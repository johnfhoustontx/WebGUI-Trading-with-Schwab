"""Tests for the journal labeler's pure core (Phase 6).

The labeler fills in what actually happened after each recommendation. Its
arithmetic is where the honesty lives:

  * a horizon that has not matured yet must stay NULL, not become 0.0 — the
    monitor has to tell "unknown" from "flat";
  * the label is stored THREE ways (raw excess, beta-adjusted, and the market's
    own move) because Phase 4 showed this model's edge is beta, and a monitor
    scoring itself on raw excess alone would report health through any rising
    market;
  * horizons are TRADING days, matching the model's own 20-day horizon — the
    fit counts bars, so the label must count bars.

Run from the repo root:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_labeler.py -v
"""
import datetime as dt

import pandas as pd
import pytest

from services.trade_svc import labeler as L


def _closes(n=60, start="2026-06-01", step=1.0, base=100.0):
    idx = pd.bdate_range(start, periods=n)
    return pd.Series([base + i * step for i in range(n)], index=idx)


class TestForwardReturns:
    def test_it_counts_TRADING_bars_not_calendar_days(self):
        """The fit's 20-day horizon is 20 BARS. A calendar-day label would be
        measuring a different horizon than the model was validated on."""
        sym = _closes(40, step=1.0)          # +1/bar from 100
        got = L.forward_return(sym, dt.date(2026, 6, 1), horizon=5)
        assert got == pytest.approx((105.0 / 100.0) - 1.0)

    def test_an_unmatured_horizon_returns_None_not_zero(self):
        sym = _closes(10)
        assert L.forward_return(sym, sym.index[-3].date(), horizon=20) is None

    def test_a_reading_date_the_series_does_not_contain_uses_the_next_bar(self):
        """Readings are stamped with a calendar date; a Saturday reading is
        priced from the next session rather than dropped."""
        sym = _closes(40, start="2026-06-01")
        sat = dt.date(2026, 6, 6)            # a Saturday
        assert L.forward_return(sym, sat, horizon=5) is not None

    def test_a_date_before_the_series_starts_is_None(self):
        sym = _closes(20, start="2026-06-01")
        assert L.forward_return(sym, dt.date(2020, 1, 1), horizon=5) is None


class TestTheThreeLabels:
    def test_it_returns_raw_beta_adjusted_and_market_for_each_horizon(self):
        sym = _closes(60, step=1.0)
        spy = _closes(60, step=0.5)
        out = L.labels_for(sym, spy, dt.date(2026, 6, 1), beta=2.0,
                           horizons=(5, 20))
        assert out["fwd_5d"] is not None
        assert out["fwd_5d_ba"] is not None
        assert out["mkt_fwd_5d"] is not None
        assert out["beta"] == 2.0

    def test_the_raw_label_is_symbol_minus_market(self):
        sym, spy = _closes(60, step=1.0), _closes(60, step=0.5)
        out = L.labels_for(sym, spy, dt.date(2026, 6, 1), beta=1.0, horizons=(5,))
        r_sym = (105.0 / 100.0) - 1.0
        r_spy = (102.5 / 100.0) - 1.0
        assert out["fwd_5d"] == pytest.approx(r_sym - r_spy)

    def test_the_beta_adjusted_label_subtracts_BETA_times_the_market(self):
        """A pure-beta name scores ~zero here and large-positive on the raw
        label — which is the whole reason both are stored."""
        sym, spy = _closes(60, step=1.0), _closes(60, step=0.5)
        out = L.labels_for(sym, spy, dt.date(2026, 6, 1), beta=2.0, horizons=(5,))
        r_sym = (105.0 / 100.0) - 1.0
        r_spy = (102.5 / 100.0) - 1.0
        assert out["fwd_5d_ba"] == pytest.approx(r_sym - 2.0 * r_spy)
        assert abs(out["fwd_5d_ba"]) < abs(out["fwd_5d"])

    def test_an_unknown_beta_leaves_the_adjusted_label_None_not_the_raw_one(self):
        """Beta is unmeasurable on a short history. The raw label is still a
        fact; the adjusted one is not, and inventing beta=1.0 would silently
        turn the honest column into the raw one."""
        sym, spy = _closes(60), _closes(60)
        out = L.labels_for(sym, spy, dt.date(2026, 6, 1), beta=None, horizons=(5,))
        assert out["fwd_5d"] is not None
        assert out["fwd_5d_ba"] is None
        assert out["beta"] is None

    def test_only_matured_horizons_appear(self):
        sym, spy = _closes(12), _closes(12)
        out = L.labels_for(sym, spy, sym.index[0].date(), beta=1.0,
                           horizons=(5, 10, 20))
        assert out["fwd_5d"] is not None and out["fwd_10d"] is not None
        assert out["fwd_20d"] is None


class TestMaturity:
    def test_a_reading_is_due_once_its_LONGEST_horizon_has_passed(self):
        """Labeling early would freeze a partial answer: `labeled_at` is what
        stops a row being revisited, so a row written at 5 days would never
        gain its 20-day outcome."""
        assert L.is_due(dt.date(2026, 6, 1), today=dt.date(2026, 7, 15)) is True
        assert L.is_due(dt.date(2026, 6, 1), today=dt.date(2026, 6, 10)) is False

    def test_the_cutoff_date_is_derived_from_the_longest_horizon(self):
        cut = L.due_before(dt.date(2026, 8, 22))
        assert cut < "2026-08-22"
        assert isinstance(cut, str)
