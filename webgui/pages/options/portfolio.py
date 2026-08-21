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
from pages.fmt import round_or_none as _round  # the ONE copy (pages/fmt.py)
from pages import busy as _busy
from nicegui import ui

from pages.ui_guard import guard

from .perf_charts import equity_curve_figure, excursion_text
from .rescue import AT_RISK_STATES as _AT_RISK_STATES
from .rescue import heat_border_class, rescue_highlight, rescue_highlight
from .theme import (BADGE_MUTED, BADGE_NEG, BADGE_POS, BTN, BTN_3D_DANGER,
                    BTN_PRIMARY)

# rescue_state values that mark a position at-risk. The options_svc manage cycle
# tags THIS view (cache:options:paper_account) with rescue_state/heat via
# handlers._apply_rescue_overlay, so the highlight is live here (unlike the
# paper-trades ledger / captured views, where the overlay is absent).

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


def side_badge_class(side):
    """Deep Slate side-badge token: SELL → green pill, BUY → red pill (a credit
    seller reads SELL_TO_OPEN as the "good" side); anything else → muted."""
    s = (side or "").upper()
    if "SELL" in s:
        return BADGE_POS
    if "BUY" in s:
        return BADGE_NEG
    return BADGE_MUTED


def order_rows(orders):
    rows = []
    for o in orders or []:
        rows.append({
            "order_id": o.get("order_id"),
            "ts": (o.get("ts") or "")[:19],
            "side": o.get("side", ""),
            "_side_class": side_badge_class(o.get("side")),
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
    # No page title — the tab strip names the page (2026-07-11 dead-space cleanup).
    # Action buttons right-justified with the tables (mirrors Captured Signals);
    # the counts status renders BELOW the fills table, bottom-right, small.
    with ui.row().classes("items-center gap-2 flex-wrap w-full justify-end"):
        ui.button("Reload", icon="refresh", color=None, on_click=lambda: _reload()) \
            .props("no-caps").classes(BTN)
        ui.button("Run entry cycle", icon="login", color=None, on_click=lambda: _cycle("entry")) \
            .props("no-caps").classes(BTN) \
            .tooltip("Simulate auto-entry: scan open captured signals and open paper "
                     "positions for the eligible ones (fills via the paper broker).")
        ui.button("Run manage cycle", icon="manage_accounts", color=None, on_click=lambda: _cycle("manage")) \
            .props("no-caps").classes(BTN_PRIMARY) \
            .tooltip("Reprice open positions and auto-close any that hit their target/stop. "
                     "Runs automatically every 5 min during market hours; this button forces "
                     "an immediate run.")
        ui.button("Reset", icon="restart_alt", color=None, on_click=lambda: _reset()) \
            .props("no-caps").classes(BTN_3D_DANGER) \
            .tooltip("Reset the paper account to a starting balance.")

    account_box = ui.column().classes("w-full gap-0")
    with account_box:
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
        # Side as a Deep Slate pill (SELL green / BUY red).
        ord_table.add_slot('body-cell-side', r'''
          <q-td :props="props">
            <q-badge :class="props.row._side_class" :label="props.value"/>
          </q-td>
        ''')
        with ui.row().classes("w-full justify-end"):
            status = ui.label("").classes("opacity-60 text-xs")

    # ── Analytics: realized equity curve + MAE/MFE (scanner-baseline book) ────
    # Same builders as the driver monitor, so this book (auto-trades every captured
    # signal) reads directly against the driver book (Claude's selection) — the
    # benchmark that shows whether the decider adds edge over the raw scanner.
    ui.separator().classes("mt-3")
    ui.label("Analytics").classes("text-subtitle1")
    ui.label("Realized equity curve + how far trades ran for/against before closing "
             "(MAE/MFE) for the manual (scanner-baseline) book. Compare against the "
             "driver's Analytics on the Claude Driver page.").classes("text-xs opacity-50")
    equity_chart = ui.highchart(equity_curve_figure([])).classes("w-full")
    excursion_label = ui.label("").classes("text-xs opacity-60")
    analytics_empty = ui.label("No closed paper trades yet — analytics populate as "
                               "positions close.").classes("text-xs opacity-50")

    @guard
    def _populate_analytics(a):
        a = a or {}
        curve = a.get("equity_curve") or []
        equity_chart.options = equity_curve_figure(curve)
        equity_chart.update()
        excursion_label.text = excursion_text(a.get("excursions"))
        analytics_empty.set_visibility(not (curve or excursion_label.text))

    # Last-seen bus cache versions for the fetch-free repaint timers.
    seen = {"version": None, "analytics": None}

    # Refresh / manage / reset all round-trip through the service; until the new
    # payload lands the cards and tables show the pre-action account.
    account_busy = _busy.build_busy(account_box, "Refreshing the account…")

    def _populate(pa):
        """Paint the cards + tables from the cached paper-account view."""
        account_busy.hide()
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
        account_busy.show()
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
                ui.button("Confirm", color=None, on_click=confirm).props("no-caps").classes(BTN_3D_DANGER)
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["version"] = bus_client.read_version("options:paper_account")
    _populate(bus_client.read("options:paper_account") or {})
    seen["analytics"] = bus_client.read_version("options:paper_analytics")
    _populate_analytics(bus_client.read("options:paper_analytics") or {})

    @guard
    def _maybe_repaint():
        # Fetch-free: compare the bus cache version to the last-painted one and
        # only re-read + repaint on change. The service bumps it after every
        # paper action (refresh/entry/manage/reset).
        version = bus_client.read_version("options:paper_account")
        if version != seen["version"]:
            seen["version"] = version
            _populate(bus_client.read("options:paper_account") or {})
        av = bus_client.read_version("options:paper_analytics")
        if av != seen["analytics"]:
            seen["analytics"] = av
            _populate_analytics(bus_client.read("options:paper_analytics") or {})

    ui.timer(2.0, _maybe_repaint)
