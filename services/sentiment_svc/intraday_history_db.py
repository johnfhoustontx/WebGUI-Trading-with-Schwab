"""SQLite persistence for the 2-min intraday sentiment + trend series.

One row per ~2-min sample: (ts unix-seconds, sentiment 0-10, trend 0-100).
Rolling window = the last N distinct LOCAL trading dates present (so weekends /
holidays / gaps are handled by date-presence, not a fixed calendar lookback).
Mirrors the gex_history_db pattern."""
from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sentiment_intraday (
    ts        INTEGER PRIMARY KEY,
    sentiment REAL,
    trend     REAL
);
CREATE INDEX IF NOT EXISTS idx_si_ts ON sentiment_intraday(ts);
"""


def connect(path=None) -> sqlite3.Connection:
    if path is None:
        from repo_paths import SENTIMENT_INTRADAY_DB
        path = SENTIMENT_INTRADAY_DB
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def _local_date(ts: int) -> _dt.date:
    return _dt.datetime.fromtimestamp(ts).astimezone().date()


def insert_point(conn, ts: int, sentiment: float, trend: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sentiment_intraday(ts, sentiment, trend) "
        "VALUES (?, ?, ?)", (int(ts), float(sentiment), float(trend)))
    conn.commit()


def load_recent(conn, n_days: int = 5):
    """[(ts, sentiment, trend)] for the last n_days distinct local dates, asc."""
    rows = conn.execute(
        "SELECT ts, sentiment, trend FROM sentiment_intraday ORDER BY ts ASC"
    ).fetchall()
    if not rows:
        return []
    dates = sorted({_local_date(r[0]) for r in rows})
    keep = set(dates[-n_days:])
    return [r for r in rows if _local_date(r[0]) in keep]


def prune(conn, n_days: int = 5) -> None:
    """Delete rows older than the last n_days distinct local dates."""
    dates = sorted({_local_date(r[0])
                    for r in conn.execute("SELECT ts FROM sentiment_intraday")})
    if len(dates) <= n_days:
        return
    cutoff_date = dates[-n_days]
    cutoff_ts = int(_dt.datetime.combine(cutoff_date, _dt.time.min)
                    .astimezone().timestamp())
    conn.execute("DELETE FROM sentiment_intraday WHERE ts < ?", (cutoff_ts,))
    conn.commit()
