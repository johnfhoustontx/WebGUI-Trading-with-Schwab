"""Options scanner page.

Wraps the copied options-scanner engine (``run_full_scan``) and renders the
VIX/regime strip, the 0-DTE and swing signal tables, and a per-signal detail
dialog. All scoring/GEX/screening logic lives in the engine — this module only
marshals results into NiceGUI widgets.
"""
import sys

from nicegui import run, ui

import proxy
from repo_paths import OPTIONS_SCANNER

# options-scanner engine modules onto sys.path (folder has no package init).
if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

from scanner_engine import run_full_scan  # noqa: E402


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
    return [
        {"name": field, "label": label, "field": field, "sortable": True, "align": "left"}
        for field, label in spec
    ]


def signal_rows(signals):
    """Map engine signal dicts to display rows, sorted by score (desc).

    Robust to sparse signals — fields vary by trade type (PCS/CCS/IC).
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


def _vix_strip(container, results):
    container.clear()
    with container:
        vix = results.get("vix")
        if vix is not None:
            ui.label(f"VIX {vix:.2f}").classes("text-subtitle1 font-bold")
        regime = results.get("vix_regime") or {}
        if regime.get("label"):
            badge = ui.badge(regime["label"]).classes("text-sm")
            if regime.get("color"):
                badge.style(f"background-color: {regime['color']}")
        term = results.get("vix_term_structure") or {}
        if term.get("structure"):
            ui.label(f"Term: {term['structure']}").classes("opacity-70")
        ts = results.get("timestamp")
        if ts:
            ui.label(f"as of {ts}").classes("opacity-50 text-sm")


def _show_detail(signal):
    with ui.dialog() as dialog, ui.card().classes("min-w-[420px]"):
        ui.label(f"{signal.get('symbol','')} {signal.get('type','')} "
                 f"{signal.get('expiration','')}").classes("text-h6")
        ui.label(f"Grade {signal.get('grade','?')} · "
                 f"Score {signal.get('composite_score','?')} · "
                 f"PoP {signal.get('pop_pct','?')}%").classes("opacity-80")
        factors = signal.get("factor_scores") or {}
        if factors:
            ui.label("Factor scores").classes("text-subtitle2 mt-2")
            with ui.row().classes("gap-2 flex-wrap"):
                for name, val in factors.items():
                    ui.badge(f"{name}: {val}")
        for label, key in (("GEX walls", "gex_walls"), ("DEX walls", "dex_walls")):
            walls = signal.get(key)
            if walls:
                ui.label(f"{label}: {', '.join(str(w) for w in walls)}").classes("text-sm opacity-70")
        ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def render():
    """Build the Options page body (called inside the shell layout)."""
    ui.label("Options Scanner").classes("text-h5")

    # raw engine signals kept for the detail lookup (rows are display-only)
    by_id: dict = {}

    with ui.row().classes("items-center gap-3"):
        scan_btn = ui.button("Run scan", icon="play_arrow")
        spinner = ui.spinner(size="lg")
        spinner.visible = False
        status = ui.label("").classes("opacity-70")

    vix_strip = ui.row().classes("gap-4 items-center")

    with ui.tabs() as tabs:
        tab_0dte = ui.tab("0-DTE")
        tab_swing = ui.tab("Swing")
    with ui.tab_panels(tabs, value=tab_0dte).classes("w-full"):
        with ui.tab_panel(tab_0dte):
            table_0dte = ui.table(columns=signal_columns(), rows=[], row_key="id").classes("w-full")
        with ui.tab_panel(tab_swing):
            table_swing = ui.table(columns=signal_columns(), rows=[], row_key="id").classes("w-full")

    def _open_detail(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sig = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if sig:
            _show_detail(sig)

    table_0dte.on("rowClick", _open_detail)
    table_swing.on("rowClick", _open_detail)

    def _populate(results):
        by_id.clear()
        for s in (results.get("signals_0dte") or []) + (results.get("signals_swing") or []):
            if s.get("id"):
                by_id[s["id"]] = s
        _vix_strip(vix_strip, results)
        table_0dte.rows = signal_rows(results.get("signals_0dte"))
        table_swing.rows = signal_rows(results.get("signals_swing"))
        table_0dte.update()
        table_swing.update()
        n = len(table_0dte.rows) + len(table_swing.rows)
        errs = results.get("errors") or []
        status.text = f"{n} signals." + (f" {len(errs)} errors." if errs else "")
        for w in (results.get("warnings") or []):
            ui.notify(w, type="warning")

    async def do_scan():
        scan_btn.disable()
        spinner.visible = True
        status.text = "Scanning… (a few seconds)"
        try:
            results = await run.io_bound(run_full_scan, proxy.schwab_py_client)
        except Exception as exc:  # surface, don't crash the page
            ui.notify(f"Scan failed: {exc}", type="negative")
            status.text = "Scan failed."
            return
        finally:
            spinner.visible = False
            scan_btn.enable()
        _populate(results)

    scan_btn.on_click(do_scan)
