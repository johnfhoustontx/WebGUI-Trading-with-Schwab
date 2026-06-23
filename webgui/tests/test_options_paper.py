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


def test_paper_rows_entry_time_drops_iso_t():
    # "2026-06-10T10:00:00+00:00" -> "2026-06-10 10:00:00" (no 'T', seconds only).
    assert paper.paper_rows([TRADE])[0]["entry_time"] == "2026-06-10 10:00:00"
    assert paper.paper_rows([{"trade_id": "X"}])[0]["entry_time"] == ""


def test_paper_columns_hide_id_but_row_keeps_it():
    # trade_id is internal (row_key + lookups) and must NOT be a visible column.
    assert "trade_id" not in {c["field"] for c in paper.paper_columns()}
    assert paper.paper_rows([TRADE])[0]["trade_id"] == "T1"   # still on the row


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


# ── Calculated-field mapping (the detail panel reads these) ──────────────────
import datetime as _dt  # noqa: E402

RICH_TRADE = {
    "trade_id": "T9", "symbol": "INTC", "strategy": "PCS", "trade_type": "0-DTE",
    "expiration": "2030-01-01", "short_strike": 130.0, "long_strike": 129.0,
    "width": 1.0, "entry_credit": 0.55, "max_loss_total": 45.0,
    "breakeven": "129.45",            # stored as a STRING by paper_trader
    "short_delta": -0.5, "net_theta": -0.016, "entry_vega": 0.2,
    "underlying_at_entry": 129.06,
}


def test_synth_maps_stored_calculated_fields():
    s = paper.synth_from_trade(RICH_TRADE)
    assert s["breakeven"] == 129.45          # coerced from the stored string
    assert s["short_delta"] == -0.5
    assert s["net_theta"] == -0.016
    assert s["net_vega"] == -0.2             # reconstructed from -entry_vega
    assert s["underlying_price"] == 129.06
    assert s["width"] == 1.0


def test_synth_computes_live_dte_from_expiration():
    s = paper.synth_from_trade(RICH_TRADE)
    assert s["dte"] == (_dt.date(2030, 1, 1) - _dt.date.today()).days


def test_synth_pop_from_short_delta():
    # PoP ≈ (1 − |short delta|) × 100
    assert paper.synth_from_trade(RICH_TRADE)["pop_pct"] == 50.0


def test_synth_pop_none_when_delta_absent_or_zero():
    # short_delta == 0 is the stored default for a signal that lacked delta —
    # must NOT become a false 100% PoP.
    assert paper.synth_from_trade({"symbol": "X", "short_delta": 0})["pop_pct"] is None
    assert paper.synth_from_trade({"symbol": "X"})["pop_pct"] is None


def test_synth_breakeven_non_numeric_is_none():
    assert paper.synth_from_trade({"symbol": "X", "breakeven": ""})["breakeven"] is None
    assert paper.synth_from_trade({"symbol": "X"})["breakeven"] is None


def test_merge_detail_overlays_non_none_only():
    base = {"short_delta": None, "breakeven": 1.0, "x": 5}
    merged = paper.merge_detail(base, {"short_delta": -0.3, "breakeven": None, "y": 9})
    assert merged["short_delta"] == -0.3     # live value overlaid
    assert merged["breakeven"] == 1.0        # None in detail does NOT clobber base
    assert merged["x"] == 5 and merged["y"] == 9
    assert base["short_delta"] is None       # base not mutated


def test_merge_detail_none_or_empty_returns_base_copy():
    assert paper.merge_detail({"a": 1}, None) == {"a": 1}
    assert paper.merge_detail({"a": 1}, {}) == {"a": 1}


def test_paper_columns_include_actions():
    assert "actions" in {c["field"] for c in paper.paper_columns()}


def test_synth_from_trade_is_em_payload_compatible():
    from pages.options import handoff
    trade = {"trade_id": "t1", "strategy": "PCS", "symbol": "SPY",
             "expiration": "2026-07-18", "short_strike": 540, "long_strike": 535}
    payload = handoff.signal_to_em_payload(paper.synth_from_trade(trade))
    assert payload["symbol"] == "SPY"
    assert payload["legs"][0] == {"strike": 540.0, "option_type": "put", "side": "short"}


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
