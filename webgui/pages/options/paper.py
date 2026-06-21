"""Paper Trades page (Tier-3 reader).

Lists the paper-trade ledger with the shared Trade detail panel. This page holds
**no engine/proxy/DB call**: the ledger read and the close/delete/delete-all/
analyze actions live in
``services/options_svc/compute`` + ``handlers``; the service writes the ledger
view under ``cache:options:paper_trades`` and re-publishes it after every action.
This page only **reads** that payload and formats it, and enqueues commands
(``paper_reload`` / ``paper_close`` / ``paper_delete`` / ``paper_delete_closed`` /
``paper_analyze``) onto the Redis bus.

A fetch-free version-poll ``ui.timer`` repaints the ledger when its bus cache
version changes; a second watch on ``options:paper_analyze`` surfaces the analyze
result via ``ui.notify`` when it lands. Dialogs (the close debit input) stay
client-side (input collection only). Graceful-empty when the service is cold.
"""
import bus_client
from nicegui import ui

from pages.ui_guard import guard

from . import detail, handoff
from .rescue import heat_color

# rescue_state values that mark a trade at-risk (tested/critical). The manage-cycle
# rescue overlay tags the paper *account* positions view; the paper-trades ledger
# this page renders carries no rescue_state in the common case, so this highlight
# is a safe no-op unless a trade row is explicitly flagged.
_AT_RISK_STATES = ("tested", "critical")


def rescue_highlight(state, heat):
    """Left-border color for an at-risk row, or '' (no tint) otherwise.

    Defensive: a missing/None ``state`` yields no highlight, so normal rows look
    unchanged."""
    return heat_color(heat) if state in _AT_RISK_STATES else ""


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


def paper_columns():
    spec = [
        ("trade_id", "ID"), ("symbol", "Symbol"), ("strategy", "Strat"),
        ("strikes", "Strikes"), ("expiration", "Exp"), ("quantity", "Qty"),
        ("entry_credit_total", "Credit$"), ("max_loss_total", "Risk$"),
        ("realized_pnl", "P&L$"), ("status", "Status"), ("entry_time", "Entry"),
    ]
    cols = [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]
    cols.append({"name": "actions", "label": "", "field": "actions", "align": "center"})
    return cols


def _strikes(t):
    if t.get("strategy") == "IC":
        return f"P {t.get('short_strike','?')}/{t.get('long_strike','?')} " \
               f"C {t.get('call_short','?')}/{t.get('call_long','?')}"
    sk, lk = t.get("short_strike"), t.get("long_strike")
    return f"{sk}/{lk}" if sk is not None else "—"


def paper_rows(trades):
    rows = []
    for t in trades or []:
        rows.append({
            "id": t.get("trade_id"),
            "trade_id": t.get("trade_id"),
            "symbol": t.get("symbol", ""),
            "strategy": t.get("strategy", ""),
            "strikes": _strikes(t),
            "expiration": t.get("expiration", ""),
            "quantity": t.get("quantity"),
            "entry_credit_total": _round(t.get("entry_credit_total")),
            "max_loss_total": _round(t.get("max_loss_total")),
            "realized_pnl": _round(t.get("realized_pnl")),
            "status": t.get("status", ""),
            "entry_time": (t.get("entry_time") or "")[:19],
            # At-risk rescue tint (left border on the symbol cell). Safe no-op
            # when the trade carries no rescue_state (the usual case).
            "_rescue_color": rescue_highlight(t.get("rescue_state"), t.get("heat")),
        })
    return rows


def _num(v):
    """Coerce to float, or None — handles values stored as strings (e.g. breakeven)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dte_from_expiration(exp):
    """Days from today to the expiration ISO date; None if unparseable."""
    try:
        import datetime as dt
        return (dt.date.fromisoformat(str(exp)[:10]) - dt.date.today()).days
    except (TypeError, ValueError):
        return None


def synth_from_trade(trade):
    """Detail-panel signal dict from a paper-trade dict.

    Maps the calculated fields the trade ALREADY stores (breakeven [stored as a
    string], the entry greeks, underlying, width) — which the detail panel reads
    — plus live DTE from the expiration and a delta-approximation PoP. Fields the
    trade never captured at entry (IV, composite score) stay absent, so the panel
    shows '—' rather than a fabricated value."""
    t = trade or {}
    delta = _num(t.get("short_delta"))
    entry_vega = _num(t.get("entry_vega"))
    # PoP ≈ 1 − |short-leg delta| (standard quick estimate). Skip the stored 0
    # default (a signal that lacked delta) so we never show a false 100%.
    pop = round((1.0 - abs(delta)) * 100, 1) if delta else None
    return {
        "symbol": t.get("symbol", ""),
        "type": t.get("strategy", ""),
        "trade_type": t.get("trade_type", ""),
        "credit": _num(t.get("entry_credit")),
        "max_loss": _num(t.get("max_loss_total")),
        "expiration": t.get("expiration", ""),
        "short_strike": t.get("short_strike"),
        "long_strike": t.get("long_strike"),
        "call_short": t.get("call_short"),
        "call_long": t.get("call_long"),
        "width": _num(t.get("width")),
        "breakeven": _num(t.get("breakeven")),
        "dte": _dte_from_expiration(t.get("expiration")),
        "short_delta": delta,
        "net_theta": _num(t.get("net_theta")),
        "net_vega": (-entry_vega) if entry_vega is not None else None,
        "underlying_price": _num(t.get("underlying_at_entry")),
        "pop_pct": pop,
        "id": t.get("trade_id"),
    }


def merge_detail(base, detail):
    """Overlay non-None live-analyze ``detail`` fields onto a synth signal dict.

    Returns a NEW dict (base is not mutated); a None field in ``detail`` never
    clobbers the stored value, so missing live data keeps the entry-time view."""
    out = dict(base or {})
    for k, v in (detail or {}).items():
        if v is not None:
            out[k] = v
    return out


def render():
    """Paper Trades page: ledger table (left) + shared detail panel (right), bus-fed."""
    ui.label("Paper Trades").classes("text-h5")

    raw_by_id: dict = {}
    # sel_id: selected trade; live: {trade_id: live-analyze detail} overlay cache.
    state = {"sel_id": None, "live": {}}

    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("flex-grow min-w-0"):
            with ui.row().classes("items-center gap-2 flex-wrap"):
                ui.button("Reload", icon="refresh", on_click=lambda: _reload())
                ui.button("Close selected", icon="check_circle",
                          on_click=lambda: _close()).props("outline")
                ui.button("Analyze selected", icon="biotech",
                          on_click=lambda: _analyze()).props("outline")
                ui.button("Delete selected", icon="delete",
                          on_click=lambda: _delete()).props("outline color=negative")
                ui.button("Delete all closed", icon="delete_sweep",
                          on_click=lambda: _delete_closed()).props("flat color=negative")
            status = ui.label("").classes("opacity-70")
            table = ui.table(columns=paper_columns(), rows=[], row_key="id",
                             selection="single").classes("w-full")
            # Symbol cell gets a colored left-border + faint tint when the row is
            # at-risk (rescue_state tested/critical). Plain cell otherwise.
            table.add_slot('body-cell-symbol', r'''
              <q-td :props="props">
                <span v-if="props.row._rescue_color"
                      :style="`border-left:4px solid ${props.row._rescue_color};
                               padding-left:6px;
                               background:${props.row._rescue_color}22`">
                  {{ props.value }}
                </span>
                <span v-else>{{ props.value }}</span>
              </q-td>
            ''')
        detail_panel = detail.render()

    # Last-seen bus cache versions for the fetch-free repaint/notify timers.
    seen = {"trades": None, "analyze": None}

    def _render_detail(trade):
        """Paint the detail panel for ``trade``: stored synth view + any cached
        live-analyze overlay for that trade."""
        if not trade:
            detail_panel.clear()
            return
        base = synth_from_trade(trade)
        live = state["live"].get(trade.get("trade_id"))
        detail_panel.update(merge_detail(base, live))

    @guard
    def _request_analyze(trade_id, symbol=""):
        bus_client.request("options",
                           {"type": "paper_analyze", "args": {"trade_id": trade_id}})
        status.text = f"Analyzing {symbol} live…" if symbol else "Analyzing live…"

    def _populate(pt):
        """Paint the ledger table from the cached paper-trades view."""
        pt = pt or {}
        trades = pt.get("trades") or []
        raw_by_id.clear()
        for t in trades:
            if t.get("trade_id"):
                raw_by_id[t["trade_id"]] = t
        table.rows = paper_rows(trades)
        table.update()
        # Keep the open detail panel in sync with the freshly-cached trades
        # (preserving any live-analyze overlay for the selected trade).
        sel = state.get("sel_id")
        if sel and sel in raw_by_id:
            _render_detail(raw_by_id[sel])
        elif sel:
            detail_panel.clear()  # selected trade no longer present
        if not pt:
            status.text = ""
        else:
            status.text = f"{len(table.rows)} trades."

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        t = raw_by_id.get(row.get("id")) if isinstance(row, dict) else None
        if t:
            state["sel_id"] = t.get("trade_id")
            _render_detail(t)                     # instant stored-data view
            _request_analyze(t.get("trade_id"), t.get("symbol", ""))  # live overlay

    table.on("rowClick", _select)
    # Per-row Expected Move button only (Calculator / Paper actions don't belong
    # on a paper-trade ledger). ``synth_from_trade`` maps the raw paper trade to a
    # signal-shaped dict (``type``/``expiration``/``*_strike``) that
    # ``signal_to_em_payload`` understands.
    handoff.add_expected_move_action(
        table, lambda row: synth_from_trade(raw_by_id.get(row.get("id"))))

    def _selected_trade():
        if not table.selected:
            ui.notify("Select a trade first.", type="warning")
            return None
        return raw_by_id.get(table.selected[0].get("id"))

    @guard
    def _reload():
        bus_client.request("options", {"type": "paper_reload"})
        ui.notify("Reloading paper trades…")
        status.text = "Reloading…"

    @guard
    def _close():
        t = _selected_trade()
        if not t:
            return
        with ui.dialog() as dlg, ui.card():
            ui.label(f"Close {t.get('symbol')} {t.get('strategy')}").classes("text-subtitle1")
            debit = ui.number("Exit debit (per spread)", value=0.0, format="%.2f")

            def confirm():
                bus_client.request("options", {
                    "type": "paper_close",
                    "args": {"trade_id": t.get("trade_id"), "debit": float(debit.value)},
                })
                dlg.close()
                ui.notify("Close requested.", type="positive")
                status.text = "Closing…"

            with ui.row():
                ui.button("Confirm", on_click=confirm).props("color=negative")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    @guard
    def _delete():
        t = _selected_trade()
        if not t:
            return
        bus_client.request("options",
                           {"type": "paper_delete", "args": {"trade_id": t.get("trade_id")}})
        ui.notify("Delete requested.", type="positive")
        status.text = "Deleting…"

    @guard
    def _delete_closed():
        bus_client.request("options", {"type": "paper_delete_closed"})
        ui.notify("Delete-all-closed requested.", type="positive")
        status.text = "Deleting closed…"

    @guard
    def _analyze():
        t = _selected_trade()
        if not t:
            return
        bus_client.request("options",
                           {"type": "paper_analyze", "args": {"trade_id": t.get("trade_id")}})
        ui.notify(f"Analyzing {t.get('symbol')}…")
        status.text = "Analyzing…"

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["trades"] = bus_client.read_version("options:paper_trades")
    seen["analyze"] = bus_client.read_version("options:paper_analyze")
    _populate(bus_client.read("options:paper_trades") or {})

    @guard
    def _maybe_repaint():
        # Fetch-free: only re-read + repaint the ledger when its version changes
        # (the service bumps it after reload/close/delete/delete-all). Also watch
        # the analyze view and surface its result via notify when it lands.
        version = bus_client.read_version("options:paper_trades")
        if version != seen["trades"]:
            seen["trades"] = version
            _populate(bus_client.read("options:paper_trades") or {})

        av = bus_client.read_version("options:paper_analyze")
        if av != seen["analyze"]:
            seen["analyze"] = av
            res = bus_client.read("options:paper_analyze") or {}
            tid, det = res.get("trade_id"), res.get("detail")
            if tid and det:
                state["live"][tid] = det              # cache the live overlay
            # Re-render with the live overlay if it's for the selected trade.
            if tid and tid == state.get("sel_id") and tid in raw_by_id:
                _render_detail(raw_by_id[tid])
            status.text = f"{len(table.rows)} trades." if table.rows else ""
            # Prefer the service's specific reason (e.g. "Expired … — no live
            # option chain") over a vague generic; fall back only if absent.
            reason = res.get("note") or ("" if det else "live data unavailable")
            suffix = f" — {reason}" if reason else ""
            ntype = "warning" if (reason and not det) else "info"
            ui.notify(f"{res.get('symbol')}: {res.get('action', '—')}{suffix}", type=ntype)

    ui.timer(2.0, _maybe_repaint)
