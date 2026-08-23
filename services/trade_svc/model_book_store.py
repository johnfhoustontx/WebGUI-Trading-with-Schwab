"""Storage for the model paper book. Isolated, like the driver's own book.

One row per position. ``(symbol, opened_on)`` is the key, so the same name
re-entering on a later day is a new position while a repeated tick on the same
day is not — the tick runs more than once a day and must be idempotent.

Mirrors ``rec_journal``'s contract: idempotent schema, ``sqlite3.Row`` so callers
read by name, and writes that never raise into the caller. A paper book failing
must not cost anyone their analysis.
"""
import datetime as dt
import logging
import sqlite3
from pathlib import Path

from repo_paths import MODEL_BOOK_DB

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = MODEL_BOOK_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    symbol        TEXT NOT NULL,
    opened_on     TEXT NOT NULL,
    side          TEXT,
    expression    TEXT,
    entry         REAL,
    spy_entry     REAL,
    composite     REAL,
    decile        INTEGER,
    stop          REAL,
    target        REAL,
    time_stop_on  TEXT,
    status        TEXT DEFAULT 'open',
    last          REAL,
    pnl_pct       REAL,
    closed_on     TEXT,
    close_reason  TEXT,
    PRIMARY KEY (symbol, opened_on)
);
CREATE INDEX IF NOT EXISTS idx_positions_open ON positions (status, symbol);
"""

_FIELDS = ("symbol", "opened_on", "side", "expression", "entry", "spy_entry",
           "composite", "decile", "stop", "target", "time_stop_on", "status")


def init_db(db_path=DEFAULT_DB_PATH):
    db_path = Path(db_path)
    if str(db_path) != ":memory:":
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


def open_position(conn, position):
    """Insert one position. A repeat for the same (symbol, day) is IGNORED.

    Not upserted: re-opening would move the entry price to whatever the latest
    tick saw, silently improving every position that had moved against it."""
    try:
        row = {k: (position or {}).get(k) for k in _FIELDS}
        if not row.get("symbol") or not row.get("opened_on"):
            return False
        row["status"] = row.get("status") or "open"
        cols = ", ".join(_FIELDS)
        marks = ", ".join(f":{k}" for k in _FIELDS)
        conn.execute(
            f"INSERT OR IGNORE INTO positions ({cols}) VALUES ({marks})", row)
        conn.commit()
        return True
    except Exception:
        logger.debug("model_book_store.open_position failed", exc_info=True)
        return False


def update_mark(conn, symbol, opened_on, last, pnl_pct):
    try:
        conn.execute(
            "UPDATE positions SET last=?, pnl_pct=? "
            "WHERE symbol=? AND opened_on=? AND status='open'",
            (last, pnl_pct, symbol, opened_on))
        conn.commit()
        return True
    except Exception:
        return False


def close_position(conn, symbol, opened_on, last, pnl_pct, reason, on=None):
    try:
        conn.execute(
            "UPDATE positions SET status='closed', last=?, pnl_pct=?, "
            "close_reason=?, closed_on=? WHERE symbol=? AND opened_on=?",
            (last, pnl_pct, reason,
             (on or dt.date.today()).isoformat(), symbol, opened_on))
        conn.commit()
        return True
    except Exception:
        return False


def positions(conn, status=None, limit=None):
    sql = "SELECT * FROM positions"
    args = []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    sql += " ORDER BY opened_on DESC, symbol ASC"
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    return conn.execute(sql, args).fetchall()


def open_symbols(conn):
    """Symbols already held, so a tick does not re-enter one it is holding."""
    try:
        return {r["symbol"] for r in
                conn.execute("SELECT symbol FROM positions WHERE status='open'")}
    except Exception:
        return set()
