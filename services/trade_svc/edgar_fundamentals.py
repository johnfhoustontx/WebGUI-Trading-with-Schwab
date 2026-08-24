"""Free cash flow from SEC EDGAR's XBRL company facts.

``Fundamentals.fcf`` has always been ``None`` — Schwab's fundamentals payload
carries no cash flow — so the Investor gate that caps a stock at HOLD on
negative free cash flow **paired with a missed quarter** has never once fired.
EDGAR files both components, so free cash flow is computable from the primary
record rather than a vendor's derivation:

    free cash flow = operating cash flow − capital expenditure

**What EDGAR cannot do, and this module does not pretend to.** It holds no
analyst estimates, so it cannot produce earnings SURPRISES — a surprise is
reported minus estimate. Probed live 2026-08-23 across all 629 ``us-gaap``
concepts Micron files: the only "estimate" hits are accounting disclosures
(unrecognised tax benefits, transaction-price changes). Surprises stay with
Alpha Vantage (`earnings_history`). EDGAR also reports **GAAP** figures, while
estimates are set against ADJUSTED earnings — pairing the two would manufacture
a surprise that is really just the GAAP-to-adjusted gap.

**Why annual rather than trailing-twelve-month.** The gate asks a structural
question — does this business generate cash — and a full fiscal year from a
10-K is unambiguous, whereas stitching four quarters together depends on
per-filer quarterly tagging that varies far more. The cost is staleness of up
to a year, which is acceptable for a question about structure.

**No API key and no daily quota**, unlike the earnings vendor: SEC asks only
for a declared User-Agent and a fair-use ceiling of 10 requests/second. Set
``EDGAR_USER_AGENT`` to your own contact string — SEC's fair-access policy asks
for one, and the default here deliberately carries no personal data.
"""
import datetime as dt
import gzip
import json
import logging
import os
import sqlite3
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "edgar.db"

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# SEC asks for a contact string. The default is deliberately impersonal — set
# the env var to your own, per https://www.sec.gov/os/webmaster-faq#code-support
_UA_ENV = "EDGAR_USER_AGENT"
_DEFAULT_UA = "WebGUI-Trading/1.0 (private research tool)"

# SEC's published fair-use ceiling is 10 requests/second. One in-process gap is
# plenty for a tool that fetches one symbol at a time.
_MIN_INTERVAL_SEC = 0.15
_last_call = [0.0]

# Annual figures from 10-K filings. A quarter is the natural refresh rhythm.
REFRESH_AFTER_DAYS = 90

# The ticker map is ~10,400 entries and changes slowly.
TICKER_MAP_TTL_DAYS = 7

# Operating cash flow, in the order filers use them.
_OCF_CONCEPTS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)

# Capital expenditure. The first covers almost everything; the rest are what
# utilities, REITs and some industrials file instead.
_CAPEX_CONCEPTS = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsForCapitalImprovements",
    "PaymentsToAcquireOtherPropertyPlantAndEquipment",
)

# A "year" is 330-400 days. Anything shorter is a quarter, and reading one as
# annual understates operating cash flow four-fold — enough to flip the sign of
# free cash flow and fire a gate that should not fire.
_MIN_YEAR_DAYS = 330
_MAX_YEAR_DAYS = 400

SCHEMA = """
CREATE TABLE IF NOT EXISTS fcf_annual (
    symbol       TEXT NOT NULL,
    fiscal_year  INTEGER NOT NULL,
    ocf          REAL NOT NULL,
    capex        REAL NOT NULL,
    fcf          REAL NOT NULL,
    period_end   TEXT,
    form         TEXT,
    filed        TEXT,
    recorded_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, fiscal_year)
);

CREATE TABLE IF NOT EXISTS edgar_fetches (
    symbol      TEXT PRIMARY KEY,
    fetched_at  TEXT NOT NULL,
    years       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS edgar_meta (
    k  TEXT PRIMARY KEY,
    v  TEXT NOT NULL,
    at TEXT NOT NULL
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


def _cursor(conn):
    """A cursor with its OWN row factory, so reads are correct however the
    caller opened the store — see `earnings_calendar.lookup` for the trap."""
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return cur


# ── parsing ─────────────────────────────────────────────────────────────────

def _annual_by_period(gaap, concepts):
    """``{period_end: (value, form, filed)}`` for FULL-year facts.

    Keyed on the period END DATE, never on ``fy``. EDGAR's ``fy`` is the
    fiscal year of the FILING, and a 10-K carries comparatives — NVDA's
    ``fy=2010`` spans period-ends 2008 through 2011 — so keying on it
    collapses several years into one and picks an arbitrary winner.

    MERGES every candidate concept rather than taking the first present one.
    Filers change tags over time: NVDA's
    ``PaymentsToAcquirePropertyPlantAndEquipment`` stops at 2011 and later
    years live under a different name, so first-wins pinned it to 2011."""
    out = {}
    for name in concepts:
        block = gaap.get(name)
        if not block:
            continue
        for r in ((block or {}).get("units", {}) or {}).get("USD", []) or []:
            try:
                start_s, end_s = r.get("start"), r.get("end")
                if not start_s or not end_s:
                    continue
                a = dt.date.fromisoformat(start_s)
                b = dt.date.fromisoformat(end_s)
                if not (_MIN_YEAR_DAYS <= (b - a).days <= _MAX_YEAR_DAYS):
                    continue                  # a quarter, not a year
                val = float(r.get("val"))
            except (TypeError, ValueError):
                continue
            # Later filings restate; the newest `filed` for a period wins.
            prev = out.get(end_s)
            if prev is None or (r.get("filed") or "") >= (prev[2] or ""):
                out[end_s] = (val, r.get("form"), r.get("filed"))
    return out


def parse_annual_fcf(body):
    """EDGAR companyfacts JSON -> chronological annual free-cash-flow rows.

    A period missing EITHER component is DROPPED. Treating an absent capex as
    zero would make free cash flow equal operating cash flow, which for a
    capital-intensive filer turns a large negative into a large positive —
    precisely the sign the gate reads. Never raises."""
    try:
        d = json.loads(body)
    except Exception:
        return []
    if not isinstance(d, dict):
        return []
    gaap = ((d.get("facts") or {}).get("us-gaap") or {})
    ocf = _annual_by_period(gaap, _OCF_CONCEPTS)
    capex = _annual_by_period(gaap, _CAPEX_CONCEPTS)
    out = []
    for end_s in sorted(set(ocf) & set(capex)):
        o, form, filed = ocf[end_s]
        c = capex[end_s][0]
        out.append({
            # The year of the PERIOD, read off its end date.
            "fiscal_year": dt.date.fromisoformat(end_s).year,
            "ocf": o, "capex": c, "fcf": o - c,
            "period_end": end_s, "form": form, "filed": filed})
    return out


def parse_ticker_map(body):
    """SEC's ticker file -> ``{TICKER: zero-padded CIK}``. Never raises."""
    try:
        d = json.loads(body)
    except Exception:
        return {}
    out = {}
    for v in (d.values() if isinstance(d, dict) else []):
        try:
            out[str(v["ticker"]).strip().upper()] = f"{int(v['cik_str']):010d}"
        except (KeyError, TypeError, ValueError):
            continue
    return out


def cik_for(symbol, mapping):
    """CIK for a ticker, tolerating class-share spellings.

    EDGAR writes ``BRK-B`` where most feeds write ``BRK.B`` or ``BRK/B``."""
    s = (symbol or "").strip().upper()
    if not s:
        return None
    for cand in (s, s.replace(".", "-"), s.replace("/", "-"),
                 s.replace("-", ".")):
        if cand in mapping:
            return mapping[cand]
    return None


# ── storage ─────────────────────────────────────────────────────────────────

def store(conn, symbol, rows):
    """Upsert a symbol's annual rows and record that we asked. Never raises."""
    sym = (symbol or "").strip().upper()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        if rows:
            conn.executemany(
                "INSERT INTO fcf_annual (symbol, fiscal_year, ocf, capex, fcf, "
                "period_end, form, filed, recorded_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol, fiscal_year) DO UPDATE SET "
                "ocf=excluded.ocf, capex=excluded.capex, fcf=excluded.fcf, "
                "period_end=excluded.period_end, form=excluded.form, "
                "filed=excluded.filed, recorded_at=excluded.recorded_at",
                [(sym, r["fiscal_year"], r["ocf"], r["capex"], r["fcf"],
                  r.get("period_end"), r.get("form"), r.get("filed"), now)
                 for r in rows])
        conn.execute(
            "INSERT INTO edgar_fetches (symbol, fetched_at, years) VALUES (?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET fetched_at=excluded.fetched_at, "
            "years=excluded.years", (sym, now, len(rows)))
        conn.commit()
        return True
    except Exception:
        logger.warning("edgar_fundamentals.store failed for %s", sym,
                       exc_info=True)
        return False


def latest_fcf(conn, symbol):
    """The most recent fiscal year's free cash flow, or None.

    Returns the whole row so a caller can say WHICH year it is — a figure up to
    a year old should be attributable, not silently current."""
    sym = (symbol or "").strip().upper()
    try:
        row = _cursor(conn).execute(
            "SELECT fiscal_year, ocf, capex, fcf, period_end, form, filed "
            "FROM fcf_annual WHERE symbol = ? "
            "ORDER BY fiscal_year DESC LIMIT 1", (sym,)).fetchone()
        return dict(row) if row else None
    except Exception:
        logger.warning("edgar_fundamentals.latest_fcf failed for %s", sym,
                       exc_info=True)
        return None


def is_due(conn, symbol, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    sym = (symbol or "").strip().upper()
    try:
        row = _cursor(conn).execute(
            "SELECT fetched_at FROM edgar_fetches WHERE symbol = ?",
            (sym,)).fetchone()
        if row is None:
            return True
        return (now - dt.datetime.fromisoformat(row["fetched_at"])).days \
            >= REFRESH_AFTER_DAYS
    except Exception:
        logger.warning("edgar_fundamentals.is_due failed for %s", sym,
                       exc_info=True)
        return False


# ── network (isolated so tests never touch it) ──────────────────────────────

def _user_agent():
    return (os.environ.get(_UA_ENV) or "").strip() or _DEFAULT_UA


def _get(url):
    # SEC publishes a 10 requests/second ceiling; space calls rather than rely
    # on being under it by luck.
    gap = time.monotonic() - _last_call[0]
    if gap < _MIN_INTERVAL_SEC:
        time.sleep(_MIN_INTERVAL_SEC - gap)
    _last_call[0] = time.monotonic()
    # ⚠ "gzip" ALONE. SEC's CDN answers 403 to `Accept-Encoding: gzip, deflate`
    # and 200 to `gzip` — measured directly, same URL, same User-Agent, one
    # header apart. Nothing documents it and no unit test could find it; the
    # only symptom is a Forbidden that looks like a UA policy or a rate limit.
    req = urllib.request.Request(
        url, headers={"User-Agent": _user_agent(),
                      "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


def _fetch_ticker_map():
    return _get(_TICKER_MAP_URL)


def _fetch_facts(cik):
    return _get(_FACTS_URL.format(cik=cik))


def _ticker_map(conn):
    """The ticker->CIK map, cached in the store for ``TICKER_MAP_TTL_DAYS``."""
    try:
        row = _cursor(conn).execute(
            "SELECT v, at FROM edgar_meta WHERE k = 'ticker_map'").fetchone()
        if row:
            age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(row["at"])
            if age.days < TICKER_MAP_TTL_DAYS:
                cached = parse_ticker_map(row["v"])
                if cached:
                    return cached
    except Exception:
        logger.warning("edgar_fundamentals: ticker map cache unreadable",
                       exc_info=True)
    body = _fetch_ticker_map()
    mapping = parse_ticker_map(body)
    if mapping:
        try:
            conn.execute(
                "INSERT INTO edgar_meta (k, v, at) VALUES ('ticker_map', ?, ?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v, at=excluded.at",
                (body, dt.datetime.now(dt.timezone.utc).isoformat()))
            conn.commit()
        except Exception:
            logger.warning("edgar_fundamentals: could not cache ticker map",
                           exc_info=True)
    return mapping


def refresh(conn, symbol):
    """Fetch and store one symbol's annual free cash flow. Never raises.

    A transport failure is NOT stored: the same rule the earnings history
    learned, since an empty result is remembered and a 503 is not evidence
    that a company files nothing."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return False
    try:
        mapping = _ticker_map(conn)
    except Exception:
        logger.warning("edgar_fundamentals: ticker map fetch failed",
                       exc_info=True)
        return False
    if not mapping:
        # ⚠ An EMPTY map is a transport failure, not an answer about anyone.
        # Without this, `cik_for` returns None for every symbol and the branch
        # below writes "not an SEC filer" — for 90 days. Measured live: SEC
        # answered 403 to a burst of map fetches, and one throttled minute
        # would have branded the whole universe unfilable until November.
        logger.info("edgar_fundamentals: no ticker map available, skipping %s",
                    sym)
        return False
    cik = cik_for(sym, mapping)
    if not cik:
        # A POPULATED map that lacks the ticker is a real answer — an ETF, an
        # index, a foreign line. Remembered so it is not looked up again on
        # every analysis.
        logger.info("edgar_fundamentals: %s is not in SEC's ticker map", sym)
        store(conn, sym, [])
        return False
    try:
        body = _fetch_facts(cik)
    except Exception:
        logger.warning("edgar_fundamentals: facts fetch failed for %s (CIK %s)",
                       sym, cik, exc_info=True)
        return False
    rows = parse_annual_fcf(body)
    store(conn, sym, rows)
    return bool(rows)
