"""Unit tests for trade_analyzer helpers."""
import pytest
from trade_analyzer import _compute_breakeven










def test_breakeven_pcs():
    trade = {
        "strategy": "PCS", "short_strike": 360.0, "long_strike": 350.0,
        "entry_credit": 1.94, "entry_credit_total": 1940.0, "quantity": 10,
    }
    assert _compute_breakeven(trade) == pytest.approx(358.06, abs=0.01)


def test_breakeven_ccs():
    trade = {
        "strategy": "CCS", "short_strike": 500.0, "long_strike": 505.0,
        "entry_credit": 1.20, "entry_credit_total": 120.0, "quantity": 1,
    }
    assert _compute_breakeven(trade) == pytest.approx(501.20, abs=0.01)


def test_breakeven_ic_returns_string():
    trade = {
        "strategy": "IC", "short_strike": 450.0, "long_strike": 445.0,
        "call_short": 470.0, "call_long": 475.0,
        "entry_credit": 2.00, "entry_credit_total": 200.0, "quantity": 1,
    }
    result = _compute_breakeven(trade)
    assert isinstance(result, str)
    assert "448" in result and "472" in result


def test_breakeven_uses_stored_when_present():
    trade = {
        "strategy": "PCS", "short_strike": 360.0, "long_strike": 350.0,
        "entry_credit": 1.94, "entry_credit_total": 1940.0, "quantity": 10,
        "breakeven": "358.06",
    }
    assert _compute_breakeven(trade) == pytest.approx(358.06, abs=0.01)


def test_net_theta_positive_for_short_pcs():
    """For a short credit spread, position theta must be positive."""
    from trade_analyzer import _calc_current_spread_value
    chain = {
        "putExpDateMap": {
            "2026-05-01:8": {
                "360.0": [{"mark": 2.50, "delta": -0.30, "theta": -0.08,
                           "vega": 0.10, "gamma": 0.02, "volatility": 0.35}],
                "350.0": [{"mark": 0.56, "delta": -0.10, "theta": -0.05,
                           "vega": 0.06, "gamma": 0.01, "volatility": 0.38}],
            }
        }
    }
    trade = {"strategy": "PCS", "short_strike": 360.0, "long_strike": 350.0}
    debit, greeks, quotes = _calc_current_spread_value(chain, trade)
    assert "bid" in quotes and "ask" in quotes and "mark" in quotes
    assert quotes["mark"] >= 0
    assert greeks["theta"] > 0, f"Short PCS theta should be positive, got {greeks['theta']}"
    # Short-put position delta = long_delta - short_delta = -0.10 - (-0.30) = +0.20
    assert greeks["delta"] > 0
    # Short-put is net short vega
    assert greeks["vega"] < 0
    # Short-put is net short gamma
    assert greeks["gamma"] < 0


def test_strike_vs_em_uses_dte_scaled():
    """Short strike 22 pts away with DTE-scaled EM ≈ 25 should be 'Inside expected move';
    distance 30 should be 'Outside 1σ'; distance 60 should be 'Outside 2σ'."""
    from trade_analyzer import _classify_strike_vs_em
    assert _classify_strike_vs_em(distance=22, em_scaled=25.0) == "Inside expected move"
    assert _classify_strike_vs_em(distance=30, em_scaled=25.0) == "Outside 1\u03c3"
    assert _classify_strike_vs_em(distance=60, em_scaled=25.0) == "Outside 2\u03c3"


def test_strike_vs_em_handles_missing_em():
    from trade_analyzer import _classify_strike_vs_em
    assert _classify_strike_vs_em(distance=10, em_scaled=None) == "N/A"
    assert _classify_strike_vs_em(distance=10, em_scaled=0) == "N/A"




def test_expiration_pnl_pcs_below_short_partial_loss():
    """TSLA 360/350 PCS, credit $1.94, qty 10. At $355.79: short put ITM by $4.21,
    long put OTM. Close-at-expiration debit = $4.21 per share * 10 * 100 = $4,210.
    P&L = credit_total - debit = 1940 - 4210 = -$2,270."""
    from trade_analyzer import _expiration_pnl
    trade = {
        "strategy": "PCS", "short_strike": 360.0, "long_strike": 350.0,
        "entry_credit": 1.94, "entry_credit_total": 1940.0, "quantity": 10,
    }
    assert _expiration_pnl(trade, 355.79) == pytest.approx(-2270, abs=1)


def test_expiration_pnl_pcs_above_short_full_credit():
    from trade_analyzer import _expiration_pnl
    trade = {
        "strategy": "PCS", "short_strike": 360.0, "long_strike": 350.0,
        "entry_credit": 1.94, "entry_credit_total": 1940.0, "quantity": 10,
    }
    assert _expiration_pnl(trade, 370.0) == pytest.approx(1940, abs=1)


def test_expiration_pnl_pcs_below_long_max_loss():
    """At $345: spread fully ITM, debit = width * qty * 100 = 10 * 10 * 100 = 10000.
    P&L = 1940 - 10000 = -8060."""
    from trade_analyzer import _expiration_pnl
    trade = {
        "strategy": "PCS", "short_strike": 360.0, "long_strike": 350.0,
        "entry_credit": 1.94, "entry_credit_total": 1940.0, "quantity": 10,
    }
    assert _expiration_pnl(trade, 345.0) == pytest.approx(-8060, abs=1)


def test_expiration_pnl_ccs_above_short():
    """Mirror test for CCS."""
    from trade_analyzer import _expiration_pnl
    trade = {
        "strategy": "CCS", "short_strike": 500.0, "long_strike": 505.0,
        "entry_credit": 1.20, "entry_credit_total": 120.0, "quantity": 1,
    }
    assert _expiration_pnl(trade, 502.0) == pytest.approx(-80, abs=1)


def test_expiration_pnl_ic_inside_both_shorts():
    """IC at price between both short strikes -> full credit."""
    from trade_analyzer import _expiration_pnl
    trade = {
        "strategy": "IC", "short_strike": 450.0, "long_strike": 445.0,
        "call_short": 470.0, "call_long": 475.0,
        "entry_credit": 2.00, "entry_credit_total": 200.0, "quantity": 1,
    }
    assert _expiration_pnl(trade, 460.0) == pytest.approx(200, abs=1)


def test_expiration_pnl_ic_put_side_partial_breach():
    """IC, short_put=450, long_put=445, call_short=470, call_long=475,
    credit_total=$200, qty=1.
    At price 447: put-side debit = (450-447) - 0 = 3. P&L = 200 - 300 = -100."""
    from trade_analyzer import _expiration_pnl
    trade = {
        "strategy": "IC", "short_strike": 450.0, "long_strike": 445.0,
        "call_short": 470.0, "call_long": 475.0,
        "entry_credit": 2.00, "entry_credit_total": 200.0, "quantity": 1,
    }
    assert _expiration_pnl(trade, 447.0) == pytest.approx(-100, abs=1)


def test_expiration_pnl_ic_call_side_max_loss_multi_contract():
    """IC with qty=3. At price 480 (above call_long), call side fully ITM.
    Call debit per share = 475 - 470 = 5. Total debit = 5 x 3 x 100 = 1500.
    credit_total = $2.00 x 3 x 100 = 600. P&L = 600 - 1500 = -900."""
    from trade_analyzer import _expiration_pnl
    trade = {
        "strategy": "IC", "short_strike": 450.0, "long_strike": 445.0,
        "call_short": 470.0, "call_long": 475.0,
        "entry_credit": 2.00, "entry_credit_total": 600.0, "quantity": 3,
    }
    assert _expiration_pnl(trade, 480.0) == pytest.approx(-900, abs=1)






def test_scenario_warning_when_negative_sigma_near_long_strike_pcs():
    """PCS with long strike $350, -1sigma price $350.46 (within 20%*width of pin) warns."""
    from trade_analyzer import _check_scenario_strike_proximity
    trade = {
        "strategy": "PCS", "short_strike": 360.0, "long_strike": 350.0,
        "width": 10.0, "entry_credit": 1.94, "entry_credit_total": 1940.0,
        "quantity": 10,
    }
    scenarios = [
        {"label": "Price stays flat", "price": 375.0, "pnl": 1940, "pnl_pct": 24.1},
        {"label": "Price +1\u03c3", "price": 400.0, "pnl": 1940, "pnl_pct": 24.1},
        {"label": "Price \u22121\u03c3", "price": 350.46, "pnl": -7600, "pnl_pct": -94.3},
    ]
    warnings = _check_scenario_strike_proximity(trade, scenarios)
    assert len(warnings) == 1
    assert "\u22121\u03c3" in warnings[0]
    assert "long put strike" in warnings[0]


def test_scenario_warning_silent_when_negative_sigma_safely_above_long_strike():
    from trade_analyzer import _check_scenario_strike_proximity
    trade = {
        "strategy": "PCS", "short_strike": 360.0, "long_strike": 350.0,
        "width": 10.0, "entry_credit": 1.94, "entry_credit_total": 1940.0,
        "quantity": 10,
    }
    scenarios = [
        {"label": "Price \u22121\u03c3", "price": 355.0, "pnl": -3060, "pnl_pct": -38.0},
    ]
    assert _check_scenario_strike_proximity(trade, scenarios) == []


def test_scenario_warning_ccs_positive_sigma_near_long_call():
    from trade_analyzer import _check_scenario_strike_proximity
    trade = {
        "strategy": "CCS", "short_strike": 500.0, "long_strike": 505.0,
        "width": 5.0, "entry_credit": 1.20, "entry_credit_total": 120.0,
        "quantity": 1,
    }
    scenarios = [
        {"label": "Price +1\u03c3", "price": 504.5, "pnl": -380, "pnl_pct": -95.0},
    ]
    warnings = _check_scenario_strike_proximity(trade, scenarios)
    assert len(warnings) == 1
    assert "+1\u03c3" in warnings[0]
    assert "long call strike" in warnings[0]


def test_earnings_inside_the_window_is_surfaced():
    """An earnings report between today and expiration becomes an event."""
    from datetime import date, timedelta
    from trade_analyzer import _collect_event_context
    today = date.today()
    exp = today + timedelta(days=10)
    report = today + timedelta(days=4)
    trade = {"symbol": "ORCL", "expiration": exp.isoformat()}
    ctx = _collect_event_context(trade, dte_remaining=10, quote_data={},
                                 earnings_date=report.isoformat())
    assert len(ctx["events_in_window"]) == 1
    ev = ctx["events_in_window"][0]
    assert ev["category"] == "EARNINGS"
    assert ev["date"] == report.isoformat()
    assert ev["label"] == "ORCL earnings"


def test_earnings_after_expiration_or_absent_is_not_an_event():
    from datetime import date, timedelta
    from trade_analyzer import _collect_event_context
    today = date.today()
    trade = {"symbol": "ORCL",
             "expiration": (today + timedelta(days=5)).isoformat()}
    late = _collect_event_context(trade, 5, {},
                                  earnings_date=today + timedelta(days=30))
    none = _collect_event_context(trade, 5, {}, earnings_date=None)
    assert late["events_in_window"] == []
    assert none["events_in_window"] == []


def test_iv_reexpansion_warning_triggers_when_all_three_conditions():
    from trade_analyzer import _iv_reexpansion_warnings
    events = [{"date": "2026-04-29", "label": "FOMC Decision",
               "category": "FOMC", "source": "macro_json"}]
    warnings = _iv_reexpansion_warnings(
        current_vega=-0.052, iv_rank=22, events_in_window=events)
    assert len(warnings) == 1
    assert "FOMC Decision" in warnings[0]
    assert "-0.052" in warnings[0] or "-0.05" in warnings[0]


def test_iv_reexpansion_warning_silent_when_long_vega():
    from trade_analyzer import _iv_reexpansion_warnings
    events = [{"date": "2026-04-29", "label": "FOMC", "category": "FOMC",
               "source": "macro_json"}]
    assert _iv_reexpansion_warnings(
        current_vega=0.05, iv_rank=22, events_in_window=events) == []


def test_iv_reexpansion_warning_silent_when_iv_not_compressed():
    from trade_analyzer import _iv_reexpansion_warnings
    events = [{"date": "2026-04-29", "label": "FOMC", "category": "FOMC",
               "source": "macro_json"}]
    assert _iv_reexpansion_warnings(
        current_vega=-0.05, iv_rank=55, events_in_window=events) == []


def test_iv_reexpansion_warning_silent_when_no_events():
    from trade_analyzer import _iv_reexpansion_warnings
    assert _iv_reexpansion_warnings(
        current_vega=-0.05, iv_rank=10, events_in_window=[]) == []


def test_iv_reexpansion_warning_silent_when_iv_rank_unknown():
    from trade_analyzer import _iv_reexpansion_warnings
    events = [{"date": "2026-04-29", "label": "FOMC", "category": "FOMC",
               "source": "macro_json"}]
    # Missing iv_rank -> cannot evaluate condition -> no warning
    assert _iv_reexpansion_warnings(
        current_vega=-0.05, iv_rank=None, events_in_window=events) == []






