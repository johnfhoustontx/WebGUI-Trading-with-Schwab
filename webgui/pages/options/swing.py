"""Swing Scanner page (Tier-3 reader).

A dedicated user-parameterized swing credit-spread scan with custom DTE / delta /
credit gates. This page holds **no engine call**: the scan pipeline
(``screen_spreads`` + ``build_iron_condors`` + ``score_all_signals``) lives in
``services/options_svc/compute.swing_scan``, and the cross-app ``scoring``
collision guard is gone (the service process loads no sentiment code). The Scan
button enqueues a ``swing_scan`` command (with the user's inputs as args) onto
the Redis bus; the service runs it and writes the result under
``cache:options:swing``; this page only **reads** that payload and formats it.

Cache view read: ``options:swing`` → ``{signals:[...], symbol, params}``. Results
reuse the scanner's table columns/rows + the shared Trade detail panel + handoff
row actions. A fetch-free version-poll ``ui.timer`` repaints when the bus cache
version changes (graceful-empty when the service is cold).
"""
import bus_client
from nicegui import ui

from pages.ui_guard import guard

from . import detail, handoff, scanner


def pct_to_fraction(value):
    """Convert a percent UI value to a fraction (screen_spreads wants 0.10, not 10)."""
    return float(value) / 100.0


def render():
    """Swing Scanner page: inputs + Scan button + results table (left) + detail panel."""
    ui.label("Swing Scanner").classes("text-h5")

    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("flex-grow min-w-0"):
            with ui.row().classes("items-end gap-2 flex-wrap"):
                symbol_in = ui.input("Symbol", value="SPY").classes("w-28")
                dte_min = ui.number("DTE min", value=5, min=0).classes("w-24")
                dte_max = ui.number("DTE max", value=30, min=1).classes("w-24")
                put_dmin = ui.number("Put Δ min", value=-0.20, format="%.2f").classes("w-24")
                put_dmax = ui.number("Put Δ max", value=-0.10, format="%.2f").classes("w-24")
                call_dmin = ui.number("Call Δ min", value=0.10, format="%.2f").classes("w-24")
                call_dmax = ui.number("Call Δ max", value=0.20, format="%.2f").classes("w-24")
                mincr = ui.number("Min credit %", value=10.0, format="%.1f").classes("w-28")
            with ui.row().classes("items-center gap-3"):
                scan_btn = ui.button("Scan", icon="search")
                status = ui.label("").classes("opacity-70")
            table = ui.table(columns=scanner.signal_columns(), rows=[],
                             row_key="id").classes("w-full")
        detail_panel = detail.render()

    by_id: dict = {}
    # Last-seen bus cache version for the fetch-free repaint timer.
    seen = {"version": None}

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sig = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if sig:
            detail_panel.update(sig)

    table.on("rowClick", _select)
    # per-row buttons: Send to Calculator / Send to Paper trade
    handoff.add_row_actions(table, lambda row: by_id.get(row.get("id")))

    def _populate(payload):
        """Paint the table + detail map from a swing-result dict."""
        payload = payload or {}
        signals = payload.get("signals") or []
        by_id.clear()
        for s in signals:
            if s.get("id"):
                by_id[s["id"]] = s
        table.rows = scanner.signal_rows(signals)
        table.update()
        if not payload:
            status.text = ""
        else:
            status.text = f"{len(table.rows)} swing signals."

    @guard
    def _request_scan():
        params = {
            "symbol": symbol_in.value.strip().upper(),
            "dte_min": int(dte_min.value),
            "dte_max": int(dte_max.value),
            "put_d_min": float(put_dmin.value),
            "put_d_max": float(put_dmax.value),
            "call_d_min": float(call_dmin.value),
            "call_d_max": float(call_dmax.value),
            "min_cr_fraction": pct_to_fraction(mincr.value),
        }
        bus_client.request("options", {"type": "swing_scan", "args": params})
        ui.notify("Swing scan requested")
        status.text = "Scanning…"

    scan_btn.on_click(_request_scan)

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    seen["version"] = bus_client.read_version("options:swing")
    _populate(bus_client.read("options:swing") or {})

    @guard
    def _maybe_repaint():
        # Fetch-free: compare the bus cache version to the last-painted one and
        # only re-read + repaint on change. The service bumps it when a requested
        # swing scan finishes.
        version = bus_client.read_version("options:swing")
        if version == seen["version"]:
            return
        seen["version"] = version
        _populate(bus_client.read("options:swing") or {})

    ui.timer(2.0, _maybe_repaint)
