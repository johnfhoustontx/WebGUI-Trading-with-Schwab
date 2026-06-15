"""Tests for the Paper Trades page (Tier-3 reader).

The ledger read (``paper_trader.get_all_trades``) and the close/delete/
delete-all/analyze actions moved to ``services/options_svc/compute`` +
``handlers`` — see that service's tests. The page now only reads the ledger view
from the Redis bus and enqueues lifecycle commands, so it must import NO engine /
proxy / scoring code. The pure transforms (``paper_rows``/``synth_from_trade``/
``_strikes``) stay on the page and are unit-tested here.
"""
import inspect

import bus_client
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


def test_strikes_iron_condor_vs_spread():
    assert paper._strikes(TRADE) == "450/445"
    ic = {"strategy": "IC", "short_strike": 450, "long_strike": 445,
          "call_short": 460, "call_long": 465}
    assert paper._strikes(ic) == "P 450/445 C 460/465"


def test_synth_from_trade_for_detail():
    s = paper.synth_from_trade(TRADE)
    assert s["type"] == "PCS"
    assert s["credit"] == 0.34
    assert s["id"] == "T1"
    assert s["short_strike"] == 450


def test_render_callable():
    assert callable(paper.render)


def test_page_imports_no_engine_or_proxy():
    """Regression: the Tier-3 page must not pull in engine / proxy / scoring code."""
    for attr in ("proxy", "paper_trader", "trade_analyzer", "OPTIONS_SCANNER", "sys"):
        assert not hasattr(paper, attr), f"paper.py still references {attr}"
    # Also guard the literal import lines so the strings never creep back.
    src = inspect.getsource(paper)
    for forbidden in ("paper_trader", "trade_analyzer", "OPTIONS_SCANNER",
                      "import proxy", "import sys"):
        assert forbidden not in src, f"paper.py must not reference {forbidden!r}"


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (options service cold) — the Tier-3 graceful-empty path."""
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:paper_trades") is None  # confirm empty
    with ui.card():
        paper.render()  # must not raise
