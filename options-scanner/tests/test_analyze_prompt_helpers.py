"""Tests for the pure helpers that feed the Analyze button prompt.

Covers the five enhancements added so the AI prompt no longer relies on
chart screenshots for chart-only signals:

  1. 0-DTE pressure projection numbers
  2. Per-top-strike delta vs previous snapshot
  3. DEX projected EOD flip strike (numerical)
  4. Per-top-strike value at market-open
  5. Top-N strikes plus tail summary (full distribution shape)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamma_tool import (
    top_strikes_with_tail,
    delta_change_for_strikes,
    value_at_open_for_strikes,
    compute_projected_flip,
    format_pressure_panel,
)


def _mk_grid(values):
    """{strike: net} → full grid dict shaped like the engine produces."""
    return {s: {"call": 0, "put": 0, "net": v} for s, v in values.items()}


class TestTopStrikesWithTail:
    def test_returns_top_n_positive_and_negative(self):
        grid = _mk_grid({i: float(i - 50) * 1e6 for i in range(100)})
        pos, neg, tail = top_strikes_with_tail(grid, n=20)
        assert len(pos) == 20
        assert len(neg) == 20
        # positives sorted descending
        assert pos[0]["value"] > pos[-1]["value"] > 0
        # negatives sorted ascending (most negative first)
        assert neg[0]["value"] < neg[-1]["value"] < 0

    def test_tail_summary_aggregates_remaining(self):
        # 25 positive strikes; with n=20, 5 should land in tail_pos.
        grid = _mk_grid({i: float(i + 1) * 1e6 for i in range(25)})
        pos, neg, tail = top_strikes_with_tail(grid, n=20)
        assert tail["count_pos"] == 5
        assert tail["sum_pos"] == sum((i + 1) * 1e6 for i in range(5))
        assert tail["count_neg"] == 0
        assert tail["sum_neg"] == 0

    def test_tail_zero_when_under_n_strikes(self):
        grid = _mk_grid({i: float(i) for i in range(5)})
        pos, neg, tail = top_strikes_with_tail(grid, n=20)
        assert tail["count_pos"] == 0
        assert tail["count_neg"] == 0


class TestDeltaChangeForStrikes:
    def test_returns_delta_per_strike(self):
        prev = _mk_grid({100: 1e6, 110: 2e6})
        curr = _mk_grid({100: 1.5e6, 110: 1.8e6})
        out = delta_change_for_strikes([100, 110], curr, prev)
        assert out[100] == 0.5e6
        assert out[110] == -0.2e6

    def test_missing_strike_in_prev_treated_as_zero(self):
        prev = _mk_grid({100: 1e6})
        curr = _mk_grid({100: 1.5e6, 110: 2e6})
        out = delta_change_for_strikes([100, 110], curr, prev)
        assert out[110] == 2e6

    def test_returns_empty_when_prev_is_none(self):
        curr = _mk_grid({100: 1.5e6})
        assert delta_change_for_strikes([100], curr, None) == {}


class TestValueAtOpenForStrikes:
    def test_returns_open_value_per_strike(self):
        open_grid = _mk_grid({100: 1e6, 110: -2e6})
        out = value_at_open_for_strikes([100, 110], open_grid)
        assert out[100] == 1e6
        assert out[110] == -2e6

    def test_missing_strike_in_open_returns_none(self):
        open_grid = _mk_grid({100: 1e6})
        out = value_at_open_for_strikes([100, 110], open_grid)
        assert out[100] == 1e6
        assert out[110] is None

    def test_returns_empty_when_open_is_none(self):
        assert value_at_open_for_strikes([100], None) == {}


class TestComputeProjectedFlip:
    def test_uniform_shift_finds_zero_crossing(self):
        # net values straddle zero; positive hedge shifts crossing higher.
        dex_data = {
            "gex": _mk_grid({100: -2e6, 105: -1e6, 110: 1e6, 115: 2e6}),
            "hedge_pressure": 0.0,  # no shift → flip near 107.5
        }
        out = compute_projected_flip(dex_data, spot=107.5)
        assert out is not None
        assert 105 < out < 110

    def test_returns_none_when_no_hedge_pressure(self):
        dex_data = {"gex": _mk_grid({100: 1e6}), "hedge_pressure": None}
        assert compute_projected_flip(dex_data, spot=100) is None

    def test_returns_none_for_empty_grid(self):
        assert compute_projected_flip({"gex": {}, "hedge_pressure": 1e6}, 100) is None


class TestFormatPressurePanel:
    def test_includes_now_projected_and_hedge(self):
        # hedge of -2e6 spread over 4 strikes → -5e5 per-strike shift, leaves
        # a valid crossing between 100 and 105.
        dex_data = {
            "net_delta_0dte": 5.5e9,
            "projected_net_delta_close": 4.2e9,
            "hedge_pressure": -2e6,
            "gex": _mk_grid({95: -2e7, 100: -1e7, 105: 1e7, 110: 2e7}),
        }
        out = format_pressure_panel(dex_data, spot=102.5)
        assert out["delta_now"] == 5.5e9
        assert out["projected_close"] == 4.2e9
        assert out["hedge_pressure"] == -2e6
        assert out["hedge_direction"] == "sell"
        assert out["projected_flip"] is not None

    def test_buy_direction_for_positive_hedge(self):
        out = format_pressure_panel(
            {"net_delta_0dte": 1, "projected_net_delta_close": 1,
             "hedge_pressure": 5e8, "gex": _mk_grid({100: 1e6})},
            spot=100,
        )
        assert out["hedge_direction"] == "buy"

    def test_returns_none_when_no_zero_dte(self):
        assert format_pressure_panel({"gex": {}}, spot=100) is None
        assert format_pressure_panel(
            {"net_delta_0dte": None, "gex": _mk_grid({100: 1})}, spot=100
        ) is None
