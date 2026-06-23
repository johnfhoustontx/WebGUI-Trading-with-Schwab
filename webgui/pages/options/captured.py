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

from . import detail, handoff
from .rescue import heat_color

# rescue_state values that mark a signal at-risk (tested/critical). Captured
# signals are advisory-only and the manage-cycle rescue overlay only tags paper
# *account* positions, so captured rows usually carry NO rescue_state — this
# highlight is therefore a safe no-op here unless a signal is explicitly flagged.
_AT_RISK_STATES = ("tested", "critical")


def rescue_highlight(state, heat):
    """Left-border color for an at-risk row, or '' (no tint) otherwise.

    Defensive: a missing/None ``state`` (the common case for captured signals)
    yields no highlight, so this never changes the look of normal rows."""
    return heat_color(heat) if state in _AT_RISK_STATES else ""


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


REC_RED, REC_AMBER, REC_GREEN = "#ef5350", "#ffa726", "#66bb6a"
# Profit/loss cell colors (green in profit, red in loss).
PNL_GREEN, PNL_RED = "#66bb6a", "#ef5350"


def rec_color(rec):
    """Recommendation -> badge color (green take-profit / red cut / amber hold)."""
    return {"TAKE_PROFIT": REC_GREEN, "CUT": REC_RED, "HOLD": REC_AMBER}.get(rec, "#666666")


# Scoped to .captured-table so it never leaks into the rest of the app. Sticky
# header (visible while the body scrolls), a bounded body height so the horizontal
# scrollbar sits at the bottom of the table viewport — reachable without scrolling
# past 100+ rows — and tight cell padding to compress the inter-column space.
# #1d1d1d matches the app's dark theme (same as the calculator sticky header).
CAPTURED_CSS = '''
.captured-table .q-table__middle { max-height: 70vh; }
.captured-table thead tr th {
  position: sticky; top: 0; z-index: 2;
  background-color: #1d1d1d;
}
.captured-table td, .captured-table th { padding: 4px 8px; }
'''


def pnl_color(value):
    """P&L -> text color: green when in profit (>0), red when in loss (<0).

    Returns '' for zero / missing / non-numeric so those render uncolored."""
    if isinstance(value, (int, float)):
        if value > 0:
            return PNL_GREEN
        if value < 0:
            return PNL_RED
    return ""


def fmt_opened(ts):
    """Format a captured signal's ``first_seen_ts`` as ``'YYYY-MM-DD HH:MM'``.

    ``first_seen_ts`` is an ISO timestamp written at capture (e.g.
    ``'2026-06-17T13:49:49.898534-05:00'``). We show the local date + HH:MM the
    signal was opened. Returns '' when absent/unparseable."""
    if not ts:
        return ""
    s = str(ts)
    if "T" in s and len(s) >= 16:
        return f"{s[:10]} {s[11:16]}"
    return s[:16]


def captured_columns():
    # Rec leads (left of Symbol). The Entry/Current/Drift score columns and the
    # redundant Status column are dropped — a closed signal leaves the table, so
    # every visible row is OPEN. "Cur Price" is the live spread mark (current
    # option price), shown next to the entry Credit for an at-a-glance comparison.
    spec = [
        ("recommendation", "Rec"),
        ("symbol", "Symbol"), ("strategy", "Strat"), ("mode", "Mode"),
        ("opened", "Opened"), ("expiration", "Exp"), ("dte", "DTE"),
        ("credit", "Credit"), ("current_value", "Cur Price"),
        ("max_loss", "Risk"), ("unrealized_pnl", "P&L"), ("grade", "Grade"),
    ]
    cols = [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]
    cols.append({"name": "actions", "label": "", "field": "actions", "align": "center"})
    return cols


def captured_rows(signals):
    """Display rows from the open-signals view (cache:options:captured)."""
    rows = []
    for s in signals or []:
        rows.append({
            "id": s.get("signal_id"),
            "recommendation": s.get("recommendation") or "HOLD",
            "symbol": s.get("symbol", ""),
            "strategy": s.get("strategy", ""),
            "mode": s.get("mode", ""),
            "opened": fmt_opened(s.get("first_seen_ts")),
            "expiration": s.get("expiration", ""),
            "dte": s.get("dte_at_entry"),
            "credit": _round(s.get("entry_credit")),
            # Current option price = the live spread mark (what it'd cost to close).
            "current_value": _round(s.get("current_value")),
            "max_loss": _round(s.get("entry_max_loss")),
            "unrealized_pnl": _round(s.get("unrealized_pnl")),
            "grade": s.get("entry_grade", ""),
            "_rec_color": rec_color(s.get("recommendation") or "HOLD"),
            "_pnl_color": pnl_color(s.get("unrealized_pnl")),
            # At-risk rescue tint (left border on the symbol cell). Safe no-op
            # when the signal carries no rescue_state (the usual case).
            "_rescue_color": rescue_highlight(s.get("rescue_state"), s.get("heat")),
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
    ui.add_css(CAPTURED_CSS)  # sticky header + bounded height + compact columns

    raw_by_id: dict = {}
    # sel_id: the signal the user clicked. There is no selection checkbox, so the
    # clicked row IS the selection — it drives the detail panel, the Rec-cell
    # highlight, and the "Close selected" action.
    state = {"sel_id": None}

    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("flex-grow min-w-0"):
            with ui.row().classes("items-center gap-3"):
                ui.button("Reload", icon="refresh", on_click=lambda: _reload())
                ui.button("Refresh marks (live)", icon="published_with_changes",
                          on_click=lambda: _reprice())
                ui.button("Close selected", icon="check_circle",
                          on_click=lambda: _close()).props("outline")
                status = ui.label("").classes("opacity-70")
            table = ui.table(columns=captured_columns(), rows=[],
                             row_key="id").classes("w-full captured-table").props("dense")
            # No selection checkbox: clicking a row selects it (detail panel +
            # Close-selected) and the Rec cell shows a blue left-accent. The symbol
            # cell keeps the at-risk rescue tint (tested/critical); plain otherwise.
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
            # Rec badge (first column) — a blue left-accent marks the selected row.
            table.add_slot('body-cell-recommendation', r'''
              <q-td :props="props"
                    :style="props.row._selected
                            ? 'border-left:4px solid #42a5f5; background:#42a5f522' : ''">
                <q-badge :style="`background:${props.row._rec_color};color:#111`" :label="props.value"/>
              </q-td>
            ''')
            # Current price (live spread mark) shown to 2dp; numeric so it sorts.
            table.add_slot('body-cell-current_value', r'''
              <q-td :props="props">
                {{ props.value == null ? '' : Number(props.value).toFixed(2) }}
              </q-td>
            ''')
            # P&L colored green in profit / red in loss (value stays numeric to sort).
            table.add_slot('body-cell-unrealized_pnl', r'''
              <q-td :props="props">
                <span :style="`color:${props.row._pnl_color};font-weight:600`">
                  {{ props.value == null ? '' : props.value }}
                </span>
              </q-td>
            ''')
        detail_panel = detail.render()

    # Last-seen bus cache versions for the fetch-free repaint/notify timers.
    seen = {"captured": None, "flags": None}

    def _apply_selection():
        """Stamp ``_selected`` on each row so the Rec cell highlights the row the
        user clicked — the one ``Close selected`` will act on."""
        sel = state.get("sel_id")
        for row in table.rows:
            row["_selected"] = (row.get("id") == sel)

    def _populate(cap):
        """Paint the signals table from the cached captured view."""
        cap = cap or {}
        sigs = cap.get("signals") or []
        raw_by_id.clear()
        for s in sigs:
            if s.get("signal_id"):
                raw_by_id[s["signal_id"]] = s
        table.rows = captured_rows(sigs)
        # Drop a stale selection (e.g. the signal we just closed is gone).
        if state.get("sel_id") not in raw_by_id:
            state["sel_id"] = None
        _apply_selection()
        table.update()
        status.text = f"{len(table.rows)} open signals." if cap else ""

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sig = raw_by_id.get(row.get("id")) if isinstance(row, dict) else None
        if sig:
            state["sel_id"] = sig.get("signal_id")
            _apply_selection()      # highlight the clicked row (Rec-cell accent)
            table.update()
            detail_panel.update(synth_from_captured(sig))

    table.on("rowClick", _select)

    # Per-row Expected Move button only (Calculator / Paper actions don't belong
    # on the captured-signals table). ``synth_from_captured`` maps the raw captured
    # signal to a signal-shaped dict (``type``/``expiration``/``*_strike``) that
    # ``signal_to_em_payload`` understands.
    handoff.add_expected_move_action(
        table, lambda row: synth_from_captured(raw_by_id.get(row.get("id"))))

    def _selected_signal():
        """The raw signal dict the user is acting on (the clicked/highlighted row),
        or None when nothing is selected."""
        sid = state.get("sel_id")
        return raw_by_id[sid] if sid and sid in raw_by_id else None

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
        sig = _selected_signal()
        if not sig:
            ui.notify("Select a signal first.", type="warning")
            return
        signal_id = sig.get("signal_id")
        with ui.dialog() as dlg, ui.card():
            ui.label(f"Close {sig.get('symbol')} {sig.get('strategy')}").classes("text-subtitle1")
            exit_val = ui.number("Exit value (spread debit)", value=0.0, format="%.2f")
            reason = ui.input("Reason", value="MANUAL_CLOSE")

            def confirm():
                bus_client.request("options", {
                    "type": "captured_close",
                    "args": {"signal_id": signal_id, "exit_val": float(exit_val.value),
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
