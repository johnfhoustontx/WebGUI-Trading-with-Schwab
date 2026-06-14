"""
SchwabProxy - Trade performance DB writer
Version: 1.0.0
Last Updated: 2026-05-30

Version 1.0.0 Changes:
- Initial implementation

Writes streaming-derived lifecycle events + IV snapshots into the OptionsScanner
trade_performance.db. Schema mirrors OptionsScanner/trade_performance_db.py; both
sides init idempotently. Write failures are logged, never raised — the stream
loop must survive a transient DB lock.
"""
import logging
import sqlite3
import sys, pathlib
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import OPTIONS_SCANNER

log = logging.getLogger("perf_writer")

#############################################
# PATH + SCHEMA (mirror of OptionsScanner side)
#############################################

PERF_DB = OPTIONS_SCANNER / "data" / "trade_performance.db"

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


#############################################
# CONNECT + INIT
#############################################

def connect(path=PERF_DB):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_schema(path=PERF_DB):
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


#############################################
# WRITES (never raise — log and swallow)
#############################################

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


def record_event(event, path=PERF_DB):
    try:
        _insert(path, "perf_events", _EVENT_COLS, event)
    except Exception as exc:  # stream loop must never crash on a DB hiccup
        log.warning("record_event failed (%s): %s", path, exc)


def record_iv_snapshot(snap, path=PERF_DB):
    try:
        _insert(path, "perf_iv_snapshots", _IV_COLS, snap)
    except Exception as exc:  # stream loop must never crash on a DB hiccup
        log.warning("record_iv_snapshot failed (%s): %s", path, exc)


#############################################
# LOAD FIRED (rebuild per-trade fired set on startup)
#############################################

def load_fired(trade_id, path=PERF_DB):
    conn = connect(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT event_type FROM perf_events WHERE trade_id=?",
            (trade_id,),
        ).fetchall()
        return {r["event_type"] for r in rows}
    finally:
        conn.close()
