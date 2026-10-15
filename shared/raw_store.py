"""The raw chain store: one SQLite file per session of the option chains the
one-minute collection fetched, before anything was derived from them.

Design: docs/plans/2026-10-14-raw-feed-and-catch-up-design.md §4. Schwab
serves no past option chain, so a minute nobody saved is gone; a minute saved
here can be derived later -- after a restart, a crash or a promote of the
service that derives it.

* **One transaction per minute.** Every chain of the minute, then its
  ``minutes`` row. A minute is complete or absent, and a reader decides what
  exists from ``minutes``, never from ``chains``.
* **A gap is recorded, never guessed.** ``gaps`` holds a minute that could not
  be collected and why.
* **Retention** is deleting whole files: ``prune`` keeps the newest N sessions.
  The folder is gitignored and never backed up (``tools/backup_local.db_paths``).
* Readers open the file read-only and never create it; only the writer does.

Stdlib + ``repo_paths`` + ``shared.raw_minute`` only. Every function takes
``root=None``, resolved to ``repo_paths.RAW_DIR`` at CALL time, so a test can
point the store at a temporary folder.
"""
from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import time
import zlib

from shared.raw_minute import RawMinute

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chains (
    ts              INTEGER NOT NULL,
    symbol          TEXT    NOT NULL,
    kind            TEXT    NOT NULL,      -- 'chain' | 'stored' | 'term'
    carried         INTEGER NOT NULL DEFAULT 0,
    carried_age_sec REAL,
    seq             INTEGER NOT NULL,      -- fetch order within the minute
    body            BLOB    NOT NULL,      -- zlib(JSON) of the chain
    PRIMARY KEY (ts, symbol, kind)
);
CREATE TABLE IF NOT EXISTS minutes (
    ts         INTEGER PRIMARY KEY,
    symbols    TEXT    NOT NULL,           -- JSON list: the universe asked
    written_at REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS gaps (
    ts       INTEGER PRIMARY KEY,
    reason   TEXT NOT NULL,
    noted_at REAL NOT NULL
);
"""

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ZLIB_LEVEL = 6
# The term chain is not a per-symbol chain of the minute; it is keyed apart so
# it can never collide with a collected symbol's row.
_TERM_KEY = "$SPX"


def _root(root):
    if root is not None:
        return pathlib.Path(root)
    import repo_paths
    return pathlib.Path(repo_paths.RAW_DIR)


def session_path(date_iso: str, root=None) -> pathlib.Path:
    """The file holding session ``date_iso`` (YYYY-MM-DD)."""
    if not _DATE.match(str(date_iso)):
        raise ValueError(f"not a session date: {date_iso!r}")
    return _root(root) / f"{date_iso}.db"


def _pack(obj) -> bytes:
    return zlib.compress(json.dumps(obj, separators=(",", ":")).encode("utf-8"),
                         _ZLIB_LEVEL)


def _unpack(blob):
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def _write_conn(date_iso, root):
    path = session_path(date_iso, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _read_conn(date_iso, root):
    """A read-only connection, or None when the session has no file."""
    path = session_path(date_iso, root)
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)


def _insert_minute_row(conn, minute: RawMinute) -> None:
    conn.execute("INSERT OR REPLACE INTO minutes (ts, symbols, written_at) VALUES (?, ?, ?)",
                 (int(minute.ts), json.dumps(list(minute.symbols)), time.time()))


def write_minute(date_iso: str, minute: RawMinute, root=None) -> None:
    """Store one minute, replacing any earlier copy of it, in ONE transaction:
    its chains first, its ``minutes`` row last. Raises on failure, having rolled
    the whole minute back."""
    conn = _write_conn(date_iso, root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            ts = int(minute.ts)
            conn.execute("DELETE FROM chains WHERE ts = ?", (ts,))
            rows = []
            for seq, (symbol, chain) in enumerate(minute.chains):
                carried = symbol in minute.carried
                rows.append((ts, symbol, "chain", int(carried),
                             minute.carried_age.get(symbol) if carried else None,
                             seq, _pack(chain)))
            for seq, (symbol, chain) in enumerate(minute.stored.items()):
                rows.append((ts, symbol, "stored", 1, None, seq, _pack(chain)))
            if minute.term is not None:
                rows.append((ts, _TERM_KEY, "term", 0, None, 0, _pack(minute.term)))
            conn.executemany(
                "INSERT INTO chains (ts, symbol, kind, carried, carried_age_sec, seq, body)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            _insert_minute_row(conn, minute)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def read_minute(date_iso: str, ts: int, root=None) -> RawMinute | None:
    """The complete minute ``ts`` of the session, or None when it is not complete."""
    conn = _read_conn(date_iso, root)
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT symbols FROM minutes WHERE ts = ?", (int(ts),)).fetchone()
        if row is None:
            return None
        minute = RawMinute(ts=int(ts), symbols=json.loads(row[0]))
        for symbol, kind, carried, age, body in conn.execute(
                "SELECT symbol, kind, carried, carried_age_sec, body FROM chains"
                " WHERE ts = ? ORDER BY kind, seq", (int(ts),)):
            chain = _unpack(body)
            if kind == "chain":
                minute.chains.append((symbol, chain))
                if carried:
                    minute.carried.add(symbol)
                    minute.carried_age[symbol] = age
            elif kind == "stored":
                minute.stored[symbol] = chain
            elif kind == "term":
                minute.term = chain
        return minute
    finally:
        conn.close()


def complete_minutes(date_iso: str, after_ts: int = -1, root=None) -> list:
    """The session's complete minutes after ``after_ts``, oldest first."""
    conn = _read_conn(date_iso, root)
    if conn is None:
        return []
    try:
        return [r[0] for r in conn.execute(
            "SELECT ts FROM minutes WHERE ts > ? ORDER BY ts", (int(after_ts),))]
    finally:
        conn.close()


def last_complete(date_iso: str, root=None) -> int | None:
    """The session's newest complete minute, or None."""
    conn = _read_conn(date_iso, root)
    if conn is None:
        return None
    try:
        return conn.execute("SELECT MAX(ts) FROM minutes").fetchone()[0]
    finally:
        conn.close()


def write_gap(date_iso: str, ts: int, reason: str, root=None) -> None:
    """Record minute ``ts`` as not collected, and why."""
    conn = _write_conn(date_iso, root)
    try:
        conn.execute("INSERT OR REPLACE INTO gaps (ts, reason, noted_at) VALUES (?, ?, ?)",
                     (int(ts), str(reason), time.time()))
    finally:
        conn.close()


def gaps(date_iso: str, root=None) -> list:
    """``[(ts, reason), ...]`` for the session, oldest first."""
    conn = _read_conn(date_iso, root)
    if conn is None:
        return []
    try:
        return [(r[0], r[1]) for r in conn.execute("SELECT ts, reason FROM gaps ORDER BY ts")]
    finally:
        conn.close()


def sessions(root=None) -> list:
    """Every stored session's date, oldest first."""
    folder = _root(root)
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.db") if _DATE.match(p.stem))


def prune(keep_sessions: int, root=None) -> list:
    """Delete every session but the newest ``keep_sessions`` (with its WAL and
    shared-memory files). Returns the dates deleted, oldest first."""
    keep = max(1, int(keep_sessions))
    folder = _root(root)
    gone = sessions(root)[:-keep]
    for date_iso in gone:
        for suffix in ("", "-wal", "-shm"):
            path = folder / f"{date_iso}.db{suffix}"
            if path.exists():
                path.unlink()
    return gone
