"""perf_report.py — read-only performance aggregation for the dashboard.

Joins claude-driver's trade_log.json (what was approved/executed) with the
proxy's trade_performance.db (live option-trade events) by trade_id. Bucket A
trades carry streamed exit events; B/C are 'polled' (P&L from the trade log)."""
import json
import os
import sqlite3
import sys
import pathlib
from typing import Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from repo_paths import OPTIONS_SCANNER
from config import TRADE_LOG

DEFAULT_PERF_DB = str(OPTIONS_SCANNER / "data" / "trade_performance.db")


def _load_log(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def _terminal_events(perf_db: str) -> dict:
    """trade_id -> last event row (dict). Empty if DB missing."""
    if not os.path.exists(perf_db):
        return {}
    out = {}
    con = sqlite3.connect(perf_db)
    con.row_factory = sqlite3.Row
    try:
        # Keep the latest event per trade_id. event_id (autoincrement) breaks
        # ts ties deterministically so the terminal/exit event wins.
        for r in con.execute("SELECT * FROM perf_events ORDER BY ts, event_id"):
            out[r["trade_id"]] = dict(r)
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return out


def build_report(log_path: Optional[str] = None,
                 perf_db: Optional[str] = None) -> dict:
    log_path = log_path or TRADE_LOG
    perf_db = perf_db or DEFAULT_PERF_DB
    log = _load_log(log_path)
    events = _terminal_events(perf_db)

    trades, wins, losses, realized = [], 0, 0, 0.0
    for e in log:
        tid = e.get("trade_id")
        ev = events.get(tid)
        try:
            pnl = float(e.get("pnl", 0) or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        is_a = e.get("bucket") == "A"
        # "closed" = has a streamed exit event, or a non-zero realized P&L.
        # Limitation: a B/C trade that closed exactly flat (pnl==0, no event)
        # is reported as open. Acceptable for v1.
        closed = bool(ev and ev.get("event_type")) or pnl != 0
        if closed:
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            realized += pnl
        trades.append({
            "trade_id": tid, "date": e.get("date"), "bucket": e.get("bucket"),
            "instrument": e.get("instrument"), "side": e.get("side"),
            "order_id": e.get("order_id"), "pnl": pnl,
            "status": "closed" if closed else "open",
            "source": "streamed" if (is_a and ev) else "polled",
            "exit_reason": ev.get("event_type") if ev else None,
        })

    decided = wins + losses
    win_rate = round(100.0 * wins / decided, 1) if decided else 0.0
    by_bucket = {}
    for t in trades:
        by_bucket.setdefault(t["bucket"], 0.0)
        by_bucket[t["bucket"]] += t["pnl"]

    return {
        "summary": {
            "total_trades": len(trades), "wins": wins, "losses": losses,
            "win_rate": win_rate, "realized_pnl": round(realized, 2),
            "pnl_by_bucket": {k: round(v, 2) for k, v in by_bucket.items()},
        },
        "trades": trades,
    }
