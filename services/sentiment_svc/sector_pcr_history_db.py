"""SQLite persistence for the daily cap-weighted cross-sector Put/Call ratio.

One row per LOCAL calendar date: (date TEXT PRIMARY KEY, pcr REAL). Today's row
is REPLACE-updated each refresh so the latest read of the day wins. The
5-trading-day delta is computed by date-presence (weekends / holidays / gaps are
handled by which dates are present, not a fixed calendar lookback) — exactly like
``intraday_history_db``. Mirrors that store's pattern."""
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_pcr (
    date TEXT PRIMARY KEY,
    pcr  REAL
);
"""


def connect(path=None) -> sqlite3.Connection:
    if path is None:
        from repo_paths import SECTOR_PCR_HISTORY_DB
        path = SECTOR_PCR_HISTORY_DB
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the sentiment service shares one connection across
    # executor threads, serialized by the caller's lock (handlers._INTRADAY_LOCK).
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.executescript(_SCHEMA)
    return conn


def record(conn, date_iso: str, pcr) -> None:
    """Upsert today's cap-weighted P/C ratio (latest read of the day wins)."""
    conn.execute(
        "INSERT OR REPLACE INTO sector_pcr(date, pcr) VALUES (?, ?)",
        (str(date_iso), None if pcr is None else float(pcr)))
    conn.commit()


def sector_pc_delta(conn, lookback: int = 5):
    """``latest.pcr - pcr_from(lookback trading-days ago)``.

    "lookback trading days ago" = the ``(lookback+1)``-th most-recent DISTINCT
    date present (dates sorted ascending, index ``-(lookback+1)``). Returns
    ``None`` if fewer than ``lookback+1`` distinct dated rows exist, or if either
    endpoint's pcr is None."""
    rows = conn.execute(
        "SELECT date, pcr FROM sector_pcr ORDER BY date ASC").fetchall()
    if len(rows) < lookback + 1:
        return None
    latest = rows[-1][1]
    prior = rows[-(lookback + 1)][1]
    if latest is None or prior is None:
        return None
    return latest - prior


def prune(conn, keep: int = 10) -> None:
    """Delete rows older than the last ``keep`` distinct dates."""
    dates = [r[0] for r in conn.execute(
        "SELECT date FROM sector_pcr ORDER BY date ASC")]
    if len(dates) <= keep:
        return
    cutoff = dates[-keep]
    conn.execute("DELETE FROM sector_pcr WHERE date < ?", (cutoff,))
    conn.commit()
