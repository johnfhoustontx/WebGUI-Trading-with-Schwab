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
import pytest
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
    assert {"symbol", "strategy", "quantity", "status", "pnl"} <= fields


def test_paper_columns_renamed_headers():
    """Credit$/Risk$/P&L$ drop the '$' (dollars are implied by the values)."""
    labels = {c["field"]: c["label"] for c in paper.paper_columns()}
    assert labels["entry_credit_total"] == "Credit"
    assert labels["max_loss_total"] == "Risk"
    assert labels["pnl"] == "P&L"


def test_paper_rows_latest_first():
    """Default order is newest entry_time on top."""
    trades = [
        {"trade_id": "old", "symbol": "A", "entry_time": "2026-06-01T09:00:00"},
        {"trade_id": "new", "symbol": "B", "entry_time": "2026-06-10T09:00:00"},
        {"trade_id": "mid", "symbol": "C", "entry_time": "2026-06-05T09:00:00"},
    ]
    assert [r["id"] for r in paper.paper_rows(trades)] == ["new", "mid", "old"]


def test_paper_rows_pnl_open_uses_unrealized():
    t = {"trade_id": "o", "status": "OPEN", "unrealized_pnl": 123.4, "realized_pnl": None}
    assert paper.paper_rows([t])[0]["pnl"] == 123.4


def test_paper_rows_pnl_closed_uses_realized():
    t = {"trade_id": "c", "status": "CLOSED", "realized_pnl": -50.0, "unrealized_pnl": 999}
    assert paper.paper_rows([t])[0]["pnl"] == -50.0


def test_paper_rows_pnl_none_when_open_unpriced():
    t = {"trade_id": "u", "status": "OPEN", "realized_pnl": None}
    assert paper.paper_rows([t])[0]["pnl"] is None


def test_pnl_color():
    assert paper.pnl_color(10) == "#66bb6a"
    assert paper.pnl_color(-10) == "#ef5350"
    assert paper.pnl_color(0) == "#bdbdbd"
    assert paper.pnl_color(None) == "#bdbdbd"


def test_pnl_class():
    # Paper's neutral is grey #bdbdbd (NOT '' like captured) for 0 / None.
    assert paper.pnl_class(10) == "text-[#66bb6a]"
    assert paper.pnl_class(-10) == "text-[#ef5350]"
    assert paper.pnl_class(0) == "text-[#bdbdbd]"
    assert paper.pnl_class(None) == "text-[#bdbdbd]"


def test_verdict_class():
    assert paper.verdict_class("TAKE PROFIT") == "bg-[#66bb6a]"
    assert paper.verdict_class("hold") == "bg-[#ffa726]"
    assert paper.verdict_class("CLOSE") == "bg-[#ef5350]"
    assert paper.verdict_class("—") == "bg-[#bdbdbd]"


def test_paper_rows_stamp_pnl_class():
    t = {"trade_id": "o", "status": "OPEN", "unrealized_pnl": 12.0}
    assert paper.paper_rows([t])[0]["_pnl_class"] == "text-[#66bb6a]"


# ── Analyze popup helpers ────────────────────────────────────────────────────
def test_verdict_color():
    assert paper.verdict_color("TAKE PROFIT") == "#66bb6a"
    assert paper.verdict_color("hold") == "#ffa726"
    assert paper.verdict_color("CLOSE") == "#ef5350"
    assert paper.verdict_color("—") == "#bdbdbd"
    assert paper.verdict_color(None) == "#bdbdbd"


def test_analyze_popup_rows_builds_available_metrics():
    res = {"metrics": {"unrealized_pnl": 120.5, "unrealized_pnl_pct": 33.0,
                       "underlying_now": 515.76, "dte_remaining": 6,
                       "target_pct": 65.0, "breakeven": 510.0}}
    rows = paper.analyze_popup_rows(res)
    by_label = {r[0]: r[1] for r in rows}
    assert by_label["Unrealized P&L"] == "+120.50"
    assert by_label["% of max profit"] == "+33.0%"
    assert by_label["Current price"] == "515.76"
    assert by_label["DTE remaining"] == "6"
    assert by_label["Profit target"] == "65%"
    assert by_label["Breakeven"] == "510.00"
    # P&L row carries a green color for a profit.
    pnl_row = next(r for r in rows if r[0] == "Unrealized P&L")
    assert pnl_row[2] == "#66bb6a"


def test_analyze_popup_rows_skips_absent_metrics():
    assert paper.analyze_popup_rows({}) == []
    assert paper.analyze_popup_rows({"metrics": {"unrealized_pnl": None}}) == []


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


def test_strikes_debit_legs():
    long_call = {"strategy": "LONG_CALL", "direction": "DEBIT",
                 "legs": [{"kind": "call", "side": "long", "strike": 450}]}
    assert paper._strikes(long_call) == "L 450C"
    debit_spread = {"strategy": "BULL_CALL", "direction": "DEBIT",
                    "legs": [{"kind": "call", "side": "long", "strike": 100},
                             {"kind": "call", "side": "short", "strike": 105}]}
    assert paper._strikes(debit_spread) == "L 100C / S 105C"
    # a debit trade with no legs falls back to "—" (None-safe, no crash)
    assert paper._strikes({"strategy": "LONG_CALL", "direction": "DEBIT"}) == "—"


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


def test_synth_max_loss_is_per_share_not_whole_position():
    # max_loss_total is max_loss * quantity * 100 (paper_trader.py:117).
    # With qty=3 and a $3.45/share max loss, the stored total is $1035.
    trade = {"symbol": "SPY", "entry_credit": 1.55, "max_loss_total": 1035.0,
             "quantity": 3, "expiration": "2099-01-15"}
    s = paper.synth_from_trade(trade)
    # credit is per-share, so max_loss must be per-share too.
    assert s["max_loss"] == pytest.approx(3.45)
    assert s["credit"] == pytest.approx(1.55)


def test_synth_max_loss_none_when_quantity_missing():
    # Without quantity the total cannot be reduced to per-share. Better to show
    # nothing than a figure that is wrong by a factor of the position size.
    trade = {"symbol": "SPY", "entry_credit": 1.55, "max_loss_total": 1035.0,
             "expiration": "2099-01-15"}
    assert paper.synth_from_trade(trade)["max_loss"] is None


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


def test_status_badge_class():
    from pages.options import paper, theme
    assert paper.status_badge_class("OPEN") == theme.BADGE_ACCENT
    assert paper.status_badge_class("open") == theme.BADGE_ACCENT
    assert paper.status_badge_class("EXPIRED") == theme.BADGE_MUTED
    assert paper.status_badge_class("CLOSED") == theme.BADGE_MUTED
    assert paper.status_badge_class(None) == theme.BADGE_MUTED
