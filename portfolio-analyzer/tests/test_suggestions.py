"""Rules engine truth table: each rule's trigger, sizing math, and prose."""
import pytest

from src.suggestions import suggest
from src.thresholds import Thresholds


def card(**over):
    base = {"symbol": "ABC", "last": 110.0, "weight": 0.05,
            "total_return": 0.10, "ann_return": 0.40, "vs_sector": 0.06,
            "vs_spy": 0.07, "sharpe": 2.0, "drawdown": 0.02,
            "capital_pct": 0.8, "atr": 2.0, "entry_price": 100.0,
            "entry_pct": 0.2, "days_held": 100, "sector_ret": 0.04,
            "spy_ret": 0.03, "quantity": 10,
            "grades": {"return": 4.0, "capital": 3.2, "risk": 4.0,
                       "execution": 3.2},
            "composite": 3.7}
    base.update(over)
    return base


CTX = {"portfolio_value": 22000.0, "avg_weight": 0.05,
       "sector_etf": "XLK", "top_sector": ("Energy", 0.12)}


def actions(sugs):
    return [s["action"] for s in sugs]


def test_healthy_position_gets_hold_only():
    sugs = suggest(card(), CTX, Thresholds())
    assert actions(sugs) == ["HOLD"]


def test_exit_on_big_loss_plus_sector_lag():
    c = card(total_return=-0.20, vs_sector=-0.10, last=80.0)
    sugs = suggest(c, CTX, Thresholds())
    assert "EXIT" in actions(sugs)
    exit_s = next(s for s in sugs if s["action"] == "EXIT")
    assert exit_s["severity"] == "high"
    # abs() prose: no double negatives like "down -20.0%"
    assert "down 20.0%" in exit_s["reason"]
    assert "lagging its sector by 10.0%" in exit_s["reason"]


def test_exit_absorbs_opportunity_cost_and_suppresses_review():
    # cost basis 10*100=1000; sector_ret 4% vs total -20% -> $240 gap
    c = card(total_return=-0.20, vs_sector=-0.10, last=80.0)
    sugs = suggest(c, CTX, Thresholds())
    exit_s = next(s for s in sugs if s["action"] == "EXIT")
    assert "$240" in exit_s["reason"]
    # design: the opportunity-cost line covers SPY too, not just the sector
    assert "SPY +3.0%" in exit_s["reason"]
    assert "REVIEW" not in actions(sugs)


def test_trim_on_overweight_with_share_sizing():
    # weight 12% vs cap 10%, portfolio $22k, last $110:
    # excess = 2% * 22000 = $440 -> 4 shares
    c = card(weight=0.12)
    sugs = suggest(c, CTX, Thresholds())
    trim = next(s for s in sugs if s["action"] == "TRIM")
    assert trim["shares"] == 4
    assert "$440" in trim["reason"]


def test_set_stop_on_drawdown_with_computed_level():
    # peak implied by dd: stop = max(entry-based, peak - 2*ATR), below last
    c = card(drawdown=0.12, last=110.0, atr=8.0)
    sugs = suggest(c, CTX, Thresholds())
    stop = next(s for s in sugs if s["action"] == "SET_STOP")
    peak = 110.0 / (1 - 0.12)                    # 125
    entry_stop = 100.0 - (0.02 * 22000.0) / 10   # cap loss at 2% of portfolio
    expected = max(entry_stop, peak - 2.0 * 8.0)  # chandelier 109 < last 110
    assert stop["level"] == pytest.approx(expected, abs=0.01)
    assert stop["severity"] == "medium"
    # chandelier leg won the max(): prose names the ATR leg, not the loss cap
    assert "×ATR below the held peak" in stop["reason"]
    assert "of portfolio" not in stop["reason"]


def test_set_stop_already_fallen_through_becomes_exit_signal():
    # peak 125, chandelier 125 - 2*2 = 121 >= last 110: a sell stop above
    # market would trigger instantly — be honest, no level, high severity.
    c = card(drawdown=0.12, last=110.0, atr=2.0)
    sugs = suggest(c, CTX, Thresholds())
    stop = next(s for s in sugs if s["action"] == "SET_STOP")
    assert "level" not in stop
    assert stop["severity"] == "high"
    assert "exit signal" in stop["reason"]


def test_set_stop_prose_names_entry_leg_when_loss_cap_binds():
    # atr=20 -> chandelier 125-40=85; qty=100 -> entry_stop 100-4.4=95.6
    c = card(drawdown=0.12, last=110.0, atr=20.0, quantity=100)
    sugs = suggest(c, CTX, Thresholds())
    stop = next(s for s in sugs if s["action"] == "SET_STOP")
    assert stop["level"] == pytest.approx(95.6, abs=0.01)
    assert "caps loss at 2% of portfolio" in stop["reason"]
    assert "×ATR" not in stop["reason"]


def test_scale_out_plan_on_big_winner():
    c = card(total_return=0.30, last=130.0)
    sugs = suggest(c, CTX, Thresholds())
    so = next(s for s in sugs if s["action"] == "SCALE_OUT")
    assert "⅓" in so["reason"] or "1/3" in so["reason"]


def test_opportunity_cost_line_on_lagging_position():
    # cost basis 10*100=1000; sector_ret 4% vs total -3% -> $70 gap
    c = card(total_return=-0.03, vs_sector=-0.07, last=97.0)
    sugs = suggest(c, CTX, Thresholds())
    review = next(s for s in sugs if s["action"] == "REVIEW")
    assert "XLK" in review["reason"]
    assert "$70" in review["reason"]
    # design: the same-capital comparison covers SPY too
    assert "SPY +3.0%" in review["reason"]


def test_opportunity_cost_names_top_sector_when_it_beats_the_sector():
    # CTX top_sector Energy +12% beats sector_ret +4% -> extra sentence
    c = card(total_return=-0.03, vs_sector=-0.07, last=97.0)
    sugs = suggest(c, CTX, Thresholds())
    review = next(s for s in sugs if s["action"] == "REVIEW")
    assert "Your top sector (Energy, +12.0%) did even better." in review["reason"]


def test_opportunity_cost_omits_top_sector_when_ctx_lacks_it():
    ctx = dict(CTX, top_sector=None)
    c = card(total_return=-0.03, vs_sector=-0.07, last=97.0)
    sugs = suggest(c, ctx, Thresholds())
    review = next(s for s in sugs if s["action"] == "REVIEW")
    assert "top sector" not in review["reason"]


def test_opportunity_cost_omits_top_sector_when_it_does_not_beat_sector():
    ctx = dict(CTX, top_sector=("Energy", 0.02))   # below sector_ret 4%
    c = card(total_return=-0.03, vs_sector=-0.07, last=97.0)
    sugs = suggest(c, ctx, Thresholds())
    review = next(s for s in sugs if s["action"] == "REVIEW")
    assert "top sector" not in review["reason"]


def test_opportunity_cost_omits_spy_clause_when_spy_ret_missing():
    c = card(total_return=-0.03, vs_sector=-0.07, last=97.0, spy_ret=None)
    sugs = suggest(c, CTX, Thresholds())
    review = next(s for s in sugs if s["action"] == "REVIEW")
    assert "SPY" not in review["reason"]
    assert "$70" in review["reason"]      # sector gap still rendered


def test_exit_opportunity_line_tolerates_missing_spy_ret():
    c = card(total_return=-0.20, vs_sector=-0.10, last=80.0, spy_ret=None)
    sugs = suggest(c, CTX, Thresholds())
    exit_s = next(s for s in sugs if s["action"] == "EXIT")
    assert "SPY" not in exit_s["reason"]
    assert "$240" in exit_s["reason"]


def test_scale_in_on_strong_underweight():
    c = card(weight=0.02, composite=3.7)
    sugs = suggest(c, CTX, Thresholds())
    assert "SCALE_IN" in actions(sugs)


def test_thresholds_injection_moves_boundaries():
    c = card(weight=0.12)
    none_trim = suggest(c, CTX, Thresholds(weight_cap=0.15))
    assert "TRIM" not in actions(none_trim)


def test_review_tolerates_missing_sector_ret():
    # docstring promises None-tolerance: lagging card with sector_ret=None
    # must not TypeError — REVIEW simply cannot fire without the comparison.
    c = card(total_return=-0.03, vs_sector=-0.07, last=97.0, sector_ret=None)
    sugs = suggest(c, CTX, Thresholds())
    assert "REVIEW" not in actions(sugs)


# --- capital efficiency: bottom quartile --------------------------------------

def test_bottom_quartile_loser_overweight_trims_toward_avg_weight():
    # weight 8% vs avg 5%, pv $22k, last $95: (3% * 22000)/95 = 6.9 -> 7 sh
    c = card(capital_pct=0.20, total_return=-0.05, ann_return=-0.17,
             weight=0.08, last=95.0, vs_sector=0.01)
    sugs = suggest(c, CTX, Thresholds())
    assert actions(sugs) == ["TRIM"]
    trim = sugs[0]
    assert trim["severity"] == "medium"
    assert trim["shares"] == 7
    assert "bottom 25%" in trim["reason"]
    assert "annualized" in trim["reason"]


def test_bottom_quartile_loser_not_overweight_falls_back_to_review():
    c = card(capital_pct=0.20, total_return=-0.05, ann_return=-0.17,
             weight=0.05, last=95.0, vs_sector=0.01)
    sugs = suggest(c, CTX, Thresholds())
    assert actions(sugs) == ["REVIEW"]
    assert sugs[0]["severity"] == "low"
    assert "bottom 25%" in sugs[0]["reason"]
    assert "shares" not in sugs[0]


def test_bottom_quartile_winner_gets_low_severity_review():
    # a winner can still be the least efficient — REVIEW, never TRIM
    c = card(capital_pct=0.20, total_return=0.05, ann_return=0.20,
             weight=0.08, last=105.0)
    sugs = suggest(c, CTX, Thresholds())
    assert actions(sugs) == ["REVIEW"]
    assert sugs[0]["severity"] == "low"
    assert "bottom 25%" in sugs[0]["reason"]
    assert "annualized" in sugs[0]["reason"]


def test_capital_rule_needs_capital_pct():
    sugs = suggest(card(capital_pct=None), CTX, Thresholds())
    assert actions(sugs) == ["HOLD"]


def test_capital_rule_fires_exactly_at_bottom_quartile_boundary():
    c = card(capital_pct=0.25, total_return=0.05, last=105.0)
    sugs = suggest(c, CTX, Thresholds())
    assert "REVIEW" in actions(sugs)
    assert "bottom 25%" in sugs[0]["reason"]


def test_exit_suppresses_capital_efficiency_rule():
    # deep loss + sector lag + bottom-quartile capital: EXIT already covers it
    c = card(total_return=-0.20, vs_sector=-0.10, last=80.0, capital_pct=0.10)
    sugs = suggest(c, CTX, Thresholds())
    assert actions(sugs) == ["EXIT"]
    assert not any("bottom 25%" in s["reason"] for s in sugs)


# --- boundary pins -----------------------------------------------------------

def test_weight_exactly_at_cap_does_not_trim():
    sugs = suggest(card(weight=0.10), CTX, Thresholds())
    assert "TRIM" not in actions(sugs)


def test_loss_exactly_at_exit_threshold_fires():
    c = card(total_return=-0.15, vs_sector=-0.10, last=85.0)
    assert "EXIT" in actions(suggest(c, CTX, Thresholds()))


def test_drawdown_exactly_at_trigger_fires_set_stop():
    c = card(drawdown=0.10, last=110.0, atr=8.0)
    assert "SET_STOP" in actions(suggest(c, CTX, Thresholds()))


def test_gain_exactly_at_take_profit_fires_scale_out():
    c = card(total_return=0.25, last=125.0)
    assert "SCALE_OUT" in actions(suggest(c, CTX, Thresholds()))


def test_trim_suppressed_when_shares_round_to_zero():
    # excess = 0.1% * $22k = $22 -> 22/110 = 0.2 sh -> rounds to 0 -> no TRIM
    sugs = suggest(card(weight=0.101), CTX, Thresholds())
    assert "TRIM" not in actions(sugs)
    assert actions(sugs) == ["HOLD"]


def test_missing_dimensions_never_crash_and_no_false_fires():
    c = card(total_return=None, vs_sector=None, sharpe=None, drawdown=None,
             atr=None, composite=None, capital_pct=None,
             grades={"return": None, "capital": None, "risk": None,
                     "execution": None})
    sugs = suggest(c, CTX, Thresholds())
    assert actions(sugs) == ["REVIEW"]  # data gap -> review, nothing else
