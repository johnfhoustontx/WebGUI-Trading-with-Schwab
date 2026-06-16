"""Captured Signals page (Tier-3 reader).

Lists open signals with live marks, score drift, and recommendations, alongside
the shared Trade detail panel. This page holds **no engine/proxy/DB call**: the
open-signals read and the reprice-marks + manual-close actions live in
``services/options_svc/compute`` + ``handlers``; the service writes the signals
view under ``cache:options:captured`` and re-publishes it after every action,
and the flags from a reprice under ``cache:options:captured_flags``. This page
only **reads** those payloads and formats them, and enqueues commands
(``captured_reload`` / ``captured_reprice`` / ``captured_close``) onto the Redis
bus.

A fetch-free version-poll ``ui.timer`` repaints the table when its bus cache
version changes; a second watch on ``options:captured_flags`` surfaces stop/target
hits via ``ui.notify`` when they land. The close dialog stays client-side (input
collection only). Graceful-empty when the service is cold.
"""
import bus_client
from nicegui import ui

from pages.ui_guard import guard

from . import detail


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


REC_RED, REC_AMBER, REC_GREEN = "#ef5350", "#ffa726", "#66bb6a"


def rec_color(rec):
    """Recommendation -> badge color (green take-profit / red cut / amber hold)."""
    return {"TAKE_PROFIT": REC_GREEN, "CUT": REC_RED, "HOLD": REC_AMBER}.get(rec, "#666666")


def captured_columns():
    spec = [
        ("symbol", "Symbol"), ("strategy", "Strat"), ("mode", "Mode"),
        ("expiration", "Exp"), ("dte", "DTE"), ("credit", "Credit"),
        ("max_loss", "Risk"), ("unrealized_pnl", "P&L"),
        ("entry_score", "Entry"), ("current_score", "Cur"), ("score_drift", "Drift"),
        ("grade", "Grade"), ("recommendation", "Rec"), ("status", "Status"),
    ]
    return [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]


def captured_rows(signals):
    """Display rows from the open-signals view (cache:options:captured)."""
    rows = []
    for s in signals or []:
        rows.append({
            "id": s.get("signal_id"),
            "symbol": s.get("symbol", ""),
            "strategy": s.get("strategy", ""),
            "mode": s.get("mode", ""),
            "expiration": s.get("expiration", ""),
            "dte": s.get("dte_at_entry"),
            "credit": _round(s.get("entry_credit")),
            "max_loss": _round(s.get("entry_max_loss")),
            "unrealized_pnl": _round(s.get("unrealized_pnl")),
            "entry_score": s.get("entry_score"),
            "current_score": s.get("current_score"),
            "score_drift": _round(s.get("score_drift")),
            "grade": s.get("entry_grade", ""),
            "recommendation": s.get("recommendation") or "HOLD",
            "status": s.get("status", ""),
            "_rec_color": rec_color(s.get("recommendation") or "HOLD"),
        })
    return rows


def synth_from_captured(row):
    """Build a detail-panel signal dict from a captured signal row."""
    r = row or {}
    score = r.get("current_score")
    if score is None:
        score = r.get("entry_score")
    return {
        "symbol": r.get("symbol", ""),
        "type": r.get("strategy", ""),
        "trade_type": r.get("scanner_type") or r.get("mode") or "",
        "composite_score": score,
        "grade": r.get("entry_grade", ""),
        "credit": r.get("entry_credit"),
        "max_loss": r.get("entry_max_loss"),
        "dte": r.get("dte_at_entry"),
        "expiration": r.get("expiration", ""),
        "short_strike": r.get("short_strike"),
        "long_strike": r.get("long_strike"),
        "call_short": r.get("call_short"),
        "call_long": r.get("call_long"),
        "width": r.get("width"),
        "short_delta": r.get("entry_short_delta"),
        "net_theta": r.get("entry_net_theta"),
        "underlying_price": r.get("current_underlying") or r.get("entry_underlying"),
        "iv_rank": r.get("entry_iv_rank"),
        "id": r.get("signal_id"),
    }


def render():
    """Captured Signals page: table (left) + shared detail panel (right), bus-fed."""
    ui.label("Captured Signals").classes("text-h5")

    raw_by_id: dict = {}

    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("flex-grow min-w-0"):
            with ui.row().classes("items-center gap-3"):
                ui.button("Reload", icon="refresh", on_click=lambda: _reload())
                ui.button("Refresh marks (live)", icon="published_with_changes",
                          on_click=lambda: _reprice())
                ui.button("Close selected", icon="check_circle",
                          on_click=lambda: _close()).props("outline")
                status = ui.label("").classes("opacity-70")
            table = ui.table(columns=captured_columns(), rows=[], row_key="id",
                             selection="single").classes("w-full")
            table.add_slot('body-cell-recommendation', r'''
              <q-td :props="props">
                <q-badge :style="`background:${props.row._rec_color};color:#111`" :label="props.value"/>
              </q-td>
            ''')
            # Drift shown as x.xx; value stays numeric so the column still sorts.
            table.add_slot('body-cell-score_drift', r'''
              <q-td :props="props">
                {{ props.value == null ? '' : Number(props.value).toFixed(2) }}
              </q-td>
            ''')
        detail_panel = detail.render()

    # Last-seen bus cache versions for the fetch-free repaint/notify timers.
    seen = {"captured": None, "flags": None}

    def _populate(cap):
        """Paint the signals table from the cached captured view."""
        cap = cap or {}
        sigs = cap.get("signals") or []
        raw_by_id.clear()
        for s in sigs:
            if s.get("signal_id"):
                raw_by_id[s["signal_id"]] = s
        table.rows = captured_rows(sigs)
        table.update()
        status.text = f"{len(table.rows)} open signals." if cap else ""

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sig = raw_by_id.get(row.get("id")) if isinstance(row, dict) else None
        if sig:
            detail_panel.update(synth_from_captured(sig))

    table.on("rowClick", _select)

    @guard
    def _reload():
        bus_client.request("options", {"type": "captured_reload"})
        ui.notify("Reloading captured signals…")
        status.text = "Reloading…"

    @guard
    def _reprice():
        bus_client.request("options", {"type": "captured_reprice"})
        ui.notify("Repricing open signals…")
        status.text = "Repricing…"

    @guard
    def _close():
        sel = table.selected
        if not sel:
            ui.notify("Select a signal first.", type="warning")
            return
        row = sel[0]
        with ui.dialog() as dlg, ui.card():
            ui.label(f"Close {row.get('symbol')} {row.get('strategy')}").classes("text-subtitle1")
            exit_val = ui.number("Exit value (spread debit)", value=0.0, format="%.2f")
            reason = ui.input("Reason", value="MANUAL_CLOSE")

            def confirm():
                bus_client.request("options", {
                    "type": "captured_close",
                    "args": {"signal_id": row["id"], "exit_val": float(exit_val.value),
                             "reason": reason.value or "MANUAL_CLOSE"},
                })
                dlg.close()
                ui.notify("Close requested.", type="positive")
                status.text = "Closing…"

            with ui.row():
                ui.button("Confirm", on_click=confirm).props("color=negative")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["captured"] = bus_client.read_version("options:captured")
    seen["flags"] = bus_client.read_version("options:captured_flags")
    _populate(bus_client.read("options:captured") or {})

    @guard
    def _maybe_repaint():
        # Fetch-free: only re-read + repaint the table when its version changes
        # (the service bumps it after reload/reprice/close). Also watch the flags
        # view and notify each stop/target hit via ui.notify when it lands.
        version = bus_client.read_version("options:captured")
        if version != seen["captured"]:
            seen["captured"] = version
            _populate(bus_client.read("options:captured") or {})

        fv = bus_client.read_version("options:captured_flags")
        if fv != seen["flags"]:
            seen["flags"] = fv
            flags = (bus_client.read("options:captured_flags") or {}).get("flags") or []
            for f in flags:
                ui.notify(f"{f.get('symbol')}: {f.get('code')} — consider closing",
                          type="warning")

    ui.timer(2.0, _maybe_repaint)
