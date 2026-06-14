"""Tests for the Captured Signals pure transforms."""
from pages.options import captured

SAMPLE = {
    "signal_id": "X1", "symbol": "SPY", "strategy": "PCS", "mode": "PREMIUM",
    "expiration": "2026-06-19", "dte_at_entry": 4, "entry_credit": 0.34,
    "entry_max_loss": 4.66, "unrealized_pnl": 12.0, "entry_score": 72,
    "current_score": 68, "score_drift": -4, "entry_grade": "A",
    "recommendation": "HOLD", "status": "OPEN",
    "short_strike": 450, "long_strike": 445, "width": 5,
    "entry_short_delta": -0.25, "entry_net_theta": 0.04, "entry_iv_rank": 40,
}


def test_captured_columns_have_keys():
    fields = {c["field"] for c in captured.captured_columns()}
    assert {"symbol", "strategy", "current_score", "recommendation", "status"} <= fields


def test_captured_rows_maps_and_keeps_id():
    rows = captured.captured_rows([SAMPLE])
    assert rows[0]["id"] == "X1"
    assert rows[0]["current_score"] == 68
    assert rows[0]["symbol"] == "SPY"


def test_captured_rows_defaults_recommendation_hold():
    rows = captured.captured_rows([{"signal_id": "Y", "symbol": "Q"}])
    assert rows[0]["recommendation"] == "HOLD"


def test_synth_from_captured_for_detail_panel():
    s = captured.synth_from_captured(SAMPLE)
    assert s["type"] == "PCS"
    assert s["composite_score"] == 68          # prefers current over entry score
    assert s["short_strike"] == 450
    assert s["id"] == "X1"


def test_synth_from_captured_falls_back_to_entry_score():
    s = captured.synth_from_captured({"signal_id": "Z", "entry_score": 55})
    assert s["composite_score"] == 55
