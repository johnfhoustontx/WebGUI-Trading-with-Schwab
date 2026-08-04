"""
EquityDeepDive - Schwab API technical + fundamental + options deep dive
Version: 1.2.0
Last Updated: 2026-08-03

Pulls quote, fundamentals, daily price history and the full option chain from
the Schwab market data API (via SchwabProxy by default) and renders a single
readable analysis to stdout, to a self-contained HTML report, and optionally
to JSON for downstream consumption.

Works on any optionable symbol. Accepts multiple tickers or a watchlist file
and persists a per-symbol volatility history so IV rank accumulates over time.

Version 1.2.0 Changes:
- Fixed proxy routing: SchwabProxy exposes routes at root, not /marketdata/v1
- Proxy calls now go through /passthrough; the proxy's own /quotes route
  hardcodes fields=quote and drops the fundamental block entirely
- Dropped the 'fields' param so Schwab returns its full response
- Added a /health preflight that reports token state before any data call
- Added --proxy-native to use the proxy's dedicated routes instead

Version 1.1.0 Changes:
- Added constant-maturity 30-day ATM IV (variance-space interpolation)
- Added IV rank / percentile via the iv_history SQLite store
- Added realized-vol rank, backfilled from price history so it works day one
- Multi-symbol and watchlist-file support with a cross-symbol summary table
- Added --snapshot-only mode for a lightweight scheduled history-building job

Version 1.0.0 Changes:
- Initial implementation
"""
import os
import sys
import json
import math
import logging
import argparse
import datetime as dt
from pathlib import Path

import requests
import numpy as np
import pandas as pd

from repo_paths import PROXY_URL
from . import iv_history as ivh

#############################################
# LOGGING SETUP
#############################################

# NOTE: no logging.basicConfig here — the trade_svc scaffold owns root-logger setup;
# reconfiguring it at import would clobber the service's RotatingFileHandler.
logger = logging.getLogger(__name__)

#############################################
# CONSTANTS
#############################################

PROXY_BASE = PROXY_URL  # migrated: source the proxy base from repo_paths (:8100)
DIRECT_BASE = 'https://api.schwabapi.com'
MARKETDATA_PREFIX = '/marketdata/v1'

# SchwabProxy mounts its routes at the root (/quotes, /chains, /pricehistory)
# and its /quotes handler hardcodes fields=quote, which drops the fundamental
# block. /passthrough forwards verbatim to Schwab, so we use that instead.
PASSTHROUGH_ROUTE = '/passthrough'
PROXY_NATIVE_ROUTES = {'/quotes', '/chains', '/pricehistory'}

TOKEN_PATH = None  # direct mode unused in-service (always proxy)
DEFAULT_OUTPUT_DIR = Path('./reports')

# Schwab uses -999.0 as a "no value" sentinel on greeks
NA_SENTINEL = -999.0

# pricehistory period is restricted to these values by the proxy
VALID_PERIODS = [1, 2, 3, 4, 5, 10]

TRADING_DAYS = 252
INDEX_SYMBOLS = {'SPX', 'VIX', 'NDX', 'RUT', 'DJI', 'OEX'}

REQUEST_TIMEOUT = 30


#############################################
# SCHWAB CLIENT
#############################################

class SchwabClient:
    """Thin wrapper over the Schwab market data endpoints.

    Defaults to routing through SchwabProxy so this tool does not open a
    second OAuth token lifecycle. Set direct=True to hit Schwab directly
    using a bearer token read from tokens.json.
    """

    def __init__(self, direct=False, base_url=None, path_prefix=MARKETDATA_PREFIX,
                 token_path=TOKEN_PATH, proxy_native=False):
        self.direct = direct
        self.proxy_native = proxy_native
        self.base_url = (base_url or (DIRECT_BASE if direct else PROXY_BASE)).rstrip('/')
        self.path_prefix = path_prefix.rstrip('/') if direct else ''
        self.session = requests.Session()

        if direct:
            token = self._load_token(token_path)
            self.session.headers.update({'Authorization': f'Bearer {token}'})

        self.session.headers.update({'Accept': 'application/json'})

    @staticmethod
    def _load_token(token_path):
        """Read the access token out of tokens.json"""
        token_path = Path(token_path)
        if not token_path.exists():
            raise FileNotFoundError(
                f'Token file not found: {token_path}. '
                f'Run without --direct to route through SchwabProxy instead.'
            )
        with open(token_path, 'r') as fh:
            payload = json.load(fh)

        # tokens.json layouts vary; check the common shapes
        for path in (('access_token',), ('token', 'access_token'),
                     ('creation_timestamp', ), ('token_dictionary', 'access_token')):
            node = payload
            ok = True
            for key in path:
                if isinstance(node, dict) and key in node:
                    node = node[key]
                else:
                    ok = False
                    break
            if ok and isinstance(node, str):
                return node

        raise KeyError(f'Could not locate access_token in {token_path}')

    def _build_request(self, endpoint, params):
        """Resolve the URL and query params for the active transport mode

        Direct mode hits Schwab itself under /marketdata/v1.

        Proxy mode routes through /passthrough, which forwards verbatim. The
        proxy's dedicated routes are avoided because /quotes pins fields=quote
        and there is no /instruments route at all.

        --proxy-native opts back into the dedicated routes where they exist.
        """
        params = {k: v for k, v in (params or {}).items() if v is not None}

        if self.direct:
            return f'{self.base_url}{self.path_prefix}{endpoint}', params

        if self.proxy_native and endpoint in PROXY_NATIVE_ROUTES:
            return f'{self.base_url}{endpoint}', params

        # /passthrough parses params as comma-separated k=v pairs, so a comma
        # inside any value would be silently mangled into extra pairs.
        for key, value in params.items():
            if ',' in str(value):
                raise ValueError(
                    f"Cannot send '{key}={value}' through /passthrough: the proxy "
                    f'splits params on commas. Use --direct for this request.'
                )

        query = {'endpoint': endpoint}
        if params:
            query['params'] = ','.join(f'{k}={v}' for k, v in params.items())
        return f'{self.base_url}{PASSTHROUGH_ROUTE}', query

    def _get(self, endpoint, params=None):
        url, query = self._build_request(endpoint, params)
        logger.debug(f'GET {url} params={query}')
        try:
            resp = self.session.get(url, params=query, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f'Could not reach {self.base_url}. '
                f'Is SchwabProxy running on port 8100? Use --direct to bypass it.'
            )

        if resp.status_code == 401:
            raise PermissionError(
                'Schwab returned 401. The refresh token has likely expired '
                '(Schwab refresh tokens are 7 days). Re-run the proxy OAuth flow.'
            )
        if resp.status_code == 404 and not self.direct:
            raise ConnectionError(
                f'Proxy returned 404 for {url}. Expected SchwabProxy to expose '
                f'{PASSTHROUGH_ROUTE}. Check the proxy version, or use --direct.'
            )
        resp.raise_for_status()
        return resp.json()

    def check_health(self):
        """Preflight the proxy so token problems surface before any data call"""
        if self.direct:
            return None
        try:
            resp = self.session.get(f'{self.base_url}/health', timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning(f'Proxy health check failed: {exc}')
            return None

    #########################################
    # ENDPOINTS
    #########################################

    def get_quote(self, symbol):
        """Quote + fundamental + reference blocks for a single symbol"""
        # No 'fields' param: Schwab returns every root node when it is omitted,
        # which is what we need. Passing a comma-separated list would also break
        # the proxy's /passthrough param parser.
        data = self._get('/quotes', {'symbols': symbol})
        return data.get(symbol, data.get(symbol.lstrip('$'), {}))

    def get_fundamental(self, symbol):
        """Fundamental block via the instruments endpoint (backstop)"""
        data = self._get('/instruments', {
            'symbol': symbol.lstrip('$'),
            'projection': 'fundamental',
        })
        instruments = data.get('instruments', [])
        if instruments:
            return instruments[0].get('fundamental', {})
        return {}

    def get_price_history(self, symbol, years=1, frequency_type='daily', frequency=1):
        """Daily OHLCV candles"""
        if years not in VALID_PERIODS:
            years = min(VALID_PERIODS, key=lambda v: abs(v - years))
            logger.warning(f'period coerced to {years} (proxy allows {VALID_PERIODS})')

        data = self._get('/pricehistory', {
            'symbol': symbol,
            'periodType': 'year',
            'period': years,
            'frequencyType': frequency_type,
            'frequency': frequency,
            'needExtendedHoursData': 'false',
        })
        candles = data.get('candles', [])
        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame(candles)
        df['date'] = pd.to_datetime(df['datetime'], unit='ms')
        df = df.set_index('date').sort_index()
        return df[['open', 'high', 'low', 'close', 'volume']]

    def get_option_chain(self, symbol, strike_count=40, from_date=None, to_date=None):
        """Full option chain with greeks"""
        params = {
            'symbol': symbol.lstrip('$'),
            'contractType': 'ALL',
            'strikeCount': strike_count,
            'includeUnderlyingQuote': 'true',
            'strategy': 'SINGLE',
        }
        if from_date:
            params['fromDate'] = from_date
        if to_date:
            params['toDate'] = to_date
        return self._get('/chains', params)


#############################################
# HELPERS
#############################################

def normalize_symbol(symbol):
    """Index symbols require a $ prefix on Schwab"""
    symbol = symbol.upper().strip()
    if symbol.lstrip('$') in INDEX_SYMBOLS and not symbol.startswith('$'):
        return f'${symbol}'
    return symbol


def unwrap_quote(node):
    """Index quotes nest under a 'quote' sub-key; equities are mixed"""
    if not isinstance(node, dict):
        return {}
    merged = {}
    for key in ('quote', 'regular', 'reference', 'extended'):
        sub = node.get(key)
        if isinstance(sub, dict):
            merged.update(sub)
    top = {k: v for k, v in node.items() if not isinstance(v, dict)}
    merged.update(top)
    return merged


def clean(value, default=None):
    """Scrub Schwab's -999 sentinel and NaNs"""
    if value is None:
        return default
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return value
    if math.isnan(fval) or fval == NA_SENTINEL:
        return default
    return fval


def fmt(value, spec='.2f', suffix='', dash='n/a'):
    """Safe formatter for report output"""
    if value is None:
        return dash
    try:
        if isinstance(value, str):
            return value
        if math.isnan(float(value)):
            return dash
        return f'{float(value):{spec}}{suffix}'
    except (TypeError, ValueError):
        return str(value)


def fmt_big(value, dash='n/a'):
    """Human-readable large numbers"""
    if value is None:
        return dash
    try:
        value = float(value)
    except (TypeError, ValueError):
        return dash
    if math.isnan(value):
        return dash
    for threshold, unit in ((1e12, 'T'), (1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if abs(value) >= threshold:
            return f'{value / threshold:,.2f}{unit}'
    return f'{value:,.2f}'


#############################################
# TECHNICAL INDICATORS
#############################################

def sma(series, period):
    """Simple moving average"""
    if series is None or len(series) < period:
        return None
    return series.rolling(period).mean()


def ema(series, period):
    """Exponential moving average"""
    if series is None or len(series) < period:
        return None
    return series.ewm(span=period, adjust=False).mean()


def rsi(series, period=14):
    """Wilder's RSI"""
    if series is None or len(series) < period + 1:
        return None
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df, period=14):
    """Average true range"""
    if df is None or len(df) < period + 1:
        return None
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def macd(series, fast=12, slow=26, signal=9):
    """MACD line, signal line, histogram"""
    if series is None or len(series) < slow + signal:
        return None, None, None
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line


def bollinger(series, period=20, num_std=2.0):
    """Bollinger bands, %B and bandwidth"""
    if series is None or len(series) < period:
        return None, None, None, None, None
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    pct_b = (series - lower) / (upper - lower).replace(0, np.nan)
    bandwidth = (upper - lower) / mid.replace(0, np.nan) * 100
    return upper, mid, lower, pct_b, bandwidth


def realized_vol(series, period=20):
    """Annualized close-to-close realized volatility (%)"""
    if series is None or len(series) < period + 1:
        return None
    returns = np.log(series / series.shift())
    return returns.rolling(period).std() * math.sqrt(TRADING_DAYS) * 100


def find_pivots(df, left=5, right=5):
    """N-bar fractal swing highs and lows -> auto support/resistance"""
    if df is None or len(df) < left + right + 1:
        return [], []

    highs, lows = [], []
    high_vals = df['high'].values
    low_vals = df['low'].values
    dates = df.index

    for i in range(left, len(df) - right):
        window_high = high_vals[i - left:i + right + 1]
        window_low = low_vals[i - left:i + right + 1]
        if high_vals[i] == window_high.max():
            highs.append((dates[i], float(high_vals[i])))
        if low_vals[i] == window_low.min():
            lows.append((dates[i], float(low_vals[i])))

    return highs, lows


def volume_profile_poc(df, bins=50):
    """Point of control: price bucket with the most traded volume"""
    if df is None or df.empty:
        return None, None

    typical = (df['high'] + df['low'] + df['close']) / 3
    lo, hi = float(df['low'].min()), float(df['high'].max())
    if hi <= lo:
        return None, None

    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(typical.values, edges) - 1, 0, bins - 1)
    volumes = np.zeros(bins)
    np.add.at(volumes, idx, df['volume'].values)

    poc_bin = int(volumes.argmax())
    poc_price = (edges[poc_bin] + edges[poc_bin + 1]) / 2

    # Value area: expand contiguously outward from the POC until 70% of
    # volume is captured, taking the heavier neighbour at each step
    target = volumes.sum() * 0.70
    lower_bin = upper_bin = poc_bin
    running = volumes[poc_bin]

    while running < target and (lower_bin > 0 or upper_bin < bins - 1):
        below = volumes[lower_bin - 1] if lower_bin > 0 else -1.0
        above = volumes[upper_bin + 1] if upper_bin < bins - 1 else -1.0
        if above >= below:
            upper_bin += 1
            running += volumes[upper_bin]
        else:
            lower_bin -= 1
            running += volumes[lower_bin]

    va_low = edges[lower_bin]
    va_high = edges[upper_bin + 1]

    return float(poc_price), (float(va_low), float(va_high))


def analyze_technicals(df):
    """Full technical snapshot from daily candles

    Args:
        df: OHLCV DataFrame indexed by date

    Returns:
        dict of technical metrics
    """
    if df is None or df.empty:
        return {}

    close = df['close']
    last = float(close.iloc[-1])

    tech = {'last_close': last, 'bars': len(df)}

    # Moving averages
    for period in (20, 50, 200):
        series = sma(close, period)
        val = float(series.iloc[-1]) if series is not None and not pd.isna(series.iloc[-1]) else None
        tech[f'sma_{period}'] = val
        tech[f'dist_sma_{period}'] = ((last / val - 1) * 100) if val else None

    for period in (9, 21):
        series = ema(close, period)
        val = float(series.iloc[-1]) if series is not None and not pd.isna(series.iloc[-1]) else None
        tech[f'ema_{period}'] = val
        tech[f'dist_ema_{period}'] = ((last / val - 1) * 100) if val else None

    # Cross state
    if tech.get('sma_50') and tech.get('sma_200'):
        tech['ma_cross'] = 'Golden Cross' if tech['sma_50'] > tech['sma_200'] else 'Death Cross'
        sma50, sma200 = sma(close, 50), sma(close, 200)
        spread = (sma50 - sma200).dropna()
        if len(spread) > 1:
            sign = np.sign(spread.values)
            flips = np.where(np.diff(sign) != 0)[0]
            tech['bars_since_cross'] = int(len(spread) - flips[-1] - 1) if len(flips) else None

    # Momentum
    rsi_series = rsi(close, 14)
    tech['rsi_14'] = float(rsi_series.iloc[-1]) if rsi_series is not None else None

    macd_line, signal_line, hist = macd(close)
    if macd_line is not None:
        tech['macd'] = float(macd_line.iloc[-1])
        tech['macd_signal'] = float(signal_line.iloc[-1])
        tech['macd_hist'] = float(hist.iloc[-1])
        tech['macd_state'] = 'bullish' if tech['macd'] > tech['macd_signal'] else 'bearish'

    # Volatility
    atr_series = atr(df, 14)
    if atr_series is not None:
        tech['atr_14'] = float(atr_series.iloc[-1])
        tech['atr_pct'] = tech['atr_14'] / last * 100

    upper, mid, lower, pct_b, bandwidth = bollinger(close)
    if pct_b is not None:
        tech['bb_upper'] = float(upper.iloc[-1])
        tech['bb_lower'] = float(lower.iloc[-1])
        tech['bb_pct_b'] = float(pct_b.iloc[-1])
        tech['bb_bandwidth'] = float(bandwidth.iloc[-1])
        bw_series = bandwidth.dropna()
        if len(bw_series) > 20:
            tech['bb_squeeze'] = bool(bw_series.iloc[-1] <= bw_series.quantile(0.20))

    for period in (20, 60):
        rv = realized_vol(close, period)
        tech[f'rvol_{period}d'] = float(rv.iloc[-1]) if rv is not None and not pd.isna(rv.iloc[-1]) else None

    # Range position
    window = df.tail(TRADING_DAYS)
    hi_52 = float(window['high'].max())
    lo_52 = float(window['low'].min())
    tech['high_52w'] = hi_52
    tech['low_52w'] = lo_52
    tech['pct_off_high'] = (last / hi_52 - 1) * 100
    tech['pct_off_low'] = (last / lo_52 - 1) * 100
    tech['range_position'] = (last - lo_52) / (hi_52 - lo_52) * 100 if hi_52 > lo_52 else None

    # Drawdown
    running_max = close.cummax()
    dd = (close / running_max - 1) * 100
    tech['current_drawdown'] = float(dd.iloc[-1])
    tech['max_drawdown'] = float(dd.min())

    # Trailing returns
    for label, lookback in (('1w', 5), ('1m', 21), ('3m', 63), ('6m', 126), ('1y', 252)):
        if len(close) > lookback:
            tech[f'return_{label}'] = (last / float(close.iloc[-1 - lookback]) - 1) * 100

    # Volume
    tech['volume_last'] = float(df['volume'].iloc[-1])
    avg_vol_20 = df['volume'].tail(20).mean()
    tech['avg_volume_20d'] = float(avg_vol_20)
    tech['relative_volume'] = tech['volume_last'] / avg_vol_20 if avg_vol_20 else None

    # Structure
    poc, value_area = volume_profile_poc(df)
    tech['poc'] = poc
    tech['value_area'] = value_area

    pivot_highs, pivot_lows = find_pivots(df.tail(TRADING_DAYS))
    resistance = sorted({round(p, 2) for _, p in pivot_highs if p > last})[:4]
    support = sorted({round(p, 2) for _, p in pivot_lows if p < last}, reverse=True)[:4]
    tech['resistance_levels'] = resistance
    tech['support_levels'] = support

    return tech


#############################################
# FUNDAMENTAL ANALYSIS
#############################################

def analyze_fundamentals(fundamental, quote, last_price):
    """Normalize Schwab's fundamental block and derive extras

    Args:
        fundamental: Schwab fundamental dict
        quote: flattened quote dict
        last_price: latest traded price

    Returns:
        dict of fundamental metrics
    """
    if not fundamental:
        return {}

    f = fundamental
    out = {
        'market_cap': clean(f.get('marketCap')),
        'market_cap_float': clean(f.get('marketCapFloat')),
        'shares_outstanding': clean(f.get('sharesOutstanding')),
        'eps_ttm': clean(f.get('epsTTM')),
        'eps_change_ttm': clean(f.get('epsChangePercentTTM')),
        'eps_change_year': clean(f.get('epsChangeYear')),
        'rev_change_ttm': clean(f.get('revChangeTTM')),
        'rev_change_year': clean(f.get('revChangeYear')),
        'pe_ratio': clean(f.get('peRatio')),
        'peg_ratio': clean(f.get('pegRatio')),
        'pb_ratio': clean(f.get('pbRatio')),
        'pr_ratio': clean(f.get('prRatio')),
        'pcf_ratio': clean(f.get('pcfRatio')),
        'book_value_per_share': clean(f.get('bookValuePerShare')),
        'gross_margin_ttm': clean(f.get('grossMarginTTM')),
        'net_margin_ttm': clean(f.get('netProfitMarginTTM')),
        'operating_margin_ttm': clean(f.get('operatingMarginTTM')),
        'roe': clean(f.get('returnOnEquity')),
        'roa': clean(f.get('returnOnAssets')),
        'roi': clean(f.get('returnOnInvestment')),
        'current_ratio': clean(f.get('currentRatio')),
        'quick_ratio': clean(f.get('quickRatio')),
        'interest_coverage': clean(f.get('interestCoverage')),
        'total_debt_to_equity': clean(f.get('totalDebtToEquity')),
        'lt_debt_to_equity': clean(f.get('ltDebtToEquity')),
        'total_debt_to_capital': clean(f.get('totalDebtToCapital')),
        'beta': clean(f.get('beta')),
        'high_52': clean(f.get('high52')),
        'low_52': clean(f.get('low52')),
        'div_amount': clean(f.get('divAmount')),
        'div_yield': clean(f.get('divYield')),
        'short_int_to_float': clean(f.get('shortIntToFloat')),
        'short_int_day_to_cover': clean(f.get('shortIntDayToCover')),
        'avg_volume_10d': clean(f.get('avg10DaysVolume')),
        'avg_volume_3m': clean(f.get('avg3MonthVolume')),
    }

    # Derived
    bvps = out.get('book_value_per_share')
    if bvps and bvps > 0 and last_price:
        out['derived_pb'] = last_price / bvps

    shares = out.get('shares_outstanding')
    if shares and out.get('market_cap'):
        out['implied_price_from_mcap'] = out['market_cap'] / shares

    if out.get('market_cap_float') and out.get('shares_outstanding'):
        # marketCapFloat is reported in millions of shares on some feeds
        out['float_ratio'] = out['market_cap_float'] / out['shares_outstanding']

    # Squeeze scoring: high short % of float + low days to cover = fast but capped
    si = out.get('short_int_to_float')
    dtc = out.get('short_int_day_to_cover')
    if si is not None:
        if si >= 20:
            grade = 'extreme'
        elif si >= 15:
            grade = 'high'
        elif si >= 8:
            grade = 'elevated'
        elif si >= 3:
            grade = 'moderate'
        else:
            grade = 'low'
        out['short_grade'] = grade
        if dtc is not None:
            out['squeeze_note'] = (
                f'{si:.1f}% of float short at {dtc:.2f} days to cover - '
                + ('fuel is there but it can unwind quickly' if dtc < 3
                   else 'thin exit door, squeezes can extend')
            )

    # Profitability flag
    if out.get('eps_ttm') is not None:
        out['profitable'] = out['eps_ttm'] > 0

    out['sector'] = quote.get('sector') or f.get('sector')
    out['description'] = quote.get('description')
    out['exchange'] = quote.get('exchangeName') or quote.get('exchange')

    return out


#############################################
# OPTIONS ANALYTICS
#############################################

def flatten_chain(chain):
    """Turn Schwab's nested expDateMap into a flat DataFrame

    Args:
        chain: raw /chains response

    Returns:
        DataFrame of contracts
    """
    if not chain:
        return pd.DataFrame()

    rows = []
    for side, key in (('CALL', 'callExpDateMap'), ('PUT', 'putExpDateMap')):
        exp_map = chain.get(key, {}) or {}
        for exp_key, strike_map in exp_map.items():
            # exp_key looks like "2026-08-07:4"
            exp_date = exp_key.split(':')[0]
            for strike_str, contracts in (strike_map or {}).items():
                for c in contracts:
                    rows.append({
                        'side': side,
                        'expiration': exp_date,
                        'strike': float(strike_str),
                        'dte': clean(c.get('daysToExpiration'), 0),
                        'bid': clean(c.get('bid'), 0.0),
                        'ask': clean(c.get('ask'), 0.0),
                        'mark': clean(c.get('mark'), 0.0),
                        'last': clean(c.get('last'), 0.0),
                        'volume': clean(c.get('totalVolume'), 0) or 0,
                        'open_interest': clean(c.get('openInterest'), 0) or 0,
                        'iv': clean(c.get('volatility')),
                        'delta': clean(c.get('delta')),
                        'gamma': clean(c.get('gamma')),
                        'theta': clean(c.get('theta')),
                        'vega': clean(c.get('vega')),
                        'intrinsic': clean(c.get('intrinsicValue'), 0.0),
                        'in_the_money': c.get('inTheMoney'),
                        'symbol': c.get('symbol'),
                    })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['expiration'] = pd.to_datetime(df['expiration'], errors='coerce')
    df['mid'] = np.where(
        (df['bid'] > 0) & (df['ask'] > 0),
        (df['bid'] + df['ask']) / 2,
        df['mark'],
    )
    return df.dropna(subset=['expiration'])


def atm_straddle(df_exp, spot):
    """ATM straddle mid and implied move for one expiration"""
    if df_exp.empty or not spot:
        return None, None, None

    strikes = df_exp['strike'].unique()
    if len(strikes) == 0:
        return None, None, None
    atm_strike = float(min(strikes, key=lambda k: abs(k - spot)))

    call = df_exp[(df_exp['side'] == 'CALL') & (df_exp['strike'] == atm_strike)]
    put = df_exp[(df_exp['side'] == 'PUT') & (df_exp['strike'] == atm_strike)]
    if call.empty or put.empty:
        return atm_strike, None, None

    straddle = float(call['mid'].iloc[0]) + float(put['mid'].iloc[0])
    implied_move = straddle / spot * 100 if spot else None
    return atm_strike, straddle, implied_move


def risk_reversal(df_exp, target_delta=0.25):
    """25-delta risk reversal: call IV minus put IV (skew)"""
    calls = df_exp[(df_exp['side'] == 'CALL') & df_exp['delta'].notna() & df_exp['iv'].notna()]
    puts = df_exp[(df_exp['side'] == 'PUT') & df_exp['delta'].notna() & df_exp['iv'].notna()]
    if calls.empty or puts.empty:
        return None, None, None

    call_row = calls.iloc[(calls['delta'] - target_delta).abs().argsort().iloc[0]]
    put_row = puts.iloc[(puts['delta'] + target_delta).abs().argsort().iloc[0]]

    call_iv = float(call_row['iv'])
    put_iv = float(put_row['iv'])
    return call_iv - put_iv, call_iv, put_iv


def max_pain(df_exp):
    """Strike that minimizes total in-the-money value to option holders"""
    if df_exp.empty:
        return None

    strikes = sorted(df_exp['strike'].unique())
    if not strikes:
        return None

    calls = df_exp[df_exp['side'] == 'CALL']
    puts = df_exp[df_exp['side'] == 'PUT']

    best_strike, best_value = None, None
    for candidate in strikes:
        call_pain = ((candidate - calls['strike']).clip(lower=0) * calls['open_interest']).sum()
        put_pain = ((puts['strike'] - candidate).clip(lower=0) * puts['open_interest']).sum()
        total = float(call_pain + put_pain)
        if best_value is None or total < best_value:
            best_value, best_strike = total, float(candidate)

    return best_strike


def gamma_exposure(df_exp, spot):
    """Naive dealer gamma exposure by strike (dealers long calls, short puts)"""
    if df_exp.empty or not spot:
        return pd.DataFrame(), None

    working = df_exp[df_exp['gamma'].notna()].copy()
    if working.empty:
        return pd.DataFrame(), None

    sign = np.where(working['side'] == 'CALL', 1.0, -1.0)
    working['gex'] = sign * working['gamma'] * working['open_interest'] * 100 * (spot ** 2) * 0.01

    by_strike = working.groupby('strike')['gex'].sum().reset_index()
    by_strike = by_strike.sort_values('strike')

    # Flip point: strike where cumulative GEX crosses zero
    by_strike['cumulative'] = by_strike['gex'].cumsum()
    flip = None
    cum = by_strike['cumulative'].values
    strikes = by_strike['strike'].values
    for i in range(1, len(cum)):
        if cum[i - 1] < 0 <= cum[i] or cum[i - 1] > 0 >= cum[i]:
            flip = float(strikes[i])
            break

    return by_strike, flip


def analyze_options(chain, spot, tech):
    """Full options analytics across all expirations

    Args:
        chain: raw /chains response
        spot: underlying price
        tech: technical dict (for realized vol comparison)

    Returns:
        dict of options metrics
    """
    df = flatten_chain(chain)
    if df.empty:
        return {'available': False}

    out = {
        'available': True,
        'contract_count': len(df),
        'underlying_price': spot,
        'chain_iv': clean(chain.get('volatility')),
        'interest_rate': clean(chain.get('interestRate')),
    }

    # Per-expiration breakdown
    expirations = []
    for exp, grp in df.groupby('expiration'):
        dte = int(grp['dte'].median()) if not grp['dte'].isna().all() else None
        atm_strike, straddle, implied_move = atm_straddle(grp, spot)

        atm_band = grp[(grp['strike'] - spot).abs() <= max(spot * 0.03, 0.01)]
        atm_iv = float(atm_band['iv'].mean()) if not atm_band['iv'].isna().all() else None

        call_oi = float(grp[grp['side'] == 'CALL']['open_interest'].sum())
        put_oi = float(grp[grp['side'] == 'PUT']['open_interest'].sum())
        call_vol = float(grp[grp['side'] == 'CALL']['volume'].sum())
        put_vol = float(grp[grp['side'] == 'PUT']['volume'].sum())

        rr, call_iv, put_iv = risk_reversal(grp)

        expirations.append({
            'expiration': exp.strftime('%Y-%m-%d'),
            'dte': dte,
            'atm_strike': atm_strike,
            'atm_iv': atm_iv,
            'straddle': straddle,
            'implied_move_pct': implied_move,
            'call_oi': call_oi,
            'put_oi': put_oi,
            'total_oi': call_oi + put_oi,
            'put_call_oi': put_oi / call_oi if call_oi else None,
            'call_volume': call_vol,
            'put_volume': put_vol,
            'put_call_volume': put_vol / call_vol if call_vol else None,
            'max_pain': max_pain(grp),
            'risk_reversal_25d': rr,
            'call_iv_25d': call_iv,
            'put_iv_25d': put_iv,
        })

    expirations.sort(key=lambda e: e['dte'] if e['dte'] is not None else 9999)
    out['expirations'] = expirations

    # Front expiration = the tradeable event window
    if expirations:
        front = expirations[0]
        out['front'] = front
        out['implied_move_pct'] = front['implied_move_pct']

        # Variance risk premium: is IV rich vs what the stock actually does?
        atm_iv = front.get('atm_iv')
        rv20 = tech.get('rvol_20d')
        rv60 = tech.get('rvol_60d')
        if atm_iv and rv20:
            out['vrp_20d'] = atm_iv - rv20
            out['iv_rv_ratio_20d'] = atm_iv / rv20 if rv20 else None
        if atm_iv and rv60:
            out['vrp_60d'] = atm_iv - rv60

    # Term structure
    term = [(e['dte'], e['atm_iv']) for e in expirations if e['atm_iv'] and e['dte'] is not None]
    out['term_structure'] = term
    if len(term) >= 2:
        near_iv, far_iv = term[0][1], term[-1][1]
        out['term_slope'] = far_iv - near_iv
        out['term_state'] = 'contango' if far_iv > near_iv else 'backwardation'

    # Constant-maturity 30d ATM IV: the comparable-over-time series.
    # Front-expiry IV drifts with DTE and with whatever event sits inside it,
    # so it is the wrong thing to rank day over day.
    out['cm30_iv'] = ivh.constant_maturity_iv(expirations)
    if out.get('cm30_iv') and tech.get('rvol_20d'):
        out['cm30_vrp'] = out['cm30_iv'] - tech['rvol_20d']

    # Aggregate OI walls across the whole chain
    calls = df[df['side'] == 'CALL'].groupby('strike')['open_interest'].sum()
    puts = df[df['side'] == 'PUT'].groupby('strike')['open_interest'].sum()
    out['call_walls'] = [(float(k), float(v)) for k, v in calls.nlargest(5).items()]
    out['put_walls'] = [(float(k), float(v)) for k, v in puts.nlargest(5).items()]

    total_call_oi = float(df[df['side'] == 'CALL']['open_interest'].sum())
    total_put_oi = float(df[df['side'] == 'PUT']['open_interest'].sum())
    out['total_call_oi'] = total_call_oi
    out['total_put_oi'] = total_put_oi
    out['put_call_oi_ratio'] = total_put_oi / total_call_oi if total_call_oi else None

    # Gamma on the front expiration
    front_exp = df[df['expiration'] == df['expiration'].min()]
    gex_df, flip = gamma_exposure(front_exp, spot)
    if not gex_df.empty:
        out['net_gex'] = float(gex_df['gex'].sum())
        out['gamma_flip'] = flip
        out['top_gamma_strikes'] = [
            (float(r.strike), float(r.gex))
            for r in gex_df.reindex(gex_df['gex'].abs().sort_values(ascending=False).index).head(5).itertuples()
        ]

    return out


#############################################
# INTERPRETATION
#############################################

def build_takeaways(tech, fund, opts, ranks=None):
    """Turn the numbers into plain-language observations"""
    notes = []
    ranks = ranks or {}

    rsi_val = tech.get('rsi_14')
    if rsi_val is not None:
        if rsi_val < 30:
            notes.append(f'RSI {rsi_val:.1f} - oversold territory.')
        elif rsi_val > 70:
            notes.append(f'RSI {rsi_val:.1f} - overbought territory.')
        else:
            notes.append(f'RSI {rsi_val:.1f} - neutral momentum.')

    d50, d200 = tech.get('dist_sma_50'), tech.get('dist_sma_200')
    if d50 is not None and d200 is not None:
        if d50 < 0 and d200 < 0:
            notes.append(f'Below both the 50DMA ({d50:+.1f}%) and 200DMA ({d200:+.1f}%) - established downtrend.')
        elif d50 > 0 and d200 > 0:
            notes.append(f'Above both the 50DMA ({d50:+.1f}%) and 200DMA ({d200:+.1f}%) - established uptrend.')
        else:
            notes.append(f'Mixed against its MAs (50DMA {d50:+.1f}%, 200DMA {d200:+.1f}%) - trend in transition.')

    pos = tech.get('range_position')
    if pos is not None:
        if pos < 15:
            notes.append(f'Sitting at {pos:.0f}% of the 52-week range - near the lows.')
        elif pos > 85:
            notes.append(f'Sitting at {pos:.0f}% of the 52-week range - near the highs.')

    if tech.get('bb_squeeze'):
        notes.append('Bollinger bandwidth in its bottom quintile - volatility compression, expansion often follows.')

    atr_pct = tech.get('atr_pct')
    if atr_pct is not None:
        notes.append(f'ATR is {atr_pct:.1f}% of price - size positions against that daily range, not a fixed dollar stop.')

    si = fund.get('short_int_to_float')
    if si is not None and si >= 10:
        notes.append(fund.get('squeeze_note', f'{si:.1f}% of float short.'))

    if fund.get('profitable') is False:
        notes.append('Negative TTM EPS - valuation rests on forward expectations, not current earnings.')

    if opts.get('available'):
        move = opts.get('implied_move_pct')
        front = opts.get('front', {})
        if move:
            notes.append(
                f"Front expiration ({front.get('expiration')}, {front.get('dte')} DTE) "
                f'prices a {move:.1f}% move.'
            )
        ratio = opts.get('iv_rv_ratio_20d')
        if ratio:
            if ratio > 1.3:
                notes.append(f'ATM IV is {ratio:.2f}x 20-day realized vol - options are rich; premium selling is favored.')
            elif ratio < 0.9:
                notes.append(f'ATM IV is {ratio:.2f}x 20-day realized vol - options are cheap relative to actual movement.')
            else:
                notes.append(f'ATM IV is {ratio:.2f}x 20-day realized vol - roughly fair.')

        rr = front.get('risk_reversal_25d')
        if rr is not None:
            if rr > 2:
                notes.append(f'25-delta risk reversal {rr:+.1f} vol points - call skew, upside is being bid.')
            elif rr < -2:
                notes.append(f'25-delta risk reversal {rr:+.1f} vol points - put skew, downside protection is bid.')

        if opts.get('term_state'):
            notes.append(f"IV term structure in {opts['term_state']} "
                         f"({opts.get('term_slope', 0):+.1f} vol points front to back).")

        mp = front.get('max_pain')
        if mp and opts.get('underlying_price'):
            delta_pct = (mp / opts['underlying_price'] - 1) * 100
            notes.append(f'Front-expiry max pain at {mp:.2f} ({delta_pct:+.1f}% from spot).')

    # Volatility ranking
    iv_r = ranks.get('iv')
    if iv_r and iv_r.get('sufficient') and iv_r.get('rank') is not None:
        descriptor = ('the top of' if iv_r['rank'] > 75
                      else 'the bottom of' if iv_r['rank'] < 25
                      else 'the middle of')
        notes.append(
            f"30-day IV rank {iv_r['rank']:.0f} (percentile {iv_r['percentile']:.0f}) - "
            f"IV sits in {descriptor} its {iv_r['samples']}-observation range "
            f"({iv_r['low']:.1f} to {iv_r['high']:.1f})."
        )
    elif iv_r:
        notes.append(
            f"IV rank not yet meaningful - only {iv_r['samples']} snapshots stored "
            f"(need {ivh.MIN_SAMPLES_FOR_RANK}). Schwab serves no IV history, so this "
            f"builds forward from your first run."
        )

    rv_r = ranks.get('rv')
    if rv_r and rv_r.get('sufficient') and rv_r.get('rank') is not None:
        notes.append(
            f"20-day realized-vol rank {rv_r['rank']:.0f} "
            f"(percentile {rv_r['percentile']:.0f}) over {rv_r['samples']} sessions - "
            f"the stock is {'moving more' if rv_r['rank'] > 60 else 'moving less' if rv_r['rank'] < 40 else 'moving about as much'} "
            f"than it typically does."
        )

    if iv_r and rv_r and iv_r.get('sufficient') and rv_r.get('sufficient'):
        if iv_r.get('rank') is not None and rv_r.get('rank') is not None:
            gap = iv_r['rank'] - rv_r['rank']
            if gap > 25:
                notes.append('IV is rich against its own history while realized vol is not - '
                             'the classic setup for selling premium.')
            elif gap < -25:
                notes.append('Realized vol is high against its history while IV is not - '
                             'options may be underpricing actual movement.')

    return notes


#############################################
# REPORT RENDERING
#############################################

def print_report(symbol, quote, tech, fund, opts, notes, ranks=None):
    """Console report"""
    ranks = ranks or {}
    width = 78
    bar = '=' * width

    def header(title):
        print(f'\n{bar}\n  {title}\n{bar}')

    print(f'\n{bar}')
    print(f'  {symbol} - DEEP DIVE   |   {dt.datetime.now():%Y-%m-%d %H:%M:%S}')
    print(bar)

    desc = fund.get('description') or quote.get('description', '')
    if desc:
        print(f'  {desc}')

    last = tech.get('last_close') or clean(quote.get('lastPrice'))
    net_change = clean(quote.get('netChange'))
    pct_change = clean(quote.get('netPercentChange'))
    print(f'  Last: {fmt(last)}   Change: {fmt(net_change, "+.2f")} ({fmt(pct_change, "+.2f", "%")})')
    print(f'  Bid/Ask: {fmt(clean(quote.get("bidPrice")))} / {fmt(clean(quote.get("askPrice")))}'
          f'   Volume: {fmt_big(clean(quote.get("totalVolume")))}')

    # ---- TREND & MOMENTUM
    header('TREND & MOMENTUM')
    rows = [
        ('20 SMA', tech.get('sma_20'), tech.get('dist_sma_20')),
        ('50 SMA', tech.get('sma_50'), tech.get('dist_sma_50')),
        ('200 SMA', tech.get('sma_200'), tech.get('dist_sma_200')),
        ('9 EMA', tech.get('ema_9'), tech.get('dist_ema_9')),
        ('21 EMA', tech.get('ema_21'), tech.get('dist_ema_21')),
    ]
    print(f'  {"Level":<12}{"Value":>12}{"Distance":>14}')
    print('  ' + '-' * (width - 4))
    for label, value, dist in rows:
        print(f'  {label:<12}{fmt(value):>12}{fmt(dist, "+.2f", "%"):>14}')

    cross_age = tech.get('bars_since_cross')
    age_str = f' ({cross_age} bars ago)' if cross_age is not None else ' (no cross in window)'
    print(f'\n  MA Cross:      {tech.get("ma_cross", "n/a")}{age_str}')
    print(f'  RSI(14):       {fmt(tech.get("rsi_14"))}')
    print(f'  MACD:          {fmt(tech.get("macd"), "+.3f")} vs signal '
          f'{fmt(tech.get("macd_signal"), "+.3f")}  [{tech.get("macd_state", "n/a")}]')
    print(f'  Bollinger %B:  {fmt(tech.get("bb_pct_b"), ".3f")}   '
          f'Bandwidth: {fmt(tech.get("bb_bandwidth"), ".2f", "%")}'
          f'{"  [SQUEEZE]" if tech.get("bb_squeeze") else ""}')

    # ---- VOLATILITY & RANGE
    header('VOLATILITY & RANGE')
    print(f'  ATR(14):            {fmt(tech.get("atr_14"))} ({fmt(tech.get("atr_pct"), ".2f", "%")} of price)')
    print(f'  Realized vol 20d:   {fmt(tech.get("rvol_20d"), ".1f", "%")}')
    print(f'  Realized vol 60d:   {fmt(tech.get("rvol_60d"), ".1f", "%")}')
    print(f'  52-week range:      {fmt(tech.get("low_52w"))} - {fmt(tech.get("high_52w"))}')
    print(f'  Position in range:  {fmt(tech.get("range_position"), ".1f", "%")}')
    print(f'  Off 52w high:       {fmt(tech.get("pct_off_high"), "+.1f", "%")}')
    print(f'  Off 52w low:        {fmt(tech.get("pct_off_low"), "+.1f", "%")}')
    print(f'  Relative volume:    {fmt(tech.get("relative_volume"), ".2f", "x")}')

    # ---- RETURNS
    header('TRAILING RETURNS')
    for label in ('1w', '1m', '3m', '6m', '1y'):
        val = tech.get(f'return_{label}')
        if val is not None:
            print(f'  {label:<6}{fmt(val, "+.2f", "%"):>10}')

    # ---- STRUCTURE
    header('STRUCTURE / KEY LEVELS')
    if tech.get('resistance_levels'):
        print(f'  Resistance:  {", ".join(fmt(v) for v in tech["resistance_levels"])}')
    if tech.get('support_levels'):
        print(f'  Support:     {", ".join(fmt(v) for v in tech["support_levels"])}')
    if tech.get('poc'):
        va = tech.get('value_area') or (None, None)
        print(f'  Volume POC:  {fmt(tech["poc"])}   Value area: {fmt(va[0])} - {fmt(va[1])}')

    # ---- FUNDAMENTALS
    header('FUNDAMENTALS')
    if not fund:
        print('  No fundamental data returned for this symbol.')
    else:
        pairs = [
            ('Market cap', fmt_big(fund.get('market_cap'))),
            ('Shares out', fmt_big(fund.get('shares_outstanding'))),
            ('EPS (TTM)', fmt(fund.get('eps_ttm'))),
            ('EPS chg TTM', fmt(fund.get('eps_change_ttm'), '+.2f', '%')),
            ('Rev chg TTM', fmt(fund.get('rev_change_ttm'), '+.2f', '%')),
            ('P/E', fmt(fund.get('pe_ratio'))),
            ('PEG', fmt(fund.get('peg_ratio'))),
            ('P/B', fmt(fund.get('pb_ratio'))),
            ('Book value/sh', fmt(fund.get('book_value_per_share'))),
            ('Net margin', fmt(fund.get('net_margin_ttm'), '.2f', '%')),
            ('Operating margin', fmt(fund.get('operating_margin_ttm'), '.2f', '%')),
            ('ROE', fmt(fund.get('roe'), '.2f', '%')),
            ('ROA', fmt(fund.get('roa'), '.2f', '%')),
            ('Current ratio', fmt(fund.get('current_ratio'))),
            ('Debt/Equity', fmt(fund.get('total_debt_to_equity'))),
            ('Interest coverage', fmt(fund.get('interest_coverage'))),
            ('Beta', fmt(fund.get('beta'))),
            ('Dividend yield', fmt(fund.get('div_yield'), '.2f', '%')),
        ]
        for i in range(0, len(pairs), 2):
            left = pairs[i]
            right = pairs[i + 1] if i + 1 < len(pairs) else ('', '')
            print(f'  {left[0]:<20}{left[1]:>14}    {right[0]:<20}{right[1]:>14}')

        header('SHORT INTEREST')
        print(f'  Short % of float:   {fmt(fund.get("short_int_to_float"), ".2f", "%")}'
              f'   [{fund.get("short_grade", "n/a")}]')
        print(f'  Days to cover:      {fmt(fund.get("short_int_day_to_cover"))}')
        if fund.get('squeeze_note'):
            print(f'  {fund["squeeze_note"]}')

    # ---- VOLATILITY RANK
    header('VOLATILITY RANK')
    iv_r, rv_r = ranks.get('iv'), ranks.get('rv')

    if iv_r:
        if iv_r.get('sufficient') and iv_r.get('rank') is not None:
            print(f'  IV rank (30d CM):   {fmt(iv_r["rank"], ".0f")}'
                  f'   percentile {fmt(iv_r["percentile"], ".0f")}')
            print(f'  IV range:           {fmt(iv_r["low"], ".1f")} - {fmt(iv_r["high"], ".1f")}'
                  f'   (mean {fmt(iv_r["mean"], ".1f")}, n={iv_r["samples"]})')
        else:
            print(f'  IV rank:            building - {iv_r["samples"]} of '
                  f'{ivh.MIN_SAMPLES_FOR_RANK} snapshots needed')
    else:
        print('  IV rank:            n/a (no options data)')

    print(f'  Current 30d CM IV:  {fmt(opts.get("cm30_iv"), ".1f")}')

    if rv_r and rv_r.get('sufficient') and rv_r.get('rank') is not None:
        print(f'  RV rank (20d):      {fmt(rv_r["rank"], ".0f")}'
              f'   percentile {fmt(rv_r["percentile"], ".0f")}')
        print(f'  RV range:           {fmt(rv_r["low"], ".1f")} - {fmt(rv_r["high"], ".1f")}'
              f'   (mean {fmt(rv_r["mean"], ".1f")}, n={rv_r["samples"]})')

    # ---- OPTIONS
    header('OPTIONS')
    if not opts.get('available'):
        print('  No option chain available for this symbol.')
    else:
        print(f'  Contracts loaded: {opts.get("contract_count")}'
              f'   Total OI: calls {fmt_big(opts.get("total_call_oi"))}'
              f' / puts {fmt_big(opts.get("total_put_oi"))}')
        print(f'  Put/Call OI ratio: {fmt(opts.get("put_call_oi_ratio"), ".3f")}')

        if opts.get('iv_rv_ratio_20d'):
            print(f'  ATM IV / 20d realized vol: {fmt(opts.get("iv_rv_ratio_20d"), ".2f", "x")}'
                  f'   (VRP {fmt(opts.get("vrp_20d"), "+.1f")} vol pts)')
        if opts.get('term_state'):
            print(f'  Term structure: {opts["term_state"]} '
                  f'({fmt(opts.get("term_slope"), "+.1f")} vol pts front to back)')
        if opts.get('net_gex') is not None:
            print(f'  Front-expiry net GEX: {fmt_big(opts["net_gex"])}'
                  f'   Gamma flip: {fmt(opts.get("gamma_flip"))}')

        print(f'\n  {"Expiry":<12}{"DTE":>5}{"ATM IV":>9}{"Move":>8}{"Straddle":>10}'
              f'{"P/C OI":>9}{"MaxPain":>10}{"RR25":>8}')
        print('  ' + '-' * (width - 4))
        for e in opts['expirations'][:12]:
            print(f'  {e["expiration"]:<12}{fmt(e["dte"], ".0f"):>5}'
                  f'{fmt(e["atm_iv"], ".1f"):>9}'
                  f'{fmt(e["implied_move_pct"], ".1f", "%"):>8}'
                  f'{fmt(e["straddle"]):>10}'
                  f'{fmt(e["put_call_oi"], ".2f"):>9}'
                  f'{fmt(e["max_pain"]):>10}'
                  f'{fmt(e["risk_reversal_25d"], "+.1f"):>8}')

        if opts.get('call_walls'):
            print(f'\n  Call OI walls: '
                  + ', '.join(f'{k:.2f} ({fmt_big(v)})' for k, v in opts['call_walls']))
        if opts.get('put_walls'):
            print(f'  Put OI walls:  '
                  + ', '.join(f'{k:.2f} ({fmt_big(v)})' for k, v in opts['put_walls']))

    # ---- TAKEAWAYS
    header('READ')
    for note in notes:
        print(f'  - {note}')

    print(f'\n{bar}')
    print('  Data: Schwab market data API. Analysis is informational, not advice.')
    print(f'{bar}\n')


def render_html(symbol, quote, tech, fund, opts, notes, ranks=None):
    """Self-contained dark-theme HTML report"""
    ranks = ranks or {}
    ts = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    last = tech.get('last_close')
    pct_change = clean(quote.get('netPercentChange'))
    change_class = 'pos' if (pct_change or 0) >= 0 else 'neg'

    def metric(label, value, cls=''):
        return f'<div class="metric"><span class="lbl">{label}</span>' \
               f'<span class="val {cls}">{value}</span></div>'

    def signed_class(value):
        if value is None:
            return ''
        return 'pos' if value >= 0 else 'neg'

    trend_rows = ''
    for label, key in (('20 SMA', 'sma_20'), ('50 SMA', 'sma_50'), ('200 SMA', 'sma_200'),
                       ('9 EMA', 'ema_9'), ('21 EMA', 'ema_21')):
        dist = tech.get(f'dist_{key}')
        trend_rows += (
            f'<tr><td>{label}</td><td>{fmt(tech.get(key))}</td>'
            f'<td class="{signed_class(dist)}">{fmt(dist, "+.2f", "%")}</td></tr>'
        )

    returns_rows = ''
    for label in ('1w', '1m', '3m', '6m', '1y'):
        val = tech.get(f'return_{label}')
        if val is not None:
            returns_rows += (f'<tr><td>{label}</td>'
                             f'<td class="{signed_class(val)}">{fmt(val, "+.2f", "%")}</td></tr>')

    fund_rows = ''
    for label, key, spec, suffix in (
        ('Market cap', 'market_cap', None, ''),
        ('Shares outstanding', 'shares_outstanding', None, ''),
        ('EPS (TTM)', 'eps_ttm', '.2f', ''),
        ('EPS change TTM', 'eps_change_ttm', '+.2f', '%'),
        ('Revenue change TTM', 'rev_change_ttm', '+.2f', '%'),
        ('P/E', 'pe_ratio', '.2f', ''),
        ('P/B', 'pb_ratio', '.2f', ''),
        ('Book value / share', 'book_value_per_share', '.2f', ''),
        ('Net margin', 'net_margin_ttm', '.2f', '%'),
        ('ROE', 'roe', '.2f', '%'),
        ('Current ratio', 'current_ratio', '.2f', ''),
        ('Debt / Equity', 'total_debt_to_equity', '.2f', ''),
        ('Beta', 'beta', '.2f', ''),
        ('Short % of float', 'short_int_to_float', '.2f', '%'),
        ('Days to cover', 'short_int_day_to_cover', '.2f', ''),
    ):
        value = fund.get(key)
        display = fmt_big(value) if spec is None else fmt(value, spec, suffix)
        fund_rows += f'<tr><td>{label}</td><td>{display}</td></tr>'

    opt_rows = ''
    if opts.get('available'):
        for e in opts['expirations'][:14]:
            opt_rows += (
                f'<tr><td>{e["expiration"]}</td><td>{fmt(e["dte"], ".0f")}</td>'
                f'<td>{fmt(e["atm_iv"], ".1f")}</td>'
                f'<td class="hot">{fmt(e["implied_move_pct"], ".1f", "%")}</td>'
                f'<td>{fmt(e["straddle"])}</td>'
                f'<td>{fmt(e["put_call_oi"], ".2f")}</td>'
                f'<td>{fmt(e["max_pain"])}</td>'
                f'<td class="{signed_class(e["risk_reversal_25d"])}">'
                f'{fmt(e["risk_reversal_25d"], "+.1f")}</td></tr>'
            )
    else:
        opt_rows = '<tr><td colspan="8">No option chain available.</td></tr>'

    notes_html = ''.join(f'<li>{n}</li>' for n in notes)

    iv_r, rv_r = ranks.get('iv'), ranks.get('rv')

    def rank_bar(label, entry):
        if not entry or not entry.get('sufficient') or entry.get('rank') is None:
            pending = f"building ({entry['samples']}/{ivh.MIN_SAMPLES_FOR_RANK})" if entry else 'n/a'
            return metric(label, pending, 'muted')
        value = entry['rank']
        cls = 'neg' if value > 75 else 'pos' if value < 25 else ''
        pct = max(0.0, min(100.0, value))
        return (
            f'<div class="metric"><span class="lbl">{label}</span>'
            f'<span class="val {cls}">{value:.0f}'
            f'<span style="color:var(--muted);font-weight:400"> / pct {entry["percentile"]:.0f}</span>'
            f'</span></div>'
            f'<div class="track"><div class="fill" style="width:{pct:.1f}%"></div></div>'
        )

    rank_html = (
        rank_bar('IV rank (30d CM)', iv_r)
        + metric('Current 30d CM IV', fmt(opts.get('cm30_iv'), '.1f'))
        + rank_bar('RV rank (20d)', rv_r)
    )
    if iv_r and iv_r.get('sufficient'):
        rank_html += metric('IV range',
                            f"{fmt(iv_r['low'], '.1f')} - {fmt(iv_r['high'], '.1f')}")
    if rv_r and rv_r.get('sufficient'):
        rank_html += metric('RV range',
                            f"{fmt(rv_r['low'], '.1f')} - {fmt(rv_r['high'], '.1f')}")

    levels_html = ''
    if tech.get('resistance_levels'):
        levels_html += metric('Resistance', ', '.join(fmt(v) for v in tech['resistance_levels']), 'neg')
    if tech.get('support_levels'):
        levels_html += metric('Support', ', '.join(fmt(v) for v in tech['support_levels']), 'pos')
    if tech.get('poc'):
        va = tech.get('value_area') or (None, None)
        levels_html += metric('Volume POC', fmt(tech['poc']))
        levels_html += metric('Value area', f'{fmt(va[0])} - {fmt(va[1])}')

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{symbol} Deep Dive - {ts}</title>
<style>
:root {{
  --bg:#0d1117; --panel:#161b22; --border:#30363d; --text:#c9d1d9;
  --muted:#8b949e; --accent:#58a6ff; --pos:#3fb950; --neg:#f85149; --hot:#d29922;
}}
* {{ box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--text); margin:0; padding:24px;
  font-family:'JetBrains Mono','Consolas','SF Mono',monospace; font-size:13px; line-height:1.6; }}
.wrap {{ max-width:1180px; margin:0 auto; }}
header {{ border-bottom:2px solid var(--accent); padding-bottom:14px; margin-bottom:24px; }}
h1 {{ margin:0; font-size:26px; letter-spacing:2px; color:var(--accent); }}
.sub {{ color:var(--muted); font-size:12px; margin-top:4px; }}
.price {{ font-size:32px; font-weight:700; margin-top:10px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:18px; }}
.panel {{ background:var(--panel); border:1px solid var(--border); border-radius:6px; padding:16px; }}
.panel h2 {{ margin:0 0 12px; font-size:12px; letter-spacing:2px; text-transform:uppercase;
  color:var(--muted); border-bottom:1px solid var(--border); padding-bottom:8px; }}
table {{ width:100%; border-collapse:collapse; }}
td, th {{ padding:5px 6px; text-align:right; border-bottom:1px solid rgba(48,54,61,.5); }}
td:first-child, th:first-child {{ text-align:left; color:var(--muted); }}
th {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:1px; }}
.metric {{ display:flex; justify-content:space-between; padding:5px 0;
  border-bottom:1px solid rgba(48,54,61,.5); }}
.lbl {{ color:var(--muted); }}
.val {{ font-weight:600; }}
.pos {{ color:var(--pos); }}
.neg {{ color:var(--neg); }}
.hot {{ color:var(--hot); }}
.full {{ grid-column:1/-1; }}
.muted {{ color:var(--muted); font-weight:400; }}
.track {{ height:5px; background:#21262d; border-radius:3px; margin:2px 0 8px; overflow:hidden; }}
.fill {{ height:100%; background:linear-gradient(90deg,var(--pos),var(--hot),var(--neg)); }}
ul {{ margin:0; padding-left:18px; }}
li {{ margin-bottom:8px; }}
footer {{ margin-top:24px; padding-top:14px; border-top:1px solid var(--border);
  color:var(--muted); font-size:11px; }}
</style></head><body><div class="wrap">

<header>
  <h1>{symbol}</h1>
  <div class="sub">{fund.get('description') or ''} &middot; generated {ts}</div>
  <div class="price {change_class}">{fmt(last)}
    <span style="font-size:15px">{fmt(pct_change, '+.2f', '%')}</span></div>
</header>

<div class="grid">

  <div class="panel">
    <h2>Trend</h2>
    <table>
      <tr><th>Level</th><th>Value</th><th>Distance</th></tr>
      {trend_rows}
    </table>
    <div style="margin-top:12px">
      {metric('MA Cross', tech.get('ma_cross', 'n/a'))}
      {metric('RSI(14)', fmt(tech.get('rsi_14')))}
      {metric('MACD', f"{fmt(tech.get('macd'), '+.3f')} / {fmt(tech.get('macd_signal'), '+.3f')}")}
      {metric('MACD state', tech.get('macd_state', 'n/a'))}
    </div>
  </div>

  <div class="panel">
    <h2>Volatility &amp; Range</h2>
    {metric('ATR(14)', f"{fmt(tech.get('atr_14'))} ({fmt(tech.get('atr_pct'), '.2f', '%')})")}
    {metric('Realized vol 20d', fmt(tech.get('rvol_20d'), '.1f', '%'))}
    {metric('Realized vol 60d', fmt(tech.get('rvol_60d'), '.1f', '%'))}
    {metric('52w range', f"{fmt(tech.get('low_52w'))} - {fmt(tech.get('high_52w'))}")}
    {metric('Range position', fmt(tech.get('range_position'), '.1f', '%'))}
    {metric('Off 52w high', fmt(tech.get('pct_off_high'), '+.1f', '%'), signed_class(tech.get('pct_off_high')))}
    {metric('Bollinger %B', fmt(tech.get('bb_pct_b'), '.3f'))}
    {metric('Squeeze', 'YES' if tech.get('bb_squeeze') else 'no', 'hot' if tech.get('bb_squeeze') else '')}
    {metric('Relative volume', fmt(tech.get('relative_volume'), '.2f', 'x'))}
  </div>

  <div class="panel">
    <h2>Trailing Returns</h2>
    <table>{returns_rows}</table>
  </div>

  <div class="panel">
    <h2>Key Levels</h2>
    {levels_html}
  </div>

  <div class="panel">
    <h2>Fundamentals</h2>
    <table>{fund_rows}</table>
  </div>

  <div class="panel">
    <h2>Volatility Rank</h2>
    {rank_html}
  </div>

  <div class="panel">
    <h2>Options Summary</h2>
    {metric('Implied move (front)', fmt(opts.get('implied_move_pct'), '.1f', '%'), 'hot')}
    {metric('ATM IV / 20d RV', fmt(opts.get('iv_rv_ratio_20d'), '.2f', 'x'))}
    {metric('VRP (vol pts)', fmt(opts.get('vrp_20d'), '+.1f'))}
    {metric('Term structure', opts.get('term_state', 'n/a'))}
    {metric('Put/Call OI', fmt(opts.get('put_call_oi_ratio'), '.3f'))}
    {metric('Net GEX (front)', fmt_big(opts.get('net_gex')))}
    {metric('Gamma flip', fmt(opts.get('gamma_flip')))}
    {metric('Call walls', ', '.join(f'{k:.2f}' for k, _ in opts.get('call_walls', [])) or 'n/a')}
    {metric('Put walls', ', '.join(f'{k:.2f}' for k, _ in opts.get('put_walls', [])) or 'n/a')}
  </div>

  <div class="panel full">
    <h2>Option Chain by Expiration</h2>
    <table>
      <tr><th>Expiry</th><th>DTE</th><th>ATM IV</th><th>Implied Move</th>
          <th>Straddle</th><th>P/C OI</th><th>Max Pain</th><th>RR 25d</th></tr>
      {opt_rows}
    </table>
  </div>

  <div class="panel full">
    <h2>Read</h2>
    <ul>{notes_html}</ul>
  </div>

</div>

<footer>
  Source: Schwab market data API. Computed locally; no third-party analytics.
  Informational only &mdash; not investment advice.
</footer>
</div></body></html>"""


#############################################
# WATCHLIST SUMMARY
#############################################

def print_watchlist_summary(results):
    """Cross-symbol comparison table, sorted by IV rank"""
    if len(results) < 2:
        return

    width = 100
    bar = '=' * width
    print(f'\n{bar}')
    print(f'  WATCHLIST SUMMARY ({len(results)} symbols)')
    print(bar)
    print(f'  {"Symbol":<8}{"Last":>10}{"1m %":>9}{"RSI":>7}{"vs200":>9}'
          f'{"CM30 IV":>10}{"IVR":>6}{"RVR":>6}{"IV/RV":>8}{"Move":>8}{"Short%":>8}')
    print('  ' + '-' * (width - 4))

    def sort_key(r):
        iv_r = (r.get('ranks') or {}).get('iv') or {}
        rank = iv_r.get('rank')
        return rank if rank is not None else -1

    for r in sorted(results, key=sort_key, reverse=True):
        tech, fund, opts = r['technicals'], r['fundamentals'], r['options']
        ranks = r.get('ranks') or {}
        iv_r = ranks.get('iv') or {}
        rv_r = ranks.get('rv') or {}

        ivr = iv_r.get('rank') if iv_r.get('sufficient') else None
        rvr = rv_r.get('rank') if rv_r.get('sufficient') else None

        print(f'  {r["symbol"].lstrip("$"):<8}'
              f'{fmt(tech.get("last_close")):>10}'
              f'{fmt(tech.get("return_1m"), "+.1f", "%"):>9}'
              f'{fmt(tech.get("rsi_14"), ".0f"):>7}'
              f'{fmt(tech.get("dist_sma_200"), "+.0f", "%"):>9}'
              f'{fmt(opts.get("cm30_iv"), ".1f"):>10}'
              f'{fmt(ivr, ".0f"):>6}'
              f'{fmt(rvr, ".0f"):>6}'
              f'{fmt(opts.get("iv_rv_ratio_20d"), ".2f", "x"):>8}'
              f'{fmt(opts.get("implied_move_pct"), ".1f", "%"):>8}'
              f'{fmt(fund.get("short_int_to_float"), ".1f"):>8}')

    print(f'{bar}')
    print('  IVR = IV rank (blank until enough snapshots). RVR = realized-vol rank.')
    print(f'{bar}\n')


#############################################
# PER-SYMBOL PIPELINE
#############################################

def analyze_symbol(client, symbol, args, conn=None):
    """Fetch, compute and record everything for one symbol

    Args:
        client: SchwabClient
        symbol: normalized ticker
        args: parsed CLI args
        conn: optional history DB connection

    Returns:
        Result dict, or None if the symbol could not be analyzed
    """
    logger.info(f'--- {symbol} ---')

    # ---- Quote + fundamentals
    try:
        raw_quote = client.get_quote(symbol)
    except Exception as exc:
        logger.error(f'{symbol}: quote fetch failed: {exc}')
        return None

    quote = unwrap_quote(raw_quote)
    fundamental_block = raw_quote.get('fundamental', {}) if isinstance(raw_quote, dict) else {}

    if not fundamental_block:
        logger.debug(f'{symbol}: no fundamental block in quote, trying instruments')
        try:
            fundamental_block = client.get_fundamental(symbol)
        except Exception as exc:
            logger.warning(f'{symbol}: fundamental fetch failed: {exc}')

    # ---- Price history
    try:
        candles = client.get_price_history(symbol, years=args.years)
    except Exception as exc:
        logger.error(f'{symbol}: price history fetch failed: {exc}')
        return None

    if candles.empty:
        logger.error(f'{symbol}: no price history returned, skipping')
        return None

    logger.info(f'{symbol}: {len(candles)} daily bars')
    tech = analyze_technicals(candles)
    spot = clean(quote.get('lastPrice')) or tech.get('last_close')
    fund = analyze_fundamentals(fundamental_block, quote, spot)

    # ---- Option chain
    opts = {'available': False}
    if not args.no_options:
        try:
            chain = client.get_option_chain(
                symbol,
                strike_count=args.strikes,
                from_date=args.from_date,
                to_date=args.to_date,
            )
            if chain.get('status') == 'SUCCESS' or chain.get('callExpDateMap'):
                underlying = chain.get('underlyingPrice') or spot
                opts = analyze_options(chain, underlying, tech)
                logger.info(f'{symbol}: {opts.get("contract_count", 0)} option contracts')
            else:
                logger.warning(f'{symbol}: chain status {chain.get("status")}')
        except Exception as exc:
            logger.warning(f'{symbol}: option chain fetch failed: {exc}')

    # ---- History store: backfill RV, record today's IV, then rank
    ranks = {}
    if conn is not None:
        try:
            ivh.backfill_rv(conn, symbol, candles)
            ivh.record_snapshot(conn, symbol, spot, opts, tech)

            cm30 = opts.get('cm30_iv')
            if cm30 is not None:
                ranks['iv'] = ivh.iv_rank(conn, symbol, cm30, lookback_days=args.lookback)
            if tech.get('rvol_20d') is not None:
                ranks['rv'] = ivh.rv_rank(conn, symbol, tech['rvol_20d'],
                                          lookback_days=args.lookback)
        except Exception as exc:
            logger.warning(f'{symbol}: history store failed: {exc}')

    notes = build_takeaways(tech, fund, opts, ranks)

    return {
        'symbol': symbol,
        'quote': quote,
        'technicals': tech,
        'fundamentals': fund,
        'options': opts,
        'ranks': ranks,
        'takeaways': notes,
    }


def write_outputs(result, args):
    """Emit HTML and JSON artifacts for one symbol"""
    output_dir = Path(args.output_dir)
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    clean_symbol = result['symbol'].lstrip('$')

    if not args.no_html:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            html_path = output_dir / f'{clean_symbol}_deepdive_{stamp}.html'
            html_path.write_text(
                render_html(result['symbol'], result['quote'], result['technicals'],
                            result['fundamentals'], result['options'],
                            result['takeaways'], result['ranks']),
                encoding='utf-8',
            )
            logger.info(f'HTML report: {html_path.resolve()}')
        except Exception as exc:
            logger.error(f'{clean_symbol}: HTML render failed: {exc}')

    if args.json:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            json_path = output_dir / f'{clean_symbol}_deepdive_{stamp}.json'
            payload = dict(result)
            payload['generated'] = dt.datetime.now().isoformat()
            json_path.write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
            logger.info(f'JSON dump: {json_path.resolve()}')
        except Exception as exc:
            logger.error(f'{clean_symbol}: JSON dump failed: {exc}')


#############################################
# CLI
#############################################

def load_watchlist(path):
    """Read tickers from a file - one per line or comma separated, # comments ok"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'Watchlist not found: {path}')

    symbols = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.split('#')[0].strip()
        if not line:
            continue
        for token in line.replace(',', ' ').split():
            symbols.append(token.strip().upper())

    # De-duplicate, preserve order
    seen, ordered = set(), []
    for sym in symbols:
        if sym not in seen:
            seen.add(sym)
            ordered.append(sym)
    return ordered


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='EquityDeepDive - Schwab technical, fundamental and options analysis',
        epilog='Examples:\n'
               '  python equity_deep_dive.py OKLO\n'
               '  python equity_deep_dive.py OKLO SMR NNE CCJ --json\n'
               '  python equity_deep_dive.py --watchlist nuclear.txt\n'
               '  python equity_deep_dive.py --watchlist all.txt --snapshot-only',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('symbols', nargs='*',
                        help='One or more tickers (index symbols get a $ prefix automatically)')
    parser.add_argument('-w', '--watchlist', default=None,
                        help='File of tickers, one per line or comma separated')
    parser.add_argument('-y', '--years', type=int, default=1,
                        help='Years of daily history (allowed: 1,2,3,4,5,10)')
    parser.add_argument('-s', '--strikes', type=int, default=40,
                        help='Strikes above and below ATM to pull (default 40)')
    parser.add_argument('--from-date', default=None, help='Chain start expiry YYYY-MM-DD')
    parser.add_argument('--to-date', default=None, help='Chain end expiry YYYY-MM-DD')
    parser.add_argument('--no-options', action='store_true', help='Skip the option chain pull')
    parser.add_argument('--direct', action='store_true',
                        help='Bypass SchwabProxy and call api.schwabapi.com with tokens.json')
    parser.add_argument('--base-url', default=None, help='Override the base URL')
    parser.add_argument('--path-prefix', default=MARKETDATA_PREFIX,
                        help='Path prefix for --direct mode (default /marketdata/v1)')
    parser.add_argument('--proxy-native', action='store_true',
                        help="Use the proxy's own /quotes and /chains routes instead of "
                             '/passthrough. Note: its /quotes drops the fundamental block.')
    parser.add_argument('-o', '--output-dir', default=str(DEFAULT_OUTPUT_DIR),
                        help='Directory for reports')
    parser.add_argument('--db', default=str(ivh.DEFAULT_DB_PATH),
                        help='SQLite history database path')
    parser.add_argument('--lookback', type=int, default=ivh.DEFAULT_LOOKBACK_DAYS,
                        help='Days of history used for IV/RV ranking (default 252)')
    parser.add_argument('--no-history', action='store_true',
                        help='Skip the history store entirely (no IV/RV rank)')
    parser.add_argument('--snapshot-only', action='store_true',
                        help='Record history and exit - no reports. For scheduled jobs.')
    parser.add_argument('--json', action='store_true', help='Also write a JSON dump')
    parser.add_argument('--no-html', action='store_true', help='Skip the HTML report')
    parser.add_argument('-v', '--verbose', action='store_true', help='Debug logging')
    return parser.parse_args()


#############################################
# MAIN
#############################################

def main():
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ---- Resolve the symbol list
    raw_symbols = list(args.symbols)
    if args.watchlist:
        try:
            raw_symbols.extend(load_watchlist(args.watchlist))
        except Exception as exc:
            logger.error(f'{exc}')
            sys.exit(1)

    if not raw_symbols:
        logger.error('No symbols given. Pass tickers as arguments or use --watchlist.')
        sys.exit(1)

    seen, symbols = set(), []
    for raw in raw_symbols:
        sym = normalize_symbol(raw)
        if sym not in seen:
            seen.add(sym)
            symbols.append(sym)

    logger.info(f'Analyzing {len(symbols)} symbol(s): {", ".join(symbols)}')

    # ---- Client
    try:
        client = SchwabClient(
            direct=args.direct,
            base_url=args.base_url,
            path_prefix=args.path_prefix,
            proxy_native=args.proxy_native,
        )
    except Exception as exc:
        logger.error(f'Client init failed: {exc}')
        sys.exit(1)

    # ---- Preflight: surface token problems before burning calls on them
    health = client.check_health()
    if health:
        if not health.get('has_token'):
            logger.error('Proxy has no token. Visit '
                         f'{client.base_url}/auth to run the OAuth flow.')
            sys.exit(1)
        if health.get('refresh_token_expired'):
            logger.error('Proxy refresh token has expired. Visit '
                         f'{client.base_url}/auth to re-authenticate.')
            sys.exit(1)
        if health.get('token_expired'):
            logger.info('Access token expired; the proxy will refresh it automatically.')
        else:
            logger.info('Proxy healthy, token valid.')

    if args.proxy_native and not args.direct:
        logger.warning('--proxy-native: the proxy pins fields=quote, so fundamentals '
                       'will be missing from this run.')

    # ---- History store
    conn = None
    if not args.no_history:
        try:
            conn = ivh.init_db(args.db)
        except Exception as exc:
            logger.warning(f'History DB unavailable, continuing without ranks: {exc}')

    # ---- Run
    results, failures = [], []
    try:
        for symbol in symbols:
            result = analyze_symbol(client, symbol, args, conn)
            if result is None:
                failures.append(symbol)
                continue
            results.append(result)

            if args.snapshot_only:
                cm30 = result['options'].get('cm30_iv')
                count = ivh.snapshot_count(conn, symbol) if conn else 0
                logger.info(f'{symbol}: recorded CM30 IV {fmt(cm30, ".1f")} '
                            f'({count} snapshots stored)')
                continue

            print_report(result['symbol'], result['quote'], result['technicals'],
                         result['fundamentals'], result['options'],
                         result['takeaways'], result['ranks'])
            write_outputs(result, args)
    finally:
        ivh.close_db(conn)

    if not args.snapshot_only:
        print_watchlist_summary(results)

    if failures:
        logger.warning(f'Failed: {", ".join(failures)}')

    logger.info(f'Done - {len(results)} of {len(symbols)} symbols analyzed')
