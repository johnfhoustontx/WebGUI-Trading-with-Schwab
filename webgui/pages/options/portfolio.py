"""Paper Portfolio page (Tier-3 reader).

This page holds **no engine/proxy/DB call**. The paper-account reads
(snapshot + open positions + fills) and the entry/manage/reset actions live in
``services/options_svc/compute`` + ``handlers``; the service writes the account
view under ``cache:options:paper_account`` and re-publishes it after every
action. This page only **reads** that payload and formats it, and enqueues
commands (``refresh_paper`` / ``paper_entry`` / ``paper_manage`` /
``paper_reset``) onto the Redis bus.

The cross-app ``scoring`` collision guard is gone (the service process loads no
sentiment code; the page does no engine call). A fetch-free version-poll
``ui.timer`` repaints when the bus cache version changes (graceful-empty when
the service is cold / no account exists yet).
"""
import bus_client
from nicegui import ui

from pages.ui_guard import guard

from .rescue import heat_border_class
from .theme import BTN_3D

# rescue_state values that mark a position at-risk. The options_svc manage cycle
# tags THIS view (cache:options:paper_account) with rescue_state/heat via
# handlers._apply_rescue_overlay, so the highlight is live here (unlike the
# paper-trades ledger / captured views, where the overlay is absent).
_AT_RISK_STATES = ("tested", "critical")


def rescue_highlight(state, heat):
    """Left-border Tailwind classes for an at-risk row's symbol cell, or '' (no
    tint) otherwise.

    Defensive: a missing/None ``state`` yields no highlight, so healthy rows look
    unchanged. The class set comes from the shared ``heat_border_class`` (rescue.py)."""
    return heat_border_class(heat) if state in _AT_RISK_STATES else ""


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


def _money(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def account_cards(snapshot):
    """[(label, value), ...] for the account summary cards."""
    s = snapshot or {}
    return [
        ("Equity", _money(s.get("equity"))),
        ("Cash", _money(s.get("cash"))),
        ("BP Reserved", _money(s.get("buying_power_reserved"))),
        ("Session P&L", _money(s.get("session_pnl"))),
        ("Total P&L", _money(s.get("realized_pnl"))),
        ("Open", str(s.get("open_count", 0))),
        ("Engine", "HALTED" if s.get("halted") else "RUNNING"),
    ]


def position_columns():
    spec = [
        ("position_id", "ID"), ("symbol", "Symbol"), ("strategy", "Strat"),
        ("strikes", "Strikes"), ("expiration", "Exp"), ("quantity", "Qty"),
        ("entry_credit", "Credit"), ("current_value", "CurVal"),
        ("unrealized_pnl", "P&L$"), ("status", "Status"),
    ]
    return [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]


def position_rows(positions):
    rows = []
    for p in positions or []:
        sk, lk = p.get("short_strike"), p.get("long_strike")
        rows.append({
            "id": p.get("position_id"),
            "position_id": p.get("position_id"),
            "symbol": p.get("symbol", ""),
            "strategy": p.get("strategy", ""),
            "strikes": f"{sk}/{lk}" if sk is not None else "—",
            "expiration": p.get("expiration", ""),
            "quantity": p.get("quantity"),
            "entry_credit": _round(p.get("entry_credit")),
            "current_value": _round(p.get("current_value")),
            "unrealized_pnl": _round(p.get("unrealized_pnl")),
            "status": p.get("status", ""),
            # At-risk rescue tint on the symbol cell (left border), fed from the
            # manage-cycle rescue overlay on this view. Safe no-op ('') when the
            # position is healthy / carries no rescue_state.
            "_rescue_class": rescue_highlight(p.get("rescue_state"), p.get("heat")),
        })
    return rows


def order_columns():
    spec = [
        ("order_id", "OrderID"), ("ts", "Time"), ("side", "Side"),
        ("symbol", "Symbol"), ("quantity", "Qty"), ("order_type", "Type"),
        ("fill_price", "Fill$"), ("status", "Status"), ("reject_reason", "Reason"),
    ]
    return [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]


def order_rows(orders):
    rows = []
    for o in orders or []:
        rows.append({
            "order_id": o.get("order_id"),
            "ts": (o.get("ts") or "")[:19],
            "side": o.get("side", ""),
            "symbol": o.get("symbol", ""),
            "quantity": o.get("quantity"),
            "order_type": o.get("order_type", ""),
            "fill_price": _round(o.get("fill_price")),
            "status": o.get("status", ""),
            "reject_reason": o.get("reject_reason") or "",
        })
    return rows


def render():
    """Paper Portfolio page: account cards + positions + fills log (bus-fed)."""
    ui.label("Paper Portfolio").classes("text-h5")

    with ui.row().classes("items-center gap-2 flex-wrap w-full"):
        ui.button("Reload", icon="refresh", on_click=lambda: _reload())
        ui.button("Run entry cycle", icon="login", color=None, on_click=lambda: _cycle("entry")) \
            .props("no-caps").classes(BTN_3D) \
            .tooltip("Simulate auto-entry: scan open captured signals and open paper "
                     "positions for the eligible ones (fills via the paper broker).")
        ui.button("Run manage cycle", icon="manage_accounts", color=None, on_click=lambda: _cycle("manage")) \
            .props("no-caps").classes(BTN_3D) \
            .tooltip("Reprice open positions and auto-close any that hit their target/stop. "
                     "Runs automatically every 5 min during market hours; this button forces "
                     "an immediate run.")
        ui.space()
        ui.button("Reset", icon="restart_alt", on_click=lambda: _reset()) \
            .props("flat dense size=sm color=negative") \
            .tooltip("Reset the paper account to a starting balance.")
    status = ui.label("").classes("opacity-70")

    cards_box = ui.row().classes("gap-3 flex-wrap")
    ui.label("Open positions").classes("text-subtitle1 mt-2")
    pos_table = ui.table(columns=position_columns(), rows=[], row_key="id").classes("w-full")
    # Symbol cell gets a colored left-border + faint tint when the position is
    # at-risk (rescue_state tested/critical, from the manage-cycle overlay).
    pos_table.add_slot('body-cell-symbol', r'''
      <q-td :props="props">
        <span v-if="props.row._rescue_class" :class="props.row._rescue_class + ' pl-1.5'">
          {{ props.value }}
        </span>
        <span v-else>{{ props.value }}</span>
      </q-td>
    ''')
    ui.label("Fills log (last 100)").classes("text-subtitle1 mt-2")
    ord_table = ui.table(columns=order_columns(), rows=[], row_key="order_id").classes("w-full")

    # Last-seen bus cache version for the fetch-free repaint timer.
    seen = {"version": None}

    def _populate(pa):
        """Paint the cards + tables from the cached paper-account view."""
        pa = pa or {}
        snap = pa.get("snapshot")
        has_account = pa.get("has_account")
        cards_box.clear()
        with cards_box:
            if not pa or snap is None or has_account is False:
                ui.label("No paper account yet — use Reset to initialize.") \
                    .classes("opacity-70")
            else:
                for label, value in account_cards(snap):
                    with ui.card().classes("p-2 min-w-[110px]"):
                        ui.label(label).classes("text-xs opacity-60")
                        ui.label(value).classes("text-base font-bold")
        pos_table.rows = position_rows(pa.get("positions"))
        ord_table.rows = order_rows(pa.get("orders"))
        pos_table.update()
        ord_table.update()
        if not pa:
            status.text = ""
        else:
            status.text = f"{len(pos_table.rows)} open positions, {len(ord_table.rows)} fills."

    @guard
    def _reload():
        bus_client.request("options", {"type": "refresh_paper"})
        ui.notify("Reloading paper account…")
        status.text = "Reloading…"

    @guard
    def _cycle(kind):
        cmd = "paper_entry" if kind == "entry" else "paper_manage"
        bus_client.request("options", {"type": cmd})
        ui.notify(f"Running {kind} cycle…")
        status.text = f"Running {kind} cycle…"

    @guard
    def _reset():
        with ui.dialog() as dlg, ui.card():
            ui.label("Reset paper account?").classes("text-subtitle1")
            bal = ui.number("Starting balance", value=25000.0, format="%.2f")

            def confirm():
                bus_client.request(
                    "options",
                    {"type": "paper_reset", "args": {"starting_balance": float(bal.value)}})
                dlg.close()
                ui.notify("Paper account reset requested.", type="positive")
                status.text = "Resetting…"

            with ui.row():
                ui.button("Confirm", on_click=confirm).props("color=negative")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["version"] = bus_client.read_version("options:paper_account")
    _populate(bus_client.read("options:paper_account") or {})

    @guard
    def _maybe_repaint():
        # Fetch-free: compare the bus cache version to the last-painted one and
        # only re-read + repaint on change. The service bumps it after every
        # paper action (refresh/entry/manage/reset).
        version = bus_client.read_version("options:paper_account")
        if version == seen["version"]:
            return
        seen["version"] = version
        _populate(bus_client.read("options:paper_account") or {})

    ui.timer(2.0, _maybe_repaint)
