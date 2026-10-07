"""SQLite schema + low-level helpers for the signal tracking database."""
import sqlite3
from pathlib import Path

from shared import structures as _structures

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "signals.db"

# Scanner types that are TRACKED, not traded: the Market Scanner's structures
# that are not credit spreads (straddles, butterflies, calendars, backspreads,
# the Directional tab's single legs), recorded so their outcomes can be
# measured. They share these tables and their shape is different - a ``legs_json``
# blob, a SIGNED ``entry_credit`` (a debit is negative), no strike columns.
#
# ⚠ Every reader below EXCLUDES them unless the caller passes ``tracked=True``.
# More than a dozen readers of this store were written for a credit spread - the
# Captured Signals page and its score, the paper Account's entry feed, the phone
# push, Rescue, the manage cycle - and a default that hid nothing would hand
# each of them a row it would misread. ``tracked=True`` returns ONLY these rows.
TRACKED_TYPES = _structures.TRACKED_SCANNER_TYPES


def is_tracked(row) -> bool:
    """Is this ``signals`` row one of the tracked structures?"""
    kind = (row or {}).get("scanner_type")
    return isinstance(kind, str) and kind.strip().upper() in TRACKED_TYPES


def _tracked_list():
    return ",".join(f"'{t}'" for t in TRACKED_TYPES)


def _not_tracked_sql(prefix=""):
    """SQL for "not a tracked row" (``shared.structures.not_tracked_sql``)."""
    return _structures.not_tracked_sql(prefix)


def _tracked_sql(prefix="", tracked=False):
    """The WHERE clause for one side of the split."""
    if tracked:
        return f"{prefix}scanner_type IN ({_tracked_list()})"
    return _not_tracked_sql(prefix)

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
    mode TEXT,
    be_armed INTEGER DEFAULT 0,
    legs_json TEXT,
    family TEXT,
    entry_max_profit REAL,
    entry_capital REAL,
    unbounded INTEGER DEFAULT 0,
    entry_spans_earnings INTEGER DEFAULT 0
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
-- The latest-mark lookup (WHERE signal_id = ? ORDER BY mark_ts DESC LIMIT 1)
-- runs once per open signal on every read of the board. On (signal_id) alone it
-- fetched every mark the signal has and sorted them: 280 ms on 640,000 rows
-- against 0.1 ms with this (audit PF-08). It also serves every plain
-- signal_id lookup, which is why the single-column index is dropped below.
CREATE INDEX IF NOT EXISTS idx_marks_signal_ts ON signal_marks(signal_id, mark_ts);
DROP INDEX IF EXISTS idx_marks_signal;

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
    # captured-autoclose (2026-08-09): break-even armed flag — set once when the
    # trade first reaches +50% credit; drives the raised break-even stop.
    if "be_armed" not in existing_cols:
        _add_column_if_missing(conn, "be_armed", "INTEGER DEFAULT 0")
    # Tracked structures (2026-10-07) - see TRACKED_TYPES. All NULL / 0 on every
    # row written before them and on every credit-spread row since.
    #   legs_json             the legs: kind, side, strike, expiration, qty
    #   family                the build family (VERTICAL, STRADDLE, ...)
    #   entry_max_profit      per share; NULL = unbounded
    #   entry_capital         per share; the risk denominator when unbounded
    #   unbounded             1 = the LOSS is unbounded (a short straddle)
    #   entry_spans_earnings  1 = opened through an earnings report
    for col, coltype in (("legs_json", "TEXT"), ("family", "TEXT"),
                         ("entry_max_profit", "REAL"), ("entry_capital", "REAL"),
                         ("unbounded", "INTEGER DEFAULT 0"),
                         ("entry_spans_earnings", "INTEGER DEFAULT 0")):
        if col not in existing_cols:
            _add_column_if_missing(conn, col, coltype)
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
    first_seen_ts, first_seen_date, dedup_key, status, mode,
    legs_json, family, entry_max_profit, entry_capital, unbounded,
    entry_spans_earnings
) VALUES (
    :signal_id, :scanner_type, :symbol, :strategy, :short_strike, :long_strike,
    :call_short, :call_long, :width, :expiration, :dte_at_entry,
    :entry_credit, :entry_max_loss, :entry_score, :entry_grade,
    :entry_short_delta, :entry_net_theta,
    :entry_net_delta_position, :entry_net_theta_position,
    :entry_spread_bid, :entry_spread_ask,
    :entry_iv_rank, :entry_underlying,
    :first_seen_ts, :first_seen_date, :dedup_key, :status, :mode,
    :legs_json, :family, :entry_max_profit, :entry_capital, :unbounded,
    :entry_spans_earnings
)
"""


_INSERT_DEFAULTS = {
    "entry_net_delta_position": 0.0,
    "entry_net_theta_position": 0.0,
    "entry_spread_bid": 0.0,
    "entry_spread_ask": 0.0,
    "mode": None,   # NEW: NULL by default; readers treat NULL as PREMIUM
    # The tracked-structure columns: absent on every credit-spread row.
    "legs_json": None,
    "family": None,
    "entry_max_profit": None,
    "entry_capital": None,
    "unbounded": 0,
    "entry_spans_earnings": 0,
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


def get_open_signals(scanner_type=None, db_path=DEFAULT_DB_PATH, *, tracked=False):
    """OPEN signals. A named ``scanner_type`` returns exactly that type; with
    none, the tracked structures are excluded (``tracked=True``: only them)."""
    conn = connect(db_path)
    try:
        if scanner_type is not None:
            cur = conn.execute(
                "SELECT * FROM signals WHERE status='OPEN' AND scanner_type=?",
                (scanner_type,),
            )
        else:
            cur = conn.execute("SELECT * FROM signals WHERE status='OPEN' AND "
                               + _tracked_sql(tracked=tracked))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def count_open_by_symbol(db_path=DEFAULT_DB_PATH, *, tracked=False):
    """``{symbol: n}`` of OPEN signals across every TRADED scanner type — the book
    the recorder's per-symbol capture cap counts against. The tracked structures
    are their own pool (``tracked=True``), so one of them can never take a slot a
    credit spread, which the paper Account can trade, would need."""
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "SELECT symbol, COUNT(*) FROM signals WHERE status='OPEN' AND "
            + _tracked_sql(tracked=tracked) + " GROUP BY symbol")
        return {sym: n for sym, n in cur.fetchall()}
    finally:
        conn.close()


def peak_unrealized(signal_id, db_path=DEFAULT_DB_PATH):
    """The best unrealized P&L this signal has ever marked, or ``None``.

    The peak the profit-lock ladder needs (gap assessment C2) - one
    ``MAX(unrealized_pnl)`` over the marks the manage cycle is already writing,
    so it costs a single indexed aggregate per open signal per cycle.

    ⚠ **``None``, not 0.0, for a signal with no marks.** A 0 peak clears the
    ladder's first rung (whose lock is 0.0) and would therefore look like "armed
    at break-even" on a position that has never been marked - and the moment a
    ladder locks a positive fraction at a low peak, a phantom 0 becomes a phantom
    stop. ``_locked_profit_level`` treats ``None`` as "no lock", which is exactly
    today's plain break-even behaviour.

    Never raises: it runs inside a manage cycle, and a failed read must not cost
    the cycle its exits.
    """
    try:
        conn = connect(db_path)
    except Exception:
        return None
    try:
        row = conn.execute(
            "SELECT MAX(unrealized_pnl) AS peak FROM signal_marks "
            "WHERE signal_id = ? AND unrealized_pnl IS NOT NULL",
            (signal_id,)).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    peak = row["peak"] if not isinstance(row, tuple) else row[0]
    return float(peak) if isinstance(peak, (int, float)) else None


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


def insert_marks(mark_rows, db_path=DEFAULT_DB_PATH):
    """Write a batch of marks in ONE transaction; returns how many.

    A manage cycle marks every open signal. Writing them one call at a time was
    a connection, a ``PRAGMA table_info`` and a commit (an fsync) per signal.
    All or nothing: a row that cannot be written rolls the batch back and
    raises. Unknown keys are dropped per row, as ``insert_mark`` does. An empty
    batch opens no connection."""
    rows = list(mark_rows or [])
    if not rows:
        return 0
    conn = connect(db_path)
    try:
        valid = {row[1] for row in conn.execute("PRAGMA table_info(signal_marks)")}
        with conn:                                   # commit, or roll back on error
            for mark_row in rows:
                row = {k: v for k, v in mark_row.items() if k in valid}
                cols = ",".join(row.keys())
                placeholders = ",".join(":" + k for k in row.keys())
                conn.execute(
                    f"INSERT INTO signal_marks ({cols}) VALUES ({placeholders})",
                    row)
        return len(rows)
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


def get_open_signals_with_latest_mark(db_path=DEFAULT_DB_PATH, *, tracked=False):
    """OPEN signals left-joined to their newest signal_marks row.

    Mark fields (unrealized_pnl, current_score, score_drift, recommendation,
    recommendation_reason, last_mark_ts) are None when no mark exists yet.
    Sorted by first_seen_ts DESC (newest captured first).

    The tracked structures are excluded; ``tracked=True`` returns only them.
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
            WHERE s.status = 'OPEN' AND """ + _tracked_sql("s.", tracked) + """
            ORDER BY s.first_seen_ts DESC
        """)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def close_signal_manually(signal_id, exit_value, exit_reason, db_path=DEFAULT_DB_PATH,
                          close_ts=None, settlement_underlying=None):
    """Manually close an OPEN signal. Writes signal_outcomes row, flips status to CLOSED.

    realized_pnl = (entry_credit - exit_value) * 100  (per-contract dollars).
    Raises ValueError if signal_id is unknown.

    ``settlement_underlying`` is the underlying price an EXPIRY settled against
    (None for every other close). The column has existed since the table did and
    was written NULL on every row, so no outcome could be checked against the
    close it claimed.
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
        "settlement_underlying": (None if settlement_underlying is None
                                  else float(settlement_underlying)),
    }
    insert_outcome(outcome, new_status="CLOSED", db_path=db_path)


def set_be_armed(signal_id, db_path=DEFAULT_DB_PATH):
    """Mark a signal's break-even stop as ARMED (idempotent).

    Set once the trade first reaches +50% credit; the manage cycle then raises the
    stop to break-even + round-trip commissions (see the captured-autoclose design)."""
    conn = connect(db_path)
    try:
        conn.execute("UPDATE signals SET be_armed=1 WHERE signal_id=?", (signal_id,))
        conn.commit()
    finally:
        conn.close()


def count_opened_on(date_iso, db_path=DEFAULT_DB_PATH, *, tracked=False):
    """How many signals were CAPTURED on ``date_iso`` (YYYY-MM-DD).

    Counts by ``first_seen_date`` (indexed), so a signal captured and closed in
    the same session still counts — this is a count of captures, not of
    positions still open. Feeds the Captured Signals footer's "Opened today"
    alongside ``get_outcomes_for_date`` for the closed side. Tracked structures
    are excluded; ``tracked=True`` counts only them.
    """
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE first_seen_date = ? AND "
            + _tracked_sql(tracked=tracked), (date_iso,))
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def get_outcomes_in_range(lo_iso, hi_iso, db_path=DEFAULT_DB_PATH, *, tracked=False):
    """Closed-signal outcomes with ``lo_iso <= close_date <= hi_iso``, OLDEST first.

    The range sibling of ``get_outcomes_for_date``, feeding the captured score's
    Daily / Weekly / MTD rows. It returns two columns that one does not, because
    a score needs both ends of a trade:

    * ``first_seen_ts`` — the "opened" date. It routinely falls OUTSIDE the
      window: a signal opened in August can close in September, and the opened
      count buckets on this while realized P&L buckets on ``close_date``.
    * ``scanner_type`` — 0DTE vs SWING, the split the report breaks out.

    Both ends are INCLUSIVE. A half-open range would drop the newest day, which
    is precisely the day the Daily row is made of.

    Tracked structures are excluded - the captured score is the traded types' -
    and ``tracked=True`` returns only them.
    """
    conn = connect(db_path)
    try:
        cur = conn.execute("""
            SELECT o.signal_id        AS signal_id,
                   s.symbol           AS symbol,
                   s.strategy         AS strategy,
                   s.scanner_type     AS scanner_type,
                   s.first_seen_ts    AS first_seen_ts,
                   s.entry_credit     AS entry_credit,
                   o.exit_value       AS exit_value,
                   o.realized_pnl     AS realized_pnl,
                   o.exit_reason      AS exit_reason,
                   o.close_date       AS close_date,
                   o.close_ts         AS close_ts
            FROM signal_outcomes o
            JOIN signals s ON s.signal_id = o.signal_id
            WHERE o.close_date >= ? AND o.close_date <= ?
              AND """ + _tracked_sql("s.", tracked) + """
            ORDER BY o.close_ts ASC
        """, (str(lo_iso), str(hi_iso)))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_outcomes_for_date(date_iso, db_path=DEFAULT_DB_PATH, *, tracked=False):
    """Closed-signal outcomes for a given ``close_date`` (YYYY-MM-DD), newest first.

    Joins ``signal_outcomes`` to ``signals`` → display rows for the EOD
    "Captured — closed today" view: ``{signal_id, symbol, strategy, entry_credit,
    exit_value, realized_pnl, exit_reason, close_ts}``, ordered by ``close_ts`` DESC.
    Tracked structures are excluded; ``tracked=True`` returns only them."""
    conn = connect(db_path)
    try:
        cur = conn.execute("""
            SELECT o.signal_id       AS signal_id,
                   s.symbol          AS symbol,
                   s.strategy        AS strategy,
                   s.entry_credit    AS entry_credit,
                   o.exit_value      AS exit_value,
                   o.realized_pnl    AS realized_pnl,
                   o.exit_reason     AS exit_reason,
                   o.close_ts        AS close_ts
            FROM signal_outcomes o
            JOIN signals s ON s.signal_id = o.signal_id
            WHERE o.close_date = ? AND """ + _tracked_sql("s.", tracked) + """
            ORDER BY o.close_ts DESC
        """, (date_iso,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_tracked_outcomes(db_path=DEFAULT_DB_PATH):
    """Every closed TRACKED structure with what a study of it needs, oldest first.

    The tracked rows' own reader: it carries the risk figure, the unbounded
    flag and the earnings flag, which the captured score's readers have no use
    for. ``realized_pnl`` is NULL on a row closed as ``UNMARKABLE`` (a calendar
    that could not be marked on its front expiry day) - it has no outcome and
    must not be counted as a scratch.
    """
    conn = connect(db_path)
    try:
        cur = conn.execute("""
            SELECT s.signal_id            AS signal_id,
                   s.symbol               AS symbol,
                   s.strategy             AS strategy,
                   s.family               AS family,
                   s.scanner_type         AS scanner_type,
                   s.first_seen_date      AS first_seen_date,
                   s.dte_at_entry         AS dte_at_entry,
                   s.entry_credit         AS entry_credit,
                   s.entry_max_loss       AS entry_max_loss,
                   s.entry_score          AS entry_score,
                   s.unbounded            AS unbounded,
                   s.entry_spans_earnings AS entry_spans_earnings,
                   o.exit_value           AS exit_value,
                   o.realized_pnl         AS realized_pnl,
                   o.exit_reason          AS exit_reason,
                   o.close_date           AS close_date,
                   o.close_ts             AS close_ts
            FROM signal_outcomes o
            JOIN signals s ON s.signal_id = o.signal_id
            WHERE """ + _tracked_sql("s.", True) + """
            ORDER BY o.close_ts ASC
        """)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def close_unmarkable(signal_id, db_path=DEFAULT_DB_PATH, close_ts=None):
    """Close an OPEN signal with NO outcome: no exit value and no P&L.

    For a tracked calendar that reached its front leg's settlement without a
    mark. It cannot be valued at intrinsic (its back month still has time
    value) and a guessed price would be a fabricated result, so it leaves the
    open set with ``exit_reason = "UNMARKABLE"`` and a NULL P&L that every
    statistic skips.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = close_ts or datetime.now(ZoneInfo("America/Chicago"))
    if get_signal(signal_id, db_path=db_path) is None:
        raise ValueError(f"close_unmarkable: signal_id {signal_id!r} not found")
    insert_outcome({"signal_id": signal_id, "close_ts": now.isoformat(),
                    "close_date": now.date().isoformat(), "exit_value": None,
                    "realized_pnl": None, "exit_reason": "UNMARKABLE",
                    "settlement_underlying": None},
                   new_status="CLOSED", db_path=db_path)
