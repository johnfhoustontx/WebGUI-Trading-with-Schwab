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
hits as a toast when they land. The close dialog stays client-side (input
collection only). Graceful-empty when the service is cold.

Built on the page kit (``pages/ui_kit.py``, the 2026-09-19 consistency
standard): the header line carries the Updated stamp, Refresh and Reprice now;
the selected signal's buttons live in the detail panel's footer.
"""
from datetime import datetime

import bus_client
from pages.fmt import round_or_none as _round  # the ONE copy (pages/fmt.py)
from pages import ui_kit as kit
from nicegui import ui

from pages.ui_guard import guard

from . import detail, handoff
from .rescue import AT_RISK_STATES as _AT_RISK_STATES
from .rescue import heat_border_class, rescue_highlight, rescue_highlight
from .theme import (BADGE_MUTED, BADGE_NEG, BADGE_POS, BADGE_WARN, EYEBROW,
                    LABEL, THEME)

# rescue_state values that mark a signal at-risk (tested/critical). Captured
# signals are advisory-only and the manage-cycle rescue overlay only tags paper
# *account* positions, so captured rows usually carry NO rescue_state — this
# highlight is therefore a safe no-op here unless a signal is explicitly flagged.

# Profit/loss cell colors (green in profit, red in loss).
PNL_GREEN, PNL_RED = "#66bb6a", "#ef5350"


def rec_class(rec):
    """Recommendation -> Deep Slate badge token (tinted bg + colored fg).

    TAKE_PROFIT green / CUT red / HOLD amber, from the shared semantic palette."""
    return {"TAKE_PROFIT": BADGE_POS, "CUT": BADGE_NEG,
            "HOLD": BADGE_WARN}.get(rec, BADGE_MUTED)


# The page's own ``CAPTURED_CSS`` went with the 2026-09-19 page-kit migration:
# the shell's app-wide ``TABLE_CSS`` already gives every table a sticky header
# over a bounded scrolling body, and the kit's table carries the dense props.

# Right-aligned columns (the kit aligns numbers right, words left).
_NUMERIC = ("credit", "current_value", "max_loss", "unrealized_pnl")


def pnl_color(value):
    """P&L -> text color: green when in profit (>0), red when in loss (<0).

    Returns '' for zero / missing / non-numeric so those render uncolored."""
    if isinstance(value, (int, float)):
        if value > 0:
            return PNL_GREEN
        if value < 0:
            return PNL_RED
    return ""


def pnl_class(value):
    """P&L -> Tailwind ``text-[<hex>]`` class (green profit / red loss), or '' for
    zero / missing / non-numeric (uncolored). Mirrors ``pnl_color``."""
    color = pnl_color(value)
    return f"text-[{color}]" if color else ""


def exit_value_default(sig):
    """Pre-fill for the close dialog's Exit-value input: the signal's current price
    (the live spread mark, ``current_value``) rounded to 2dp, or 0.0 when it isn't
    known yet (no mark — e.g. a freshly captured signal not yet repriced)."""
    cur = (sig or {}).get("current_value")
    return round(float(cur), 2) if isinstance(cur, (int, float)) else 0.0


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
    # Each label names the READING. Three of these were wrong rather than terse.
    #
    # WARNING: "DTE at entry" and "Entry grade" hold ``dte_at_entry`` and
    # ``entry_grade``. Both are captured once and NEVER MOVE. On a page whose
    # whole purpose is tracking a signal over time, a bare "DTE" is read as days
    # left - and drifts further from the truth every session that passes.
    #
    # WARNING: "Entry", not "Credit". ``mode`` is the PREMIUM-vs-DIRECTIONAL tag
    # and a directional signal is a DEBIT, so this book is not all credits. Same
    # defect the Paper Ledger carried in its same-named column, fixed the same
    # way: the sign carries credit-vs-debit.
    #
    # "Open P&L" is right HERE and wrong on the Paper Ledger, which is not a
    # drift. A closed signal LEAVES this table (the reason the Status column was
    # dropped above), so every visible row is open. That ledger keeps closed rows
    # and its ``trade_pnl`` returns REALIZED for them.
    #
    # "Style" for ``mode``, deliberately NOT "Trade type": this app already uses
    # ``trade_type`` for 0-DTE / Swing / Directional, and reusing the phrase
    # would put one name on two different splits.
    spec = [
        ("recommendation", "Action"),
        ("symbol", "Symbol"), ("strategy", "Strategy"), ("mode", "Style"),
        ("opened", "Opened"), ("expiration", "Expiry"),
        ("dte", "DTE at entry"), ("credit", "Entry"),
        ("current_value", "Mark"), ("max_loss", "Max loss"),
        ("unrealized_pnl", "Open P&L"), ("grade", "Entry grade"),
    ]
    # No ``actions`` column: the selected signal's buttons live in the detail
    # panel's footer (the 2026-09-19 standard), so nothing acts on an unselected
    # row and the icon column's width goes back to the data.
    return [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]


def _captured_at(sig):
    """The capture instant as a POSIX timestamp, or None when it can't be read.

    Parsed rather than string-compared: ``first_seen_ts`` carries a UTC offset
    that shifts with DST (-05:00 in summer, -06:00 in winter), so two identical
    wall-clock strings are an hour apart as instants. ``.timestamp()`` resolves
    an aware value against its own offset and a naive one against local time,
    which is the right reading for both.
    """
    ts = (sig or {}).get("first_seen_ts")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def sort_newest_first(signals):
    """PURE: open signals ordered newest capture first (the table's default).

    Signals with no readable timestamp trail the dated ones in the order the
    service gave them, tie-broken on the incoming index, so neither an undated
    pair nor two captures at the same instant jitter between the 2 s repaints.
    """
    dated, undated = [], []
    for i, sig in enumerate(signals or []):
        ts = _captured_at(sig)
        (undated if ts is None else dated).append((ts, i, sig))
    dated.sort(key=lambda x: (-x[0], x[1]))
    return [sig for _, _, sig in dated + undated]


def captured_rows(signals):
    """Display rows from the open-signals view (cache:options:captured), newest
    capture first (see ``sort_newest_first``)."""
    rows = []
    for s in sort_newest_first(signals):
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
            "_rec_class": rec_class(s.get("recommendation") or "HOLD"),
            "_pnl_class": pnl_class(s.get("unrealized_pnl")),
            # At-risk rescue tint (left border on the symbol cell). Safe no-op
            # ('') when the signal carries no rescue_state (the usual case).
            "_rescue_class": rescue_highlight(s.get("rescue_state"), s.get("heat")),
        })
    return rows


# ── Day footer (the four figures under the table) ───────────────────────────
def open_pnl(signals):
    """Total unrealized P&L across the OPEN signals — the footer's open figure.

    Summed page-side off the very list the table renders, rather than published
    as its own number, so the footer can never disagree with the P&L column
    above it. A signal with no mark yet carries ``unrealized_pnl: None`` and
    contributes nothing.
    """
    total = 0.0
    for s in signals or []:
        v = _float(s.get("unrealized_pnl"))
        if v is not None:
            total += v
    return round(total, 2)


def _money(v):
    """'+$61.50' / '-$120.00' / '$0.00' — signed except at exactly flat."""
    amount = _float(v) or 0.0
    sign = "+" if amount > 0 else ("-" if amount < 0 else "")
    return f"{sign}${abs(amount):,.2f}"


def _count(v):
    n = _float(v)
    return str(int(n)) if n is not None else "0"


def priced_count(signals):
    """How many open signals carry a live mark (a numeric ``unrealized_pnl``)."""
    return sum(1 for s in signals or [] if _float(s.get("unrealized_pnl")) is not None)


NOT_PRICED = "—"   # em dash — "no reading", as distinct from a reading of zero


def footer_cells(day, signals):
    """The four footer figures as ``[{label, value, cls, note}]`` (PURE).

    ``day`` is the service's summary block (opened/closed/booked_pnl for today,
    CT); the open figure is derived here from ``signals``. A missing or
    part-cold ``day`` degrades to zeros — a payload published before this
    shipped has no block at all, and the page must still build.

    Only the two P&L figures are coloured: a count has no direction to signal.

    **An unpriced book reads as an em dash, not as $0.00.** The persisted view
    carries no marks until a reprice runs (``signal_marks`` is only written by
    the auto-manage cycle), so every ``unrealized_pnl`` can be None — and
    summing that to a confident zero would report a flat book where the truth
    is "not priced yet", the same failure mode as a missing indicator clamping
    to a confident extreme. An empty book is still a real $0.00. When only some
    signals are priced the sum is real but partial, so the cell carries a
    ``note`` naming the coverage.
    """
    d = day or {}
    booked = _float(d.get("booked_pnl")) or 0.0
    n_open = len(signals or [])
    n_priced = priced_count(signals)
    live = open_pnl(signals)
    if n_open and not n_priced:
        open_cell = {"value": NOT_PRICED, "cls": "",
                     "note": f"none of {n_open} open signals priced yet"}
    else:
        open_cell = {
            "value": _money(live), "cls": pnl_class(live),
            "note": (f"{n_priced} of {n_open} open signals priced"
                     if n_priced < n_open else "")}
    return [
        {"label": "Opened today", "value": _count(d.get("opened")), "cls": "", "note": ""},
        {"label": "Closed today", "value": _count(d.get("closed")), "cls": "", "note": ""},
        {"label": "P&L today (booked)", "value": _money(booked),
         "cls": pnl_class(booked), "note": ""},
        {"label": "P&L today (open)", **open_cell},
    ]


def _float(v):
    """Coerce to float, or None — stored values arrive as strings often enough."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _breakeven_from(r):
    """Breakeven(s) derived from the stored strikes and per-share entry credit.

    The signals table has NO breakeven column, so the detail panel rendered an
    em-dash for every captured signal. This is exact arithmetic rather than an
    estimate: a put credit spread breaks even at ``short_strike - credit`` and a
    call credit spread ABOVE its short at ``short_strike + credit``. An iron
    condor has TWO, returned as the ``"put/call"`` string the panel's
    ``breakevens()`` already parses — the same shape the scanner engine emits.
    """
    credit = _float(r.get("entry_credit"))
    short = _float(r.get("short_strike"))
    if credit is None or short is None:
        return None
    strategy = str(r.get("strategy") or "").upper()
    if strategy == "IC":
        call_short = _float(r.get("call_short"))
        if call_short is None:
            return None
        return f"{round(short - credit, 2)}/{round(call_short + credit, 2)}"
    if strategy == "CCS":
        return round(short + credit, 2)
    return round(short - credit, 2)


def _pop_from(r):
    """Probability of profit approximated as ``1 - |short delta|``.

    The signals table stores no PoP. This is the same standard approximation the
    paper adapter uses, so the two panels agree. ``signal_recorder`` writes
    ``short_delta`` with a DEFAULT OF 0, so a falsy delta is treated as absent —
    otherwise a signal that never carried a delta would report a false 100%.
    """
    delta = _float(r.get("entry_short_delta"))
    return round((1.0 - abs(delta)) * 100, 1) if delta else None


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
        # Neither is a stored column — both are derived. See the helpers above.
        "breakeven": _breakeven_from(r),
        "pop_pct": _pop_from(r),
        "dte": r.get("dte_at_entry"),
        "dte_is_entry": r.get("dte_at_entry") is not None,
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
    """Captured Signals: header line, the signals table with its day footer,
    and the shared detail panel whose footer closes the selected signal."""
    raw_by_id: dict = {}
    # sel_id: the signal the user clicked. There is no selection checkbox, so the
    # clicked row IS the selection - it drives the detail panel and the actions
    # in its footer. close_fields: the open Close dialog's inputs.
    state = {"sel_id": None, "close_fields": None}

    with kit.page():
        head = kit.header("Captured Signals", view="options:captured")
        with head.actions:
            refresh_btn = kit.button("Refresh", kind="secondary", icon="refresh",
                                     on_click=lambda: _reload())
            reprice_btn = kit.button("Reprice now", kind="primary",
                                     icon="published_with_changes",
                                     on_click=lambda: _reprice())
        status = kit.status_line()
        with ui.row().classes("w-full no-wrap gap-4 items-start"):
            box = kit.region("Refreshing the signals…", classes="flex-grow min-w-0")
            with box.content:
                table = kit.table(captured_columns(), numeric=_NUMERIC)
                # The day footer sits UNDER the table but inside the region, so
                # a reprice's spinner covers it too - its figures are as stale
                # as the marks above them while a reprice runs.
                foot = ui.row().classes(
                    "w-full flex-wrap items-start gap-x-10 gap-y-2 px-2 pt-2 mt-1 "
                    f"border-t border-[{THEME['palette']['card_border']}]")
            detail_panel = detail.render()

    # No selection checkbox: clicking a row selects it (detail panel + its action
    # footer) and the kit paints the row accent. The symbol cell keeps the
    # at-risk rescue tint (tested/critical); plain otherwise.
    table.add_slot('body-cell-symbol', r'''
      <q-td :props="props">
        <span v-if="props.row._rescue_class" :class="props.row._rescue_class + ' pl-1.5'">
          {{ props.value }}
        </span>
        <span v-else>{{ props.value }}</span>
      </q-td>
    ''')
    # Rec badge (first column). The selected-row accent is the kit's, on the ROW.
    table.add_slot('body-cell-recommendation', r'''
      <q-td :props="props">
        <q-badge :class="props.row._rec_class" :label="props.value"/>
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
        <span :class="props.row._pnl_class + ' font-semibold'">
          {{ props.value == null ? '' : props.value }}
        </span>
      </q-td>
    ''')

    # The selected signal's actions, built ONCE: the footer shows only while a
    # signal is shown, so nothing here can be pressed without one.
    with detail_panel.actions:
        kit.button("Expected Move", kind="secondary", icon="show_chart",
                   on_click=lambda: _send_em())
        kit.button("Close signal", kind="primary", icon="check_circle",
                   on_click=lambda: _close())

    # Built once at the page's own level and retitled per use: a dialog built
    # inside a repainted container dies with its slot.
    close_dlg = kit.confirm("Close signal", confirm_text="Close signal",
                            on_confirm=lambda: _confirm_close())

    # Last-seen bus cache versions for the fetch-free repaint/notify timers.
    seen = {"captured": None, "flags": None}

    # One handle per footer figure; the labels come from ``footer_cells`` itself
    # so the pure builder stays the single source of the footer's wording.
    foot_refs = []
    with foot:
        for cell in footer_cells(None, []):
            with ui.column().classes("gap-0.5"):
                ui.label(cell["label"]).classes(EYEBROW)
                lbl = ui.label(cell["value"]).classes(
                    f"{LABEL} text-sm font-semibold tabular-nums")
                with lbl:
                    # Mark coverage lives on hover, not in the number: a partial
                    # sum is still a real number, but the reader deserves to know
                    # it doesn't cover the whole book.
                    tip = ui.tooltip("")
                foot_refs.append({"lbl": lbl, "tip": tip, "cls": ""})

    def _paint_footer(day, sigs):
        for ref, cell in zip(foot_refs, footer_cells(day, sigs)):
            ref["lbl"].text = cell["value"]
            ref["tip"].text = cell["note"]
            ref["tip"].set_visibility(bool(cell["note"]))
            if cell["cls"] != ref["cls"]:
                # swap only the tracked previous colour, so repeated repaints
                # never stack two conflicting text-[...] classes
                ref["lbl"].classes(remove=ref["cls"], add=cell["cls"])
                ref["cls"] = cell["cls"]

    def _populate(cap):
        """Paint the signals table + day footer from the cached captured view."""
        box.busy.hide()
        kit.set_busy(refresh_btn, False)
        kit.set_busy(reprice_btn, False)
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
        kit.mark_selected(table.rows, state.get("sel_id"))
        table.update()
        _paint_footer(cap.get("day"), sigs)
        n = len(table.rows)
        status.text = f"{n} open signal{'s' if n != 1 else ''}" if cap else ""

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sig = raw_by_id.get(row.get("id")) if isinstance(row, dict) else None
        if sig:
            state["sel_id"] = sig.get("signal_id")
            kit.mark_selected(table.rows, state["sel_id"])
            table.update()
            detail_panel.update(synth_from_captured(sig))

    table.on("rowClick", _select)

    def _selected_signal():
        """The raw signal dict the user is acting on (the clicked/highlighted row),
        or None when nothing is selected."""
        sid = state.get("sel_id")
        return raw_by_id[sid] if sid and sid in raw_by_id else None

    def _send_em():
        # ``synth_from_captured`` maps the raw captured signal to a signal-shaped
        # dict (``type``/``expiration``/``*_strike``) that ``signal_to_em_payload``
        # understands.
        sig = _selected_signal()
        if sig:
            handoff.send_to_expected_move(
                handoff.signal_to_em_payload(synth_from_captured(sig)))

    @guard
    def _reload():
        bus_client.request("options", {"type": "captured_reload"})
        box.busy.show("Refreshing the signals…")
        kit.set_busy(refresh_btn)

    @guard
    def _reprice():
        bus_client.request("options", {"type": "captured_reprice"})
        box.busy.show("Repricing…")
        kit.set_busy(reprice_btn)

    @guard
    def _close():
        sig = _selected_signal()
        if not sig:
            return
        close_dlg.title.text = f"Close {sig.get('symbol', '')} {sig.get('strategy', '')}"
        close_dlg.content.clear()
        with close_dlg.content:
            # The current mark, when repriced; the reader can still override it.
            exit_val = kit.number_field("Exit value (spread debit)",
                                        value=exit_value_default(sig), min=0,
                                        format="%.2f", width="w-40")
            reason = kit.text_field("Reason", value="MANUAL_CLOSE", width="w-40")
        # The symbol travels with the fields rather than being looked up again on
        # confirm: the 2 s repaint can drop the row from ``raw_by_id`` while the
        # dialog is open, and the toast must name the signal the dialog was
        # opened for.
        state["close_fields"] = (sig.get("signal_id"), sig.get("symbol", ""),
                                 exit_val, reason)
        close_dlg.open()

    def _confirm_close():
        signal_id, symbol, exit_val, reason = \
            state["close_fields"] or (None, "", None, None)
        if not signal_id or not exit_val.validate():
            return False
        bus_client.request("options", {
            "type": "captured_close",
            "args": {"signal_id": signal_id, "exit_val": float(exit_val.value),
                     "reason": reason.value or "MANUAL_CLOSE"},
        })
        kit.toast("info", f"Closing {symbol} — the list updates when the engine "
                          "confirms.")

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["captured"] = bus_client.read_version("options:captured")
    seen["flags"] = bus_client.read_version("options:captured_flags")
    _populate(bus_client.read("options:captured") or {})

    @guard
    def _maybe_repaint():
        # Fetch-free: only re-read + repaint the table when its version changes
        # (the service bumps it after reload/reprice/close). Also watch the flags
        # view and raise a toast for each stop/target hit when it lands.
        version = bus_client.read_version("options:captured")
        if version != seen["captured"]:
            seen["captured"] = version
            _populate(bus_client.read("options:captured") or {})

        fv = bus_client.read_version("options:captured_flags")
        if fv != seen["flags"]:
            seen["flags"] = fv
            flags = (bus_client.read("options:captured_flags") or {}).get("flags") or []
            for f in flags:
                kit.toast("warn",
                          f"{f.get('symbol')}: {f.get('code')} — consider closing")

    ui.timer(2.0, _maybe_repaint)
