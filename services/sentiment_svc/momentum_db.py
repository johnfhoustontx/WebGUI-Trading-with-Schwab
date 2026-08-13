"""SQLite persistence for the momentum cascade — daily bars + scored levels.

``daily_bars`` is what makes the nightly run cheap: ``max_date`` drives a
delta fetch, so run two onward pulls one trading day per symbol instead of
252. ``momentum_scores`` holds everything the GUI needs, so the page never
calls the proxy.

Follows intraday_history_db for connection handling and the pytest default.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from pathlib import Path

# No separate index on daily_bars(symbol): the (symbol, date) PK autoindex has
# symbol as its leading column and already serves those lookups. A duplicate
# index is pure write cost (the idx_snap_today lesson from gex_history_db).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS momentum_scores (
    session_date    TEXT NOT NULL,
    level           TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    score           REAL,
    percentile      REAL,
    rank            INTEGER,
    components_json TEXT,
    participation   REAL,
    PRIMARY KEY (session_date, level, symbol)
);
CREATE INDEX IF NOT EXISTS idx_scores_level ON momentum_scores(level, session_date);
"""

KEEP_DAYS = 400
BARS_LIMIT = 252
RANK_HISTORY_DAYS = 60


def connect(path=None) -> sqlite3.Connection:
    if path is None:
        # Same rule as intraday_history_db: under pytest the default store is
        # in-memory so fixture rows can never reach the real nightly DB.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            path = ":memory:"
        else:
            from repo_paths import MOMENTUM_DB
            path = MOMENTUM_DB
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the nightly run fans out across executor threads,
    # serialized by the caller's lock (handlers._MOMENTUM_LOCK).
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.executescript(_SCHEMA)
    return conn


def _f(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def upsert_bars(conn, rows) -> None:
    payload = []
    for row in rows or []:
        symbol = (row.get("symbol") or "").strip()
        date = row.get("date")
        if not symbol or not date:
            continue
        payload.append((symbol, str(date), _f(row.get("open")), _f(row.get("high")),
                        _f(row.get("low")), _f(row.get("close")), _f(row.get("volume"))))
    if not payload:
        return
    conn.executemany(
        "INSERT OR REPLACE INTO daily_bars"
        "(symbol, date, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", payload)
    conn.commit()


def max_date(conn, symbol):
    """Latest stored bar date for ``symbol``, or None on the first-backfill path."""
    row = conn.execute("SELECT MAX(date) FROM daily_bars WHERE symbol = ?",
                       (symbol,)).fetchone()
    return row[0] if row and row[0] else None


def bars(conn, symbol, limit=BARS_LIMIT):
    """The most recent ``limit`` bars for ``symbol``, oldest first."""
    rows = conn.execute(
        "SELECT symbol, date, open, high, low, close, volume FROM daily_bars "
        "WHERE symbol = ? ORDER BY date DESC LIMIT ?", (symbol, int(limit))
    ).fetchall()
    keys = ("symbol", "date", "open", "high", "low", "close", "volume")
    return [dict(zip(keys, r)) for r in reversed(rows)]


def write_scores(conn, session_date, level, rows) -> None:
    """Replace the whole (session_date, level) slice — a rerun must not leave
    yesterday's symbols behind when the universe shrinks."""
    conn.execute("DELETE FROM momentum_scores WHERE session_date = ? AND level = ?",
                 (str(session_date), str(level)))
    payload = []
    for row in rows or []:
        symbol = (row.get("symbol") or "").strip()
        if not symbol:
            continue
        rank = row.get("rank")
        payload.append((
            str(session_date), str(level), symbol,
            _f(row.get("score")), _f(row.get("percentile")),
            int(rank) if rank is not None else None,
            json.dumps(row.get("components") or {}),
            _f(row.get("participation")),
        ))
    if payload:
        conn.executemany(
            "INSERT OR REPLACE INTO momentum_scores"
            "(session_date, level, symbol, score, percentile, rank,"
            " components_json, participation) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            payload)
    conn.commit()


def _score_rows(rows):
    out = []
    for r in rows:
        try:
            components = json.loads(r[5]) if r[5] else {}
        except (TypeError, ValueError):
            components = {}
        out.append({"symbol": r[0], "score": r[1], "percentile": r[2],
                    "rank": r[3], "participation": r[4], "components": components})
    return out


def scores(conn, session_date, level):
    """Scored rows for one session and level, best rank first."""
    rows = conn.execute(
        "SELECT symbol, score, percentile, rank, participation, components_json "
        "FROM momentum_scores WHERE session_date = ? AND level = ? "
        "ORDER BY rank IS NULL, rank ASC, symbol ASC",
        (str(session_date), str(level))).fetchall()
    return _score_rows(rows)


def rank_history(conn, level, days=RANK_HISTORY_DAYS):
    """{symbol: [(session_date, rank), ...]} over the last ``days`` sessions."""
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT session_date FROM momentum_scores WHERE level = ? "
        "ORDER BY session_date ASC", (str(level),)).fetchall()]
    if not dates:
        return {}
    keep = dates[-int(days):]
    placeholders = ",".join("?" * len(keep))
    rows = conn.execute(
        f"SELECT symbol, session_date, rank FROM momentum_scores "  # noqa: S608 - placeholders only
        f"WHERE level = ? AND session_date IN ({placeholders}) "
        "ORDER BY session_date ASC", (str(level), *keep)).fetchall()
    out: dict[str, list] = {}
    for symbol, session_date, rank in rows:
        out.setdefault(symbol, []).append((session_date, rank))
    return out


def prune(conn, keep_days=KEEP_DAYS) -> None:
    """Drop bars and scores older than ``keep_days`` calendar days before the
    newest row (calendar, not sessions — a stale symbol must age out too)."""
    newest = conn.execute(
        "SELECT MAX(d) FROM (SELECT MAX(date) AS d FROM daily_bars "
        "UNION SELECT MAX(session_date) FROM momentum_scores)").fetchone()[0]
    if not newest:
        return
    try:
        cutoff = (_dt.date.fromisoformat(newest)
                  - _dt.timedelta(days=int(keep_days))).isoformat()
    except ValueError:
        return
    conn.execute("DELETE FROM daily_bars WHERE date < ?", (cutoff,))
    conn.execute("DELETE FROM momentum_scores WHERE session_date < ?", (cutoff,))
    conn.commit()
