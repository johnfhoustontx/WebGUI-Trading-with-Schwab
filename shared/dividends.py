"""The forward dividends calendar's store and READ path, over ``DIVIDENDS_DB``.

**Why the read half lives here.** The store is filled by one service's fetch,
but more than one service reads it, and a Tier-2 service may not import another
service's engines — so, exactly as ``shared/earnings.py`` does for the earnings
calendar, the store and its readers live in ``shared/`` and every caller imports
them from here: one implementation, no copy to drift.

⚠ :func:`coverage` is three-valued plus ``"unknown"`` for the same reason the
earnings one is: "no dividend ahead" (``none``) and "we could not tell"
(``error`` / ``unknown``) are different facts, and a reader that folds the
second into the first fails open silently.
"""
import datetime as dt
import logging
import sqlite3
from pathlib import Path

from repo_paths import DIVIDENDS_DB

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = DIVIDENDS_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS dividends (
    symbol        TEXT NOT NULL,
    ex_date       TEXT NOT NULL,
    pay_date      TEXT,
    amount        REAL,
    frequency     INTEGER,
    declared_date TEXT,
    recorded_at   TEXT,
    PRIMARY KEY (symbol, ex_date)
);
CREATE INDEX IF NOT EXISTS idx_div_ex ON dividends (ex_date, symbol);
CREATE TABLE IF NOT EXISTS coverage (
    symbol     TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    checked_on TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_LAST_RUN_KEY = "last_run_day"


# ── store ───────────────────────────────────────────────────────────────────

def init_db(db_path=None):
    """Open the dividends store, creating it if absent.

    ⚠ ``db_path=None`` resolved HERE, not ``db_path=DEFAULT_DB_PATH``: a
    default binds at ``def`` time, which would leave the path unpatchable
    (see ``shared/earnings.init_db``)."""
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


def _sym(symbol):
    """The upper-cased symbol, or ``""`` for anything that is not a usable string."""
    if not isinstance(symbol, str):
        return ""
    return symbol.strip().upper()


def _finite_amount(value):
    """A usable per-share amount or None. NaN / inf / bool / junk store NULL."""
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _int_or_none(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_DIGITS = frozenset("0123456789")


def _iso_date(value):
    """``YYYY-MM-DD`` or None. A timestamp keeps its date part, so
    ``"2026-10-31T00:00:00Z"`` and ``"2026-10-31"`` are ONE key and both fall
    inside a window ending that day; anything unparseable is None.

    STRICT: exactly ``YYYY-MM-DD`` (ASCII digits), then ``T``, a space, or the
    end of the string. So ``"2026-10-061"`` (a trailing digit), ``"20261006"``
    (basic format) and ``"2026-W40-1"`` (ISO week, which ``fromisoformat``
    would accept) are None rather than being silently read as some date. A
    ``date`` / ``datetime`` object works through ``str()``."""
    if value is None or isinstance(value, bool):
        return None
    s = str(value).strip()
    if len(s) < 10 or (len(s) > 10 and s[10] not in "T "):
        return None
    head = s[:10]
    if head[4] != "-" or head[7] != "-" or not all(
            ch in _DIGITS for ch in head[:4] + head[5:7] + head[8:]):
        return None
    try:
        return dt.date.fromisoformat(head).isoformat()
    except ValueError:
        return None


def _require_day(value, name):
    """``_iso_date(value)`` or ValueError — for an ARGUMENT, where an unusable
    date is a programming error, never a row to skip."""
    day = _iso_date(value)
    if not day:
        raise ValueError(f"unusable {name}: {value!r} (want YYYY-MM-DD or a date)")
    return day


def _row_tuple(r, now):
    """The stored tuple for one row, or None when it has no usable key."""
    sym, ex = _sym(r.get("symbol")), _iso_date(r.get("ex_date"))
    if not sym or not ex:
        return None
    return (sym, ex, _iso_date(r.get("pay_date")), _finite_amount(r.get("amount")),
            _int_or_none(r.get("frequency")), r.get("declared_date"), now)


_INSERT = ("INSERT OR REPLACE INTO dividends (symbol, ex_date, pay_date, amount, "
           "frequency, declared_date, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)")


def _dedupe(tuples):
    """One tuple per ``(symbol, ex_date)``, the LAST one winning — what
    ``INSERT OR REPLACE`` would leave — in first-seen order."""
    out = {}
    for t in tuples:
        out[(t[0], t[1])] = t
    return list(out.values())


def _now(now):
    return now or dt.datetime.now().isoformat(timespec="seconds")


def upsert(conn, rows, now=None):
    """Insert or replace dividend rows keyed on ``(symbol, ex_date)``.

    Only ADDS: a revised or cancelled dividend leaves its old row behind, which
    is why the writer uses :func:`replace_symbol` for a symbol it fetched.

    Rows without a usable symbol or ex_date are skipped. Rows sharing a
    ``(symbol, ex_date)`` key are collapsed first, the last one winning, and
    the return value is the number of DISTINCT rows written."""
    now = _now(now)
    data = _dedupe(t for t in (_row_tuple(r, now) for r in rows) if t)
    conn.executemany(_INSERT, data)
    conn.commit()
    return len(data)


def replace_symbol(conn, symbol, rows, today, now=None):
    """Make ``rows`` the whole forward calendar for ``symbol``, in ONE transaction.

    Deletes that symbol's rows with ``ex_date >= today`` and inserts ``rows``,
    so a revised date leaves one row and a suspended dividend (``rows=[]``)
    leaves none. Past rows are history and stay. A row naming another symbol
    is ignored. Call it only for a symbol whose fetch SUCCEEDED (``ok`` or
    ``none``) — an ``error`` symbol must keep what it had.

    It replaces the FORWARD calendar only, so a row with ``ex_date < today``
    is ignored (it would otherwise overwrite history the delete never
    touched). Rows sharing an ex_date are collapsed, the last one winning;
    the return value is the number of distinct rows written.

    ``today`` may be a ``YYYY-MM-DD`` string, a timestamp string or a
    ``date``. An unusable ``symbol`` or ``today`` raises ValueError and
    changes nothing — that is a caller bug, and returning 0 would read as
    "fetched, no dividend"."""
    sym = _sym(symbol)
    if not sym:
        raise ValueError(f"unusable symbol: {symbol!r}")
    day = _require_day(today, "today")
    now = _now(now)
    data = _dedupe(t for t in (_row_tuple(r, now) for r in rows)
                   if t and t[0] == sym and t[1] >= day)
    with conn:  # one transaction: commit both, or roll both back
        conn.execute("DELETE FROM dividends WHERE symbol = ? AND ex_date >= ?",
                     (sym, day))
        conn.executemany(_INSERT, data)
    return len(data)


def prune(conn, before):
    """Delete rows with ``ex_date < before``; returns how many went.

    An unusable ``before`` raises ValueError and deletes nothing."""
    day = _require_day(before, "before")
    with conn:
        cur = conn.execute("DELETE FROM dividends WHERE ex_date < ?", (day,))
    return cur.rowcount


_STATUSES = frozenset({"ok", "none", "error"})


def set_coverage(conn, statuses, day):
    """Record per-symbol coverage: ``ok`` / ``none`` / ``error``.

    Anything else is stored as ``error`` — an unrecognised status is not
    evidence of either answer, and must never read as "no dividend". That
    includes a non-string status (``["ok"]``, ``None``, ``3``), which is
    stored as ``error`` rather than raising."""
    conn.executemany(
        "INSERT OR REPLACE INTO coverage (symbol, status, checked_on) VALUES (?, ?, ?)",
        [(_sym(s), status if isinstance(status, str) and status in _STATUSES else "error",
          day)
         for s, status in statuses.items() if _sym(s)])
    conn.commit()


def set_last_run_day(conn, day):
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                 (_LAST_RUN_KEY, day))
    conn.commit()


# ── read ────────────────────────────────────────────────────────────────────

def _cursor(conn):
    # A cursor carrying its own row_factory, so a caller's plain connection
    # still yields name-indexable rows (the shared/earnings.lookup lesson).
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return cur


def upcoming(conn, symbols, start, end):
    """Rows with ``start <= ex_date <= end`` for ``symbols``, by ex_date then symbol.

    ``start`` / ``end`` go through the same normalisation as a stored date, so a
    ``date``, a ``datetime`` or a timestamp string bounds the window by its DATE
    (an ``end`` of ``"2026-10-31T23:59:59Z"`` includes the 31st). An unusable
    bound raises ValueError."""
    start = _require_day(start, "start")
    end = _require_day(end, "end")
    syms = sorted({_sym(s) for s in symbols if _sym(s)})
    if not syms:
        return []
    marks = ",".join("?" * len(syms))
    rows = _cursor(conn).execute(
        f"SELECT symbol, ex_date, pay_date, amount, frequency FROM dividends "
        f"WHERE symbol IN ({marks}) AND ex_date >= ? AND ex_date <= ? "
        f"ORDER BY ex_date ASC, symbol ASC", (*syms, start, end)).fetchall()
    return [dict(r) for r in rows]


def coverage(conn, symbols):
    """``{symbol: "ok" | "none" | "error" | "unknown"}`` — never checked is unknown."""
    wanted = [_sym(s) for s in symbols if _sym(s)]
    out = {s: "unknown" for s in wanted}
    if not wanted:
        return out
    marks = ",".join("?" * len(wanted))
    for r in _cursor(conn).execute(
            f"SELECT symbol, status FROM coverage WHERE symbol IN ({marks})", wanted):
        out[r["symbol"]] = r["status"]
    return out


def last_run_day(conn):
    row = _cursor(conn).execute(
        "SELECT value FROM meta WHERE key = ?", (_LAST_RUN_KEY,)).fetchone()
    return row["value"] if row else None
