"""Tests for the Paper Portfolio pure transforms + bus-reader migration."""
import inspect

from pages.options import portfolio

SNAP = {
    "equity": 25100.0, "cash": 24000.0, "buying_power_reserved": 1000.0,
    "open_unrealized": 100.0, "session_pnl": 50.0, "realized_pnl": 75.0,
    "open_count": 2, "halted": False,
}

POS = {
    "position_id": 1, "symbol": "SPY", "strategy": "PCS", "short_strike": 450,
    "long_strike": 445, "expiration": "2026-06-19", "quantity": 2,
    "entry_credit": 0.34, "max_loss_total": 932.0, "current_value": 0.2,
    "unrealized_pnl": 28.0, "status": "OPEN",
}

ORD = {
    "order_id": 10, "ts": "2026-06-10T10:00:00", "side": "SELL", "symbol": "SPY",
    "quantity": 2, "order_type": "LIMIT", "fill_price": 0.34, "status": "FILLED",
    "reject_reason": None,
}


def test_account_cards_labels_and_values():
    cards = portfolio.account_cards(SNAP)
    labels = {c[0] for c in cards}
    assert {"Equity", "Cash", "Session P&L", "Open"} <= labels
    equity = dict((c[0], c[1]) for c in cards)["Equity"]
    assert "25,100" in equity


def test_account_cards_engine_status():
    halted = dict((c[0], c[1]) for c in portfolio.account_cards({**SNAP, "halted": True}))
    assert halted["Engine"] == "HALTED"


def test_position_rows_maps_and_keeps_id():
    rows = portfolio.position_rows([POS])
    assert rows[0]["id"] == 1
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["unrealized_pnl"] == 28.0


def test_rescue_highlight_zones():
    # at-risk states return the shared heat border classes; others return ''.
    assert portfolio.rescue_highlight("critical", 90) == portfolio.heat_border_class(90)
    assert portfolio.rescue_highlight("tested", 60) == portfolio.heat_border_class(60)
    assert portfolio.rescue_highlight("watch", 40) == ""
    assert portfolio.rescue_highlight("ok", 0) == ""
    assert portfolio.rescue_highlight(None, None) == ""


def test_position_rows_healthy_has_no_rescue_tint():
    # POS carries no rescue_state -> no highlight (safe no-op for normal rows).
    assert portfolio.position_rows([POS])[0]["_rescue_class"] == ""


def test_position_rows_at_risk_gets_heat_tint():
    tested = {**POS, "rescue_state": "critical", "heat": 88.0}
    row = portfolio.position_rows([tested])[0]
    assert row["_rescue_class"] == portfolio.heat_border_class(88.0)
    assert row["_rescue_class"]  # non-empty
    assert "border-l-4" in row["_rescue_class"]


def test_order_rows_maps():
    rows = portfolio.order_rows([ORD])
    assert rows[0]["order_id"] == 10
    assert rows[0]["side"] == "SELL"
    assert rows[0]["fill_price"] == 0.34


def test_columns_present():
    assert {c["field"] for c in portfolio.position_columns()} >= {"symbol", "unrealized_pnl", "status"}
    assert {c["field"] for c in portfolio.order_columns()} >= {"side", "fill_price", "status"}


# ── Migration regression: the page does NO engine/proxy/DB call ──────────────
def test_page_imports_no_engine_or_proxy():
    """The page must not import the engine/proxy/DB or use the scoring guard —
    those reads/actions moved to the options service (Task 2.6c-1)."""
    src = inspect.getsource(portfolio)
    for forbidden in ("paper_engine", "paper_account_db", "signal_db",
                      "options_scoring", "import proxy", "OPTIONS_SCANNER",
                      "engines"):
        assert forbidden not in src, f"portfolio.py still references {forbidden!r}"


def test_no_engine_attrs_on_module():
    """No engine/proxy modules leaked onto the page namespace."""
    for attr in ("paper_engine", "paper_account_db", "signal_db", "proxy"):
        assert not hasattr(portfolio, attr)


def test_render_graceful_empty(monkeypatch):
    """render() paints the no-account state from an empty bus cache without error."""
    import bus_client

    monkeypatch.setattr(bus_client, "read", lambda view: None)
    monkeypatch.setattr(bus_client, "read_version", lambda view: None)

    # render() builds NiceGUI widgets; a bare call needs a slot context. We assert
    # it runs end-to-end against an empty cache (graceful-empty) inside one.
    from nicegui import ui

    with ui.card():  # provide a parent slot for the widgets
        portfolio.render()


def test_side_badge_class():
    from pages.options import portfolio, theme
    assert portfolio.side_badge_class("SELL_TO_OPEN") == theme.BADGE_POS
    assert portfolio.side_badge_class("BUY_TO_CLOSE") == theme.BADGE_NEG
    assert portfolio.side_badge_class("") == theme.BADGE_MUTED
    assert portfolio.side_badge_class(None) == theme.BADGE_MUTED
