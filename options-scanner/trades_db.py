"""SQLite persistence for paper trades and their event log.

Replaces the previous paper_trades.json / trade_log.json approach, which
rewrote the whole file on every mutation (N+1 I/O) and risked corruption if
the process was killed mid-write.

Public API is dict-in / dict-out so paper_trader.py can swap its backend
without changing its own callers (dashboard, server/routes/trades).

Schema is intentionally typed rather than a JSON blob column so that query
paths (status/symbol filters, open-risk sums) run as SQL, not as in-memory
filters over decoded JSON.
"""
import json
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "trades.db"
LEGACY_TRADES_JSON = Path(__file__).parent / "data" / "paper_trades.json"
LEGACY_EVENTS_JSON = Path(__file__).parent / "data" / "trade_log.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    mode TEXT,
    status TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    trade_type TEXT,
    expiration TEXT,
    dte_at_entry INTEGER,
    short_strike REAL,
    long_strike REAL,
    call_short REAL,
    call_long REAL,
    width REAL,
    quantity INTEGER,
    entry_credit REAL,
    entry_credit_total REAL,
    max_loss_per REAL,
    max_loss_total REAL,
    -- DEBIT structures (long options / debit verticals). NULL on a credit spread.
    -- `direction` routes expiration settlement; `legs` (JSON) and `entry_debit`
    -- are what settling at intrinsic is computed FROM, so all three must persist.
    direction TEXT,
    legs TEXT,
    entry_debit REAL,
    entry_debit_total REAL,
    max_profit_total REAL,
    unbounded INTEGER,
    breakeven TEXT,
    short_delta REAL,
    net_theta REAL,
    entry_delta REAL,
    entry_theta REAL,
    entry_vega REAL,
    entry_gamma REAL,
    entry_bid REAL,
    entry_ask REAL,
    underlying_at_entry REAL,
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    exit_debit REAL,
    exit_debit_total REAL,
    realized_pnl REAL,
    exit_reason TEXT,
    notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);

CREATE TABLE IF NOT EXISTS trade_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    symbol TEXT,
    strategy TEXT,
    short_strike REAL,
    credit REAL,
    pnl REAL
);
CREATE INDEX IF NOT EXISTS idx_events_trade ON trade_events(trade_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON trade_events(timestamp);
"""

# Ordered to match the SCHEMA columns list; used to bind a dict to an INSERT.
_TRADE_COLUMNS = [
    "trade_id", "mode", "status", "symbol", "strategy", "trade_type",
    "expiration", "dte_at_entry", "short_strike", "long_strike",
    "call_short", "call_long", "width", "quantity",
    "entry_credit", "entry_credit_total", "max_loss_per", "max_loss_total",
    "direction", "legs", "entry_debit", "entry_debit_total",
    "max_profit_total", "unbounded",
    "breakeven", "short_delta", "net_theta",
    "entry_delta", "entry_theta", "entry_vega", "entry_gamma",
    "entry_bid", "entry_ask",
    "underlying_at_entry",
    "entry_time", "exit_time", "exit_debit", "exit_debit_total",
    "realized_pnl", "exit_reason", "notes",
]

_EVENT_COLUMNS = [
    "timestamp", "event", "trade_id", "symbol", "strategy",
    "short_strike", "credit", "pnl",
]


def _row_to_dict(row):
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _trade_to_row(trade):
    """Bind a trade dict to the typed columns, encoding the non-scalar ones.

    ``legs`` is a list[dict] -> JSON TEXT, matching how ``paper_account_db``
    already stores its ``legs`` columns. ``unbounded`` is a bool -> INTEGER,
    since SQLite has no boolean type. Both keep NULL as NULL so a credit spread
    (which has neither) stays distinguishable from a debit trade that has an
    empty list or an explicit ``False``.
    """
    values = {c: trade.get(c) for c in _TRADE_COLUMNS}
    legs = values.get("legs")
    if legs is not None and not isinstance(legs, str):
        values["legs"] = json.dumps(legs)
    unbounded = values.get("unbounded")
    if unbounded is not None:
        values["unbounded"] = int(bool(unbounded))
    return values


def _trade_row_to_dict(row):
    """Decode a trades row, reversing ``_trade_to_row``.

    Kept separate from ``_row_to_dict`` so the shared event-row decoder is
    untouched. A malformed ``legs`` payload degrades to ``[]`` rather than
    raising — the same tolerance ``paper_account_db.list_adjustments`` applies.
    """
    d = _row_to_dict(row)
    if d is None:
        return None
    if d.get("legs") is not None:
        try:
            d["legs"] = json.loads(d["legs"])
        except (TypeError, ValueError):
            d["legs"] = []
    if d.get("unbounded") is not None:
        d["unbounded"] = bool(d["unbounded"])
    return d


def init_db(db_path=None):
    # Resolve at call time so tests can monkeypatch DEFAULT_DB_PATH.
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        # Additive migrations for pre-existing DBs (SQLite has no ADD COLUMN IF NOT
        # EXISTS). Existing rows keep NULL in every new column.
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
        for col, col_type in (
            ("entry_delta", "REAL"), ("entry_theta", "REAL"),
            ("entry_vega", "REAL"), ("entry_gamma", "REAL"),
            ("entry_bid", "REAL"), ("entry_ask", "REAL"),
            ("direction", "TEXT"), ("legs", "TEXT"),
            ("entry_debit", "REAL"), ("entry_debit_total", "REAL"),
            ("max_profit_total", "REAL"), ("unbounded", "INTEGER"),
        ):
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
        conn.commit()
        _maybe_migrate_from_json(conn, db_path)
    finally:
        conn.close()


_initialised: set = set()


def connect(db_path=None):
    """Open a connection, initialising the schema once per path per process.

    NOTE: ``PRAGMA journal_mode=WAL`` is deliberately NOT issued here. journal_mode
    is a PERSISTENT property stored in the database file header — ``init_db`` sets it
    at creation and every later connection inherits it (and ``init_db`` still runs
    once per path per process below, so even a non-WAL file gets upgraded), which
    makes re-issuing it on every connect pure redundancy.

    Honest performance note (measured 2026-07-25, do not oversell this): dropping the
    pragma makes ``connect()`` itself ~3x cheaper (1.78 ms -> 0.60 ms), but it does
    NOT speed up real work. On a fresh connection the FIRST query pays a ~1.1 ms
    WAL/shm attach (open+close 0.42 ms vs open+SELECT+close 1.54 ms). The pragma used
    to BE that first query, so removing it just moves the cost to the next statement:
    ``paper_trader.load_trades()`` measured 1.892 ms before and 1.856 ms after.

    The only way to avoid the repeated attach is to reuse connections rather than
    opening one per operation. That was evaluated and rejected: ledger ops run on the
    hourly paper cycle, the 5-min manage tick and page loads — a few ms per tick in
    total — which does not justify sharing a connection across the service's executor
    threads (``check_same_thread=False`` + locking). Same conclusion the GEX history
    layer reached. This change is a redundancy/hygiene fix, not a speedup.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    db_path = Path(db_path).resolve()
    if db_path not in _initialised:
        init_db(db_path)
        _initialised.add(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _maybe_migrate_from_json(conn, db_path):
    """One-shot import of legacy paper_trades.json / trade_log.json.

    Runs only when the trades table is empty AND the legacy JSON exists.
    The JSON files are left in place as a backup — not deleted.
    """
    row = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()
    if row and row[0]:
        return  # already populated

    # Only migrate when the legacy files live alongside the DB. This
    # prevents a test DB in tmp_path from pulling in the developer's
    # real paper_trades.json.
    if db_path.parent != LEGACY_TRADES_JSON.parent:
        return

    migrated = 0
    if LEGACY_TRADES_JSON.exists():
        try:
            with open(LEGACY_TRADES_JSON) as f:
                legacy = json.load(f)
            for t in legacy:
                insert_trade(conn, t)
            migrated = len(legacy)
        except (json.JSONDecodeError, KeyError):
            pass

    if LEGACY_EVENTS_JSON.exists():
        try:
            with open(LEGACY_EVENTS_JSON) as f:
                events = json.load(f)
            for e in events:
                insert_event(conn, e)
        except (json.JSONDecodeError, KeyError):
            pass

    conn.commit()
    if migrated:
        import logging
        logging.getLogger("paper_trader").info(
            "trades_db: migrated %d legacy paper trades from JSON", migrated
        )


def insert_trade(conn, trade):
    """Insert a trade dict. Extra keys are ignored; missing keys become NULL."""
    placeholders = ", ".join(f":{c}" for c in _TRADE_COLUMNS)
    cols = ", ".join(_TRADE_COLUMNS)
    values = _trade_to_row(trade)
    conn.execute(f"INSERT OR REPLACE INTO trades ({cols}) VALUES ({placeholders})", values)


def update_trade(conn, trade_id, trade):
    """Full-row update. Missing columns become NULL; match old save_trades semantics."""
    set_clause = ", ".join(f"{c}=:{c}" for c in _TRADE_COLUMNS if c != "trade_id")
    values = _trade_to_row(trade)
    values["trade_id"] = trade_id  # always bind the WHERE key
    conn.execute(
        f"UPDATE trades SET {set_clause} WHERE trade_id = :trade_id", values,
    )


def delete_trade(conn, trade_id):
    conn.execute("DELETE FROM trades WHERE trade_id = ?", (trade_id,))


def delete_trades_by_status(conn, statuses):
    placeholders = ", ".join("?" for _ in statuses)
    cur = conn.execute(
        f"DELETE FROM trades WHERE status IN ({placeholders})", list(statuses),
    )
    return cur.rowcount


def fetch_all(conn):
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY entry_time ASC, rowid ASC"
    ).fetchall()
    return [_trade_row_to_dict(r) for r in rows]


def fetch_by_status(conn, status):
    rows = conn.execute(
        "SELECT * FROM trades WHERE status = ? ORDER BY entry_time ASC, rowid ASC",
        (status,),
    ).fetchall()
    return [_trade_row_to_dict(r) for r in rows]


def fetch_one(conn, trade_id):
    return _trade_row_to_dict(
        conn.execute("SELECT * FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
    )


def insert_event(conn, event):
    placeholders = ", ".join(f":{c}" for c in _EVENT_COLUMNS)
    cols = ", ".join(_EVENT_COLUMNS)
    values = {c: event.get(c) for c in _EVENT_COLUMNS}
    conn.execute(f"INSERT INTO trade_events ({cols}) VALUES ({placeholders})", values)


def fetch_events(conn, limit=None):
    sql = "SELECT * FROM trade_events ORDER BY event_id ASC"
    if limit is not None:
        sql += " LIMIT ?"
        rows = conn.execute(sql, (limit,)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()
    return [_row_to_dict(r) for r in rows]
