"""Paper Portfolio page.

Paper-account snapshot cards (``paper_engine.account_snapshot``), open positions
(``paper_account_db.fetch_open_positions``), and the fills log
(``fetch_orders``). Actions: reset account, run entry/manage cycle (off-thread).
Engine owns the cycles; this module marshals + wires.
"""
import sys

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


def _money(v):
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "—"


def account_cards(snapshot):
    """[(label, value), ...] for the account summary cards."""
    s = snapshot or {}
    return [
        ("Equity", _money(s.get("equity"))),
        ("Cash", _money(s.get("cash"))),
        ("BP Reserved", _money(s.get("buying_power_reserved"))),
        ("Session P&L", _money(s.get("session_pnl"))),
        ("Total P&L", _money(s.get("realized_pnl"))),
        ("Open", str(s.get("open_count", 0))),
        ("Engine", "HALTED" if s.get("halted") else "RUNNING"),
    ]


def position_columns():
    spec = [
        ("position_id", "ID"), ("symbol", "Symbol"), ("strategy", "Strat"),
        ("strikes", "Strikes"), ("expiration", "Exp"), ("quantity", "Qty"),
        ("entry_credit", "Credit"), ("current_value", "CurVal"),
        ("unrealized_pnl", "P&L$"), ("status", "Status"),
    ]
    return [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]


def position_rows(positions):
    rows = []
    for p in positions or []:
        sk, lk = p.get("short_strike"), p.get("long_strike")
        rows.append({
            "id": p.get("position_id"),
            "position_id": p.get("position_id"),
            "symbol": p.get("symbol", ""),
            "strategy": p.get("strategy", ""),
            "strikes": f"{sk}/{lk}" if sk is not None else "—",
            "expiration": p.get("expiration", ""),
            "quantity": p.get("quantity"),
            "entry_credit": _round(p.get("entry_credit")),
            "current_value": _round(p.get("current_value")),
            "unrealized_pnl": _round(p.get("unrealized_pnl")),
            "status": p.get("status", ""),
        })
    return rows


def order_columns():
    spec = [
        ("order_id", "OrderID"), ("ts", "Time"), ("side", "Side"),
        ("symbol", "Symbol"), ("quantity", "Qty"), ("order_type", "Type"),
        ("fill_price", "Fill$"), ("status", "Status"), ("reject_reason", "Reason"),
    ]
    return [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]


def order_rows(orders):
    rows = []
    for o in orders or []:
        rows.append({
            "order_id": o.get("order_id"),
            "ts": (o.get("ts") or "")[:19],
            "side": o.get("side", ""),
            "symbol": o.get("symbol", ""),
            "quantity": o.get("quantity"),
            "order_type": o.get("order_type", ""),
            "fill_price": _round(o.get("fill_price")),
            "status": o.get("status", ""),
            "reject_reason": o.get("reject_reason") or "",
        })
    return rows


def render():
    """Paper Portfolio page: account cards + positions + fills log."""
    import datetime as dt

    from nicegui import run, ui

    import proxy
    import paper_account_db
    import paper_engine
    import signal_db

    ui.label("Paper Portfolio").classes("text-h5")

    with ui.row().classes("items-center gap-2 flex-wrap"):
        ui.button("Reload", icon="refresh", on_click=lambda: _load())
        ui.button("Run entry cycle", icon="login",
                  on_click=lambda: _cycle("entry")).props("outline")
        ui.button("Run manage cycle", icon="manage_accounts",
                  on_click=lambda: _cycle("manage")).props("outline")
        ui.button("Reset account", icon="restart_alt",
                  on_click=lambda: _reset()).props("flat color=negative")
        spinner = ui.spinner(size="lg")
        spinner.visible = False
    status = ui.label("").classes("opacity-70")

    cards_box = ui.row().classes("gap-3 flex-wrap")
    ui.label("Open positions").classes("text-subtitle1 mt-2")
    pos_table = ui.table(columns=position_columns(), rows=[], row_key="id").classes("w-full")
    ui.label("Fills log (last 100)").classes("text-subtitle1 mt-2")
    ord_table = ui.table(columns=order_columns(), rows=[], row_key="order_id").classes("w-full")

    def _load():
        cards_box.clear()
        try:
            snap = paper_engine.account_snapshot()
        except Exception:
            snap = None
        with cards_box:
            if snap is None:
                ui.label("No paper account yet — use Reset account to initialize.").classes("opacity-70")
            else:
                for label, value in account_cards(snap):
                    with ui.card().classes("p-2 min-w-[110px]"):
                        ui.label(label).classes("text-xs opacity-60")
                        ui.label(value).classes("text-base font-bold")
        try:
            pos_table.rows = position_rows(paper_account_db.fetch_open_positions(None))
        except Exception:
            pos_table.rows = []
        try:
            ord_table.rows = order_rows(paper_account_db.fetch_orders(None, limit=100, status="FILLED"))
        except Exception:
            ord_table.rows = []
        pos_table.update()
        ord_table.update()
        status.text = f"{len(pos_table.rows)} open positions, {len(ord_table.rows)} fills."

    def _reset():
        with ui.dialog() as dlg, ui.card():
            ui.label("Reset paper account?").classes("text-subtitle1")
            bal = ui.number("Starting balance", value=25000.0, format="%.2f")

            def confirm():
                try:
                    paper_account_db.reset_account(starting_balance=float(bal.value))
                except Exception as exc:
                    ui.notify(f"Reset failed: {exc}", type="negative")
                    return
                dlg.close()
                ui.notify("Paper account reset.", type="positive")
                _load()

            with ui.row():
                ui.button("Confirm", on_click=confirm).props("color=negative")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    def _run_cycle(kind):
        now_date = dt.date.today().isoformat()
        if kind == "entry":
            signals = signal_db.get_open_signals_with_latest_mark()
            paper_engine.run_entry_cycle(proxy.schwab_py_client, now_date, signals)
        else:
            paper_engine.run_manage_cycle(proxy.schwab_py_client, now_date)

    async def _cycle(kind):
        spinner.visible = True
        status.text = f"Running {kind} cycle…"
        try:
            await run.io_bound(_run_cycle, kind)
        except Exception as exc:
            ui.notify(f"{kind} cycle failed: {exc}", type="negative")
            return
        finally:
            spinner.visible = False
        ui.notify(f"{kind} cycle complete.", type="positive")
        _load()

    _load()
