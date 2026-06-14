"""Tests for the Paper Trades pure transforms."""
from pages.options import paper

TRADE = {
    "trade_id": "T1", "symbol": "SPY", "strategy": "PCS", "status": "OPEN",
    "quantity": 2, "entry_credit": 0.34, "entry_credit_total": 68.0,
    "max_loss_total": 932.0, "realized_pnl": None,
    "entry_time": "2026-06-10T10:00:00+00:00", "expiration": "2026-06-19",
    "short_strike": 450, "long_strike": 445,
}


def test_paper_columns_have_keys():
    fields = {c["field"] for c in paper.paper_columns()}
    assert {"symbol", "strategy", "quantity", "status", "realized_pnl"} <= fields


def test_paper_rows_maps_and_keeps_id():
    rows = paper.paper_rows([TRADE])
    assert rows[0]["id"] == "T1"
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["quantity"] == 2


def test_paper_rows_handles_missing():
    rows = paper.paper_rows([{"trade_id": "T2"}])
    assert rows[0]["id"] == "T2"


def test_synth_from_trade_for_detail():
    s = paper.synth_from_trade(TRADE)
    assert s["type"] == "PCS"
    assert s["credit"] == 0.34
    assert s["id"] == "T1"
    assert s["short_strike"] == 450
