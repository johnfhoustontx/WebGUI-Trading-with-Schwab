"""The forward earnings calendar's READ path, over ``EARNINGS_CALENDAR_DB``.

**Why the read half lives here and the write half does not.** The store is
filled by one nightly Alpha Vantage pull owned by ``trade_svc`` — a vendor
credential, an HTTP call and a parser, all of which belong to that service
alone. But two services now need to *read* it: ``trade_svc`` for the analyze
verdict, and ``options_svc`` for the earnings gate on the 30-45 DTE income
window, where a straddled report is close to certain rather than occasional.
A Tier-2 service may not import another service's engines, and
``options_svc`` importing ``trade_svc.earnings_calendar`` would be exactly
that.

**Reading a shared store is not a cross-service import.** ``shared/market_calendar.py``
and ``shared/symbols.py`` are the standing precedent, and ``EARNINGS_CALENDAR_DB``
is already a ``repo_paths`` constant. So the readers move here and
``services/trade_svc/earnings_calendar.py`` imports them back — one
implementation, two callers, no copy to drift.

⚠ **The three-valued :func:`coverage` is the reason this is a careful move and
not a rewrite.** ``days_to_earnings is None`` means two different things, and a
gate that reads the second as "clear" fails open silently on exactly the names
most likely to be traded. See that function's docstring; the invariant is
pinned in ``shared/tests/test_earnings.py``.
"""
import datetime as dt
import logging
import sqlite3
from pathlib import Path

from repo_paths import EARNINGS_CALENDAR_DB

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = EARNINGS_CALENDAR_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS earnings (
    symbol             TEXT NOT NULL,
    report_date        TEXT NOT NULL,
    fiscal_date_ending TEXT,
    estimate           REAL,
    recorded_at        TEXT,
    PRIMARY KEY (symbol, report_date)
);
CREATE INDEX IF NOT EXISTS idx_earn_symbol ON earnings (symbol, report_date);
"""


# ── store ───────────────────────────────────────────────────────────────────

def init_db(db_path=None):
    """Open the calendar store, creating it if absent.

    ⚠ ``db_path=None`` resolved HERE, deliberately not
    ``db_path=DEFAULT_DB_PATH``: Python binds a default at ``def`` time, so the
    latter shape leaves the path unpatchable from outside — which is precisely
    why ``signal_db``'s test isolation had never worked, and how 24 synthetic
    signals reached both live environments. ``paper_account_db`` does it this
    way and is the shape to copy."""
    db_path = Path(db_path or DEFAULT_DB_PATH)
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


# ── read ────────────────────────────────────────────────────────────────────

def lookup(conn, symbol, as_of=None):
    """The symbol's NEXT scheduled report on/after ``as_of``, or None.

    A symbol carries several scheduled quarters; the gate cares about the next
    one. A date already past is never returned as upcoming.

    ⚠ Reads through a CURSOR carrying its own ``row_factory`` rather than
    ``conn.execute``, so the result is a name-indexable row no matter how the
    caller opened the store. It used to inherit the connection's factory, and
    every reader here indexes by column name — so a caller who opened the file
    with a plain ``sqlite3.connect`` got ``TypeError`` on ``row["report_date"]``,
    which ``days_to_earnings`` swallowed into ``None``. That renders as "no
    earnings scheduled" for EVERY symbol, which is a confident answer over a
    working 1,808-row calendar, and it is indistinguishable on screen from an
    empty one. A cursor factory overrides the connection's, so this is correct
    even against a connection configured some other way."""
    as_of = as_of or dt.date.today()
    try:
        cur = conn.cursor()
        cur.row_factory = sqlite3.Row
        return cur.execute(
            "SELECT * FROM earnings WHERE symbol = ? AND report_date >= ? "
            "ORDER BY report_date ASC LIMIT 1",
            ((symbol or "").strip().upper(), as_of.isoformat())).fetchone()
    except Exception:
        # A read failure here is genuinely unknown-not-absent, and the callers
        # distinguish those (see `coverage`). Logged rather than silent, so a
        # broken store cannot masquerade as an empty calendar again.
        logger.warning("earnings.lookup failed for %s", symbol, exc_info=True)
        return None


def coverage(conn, symbol, as_of=None):
    """Whether the calendar can speak for this symbol at all.

    ``"upcoming"``       a scheduled report on or after ``as_of``
    ``"none_scheduled"`` the vendor knows the symbol but has nothing ahead —
                         a real, trustworthy "no earnings in the window"
    ``"not_listed"``     the vendor does not carry the symbol — we do NOT know

    The last two both produce ``days_to_earnings is None``, and conflating them
    makes the gate fail OPEN silently. Measured live 2026-08-22 with a real
    key: the 12-month horizon returns 1,814 symbols and coverage collapses with
    distance (1,032 rows in October, 40 in December, 11 in March). It is
    genuinely patchy rather than merely announced-only — AAPL and GOOGL appear
    at 67-68 days out while MSFT, AMZN and META, the same late-October cycle,
    are absent entirely. A caller that reads "not_listed" as "no earnings" walks
    a trade into an unlisted report wearing the appearance of protection."""
    try:
        if lookup(conn, symbol, as_of=as_of) is not None:
            return "upcoming"
        row = conn.execute(
            "SELECT 1 FROM earnings WHERE symbol = ? LIMIT 1",
            ((symbol or "").strip().upper(),)).fetchone()
        return "none_scheduled" if row else "not_listed"
    except Exception:
        # "not_listed" is the honest answer here — it already means "we do NOT
        # know" — but a read failure reaching it silently is how this function
        # would come to describe a broken store as a patchy vendor.
        logger.warning("earnings.coverage failed for %s", symbol, exc_info=True)
        return "not_listed"


def days_to_earnings(conn, symbol, as_of=None):
    """Calendar days until the next report, or None when unknown.

    **Zero is a real answer** — a report TODAY is the most gate-worthy value
    there is, and must not collapse into the None that means "no idea"."""
    as_of = as_of or dt.date.today()
    row = lookup(conn, symbol, as_of=as_of)
    if row is None:
        return None
    try:
        return (dt.date.fromisoformat(row["report_date"]) - as_of).days
    except (TypeError, ValueError):
        return None
