"""Tests for the Captured Signals page (Tier-3 reader).

The open-signals read (``signal_db.get_open_signals_with_latest_mark``) and the
reprice-marks + manual-close actions moved to ``services/options_svc/compute`` +
``handlers`` — see that service's tests. The page now only reads the signals view
from the Redis bus and enqueues commands, so it must import NO engine / proxy /
scoring code. The pure transforms (``captured_columns``/``captured_rows``/
``synth_from_captured``/``_round``) stay on the page and are unit-tested here.
"""
import inspect

import bus_client
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


def test_drift_rounded_to_two_decimals():
    assert captured.captured_rows([{"score_drift": 1.23456}])[0]["score_drift"] == 1.23


def test_drift_preserves_whole_float():
    assert captured.captured_rows([{"score_drift": -4.0}])[0]["score_drift"] == -4.0


def test_drift_none_preserved():
    assert captured.captured_rows([{}])[0]["score_drift"] is None


def test_rec_color_take_profit_is_green():
    assert captured.rec_color("TAKE_PROFIT") == captured.REC_GREEN


def test_rec_color_cut_is_red():
    assert captured.rec_color("CUT") == captured.REC_RED


def test_rec_color_hold_is_amber():
    assert captured.rec_color("HOLD") == captured.REC_AMBER


def test_rec_color_unknown_is_grey():
    assert captured.rec_color("WHATEVER") == "#666666"


def test_row_stamps_rec_color_for_cut():
    row = captured.captured_rows([{"recommendation": "CUT"}])[0]
    assert row["_rec_color"] == captured.REC_RED


def test_row_defaults_rec_color_to_amber_when_missing():
    row = captured.captured_rows([{}])[0]
    assert row["_rec_color"] == captured.REC_AMBER


def test_render_callable():
    assert callable(captured.render)


def test_page_imports_no_engine_or_proxy():
    """Regression: the Tier-3 page must not pull in engine / proxy / scoring code."""
    for attr in ("proxy", "signal_db", "signal_repricer", "signal_recommender",
                 "OPTIONS_SCANNER", "sys"):
        assert not hasattr(captured, attr), f"captured.py still references {attr}"
    # Also guard the literal import lines so the strings never creep back.
    src = inspect.getsource(captured)
    for forbidden in ("signal_db", "signal_repricer", "signal_recommender",
                      "OPTIONS_SCANNER", "import proxy", "import sys"):
        assert forbidden not in src, f"captured.py must not reference {forbidden!r}"


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (options service cold) — the Tier-3 graceful-empty path."""
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:captured") is None  # confirm empty
    with ui.card():
        captured.render()  # must not raise
