"""Tests for the dealer/IV context block (Phase 2, task 2.3).

The app collects per-strike GEX every minute for ~93 symbols and publishes
call_wall / put_wall / flip / net_gex / atm_iv / iv_state / dealer_regime per
symbol — and the Trade Analyzer reads none of it. This block joins one matrix
row onto an analysis so the page can say what dealer positioning implies for
the name being looked at.

It is pure: the caller supplies the row and the payload's timestamp.
"""
import datetime as dt

import pytest

from services.trade_svc import dealer_context as dc


NOW = dt.datetime(2026, 8, 22, 15, 0, tzinfo=dt.timezone.utc)
FRESH = (NOW - dt.timedelta(minutes=2)).isoformat()
STALE = (NOW - dt.timedelta(hours=6)).isoformat()


def _row(**over):
    base = {"symbol": "AAPL", "spot": 309.69, "flip": 306.5,
            "call_wall": 315.0, "put_wall": 300.0, "net_gex": 412_000_000.0,
            "atm_iv": 27.4, "iv_state": "stable", "gex_regime": "above",
            "dealer_regime": "charm_grind"}
    base.update(over)
    return base


class TestNotCollected:
    def test_a_symbol_absent_from_the_matrix_says_so(self):
        got = dc.build(None, ts=FRESH, now=NOW)
        assert got["collected"] is False
        assert "not collected" in got["summary"].lower()

    def test_an_uncollected_symbol_carries_no_fabricated_levels(self):
        got = dc.build(None, ts=FRESH, now=NOW)
        for k in ("flip", "call_wall", "put_wall", "net_gex", "atm_iv"):
            assert got[k] is None


class TestStaleness:
    def test_a_stale_payload_suppresses_the_walls(self):
        got = dc.build(_row(), ts=STALE, now=NOW)
        assert got["stale"] is True
        assert got["call_wall"] is None and got["put_wall"] is None

    def test_a_fresh_payload_keeps_them(self):
        got = dc.build(_row(), ts=FRESH, now=NOW)
        assert got["stale"] is False
        assert got["call_wall"] == 315.0 and got["put_wall"] == 300.0

    def test_an_unparseable_timestamp_is_treated_as_stale(self):
        assert dc.build(_row(), ts="not-a-time", now=NOW)["stale"] is True


class TestTheAfterHoursZeroGexSignature:
    def test_net_gex_exactly_zero_suppresses_the_walls(self):
        """Index option OI reads 0 after hours, which yields an all-zero GEX
        grid; the wall picked out of it is the argmax tie-break — an arbitrary
        strike wearing the authority of a level. Mirrors the Desk's guard."""
        got = dc.build(_row(net_gex=0.0), ts=FRESH, now=NOW)
        assert got["call_wall"] is None and got["put_wall"] is None
        assert got["walls_trustworthy"] is False

    def test_ABSENT_net_gex_is_not_that_signature(self):
        """A symbol that simply does not publish the figure keeps its walls —
        absent is not the same as present-and-exactly-zero."""
        got = dc.build(_row(net_gex=None), ts=FRESH, now=NOW)
        assert got["call_wall"] == 315.0
        assert got["walls_trustworthy"] is True


class TestFlipRead:
    def test_above_the_flip_is_long_gamma(self):
        got = dc.build(_row(), ts=FRESH, now=NOW)
        assert got["gamma_regime"] == "above"
        assert "long gamma" in got["regime_words"].lower()

    def test_below_the_flip_is_short_gamma(self):
        got = dc.build(_row(spot=300.0, flip=306.5, gex_regime="below"),
                       ts=FRESH, now=NOW)
        assert "short gamma" in got["regime_words"].lower()

    def test_a_missing_flip_yields_no_regime_words_rather_than_a_guess(self):
        got = dc.build(_row(flip=None, gex_regime="na"), ts=FRESH, now=NOW)
        assert got["gamma_regime"] == "na"
        assert got["regime_words"] == ""


class TestWallDistance:
    def test_distance_to_each_wall_is_reported_as_a_percentage(self):
        got = dc.build(_row(), ts=FRESH, now=NOW)
        # 315 is +1.71% from 309.69; 300 is -3.13%.
        assert got["call_wall_pct"] == pytest.approx(1.71, abs=0.01)
        assert got["put_wall_pct"] == pytest.approx(-3.13, abs=0.01)

    def test_no_spot_means_no_distances(self):
        got = dc.build(_row(spot=None), ts=FRESH, now=NOW)
        assert got["call_wall_pct"] is None and got["put_wall_pct"] is None

    def test_suppressed_walls_have_no_distances_either(self):
        got = dc.build(_row(net_gex=0.0), ts=FRESH, now=NOW)
        assert got["call_wall_pct"] is None and got["put_wall_pct"] is None


class TestSetupWords:
    @pytest.mark.parametrize("regime,expected", [
        ("gamma_cascade", "cascade"),
        ("vanna_squeeze", "vol crush"),
        ("delta_wall_pin", "pin"),
        ("charm_grind", "grind"),
    ])
    def test_each_dealer_regime_gets_readable_words(self, regime, expected):
        got = dc.build(_row(dealer_regime=regime), ts=FRESH, now=NOW)
        assert expected in got["setup_words"].lower()

    def test_neutral_and_na_produce_no_setup_words(self):
        assert dc.build(_row(dealer_regime="neutral"), ts=FRESH, now=NOW)["setup_words"] == ""
        assert dc.build(_row(dealer_regime="na"), ts=FRESH, now=NOW)["setup_words"] == ""

    def test_an_unknown_regime_string_does_not_raise(self):
        assert dc.build(_row(dealer_regime="something_new"), ts=FRESH, now=NOW)["setup_words"] == ""


class TestSummary:
    def test_a_fresh_collected_row_reads_as_a_sentence(self):
        s = dc.build(_row(), ts=FRESH, now=NOW)["summary"]
        assert "long gamma" in s.lower()
        assert "315" in s

    def test_a_stale_row_says_stale_rather_than_quoting_levels(self):
        s = dc.build(_row(), ts=STALE, now=NOW)["summary"]
        assert "stale" in s.lower()


class TestNeverRaises:
    @pytest.mark.parametrize("row", [
        {}, {"spot": "x"}, {"call_wall": float("nan")},
        {"net_gex": "zero"}, {"iv_state": 5},
    ])
    def test_a_malformed_row_degrades(self, row):
        got = dc.build(row, ts=FRESH, now=NOW)
        assert isinstance(got["summary"], str)
        assert got["collected"] is True
