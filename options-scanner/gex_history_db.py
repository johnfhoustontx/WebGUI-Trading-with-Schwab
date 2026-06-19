"""SQLite persistence for intraday GEX/Charm snapshots."""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Tuple

DB_PATH = Path(__file__).parent / "gex_history.db"


def _today_local_unix_range() -> tuple[int, int]:
    """``[start, end)`` unix seconds spanning the current LOCAL calendar day.

    Lets today-filters use a sargable ``ts >= ? AND ts < ?`` range (the index on
    ``ts`` applies) instead of ``DATE(ts,'unixepoch','localtime') = DATE('now')``,
    which wraps ``ts`` in a function and so can't use the index.

    NOTE: uses the current fixed local UTC offset; on the two DST-transition days
    the [start,end) edge can differ by an hour from SQLite's DST-aware
    ``DATE(...,'localtime')``. Immaterial here — GEX snapshots only exist 08:30–
    15:20 CT, never near the 02:00/midnight boundary, so no row is ever classified
    differently."""
    now = _dt.datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = (start + _dt.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()), int(tomorrow.timestamp())

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    symbol          TEXT    NOT NULL,
    view            TEXT    NOT NULL,
    ts              INTEGER NOT NULL,
    spot            REAL,
    flip            REAL,
    top_pos_strike  REAL,
    top_neg_strike  REAL,
    net_total       REAL,
    dte             INTEGER,
    gex_json        TEXT,
    net_delta_0dte            REAL,
    projected_net_delta_close REAL,
    hedge_pressure            REAL,
    PRIMARY KEY (symbol, view, ts)
);
CREATE INDEX IF NOT EXISTS idx_snap_today
    ON snapshots(symbol, view, ts);
"""


TERM_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS gex_term_snapshots (
    timestamp_ct      TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    expiration_date   TEXT    NOT NULL,
    strike            REAL    NOT NULL,
    call_gex_usd      REAL    NOT NULL,
    put_gex_usd       REAL    NOT NULL,
    net_gex_usd       REAL    NOT NULL,
    underlying_price  REAL    NOT NULL,
    PRIMARY KEY (timestamp_ct, symbol, expiration_date, strike)
);
-- PK already covers (timestamp_ct, ...) lookups; only the date-prefix
-- index is needed. The substr() expression must match the WHERE clause
-- in list_term_timestamps_for_date for SQLite to use this index.
CREATE INDEX IF NOT EXISTS idx_term_date ON gex_term_snapshots (substr(timestamp_ct, 1, 10));
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation."""
    conn.executescript(_SCHEMA)
    # Backfill columns on pre-existing DBs (SQLite lacks IF NOT EXISTS for columns).
    existing = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
    for col in ("net_delta_0dte", "projected_net_delta_close", "hedge_pressure"):
        if col not in existing:
            conn.execute(f"ALTER TABLE snapshots ADD COLUMN {col} REAL")
    conn.commit()
    init_term_schema(conn)


def init_term_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation for the term-structure snapshots table."""
    conn.executescript(TERM_SCHEMA_SQL)
    conn.commit()


def insert_term_snapshot_rows(
    conn: sqlite3.Connection,
    rows: Iterable[Tuple[str, str, str, float, float, float, float, float]],
) -> None:
    """rows: iterable of 8-tuples
        (timestamp_ct, symbol, expiration_date, strike,
         call_gex_usd, put_gex_usd, net_gex_usd, underlying_price)."""
    conn.executemany(
        "INSERT OR REPLACE INTO gex_term_snapshots "
        "(timestamp_ct, symbol, expiration_date, strike, call_gex_usd, "
        " put_gex_usd, net_gex_usd, underlying_price) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def load_term_snapshot(
    conn: sqlite3.Connection,
    timestamp_ct: str,
    symbol: str,
) -> list[dict]:
    """Return rows for a given (timestamp_ct, symbol) as dicts ordered by
    expiration_date then strike."""
    cur = conn.execute(
        "SELECT expiration_date, strike, call_gex_usd, put_gex_usd, "
        "       net_gex_usd, underlying_price "
        "FROM gex_term_snapshots "
        "WHERE timestamp_ct = ? AND symbol = ? "
        "ORDER BY expiration_date, strike",
        (timestamp_ct, symbol),
    )
    return [
        {"expiration_date": e, "strike": s, "call_gex_usd": c,
         "put_gex_usd": p, "net_gex_usd": n, "underlying_price": u}
        for (e, s, c, p, n, u) in cur.fetchall()
    ]


def list_term_timestamps_for_date(
    conn: sqlite3.Connection,
    date_str: str,
    symbol: str,
) -> list[str]:
    """Return distinct timestamp_ct values for the given local date prefix
    (YYYY-MM-DD) and symbol, ordered ascending."""
    cur = conn.execute(
        "SELECT DISTINCT timestamp_ct FROM gex_term_snapshots "
        "WHERE substr(timestamp_ct, 1, 10) = ? AND symbol = ? "
        "ORDER BY timestamp_ct",
        (date_str, symbol),
    )
    return [r[0] for r in cur.fetchall()]


def connect(read_only: bool = False) -> sqlite3.Connection:
    """Open the snapshots DB. read_only=True uses SQLite URI mode=ro."""
    if read_only:
        uri = f"file:{DB_PATH.as_posix()}?mode=ro"
        return sqlite3.connect(uri, uri=True, isolation_level=None)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def insert_snapshot(
    conn: sqlite3.Connection,
    symbol: str,
    view: str,
    summary: dict,
    gex_grid: dict,
    dte: int,
) -> None:
    """Write one snapshot row. INSERT OR REPLACE keeps re-runs idempotent."""
    conn.execute(
        """
        INSERT OR REPLACE INTO snapshots
            (symbol, view, ts, spot, flip, top_pos_strike,
             top_neg_strike, net_total, dte, gex_json,
             net_delta_0dte, projected_net_delta_close, hedge_pressure)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            view,
            int(summary.get("ts") or time.time()),
            summary.get("spot"),
            summary.get("flip"),
            summary.get("top_pos_strike"),
            summary.get("top_neg_strike"),
            summary.get("net_total"),
            dte,
            json.dumps(gex_grid) if gex_grid else None,
            summary.get("net_delta_0dte"),
            summary.get("projected_net_delta_close"),
            summary.get("hedge_pressure"),
        ),
    )


def load_today(
    conn: sqlite3.Connection,
    symbol: str,
    view: str,
) -> list[tuple]:
    """Return today's snapshots for (symbol, view), ordered by ts ascending.

    Rows: (ts, spot, flip, top_pos_strike, top_neg_strike, net_total).
    """
    start, end = _today_local_unix_range()
    cur = conn.execute(
        """
        SELECT ts, spot, flip, top_pos_strike, top_neg_strike, net_total
          FROM snapshots
         WHERE symbol = ?
           AND view   = ?
           AND ts >= ? AND ts < ?
         ORDER BY ts ASC
        """,
        (symbol, view, start, end),
    )
    return cur.fetchall()


def load_today_with_grid(
    conn: sqlite3.Connection,
    symbol: str,
    view: str,
) -> list[tuple]:
    """Like load_today but also includes the per-strike gex grid dict.

    Returns list of (ts, spot, flip, top_pos_strike, top_neg_strike,
                     net_total, gex_grid) tuples. gex_grid is the decoded
    JSON dict or {} if NULL.
    """
    start, end = _today_local_unix_range()
    cur = conn.execute(
        """
        SELECT ts, spot, flip, top_pos_strike, top_neg_strike, net_total, gex_json
          FROM snapshots
         WHERE symbol = ?
           AND view   = ?
           AND ts >= ? AND ts < ?
         ORDER BY ts ASC
        """,
        (symbol, view, start, end),
    )
    out = []
    for row in cur.fetchall():
        if row[6]:
            # JSON stringifies dict keys — cast back to float so downstream
            # numeric grouping (GammaEngine.group_gex) works.
            raw = json.loads(row[6])
            grid = {float(k): v for k, v in raw.items()}
        else:
            grid = {}
        out.append((*row[:6], grid))
    return out


def purge_old(conn: sqlite3.Connection) -> int:
    """Delete snapshots older than today (local date). Returns rows deleted."""
    cur = conn.execute(
        """
        DELETE FROM snapshots
         WHERE DATE(ts, 'unixepoch', 'localtime') < DATE('now', 'localtime')
        """
    )
    conn.commit()
    return cur.rowcount


def last_snapshot_age(
    conn: sqlite3.Connection,
    symbol: str,
    view: str,
) -> tuple[int | None, int | None]:
    """Return (age_seconds, last_ts) for today's latest snapshot, or (None, None)."""
    cur = conn.execute(
        """
        SELECT MAX(ts) FROM snapshots
         WHERE symbol = ? AND view = ?
           AND DATE(ts, 'unixepoch', 'localtime') = DATE('now', 'localtime')
        """,
        (symbol, view),
    )
    row = cur.fetchone()
    if not row or row[0] is None:
        return None, None
    last_ts = int(row[0])
    return int(time.time()) - last_ts, last_ts


def first_snapshot_today(
    conn: sqlite3.Connection,
    symbol: str,
    view: str,
) -> dict:
    """Return the gex_grid dict of today's earliest snapshot, or {} if none.

    Used as the "vs Open" baseline for ΔDEX ghost bars.
    """
    cur = conn.execute(
        """
        SELECT gex_json FROM snapshots
         WHERE symbol = ? AND view = ?
           AND DATE(ts, 'unixepoch', 'localtime') = DATE('now', 'localtime')
         ORDER BY ts ASC
         LIMIT 1
        """,
        (symbol, view),
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return {}
    raw = json.loads(row[0])
    return {float(k): v for k, v in raw.items()}
