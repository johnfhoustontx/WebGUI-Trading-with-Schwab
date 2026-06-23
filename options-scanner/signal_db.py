"""SQLite schema + low-level helpers for the signal tracking database."""
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "signals.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    scanner_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    short_strike REAL,
    long_strike REAL,
    call_short REAL,
    call_long REAL,
    width REAL,
    expiration TEXT,
    dte_at_entry INTEGER,
    entry_credit REAL,
    entry_max_loss REAL,
    entry_score INTEGER,
    entry_grade TEXT,
    entry_short_delta REAL,
    entry_net_theta REAL,
    entry_net_delta_position REAL,
    entry_net_theta_position REAL,
    entry_spread_bid REAL,
    entry_spread_ask REAL,
    entry_iv_rank REAL,
    entry_underlying REAL,
    first_seen_ts TEXT,
    first_seen_date TEXT,
    dedup_key TEXT UNIQUE,
    status TEXT DEFAULT 'OPEN',
    mode TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_first_seen_date ON signals(first_seen_date);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_scanner_type ON signals(scanner_type);
-- Hot query path: get_open_signals() filters WHERE status='OPEN' AND scanner_type=?
CREATE INDEX IF NOT EXISTS idx_signals_status_type ON signals(status, scanner_type);

CREATE TABLE IF NOT EXISTS signal_marks (
    mark_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL,
    mark_ts TEXT,
    mark_date TEXT,
    current_value REAL,
    unrealized_pnl REAL,
    pnl_pct_of_credit REAL,
    current_underlying REAL,
    current_short_delta REAL,
    current_score INTEGER,
    score_drift INTEGER,
    recommendation TEXT,
    recommendation_reason TEXT,
    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
);
CREATE INDEX IF NOT EXISTS idx_marks_date ON signal_marks(mark_date);
CREATE INDEX IF NOT EXISTS idx_marks_signal ON signal_marks(signal_id);

CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id TEXT PRIMARY KEY,
    close_ts TEXT,
    close_date TEXT,
    exit_value REAL,
    realized_pnl REAL,
    exit_reason TEXT,
    settlement_underlying REAL,
    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
);
CREATE INDEX IF NOT EXISTS idx_outcomes_date ON signal_outcomes(close_date);
"""


def _add_column_if_missing(conn, column_name, column_type):
    """Idempotent: add column if it doesn't already exist."""
    try:
        conn.execute(f"ALTER TABLE signals ADD COLUMN {column_name} {column_type}")
    except sqlite3.OperationalError:
        pass  # column exists already


def init_db(db_path=DEFAULT_DB_PATH):
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        # WAL allows concurrent readers alongside a writer.
        conn.execute("PRAGMA journal_mode=WAL")
        # If a legacy `signals` table already exists, backfill any missing
        # columns BEFORE executescript runs (because the SCHEMA also defines
        # indices that reference columns the legacy table may be missing).
        has_signals = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signals'"
        ).fetchone()
        if has_signals:
            _migrate_signals_table(conn)
        conn.executescript(SCHEMA)
        # Idempotent migration for fields added 2026-05-27 (runs again for
        # freshly-created DBs too — _add_column_if_missing is a no-op there).
        _migrate_signals_table(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_signals_table(conn):
    """Idempotent ALTER TABLE migrations for the signals table."""
    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()
    }
    # Schema-2026-05-27: position-perspective + spread bid/ask
    for col in ("entry_net_delta_position", "entry_net_theta_position",
                "entry_spread_bid", "entry_spread_ask"):
        if col not in existing_cols:
            _add_column_if_missing(conn, col, "REAL")
    # Older legacy DBs may also be missing these — keep executescript happy.
    legacy_backfill = {
        "scanner_type": "TEXT",
        "short_strike": "REAL",
        "long_strike": "REAL",
        "call_short": "REAL",
        "call_long": "REAL",
        "width": "REAL",
        "expiration": "TEXT",
        "dte_at_entry": "INTEGER",
        "entry_credit": "REAL",
        "entry_max_loss": "REAL",
        "entry_score": "INTEGER",
        "entry_grade": "TEXT",
        "entry_iv_rank": "REAL",
        "entry_underlying": "REAL",
        "first_seen_ts": "TEXT",
        "first_seen_date": "TEXT",
        "dedup_key": "TEXT",
        "status": "TEXT",
        "mode": "TEXT",   # NEW: PREMIUM vs DIRECTIONAL tag
    }
    for col, coltype in legacy_backfill.items():
        if col not in existing_cols:
            _add_column_if_missing(conn, col, coltype)


# Alias matching the project-wide `init_schema` convention.
init_schema = init_db


_initialised: set = set()


def connect(db_path=DEFAULT_DB_PATH):
    db_path = Path(db_path).resolve()
    if db_path not in _initialised:
        init_db(db_path)
        _initialised.add(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


INSERT_SIGNAL_SQL = """
INSERT OR IGNORE INTO signals (
    signal_id, scanner_type, symbol, strategy, short_strike, long_strike,
    call_short, call_long, width, expiration, dte_at_entry,
    entry_credit, entry_max_loss, entry_score, entry_grade,
    entry_short_delta, entry_net_theta,
    entry_net_delta_position, entry_net_theta_position,
    entry_spread_bid, entry_spread_ask,
    entry_iv_rank, entry_underlying,
    first_seen_ts, first_seen_date, dedup_key, status, mode
) VALUES (
    :signal_id, :scanner_type, :symbol, :strategy, :short_strike, :long_strike,
    :call_short, :call_long, :width, :expiration, :dte_at_entry,
    :entry_credit, :entry_max_loss, :entry_score, :entry_grade,
    :entry_short_delta, :entry_net_theta,
    :entry_net_delta_position, :entry_net_theta_position,
    :entry_spread_bid, :entry_spread_ask,
    :entry_iv_rank, :entry_underlying,
    :first_seen_ts, :first_seen_date, :dedup_key, :status, :mode
)
"""


_INSERT_DEFAULTS = {
    "entry_net_delta_position": 0.0,
    "entry_net_theta_position": 0.0,
    "entry_spread_bid": 0.0,
    "entry_spread_ask": 0.0,
    "mode": None,   # NEW: NULL by default; readers treat NULL as PREMIUM
}


def insert_signal(row, db_path=DEFAULT_DB_PATH):
    """Insert a signal; returns True if a row was inserted, False if deduped.

    Missing position-perspective fields (added 2026-05-27) default to 0.0 so
    legacy callers/tests that predate them continue to work.
    """
    # Backfill defaults for newly-added columns to keep old callers working.
    for k, v in _INSERT_DEFAULTS.items():
        row.setdefault(k, v)
    conn = connect(db_path)
    try:
        cur = conn.execute(INSERT_SIGNAL_SQL, row)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_signal(signal_id, db_path=DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        cur = conn.execute("SELECT * FROM signals WHERE signal_id = ?", (signal_id,))
        r = cur.fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def get_open_signals(scanner_type=None, db_path=DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        if scanner_type is not None:
            cur = conn.execute(
                "SELECT * FROM signals WHERE status='OPEN' AND scanner_type=?",
                (scanner_type,),
            )
        else:
            cur = conn.execute("SELECT * FROM signals WHERE status='OPEN'")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def insert_mark(mark_row, db_path=DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        valid = {row[1] for row in conn.execute("PRAGMA table_info(signal_marks)")}
        # Build a filtered copy so callers can pass derived-but-not-persisted
        # fields (e.g. recommendation_code) without mutating their dict or
        # breaking the dynamic INSERT.
        row = {k: v for k, v in mark_row.items() if k in valid}
        cols = ",".join(row.keys())
        placeholders = ",".join(":" + k for k in row.keys())
        conn.execute(
            f"INSERT INTO signal_marks ({cols}) VALUES ({placeholders})",
            row,
        )
        conn.commit()
    finally:
        conn.close()


def insert_outcome(outcome_row, new_status, db_path=DEFAULT_DB_PATH):
    """Insert an outcome and update the parent signal's status."""
    conn = connect(db_path)
    try:
        cols = ",".join(outcome_row.keys())
        placeholders = ",".join(":" + k for k in outcome_row.keys())
        conn.execute(
            f"INSERT OR REPLACE INTO signal_outcomes ({cols}) VALUES ({placeholders})",
            outcome_row,
        )
        cur = conn.execute(
            "UPDATE signals SET status = ? WHERE signal_id = ?",
            (new_status, outcome_row["signal_id"]),
        )
        if cur.rowcount != 1:
            conn.rollback()
            raise ValueError(
                f"insert_outcome: signal_id {outcome_row['signal_id']!r} not found"
            )
        conn.commit()
    finally:
        conn.close()


def get_open_signals_with_latest_mark(db_path=DEFAULT_DB_PATH):
    """OPEN signals left-joined to their newest signal_marks row.

    Mark fields (unrealized_pnl, current_score, score_drift, recommendation,
    recommendation_reason, last_mark_ts) are None when no mark exists yet.
    Sorted by first_seen_ts DESC (newest captured first).
    """
    conn = connect(db_path)
    try:
        cur = conn.execute("""
            SELECT s.*,
                   m.current_value        AS current_value,
                   m.unrealized_pnl       AS unrealized_pnl,
                   m.current_score        AS current_score,
                   m.score_drift          AS score_drift,
                   m.recommendation       AS recommendation,
                   m.recommendation_reason AS recommendation_reason,
                   m.current_underlying   AS current_underlying,
                   m.mark_ts              AS last_mark_ts
            FROM signals s
            LEFT JOIN signal_marks m ON m.mark_id = (
                SELECT mark_id FROM signal_marks
                WHERE signal_id = s.signal_id
                ORDER BY mark_ts DESC LIMIT 1
            )
            WHERE s.status = 'OPEN'
            ORDER BY s.first_seen_ts DESC
        """)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def close_signal_manually(signal_id, exit_value, exit_reason, db_path=DEFAULT_DB_PATH,
                          close_ts=None):
    """Manually close an OPEN signal. Writes signal_outcomes row, flips status to CLOSED.

    realized_pnl = (entry_credit - exit_value) * 100  (per-contract dollars).
    Raises ValueError if signal_id is unknown.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = close_ts or datetime.now(ZoneInfo("America/Chicago"))
    sig = get_signal(signal_id, db_path=db_path)
    if sig is None:
        raise ValueError(f"close_signal_manually: signal_id {signal_id!r} not found")
    entry_credit = sig.get("entry_credit")
    if entry_credit is None:
        raise ValueError(
            f"close_signal_manually: signal {signal_id!r} has no entry_credit; "
            "cannot compute realized_pnl"
        )
    realized_pnl = (float(entry_credit) - float(exit_value)) * 100.0
    outcome = {
        "signal_id": signal_id,
        "close_ts": now.isoformat() if hasattr(now, "isoformat") else str(now),
        "close_date": (now.date().isoformat()
                       if hasattr(now, "date") else str(now)[:10]),
        "exit_value": float(exit_value),
        "realized_pnl": realized_pnl,
        "exit_reason": exit_reason,
        "settlement_underlying": None,
    }
    insert_outcome(outcome, new_status="CLOSED", db_path=db_path)
