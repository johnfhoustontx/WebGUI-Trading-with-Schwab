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
    last_mark_ts TEXT,
    parent_position_id INTEGER,
    mae REAL,               -- max adverse excursion (most-negative position-level unrealized $ seen)
    mfe REAL,               -- max favorable excursion (most-positive position-level unrealized $ seen)
    entry_context TEXT,     -- JSON: decision context at open (driver posture/market_read); NULL for manual
    be_armed INTEGER DEFAULT 0  -- opt-in manual-paper lifecycle: 1 once +50% credit has armed break-even
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_signal ON paper_positions(signal_id);

CREATE TABLE IF NOT EXISTS position_adjustments (
    adjustment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER,
    parent_position_id INTEGER,
    action TEXT,
    legs TEXT,            -- JSON
    gross_cash REAL,
    commission REAL,
    net_cash REAL,
    reason TEXT,
    ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_adj_position ON position_adjustments(position_id);

-- Share inventory. A lot is cash that has been CONVERTED INTO SHARES; it never
-- holds reserved buying power. That is why it is a table of its own rather than
-- a `kind` column on paper_positions: reconcile_buying_power recomputes
-- buying_power_reserved as Σ OPEN paper_positions.max_loss_total and corrects
-- the drift against cash, so anything reserving outside that sum is silently
-- zeroed at the next service start. Keeping lots out of paper_positions also
-- keeps every one of that table's many readers correct without learning to
-- filter — and any one of them forgetting would be a silent miscount.
CREATE TABLE IF NOT EXISTS equity_lots (
    lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    shares INTEGER NOT NULL,
    cost_basis REAL NOT NULL,          -- per share
    opened_ts TEXT,
    source TEXT,                       -- 'assignment' | 'manual'
    source_position_id INTEGER,        -- the assigned short put, when source='assignment'
    status TEXT DEFAULT 'OPEN',
    closed_ts TEXT,
    exit_price REAL,
    realized_pnl REAL,
    exit_reason TEXT                   -- 'called_away' | 'sold'
);
CREATE INDEX IF NOT EXISTS idx_lots_status ON equity_lots(status);
CREATE INDEX IF NOT EXISTS idx_lots_symbol ON equity_lots(symbol);
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
        # idempotent migration for older DBs — additive nullable columns only.
        # A whole new TABLE needs no entry here: executescript above runs the
        # full SCHEMA every time, and each statement is CREATE ... IF NOT EXISTS,
        # so an existing DB gains it (empty) on the next open. Only a new COLUMN
        # on a table that already exists needs an ALTER.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_positions)")}
        for name, decl in (("parent_position_id", "INTEGER"), ("mae", "REAL"),
                           ("mfe", "REAL"), ("entry_context", "TEXT"),
                           ("be_armed", "INTEGER DEFAULT 0")):
            if name not in cols:
                conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {name} {decl}")
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


def reconcile_buying_power(db_path):
    """Recompute ``buying_power_reserved`` = Σ OPEN positions' ``max_loss_total``
    and correct any drift, crediting/debiting ``cash`` by the inverse delta so the
    account's total committed capital (``cash + buying_power_reserved``) is
    invariant across the fix.

    Why (R6): opening a position is a 3-commit sequence — record_order →
    reserve_buying_power → insert_position — that is NOT atomic. A crash BETWEEN
    reserve and insert leaves buying power reserved against no position, so the
    driver's halt/loss-cap math (which reads reserved/equity) runs on a corrupted
    book. Rewriting the sequence into one transaction is too invasive; instead we
    reconcile at service startup for BOTH the manual and the driver account.

    Idempotent + defensive: a no-op when reserved already equals the open-position
    total (within a rounding tolerance); returns the corrected drift amount (0.0 on
    no-op / no account). Logs when it actually corrects. Never raises."""
    try:
        conn = connect(db_path)
    except Exception:  # noqa: BLE001 — a cold/unreadable DB must not crash startup.
        log.exception("reconcile_buying_power could not open %s", db_path)
        return 0.0
    try:
        with conn:
            a = conn.execute(
                "SELECT cash, buying_power_reserved FROM account WHERE id=1").fetchone()
            if a is None:
                return 0.0  # no account yet → nothing to reconcile
            rows = conn.execute(
                "SELECT COALESCE(SUM(max_loss_total), 0) AS s "
                "FROM paper_positions WHERE status='OPEN'").fetchone()
            correct_reserved = round(float(rows["s"] or 0.0), 2)
            current_reserved = round(float(a["buying_power_reserved"] or 0.0), 2)
            drift = round(current_reserved - correct_reserved, 2)
            if abs(drift) < 0.01:
                return 0.0  # consistent → no-op
            # Over-reserved (drift>0) → credit the orphaned BP back to cash;
            # under-reserved (drift<0) → debit cash. Keeps cash+reserved invariant.
            new_cash = round(float(a["cash"]) + drift, 2)
            _update_account(conn, cash=new_cash, buying_power_reserved=correct_reserved)
            log.warning(
                "reconciled buying power on %s: reserved %.2f -> %.2f "
                "(drift %.2f, cash %.2f -> %.2f)",
                db_path, current_reserved, correct_reserved, drift, a["cash"], new_cash)
            return drift
    except Exception:  # noqa: BLE001 — reconciliation must never crash startup.
        log.exception("reconcile_buying_power degraded for %s", db_path)
        return 0.0
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


def debit_cash(db_path, amount):
    """Move cash OUT of the account, touching neither reserved BP nor realized P&L.

    Buying shares on assignment is a CONVERSION of cash into stock — it is
    neither a reservation nor a loss, and the two functions either side of this
    one are both wrong for it. Reserving would be undone: ``reconcile_buying_power``
    recomputes ``buying_power_reserved`` from the open-position sum and hands any
    excess straight back to cash. Booking it through ``realize_pnl`` would report
    the purchase price as a realized loss and, at a whole strike notional, trip
    the session drawdown halt on a trade that lost nothing. Hence a plain cash
    move; the shares it bought are recorded in ``equity_lots``.
    """
    conn = connect(db_path)
    try:
        with conn:
            a = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()
            _update_account(conn, cash=round(a["cash"] - amount, 2))
    finally:
        conn.close()


def credit_cash(db_path, amount):
    """Move cash INTO the account, touching neither reserved BP nor realized P&L.

    The exact mirror of :func:`debit_cash`, and it exists for the exact mirror of
    that function's reason: shares being called away is a CONVERSION of stock
    back into cash, not a gain. The gain is the difference between the exit price
    and the basis, and it is booked separately through ``realize_pnl``.

    ⚠ **Credit the BASIS here, never the exit price.** ``realize_pnl`` moves cash
    too, so a caller crediting ``strike x shares`` here AND booking the lot's P&L
    would credit the gain twice — the disposal-side mirror of the double-release
    the assignment path is guarded against, and just as invisible: the lot closes,
    the share count is right, the exit price is right, and only the account
    balance is wrong. ``paper_engine._call_away_shares`` credits
    ``cost_basis x shares`` and lets ``realize_pnl`` carry the rest, so the two
    together move exactly ``strike x shares``.
    """
    conn = connect(db_path)
    try:
        with conn:
            a = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()
            _update_account(conn, cash=round(a["cash"] + amount, 2))
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
            # Shares are in this sum for exactly that reason, and AT COST: a lot's
            # cost basis is committed capital by the same definition that puts
            # buying_power_reserved here, while its mark is unrealized and so stays
            # out, matching how options are treated. Omitting the term would
            # understate equity for any session that opens holding stock.
            # ⚠ Measured 2026-09-05: NOTHING reads session_start_equity yet — the
            # live drawdown guard is should_halt against the absolute-dollar
            # config_paper.MAX_SESSION_DRAWDOWN, which never consults it. So this
            # is about storing the correct number for the first reader, not about
            # fixing a guard that is loose today; do not cite it as a live fix.
            # `_equity_at_cost_conn` takes the open connection so this stays one
            # transaction rather than opening a second connection mid-`with`.
            equity = (a["cash"] + a["buying_power_reserved"]
                      + _equity_at_cost_conn(conn))
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
                "max_loss_total", "entry_ts", "entry_context")
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


def set_be_armed(db_path, position_id):
    """Arm the opt-in manual-paper break-even lifecycle stop for one position —
    a one-way latch (never unarmed). Mirrors ``signal_db.set_be_armed`` for
    captured signals; ``paper_engine.run_manage_cycle`` calls this the first time
    a lifecycle-enabled position's pnl reaches +50% of credit."""
    conn = connect(db_path)
    try:
        conn.execute("UPDATE paper_positions SET be_armed=1 WHERE position_id=?",
                     (position_id,))
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
# POSITION ADJUSTMENTS (rescue)
#############################################

def insert_adjustment(db_path, position_id, action, legs=None, gross_cash=0.0,
                      commission=0.0, net_cash=0.0, reason="", ts=None,
                      parent_position_id=None):
    """Record one rescue adjustment. legs is a list[dict] (JSON-encoded)."""
    import json, datetime
    if ts is None:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO position_adjustments (position_id, parent_position_id, "
            "action, legs, gross_cash, commission, net_cash, reason, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (position_id, parent_position_id, action, json.dumps(legs or []),
             gross_cash, commission, net_cash, reason, ts))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_adjustments(db_path, position_id):
    """Return adjustment rows for a position (newest first), legs JSON-decoded."""
    import json
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM position_adjustments WHERE position_id = ? "
            "ORDER BY adjustment_id DESC", (position_id,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["legs"] = json.loads(d.get("legs") or "[]")
        except Exception:
            d["legs"] = []
        out.append(d)
    return out


#############################################
# EQUITY LOTS (share inventory)
#############################################

# ⚠ THE INVARIANT for everything below: a lot is cash already converted into
# shares, NEVER a buying-power reservation. `reconcile_buying_power` recomputes
# `buying_power_reserved` from Σ OPEN paper_positions.max_loss_total and returns
# any excess to cash, so a lot that reserved would be silently zeroed at the next
# service start. Nothing here touches `buying_power_reserved`, and nothing may.
#
# ⚠ Every signature below is `def f(db_path=None, ...)` with DEFAULT_DB_PATH
# resolved inside `connect`, never `db_path=DEFAULT_DB_PATH` in the signature:
# Python binds a default at `def` time, which is exactly why `signal_db`'s
# test-isolation monkeypatch was inert for weeks and leaked 24 synthetic signals
# into both live environments.


def insert_equity_lot(db_path=None, lot=None):
    """Record one lot of shares. Returns the new ``lot_id``.

    ``status`` defaults to 'OPEN' from the DDL, and ``opened_ts`` is stamped when
    the caller does not supply one so a lot can always be aged (the covered-call
    screen wants holding period).
    """
    import datetime
    lot = dict(lot or {})
    if not lot.get("opened_ts"):
        lot["opened_ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    cols = ("symbol", "shares", "cost_basis", "opened_ts", "source",
            "source_position_id")
    conn = connect(db_path)
    try:
        cur = conn.execute(
            f"INSERT INTO equity_lots ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})",
            tuple(lot.get(c) for c in cols))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def fetch_open_lots(db_path=None):
    """Every lot still held, oldest first — 'what stock do I own right now'."""
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM equity_lots WHERE status='OPEN' ORDER BY lot_id")]
    finally:
        conn.close()


def close_equity_lot(db_path=None, lot_id=None, exit_price=None, reason=None,
                     closed_ts=None):
    """Dispose of a whole lot. Returns the realized P&L it booked.

    One lot per assignment, closed whole — multi-lot cost-basis accounting
    (FIFO / LIFO / specific-ID) is deliberately not built, so realized P&L is
    simply the whole-lot move off the basis. A missing ``lot_id`` raises rather
    than degrading to a plausible zero: a caller closing a lot that is not there
    is a bug, and a silent 0.0 would book it as a flat trade.
    """
    import datetime
    if closed_ts is None:
        closed_ts = datetime.datetime.now().isoformat(timespec="seconds")
    conn = connect(db_path)
    try:
        with conn:
            row = conn.execute(
                "SELECT shares, cost_basis FROM equity_lots WHERE lot_id=?",
                (lot_id,)).fetchone()
            if row is None:
                raise ValueError(f"no equity lot {lot_id!r} in {db_path}")
            realized = round((float(exit_price) - float(row["cost_basis"]))
                             * int(row["shares"]), 2)
            conn.execute(
                "UPDATE equity_lots SET status='CLOSED', closed_ts=?, "
                "exit_price=?, realized_pnl=?, exit_reason=? WHERE lot_id=?",
                (closed_ts, exit_price, realized, reason, lot_id))
        return realized
    finally:
        conn.close()


def _equity_at_cost_conn(conn):
    """Σ(open lots: shares × cost_basis) on an ALREADY-OPEN connection.

    Exists so ``roll_session_if_needed`` can add this term inside its existing
    ``with conn:`` transaction instead of opening a second connection mid-write.
    ``COALESCE`` so a cold table reads 0.0 and never None — the caller adds this
    straight onto cash.
    """
    r = conn.execute("SELECT COALESCE(SUM(shares * cost_basis), 0) AS c "
                     "FROM equity_lots WHERE status='OPEN'").fetchone()
    return round(float(r["c"]), 2)


def equity_at_cost(db_path=None):
    """Capital committed to shares, at basis. Open lots only — a closed lot is
    cash again, and counting it here would double it."""
    conn = connect(db_path)
    try:
        return _equity_at_cost_conn(conn)
    finally:
        conn.close()


#############################################
# RESET
#############################################

def reset_account(db_path=None, starting_balance=25_000.0, session_date=None):
    """Clear all positions + orders + share lots and reset the account row.
    For the Portfolio tab's 'Reset paper account' action — intentional, explicit.

    `equity_lots` goes with them: a lot is cash this account already spent, so a
    lot surviving a reset reports shares against a wiped balance. `equity_at_cost`
    would keep counting it, and `roll_session_if_needed` feeds that term into
    `session_start_equity` — the next session would open claiming committed
    capital the account no longer has."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM paper_positions")
            conn.execute("DELETE FROM paper_orders")
            conn.execute("DELETE FROM equity_lots")
            conn.execute(
                "UPDATE account SET cash=?, buying_power_reserved=0, realized_pnl=0, "
                "session_date=?, session_start_equity=?, session_realized_pnl=0, "
                "halted=0 WHERE id=1",
                (starting_balance, session_date, starting_balance))
        log.info("reset paper account: cash=%.2f session=%s", starting_balance, session_date)
    finally:
        conn.close()
