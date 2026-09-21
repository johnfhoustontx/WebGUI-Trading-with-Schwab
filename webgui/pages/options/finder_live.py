"""The PUBLIC Strategy Finder (``live.neuralstrike.co/finder``).

A visitor types a symbol and presses Scan. The page asks for one scan through
``bus_client.request_public_scan`` - the one write the public origin may make -
and reads the answer from the per-symbol public keys. Reached through
``swing.render(public=True)``, which hands off here before the private page
builds anything. Roadmap: docs/plans/2026-09-21-public-strategy-finder-roadmap.md.

What the visitor sees is drawn by the private page's own builders - the summary
facts, chips, top-pick card body and ranked-list rows from ``finder_view`` and
``swing`` - so a result reads the same on both origins. What they do NOT get is
every owner control: no filters (the scan is pinned in
``config/finder_public.toml``), no Paper, no Calculator, no Expected Move, no
Trade detail panel, no Checks column (its Paper book line reads the owner's
ledger). A test pins each absence.

Three rules from the Phase 1 review, and where each lives:

* **One visitor cannot fill the queue.** ``_request`` checks the per-visitor
  limit (``visitor_limit``) before it sends anything. Addresses stay in memory.
* **The status view is not the page's to show.** It maps every symbol anyone
  searched. The page reads its own symbol's entry (``request_state``) and the
  budget, and never iterates the map.
* **No repaint per stranger's request.** The status view changes whenever
  anyone asks; the page polls it only while its own request is waiting, and
  repaints the results only when its own answer lands.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

import bus_client
import visitor_limit
from nicegui import context, run, ui
from shared import public_scan as ps
from shared.symbols import clean_symbol

from pages import ui_kit as kit
from pages.ui_guard import guard, guard_async

from . import finder_view as fv
from . import swing
from .theme import BADGE_ACCENT, BADGE_MUTED, CARD, LABEL, MUTED

log = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")
DEFAULT_SYMBOL = "SPY"
EMPTY_PROMPT = "Type a symbol and press Scan to rank the strategies for it."
POLL_SEC = 1.0
# How long the page waits for its answer: the longest a request may sit in the
# queue, plus a slow scan. The service answers every request it reads, so this
# only fires when the service is down.
_SCAN_ALLOWANCE_SEC = 90
NO_ANSWER = ("No answer yet. The scanner may be busy or offline; please try "
             "again in a few minutes.")
REQUEST_FAILED = "The scan could not be requested. Please try again later."
_PILL = "px-2 py-0.5 text-xs"

# One limiter for the whole process: every visitor's page shares it.
LIMITER = visitor_limit.Limiter(ps.scans_per_hour)


def limit_text() -> str:
    n = ps.scans_per_hour()
    return (f"You have reached the limit of {n} scans an hour. "
            "Please try again later.")


# ── pure: what the page says ─────────────────────────────────────────────────

def _pct(fraction) -> str:
    return f"{round(fraction * 100):g}%"


def intro_text(pin, window) -> str:
    """The fixed filters, in words, above the symbol box."""
    lo = round(min(abs(pin["put_d_max"]), abs(pin["call_d_min"])) * 100)
    hi = round(max(abs(pin["put_d_min"]), abs(pin["call_d_max"])) * 100)
    return (f"Every scan uses the same filters: expirations up to "
            f"{pin['dte_max']} days out, short legs between {lo} and {hi} delta, "
            f"and a credit of at least {_pct(pin['min_cr_fraction'])} of a "
            f"spread's width. Scans run {window['start']}–{window['end']} CT on "
            f"trading days.")


def budget_text(status):
    left = (status or {}).get("scans_left")
    if not isinstance(left, int) or isinstance(left, bool):
        return None
    return f"{left} public scan{'' if left == 1 else 's'} left today"


def _when(iso):
    try:
        when = dt.datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def scanned_at_text(payload):
    when = _when((payload or {}).get("scanned_at"))
    return None if when is None else f"Scanned {when.astimezone(CT):%H:%M} CT"


def answer_line(outcome, status) -> str:
    """The line under the symbol box once the request is answered. When is in
    the summary strip ("Scanned 10:42 CT"), beside the result it dates."""
    parts = [ps.OUTCOME_TEXT.get(outcome, ps.OUTCOME_TEXT["error"])]
    budget = budget_text(status)
    if budget:
        parts.append(budget.capitalize() + ".")
    return " ".join(parts)


def request_state(status, symbol, since):
    """``(state, outcome)`` for THIS visitor's request of ``symbol`` sent at
    ``since`` (an aware datetime).

    ``done`` once the symbol's own entry records an outcome at or after the
    request; ``scanning`` while the service is working on this symbol;
    ``queued`` otherwise. Reads ``status["last"][symbol]`` and ``busy`` only -
    never another symbol's entry."""
    st = status if isinstance(status, dict) else {}
    last = st.get("last") if isinstance(st.get("last"), dict) else {}
    rec = last.get(symbol) if isinstance(last.get(symbol), dict) else {}
    at = _when(rec.get("at"))
    if at is not None and at >= since - dt.timedelta(seconds=2):
        return "done", rec.get("outcome")
    busy = st.get("busy") if isinstance(st.get("busy"), dict) else {}
    if busy.get("symbol") == symbol:
        return "scanning", None
    return "queued", None


def waiting_text(symbol, state, seconds) -> str:
    head = (f"Scanning {symbol}…" if state == "scanning"
            else f"Waiting to scan {symbol}…")
    return f"{head} {int(seconds)} s"


def public_columns():
    """The private list's columns, minus the owner's Checks and actions."""
    return [c for c in fv.finder_columns() if c["name"] not in ("checks", "actions")]


# ── the page ─────────────────────────────────────────────────────────────────

def _visitor():
    try:
        return visitor_limit.client_key(context.client.request)
    except Exception:  # noqa: BLE001 - no request is one bucket, not a crash
        return "unknown"


def render():
    ui.add_css(swing.FINDER_CSS)
    visitor = _visitor()
    pin = ps.scan_pin()
    state = {"symbol": None, "payload": None, "active": None, "since": None,
             "t0": None, "rows": [], "row_signal": {}, "memo": {}}

    with kit.page():
        kit.header("Strategy Finder")
        ui.label(intro_text(pin, _window())).classes(f"text-sm {MUTED}")
        with kit.control_bar():
            symbol_in = kit.symbol_field(value=DEFAULT_SYMBOL, tab=False,
                                         on_load=lambda: _request())
            scan_btn = kit.button("Scan", kind="primary", icon="search",
                                  on_click=lambda: _request())
        status_line = kit.status_line()
        summary_box = ui.row().classes(f"{CARD} w-full items-center gap-3 flex-wrap")
        summary_box.set_visibility(False)
        chips_row = ui.row().classes("w-full items-center gap-2 flex-wrap")
        picks_grid = ui.element("div").classes(
            "grid w-full gap-3 grid-cols-1 sm:grid-cols-2 xl:grid-cols-4")
        empty_line = kit.empty(EMPTY_PROMPT)
        table = kit.table(public_columns(), rows=[], row_key="id",
                          rows_per_page=swing.PAGE_SIZE, rows_number=0,
                          classes="finder-table w-full")
        table._props["rows-per-page-options"] = [swing.PAGE_SIZE]
        table.set_visibility(False)

    for name, slot in (("strategy", swing._STRATEGY_SLOT),
                       ("composite_score", swing._SCORE_SLOT),
                       ("expiry", swing._EXPIRY_SLOT),
                       ("max_profit", swing._MAX_PROFIT_SLOT),
                       ("max_loss", swing._MAX_LOSS_SLOT),
                       ("pop", swing._POP_SLOT),
                       ("grade", swing._GRADE_SLOT)):
        table.add_slot(f"body-cell-{name}", slot)

    # ---------------------------------------------------------------- painting

    def _show_page(request):
        page, pagination = swing.page_of(state["rows"], table.columns, request)
        table.rows = swing.with_shapes(page, lambda r: state["row_signal"].get(id(r)))
        table.pagination = pagination
        table.update()

    @guard
    def _on_page_request(event):
        args = event.args if isinstance(event.args, dict) else {}
        _show_page(args.get("pagination"))

    table.on("request", _on_page_request, ["pagination"])

    def _paint_summary(payload):
        facts = fv.summary_facts(payload)
        summary_box.clear()
        summary_box.set_visibility(facts is not None)
        if facts is None:
            return
        with summary_box:
            ui.label(facts["symbol"]).classes(f"text-h6 font-bold {LABEL}")
            ui.label(facts["price"]).classes(f"text-subtitle1 {MUTED}")
            for pill in facts["pills"]:
                ui.label(pill).classes(f"{BADGE_MUTED} {_PILL}")
            if facts["vol_rank"]:
                ui.label(facts["vol_rank"]).classes(f"{BADGE_ACCENT} {_PILL}")
            stamp = scanned_at_text(payload)
            counts = facts["counts"] + (f" · {stamp}" if stamp else "")
            ui.label(counts).classes(f"text-sm {MUTED} ml-auto")

    @guard
    def _on_chip(code):
        state["active"] = fv.toggle_chip(state["active"], code)
        _paint_results()

    def _paint_results():
        signals = [s for s in ((state["payload"] or {}).get("signals") or []) if s]
        visible = fv.filter_groups(signals, state["active"])
        chips_row.clear()
        counts = fv.chip_counts(signals)
        if counts:
            with chips_row:
                swing.strategy_chip(f"All {len(signals)}",
                                    active=fv.chip_is_active(state["active"], fv.ALL_CHIP),
                                    on_click=lambda: _on_chip(fv.ALL_CHIP))
                for code, label, n in counts:
                    swing.strategy_chip(f"{label} {n}",
                                        active=fv.chip_is_active(state["active"], code),
                                        on_click=lambda c=code: _on_chip(c))
        picks_grid.clear()
        with picks_grid:
            for sig in fv.top_picks(visible):
                with ui.column().classes(f"{CARD} w-full gap-2"):
                    swing.pick_card_body(swing.card_view(sig))
        rows, sigs = swing.list_rows(visible)
        state["rows"] = rows
        state["row_signal"] = {id(r): s for r, s in zip(rows, sigs)}
        has = state["payload"] is not None
        table.set_visibility(has)
        empty_line.set_visibility(not has)
        label = fv.no_data_label(state["payload"])
        if label is None:
            table._props.pop("no-data-label", None)
        else:
            table._props["no-data-label"] = label
        _show_page({**(table.pagination or {}), "page": 1})

    def _paint(payload):
        payload = payload or None
        symbol = (payload or {}).get("symbol")
        signals = [s for s in ((payload or {}).get("signals") or []) if s]
        state["active"] = fv.carry_chips(state["active"], state["symbol"], symbol, signals)
        state["symbol"], state["payload"] = symbol, payload
        _paint_summary(payload)
        _paint_results()

    # ----------------------------------------------------------------- request

    @guard
    def _request():
        symbol = clean_symbol(symbol_in.value)
        if symbol is None:
            kit.symbol_error(symbol_in, ps.OUTCOME_TEXT["invalid"])
            return
        kit.symbol_error(symbol_in, None)
        if state["since"] is not None:
            return                                   # one request at a time
        if not LIMITER.allow(visitor):
            status_line.text = limit_text()
            return
        try:
            bus_client.request_public_scan(symbol)
        except Exception:  # noqa: BLE001 - worded for the visitor, logged for us
            log.warning("public scan request for %s failed", symbol, exc_info=True)
            status_line.text = REQUEST_FAILED
            return
        state.update(since=dt.datetime.now(dt.timezone.utc), t0=time.monotonic(),
                     pending=symbol, memo={})
        kit.set_busy(scan_btn, True, timeout=_wait_sec())
        status_line.text = waiting_text(symbol, "queued", 0)

    @guard_async
    async def _poll():
        symbol = state.get("pending")
        if state["since"] is None or symbol is None:
            return
        waited = time.monotonic() - state["t0"]
        status, _changed = await run.io_bound(bus_client.read_gated,
                                              ps.STATUS_VIEW, state["memo"])
        where, outcome = request_state(status, symbol, state["since"])
        if where != "done":
            if waited > _wait_sec():
                _finish()
                status_line.text = NO_ANSWER
            else:
                status_line.text = waiting_text(symbol, where, waited)
            return
        payload = await run.io_bound(bus_client.read, ps.result_view(symbol))
        _finish()
        # No result for THIS symbol clears the last one: another symbol's
        # ideas under "No listed options were found" would read as this one's.
        _paint(payload or None)
        status_line.text = answer_line(outcome, status)

    def _finish():
        state["since"] = None
        state["pending"] = None
        kit.set_busy(scan_btn, False)

    ui.timer(POLL_SEC, _poll)

    @guard_async
    async def _load_default():
        # A page load never spends a scan: it shows the default symbol's last
        # public result, if there is one, and waits for the visitor to ask.
        payload = await run.io_bound(bus_client.read, ps.result_view(DEFAULT_SYMBOL))
        if payload:
            _paint(payload)

    ui.timer(0.1, _load_default, once=True)


def _window():
    try:
        from shared import market_calendar
        start, end = market_calendar.window_bounds("finder_public")
        return {"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")}
    except Exception:  # noqa: BLE001 - the words, not the gate; the service decides
        return {"start": "08:40", "end": "15:00"}


def _wait_sec():
    return ps.limits()["max_wait_sec"] + _SCAN_ALLOWANCE_SEC
