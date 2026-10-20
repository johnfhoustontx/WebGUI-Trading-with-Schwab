"""The Opening Scalp's store: price bars and the morning's chain slice.

``services/options_svc/data/scalp.db`` (``repo_paths.SCALP_DB``). It is gitignored
like every ``*.db`` and the nightly backup sweeps it with no list to edit.
Later tasks add their own tables through ``connect`` (the schema is
``CREATE TABLE IF NOT EXISTS``, run on every connection): the study's
``outcomes``, ``mornings`` and ``verdicts`` are written only by
``scalp_study.rebuild``; ``calls`` (each published morning call, frozen as shown)
only by ``scalp_call``, and ``rebuild`` never touches it. ``grades`` (each
published role's result, once the day's bars are in) and ``iv_calibration`` (the
chain's at-the-money volatility over the morning's reading) are written only by
``scalp_grade`` and, like ``calls``, are never rebuilt: they are records of what
was shown, not part of the study. The ``intraday_*`` tables hold the 15-minute
calls: each reading, call and chain slice keyed by ``(day, slot, symbol)``. They
sit beside the morning tables and never alter them.

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
CREATE TABLE IF NOT EXISTS outcomes (
    day          TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    iv_scale     REAL    NOT NULL,
    role         TEXT    NOT NULL,
    structure    TEXT    NOT NULL,
    bucket       TEXT,
    result       TEXT    NOT NULL,
    minutes      INTEGER,
    entry_value  REAL,
    end_pnl_pct  REAL,
    both_in_bar  INTEGER NOT NULL,
    interval     INTEGER NOT NULL,
    PRIMARY KEY (day, symbol, iv_scale, role, interval)
);
CREATE TABLE IF NOT EXISTS mornings (
    day    TEXT NOT NULL,
    symbol TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS verdicts (
    symbol TEXT NOT NULL,
    role   TEXT NOT NULL,
    bucket TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (symbol, role, bucket)
);
CREATE TABLE IF NOT EXISTS calls (
    day    TEXT NOT NULL,
    symbol TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS grades (
    day          TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    structure    TEXT    NOT NULL,
    model_result TEXT,
    chain_result TEXT,
    highlighted  INTEGER NOT NULL,
    avoided      INTEGER NOT NULL,
    p_base       REAL,
    PRIMARY KEY (day, symbol, role)
);
CREATE TABLE IF NOT EXISTS iv_calibration (
    day         TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    atm_iv      REAL NOT NULL,
    vol_reading REAL NOT NULL,
    ratio       REAL NOT NULL,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS intraday_readings (
    day    TEXT NOT NULL,
    slot   TEXT NOT NULL,
    symbol TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (day, slot, symbol)
);
CREATE TABLE IF NOT EXISTS intraday_calls (
    day    TEXT NOT NULL,
    slot   TEXT NOT NULL,
    symbol TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (day, slot, symbol)
);
CREATE TABLE IF NOT EXISTS intraday_chain_slices (
    day    TEXT    NOT NULL,
    slot   TEXT    NOT NULL,
    symbol TEXT    NOT NULL,
    ts     INTEGER NOT NULL,
    body   BLOB    NOT NULL,
    PRIMARY KEY (day, slot, symbol)
);
CREATE TABLE IF NOT EXISTS intraday_outcomes (
    day          TEXT    NOT NULL,
    slot         TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    iv_scale     REAL    NOT NULL,
    role         TEXT    NOT NULL,
    structure    TEXT    NOT NULL,
    block        TEXT    NOT NULL,
    bucket       TEXT,
    result       TEXT    NOT NULL,
    minutes      INTEGER,
    entry_value  REAL,
    both_in_bar  INTEGER NOT NULL,
    PRIMARY KEY (day, slot, symbol, iv_scale, role)
);
CREATE TABLE IF NOT EXISTS intraday_verdicts (
    symbol TEXT NOT NULL,
    block  TEXT NOT NULL,
    role   TEXT NOT NULL,
    bucket TEXT NOT NULL,
    json   TEXT NOT NULL,
    PRIMARY KEY (symbol, block, role, bucket)
);
CREATE TABLE IF NOT EXISTS intraday_grades (
    day          TEXT    NOT NULL,
    slot         TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    structure    TEXT    NOT NULL,
    model_result TEXT,
    chain_result TEXT,
    highlighted  INTEGER NOT NULL,
    avoided      INTEGER NOT NULL,
    confirm_word TEXT,
    p_base       REAL,
    block        TEXT    NOT NULL,
    PRIMARY KEY (day, slot, symbol, role)
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


def save_call(day, symbol, body, db_path=None) -> None:
    """Keep ``symbol``'s published call for ``day`` (a JSON-able dict), replacing
    the day's earlier one."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO calls (day, symbol, json) VALUES (?, ?, ?)",
                (day, symbol, json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False)))
    finally:
        conn.close()


def calls_on(day, db_path=None) -> dict:
    """``{symbol: call}`` saved for ``day`` (empty when none)."""
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT symbol, json FROM calls WHERE day = ?",
                            (day,)).fetchall()
    finally:
        conn.close()
    return {sym: json.loads(body) for sym, body in rows}


def latest_call_day(db_path=None):
    """The most recent ``day`` with a saved call, or ``None``."""
    conn = connect(db_path)
    try:
        return conn.execute("SELECT MAX(day) FROM calls").fetchone()[0]
    finally:
        conn.close()


def save_reading(day, slot, symbol, body, db_path=None) -> None:
    """Keep ``symbol``'s intraday reading for ``day``'s ``slot`` (a JSON-able dict),
    replacing the earlier write for that slot."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO intraday_readings (day, slot, symbol, json) "
                "VALUES (?, ?, ?, ?)",
                (day, slot, symbol, json.dumps(body, sort_keys=True, separators=(",", ":"),
                                               allow_nan=False)))
    finally:
        conn.close()


def readings_on(day, db_path=None) -> dict:
    """``{(slot, symbol): reading}`` saved for ``day`` (empty when none)."""
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT slot, symbol, json FROM intraday_readings "
                            "WHERE day = ?", (day,)).fetchall()
    finally:
        conn.close()
    return {(slot, sym): json.loads(body) for slot, sym, body in rows}


def save_intraday_call(day, slot, symbol, body, db_path=None) -> None:
    """Keep ``symbol``'s intraday call for ``day``'s ``slot`` (a JSON-able dict),
    replacing the earlier write for that slot. The morning ``calls`` table is not
    touched."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO intraday_calls (day, slot, symbol, json) "
                "VALUES (?, ?, ?, ?)",
                (day, slot, symbol, json.dumps(body, sort_keys=True, separators=(",", ":"),
                                               allow_nan=False)))
    finally:
        conn.close()


def intraday_calls_on(day, db_path=None) -> dict:
    """``{(slot, symbol): call}`` saved for ``day`` (empty when none)."""
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT slot, symbol, json FROM intraday_calls "
                            "WHERE day = ?", (day,)).fetchall()
    finally:
        conn.close()
    return {(slot, sym): json.loads(body) for slot, sym, body in rows}


def save_intraday_slice(day, slot, symbol, ts, body, db_path=None) -> None:
    """Keep ``symbol``'s chain slice for ``day``'s ``slot`` (zlib-compressed JSON),
    replacing the earlier one for that slot."""
    blob = zlib.compress(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO intraday_chain_slices "
                "(day, slot, symbol, ts, body) VALUES (?, ?, ?, ?, ?)",
                (day, slot, symbol, ts, blob))
    finally:
        conn.close()


def intraday_slice(day, slot, symbol, db_path=None):
    """The saved slice for ``day``'s ``slot`` and ``symbol``, or ``None``."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT body FROM intraday_chain_slices WHERE day = ? AND slot = ? "
            "AND symbol = ?", (day, slot, symbol)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return json.loads(zlib.decompress(row[0]).decode("utf-8"))
