"""Forward earnings dates from Alpha Vantage.

**Why a vendor at all.** Schwab's ``/instruments`` carries no earnings date, so
the gate that matters most for a multi-week single-stock hold — is there a
report inside the horizon? — has never been able to fire. And there is no
OFFICIAL alternative: SEC Form 8-K Item 2.02 is retrospective, carrying the
release as it happens with no field for a scheduled future date, and no exchange
or regulator publishes a forward calendar. Every forward earnings date in
existence is a commercial research product.

**Why the free tier is enough.** ``EARNINGS_CALENDAR`` returns ONE bulk CSV for
the entire market, not a row per request. Alpha Vantage's free tier allows 25
requests a day; this needs one a night, with 24 to spare.

⚠ **The 12-month horizon is requested deliberately.** A 3-month probe measured
INCOMPLETE — mega-caps reporting inside the window were simply absent — so the
shorter horizon would produce a gate that silently fails open on exactly the
names most likely to be traded.

⚠ **Alpha Vantage reports errors with HTTP 200.** A bad or missing key returns
an explanatory JSON/HTML note, not a 4xx. Parsed as CSV that yields zero rows,
which is why :func:`fetch_calendar` refuses to call at all without a key: an
empty calendar must never be mistaken for "nobody reports soon".

The key resolves like every other in this repo — env var first, then a
gitignored file under ``shared/`` — and its absence is never fatal. Without it
the earnings gate simply stays quiet, exactly as it was before this module.

**This module is now the WRITE half only.** ``lookup``, ``coverage``,
``days_to_earnings`` and the store helpers moved to ``shared/earnings.py`` when
``options_svc`` needed to read the same calendar for the 30-45 DTE income
window; they are re-exported below, so every caller of this module is
unaffected. The vendor credential, the HTTP call and the CSV parser stay here —
they belong to this service alone.
"""
import csv
import datetime as dt
import io
import logging
import os
from pathlib import Path

from repo_paths import SHARED_DIR

# The READ path lives in `shared/earnings.py` so `options_svc` can use it too —
# a Tier-2 service may not import another service's engines, but reading a
# shared store is not a cross-service import (`shared/market_calendar.py` and
# `shared/symbols.py` are the precedent). Re-exported HERE rather than copied,
# so `lookup`/`coverage`/`days_to_earnings` have exactly one implementation and
# every existing caller of this module keeps working unchanged.
from shared.earnings import (  # noqa: F401  (re-export)
    DEFAULT_DB_PATH,
    SCHEMA,
    close_db,
    coverage,
    days_to_earnings,
    init_db,
    lookup,
)

logger = logging.getLogger(__name__)

_ENV_VAR = "ALPHAVANTAGE_API_KEY"
_KEY_FILE = SHARED_DIR / "alphavantage_key.txt"

_BASE = "https://www.alphavantage.co/query"
HORIZON = "12month"


# ── key ─────────────────────────────────────────────────────────────────────

def api_key():
    """The Alpha Vantage key, or None. Never raises.

    Order: ``ALPHAVANTAGE_API_KEY`` env var → gitignored
    ``shared/alphavantage_key.txt``. Mirrors ``driver_svc.api_keys``."""
    key = os.environ.get(_ENV_VAR)
    if key and key.strip():
        return key.strip()
    try:
        p = Path(_KEY_FILE)
        if p.exists():
            text = p.read_text(encoding="utf-8").strip()
            return text or None
    except Exception:
        pass
    return None


# ── pure ────────────────────────────────────────────────────────────────────

def _iso_or_none(v):
    try:
        return dt.date.fromisoformat(str(v).strip()).isoformat()
    except (TypeError, ValueError):
        return None


def parse_calendar(text):
    """Alpha Vantage's bulk CSV -> row dicts. Never raises.

    A body that is not CSV at all (the HTTP-200 error note) yields no rows —
    which the caller must not confuse with a market where nobody reports."""
    out = []
    try:
        reader = csv.DictReader(io.StringIO(text or ""))
        if not reader.fieldnames or "symbol" not in reader.fieldnames:
            return []
        for raw in reader:
            symbol = (raw.get("symbol") or "").strip().upper()
            report = _iso_or_none(raw.get("reportDate"))
            if not symbol or not report:
                continue
            try:
                estimate = float(raw.get("estimate"))
            except (TypeError, ValueError):
                estimate = None
            out.append({"symbol": symbol, "report_date": report,
                        "fiscal_date_ending": (raw.get("fiscalDateEnding") or "").strip(),
                        "estimate": estimate})
    except Exception:
        logger.debug("earnings_calendar.parse_calendar failed", exc_info=True)
    return out


# ── store ───────────────────────────────────────────────────────────────────

def store_calendar(conn, rows):
    """Upsert calendar rows. True/False; never raises."""
    try:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        conn.executemany(
            "INSERT INTO earnings (symbol, report_date, fiscal_date_ending, "
            "estimate, recorded_at) VALUES (:symbol, :report_date, "
            ":fiscal_date_ending, :estimate, :recorded_at) "
            "ON CONFLICT(symbol, report_date) DO UPDATE SET "
            "fiscal_date_ending=excluded.fiscal_date_ending, "
            "estimate=excluded.estimate, recorded_at=excluded.recorded_at",
            [{**r, "recorded_at": now} for r in rows])
        conn.commit()
        return True
    except Exception:
        logger.debug("earnings_calendar.store_calendar failed", exc_info=True)
        return False


# ── network (isolated so tests never touch it) ──────────────────────────────

def _get(url):
    import requests
    return requests.get(url, timeout=60).text


def fetch_calendar(horizon=HORIZON):
    """The whole forward calendar as rows, or [] on any failure.

    Returns [] WITHOUT making a request when no key is configured: the call
    would answer HTTP 200 with an explanatory note that parses to zero rows,
    and an empty calendar must never be mistaken for a real one."""
    key = api_key()
    if not key:
        logger.info("earnings_calendar: no %s configured — earnings gate stays "
                    "quiet", _ENV_VAR)
        return []
    url = (f"{_BASE}?function=EARNINGS_CALENDAR&horizon={horizon}&apikey={key}")
    try:
        return parse_calendar(_get(url))
    except Exception:
        logger.debug("earnings_calendar.fetch_calendar failed", exc_info=True)
        return []


def refresh(conn, horizon=HORIZON):
    """Pull the calendar and store it. Returns the row count stored."""
    rows = fetch_calendar(horizon=horizon)
    if rows:
        store_calendar(conn, rows)
    return len(rows)
