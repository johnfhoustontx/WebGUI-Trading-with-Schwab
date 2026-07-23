"""SQLite persistence for the 2-min intraday sentiment + trend series.

One row per ~2-min sample: (ts unix-seconds, sentiment 0-10, trend 0-100).
Rolling window = the last N distinct LOCAL trading dates present (so weekends /
holidays / gaps are handled by date-presence, not a fixed calendar lookback).
Mirrors the gex_history_db pattern."""
from __future__ import annotations

import datetime as _dt
import os
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sentiment_intraday (
    ts        INTEGER PRIMARY KEY,
    sentiment REAL,
    trend     REAL
);
CREATE TABLE IF NOT EXISTS regime_intraday (
    ts         INTEGER PRIMARY KEY,
    mr         REAL,   -- mean_reversion membership
    tr         REAL,   -- trending
    bo         REAL,   -- breakout
    ch         REAL,   -- choppy
    cr         REAL,   -- crisis
    confidence REAL,
    label      TEXT
);
"""

# regime membership key <-> column mapping (single source of truth).
_REGIME_COLS = (
    ("mean_reversion", "mr"),
    ("trending", "tr"),
    ("breakout", "bo"),
    ("choppy", "ch"),
    ("crisis", "cr"),
)


def connect(path=None) -> sqlite3.Connection:
    if path is None:
        # Under pytest the default connection is in-memory (mirrors Bus's
        # fakeredis-under-pytest convention): tests that exercise the refresh
        # path must never insert their fixture points into the REAL rolling
        # intraday DB — the live service republishes whatever is in the file,
        # so leaked test rows showed up as spikes on the /sentiment intraday
        # graphs (2026-07-07). An explicit ``path`` is always honored.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            path = ":memory:"
        else:
            from repo_paths import SENTIMENT_INTRADAY_DB
            path = SENTIMENT_INTRADAY_DB
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the sentiment service shares one connection across
    # executor threads, serialized by the caller's lock (handlers._INTRADAY_LOCK).
    conn = sqlite3.connect(path, check_same_thread=False)
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


def insert_regime_point(conn, ts: int, memberships: dict,
                        confidence: float, label: str) -> None:
    """Record one regime-membership sample. `memberships` is a dict with the
    five regime keys (mean_reversion/trending/breakout/choppy/crisis)."""
    cols = [float(memberships[key]) for key, _ in _REGIME_COLS]
    conn.execute(
        "INSERT OR REPLACE INTO regime_intraday"
        "(ts, mr, tr, bo, ch, cr, confidence, label) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (int(ts), *cols, float(confidence), str(label)))
    conn.commit()


def load_regime_recent(conn, n_days: int = 1):
    """[(ts, {5-key memberships}, confidence, label)] for the last n_days
    distinct local dates, asc. Default n_days=1 = today's points."""
    rows = conn.execute(
        "SELECT ts, mr, tr, bo, ch, cr, confidence, label "
        "FROM regime_intraday ORDER BY ts ASC"
    ).fetchall()
    if not rows:
        return []
    dates = sorted({_local_date(r[0]) for r in rows})
    keep = set(dates[-n_days:])
    out = []
    for r in rows:
        if _local_date(r[0]) not in keep:
            continue
        mem = {key: r[1 + i] for i, (key, _) in enumerate(_REGIME_COLS)}
        out.append((r[0], mem, r[6], r[7]))
    return out


def prune_regime(conn, n_days: int = 30) -> None:
    """Delete rows older than the last n_days distinct local dates (30 = the
    tuning/offline-validation window, wider than the 5-session sentiment one)."""
    dates = sorted({_local_date(r[0])
                    for r in conn.execute("SELECT ts FROM regime_intraday")})
    if len(dates) <= n_days:
        return
    cutoff_date = dates[-n_days]
    cutoff_ts = int(_dt.datetime.combine(cutoff_date, _dt.time.min)
                    .astimezone().timestamp())
    conn.execute("DELETE FROM regime_intraday WHERE ts < ?", (cutoff_ts,))
    conn.commit()
