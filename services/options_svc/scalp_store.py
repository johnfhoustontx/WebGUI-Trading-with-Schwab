"""The Opening Scalp's store: price bars and the morning's chain slice.

``services/options_svc/data/scalp.db`` (``repo_paths.SCALP_DB``). It is gitignored
like every ``*.db`` and the nightly backup sweeps it with no list to edit.
Later tasks add their own tables through ``connect`` (the schema is
``CREATE TABLE IF NOT EXISTS``, run on every connection).

Pure: ``sqlite3``, ``zlib``, ``json`` and ``repo_paths`` only; it imports nothing
from ``compute`` and calls no network. Every function takes ``db_path=None`` and
resolves the live path when it is CALLED, never at ``def`` time, so a test (or a
monkeypatched ``repo_paths.SCALP_DB``) redirects it. Timestamps are epoch
SECONDS, the START of the bar (Schwab's candle ``datetime`` is milliseconds; the
caller divides).
"""
import json
import os
import sqlite3
import zlib
from typing import NamedTuple

import repo_paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol   TEXT    NOT NULL,
    interval INTEGER NOT NULL,
    ts       INTEGER NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    PRIMARY KEY (symbol, interval, ts)
);
CREATE TABLE IF NOT EXISTS chain_slices (
    day    TEXT    NOT NULL,
    symbol TEXT    NOT NULL,
    ts     INTEGER NOT NULL,
    body   BLOB    NOT NULL,
    PRIMARY KEY (day, symbol)
);
"""


class Bar(NamedTuple):
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def _path(db_path):
    return repo_paths.SCALP_DB if db_path is None else db_path


def connect(db_path=None) -> sqlite3.Connection:
    """Open the store (creating the folder, the file and the schema) in WAL mode.
    The caller closes it."""
    path = _path(db_path)
    parent = os.path.dirname(os.fspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(os.fspath(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def upsert_bars(rows, db_path=None) -> int:
    """Write ``(symbol, interval, ts, open, high, low, close, volume)`` rows,
    a row already stored replaced by the newer write. Returns the row count."""
    rows = list(rows)
    if not rows:
        return 0
    conn = connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO bars "
                "(symbol, interval, ts, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    finally:
        conn.close()
    return len(rows)


def bars(symbol, interval, start_ts, end_ts, db_path=None) -> list:
    """The stored bars with ``start_ts <= ts < end_ts``, oldest first."""
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "SELECT ts, open, high, low, close, volume FROM bars "
            "WHERE symbol = ? AND interval = ? AND ts >= ? AND ts < ? ORDER BY ts",
            (symbol, interval, start_ts, end_ts))
        return [Bar(*r) for r in cur.fetchall()]
    finally:
        conn.close()


def latest_ts(symbol, interval, db_path=None):
    """The newest stored bar's ``ts``, or ``None`` when there is none."""
    conn = connect(db_path)
    try:
        return conn.execute(
            "SELECT MAX(ts) FROM bars WHERE symbol = ? AND interval = ?",
            (symbol, interval)).fetchone()[0]
    finally:
        conn.close()


def save_chain_slice(day, symbol, ts, body, db_path=None) -> None:
    """Keep one day's morning chain slice for ``symbol`` (zlib-compressed JSON),
    replacing the day's earlier one."""
    blob = zlib.compress(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO chain_slices (day, symbol, ts, body) "
                "VALUES (?, ?, ?, ?)", (day, symbol, ts, blob))
    finally:
        conn.close()


def chain_slice(day, symbol, db_path=None):
    """The saved slice for ``day`` and ``symbol``, or ``None``."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT body FROM chain_slices WHERE day = ? AND symbol = ?",
            (day, symbol)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return json.loads(zlib.decompress(row[0]).decode("utf-8"))
