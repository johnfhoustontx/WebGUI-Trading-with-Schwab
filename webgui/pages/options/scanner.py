"""Options scanner page (0-4 DTE / 5-15 DTE).

Wraps the copied options-scanner engine (``run_full_scan``) and renders the
two signal tables. Row selection drives the shared Trade detail panel
(wired in the two-pane render — see Task A4). Scoring/GEX/screening logic lives
in the engine; this module only marshals results into NiceGUI widgets.
"""
import sys
from datetime import date as _date, time as _time
from zoneinfo import ZoneInfo

from nicegui import run, ui

import proxy
from repo_paths import OPTIONS_SCANNER

# options-scanner engine modules onto sys.path (folder has no package init).
if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

from scanner_engine import run_full_scan  # noqa: E402

from . import detail, handoff, header  # noqa: E402

# Last scan results, cached at module level so they persist across page
# navigation and so the server-side auto-scan can push fresh data to open pages.
# ``version`` bumps on every update so a page's refresh timer knows to repaint.
_LAST_RESULTS: dict = {"data": None, "version": 0}

# Auto-scan state (server-side scheduler). Trading window + cadence mirror the
# legacy scanner: 08:00–15:15 CT, every 15 min, trading days only. The schedule
# logic is replicated here (not imported from the legacy `scanner` CLI) to avoid
# dragging its heavy import chain — and the cross-app module-name collisions — in.
_AUTOSCAN: dict = {"started": False, "last_slot": None}

_CT = ZoneInfo("America/Chicago")
_SCAN_START = (8, 0)    # 08:00 CT
_SCAN_END = (15, 15)    # 15:15 CT
# US market holidays 2026 (keep in sync with options-scanner/scanner.py Config.HOLIDAYS)
_HOLIDAYS = {
    _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16), _date(2026, 4, 3),
    _date(2026, 5, 25), _date(2026, 7, 3), _date(2026, 9, 7),
    _date(2026, 11, 26), _date(2026, 12, 25),
}


def _is_trading_day(now):
    return now.weekday() < 5 and now.date() not in _HOLIDAYS


def _is_market_hours(now):
    t = now.time()
    return _time(*_SCAN_START) <= t <= _time(*_SCAN_END)


def _store_results(results):
    _LAST_RESULTS["data"] = results
    _LAST_RESULTS["version"] = _LAST_RESULTS.get("version", 0) + 1


def _slot_key(now):
    return (now.date().isoformat(), now.hour, now.minute // 15)


def autoscan_due(now, last_slot):
    """(should_scan, slot_key): True at most once per 15-min slot, only on a
    trading day within the 08:00–15:15 CT window."""
    if not (_is_trading_day(now) and _is_market_hours(now)):
        return (False, last_slot)
    slot = _slot_key(now)
    return (slot != last_slot, slot)


def _market_now():
    import datetime as _dt
    return _dt.datetime.now(_CT)


def _run_scan_sync():
    from . import engines
    with engines.options_scoring():  # guard scoring name collision
        return run_full_scan(proxy.schwab_py_client)


async def _autoscan_loop():
    import asyncio
    loop = asyncio.get_event_loop()
    while True:
        try:
            now = _market_now()
            due, slot = autoscan_due(now, _AUTOSCAN["last_slot"])
            if due:
                _AUTOSCAN["last_slot"] = slot
                results = await loop.run_in_executor(None, _run_scan_sync)
                _store_results(results)
        except Exception:
            pass  # never let the scheduler die
        await asyncio.sleep(30)


def start_autoscan():
    """Start the server-side 15-min auto-scan loop (idempotent)."""
    import asyncio
    if _AUTOSCAN["started"]:
        return
    _AUTOSCAN["started"] = True
    asyncio.create_task(_autoscan_loop())


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


def signal_columns():
    """ui.table column defs for a signal table."""
    spec = [
        ("symbol", "Symbol"),
        ("type", "Type"),
        ("expiration", "Exp"),
        ("dte", "DTE"),
        ("short_strike", "Short"),
        ("long_strike", "Long"),
        ("credit", "Credit"),
        ("max_loss", "Max Loss"),
        ("rr_pct", "R/R %"),
        ("pop_pct", "PoP %"),
        ("composite_score", "Score"),
        ("grade", "Grade"),
    ]
    cols = [
        {"name": field, "label": label, "field": field, "sortable": True, "align": "left"}
        for field, label in spec
    ]
    cols.append({"name": "actions", "label": "", "field": "actions", "align": "center"})
    return cols


def signal_rows(signals):
    """Map engine signal dicts to display rows, sorted by score (desc).

    Robust to sparse signals — fields vary by trade type (PCS/CCS/IC). Each row
    keeps ``id`` so the detail panel can look up the raw engine signal.
    """
    rows = []
    for s in signals or []:
        rows.append({
            "id": s.get("id"),
            "symbol": s.get("symbol", ""),
            "type": s.get("type", ""),
            "expiration": s.get("expiration", ""),
            "dte": s.get("dte"),
            "short_strike": s.get("short_strike"),
            "long_strike": s.get("long_strike"),
            "credit": _round(s.get("credit")),
            "max_loss": _round(s.get("max_loss")),
            "rr_pct": _round(s.get("rr_pct"), 1),
            "pop_pct": _round(s.get("pop_pct"), 1),
            "composite_score": s.get("composite_score"),
            "grade": s.get("grade", ""),
        })
    rows.sort(key=lambda r: (r["composite_score"] is not None, r["composite_score"] or 0),
              reverse=True)
    return rows


def _scan_meta_strip(container, results):
    """Post-scan info NOT already shown by the header strip (term + timestamp)."""
    container.clear()
    with container:
        term = results.get("vix_term_structure") or {}
        if term.get("structure"):
            ui.label(f"Term: {term['structure']}").classes("opacity-70")
        ts = results.get("timestamp")
        if ts:
            ui.label(f"as of {ts}").classes("opacity-50 text-sm")


def render():
    """Build the Options scanner page body (two-pane: tables + detail panel)."""
    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("flex-grow min-w-0"):
            header.render()
            ui.label("Options Scanner").classes("text-h5")
            with ui.row().classes("items-center gap-3"):
                scan_btn = ui.button("Run scan", icon="play_arrow")
                spinner = ui.spinner(size="lg")
                spinner.visible = False
                status = ui.label("").classes("opacity-70")
                auto_lbl = ui.label("").classes("opacity-60 text-sm")
            meta_strip = ui.row().classes("gap-4 items-center")
            with ui.tabs() as tabs:
                tab_0dte = ui.tab("0-DTE")
                tab_swing = ui.tab("Swing")
            with ui.tab_panels(tabs, value=tab_0dte).classes("w-full"):
                with ui.tab_panel(tab_0dte):
                    table_0dte = ui.table(columns=signal_columns(), rows=[], row_key="id").classes("w-full")
                with ui.tab_panel(tab_swing):
                    table_swing = ui.table(columns=signal_columns(), rows=[], row_key="id").classes("w-full")
        detail_panel = detail.render()

    by_id: dict = {}

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sig = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if sig:
            detail_panel.update(sig)

    for _t in (table_0dte, table_swing):
        _t.on("rowClick", _select)
        # per-row buttons: Send to Calculator / Send to Paper trade
        handoff.add_row_actions(_t, lambda row: by_id.get(row.get("id")))

    def _populate(results, *, notify=True, remember=True):
        by_id.clear()
        for s in (results.get("signals_0dte") or []) + (results.get("signals_swing") or []):
            if s.get("id"):
                by_id[s["id"]] = s
        _scan_meta_strip(meta_strip, results)
        table_0dte.rows = signal_rows(results.get("signals_0dte"))
        table_swing.rows = signal_rows(results.get("signals_swing"))
        table_0dte.update()
        table_swing.update()
        n = len(table_0dte.rows) + len(table_swing.rows)
        errs = results.get("errors") or []
        status.text = f"{n} signals." + (f" {len(errs)} errors." if errs else "")
        if remember:
            _store_results(results)
        if notify:
            for w in (results.get("warnings") or []):
                ui.notify(w, type="warning")

    async def do_scan():
        scan_btn.disable()
        spinner.visible = True
        status.text = "Scanning… (a few seconds)"

        def _run():
            from . import engines
            with engines.options_scoring():  # guard scoring name collision
                return run_full_scan(proxy.schwab_py_client)

        try:
            results = await run.io_bound(_run)
        except Exception as exc:  # surface, don't crash the page
            ui.notify(f"Scan failed: {exc}", type="negative")
            status.text = f"Scan failed: {exc}"
            return
        finally:
            spinner.visible = False
            scan_btn.enable()
        _populate(results)

    scan_btn.on_click(do_scan)

    # Restore the last scan when returning to the page (no re-notify of warnings).
    seen = {"version": _LAST_RESULTS.get("version", 0)}
    if _LAST_RESULTS["data"] is not None:
        _populate(_LAST_RESULTS["data"], notify=False, remember=False)

    def _tick():
        # Repaint when the server-side auto-scan (or another tab) refreshed data.
        if _LAST_RESULTS.get("version", 0) != seen["version"] and _LAST_RESULTS["data"]:
            seen["version"] = _LAST_RESULTS["version"]
            _populate(_LAST_RESULTS["data"], notify=False, remember=False)
        now = _market_now()
        in_window = _is_trading_day(now) and _is_market_hours(now)
        auto_lbl.text = ("Auto-scan: every 15 min (market open)" if in_window
                         else "Auto-scan: idle (outside 08:00–15:15 CT)")

    _tick()
    ui.timer(20.0, _tick)
