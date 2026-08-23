"""Tests for direction clearance (Phase 2, task 2.2).

The swing model's labels are 20-day forward EXCESS returns vs SPY, so a
bottom-band name is predicted to LAG the index, not to fall. In a rising tape a
naked short on a correct SELL read still loses money — this repo's own driver
already paid that tuition selling call spreads into a rally. So the short side
needs one input the long side can skip: what the market itself is doing.

Everything here is pure. The SPY series and the regime payload are already
fetched elsewhere; this only decides what they permit.
"""
import datetime as dt

import pandas as pd
import pytest

from services.trade_svc import market_filter as mf


def _spy(days=300, drift=0.0006, start=400.0):
    closes, c = [], start
    for _ in range(days):
        c *= (1 + drift)
        closes.append(c)
    return pd.Series(closes)


def _bounced_in_downtrend():
    """A long decline, then a sharp rally: price back ABOVE a 200-DMA that is
    still falling. The textbook short entry, and the case a bare
    'price above the 200' test would wrongly gate away."""
    closes, c = [], 500.0
    for _ in range(280):                      # the decline
        c *= (1 - 0.0022)
        closes.append(c)
    for _ in range(20):                       # the bounce
        c *= (1 + 0.011)
        closes.append(c)
    return pd.Series(closes)


def _regime(label="Neutral", committed="trending", direction=0, age_hours=1.0,
            now=None):
    now = now or dt.datetime(2026, 8, 22, 15, 0, tzinfo=dt.timezone.utc)
    return {"committed_label": committed, "label": label, "direction": direction,
            "as_of": (now - dt.timedelta(hours=age_hours)).isoformat()}


NOW = dt.datetime(2026, 8, 22, 15, 0, tzinfo=dt.timezone.utc)


class TestSpyTrend:
    def test_a_rising_series_is_above_and_rising(self):
        t = mf.spy_trend(_spy())
        assert t["above_200dma"] is True
        assert t["rising_200dma"] is True

    def test_a_falling_series_is_below_and_falling(self):
        t = mf.spy_trend(_spy(drift=-0.0006))
        assert t["above_200dma"] is False
        assert t["rising_200dma"] is False

    def test_too_short_a_series_reports_unknown_not_a_guess(self):
        """Fewer bars than the average needs must not resolve to False, which
        would read as 'below the 200-DMA' and clear directional shorts."""
        t = mf.spy_trend(_spy(days=40))
        assert t["above_200dma"] is None
        assert t["rising_200dma"] is None

    def test_no_series_at_all_is_unknown(self):
        assert mf.spy_trend(None)["above_200dma"] is None


class TestShortSideClearance:
    def test_a_rising_tape_permits_only_RELATIVE_shorts(self):
        c = mf.direction_clearance(_spy(), _regime(), now=NOW)
        assert c["short"]["state"] == "relative_only"
        assert any("200-DMA" in r for r in c["short"]["reasons"])

    def test_spy_below_its_200dma_clears_directional_shorts(self):
        c = mf.direction_clearance(_spy(drift=-0.0006), _regime(), now=NOW)
        assert c["short"]["state"] == "cleared"

    def test_a_RISING_200dma_outranks_a_committed_downward_regime(self):
        """The structural read wins over the fast one, and the horizons are why.

        The 200-DMA is a multi-week structure; the committed direction comes
        from a 5-minute EMA slope and a 15-minute composite. Letting an
        intraday "Softening" clear a TWENTY-DAY directional short against a
        rising 200-DMA is the same horizon mismatch the audit criticised in the
        legacy engine, where 5-minute RSI drove a 1-8 week verdict.

        Caught live: SPY sat above a rising 200-DMA while the regime read
        Softening, and both sides came back cleared — a contradiction on its
        face."""
        c = mf.direction_clearance(_spy(), _regime(label="Softening", direction=-1),
                                   now=NOW)
        assert c["short"]["state"] == "relative_only"
        assert any("rising 200-DMA" in r for r in c["short"]["reasons"])

    def test_a_downward_regime_DOES_clear_shorts_once_the_structure_is_neutral(self):
        """The fast read is not ignored — it tips a structure that is no longer
        rising. Price above a still-FALLING 200-DMA plus a committed downward
        direction is a rally inside a broken trend."""
        c = mf.direction_clearance(_bounced_in_downtrend(),
                                   _regime(label="Softening", direction=-1), now=NOW)
        assert c["market"]["spy_above_200dma"] is True
        assert c["market"]["spy_200dma_rising"] is False
        assert c["short"]["state"] == "cleared"
        assert any("Softening" in r for r in c["short"]["reasons"])

    def test_that_same_neutral_structure_without_a_downward_regime_stays_relative(self):
        c = mf.direction_clearance(_bounced_in_downtrend(), _regime(), now=NOW)
        assert c["short"]["state"] == "relative_only"

    def test_a_stale_regime_does_NOT_clear_shorts(self):
        """Staleness must fail CONSERVATIVE. Reading a four-day-old 'Softening'
        as permission is how a dead service authorizes directional shorts into
        a tape that has since turned back up."""
        c = mf.direction_clearance(_spy(), _regime(label="Softening", direction=-1,
                                                   age_hours=200, now=NOW), now=NOW)
        assert c["short"]["state"] == "relative_only"
        assert c["market"]["stale"] is True
        assert any("stale" in r.lower() for r in c["short"]["reasons"])

    def test_a_missing_regime_does_not_clear_shorts_either(self):
        c = mf.direction_clearance(_spy(), None, now=NOW)
        assert c["short"]["state"] == "relative_only"

    def test_an_unknown_spy_trend_does_not_clear_shorts(self):
        c = mf.direction_clearance(_spy(days=40), _regime(), now=NOW)
        assert c["short"]["state"] == "relative_only"


class TestLongSideClearance:
    def test_a_rising_tape_clears_longs(self):
        c = mf.direction_clearance(_spy(), _regime(), now=NOW)
        assert c["long"]["state"] == "cleared"

    def test_a_falling_tape_demotes_longs_but_never_blocks_them(self):
        """A long in a downtrend is a worse trade, not a forbidden one — the
        model still ranks names cross-sectionally. Demote, don't block."""
        c = mf.direction_clearance(_spy(drift=-0.0006), _regime(), now=NOW)
        assert c["long"]["state"] == "relative_only"

    def test_an_unknown_tape_demotes_longs(self):
        assert mf.direction_clearance(_spy(days=40), _regime(),
                                      now=NOW)["long"]["state"] == "relative_only"


class TestBothSidesAlwaysPresent:
    def test_every_result_carries_both_sides_with_reasons(self):
        """A blocked side WITH its reasons is a research finding; a missing
        side is an absence the reader has to interpret. Both are always here."""
        for spy, reg in ((_spy(), _regime()),
                         (_spy(drift=-0.0006), _regime(label="Softening", direction=-1)),
                         (None, None)):
            c = mf.direction_clearance(spy, reg, now=NOW)
            assert set(c) == {"market", "long", "short"}
            for side in ("long", "short"):
                assert c[side]["state"] in {"cleared", "relative_only", "blocked"}
                assert isinstance(c[side]["reasons"], list)
                assert c[side]["reasons"], f"{side} must always say why"

    def test_the_market_summary_is_a_sentence_a_person_can_read(self):
        c = mf.direction_clearance(_spy(), _regime(label="Softening", direction=-1),
                                   now=NOW)
        assert "200-DMA" in c["market"]["summary"]
        assert "Softening" in c["market"]["summary"]


class TestNeverRaises:
    @pytest.mark.parametrize("regime", [
        {}, {"as_of": "not-a-date"}, {"direction": "sideways"},
        {"as_of": None, "committed_label": None},
    ])
    def test_a_malformed_regime_payload_degrades(self, regime):
        c = mf.direction_clearance(_spy(), regime, now=NOW)
        assert c["short"]["state"] in {"relative_only", "cleared"}
        assert c["long"]["state"] in {"cleared", "relative_only"}
