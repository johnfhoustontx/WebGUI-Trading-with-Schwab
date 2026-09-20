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
version changes; a second watch on ``options:paper_analyze`` fills the Analyze
dialog when its result lands. Graceful-empty when the service is cold.

Built on the page kit (``pages/ui_kit.py``, the 2026-09-19 consistency
standard): the header line carries the Updated stamp and the page actions, the
selected trade's buttons live in the detail panel's footer, and every delete
asks first. Dialogs stay client-side (input collection only).
"""
import bus_client
from pages.fmt import round_or_none as _round  # the ONE copy (pages/fmt.py)
from pages import ui_kit as kit
from nicegui import ui

from pages.ui_guard import guard

from . import detail, handoff
from .rescue import AT_RISK_STATES as _AT_RISK_STATES
from .rescue import heat_border_class, rescue_highlight, rescue_highlight
from .theme import BADGE_ACCENT, BADGE_MUTED, MUTED

# rescue_state values that mark a trade at-risk (tested/critical). The manage-cycle
# rescue overlay tags the paper *account* positions view; the paper-trades ledger
# this page renders carries no rescue_state in the common case, so this highlight
# is a safe no-op unless a trade row is explicitly flagged.
#
# The page's own ``PAPER_CSS`` went with the 2026-09-19 page-kit migration: the
# shell's app-wide ``TABLE_CSS`` already gives every table a sticky header over a
# bounded scrolling body, and the kit's table carries the dense/flat props.

# Right-aligned columns (the kit aligns numbers right, words left).
_NUMERIC = ("quantity", "entry_credit_total", "max_loss_total", "pnl")


def paper_columns():
    # trade_id is kept on each row (row_key + internal lookups) but is NOT a
    # visible column — it's an internal id, not trader-facing.
    # Each label names the READING.
    #
    # WARNING: "Entry", not "Credit". ``entry_credit_total`` stores a DEBIT
    # trade's debit as a NEGATIVE credit, so "Credit" contradicted the sign in
    # the cell for half the book. Under "Entry" the sign carries credit-vs-debit,
    # which is what it was already doing - and it is /desk's word for this same
    # quantity.
    #
    # That name was taken by ``entry_time``, which is now "Opened" - the better
    # word for a timestamp regardless. The rename is therefore a SWAP: a reader
    # who knows the old layout sees "Entry" move from a time to a price.
    #
    # WARNING: "P&L" is NOT renamed to /desk's "OPEN P&L". ``trade_pnl`` returns
    # REALIZED for a closed trade, so half these rows are not open at all.
    #
    # "Strategy" and "Contracts" are spelled out where /desk keeps STRAT and QTY:
    # that grid's floors are label-bound and the words would clip it at the
    # 1920px it is read at. A width concession there, not the app's word for the
    # concept - see test_the_two_labels_the_desk_cannot_fit_are_spelled_out_here.
    spec = [
        ("symbol", "Symbol"), ("strategy", "Strategy"),
        ("strikes", "Strikes"), ("expiration", "Expiry"),
        ("quantity", "Contracts"), ("entry_credit_total", "Entry"),
        ("max_loss_total", "Max loss"), ("pnl", "P&L"),
        ("status", "Status"), ("entry_time", "Opened"),
    ]
    # No ``actions`` column: the selected trade's buttons live in the detail
    # panel's footer (the 2026-09-19 standard), so nothing acts on an unselected
    # row and the icon column's width goes back to the data.
    return [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]


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
    """Compact leg string for a DEBIT/legs trade, e.g. ``L 450C`` or ``L 100C / S 105C``.

    IS the swing table's ``strategy_table.legs_summary`` rather than a mirror of
    it: the ledger stores legs in the same ``{kind, side, strike, qty}`` shape.
    The mirror this replaced ignored ``qty``, so a butterfly - the first ledger
    debit with a two-lot body - read ``L 95.0C / S 100.0C / L 105.0C``, a call
    spread with a stray short call, where the Finder shows ``S 2×100C``."""
    from .strategy_table import legs_summary
    return legs_summary(legs)


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


def ledger_status(trades):
    """The status line: how many trades, how many open. PURE. Blank for no
    trades - the empty table says that."""
    trades = trades or []
    if not trades:
        return ""
    n = len(trades)
    open_n = sum(1 for t in trades if str(t.get("status") or "").upper() == "OPEN")
    return f"{n} trade{'s' if n != 1 else ''} · {open_n} open"


def _num(v):
    """Coerce to float, or None — handles values stored as strings (e.g. breakeven)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def dte_from_expiration(exp):
    """Days from today to the expiration ISO date; None if unparseable.

    Public: the Desk and the Symbol Dossier read their DTE through it, so one
    calendar answers for every page that prints a position's countdown."""
    try:
        import datetime as dt
        return (dt.date.fromisoformat(str(exp)[:10]) - dt.date.today()).days
    except (TypeError, ValueError):
        return None


# The name this module's own callers (and test_desk) have always used.
_dte_from_expiration = dte_from_expiration


def _max_loss_per_share(t):
    """The trade's max loss in PER-SHARE dollars, matching ``entry_credit``.

    The ledger writer (``options-scanner``'s ``create_paper_trade``) persists BOTH
    ``max_loss_per`` (per share) and ``max_loss_total`` (``max_loss * quantity *
    100``, the whole position). Reading the total while ``entry_credit`` is per
    share displayed a per-share credit beside a whole-position max loss -- a
    mismatch that scales with quantity.

    ``max_loss_per`` is a real persisted column and is the EXACT value, so it is
    the primary source; ``max_loss`` is honoured next for callers that synthesize
    a trade-shaped dict by hand instead of loading a ledger row. Dividing the
    total back down is only a fallback for a row carrying neither, and it needs
    ``quantity`` -- absent that, return None rather than a figure wrong by a
    factor of the position size.
    """
    for key in ("max_loss_per", "max_loss"):
        direct = _num(t.get(key))
        if direct is not None:
            return direct
    total = _num(t.get("max_loss_total"))
    qty = _num(t.get("quantity"))
    if total is None or not qty:
        return None
    return round(total / (qty * 100.0), 4)


def synth_from_trade(trade):
    """Detail-panel signal dict from a paper-trade dict.

    Maps the calculated fields the trade ALREADY stores (breakeven [stored as a
    string, passed through verbatim], the entry greeks, underlying, width) — which the detail panel reads
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
        "max_loss": _max_loss_per_share(t),
        "expiration": t.get("expiration", ""),
        "short_strike": t.get("short_strike"),
        "long_strike": t.get("long_strike"),
        "call_short": t.get("call_short"),
        "call_long": t.get("call_long"),
        # A DEBIT trade (LONG_CALL/LONG_PUT/BULL_CALL/BEAR_PUT) stores
        # short_strike/long_strike/width as None and its real strikes ONLY here
        # (the ledger's debit-trade writer in ``options-scanner``), in the
        # canonical {kind, side, strike, qty} shape that detail.contract_lines
        # prefers. Dropping it left contract_lines nothing
        # to fall back to, so the contract card vanished entirely for exactly the
        # trades whose strikes live nowhere else. None for a credit spread, which
        # keeps taking the strike-key path.
        "legs": t.get("legs"),
        "width": _num(t.get("width")),
        # NOT coerced. An iron condor stores BOTH breakevens as the string
        # "put_be/call_be" (scanner_engine.py:1069), and ``_num`` is a bare
        # float() -- which raises on the slash and returns None, destroying the
        # value before ``detail.breakeven_text`` (which parses exactly that
        # shape) could see it. Passed through as stored so the slash-aware
        # formatter decides; it handles a plain float, a numeric string and an
        # unparseable one (-> em-dash) alike, so nothing downstream needs a float.
        "breakeven": t.get("breakeven"),
        "dte": _dte_from_expiration(t.get("expiration")),
        "short_delta": delta,
        "net_theta": _num(t.get("net_theta")),
        "net_vega": (-entry_vega) if entry_vega is not None else None,
        "underlying_price": _num(t.get("underlying_at_entry")),
        "pop_pct": pop,
        "id": t.get("trade_id"),
    }


def close_prompt_label(trade) -> str:
    """The Close dialog's input label for one ledger row.

    ⚠ **The two directions close in opposite directions.** A credit spread pays a
    DEBIT to close; a long option or debit vertical RECEIVES a credit — so asking
    a long call for its "Exit debit" named the wrong side of the trade, on top of
    an engine that then booked the P&L with the credit formula.

    Both spell out "per spread", because the ledger stores per-SHARE prices: a
    200 typed where 2.00 belongs books a 100x result that nothing downstream
    flags. An absent ``direction`` reads as a credit spread, which is what every
    pre-D3 row is.
    """
    row = trade if isinstance(trade, dict) else {}
    if str(row.get("direction") or "").upper() == "DEBIT":
        return "Exit credit received (per spread)"
    return "Exit debit paid (per spread)"


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
    """Paper Ledger: the header line, the ledger and the shared detail panel,
    whose footer carries the selected trade's actions (bus-fed)."""
    raw_by_id: dict = {}
    # sel_id: the clicked trade; live: {trade_id: live-analyze detail};
    # analyze_popup_for: the trade whose Analyze BUTTON result pops the dialog;
    # close_field: the exit-price field of the open Close dialog.
    state = {"sel_id": None, "live": {}, "analyze_popup_for": None,
             "close_field": None}

    with kit.page():
        head = kit.header("Paper Ledger", view="options:paper_trades")
        with head.actions:
            kit.button("Delete all closed", kind="danger", icon="delete_sweep",
                       on_click=lambda: delete_closed_dlg.open())
            refresh_btn = kit.button("Refresh", kind="secondary", icon="refresh",
                                     on_click=lambda: _reload())
        status = kit.status_line()
        with ui.row().classes("w-full no-wrap gap-4 items-start"):
            ledger = kit.region("Refreshing the ledger…", classes="flex-grow min-w-0")
            with ledger.content:
                # No selection checkbox — clicking a row selects it, which drives
                # the detail panel and the action footer inside it.
                table = kit.table(paper_columns(), numeric=_NUMERIC)
            detail_panel = detail.render()

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

    # The selected trade's actions: danger leftmost, primary rightmost. Built
    # ONCE - the footer shows only while a row is shown, so nothing here can be
    # pressed with no selection and no button prints "click a row first".
    with detail_panel.actions:
        kit.button("Delete", kind="danger", icon="delete",
                   on_click=lambda: _delete()).classes("mr-auto")
        kit.button("Expected Move", kind="secondary", icon="show_chart",
                   on_click=lambda: _send_em())
        analyze_btn = kit.button("Analyze", kind="secondary", icon="biotech",
                                 on_click=lambda: _analyze())
        # "Close trade", not "Close": a dialog's own Close dismisses the dialog.
        kit.button("Close trade", kind="primary", icon="check_circle",
                   on_click=lambda: _close())

    # Built once at the page's own level and retitled per use: a dialog built
    # inside a repainted container dies with its slot.
    close_dlg = kit.confirm("Close trade", confirm_text="Close trade",
                            on_confirm=lambda: _confirm_close())
    delete_dlg = kit.confirm("Delete this trade?", "It leaves the ledger for good.",
                             confirm_text="Delete", danger=True,
                             on_confirm=lambda: _confirm_delete())
    delete_closed_dlg = kit.confirm(
        "Delete every closed trade?",
        "Their realized P&L leaves the ledger, and the deployment cap's equity "
        "moves with it.",
        confirm_text="Delete all closed", danger=True,
        on_confirm=lambda: _confirm_delete_closed())
    analyze_dlg = kit.info_dialog("Trade analysis", width="min-w-[360px] max-w-[460px]")

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
        # No status line: the panel repaints when the result lands.
        bus_client.request("options",
                           {"type": "paper_analyze", "args": {"trade_id": trade_id}})

    def _populate(pt):
        """Paint the ledger table from the cached paper-trades view."""
        ledger.busy.hide()
        kit.set_busy(refresh_btn, False)
        pt = pt or {}
        trades = pt.get("trades") or []
        raw_by_id.clear()
        for t in trades:
            if t.get("trade_id"):
                raw_by_id[t["trade_id"]] = t
        table.rows = paper_rows(trades)
        kit.mark_selected(table.rows, state.get("sel_id"))
        table.update()
        # Keep the open detail panel in sync with the freshly-cached trades
        # (preserving any live-analyze overlay for the selected trade).
        sel = state.get("sel_id")
        if sel and sel in raw_by_id:
            _render_detail(raw_by_id[sel])
        elif sel:
            detail_panel.clear()  # selected trade no longer present
        status.text = ledger_status(trades)

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        t = raw_by_id.get(row.get("id")) if isinstance(row, dict) else None
        if t:
            state["sel_id"] = t.get("trade_id")
            kit.mark_selected(table.rows, state["sel_id"])
            table.update()
            _render_detail(t)                     # instant stored-data view
            _request_analyze(t.get("trade_id"), t.get("symbol", ""))  # live overlay

    table.on("rowClick", _select)

    def _selected_trade():
        """The clicked trade, or None. Silent: the action footer is only visible
        while a row is shown, so 'click a row first' is unreachable."""
        return raw_by_id.get(state.get("sel_id"))

    def _send_em():
        t = _selected_trade()
        if t:
            handoff.send_to_expected_move(
                handoff.signal_to_em_payload(synth_from_trade(t)))

    @guard
    def _reload():
        bus_client.request("options", {"type": "paper_reload"})
        ledger.busy.show("Refreshing the ledger…")
        kit.set_busy(refresh_btn)

    @guard
    def _close():
        t = _selected_trade()
        if not t:
            return
        close_dlg.title.text = f"Close {t.get('symbol', '')} {t.get('strategy', '')}"
        close_dlg.content.clear()
        with close_dlg.content:
            state["close_field"] = kit.number_field(
                close_prompt_label(t), value=0.0, min=0, format="%.2f", width="w-40")
        close_dlg.open()

    def _confirm_close():
        t, field = _selected_trade(), state["close_field"]
        if not t or field is None or not field.validate():
            return False
        bus_client.request("options", {
            "type": "paper_close",
            "args": {"trade_id": t.get("trade_id"), "debit": float(field.value)},
        })
        kit.toast("info", f"Closing {t.get('symbol', '')} — the ledger updates "
                          "when the engine confirms.")

    @guard
    def _delete():
        t = _selected_trade()
        if not t:
            return
        delete_dlg.title.text = f"Delete the {t.get('symbol', '')} {t.get('strategy', '')}?"
        delete_dlg.open()

    def _confirm_delete():
        t = _selected_trade()
        if not t:
            return
        bus_client.request("options", {"type": "paper_delete",
                                       "args": {"trade_id": t.get("trade_id")}})
        kit.toast("info", f"Deleting {t.get('symbol', '')} — the row clears when "
                          "the engine confirms.")

    def _confirm_delete_closed():
        bus_client.request("options", {"type": "paper_delete_closed"})
        kit.toast("info", "Deleting every closed trade — the ledger updates when "
                          "the engine confirms.")

    def _show_analyze_popup(res):
        """Fill the page's information dialog with the Analyze verdict (verdict +
        rationale + metrics), rather than building one dialog per result."""
        res = res or {}
        action = res.get("action", "—")
        analyze_dlg.title.text = f"{res.get('symbol', '')} · Trade analysis"
        analyze_dlg.content.clear()
        with analyze_dlg.content:
            ui.label(action).classes(
                f"text-weight-bold px-2 py-1 rounded-[6px] {verdict_class(action)} "
                "text-[#111] w-fit")
            if res.get("rationale"):
                ui.label(res["rationale"]).classes("text-sm")
            if res.get("note"):
                ui.label(res["note"]).classes(f"text-sm {MUTED}")
            rows = analyze_popup_rows(res)
            if rows:
                with ui.column().classes("w-full gap-1 pt-2"):
                    for label, text, color in rows:
                        with ui.row().classes("justify-between w-full no-wrap"):
                            ui.label(label).classes(f"text-sm {MUTED}")
                            ui.label(text).classes(
                                f"text-sm text-weight-medium text-[{color}]")
        analyze_dlg.open()

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
        kit.set_busy(analyze_btn)

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
            # If this result was triggered by the Analyze BUTTON, pop the
            # descriptive dialog (verdict + rationale + metrics). Row-click
            # analyses update the detail panel silently (no popup).
            if tid and tid == state.get("analyze_popup_for"):
                state["analyze_popup_for"] = None
                kit.set_busy(analyze_btn, False)
                _show_analyze_popup(res)

    ui.timer(2.0, _maybe_repaint)
