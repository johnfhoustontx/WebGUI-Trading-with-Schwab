"""
paper_account_db.py - Paper account / orders / positions store
Version: 1.0.0
Last Updated: 2026-06-03

SQLite store for the Portfolio paper-trading system. Deliberately SEPARATE from
trades.db so paper account state is never shared with any live path. Schema is
idempotent (safe to call init on every startup).

Version 1.0.0 Changes:
- Initial implementation
"""
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "paper_account.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    buying_power_reserved REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    session_date TEXT,
    session_start_equity REAL NOT NULL DEFAULT 0,
    session_realized_pnl REAL NOT NULL DEFAULT 0,
    halted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS paper_orders (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT,
    ts TEXT,
    symbol TEXT,
    side TEXT,
    strategy TEXT,
    legs TEXT,
    quantity INTEGER,
    order_type TEXT,
    limit_price REAL,
    status TEXT,
    fill_price REAL,
    fill_ts TEXT,
    reject_reason TEXT,
    response_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_signal ON paper_orders(signal_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON paper_orders(status);

CREATE TABLE IF NOT EXISTS paper_positions (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT,
    symbol TEXT,
    strategy TEXT,
    short_strike REAL,
    long_strike REAL,
    call_short REAL,
    call_long REAL,
    width REAL,
    expiration TEXT,
    dte_at_entry INTEGER,
    quantity INTEGER,
    entry_credit REAL,
    entry_order_id INTEGER,
    max_loss_per REAL,
    max_loss_total REAL,
    entry_ts TEXT,
    status TEXT DEFAULT 'OPEN',
    exit_debit REAL,
    exit_order_id INTEGER,
    realized_pnl REAL,
    exit_reason TEXT,
    exit_ts TEXT,
    current_value REAL,
    unrealized_pnl REAL,
    current_short_delta REAL,
    last_mark_ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_signal ON paper_positions(signal_id);
"""

_initialised: set = set()


#############################################
# CONNECTION
#############################################

def init_db(db_path=None):
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def connect(db_path=None):
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    db_path = Path(db_path).resolve()
    if db_path not in _initialised:
        init_db(db_path)
        _initialised.add(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row):
    return dict(row) if row is not None else None


#############################################
# ACCOUNT
#############################################

def ensure_account(db_path=None, starting_balance=25_000.0, session_date=None):
    """Seed the single account row if it does not exist. Idempotent — never
    overwrites an existing account."""
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT 1 FROM account WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO account (id, cash, buying_power_reserved, "
                "realized_pnl, session_date, session_start_equity, "
                "session_realized_pnl, halted) VALUES (1, ?, 0, 0, ?, ?, 0, 0)",
                (starting_balance, session_date, starting_balance),
            )
            conn.commit()
            log.info("seeded paper account: cash=%.2f session=%s", starting_balance, session_date)
    finally:
        conn.close()


def get_account(db_path=None):
    conn = connect(db_path)
    try:
        return _row_to_dict(conn.execute("SELECT * FROM account WHERE id = 1").fetchone())
    finally:
        conn.close()


#############################################
# ACCOUNT MUTATIONS
#############################################

def _update_account(conn, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE account SET {sets} WHERE id = 1", tuple(fields.values()))


def reserve_buying_power(db_path, amount):
    conn = connect(db_path)
    try:
        with conn:
            a = conn.execute("SELECT cash, buying_power_reserved FROM account WHERE id=1").fetchone()
            _update_account(conn, cash=round(a["cash"] - amount, 2),
                            buying_power_reserved=round(a["buying_power_reserved"] + amount, 2))
    finally:
        conn.close()


def release_buying_power(db_path, amount):
    conn = connect(db_path)
    try:
        with conn:
            a = conn.execute("SELECT cash, buying_power_reserved FROM account WHERE id=1").fetchone()
            _update_account(conn, cash=round(a["cash"] + amount, 2),
                            buying_power_reserved=round(a["buying_power_reserved"] - amount, 2))
    finally:
        conn.close()


def realize_pnl(db_path, pnl):
    conn = connect(db_path)
    try:
        with conn:
            a = conn.execute("SELECT cash, realized_pnl, session_realized_pnl FROM account WHERE id=1").fetchone()
            _update_account(conn, cash=round(a["cash"] + pnl, 2),
                            realized_pnl=round(a["realized_pnl"] + pnl, 2),
                            session_realized_pnl=round(a["session_realized_pnl"] + pnl, 2))
    finally:
        conn.close()


def set_halted(db_path, halted):
    conn = connect(db_path)
    try:
        with conn:
            _update_account(conn, halted=1 if halted else 0)
    finally:
        conn.close()


def roll_session_if_needed(db_path, today):
    """If session_date != today, reset session counters + un-halt. Returns True
    if a roll happened. Lifetime cash / realized_pnl are preserved."""
    conn = connect(db_path)
    try:
        with conn:
            a = conn.execute("SELECT * FROM account WHERE id=1").fetchone()
            if a["session_date"] == today:
                return False
            # Committed capital at roll; open unrealized P&L is deliberately excluded
            # (should_halt tracks realized + unrealized separately for the drawdown guard).
            equity = a["cash"] + a["buying_power_reserved"]
            _update_account(conn, session_date=today, session_start_equity=equity,
                            session_realized_pnl=0.0, halted=0)
            log.info("rolled paper session -> %s", today)
            return True
    finally:
        conn.close()


def should_halt(db_path, open_unrealized, max_drawdown):
    a = get_account(db_path)
    return (a["session_realized_pnl"] + (open_unrealized or 0)) <= -abs(max_drawdown)


#############################################
# ORDERS
#############################################

def insert_order(db_path, o):
    conn = connect(db_path)
    try:
        cols = ("signal_id", "ts", "symbol", "side", "strategy", "legs",
                "quantity", "order_type", "limit_price", "status", "fill_price",
                "fill_ts", "reject_reason", "response_json")
        cur = conn.execute(
            f"INSERT INTO paper_orders ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})",
            tuple(o.get(c) for c in cols))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def fetch_orders(db_path, limit=None, status=None):
    conn = connect(db_path)
    try:
        q = "SELECT * FROM paper_orders"
        params = ()
        if status:
            q += " WHERE status=?"
            params = (status,)
        q += " ORDER BY order_id DESC"
        if limit is not None:
            q += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(q, params)]
    finally:
        conn.close()


def has_order_for_signal(db_path, signal_id):
    """True if ANY order (filled OR rejected) was already recorded for this
    signal. Used to evaluate each signal at most once — prevents both re-entry
    of a filled/closed signal and repeated re-rejection of un-sizeable ones
    (the reject-churn flood)."""
    conn = connect(db_path)
    try:
        r = conn.execute("SELECT 1 FROM paper_orders WHERE signal_id=? LIMIT 1",
                         (signal_id,)).fetchone()
        return r is not None
    finally:
        conn.close()


def purge_rejected_orders(db_path=None):
    """Delete all REJECTED order rows. They carry no cash/position effect, so
    removing them is safe; the paper_engine log file retains a copy. Returns the
    number of rows deleted."""
    conn = connect(db_path)
    try:
        with conn:
            cur = conn.execute("DELETE FROM paper_orders WHERE status='REJECTED'")
        return cur.rowcount
    finally:
        conn.close()


#############################################
# POSITIONS
#############################################

def insert_position(db_path, p):
    conn = connect(db_path)
    try:
        cols = ("signal_id", "symbol", "strategy", "short_strike", "long_strike",
                "call_short", "call_long", "width", "expiration", "dte_at_entry",
                "quantity", "entry_credit", "entry_order_id", "max_loss_per",
                "max_loss_total", "entry_ts")
        cur = conn.execute(
            f"INSERT INTO paper_positions ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})",
            tuple(p.get(c) for c in cols))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def has_open_position(db_path, signal_id):
    conn = connect(db_path)
    try:
        r = conn.execute("SELECT 1 FROM paper_positions WHERE signal_id=? "
                         "AND status='OPEN' LIMIT 1", (signal_id,)).fetchone()
        return r is not None
    finally:
        conn.close()


def has_traded_signal(db_path, signal_id):
    """True if ANY position (open OR closed) exists for this signal. Used to
    avoid re-opening a signal that already traded today — prevents the
    stop-out/re-entry churn loop."""
    conn = connect(db_path)
    try:
        r = conn.execute("SELECT 1 FROM paper_positions WHERE signal_id=? LIMIT 1",
                         (signal_id,)).fetchone()
        return r is not None
    finally:
        conn.close()


def fetch_open_positions(db_path):
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY position_id")]
    finally:
        conn.close()


def fetch_all_positions(db_path):
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM paper_positions ORDER BY position_id DESC")]
    finally:
        conn.close()


def update_position_mark(db_path, position_id, **fields):
    conn = connect(db_path)
    try:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE paper_positions SET {sets} WHERE position_id=?",
                     tuple(fields.values()) + (position_id,))
        conn.commit()
    finally:
        conn.close()


def close_position(db_path, position_id, exit_debit, exit_order_id,
                   realized_pnl, exit_reason, exit_ts, status="CLOSED"):
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE paper_positions SET status=?, exit_debit=?, exit_order_id=?, "
            "realized_pnl=?, exit_reason=?, exit_ts=? WHERE position_id=?",
            (status, exit_debit, exit_order_id, realized_pnl, exit_reason,
             exit_ts, position_id))
        conn.commit()
    finally:
        conn.close()


def open_unrealized(db_path):
    conn = connect(db_path)
    try:
        r = conn.execute("SELECT COALESCE(SUM(unrealized_pnl),0) AS u "
                         "FROM paper_positions WHERE status='OPEN'").fetchone()
        return float(r["u"])
    finally:
        conn.close()


#############################################
# RESET
#############################################

def reset_account(db_path=None, starting_balance=25_000.0, session_date=None):
    """Clear all positions + orders and reset the account row to starting values.
    For the Portfolio tab's 'Reset paper account' action — intentional, explicit."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM paper_positions")
            conn.execute("DELETE FROM paper_orders")
            conn.execute(
                "UPDATE account SET cash=?, buying_power_reserved=0, realized_pnl=0, "
                "session_date=?, session_start_equity=?, session_realized_pnl=0, "
                "halted=0 WHERE id=1",
                (starting_balance, session_date, starting_balance))
        log.info("reset paper account: cash=%.2f session=%s", starting_balance, session_date)
    finally:
        conn.close()
