"""EPS-surprise history from Alpha Vantage's EARNINGS endpoint.

Schwab's `/instruments?projection=fundamental` carries 56 fields and no
earnings surprises, which is why the Investor scorecard's `earnings_traj`
component scored a permanent 0 for every symbol — 15 of its 100 points
unreachable before any company was examined. Alpha Vantage publishes the
history: probed live 2026-08-23, MU returned **122 quarters** each carrying
`reportedEPS`, `estimatedEPS`, `surprise` and `surprisePercentage`.

**Two conversions this module exists to own**, because the vendor's shape and
the scorer's contract disagree on both:

* **Order.** The vendor returns NEWEST first. ``score_earnings_surprise_streak``
  reads a CHRONOLOGICAL list — its ``[-1]`` is the most recent quarter.
* **Units.** The vendor gives PERCENT (``18.6368``). The scorer compares
  against ``0.05``, i.e. FRACTIONS. Feeding percentages straight through would
  make every trivial beat clear a 5% bar by a factor of a hundred.

**The request budget is the other half of the design.** The free tier allows 25
calls a day, and `earnings_calendar`'s bulk CSV already takes one — that
calendar feeds the earnings GATE, which is the more important consumer. So this
module caps itself below the shared allowance, records every call it makes
(including the ones that fail, because the vendor counted those too), and
refuses rather than borrowing from the calendar's headroom.

Quarterly data cannot move for three months, so a symbol is re-asked at most
every ``REFRESH_AFTER_DAYS``. A symbol the vendor does not cover is REMEMBERED
as uncovered — otherwise every uncovered name costs a call on every analysis.
"""
import datetime as dt
import json
import logging
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

from services.trade_svc.earnings_calendar import api_key

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "earnings_history.db"

_BASE = "https://www.alphavantage.co/query"

# Below the vendor's 25/day so `earnings_calendar` always has room for its one
# bulk call. The gate it feeds matters more than any single scorecard row.
DAILY_BUDGET = 20

# Quarterly data. Re-asking sooner spends the allowance on numbers that cannot
# have changed.
REFRESH_AFTER_DAYS = 30

# What the scorer reads. Four quarters decide the streak; a few more make the
# record legible on the Evidence screen without carrying 122 rows around.
DEFAULT_LIMIT = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS eps_surprises (
    symbol              TEXT NOT NULL,
    fiscal_date_ending  TEXT NOT NULL,
    reported_date       TEXT,
    reported_eps        REAL,
    estimated_eps       REAL,
    surprise            REAL,
    surprise_pct        REAL,
    recorded_at         TEXT NOT NULL,
    PRIMARY KEY (symbol, fiscal_date_ending)
);
CREATE INDEX IF NOT EXISTS idx_eps_symbol ON eps_surprises (symbol, fiscal_date_ending);

-- A symbol we asked about and got nothing for is a REAL answer worth keeping,
-- or every uncovered name costs a call on every analysis.
CREATE TABLE IF NOT EXISTS eps_fetches (
    symbol       TEXT PRIMARY KEY,
    fetched_at   TEXT NOT NULL,
    quarters     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS eps_calls (
    day    TEXT PRIMARY KEY,
    n      INTEGER NOT NULL
);
"""


def init_db(db_path=DEFAULT_DB_PATH):
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _float_or_none(v):
    """Alpha Vantage writes absent numbers as the STRING "None"."""
    s = str(v).strip() if v is not None else ""
    if not s or s.lower() in ("none", "null", "-", "nan"):
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_earnings(body):
    """Vendor JSON -> quarter rows, vendor order preserved. Never raises.

    A quarter with no usable surprise is DROPPED rather than defaulted: a 0.0
    there would read as "met expectations exactly", which is a real and quite
    different reading from "no estimate existed"."""
    try:
        d = json.loads(body)
    except Exception:
        return []
    if not isinstance(d, dict):
        return []
    out = []
    for q in (d.get("quarterlyEarnings") or []):
        if not isinstance(q, dict):
            continue
        fde = str(q.get("fiscalDateEnding") or "").strip()
        pct = _float_or_none(q.get("surprisePercentage"))
        if not fde or pct is None:
            continue
        out.append({
            "fiscal_date_ending": fde,
            "reported_date": str(q.get("reportedDate") or "").strip() or None,
            "reported_eps": _float_or_none(q.get("reportedEPS")),
            "estimated_eps": _float_or_none(q.get("estimatedEPS")),
            "surprise": _float_or_none(q.get("surprise")),
            "surprise_pct": pct,
        })
    return out


def is_transient(body):
    """Is this reply a refusal rather than an answer about coverage?

    ⚠ Alpha Vantage throttles at 5 calls a MINUTE as well as 25 a day, and a
    throttled reply carries a ``Note``/``Information`` string instead of a
    ``quarterlyEarnings`` key. Parsed, that is an empty list — byte-identical
    in effect to a symbol the vendor genuinely does not cover.

    That distinction is load-bearing because an empty answer is REMEMBERED, so
    uncovered symbols are not re-asked daily. Without this, one throttle cached
    "no earnings history for NVDA" for 30 days. NVDA has 109 quarters; it was
    measured doing exactly that on the first live run.

    The test is the ENVELOPE, not the rows: an answer that CARRIES the
    quarterly block is a real answer, however empty."""
    try:
        d = json.loads(body)
    except Exception:
        return True                      # an HTML error page says nothing
    if not isinstance(d, dict):
        return True
    if "quarterlyEarnings" in d:
        return False                     # the vendor answered, empty or not
    return True


def store(conn, symbol, rows):
    """Upsert a symbol's quarters and record that we asked. Never raises."""
    sym = (symbol or "").strip().upper()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        if rows:
            conn.executemany(
                "INSERT INTO eps_surprises (symbol, fiscal_date_ending, "
                "reported_date, reported_eps, estimated_eps, surprise, "
                "surprise_pct, recorded_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol, fiscal_date_ending) DO UPDATE SET "
                "reported_date=excluded.reported_date, "
                "reported_eps=excluded.reported_eps, "
                "estimated_eps=excluded.estimated_eps, "
                "surprise=excluded.surprise, "
                "surprise_pct=excluded.surprise_pct, "
                "recorded_at=excluded.recorded_at",
                [(sym, r["fiscal_date_ending"], r.get("reported_date"),
                  r.get("reported_eps"), r.get("estimated_eps"),
                  r.get("surprise"), r["surprise_pct"], now) for r in rows])
        conn.execute(
            "INSERT INTO eps_fetches (symbol, fetched_at, quarters) "
            "VALUES (?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
            "fetched_at=excluded.fetched_at, quarters=excluded.quarters",
            (sym, now, len(rows)))
        conn.commit()
        return True
    except Exception:
        logger.warning("earnings_history.store failed for %s", sym,
                       exc_info=True)
        return False


def _cursor(conn):
    """A cursor carrying its OWN row factory, so reads are correct however the
    caller opened the store. See `earnings_calendar.lookup` — the same trap
    silently reported an empty calendar for every symbol."""
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return cur


def surprise_fractions(conn, symbol, limit=DEFAULT_LIMIT):
    """The most recent ``limit`` quarters as CHRONOLOGICAL FRACTIONS, or None.

    ``None`` means "never asked" — distinct from ``[]``, which the scorer would
    read as a symbol with no streak. Fractions because
    ``score_earnings_surprise_streak`` compares against 0.05."""
    sym = (symbol or "").strip().upper()
    try:
        cur = _cursor(conn)
        seen = cur.execute("SELECT quarters FROM eps_fetches WHERE symbol = ?",
                           (sym,)).fetchone()
        if seen is None:
            return None
        rows = cur.execute(
            "SELECT surprise_pct FROM eps_surprises WHERE symbol = ? "
            "ORDER BY fiscal_date_ending DESC LIMIT ?", (sym, int(limit))
        ).fetchall()
        return [r["surprise_pct"] / 100.0 for r in reversed(rows)]
    except Exception:
        logger.warning("earnings_history.surprise_fractions failed for %s",
                       sym, exc_info=True)
        return None


def is_due(conn, symbol, now=None):
    """Should this symbol be re-asked? True when never asked or stale."""
    now = now or dt.datetime.now(dt.timezone.utc)
    sym = (symbol or "").strip().upper()
    try:
        row = _cursor(conn).execute(
            "SELECT fetched_at FROM eps_fetches WHERE symbol = ?",
            (sym,)).fetchone()
        if row is None:
            return True
        when = dt.datetime.fromisoformat(row["fetched_at"])
        return (now - when).days >= REFRESH_AFTER_DAYS
    except Exception:
        logger.warning("earnings_history.is_due failed for %s", sym,
                       exc_info=True)
        return False        # do not spend a call on a store we cannot read


def budget_left(conn, today=None):
    today = (today or dt.date.today()).isoformat()
    try:
        row = _cursor(conn).execute(
            "SELECT n FROM eps_calls WHERE day = ?", (today,)).fetchone()
        used = int(row["n"]) if row else 0
    except Exception:
        logger.warning("earnings_history.budget_left failed", exc_info=True)
        return 0            # unreadable ledger -> spend nothing
    return max(0, DAILY_BUDGET - used)


def note_call(conn, today=None):
    """Record a call we MADE, successful or not — the vendor counted it."""
    day = (today or dt.date.today()).isoformat()
    try:
        conn.execute(
            "INSERT INTO eps_calls (day, n) VALUES (?, 1) "
            "ON CONFLICT(day) DO UPDATE SET n = n + 1", (day,))
        conn.commit()
    except Exception:
        logger.warning("earnings_history.note_call failed", exc_info=True)


def _fetch(symbol):
    """Isolated so tests never touch the network."""
    key = api_key()
    if not key:
        raise RuntimeError("no Alpha Vantage key")
    url = _BASE + "?" + urllib.parse.urlencode(
        {"function": "EARNINGS", "symbol": (symbol or "").strip().upper(),
         "apikey": key})
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def refresh(conn, symbol, today=None):
    """Fetch and store one symbol's history. True when stored. Never raises.

    Spends at most one call, and only when the budget allows. A FAILED fetch
    still spends its call: the vendor counted the request, and pretending
    otherwise is how a retry loop empties the allowance the earnings gate
    depends on."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return False
    # Checked BEFORE the budget is charged. `_fetch` raises on a missing key
    # without contacting the vendor, so charging for it spends an allowance the
    # vendor never saw — on a machine with no key that drains the whole day in
    # `DAILY_BUDGET` analyses, for nothing. Found on the first live run, in a
    # worktree, which is exactly where the gitignored key is absent.
    if not api_key():
        logger.info("earnings_history: no Alpha Vantage key, skipping %s", sym)
        return False
    if budget_left(conn, today=today) <= 0:
        logger.info("earnings_history: daily budget spent, skipping %s", sym)
        return False
    note_call(conn, today=today)
    try:
        body = _fetch(sym)
    except Exception:
        logger.warning("earnings_history.refresh fetch failed for %s", sym,
                       exc_info=True)
        return False
    # A refusal is not evidence about coverage, and must NOT be stored — an
    # empty result is remembered for REFRESH_AFTER_DAYS, so caching a throttle
    # would hide a symbol's real history for a month.
    if is_transient(body):
        logger.info("earnings_history: vendor refused %s (throttle or error); "
                    "not caching an empty result", sym)
        return False
    rows = parse_earnings(body)
    # An empty ANSWER is stored, so an uncovered symbol is not re-asked daily.
    store(conn, sym, rows)
    return bool(rows)
