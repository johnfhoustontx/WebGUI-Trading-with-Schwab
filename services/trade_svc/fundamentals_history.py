"""Point-in-time fundamentals — what each field read on the day it was read.

The Investor verdict's six weights have never been validated against forward
returns, and the 2026-08-22 audit had to mark that **impossible**: the only
fundamentals available are live-parsed and describe TODAY, so scoring history
with them leaks today's data into the past. There is no free fix — the source
serves no history — so the only honest path is to start remembering.

**This store keeps the INPUTS, not the score.** That is the entire point: a
score is recomputable from stored inputs under new weights, while inputs cannot
be recovered from a stored score. It also stores ``sector_pe_median`` alongside,
because the valuation component is relative and the peer median moves too.

Like the recommendation journal it is forward-accruing and cheap, so it lands in
Phase 1 rather than beside its Phase 6 readers. Same contract as its siblings:
idempotent schema, ``sqlite3.Row`` factory, one row per (symbol, day), and a
write path that never raises into the caller.
"""
import datetime as dt
import logging
import sqlite3
from pathlib import Path

from repo_paths import FUNDAMENTALS_HISTORY_DB

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = FUNDAMENTALS_HISTORY_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    symbol                  TEXT NOT NULL,
    snapshot_date           TEXT NOT NULL,
    recorded_at             TEXT,
    pe_ratio                REAL,
    peg_ratio               REAL,
    rev_growth_ttm          REAL,
    eps_growth_ttm          REAL,
    roe                     REAL,
    margin_expanding        INTEGER,
    fcf                     REAL,
    short_int_to_float      REAL,
    short_int_day_to_cover  REAL,
    days_to_earnings        INTEGER,
    sector                  TEXT,
    sector_pe_median        REAL,
    PRIMARY KEY (symbol, snapshot_date)
);
"""

_FIELDS = ("symbol", "snapshot_date", "recorded_at", "pe_ratio", "peg_ratio",
           "rev_growth_ttm", "eps_growth_ttm", "roe", "margin_expanding",
           "fcf", "short_int_to_float", "short_int_day_to_cover",
           "days_to_earnings", "sector", "sector_pe_median")


def init_db(db_path=DEFAULT_DB_PATH):
    """Open the store, creating the schema if needed. Idempotent."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def close_db(conn):
    try:
        conn.close()
    except Exception:
        pass


def _as_int_or_none(v):
    """True/False -> 1/0, None -> None.

    ``margin_expanding`` is genuinely three-valued: None means the pair of
    margins needed to decide was absent, which is NOT the same as "not
    expanding". Collapsing it to 0 would invent a bearish reading out of
    missing data — the exact class of bug the audit was about."""
    return None if v is None else int(bool(v))


def record(conn, snapshot):
    """Upsert one snapshot. True on success, False on ANY failure. Never raises."""
    try:
        row = {k: snapshot.get(k) for k in _FIELDS}
        if not row.get("symbol") or not row.get("snapshot_date"):
            return False
        row["recorded_at"] = row.get("recorded_at") or _now_iso()
        row["margin_expanding"] = _as_int_or_none(row.get("margin_expanding"))
        cols = ", ".join(_FIELDS)
        marks = ", ".join(f":{k}" for k in _FIELDS)
        updates = ", ".join(f"{k}=excluded.{k}" for k in _FIELDS
                            if k not in ("symbol", "snapshot_date"))
        conn.execute(
            f"INSERT INTO snapshots ({cols}) VALUES ({marks}) "
            f"ON CONFLICT(symbol, snapshot_date) DO UPDATE SET {updates}", row)
        conn.commit()
        return True
    except Exception:
        logger.debug("fundamentals_history.record failed", exc_info=True)
        return False


def snapshots(conn, limit=None):
    """All snapshots, newest first."""
    sql = "SELECT * FROM snapshots ORDER BY snapshot_date DESC, symbol ASC"
    args = []
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    return conn.execute(sql, args).fetchall()


def history(conn, symbol):
    """One symbol's series, OLDEST first — a point-in-time series is only ever
    read forward in time."""
    return conn.execute(
        "SELECT * FROM snapshots WHERE symbol = ? ORDER BY snapshot_date ASC",
        (symbol,)).fetchall()


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()
