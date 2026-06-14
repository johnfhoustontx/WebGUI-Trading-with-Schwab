"""Captured Signals page.

Lists open signals (from ``signal_db``) with live marks, score drift, and
recommendations, alongside the shared Trade detail panel. "Refresh marks"
reprices each open signal off-thread (read-only display update); "Close
selected" records a manual outcome. Reprice/recommend logic lives in the
engines; this module marshals + wires.
"""
import sys

from repo_paths import OPTIONS_SCANNER

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


def captured_columns():
    spec = [
        ("symbol", "Symbol"), ("strategy", "Strat"), ("mode", "Mode"),
        ("expiration", "Exp"), ("dte", "DTE"), ("credit", "Credit"),
        ("max_loss", "Risk"), ("unrealized_pnl", "P&L"),
        ("entry_score", "Entry"), ("current_score", "Cur"), ("score_drift", "Drift"),
        ("grade", "Grade"), ("recommendation", "Rec"), ("status", "Status"),
    ]
    return [{"name": f, "label": lbl, "field": f, "sortable": True, "align": "left"}
            for f, lbl in spec]


def captured_rows(signals):
    """Display rows from signal_db.get_open_signals_with_latest_mark() output."""
    rows = []
    for s in signals or []:
        rows.append({
            "id": s.get("signal_id"),
            "symbol": s.get("symbol", ""),
            "strategy": s.get("strategy", ""),
            "mode": s.get("mode", ""),
            "expiration": s.get("expiration", ""),
            "dte": s.get("dte_at_entry"),
            "credit": _round(s.get("entry_credit")),
            "max_loss": _round(s.get("entry_max_loss")),
            "unrealized_pnl": _round(s.get("unrealized_pnl")),
            "entry_score": s.get("entry_score"),
            "current_score": s.get("current_score"),
            "score_drift": s.get("score_drift"),
            "grade": s.get("entry_grade", ""),
            "recommendation": s.get("recommendation") or "HOLD",
            "status": s.get("status", ""),
        })
    return rows


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
        "dte": r.get("dte_at_entry"),
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
    """Captured Signals page: table (left) + shared detail panel (right)."""
    import datetime as dt

    from nicegui import run, ui

    import proxy
    import signal_db
    import signal_recommender
    import signal_repricer

    from . import detail

    ui.label("Captured Signals").classes("text-h5")

    raw_by_id: dict = {}

    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("flex-grow min-w-0"):
            with ui.row().classes("items-center gap-3"):
                refresh_btn = ui.button("Reload", icon="refresh")
                marks_btn = ui.button("Refresh marks (live)", icon="published_with_changes")
                close_btn = ui.button("Close selected", icon="check_circle").props("outline")
                spinner = ui.spinner(size="lg")
                spinner.visible = False
                status = ui.label("").classes("opacity-70")
            table = ui.table(columns=captured_columns(), rows=[], row_key="id",
                             selection="single").classes("w-full")
        detail_panel = detail.render()

    def _load():
        try:
            sigs = signal_db.get_open_signals_with_latest_mark()
        except Exception as exc:
            ui.notify(f"DB read failed: {exc}", type="negative")
            sigs = []
        raw_by_id.clear()
        for s in sigs:
            if s.get("signal_id"):
                raw_by_id[s["signal_id"]] = s
        table.rows = captured_rows(sigs)
        table.update()
        status.text = f"{len(table.rows)} open signals."

    def _select(event):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sig = raw_by_id.get(row.get("id")) if isinstance(row, dict) else None
        if sig:
            detail_panel.update(synth_from_captured(sig))

    table.on("rowClick", _select)

    def _reprice_all(rows, client):
        now = dt.datetime.now(dt.timezone.utc)
        updates = {}
        flagged = []
        for r in rows:
            try:
                rep = signal_repricer.reprice_swing(r, client)
                mark = signal_recommender.build_mark(r, rep, now)
            except Exception:
                continue
            if not mark:
                continue
            updates[r.get("signal_id")] = mark
            code = (mark.get("recommendation_code") or "").upper()
            if code in ("TARGET_HIT", "MONEY_STOP", "DELTA_STOP", "TIME_STOP"):
                flagged.append((r.get("symbol"), code))
        return updates, flagged

    async def do_marks():
        if not table.rows:
            return
        marks_btn.disable()
        spinner.visible = True
        status.text = "Repricing open signals…"
        try:
            updates, flagged = await run.io_bound(_reprice_all, list(raw_by_id.values()),
                                                  proxy.schwab_py_client)
        except Exception as exc:
            ui.notify(f"Reprice failed: {exc}", type="negative")
            return
        finally:
            spinner.visible = False
            marks_btn.enable()
        for row in table.rows:
            m = updates.get(row["id"])
            if not m:
                continue
            row["unrealized_pnl"] = _round(m.get("unrealized_pnl"))
            row["current_score"] = m.get("current_score")
            row["score_drift"] = m.get("score_drift")
            row["recommendation"] = m.get("recommendation") or row["recommendation"]
        table.update()
        status.text = f"Repriced {len(updates)} signals."
        for sym, code in flagged:
            ui.notify(f"{sym}: {code} — consider closing", type="warning")

    def do_close():
        sel = table.selected
        if not sel:
            ui.notify("Select a signal first.", type="warning")
            return
        row = sel[0]
        with ui.dialog() as dlg, ui.card():
            ui.label(f"Close {row.get('symbol')} {row.get('strategy')}").classes("text-subtitle1")
            exit_val = ui.number("Exit value (spread debit)", value=0.0, format="%.2f")
            reason = ui.input("Reason", value="MANUAL_CLOSE")

            def confirm():
                try:
                    signal_db.close_signal_manually(row["id"], float(exit_val.value),
                                                    reason.value or "MANUAL_CLOSE")
                except Exception as exc:
                    ui.notify(f"Close failed: {exc}", type="negative")
                    return
                dlg.close()
                ui.notify("Signal closed.", type="positive")
                _load()

            with ui.row():
                ui.button("Confirm", on_click=confirm).props("color=negative")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    refresh_btn.on_click(_load)
    marks_btn.on_click(do_marks)
    close_btn.on_click(do_close)
    _load()
