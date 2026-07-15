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
from .rescue import heat_border_class
from .theme import BADGE_ACCENT, BADGE_MUTED, BTN, BTN_3D_DANGER, BTN_PRIMARY

# rescue_state values that mark a trade at-risk (tested/critical). The manage-cycle
# rescue overlay tags the paper *account* positions view; the paper-trades ledger
# this page renders carries no rescue_state in the common case, so this highlight
# is a safe no-op unless a trade row is explicitly flagged.
_AT_RISK_STATES = ("tested", "critical")

# Paper-ledger styling (injected via ui.add_css — ui.html strips <style>):
#  • compact rows to match the Scanner table (dense + tight padding);
#  • fixed header with a scrollable body (sticky thead + bounded scroll area).
# Action buttons use the shared flat Deep Slate tokens: BTN (secondary Reload/
# Close), BTN_PRIMARY (Analyze), BTN_3D_DANGER (ghost-danger Delete).
PAPER_CSS = """
.paper-table td, .paper-table th { padding: 2px 6px; font-size: 13px; }
.paper-table .q-table__middle { max-height: 62vh; }
.paper-table thead tr th {
  position: sticky; top: 0; z-index: 2; background: #141a30;
}
"""


def rescue_highlight(state, heat):
    """Left-border Tailwind classes for an at-risk row, or '' (no tint) otherwise.

    Defensive: a missing/None ``state`` yields no highlight, so normal rows look
    unchanged. The class set comes from the shared ``heat_border_class`` (rescue.py)."""
    return heat_border_class(heat) if state in _AT_RISK_STATES else ""


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


def paper_columns():
    # trade_id is kept on each row (row_key + internal lookups) but is NOT a
    # visible column — it's an internal id, not trader-facing.
    spec = [
        ("symbol", "Symbol"), ("strategy", "Strat"),
        ("strikes", "Strikes"), ("expiration", "Exp"), ("quantity", "Qty"),
        ("entry_credit_total", "Credit"), ("max_loss_total", "Risk"),
        ("pnl", "P&L"), ("status", "Status"), ("entry_time", "Entry"),
    ]
    cols = [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]
    cols.append({"name": "actions", "label": "", "field": "actions", "align": "center"})
    return cols


# P&L cell colors (green profit / red loss / grey flat-or-unknown).
PNL_GREEN, PNL_RED, PNL_NEUTRAL, PNL_AMBER = "#66bb6a", "#ef5350", "#bdbdbd", "#ffa726"

# Verdict action → chip color for the Analyze popup.
_VERDICT_COLORS = {"TAKE PROFIT": PNL_GREEN, "HOLD": PNL_AMBER,
                   "CLOSE": PNL_RED, "EXPIRED": PNL_NEUTRAL}


def verdict_color(action):
    """Chip color for an Analyze verdict action (green take-profit / amber hold /
    red close / grey otherwise)."""
    return _VERDICT_COLORS.get((action or "").strip().upper(), PNL_NEUTRAL)


def verdict_class(action):
    """Tailwind ``bg-[<hex>]`` chip class for an Analyze verdict (mirrors
    ``verdict_color``)."""
    return f"bg-[{verdict_color(action)}]"


def analyze_popup_rows(res):
    """[(label, text, color), ...] metric rows for the Analyze popup, built from the
    enriched ``paper_analyze`` result's ``metrics`` block. Skips absent metrics so
    the popup only shows what's actually available."""
    m = (res or {}).get("metrics") or {}
    rows = []
    pnl = m.get("unrealized_pnl")
    if isinstance(pnl, (int, float)):
        rows.append(("Unrealized P&L", f"{pnl:+,.2f}", pnl_color(pnl)))
    pct = m.get("unrealized_pnl_pct")
    if isinstance(pct, (int, float)):
        rows.append(("% of max profit", f"{pct:+.1f}%", PNL_NEUTRAL))
    und = m.get("underlying_now")
    if isinstance(und, (int, float)):
        rows.append(("Current price", f"{und:,.2f}", PNL_NEUTRAL))
    dte = m.get("dte_remaining")
    if dte is not None:
        rows.append(("DTE remaining", str(dte), PNL_NEUTRAL))
    tgt = m.get("target_pct")
    if isinstance(tgt, (int, float)):
        rows.append(("Profit target", f"{tgt:.0f}%", PNL_NEUTRAL))
    be = m.get("breakeven")
    if isinstance(be, (int, float)):
        rows.append(("Breakeven", f"{be:,.2f}", PNL_NEUTRAL))
    return rows


def pnl_color(v):
    """Hex color for a P&L value: green > 0, red < 0, grey for 0 / None."""
    if not isinstance(v, (int, float)) or v == 0:
        return PNL_NEUTRAL
    return PNL_GREEN if v > 0 else PNL_RED


def pnl_class(v):
    """Tailwind ``text-[<hex>]`` class for a P&L value (green / red / grey-neutral).

    Note paper's neutral is grey ``#bdbdbd`` (NOT '' like captured) — so 0 / None
    always carries a class. Mirrors ``pnl_color``."""
    return f"text-[{pnl_color(v)}]"


def status_badge_class(status):
    """Deep Slate status-badge token: OPEN → blue-accent pill, everything else
    (EXPIRED / CLOSED) → muted grey pill."""
    return BADGE_ACCENT if (status or "").upper() == "OPEN" else BADGE_MUTED


def trade_pnl(t):
    """Display P&L for a ledger trade: realized when closed, live unrealized when
    OPEN (attached by the service's reprice). None when unavailable (e.g. an open
    trade not yet repriced / off-hours with no live chain)."""
    t = t or {}
    v = t.get("unrealized_pnl") if (t.get("status") or "").upper() == "OPEN" \
        else t.get("realized_pnl")
    return v if isinstance(v, (int, float)) else None


def _legs_text(legs):
    """Compact leg string for a DEBIT/legs trade, e.g. ``L 450C`` or ``L 100C / S 105C``
    (mirrors the swing table's leg format)."""
    parts = []
    for leg in legs or []:
        side = "L" if leg.get("side") == "long" else "S"
        kind = "C" if leg.get("kind") == "call" else "P"
        parts.append(f"{side} {leg.get('strike', '?')}{kind}")
    return " / ".join(parts) if parts else "—"


def _strikes(t):
    if t.get("strategy") == "IC":
        return f"P {t.get('short_strike','?')}/{t.get('long_strike','?')} " \
               f"C {t.get('call_short','?')}/{t.get('call_long','?')}"
    if t.get("direction") == "DEBIT" and t.get("legs"):
        return _legs_text(t["legs"])
    sk, lk = t.get("short_strike"), t.get("long_strike")
    return f"{sk}/{lk}" if sk is not None else "—"


def paper_rows(trades):
    rows = []
    for t in trades or []:
        pnl = trade_pnl(t)
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
            "pnl": _round(pnl),
            "_pnl_class": pnl_class(pnl),
            "status": t.get("status", ""),
            "_status_class": status_badge_class(t.get("status")),
            # Trim to seconds and show "YYYY-MM-DD HH:MM:SS" (drop the ISO 'T').
            "entry_time": (t.get("entry_time") or "")[:19].replace("T", " "),
            # At-risk rescue tint (left border on the symbol cell). Safe no-op
            # ('') when the trade carries no rescue_state (the usual case).
            "_rescue_class": rescue_highlight(t.get("rescue_state"), t.get("heat")),
        })
    # Newest trades on top by default (entry_time is a sortable ISO string; rows
    # with no time sort last). The columns stay click-sortable from here.
    rows.sort(key=lambda r: r.get("entry_time") or "", reverse=True)
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
    ui.add_css(PAPER_CSS)
    # No page title — the tab strip names the page (2026-07-11 dead-space cleanup).

    raw_by_id: dict = {}
    # sel_id: selected trade (set by row click — no checkbox); live: {trade_id:
    # live-analyze detail} overlay cache. analyze_popup_for: trade_id awaiting the
    # Analyze-button popup (row-click analyses update the panel silently).
    state = {"sel_id": None, "live": {}, "analyze_popup_for": None}

    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("flex-grow min-w-0"):
            # No selection checkbox — clicking a row selects it (drives the detail
            # panel + the action buttons below). dense + .paper-table = Scanner-like
            # compact rows with a fixed header over a scrolling body. The row-count
            # status renders BELOW the table, bottom-right, small (see after table).
            table = ui.table(columns=paper_columns(), rows=[], row_key="id") \
                .classes("w-full paper-table").props("dense")
            status = ui.label("").classes("opacity-60 text-xs self-end")
            # Symbol cell gets a colored left-border + faint tint when the row is
            # at-risk (rescue_state tested/critical). Plain cell otherwise.
            table.add_slot('body-cell-symbol', r'''
              <q-td :props="props">
                <span v-if="props.row._rescue_class" :class="props.row._rescue_class + ' pl-1.5'">
                  {{ props.value }}
                </span>
                <span v-else>{{ props.value }}</span>
              </q-td>
            ''')
            # Credit / Risk show 2 decimals (numeric value kept for sorting).
            for _f in ("entry_credit_total", "max_loss_total"):
                table.add_slot(f'body-cell-{_f}', r'''
                  <q-td :props="props" class="text-right">
                    {{ props.value == null ? '—' : Number(props.value).toFixed(2) }}
                  </q-td>
                ''')
            # P&L: 2 decimals, signed, green/red/grey (class from _pnl_class).
            table.add_slot('body-cell-pnl', r'''
              <q-td :props="props" class="text-right">
                <span v-if="props.value == null">—</span>
                <span v-else :class="props.row._pnl_class + ' font-semibold'">
                  {{ (props.value >= 0 ? '+' : '') + Number(props.value).toFixed(2) }}
                </span>
              </q-td>
            ''')
            # Status as a Deep Slate pill (OPEN blue-accent / closed grey).
            table.add_slot('body-cell-status', r'''
              <q-td :props="props">
                <q-badge :class="props.row._status_class" :label="props.value"/>
              </q-td>
            ''')
            # Action buttons live BELOW the table (solid 3D). color=None drops
            # Quasar's bg-primary so the .pt-btn gradient (blue) / .pt-danger (red)
            # actually paint — WITHOUT it, bg-primary wins and every button reads
            # solid blue (which is why Delete didn't look red).
            with ui.row().classes("items-center gap-3 flex-wrap q-mt-md"):
                ui.button("Reload", icon="refresh", color=None,
                          on_click=lambda: _reload()).props("no-caps").classes(BTN)
                ui.button("Close", icon="check_circle", color=None,
                          on_click=lambda: _close()).props("no-caps").classes(BTN)
                ui.button("Analyze", icon="biotech", color=None,
                          on_click=lambda: _analyze()).props("no-caps").classes(BTN_PRIMARY)
                ui.button("Delete", icon="delete", color=None,
                          on_click=lambda: _delete()).props("no-caps").classes(BTN_3D_DANGER)
                ui.button("Delete all closed", icon="delete_sweep", color=None,
                          on_click=lambda: _delete_closed()).props("no-caps").classes(BTN_3D_DANGER)
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
        # Selection is driven by row click (no checkbox) → state["sel_id"].
        sid = state.get("sel_id")
        if not sid or sid not in raw_by_id:
            ui.notify("Click a trade row first.", type="warning")
            return None
        return raw_by_id.get(sid)

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
                ui.button("Confirm", color=None, on_click=confirm).props("no-caps").classes(BTN_PRIMARY)
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

    def _show_analyze_popup(res):
        """Descriptive Analyze dialog (verdict + rationale + metrics + close X) —
        replaces the old one-word toast."""
        res = res or {}
        action = res.get("action", "—")
        with ui.dialog() as dlg, ui.card().classes("min-w-[360px] max-w-[460px] gap-2"):
            with ui.row().classes("items-center justify-between w-full no-wrap"):
                ui.label(f"{res.get('symbol', '')} · Trade Analysis") \
                    .classes("text-subtitle1 font-bold")
                ui.button(icon="close", on_click=dlg.close).props("flat round dense")
            ui.label(action).classes(
                f"text-weight-bold q-px-sm q-py-xs rounded-borders "
                f"{verdict_class(action)} text-[#111] w-fit")
            if res.get("rationale"):
                ui.label(res["rationale"]).classes("text-sm")
            if res.get("note"):
                ui.label(res["note"]).classes("text-sm opacity-70")
            rows = analyze_popup_rows(res)
            if rows:
                with ui.column().classes("w-full gap-1 q-mt-sm"):
                    for label, text, color in rows:
                        with ui.row().classes("justify-between w-full no-wrap"):
                            ui.label(label).classes("opacity-70 text-sm")
                            ui.label(text).classes(
                                f"text-sm text-weight-medium text-[{color}]")
            ui.button("Close", on_click=dlg.close).props("flat").classes("self-end")
        dlg.open()

    @guard
    def _analyze():
        t = _selected_trade()
        if not t:
            return
        # Mark this trade so its analyze RESULT pops the descriptive dialog (row
        # clicks analyze too, but only update the panel — no popup).
        state["analyze_popup_for"] = t.get("trade_id")
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
            # If this result was triggered by the Analyze BUTTON, pop the
            # descriptive dialog (verdict + rationale + metrics). Row-click
            # analyses update the detail panel silently (no popup).
            if tid and tid == state.get("analyze_popup_for"):
                state["analyze_popup_for"] = None
                _show_analyze_popup(res)

    ui.timer(2.0, _maybe_repaint)
