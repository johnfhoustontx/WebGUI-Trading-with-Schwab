"""Short interest from FINRA, joined to Schwab's float.

**Why this module exists.** Schwab's ``/instruments?projection=fundamental``
ships ``shortIntToFloat`` and ``shortIntDayToCover`` and populates NEITHER —
measured live 2026-08-22, both read 0.0 for AAPL, TSLA, GME and CVNA alike
while ``peRatio`` and ``returnOnEquity`` in the same payload were correct. So
the short side needs a real source, and a scraper was ruled out.

FINRA publishes the regulatory filing itself. ``POST`` to
``api.finra.org/data/group/otcMarket/name/consolidatedShortInterest``, no
credentials required today, and its Specific Terms for Equity Data permit
"non-commercial personal or professional use" and creating derivative data,
with attribution. Despite the dataset's "otcMarket" path the coverage is
consolidated — NYSE, Nasdaq, ARCA and AMEX all appear.

⚠ **It returns CSV**, not the JSON its docs suggest (``content-type:
text/plain``, every field quoted) — verified live. It also honours
``Accept: application/json``; CSV is used here because it is the default and
one less thing to negotiate.

**FINRA has no float**, so percent-of-float needs a denominator. Schwab's
``marketCapFloat`` is that denominator: despite the name it is float in
SHARES, not dollars (cross-checked to 4-5 significant figures against an
independent source). Days-to-cover, by contrast, FINRA computes itself, so it
needs no join and is immune to the float disagreement — which is why the
squeeze test fires on either leg. See :func:`squeeze_flag`.

**Cadence:** bi-monthly, published 9-12 calendar days after settlement, so the
freshest reading is 8-27 days old. That is fine for a coarse gate and it is the
best any OFFICIAL source can do — anything advertising daily short interest is
a stock-loan model, not a measurement. FINRA has a weekly-reporting rule change
pending; if it lands, only the refresh cadence here changes.

Attribution requirement: FINRA is the owner and source of this data.
"""
import csv
import datetime as dt
import io
import logging
import sqlite3
from pathlib import Path

from repo_paths import SHORT_INTEREST_DB

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = SHORT_INTEREST_DB

FINRA_URL = ("https://api.finra.org/data/group/otcMarket/name/"
             "consolidatedShortInterest")
FINRA_PAGE = 5000          # rows per request; the whole US cycle is ~22k
FINRA_MAX_PAGES = 10       # backstop so a shape change cannot loop forever

# Either leg fires the gate — see squeeze_flag for why it is OR, not AND.
SQUEEZE_PCT_OF_FLOAT = 15.0
SQUEEZE_DAYS_TO_COVER = 10.0

# Above this, the numerator and denominator are in different share units (a
# split between settlement and today). Not a squeeze — a unit mismatch.
_MAX_CREDIBLE_PCT = 100.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS short_interest (
    symbol            TEXT NOT NULL,
    settlement_date   TEXT NOT NULL,
    short_qty         INTEGER,
    days_to_cover     REAL,
    avg_daily_volume  INTEGER,
    recorded_at       TEXT,
    PRIMARY KEY (symbol, settlement_date)
);
CREATE INDEX IF NOT EXISTS idx_si_symbol ON short_interest (symbol, settlement_date DESC);
"""


# ── pure ────────────────────────────────────────────────────────────────────

def _int_or_none(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _float_or_none(v):
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return f if f == f else None          # reject NaN


def parse_cycle(text):
    """FINRA's quoted CSV -> normalized row dicts. Never raises.

    A malformed row is skipped rather than fatal: one bad line must not cost
    the other twenty-two thousand."""
    out = []
    try:
        reader = csv.DictReader(io.StringIO(text or ""))
        for raw in reader:
            symbol = (raw.get("symbolCode") or "").strip().upper()
            short_qty = _int_or_none(raw.get("currentShortPositionQuantity"))
            settle = (raw.get("settlementDate") or "").strip()
            if not symbol or short_qty is None or not settle:
                continue
            out.append({
                "symbol": symbol,
                "short_qty": short_qty,
                "days_to_cover": _float_or_none(raw.get("daysToCoverQuantity")),
                "avg_daily_volume": _int_or_none(raw.get("averageDailyVolumeQuantity")),
                "settlement_date": settle,
            })
    except Exception:
        logger.debug("short_interest.parse_cycle failed", exc_info=True)
    return out


def percent_of_float(short_qty, float_shares):
    """Percent of float short, or None when it cannot be stated honestly.

    Returns None above :data:`_MAX_CREDIBLE_PCT`. FINRA is **not**
    split-adjusted and its ``stockSplitFlag`` only appears in the cycle AFTER
    a split, so a reverse split between settlement and today leaves a pre-split
    numerator over a post-split float — measured live, one name computed to
    783%. Reporting that would fire the squeeze gate hardest exactly when the
    data is meaningless, which is the same failure shape as a missing reading
    clamping to an extreme."""
    if short_qty is None or float_shares is None:
        return None
    try:
        short_qty = float(short_qty)
        float_shares = float(float_shares)
    except (TypeError, ValueError):
        return None
    if float_shares <= 0 or short_qty < 0:
        return None
    pct = 100.0 * short_qty / float_shares
    if pct != pct or pct > _MAX_CREDIBLE_PCT:
        return None
    return pct


def squeeze_flag(pct_of_float, days_to_cover):
    """``(fires, reason)`` for the short-side squeeze gate.

    **Either leg fires it, deliberately.** Float is the contested term: one
    live name gave 89% / 51% / 12% depending on whose float you take, straddling
    any single threshold in both directions. Days-to-cover is FINRA's own
    computation over its own numerator and never touches float, so it is the
    robust leg. Requiring BOTH would let a float disagreement veto a real
    signal; requiring either keeps the robust leg live when the contested one
    is unusable.

    No data fires nothing, and says so — absence is neither safety nor risk,
    and the caller needs to be able to tell the gate could not be evaluated."""
    if pct_of_float is None and days_to_cover is None:
        return False, "no short-interest data for this symbol"
    reasons = []
    if pct_of_float is not None and pct_of_float >= SQUEEZE_PCT_OF_FLOAT:
        reasons.append(f"{pct_of_float:.1f}% of float short")
    if days_to_cover is not None and days_to_cover >= SQUEEZE_DAYS_TO_COVER:
        reasons.append(f"{days_to_cover:.1f} days to cover")
    return (True, "; ".join(reasons)) if reasons else (False, "")


# ── store ───────────────────────────────────────────────────────────────────

def init_db(db_path=DEFAULT_DB_PATH):
    db_path = Path(db_path)
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


def store_cycle(conn, rows):
    """Upsert one settlement cycle. True/False; never raises."""
    try:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        conn.executemany(
            "INSERT INTO short_interest "
            "(symbol, settlement_date, short_qty, days_to_cover, avg_daily_volume, recorded_at) "
            "VALUES (:symbol, :settlement_date, :short_qty, :days_to_cover, "
            ":avg_daily_volume, :recorded_at) "
            "ON CONFLICT(symbol, settlement_date) DO UPDATE SET "
            "short_qty=excluded.short_qty, days_to_cover=excluded.days_to_cover, "
            "avg_daily_volume=excluded.avg_daily_volume, recorded_at=excluded.recorded_at",
            [{**r, "recorded_at": now} for r in rows])
        conn.commit()
        return True
    except Exception:
        logger.debug("short_interest.store_cycle failed", exc_info=True)
        return False


def lookup(conn, symbol):
    """The newest stored row for ``symbol``, or None. Case-insensitive."""
    try:
        return conn.execute(
            "SELECT * FROM short_interest WHERE symbol = ? "
            "ORDER BY settlement_date DESC LIMIT 1",
            ((symbol or "").strip().upper(),)).fetchone()
    except Exception:
        return None


def latest_settlement(conn):
    """Newest settlement date held, or None."""
    try:
        row = conn.execute(
            "SELECT MAX(settlement_date) AS d FROM short_interest").fetchone()
        return row["d"] if row and row["d"] else None
    except Exception:
        return None


def for_symbol(conn, symbol, float_shares):
    """``{pct_of_float, days_to_cover, short_qty, settlement_date, squeeze,
    squeeze_reason}`` for one symbol, or None when FINRA does not carry it.

    None means the symbol was not in the cycle at all — most often a RENAME
    (Block's ``SQ`` became ``XYZ``, and FINRA keys on the current symbol), which
    is a silent miss unless the caller notices."""
    row = lookup(conn, symbol)
    if row is None:
        return None
    pct = percent_of_float(row["short_qty"], float_shares)
    dtc = row["days_to_cover"]
    fires, why = squeeze_flag(pct, dtc)
    return {"pct_of_float": pct, "days_to_cover": dtc,
            "short_qty": row["short_qty"],
            "settlement_date": row["settlement_date"],
            "squeeze": fires, "squeeze_reason": why}


# ── network (isolated so tests never touch it) ──────────────────────────────

def fetch_cycle(settlement_date, session=None):
    """Every row for one settlement date. Returns [] on any failure.

    ``settlementDate`` is a partition key, so filtering on it pulls a whole
    cycle cheaply. ⚠ Do NOT substitute ``limit`` + client-side sorting: the
    default ordering is not newest-first, and a naive limit silently returns
    an OLD cycle (this was hit while verifying — a limit of 200 returned rows
    four months stale)."""
    import requests
    sess = session or requests
    rows, offset = [], 0
    try:
        for _ in range(FINRA_MAX_PAGES):
            resp = sess.post(FINRA_URL, timeout=60, json={
                "limit": FINRA_PAGE, "offset": offset,
                "compareFilters": [{"fieldName": "settlementDate",
                                    "fieldValue": settlement_date,
                                    "compareType": "EQUAL"}]})
            if resp.status_code != 200:
                break
            page = parse_cycle(resp.text)
            rows.extend(page)
            if len(page) < FINRA_PAGE:
                break
            offset += FINRA_PAGE
    except Exception:
        logger.debug("short_interest.fetch_cycle failed", exc_info=True)
    return rows


def discover_latest_settlement(session=None, symbol="AAPL"):
    """Newest settlement date FINRA is serving, or None.

    Asks for one heavily-covered symbol's rows and takes the max date, rather
    than guessing from the published calendar — the calendar gives settlement
    dates, not the date each becomes AVAILABLE, and those differ by 9-12 days."""
    import requests
    sess = session or requests
    try:
        resp = sess.post(FINRA_URL, timeout=60, json={
            "limit": 500,
            "compareFilters": [{"fieldName": "symbolCode",
                                "fieldValue": symbol, "compareType": "EQUAL"}]})
        if resp.status_code != 200:
            return None
        dates = [r["settlement_date"] for r in parse_cycle(resp.text)]
        return max(dates) if dates else None
    except Exception:
        logger.debug("short_interest.discover_latest_settlement failed", exc_info=True)
        return None


def refresh(conn, session=None):
    """Fetch the newest cycle if the store does not already hold it.

    Returns the settlement date now held, or None. Bi-monthly data, so this is
    a no-op on all but ~24 runs a year."""
    latest = discover_latest_settlement(session=session)
    if not latest:
        return latest_settlement(conn)
    if latest_settlement(conn) == latest:
        return latest
    rows = fetch_cycle(latest, session=session)
    if rows:
        store_cycle(conn, rows)
    return latest_settlement(conn)
