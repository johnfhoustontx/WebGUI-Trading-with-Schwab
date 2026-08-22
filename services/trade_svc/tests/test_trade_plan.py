"""Tests for the Trade Plan block (Phase 3, task 3.3).

The verdict says WHAT; the plan says how you'd hold it and — the part that
matters — what would prove it wrong. Every field is derived from something the
analysis already computed; nothing here forecasts.

The time stop is the point of the whole exercise. The model predicts 20 trading
days; past that the read is unmodelled, and today nothing in the app says so.
"""
import datetime as dt

import pytest

from services.trade_svc import trade_plan as tp


def _analysis(**over):
    base = {
        "symbol": "NVDA", "price": 180.0,
        "swing_model": {"verdict": "BUY", "percentile": 91,
                        "expected_fwd": 0.016, "hit_rate": 0.53,
                        "horizon_days": 20},
        "direction_clearance": {
            "long": {"state": "cleared", "reasons": ["SPY above a rising 200-DMA"]},
            "short": {"state": "relative_only", "reasons": ["SPY above a rising 200-DMA"]},
        },
        "dealer_context": {"collected": True, "stale": False, "flip": 178.0,
                           "call_wall": 195.0, "put_wall": 175.0,
                           "atm_iv": 31.0, "iv_state": "stable"},
        "position_verdict": {"gates_triggered": [], "short_gates": []},
        "momentum": {"atr": 3.5},
        "fundamentals": {"days_to_earnings": None},
        "earnings_coverage": "none_scheduled",
    }
    base.update(over)
    return base


class TestTheTimeStop:
    def test_it_is_the_MODEL_horizon_not_a_round_number(self):
        """20 trading days is what the artifact predicts. A '30 days' or 'one
        month' stop would be a number we made up sitting on a number we
        measured."""
        plan = tp.build(_analysis(), today=dt.date(2026, 8, 24))
        assert plan["time_stop_trading_days"] == 20

    def test_it_resolves_to_a_real_DATE_skipping_weekends(self):
        # 2026-08-24 is a Monday; 20 trading days later is 2026-09-21.
        plan = tp.build(_analysis(), today=dt.date(2026, 8, 24))
        assert plan["time_stop_date"] == "2026-09-21"

    def test_it_says_WHY_rather_than_just_when(self):
        plan = tp.build(_analysis(), today=dt.date(2026, 8, 24))
        assert "unmodel" in plan["time_stop_note"].lower()


class TestDirectionAndStructure:
    def test_a_cleared_long_gets_a_directional_structure(self):
        plan = tp.build(_analysis(), today=dt.date(2026, 8, 24))
        assert plan["side"] == "long"
        assert plan["action"] in ("debit", "credit")
        assert plan["structure"]

    def test_a_bottom_band_read_in_a_rising_tape_is_RELATIVE(self):
        a = _analysis(swing_model={"verdict": "SELL", "percentile": 7,
                                   "expected_fwd": -0.008, "hit_rate": 0.44,
                                   "horizon_days": 20})
        plan = tp.build(a, today=dt.date(2026, 8, 24))
        assert plan["side"] == "short"
        assert plan["action"] == "relative"

    def test_a_HOLD_proposes_no_trade_and_says_what_would_change_it(self):
        a = _analysis(swing_model={"verdict": "HOLD", "percentile": 55,
                                   "expected_fwd": 0.001, "hit_rate": 0.49,
                                   "horizon_days": 20})
        plan = tp.build(a, today=dt.date(2026, 8, 24))
        assert plan["action"] == "none"
        assert plan["what_would_change_it"]


class TestStopAndTarget:
    def test_the_stop_falls_back_to_ATR_when_the_wall_is_further_away(self):
        """With the put wall down at 160, the 1.8x ATR level (173.7) is the
        tighter of the two and wins."""
        a = _analysis(dealer_context={"collected": True, "stale": False,
                                      "flip": 178.0, "call_wall": 195.0,
                                      "put_wall": 160.0, "atm_iv": 31.0,
                                      "iv_state": "stable"})
        plan = tp.build(a, today=dt.date(2026, 8, 24))
        assert plan["stop"] == pytest.approx(173.7)
        assert plan["stop"] < 180.0
        assert "atr" in plan["stop_note"].lower()

    def test_the_stop_prefers_the_PUT_WALL_when_it_is_the_tighter_level(self):
        """A structural level beats an arithmetic one when it is closer — the
        wall is where the cushion actually is."""
        a = _analysis(dealer_context={"collected": True, "stale": False,
                                      "flip": 178.0, "call_wall": 195.0,
                                      "put_wall": 179.0, "atm_iv": 31.0,
                                      "iv_state": "stable"})
        plan = tp.build(a, today=dt.date(2026, 8, 24))
        assert plan["stop"] == pytest.approx(179.0)
        assert "wall" in plan["stop_note"].lower()

    def test_the_target_is_the_CALIBRATED_expectation_not_a_guess(self):
        plan = tp.build(_analysis(), today=dt.date(2026, 8, 24))
        assert "+1.6%" in plan["target"]
        assert "spy" in plan["target"].lower()

    def test_no_atr_and_no_walls_leaves_the_stop_absent_not_invented(self):
        a = _analysis(momentum={}, dealer_context={"collected": False})
        plan = tp.build(a, today=dt.date(2026, 8, 24))
        assert plan["stop"] is None
        assert plan["stop_note"] == ""


class TestEarningsLine:
    def test_a_report_inside_the_window_is_called_out(self):
        a = _analysis(fundamentals={"days_to_earnings": 9},
                      earnings_coverage="upcoming")
        plan = tp.build(a, today=dt.date(2026, 8, 24))
        assert "9 days" in plan["events"]
        assert "inside" in plan["events"].lower()

    def test_a_report_beyond_the_window_is_noted_as_outside_it(self):
        a = _analysis(fundamentals={"days_to_earnings": 60},
                      earnings_coverage="upcoming")
        plan = tp.build(a, today=dt.date(2026, 8, 24))
        assert "outside" in plan["events"].lower()

    def test_an_unlisted_symbol_says_the_date_is_UNKNOWN(self):
        """The fail-open case. Silence must not read as an all-clear."""
        a = _analysis(fundamentals={"days_to_earnings": None},
                      earnings_coverage="not_listed")
        plan = tp.build(a, today=dt.date(2026, 8, 24))
        assert "unknown" in plan["events"].lower()


class TestNeverRaises:
    @pytest.mark.parametrize("a", [
        {}, {"symbol": "X"}, {"symbol": "X", "swing_model": None},
        {"symbol": "X", "price": "not-a-number"},
    ])
    def test_a_degraded_analysis_still_yields_a_plan_shaped_dict(self, a):
        plan = tp.build(a, today=dt.date(2026, 8, 24))
        assert set(plan) >= {"side", "action", "structure", "stop", "target",
                             "events", "time_stop_trading_days"}
