"""Swing Scanner page.

A dedicated swing credit-spread scan with custom DTE / delta / credit gates
(``screen_spreads`` + ``build_iron_condors`` + ``score_all_signals``). Results
reuse the scanner's table columns/rows and the shared Trade detail panel.
"""
import sys

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))


def pct_to_fraction(value):
    """Convert a percent UI value to a fraction (screen_spreads wants 0.10, not 10)."""
    return float(value) / 100.0


def assign_ids(signals, symbol):
    """Ensure each signal has a unique ``id`` (for detail lookup)."""
    for i, s in enumerate(signals or []):
        if not s.get("id"):
            s["id"] = f"{symbol}_{i}_{s.get('type','')}_{s.get('short_strike','')}"
    return signals


def _swing_scan(symbol, dte_min, dte_max, put_d_min, put_d_max,
                call_d_min, call_d_max, min_cr_pct, client):
    """Run the swing scan pipeline; returns scored signals (list of dicts)."""
    import datetime as dt

    import scanner_engine as se
    import scoring
    from iv_analysis import run_iv_analysis

    import proxy

    today = dt.date.today()
    chain = se.fetch_option_chain(client, symbol, from_date=today,
                                  to_date=today + dt.timedelta(days=dte_max + 2))
    quote = proxy.schwab_client.get_quote(symbol) or {}
    spot = quote.get("last") or chain.get("underlyingPrice")
    hist = se.fetch_price_history(client, symbol)
    tech = se.calc_technicals(hist) if hist is not None else {}
    iv = run_iv_analysis(client, symbol, price=spot, hist=hist, chain=chain) or {}
    dem = ((iv.get("expected_moves") or {}).get("daily") or {}).get("move_dollars")

    spreads = se.screen_spreads(chain, symbol, dte_min, dte_max, put_d_min, put_d_max,
                                call_d_min, call_d_max, min_cr_pct, "SWING",
                                spot=spot, daily_expected_move=dem)
    signals = list(spreads) + list(se.build_iron_condors(spreads))
    scoring.score_all_signals(signals, {symbol: iv}, {symbol: tech})
    return assign_ids(signals, symbol)


def render():
    """Swing Scanner page: inputs + results table (left) + detail panel (right)."""
    from nicegui import run, ui

    import proxy

    from . import detail, scanner

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
                spinner = ui.spinner(size="lg")
                spinner.visible = False
                status = ui.label("").classes("opacity-70")
            table = ui.table(columns=scanner.signal_columns(), rows=[],
                             row_key="id").classes("w-full")
        detail_panel = detail.render()

    by_id: dict = {}

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sig = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if sig:
            detail_panel.update(sig)

    table.on("rowClick", _select)

    async def do_scan():
        scan_btn.disable()
        spinner.visible = True
        status.text = "Scanning…"
        try:
            signals = await run.io_bound(
                _swing_scan, symbol_in.value.strip().upper(),
                int(dte_min.value), int(dte_max.value),
                float(put_dmin.value), float(put_dmax.value),
                float(call_dmin.value), float(call_dmax.value),
                pct_to_fraction(mincr.value), proxy.schwab_py_client)
        except Exception as exc:
            ui.notify(f"Scan failed: {exc}", type="negative")
            status.text = "Scan failed."
            return
        finally:
            spinner.visible = False
            scan_btn.enable()
        by_id.clear()
        for s in signals:
            by_id[s["id"]] = s
        table.rows = scanner.signal_rows(signals)
        table.update()
        status.text = f"{len(table.rows)} swing signals."

    scan_btn.on_click(do_scan)
