"""SQLite persistence for the daily committed market-state (validation record).

One row per LOCAL calendar date: the five-state classifier's committed state plus
the direction score, aggression, and a JSON blob of supporting components. Today's
row is REPLACE-updated each RTH recompute so the latest read of the day wins. A
generous window (90 distinct dates) is retained so a later task can backtest whether
the five states stratify forward returns. Mirrors the ``sector_pcr_history_db`` /
``intraday_history_db`` pattern."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_state (
    date            TEXT PRIMARY KEY,
    committed_state TEXT,
    direction_score REAL,
    aggression      REAL,
    components      TEXT
);
"""


def connect(path=None) -> sqlite3.Connection:
    if path is None:
        from repo_paths import MARKET_STATE_HISTORY_DB
        path = MARKET_STATE_HISTORY_DB
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the sentiment service shares one connection across
    # executor threads, serialized by the caller's lock (handlers._INTRADAY_LOCK).
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.executescript(_SCHEMA)
    return conn


def record(conn, date_iso: str, committed_state, direction_score, aggression,
           components) -> None:
    """Upsert today's committed market-state (latest read of the day wins).

    ``components`` is a dict; it is JSON-encoded (defensive: an unserializable
    value stores ``"{}"`` rather than raising)."""
    try:
        components_json = json.dumps(components if components is not None else {})
    except (TypeError, ValueError):
        components_json = "{}"
    conn.execute(
        "INSERT OR REPLACE INTO market_state"
        "(date, committed_state, direction_score, aggression, components) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(date_iso),
         None if committed_state is None else str(committed_state),
         None if direction_score is None else float(direction_score),
         None if aggression is None else float(aggression),
         components_json))
    conn.commit()


def load_recent(conn, n_days: int = 60):
    """Rows for the last ``n_days`` distinct dates, date-asc, each a dict with
    the components JSON decoded back to a dict."""
    rows = conn.execute(
        "SELECT date, committed_state, direction_score, aggression, components "
        "FROM market_state ORDER BY date ASC").fetchall()
    if not rows:
        return []
    dates = [r[0] for r in rows]
    keep = set(dates[-n_days:])
    out = []
    for date, state, score, aggr, comp_json in rows:
        if date not in keep:
            continue
        try:
            components = json.loads(comp_json) if comp_json else {}
        except (TypeError, ValueError):
            components = {}
        out.append({"date": date, "committed_state": state,
                    "direction_score": score, "aggression": aggr,
                    "components": components})
    return out


def prune(conn, keep: int = 90) -> None:
    """Delete rows older than the last ``keep`` distinct dates."""
    dates = [r[0] for r in conn.execute(
        "SELECT date FROM market_state ORDER BY date ASC")]
    if len(dates) <= keep:
        return
    cutoff = dates[-keep]
    conn.execute("DELETE FROM market_state WHERE date < ?", (cutoff,))
    conn.commit()
