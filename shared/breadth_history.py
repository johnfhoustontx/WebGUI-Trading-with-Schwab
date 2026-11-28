"""The daily breadth history: one value per series per session, kept forever.

A SQLite file at ``repo_paths.BREADTH_DB`` (``shared/data/breadth.db``), one
table, ``daily(series, day, value, source)``. The market cycle
(docs/plans/2026-11-27-market-cycle-design.md) will be decided from it.

**Kept forever and in the nightly backup; never add it to ``DB_EXCLUDE``.** Its
own-count series (our new highs / lows) exist nowhere else once a day has
passed, and Schwab may not always serve twenty years of daily breadth.

**Why it lives in ``shared/``.** Two writers that cannot import each other share
it: ``services/market_svc/breadth_daily.py`` (the daily row) and
``tools/breadth_backfill.py`` (the history). A store must have one write path,
and this is it: the ``shared/iv_history.py`` precedent.

**The vocabulary is fixed** (``SERIES``, ``SOURCES``). A row outside it is a
programming error and raises. Two rules ride on the names:

- A ``tv_`` series comes from TradingView, through a file a Claude session
  pulled with the TradingView connector. It is for the study only: no value of
  it may ever reach a cache view or a site file, because ``cache:*`` is readable
  by the public process. Its universe (the NYSE) also differs from our own
  counts (all US stocks at $5 and up), so the two are never spliced.
- Schwab's daily history is the reference. A day is first written from the
  closing quotes (``schwab_close_quote``) and replaced by the history bar
  (``schwab_history``) the next morning. Nothing but another history bar may
  replace a history value.

A row more than two sessions old is locked: only the backfill's ``--force``
changes it. Filling a day that has no row is never refused.
"""
import datetime as _dt
import math
import re
import sqlite3
from pathlib import Path

from repo_paths import BREADTH_DB
from shared import market_calendar as _mc
from shared.numeric import finite

DEFAULT_DB_PATH = BREADTH_DB

SOURCES = ("schwab_history", "schwab_close_quote", "own_count", "tradingview")
_HISTORY = "schwab_history"
_TRADINGVIEW = "tradingview"
_TV_PREFIX = "tv_"

SERIES = {
    # Schwab: history bars, or the closing quote until the morning correction.
    "nyse_adv": "NYSE advancing issues",
    "nyse_dec": "NYSE declining issues",
    "nyse_trin": "NYSE Arms index (TRIN)",
    "nyse_up_share": "Share of NYSE volume in rising stocks, 0 to 1",
    "spx_close": "S&P 500 close",
    "spx_high": "S&P 500 high",
    "spx_low": "S&P 500 low",
    "vix_close": "VIX close",
    "vix3m_close": "3-month VIX close",
    "spy_close": "SPY close",
    "spy_volume": "SPY volume, shares",
    "rsp_close": "RSP (equal-weight S&P 500) close",
    # Our own counts (cache:market:new_highs): US stocks at $5 and up.
    "us_universe": "Stocks counted for new highs and lows",
    "us_52w_highs": "New 52-week highs",
    "us_52w_lows": "New 52-week lows",
    "us_13w_highs": "New 13-week highs",
    "us_13w_lows": "New 13-week lows",
    "us_4w_highs": "New 4-week highs",
    "us_4w_lows": "New 4-week lows",
    # TradingView, for the study only.
    "tv_nyse_adv": "NYSE advancing issues (TradingView USI:ADV)",
    "tv_nyse_dec": "NYSE declining issues (USI:DECL)",
    "tv_nyse_up_vol": "NYSE up volume (USI:UVOL)",
    "tv_nyse_down_vol": "NYSE down volume (USI:DVOL)",
    "tv_pct_above_20": "Percent of US stocks above their 20-day average (INDEX:MMTW)",
    "tv_pct_above_50": "Percent of US stocks above their 50-day average (INDEX:MMFI)",
    "tv_pct_above_200": "Percent of US stocks above their 200-day average (INDEX:MMTH)",
    "tv_sp500_pct_above_50": "Percent of S&P 500 stocks above their 50-day average (INDEX:S5FI)",
    "tv_sp500_pct_above_200": "Percent of S&P 500 stocks above their 200-day average (INDEX:S5TH)",
    "tv_nyse_52w_highs": "NYSE 52-week highs (INDEX:MAHN)",
    "tv_nyse_52w_lows": "NYSE 52-week lows (INDEX:MALN)",
    "tv_nyse_3m_highs": "NYSE 3-month highs (INDEX:M3HN)",
    "tv_nyse_3m_lows": "NYSE 3-month lows (INDEX:M3LN)",
    "tv_nyse_1m_highs": "NYSE 1-month highs (INDEX:M1HN)",
    "tv_nyse_1m_lows": "NYSE 1-month lows (INDEX:M1LN)",
    "tv_equity_put_call": "CBOE equity put/call ratio (USI:PCCE)",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily (
    series TEXT NOT NULL,
    day    TEXT NOT NULL,
    value  REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (series, day)
)"""

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def connect(db_path=None) -> sqlite3.Connection:
    """Open the store, creating the file and its table. ``db_path=None`` is
    resolved HERE, so a test that redirects ``DEFAULT_DB_PATH`` is obeyed."""
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def locked(day: _dt.date, today: _dt.date) -> bool:
    """Whether ``day`` is more than two sessions before ``today``."""
    return _mc.next_trading_day(_mc.next_trading_day(day)) < today


def _check_vocabulary(row) -> None:
    series, _day, _value, source = row
    if series not in SERIES:
        raise ValueError(f"unknown breadth series {series!r}")
    if source not in SOURCES:
        raise ValueError(f"unknown breadth source {source!r}")
    if series.startswith(_TV_PREFIX) != (source == _TRADINGVIEW):
        raise ValueError(f"series {series!r} cannot come from {source!r}")


def _as_day(text):
    if not isinstance(text, str) or not _DAY.match(text):
        return None
    try:
        return _dt.date.fromisoformat(text)
    except ValueError:
        return None


def upsert(conn, rows, today: _dt.date, force: bool = False) -> dict:
    """Write ``rows`` (``(series, day, value, source)``) in one transaction and
    say what happened to each. A row outside the vocabulary raises before
    anything is written."""
    rows = list(rows)
    for row in rows:
        _check_vocabulary(row)
    out = {"written": 0, "unchanged": 0, "locked": 0, "outranked": 0, "invalid": 0}
    with conn:
        for series, day_text, value, source in rows:
            day, value = _as_day(day_text), finite(value)
            if day is None or value is None:
                out["invalid"] += 1
                continue
            stored = conn.execute("SELECT value, source FROM daily WHERE series=? AND day=?",
                                  (series, day_text)).fetchone()
            if stored is not None:
                was, was_source = stored
                if was_source == source and math.isclose(was, value, rel_tol=1e-9):
                    out["unchanged"] += 1
                    continue
                if was_source == _HISTORY and source != _HISTORY:
                    out["outranked"] += 1
                    continue
                if locked(day, today) and not force:
                    out["locked"] += 1
                    continue
            conn.execute("INSERT OR REPLACE INTO daily (series, day, value, source) "
                         "VALUES (?, ?, ?, ?)", (series, day_text, value, source))
            out["written"] += 1
    return out


def day_row(conn, day: str) -> dict[str, tuple[float, str]]:
    """Every series stored for ``day``: ``{series: (value, source)}``."""
    return {s: (v, src) for s, v, src in conn.execute(
        "SELECT series, value, source FROM daily WHERE day=? ORDER BY series", (day,))}


def series_values(conn, series: str, start: str | None = None,
                  end: str | None = None) -> list[tuple[str, float]]:
    """``[(day, value)]`` for one series, oldest first, inside the optional window."""
    sql, args = "SELECT day, value FROM daily WHERE series=?", [series]
    if start is not None:
        sql, args = sql + " AND day>=?", args + [start]
    if end is not None:
        sql, args = sql + " AND day<=?", args + [end]
    return list(conn.execute(sql + " ORDER BY day", args))


def coverage(conn) -> list[tuple[str, str, str, int]]:
    """``[(series, first day, last day, rows)]``, by series name."""
    return list(conn.execute("SELECT series, MIN(day), MAX(day), COUNT(*) FROM daily "
                             "GROUP BY series ORDER BY series"))
