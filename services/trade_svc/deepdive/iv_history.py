"""
IVHistory - persistent IV / realized-vol history store for EquityDeepDive
Version: 1.0.0
Last Updated: 2026-08-03

Schwab serves no implied-volatility history, so IV rank and IV percentile can
only accumulate forward from the first run. Realized volatility, by contrast,
is derivable from the price history already being fetched, so RV rank is
backfilled on every run and is available immediately.

Storage is a single SQLite file keyed by (symbol, snapshot_date), so one
database serves an entire watchlist.

Version 1.0.0 Changes:
- Initial implementation
"""
import math
import sqlite3
import logging
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

#############################################
# CONSTANTS
#############################################

DEFAULT_DB_PATH = Path('./iv_history.db')
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


def backfill_rv(conn, symbol, candles):
    """Recompute and store the realized-vol series from price history.

    Cheap and idempotent - runs on every invocation so the RV series stays
    current without a separate job.

    Args:
        conn: sqlite connection
        symbol: ticker
        candles: OHLCV DataFrame indexed by date

    Returns:
        Number of rows written
    """
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

