"""SQLite persistence for streaming-derived trade performance.

Owned by OptionsScanner; written by the external SchwabProxy stream worker and
read by the dashboard / EOD report. Separate file from trades.db so the
hand-managed trade lifecycle and the stream-derived performance data never
contend for the same table locks.

Schema is view-stable and idempotent — safe to init on every startup from
either process.
"""
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "trade_performance.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS perf_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id       TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    ts             TEXT NOT NULL,
    underlying     REAL,
    mid            REAL,
    unrealized_pnl REAL,
    pnl_pct        REAL,
    note           TEXT
);
CREATE TABLE IF NOT EXISTS perf_iv_snapshots (
    snap_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id       TEXT NOT NULL,
    moment         TEXT NOT NULL,
    ts             TEXT NOT NULL,
    put_short_iv   REAL,
    put_long_iv    REAL,
    call_short_iv  REAL,
    call_long_iv   REAL,
    underlying     REAL
);
CREATE INDEX IF NOT EXISTS idx_perf_events_trade ON perf_events(trade_id);
CREATE INDEX IF NOT EXISTS idx_perf_iv_trade ON perf_iv_snapshots(trade_id);
"""

_EVENT_COLS = ["trade_id", "event_type", "ts", "underlying", "mid",
               "unrealized_pnl", "pnl_pct", "note"]
_IV_COLS = ["trade_id", "moment", "ts", "put_short_iv", "put_long_iv",
            "call_short_iv", "call_long_iv", "underlying"]


def connect(path=DEFAULT_DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(path=DEFAULT_DB_PATH):
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _insert(path, table, cols, row):
    conn = connect(path)
    try:
        placeholders = ", ".join("?" for _ in cols)
        # cols come only from module-level _EVENT_COLS/_IV_COLS, never user input
        conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
            [row.get(c) for c in cols],
        )
        conn.commit()
    finally:
        conn.close()


def record_event(path, event):
    _insert(path, "perf_events", _EVENT_COLS, event)


def record_iv_snapshot(path, snap):
    _insert(path, "perf_iv_snapshots", _IV_COLS, snap)


def get_events(path, trade_id):
    conn = connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM perf_events WHERE trade_id=? ORDER BY ts", (trade_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_iv_snapshots(path, trade_id):
    conn = connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM perf_iv_snapshots WHERE trade_id=? ORDER BY ts", (trade_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def events_on_date(path, date_str):
    conn = connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM perf_events WHERE substr(ts,1,10)=? ORDER BY ts", (date_str,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def entry_ts(path, trade_id):
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT ts FROM perf_iv_snapshots WHERE trade_id=? AND moment='entry' "
            "ORDER BY ts LIMIT 1", (trade_id,)
        ).fetchone()
        return row["ts"] if row else None
    finally:
        conn.close()


def summarize(path, trade_id):
    events = get_events(path, trade_id)
    by_type = {e["event_type"]: e for e in events}
    return {
        "target_hit_ts": by_type.get("target_hit", {}).get("ts"),
        "stop_hit_ts": by_type.get("stop_hit", {}).get("ts"),
        "strike_test_ts": by_type.get("strike_test", {}).get("ts"),
        "events": events,
    }
