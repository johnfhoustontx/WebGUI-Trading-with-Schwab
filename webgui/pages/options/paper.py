"""Paper Trades page.

Lists the paper-trade ledger (``paper_trader.get_all_trades``) with the shared
Trade detail panel. Actions: close (debit dialog), delete, delete-all-closed,
and analyze (live Greeks via ``trade_analyzer.analyze_trade``). Lifecycle logic
lives in the engines; this module marshals + wires.
"""
import sys

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


def paper_columns():
    spec = [
        ("trade_id", "ID"), ("symbol", "Symbol"), ("strategy", "Strat"),
        ("strikes", "Strikes"), ("expiration", "Exp"), ("quantity", "Qty"),
        ("entry_credit_total", "Credit$"), ("max_loss_total", "Risk$"),
        ("realized_pnl", "P&L$"), ("status", "Status"), ("entry_time", "Entry"),
    ]
    return [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]


def _strikes(t):
    if t.get("strategy") == "IC":
        return f"P {t.get('short_strike','?')}/{t.get('long_strike','?')} " \
               f"C {t.get('call_short','?')}/{t.get('call_long','?')}"
    sk, lk = t.get("short_strike"), t.get("long_strike")
    return f"{sk}/{lk}" if sk is not None else "—"


def paper_rows(trades):
    rows = []
    for t in trades or []:
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
            "realized_pnl": _round(t.get("realized_pnl")),
            "status": t.get("status", ""),
            "entry_time": (t.get("entry_time") or "")[:19],
        })
    return rows


def synth_from_trade(trade):
    """Detail-panel signal dict from a paper-trade dict."""
    t = trade or {}
    return {
        "symbol": t.get("symbol", ""),
        "type": t.get("strategy", ""),
        "trade_type": t.get("trade_type", ""),
        "credit": t.get("entry_credit"),
        "max_loss": t.get("max_loss_total"),
        "expiration": t.get("expiration", ""),
        "short_strike": t.get("short_strike"),
        "long_strike": t.get("long_strike"),
        "call_short": t.get("call_short"),
        "call_long": t.get("call_long"),
        "id": t.get("trade_id"),
    }


def render():
    """Paper Trades page: ledger table (left) + shared detail panel (right)."""
    from nicegui import run, ui

    import proxy
    import paper_trader
    import trade_analyzer

    from . import detail

    ui.label("Paper Trades").classes("text-h5")

    raw_by_id: dict = {}

    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("flex-grow min-w-0"):
            with ui.row().classes("items-center gap-2 flex-wrap"):
                ui.button("Reload", icon="refresh", on_click=lambda: _load())
                ui.button("Close selected", icon="check_circle",
                          on_click=lambda: _close()).props("outline")
                ui.button("Analyze selected", icon="biotech",
                          on_click=lambda: _analyze()).props("outline")
                ui.button("Delete selected", icon="delete",
                          on_click=lambda: _delete()).props("outline color=negative")
                ui.button("Delete all closed", icon="delete_sweep",
                          on_click=lambda: _delete_closed()).props("flat color=negative")
                spinner = ui.spinner(size="lg")
                spinner.visible = False
            status = ui.label("").classes("opacity-70")
            table = ui.table(columns=paper_columns(), rows=[], row_key="id",
                             selection="single").classes("w-full")
        detail_panel = detail.render()

    def _load():
        try:
            trades = paper_trader.get_all_trades()
        except Exception as exc:
            ui.notify(f"DB read failed: {exc}", type="negative")
            trades = []
        raw_by_id.clear()
        for t in trades:
            if t.get("trade_id"):
                raw_by_id[t["trade_id"]] = t
        table.rows = paper_rows(trades)
        table.update()
        status.text = f"{len(table.rows)} trades."

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        t = raw_by_id.get(row.get("id")) if isinstance(row, dict) else None
        if t:
            detail_panel.update(synth_from_trade(t))

    table.on("rowClick", _select)

    def _selected_trade():
        if not table.selected:
            ui.notify("Select a trade first.", type="warning")
            return None
        return raw_by_id.get(table.selected[0].get("id"))

    def _close():
        t = _selected_trade()
        if not t:
            return
        with ui.dialog() as dlg, ui.card():
            ui.label(f"Close {t.get('symbol')} {t.get('strategy')}").classes("text-subtitle1")
            debit = ui.number("Exit debit (per spread)", value=0.0, format="%.2f")

            def confirm():
                try:
                    closed = paper_trader.close_paper_trade(t, float(debit.value), "MANUAL_CLOSE")
                    paper_trader.update_trade(t.get("trade_id"), closed)
                except Exception as exc:
                    ui.notify(f"Close failed: {exc}", type="negative")
                    return
                dlg.close()
                ui.notify("Trade closed.", type="positive")
                _load()

            with ui.row():
                ui.button("Confirm", on_click=confirm).props("color=negative")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    def _delete():
        t = _selected_trade()
        if not t:
            return
        try:
            paper_trader.delete_trade(t.get("trade_id"))
        except Exception as exc:
            ui.notify(f"Delete failed: {exc}", type="negative")
            return
        ui.notify("Trade deleted.", type="positive")
        _load()

    def _delete_closed():
        try:
            paper_trader.delete_closed_trades()
        except Exception as exc:
            ui.notify(f"Delete failed: {exc}", type="negative")
            return
        ui.notify("Closed trades deleted.", type="positive")
        _load()

    async def _analyze():
        t = _selected_trade()
        if not t:
            return
        spinner.visible = True
        try:
            result = await run.io_bound(trade_analyzer.analyze_trade, proxy.schwab_py_client, t, None)
        except Exception as exc:
            ui.notify(f"Analyze failed: {exc}", type="negative")
            return
        finally:
            spinner.visible = False
        verdict = (result or {}).get("verdict", {}) if isinstance(result, dict) else {}
        action = verdict.get("action", "—")
        ui.notify(f"{t.get('symbol')}: {action}", type="info")

    _load()
