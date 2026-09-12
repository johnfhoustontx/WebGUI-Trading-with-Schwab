"""Persistent IV / realized-vol history — the store behind a TRUE volatility rank.

Schwab serves no implied-volatility history, so IV rank and IV percentile can
only accumulate forward from the first run. Realized volatility, by contrast, is
derivable from the price history already being fetched, so RV rank is backfilled
on every run and is available immediately.

Storage is a single SQLite file keyed by ``(symbol, snapshot_date)``, so one
database serves an entire watchlist.

**Why it lives in ``shared/`` (moved here 2026-09-12, gap assessment C3).** The
store is written from two tiers that cannot import each other:
``services/trade_svc``'s Deep Dive, and ``options-scanner/scanner_engine.py``'s
scan pass, which is the only place that holds a 20-45 DTE chain per symbol
already. Duplicating a store's write path is how two writers come to disagree
about a schema. The precedent is exact: ``shared/earnings.py`` is the cross-tier
path to ``EARNINGS_CALENDAR_DB``, a store that likewise lives under
``services/trade_svc/data/``.

⚠ **It had 7 rows across just three days** (2026-08-04, 08-23, 08-25) for its
whole life before that move.
``record_snapshot`` was reached only from ``deepdive/engine.analyze_symbol``, so
the store filled only when somebody opened a Deep Dive report — built, tested,
*called*, and called by a surface nobody runs daily. ``run_full_scan`` now records
on every pass, at no Schwab cost.

⚠ **What this store's rank is NOT.** ``options-scanner/iv_analysis.py``'s
``iv_rank`` is a different quantity and has said so since 2026-04-19: it places
current ATM IV inside the 52-week *realized*-volatility distribution, i.e. a
variance-risk-premium reading, and exposes honest ``hv_rank`` aliases. That proxy
is the field measured to predict outcomes (gap assessment B2). Nothing here claims
to beat it — a year of ``cm30_iv`` is what makes that question answerable, which
is the C tier's stated purpose.
"""
import math
import sqlite3
import logging
import datetime as dt
from pathlib import Path

from repo_paths import IV_HISTORY_DB

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

#############################################
# CONSTANTS
#############################################

# ⚠ This was ``Path('./iv_history.db')`` - a RELATIVE default, so any caller that
# omitted the path would have written a stray database into whatever the process's
# working directory happened to be. Both existing callers passed the constant
# explicitly, so nothing leaked; now nothing can.
DEFAULT_DB_PATH = IV_HISTORY_DB
DEFAULT_LOOKBACK_DAYS = 252
MIN_SAMPLES_FOR_RANK = 20
TARGET_DTE = 30
TRADING_DAYS = 252

SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_snapshots (
    symbol           TEXT NOT NULL,
    snapshot_date    TEXT NOT NULL,
    spot             REAL,
    cm30_iv          REAL,
    front_iv         REAL,
    front_dte        INTEGER,
    rvol_20d         REAL,
    rvol_60d         REAL,
    vrp              REAL,
    put_call_oi      REAL,
    term_slope       REAL,
    net_gex          REAL,
    captured_at      TEXT NOT NULL,
    PRIMARY KEY (symbol, snapshot_date)
);

CREATE TABLE IF NOT EXISTS rv_history (
    symbol       TEXT NOT NULL,
    bar_date     TEXT NOT NULL,
    close        REAL,
    rvol_20d     REAL,
    rvol_60d     REAL,
    PRIMARY KEY (symbol, bar_date)
);

CREATE INDEX IF NOT EXISTS idx_iv_symbol_date
    ON iv_snapshots (symbol, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_rv_symbol_date
    ON rv_history (symbol, bar_date DESC);
"""


#############################################
# DATABASE
#############################################

def init_db(db_path=DEFAULT_DB_PATH):
    """Open the history database, creating the schema if needed

    Args:
        db_path: path to the SQLite file

    Returns:
        sqlite3.Connection
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()

    logger.debug(f'History DB ready at {db_path.resolve()}')
    return conn


def close_db(conn):
    """Commit and close"""
    if conn is not None:
        conn.commit()
        conn.close()


#############################################
# CONSTANT MATURITY IV
#############################################

def constant_maturity_iv(expirations, target_dte=TARGET_DTE):
    """Interpolate a constant-maturity ATM IV across expirations.

    Front-expiry IV is contaminated by DTE decay and by whichever earnings
    cycle happens to sit inside it, so comparing it day over day is noisy.
    Interpolating to a fixed 30-day tenor makes the series comparable.

    Interpolation is done in total-variance space (iv^2 * t), which is the
    correct linear domain, not in vol space.

    Args:
        expirations: list of dicts with 'dte' and 'atm_iv' keys
        target_dte: tenor to interpolate to, in days

    Returns:
        Interpolated IV as a percent, or None
    """
    points = [
        (float(e['dte']), float(e['atm_iv']))
        for e in expirations
        if e.get('dte') is not None and e.get('atm_iv') is not None and e['dte'] > 0
    ]
    if not points:
        return None

    points.sort(key=lambda p: p[0])

    # Exact or single-point cases
    if len(points) == 1:
        return points[0][1]

    for dte, iv in points:
        if abs(dte - target_dte) < 0.5:
            return iv

    # Outside the available range: clamp to the nearest tenor
    if target_dte <= points[0][0]:
        return points[0][1]
    if target_dte >= points[-1][0]:
        return points[-1][1]

    # Bracket the target and interpolate total variance
    for i in range(1, len(points)):
        dte_lo, iv_lo = points[i - 1]
        dte_hi, iv_hi = points[i]
        if dte_lo <= target_dte <= dte_hi:
            var_lo = (iv_lo / 100.0) ** 2 * dte_lo
            var_hi = (iv_hi / 100.0) ** 2 * dte_hi
            weight = (target_dte - dte_lo) / (dte_hi - dte_lo)
            var_target = var_lo + weight * (var_hi - var_lo)
            if var_target <= 0 or target_dte <= 0:
                return None
            return math.sqrt(var_target / target_dte) * 100.0

    return None


#: Strikes within this fraction of spot count as "at the money" for the ladder
#: below. 3% mirrors the Deep Dive's own ATM band so the two agree about what ATM
#: means - the whole value of this series is that it is comparable.
ATM_BAND_PCT = 0.03

#: Schwab's "no data" volatility sentinel. ⚠ A documented live value, not a
#: theoretical one: ``flow_skew`` accepted it as a usable IV until that was fixed,
#: and a -999 averaged into an ATM band would store a NEGATIVE volatility.
_IV_SENTINEL_FLOOR = 0.0
_IV_SENTINEL_CEIL = 500.0


def atm_iv_ladder(chain, spot=None):
    """``[{"dte", "atm_iv"}]`` per expiry from a raw chain, sorted by DTE.

    The input ``constant_maturity_iv`` wants, built with a light dict walk rather
    than the Deep Dive's DataFrame flatten - this one runs inside a scan pass,
    once per symbol.

    ATM is the **mean IV of strikes within** :data:`ATM_BAND_PCT` **of spot**,
    merged across the put and call maps, matching the Deep Dive's band so the two
    producers of this column cannot disagree about what ATM means.

    ``spot`` is preferred over the chain's own ``underlyingPrice`` when supplied,
    because the caller usually has a better one: ``run_full_scan`` already priced
    every other decision in its loop off ``prices[symbol]``, and using a second
    source here would let the snapshot's ATM band sit on a different spot than the
    scan's. The chain's value is the fallback - and it is genuinely absent on some
    payloads, so the fallback is not decoration.

    Every degradation is silent and empty rather than raising: this feeds a
    volatility snapshot that must never be able to break a scan. An expiry with no
    usable IV is **dropped, not zeroed** (a 0 vol would pin the bottom of the
    ranked range for a year), and ``dte <= 0`` is excluded to match
    ``constant_maturity_iv``'s own rule.
    """
    if not isinstance(chain, dict):
        return []
    if spot is None or isinstance(spot, bool) or not isinstance(spot, (int, float)):
        spot = chain.get("underlyingPrice")
    if isinstance(spot, bool) or not isinstance(spot, (int, float)):
        return []
    spot = float(spot)
    if not math.isfinite(spot) or spot <= 0:
        return []
    band = max(spot * ATM_BAND_PCT, 0.01)

    per_dte: dict = {}
    for map_key in ("putExpDateMap", "callExpDateMap"):
        for exp_key, strikes in (chain.get(map_key) or {}).items():
            dte = _dte_of(exp_key)
            if dte is None or dte <= 0:
                continue
            for raw_strike, contracts in (strikes or {}).items():
                try:
                    strike = float(raw_strike)
                except (TypeError, ValueError):
                    continue
                if abs(strike - spot) > band:
                    continue
                iv = _usable_iv((contracts or [{}])[0].get("volatility")
                                if contracts else None)
                if iv is not None:
                    per_dte.setdefault(dte, []).append(iv)

    return sorted(({"dte": dte, "atm_iv": sum(ivs) / len(ivs)}
                   for dte, ivs in per_dte.items() if ivs),
                  key=lambda e: e["dte"])


def _dte_of(exp_key):
    """DTE out of a ``"<date>:<dte>"`` expiry-map key, or ``None``."""
    try:
        return int(str(exp_key).split(":")[1])
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def _usable_iv(value):
    """A real percent volatility, or ``None`` for a sentinel / junk / non-finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    iv = float(value)
    if not math.isfinite(iv):
        return None
    return iv if _IV_SENTINEL_FLOOR < iv < _IV_SENTINEL_CEIL else None


def cm30_from_chain(chain, spot=None, target_dte=TARGET_DTE):
    """``(cm30_iv, basis)`` for a raw chain. ``basis`` is what makes it safe to store.

    ``basis`` is one of ``"exact"`` (an expiry sits on the target tenor),
    ``"interpolated"`` (the ladder brackets it), ``"clamped"`` (it does not - the
    value is the nearest tenor's IV wearing the target's name), or ``None``.

    ⚠ **The clamp is the reason this wrapper exists.**
    ``constant_maturity_iv`` documents clamping to the nearest tenor, which is
    right for a one-off report and **corrupting for a ranked series**: a file
    mixing 7-day and 30-day readings under one column is not rankable, and the
    damage is invisible because the number still looks like an IV. Worse,
    ``_rank_from_series`` takes ``min``/``max``, so ONE contaminated reading pins
    the bottom of the range for a year. Callers writing a series must record only
    ``exact`` or ``interpolated`` - a missing sample costs one day, a wrong one
    costs the range.

    Measured 2026-09-12: the ``+20..+45`` DTE window ``run_full_scan`` already
    fetches brackets 30 DTE for every symbol tried and gives the identical value
    to a ``today..+60`` fetch, while the ``today..+7`` collector window would
    clamp every time.
    """
    ladder = atm_iv_ladder(chain, spot=spot)
    if not ladder:
        return None, None
    value = constant_maturity_iv(ladder, target_dte=target_dte)
    if value is None:
        return None, None
    dtes = [e["dte"] for e in ladder]
    if any(abs(d - target_dte) < 0.5 for d in dtes):
        return value, "exact"
    if min(dtes) <= target_dte <= max(dtes):
        return value, "interpolated"
    return value, "clamped"


#############################################
# WRITES
#############################################

def record_snapshot(conn, symbol, spot, opts, tech, snapshot_date=None):
    """Upsert today's volatility snapshot for one symbol

    Args:
        conn: sqlite connection
        symbol: ticker
        spot: underlying price
        opts: options analytics dict
        tech: technicals dict
        snapshot_date: override the date (YYYY-MM-DD), defaults to today

    Returns:
        The cm30 IV that was stored, or None
    """
    if conn is None:
        return None

    snapshot_date = snapshot_date or dt.date.today().isoformat()
    symbol = symbol.lstrip('$').upper()

    cm30 = opts.get('cm30_iv')
    front = opts.get('front', {}) or {}

    conn.execute(
        """
        INSERT INTO iv_snapshots
            (symbol, snapshot_date, spot, cm30_iv, front_iv, front_dte,
             rvol_20d, rvol_60d, vrp, put_call_oi, term_slope, net_gex, captured_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, snapshot_date) DO UPDATE SET
            spot=excluded.spot,
            cm30_iv=excluded.cm30_iv,
            front_iv=excluded.front_iv,
            front_dte=excluded.front_dte,
            rvol_20d=excluded.rvol_20d,
            rvol_60d=excluded.rvol_60d,
            vrp=excluded.vrp,
            put_call_oi=excluded.put_call_oi,
            term_slope=excluded.term_slope,
            net_gex=excluded.net_gex,
            captured_at=excluded.captured_at
        """,
        (
            symbol,
            snapshot_date,
            spot,
            cm30,
            front.get('atm_iv'),
            front.get('dte'),
            tech.get('rvol_20d'),
            tech.get('rvol_60d'),
            opts.get('vrp_20d'),
            opts.get('put_call_oi_ratio'),
            opts.get('term_slope'),
            opts.get('net_gex'),
            dt.datetime.now().isoformat(timespec='seconds'),
        ),
    )
    conn.commit()
    return cm30


def _as_candle_frame(candles):
    """Normalise a price history to a close-indexed DataFrame, or ``None``.

    ⚠ **Two real shapes reach this store, and only one used to work.** The Deep
    Dive holds a DataFrame indexed by date; ``scanner_engine.fetch_price_history``
    returns the RAW Schwab payload - a dict with a ``candles`` list whose stamps
    are epoch **milliseconds**. Passing the raw shape to the DataFrame-only code
    raised ``AttributeError`` on ``.empty``, which the caller's guard swallowed:
    a backfill that read like a feature and wrote nothing. Normalising here rather
    than at one call site keeps ONE realized-vol computation, which is the whole
    reason this store is shared.

    Daily Schwab bars are stamped at midnight **Central** (05:00/06:00 UTC), so
    taking the date off the naive-UTC conversion keeps the session date - the
    documented reason the Expected Move path's epoch handling comes out right.
    """
    if candles is None:
        return None
    if hasattr(candles, "empty"):          # already a DataFrame
        return candles
    rows = candles.get("candles") if isinstance(candles, dict) else candles
    if not isinstance(rows, (list, tuple)) or not rows:
        return None
    stamps, closes = [], []
    for bar in rows:
        if not isinstance(bar, dict):
            continue
        close = bar.get("close")
        ts = bar.get("datetime")
        if not isinstance(close, (int, float)) or isinstance(close, bool):
            continue
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            continue
        if not (math.isfinite(close) and math.isfinite(ts)) or close <= 0:
            continue
        stamps.append(pd.to_datetime(int(ts), unit="ms"))
        closes.append(float(close))
    if not closes:
        return None
    frame = pd.DataFrame({"close": closes}, index=pd.DatetimeIndex(stamps))
    # A day can appear twice if a caller mixes intraday bars in; the last wins,
    # matching the store's own (symbol, bar_date) upsert.
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def backfill_rv(conn, symbol, candles):
    """Recompute and store the realized-vol series from price history.

    Cheap and idempotent - runs on every invocation so the RV series stays
    current without a separate job.

    Args:
        conn: sqlite connection
        symbol: ticker
        candles: either an OHLCV DataFrame indexed by date, or the RAW Schwab
            ``/pricehistory`` payload (``{"candles": [{close, datetime, ...}]}``)
            / a bare candle list. See :func:`_as_candle_frame`.

    Returns:
        Number of rows written
    """
    candles = _as_candle_frame(candles)
    if conn is None or candles is None or candles.empty:
        return 0

    symbol = symbol.lstrip('$').upper()
    close = candles['close']
    log_returns = np.log(close / close.shift())

    frame = pd.DataFrame({
        'close': close,
        'rvol_20d': log_returns.rolling(20).std() * math.sqrt(TRADING_DAYS) * 100,
        'rvol_60d': log_returns.rolling(60).std() * math.sqrt(TRADING_DAYS) * 100,
    }).dropna(subset=['rvol_20d'])

    if frame.empty:
        return 0

    rows = [
        (
            symbol,
            idx.strftime('%Y-%m-%d'),
            float(row.close),
            float(row.rvol_20d),
            float(row.rvol_60d) if not pd.isna(row.rvol_60d) else None,
        )
        for idx, row in frame.iterrows()
    ]

    conn.executemany(
        """
        INSERT INTO rv_history (symbol, bar_date, close, rvol_20d, rvol_60d)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(symbol, bar_date) DO UPDATE SET
            close=excluded.close,
            rvol_20d=excluded.rvol_20d,
            rvol_60d=excluded.rvol_60d
        """,
        rows,
    )
    conn.commit()
    logger.debug(f'{symbol}: backfilled {len(rows)} RV rows')
    return len(rows)


#############################################
# RANKING
#############################################

def _rank_from_series(values, current):
    """Shared rank / percentile math

    Rank is where the current value sits between the period low and high.
    Percentile is the share of observations at or below the current value.
    They answer different questions and can diverge sharply on a skewed
    distribution, which is exactly when the difference matters.
    """
    series = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not series or current is None:
        return None

    lo, hi = min(series), max(series)
    count = len(series)

    rank = None
    if hi > lo:
        rank = (current - lo) / (hi - lo) * 100.0
        rank = max(0.0, min(100.0, rank))

    below = sum(1 for v in series if v <= current)
    percentile = below / count * 100.0

    return {
        'current': current,
        'rank': rank,
        'percentile': percentile,
        'low': lo,
        'high': hi,
        'mean': sum(series) / count,
        'samples': count,
        'sufficient': count >= MIN_SAMPLES_FOR_RANK,
    }


def iv_rank(conn, symbol, current_iv, lookback_days=DEFAULT_LOOKBACK_DAYS):
    """IV rank and percentile from accumulated snapshots

    Returns None until enough snapshots exist. Builds forward from first run.
    """
    if conn is None or current_iv is None:
        return None

    symbol = symbol.lstrip('$').upper()
    cutoff = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()

    rows = conn.execute(
        """
        SELECT cm30_iv FROM iv_snapshots
        WHERE symbol = ? AND snapshot_date >= ? AND cm30_iv IS NOT NULL
        ORDER BY snapshot_date
        """,
        (symbol, cutoff),
    ).fetchall()

    result = _rank_from_series([r['cm30_iv'] for r in rows], current_iv)
    if result:
        result['lookback_days'] = lookback_days
        result['basis'] = 'cm30_iv'
    return result


def rv_rank(conn, symbol, current_rv, lookback_days=DEFAULT_LOOKBACK_DAYS, window='rvol_20d'):
    """Realized-vol rank and percentile - available immediately via backfill"""
    if conn is None or current_rv is None:
        return None
    if window not in ('rvol_20d', 'rvol_60d'):
        raise ValueError(f'Unsupported window: {window}')

    symbol = symbol.lstrip('$').upper()
    cutoff = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()

    rows = conn.execute(
        f"""
        SELECT {window} AS value FROM rv_history
        WHERE symbol = ? AND bar_date >= ? AND {window} IS NOT NULL
        ORDER BY bar_date
        """,
        (symbol, cutoff),
    ).fetchall()

    result = _rank_from_series([r['value'] for r in rows], current_rv)
    if result:
        result['lookback_days'] = lookback_days
        result['basis'] = window
    return result


def snapshot_count(conn, symbol):
    """How many IV snapshots exist for a symbol - shows history maturity"""
    if conn is None:
        return 0
    symbol = symbol.lstrip('$').upper()
    row = conn.execute(
        'SELECT COUNT(*) AS n FROM iv_snapshots WHERE symbol = ? AND cm30_iv IS NOT NULL',
        (symbol,),
    ).fetchone()
    return int(row['n']) if row else 0

