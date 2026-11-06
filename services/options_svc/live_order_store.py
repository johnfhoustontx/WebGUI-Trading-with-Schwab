"""The live-order tickets: ``services/options_svc/data/live_orders.db``.

One row per ticket (a previewed order and what became of it). Design:
docs/plans/2026-11-05-live-order-to-thinkorswim-design.md ("The store").

A ticket moves ``previewed -> sending -> sent | unknown | rejected``, or
``previewed -> expired`` (or ``refused`` when the preview itself did not pass).
``claim`` is the ONE way out of ``previewed`` toward Schwab: a single
conditional UPDATE, so a second delivery of the same Send places nothing.

It holds the account HASH (the proxy needs it) and never the account number;
Schwab's answers are stored scrubbed. It holds no secret. It is in the nightly
backup and must never be added to ``DB_EXCLUDE``: it is the journal of every
order sent to the account.

Every function takes ``db_path=None``, resolved at CALL time, so a test can
point it at a temporary file. Imports nothing from ``compute``.
"""
from __future__ import annotations

import json
import os
import sqlite3

import repo_paths

STATUSES = ("previewed", "refused", "sending", "sent", "unknown", "rejected", "expired")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id     TEXT PRIMARY KEY,
    trade_id      TEXT NOT NULL,
    idea_posted   TEXT,
    alias         TEXT NOT NULL,
    account_hash  TEXT NOT NULL,
    symbol        TEXT,
    structure     TEXT,
    contracts     INTEGER NOT NULL,
    limit_price   REAL NOT NULL,
    debit         INTEGER NOT NULL,
    max_loss      REAL,
    payload       TEXT NOT NULL,
    digest        TEXT NOT NULL,
    verdict       TEXT,
    messages      TEXT,
    answer        TEXT,
    status        TEXT NOT NULL,
    order_id      TEXT,
    order_status  TEXT,
    error         TEXT,
    created_at    REAL NOT NULL,
    expires_at    REAL NOT NULL,
    sent_at       REAL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tickets_trade ON tickets (trade_id, created_at);
"""

#: Columns ``update`` may set. A claim is not one of them: it goes through ``claim``.
_UPDATABLE = frozenset({"verdict", "messages", "answer", "status", "order_id",
                        "order_status", "error", "sent_at", "updated_at"})
_JSON = ("payload", "messages", "answer")


def _path(db_path):
    return repo_paths.LIVE_ORDERS_DB if db_path is None else db_path


def connect(db_path=None) -> sqlite3.Connection:
    """Open the store (folder, file and schema made if missing). The caller closes it."""
    path = _path(db_path)
    parent = os.path.dirname(os.fspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(os.fspath(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _dump(field, value):
    return json.dumps(value, separators=(",", ":")) if field in _JSON and value is not None \
        else value


def _row(r):
    if r is None:
        return None
    out = dict(r)
    for f in _JSON:
        if out.get(f) is not None:
            try:
                out[f] = json.loads(out[f])
            except ValueError:
                pass
    out["debit"] = bool(out.get("debit"))
    return out


def insert(ticket: dict, db_path=None) -> bool:
    """Add a new ticket. False when the id is already taken (a repeated preview
    command), so a replay never overwrites a ticket."""
    if ticket.get("status") not in STATUSES:
        raise ValueError(f"unknown status {ticket.get('status')!r}")
    cols = list(ticket)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                f"INSERT INTO tickets ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' for _ in cols)})",
                [_dump(c, ticket[c]) for c in cols])
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get(ticket_id: str, db_path=None):
    """One ticket, or None."""
    conn = connect(db_path)
    try:
        return _row(conn.execute("SELECT * FROM tickets WHERE ticket_id = ?",
                                 (ticket_id,)).fetchone())
    finally:
        conn.close()


def claim(ticket_id: str, now: float, db_path=None) -> bool:
    """Move a ticket ``previewed -> sending`` if, and only if, it is still
    previewed and not expired. ONE statement: of two deliveries of the same
    Send, exactly one gets True."""
    conn = connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE tickets SET status = 'sending', updated_at = ? "
                "WHERE ticket_id = ? AND status = 'previewed' AND expires_at > ?",
                (now, ticket_id, now))
        return cur.rowcount == 1
    finally:
        conn.close()


def update(ticket_id: str, db_path=None, **fields) -> None:
    """Set some of a ticket's result columns."""
    bad = set(fields) - _UPDATABLE
    if bad:
        raise ValueError(f"not updatable: {sorted(bad)}")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"unknown status {fields['status']!r}")
    if not fields:
        return
    cols = sorted(fields)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                f"UPDATE tickets SET {', '.join(f'{c} = ?' for c in cols)} "
                "WHERE ticket_id = ?",
                [_dump(c, fields[c]) for c in cols] + [ticket_id])
    finally:
        conn.close()


def expire_stale(now: float, db_path=None) -> int:
    """Mark every previewed ticket past its time as expired. Returns how many."""
    conn = connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE tickets SET status = 'expired', updated_at = ? "
                "WHERE status = 'previewed' AND expires_at <= ?", (now, now))
        return cur.rowcount
    finally:
        conn.close()
