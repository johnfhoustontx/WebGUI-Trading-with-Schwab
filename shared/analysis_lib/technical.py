"""
Technical Analysis - Technical Indicators and Calculations
Version: 1.0.0
Last Updated: 2025-01-01

Technical analysis functions for Blueprint trading system.

Version 1.0.0 Changes:
- Initial implementation
- EMA, RSI, ADX, VWAP, ATR calculations
- Volume profile analysis
- Trend structure detection
"""

import numpy as np
import pandas as pd
from typing import Optional, Dict, Tuple, List
import logging

from config import (
    EMA_PERIODS, TREND_STATES, RSI_OVERSOLD, RSI_OVERBOUGHT,
    ADX_TRENDING, TIMEFRAME_WEIGHTS
)

logger = logging.getLogger(__name__)

#############################################
# MOVING AVERAGES
#############################################

def calculate_ema(df: pd.DataFrame, period: int) -> Optional[pd.Series]:
    """Calculate Exponential Moving Average matching TOS/Schwab methodology
    
    Args:
        df: DataFrame with 'close' column
        period: EMA period
        
    Returns:
        Series of EMA values or None if insufficient data
    """
    if df is None or len(df) < period:
        return None
    
    closes = df['close'].to_numpy(dtype=float)
    n = len(closes)
    multiplier = 2.0 / (period + 1)

    ema_values = np.full(n, np.nan)
    # Seed with the SMA of the first `period` bars, then apply the EMA recurrence.
    # Vectorized via pandas .ewm (C-level) instead of a Python bar-by-bar loop:
    # feeding [seed, closes[period:]] through ewm(alpha, adjust=False) reproduces
    # y0=seed, y[i]=closes[i]*m + y[i-1]*(1-m) exactly — identical values, no loop.
    seq = np.empty(n - period + 1)
    seq[0] = np.mean(closes[:period])
    seq[1:] = closes[period:]
    ema_values[period - 1:] = (
        pd.Series(seq).ewm(alpha=multiplier, adjust=False).mean().to_numpy())

    return pd.Series(ema_values, index=df.index)


def calculate_sma(df: pd.DataFrame, period: int) -> Optional[pd.Series]:
    """Calculate Simple Moving Average
    
    Args:
        df: DataFrame with 'close' column
        period: SMA period
        
    Returns:
        Series of SMA values or None if insufficient data
    """
    if df is None or len(df) < period:
        return None
    
    return df['close'].rolling(window=period).mean()


def get_ma_values(df: pd.DataFrame, periods: List[int] = None) -> Dict[int, float]:
    """Get current MA values for multiple periods
    
    Args:
        df: DataFrame with 'close' column
        periods: List of periods (default: EMA_PERIODS from config)
        
    Returns:
        Dict mapping period to current MA value
    """
    if periods is None:
        periods = EMA_PERIODS
    
    results = {}
    for period in periods:
        ema = calculate_ema(df, period)
        if ema is not None and not pd.isna(ema.iloc[-1]):
            results[period] = float(ema.iloc[-1])
        else:
            results[period] = None
    
    return results


#############################################
# MOMENTUM INDICATORS
#############################################

def _wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothed moving average (RMA), SMA-seeded.

    RMA_t = RMA_{t-1} + (x_t - RMA_{t-1}) / period, i.e. an EWMA with
    alpha = 1/period. To match the reference implementations exactly, the
    first RMA value is the SMA of the first `period` observations, after which
    the ewm(adjust=False) recurrence takes over — the same SMA-seed idiom used
    by ``calculate_ema``. Values before the seed bar are NaN.
    """
    x = series.to_numpy(dtype=float)
    n = len(x)
    out = np.full(n, np.nan)
    if n < period:
        return pd.Series(out, index=series.index)
    # Seed at index period-1 with the SMA of the first `period` values, then run
    # the Wilder recurrence over the remainder via ewm(alpha=1/period).
    seq = np.empty(n - period + 1)
    seq[0] = np.mean(x[:period])
    seq[1:] = x[period:]
    out[period - 1:] = (
        pd.Series(seq).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy())
    return pd.Series(out, index=series.index)


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate Relative Strength Index
    
    Args:
        df: DataFrame with 'close' column
        period: RSI period (default 14)
        
    Returns:
        Current RSI value (0-100)
    """
    if df is None or len(df) < period + 1:
        return 50.0

    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # Wilder's smoothing (RMA), matching TOS/TradingView/StockCharts: seed the
    # first average with the SMA of the first `period` deltas, then apply the
    # Wilder recurrence via ewm(alpha=1/period, adjust=False). The first delta
    # (NaN) is dropped so bar `period` is the seeded value.
    avg_gain = _wilder_rma(gain.iloc[1:], period)
    avg_loss = _wilder_rma(loss.iloc[1:], period)

    rs = avg_gain / avg_loss.replace(0, 0.0001)
    rsi = 100 - (100 / (1 + rs))

    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0


def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate Average Directional Index
    
    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: ADX period (default 14)
        
    Returns:
        Current ADX value
    """
    if df is None or len(df) < period * 2:
        return 20.0
    
    high = df['high']
    low = df['low']
    close = df['close']
    
    # Calculate +DM and -DM
    plus_dm = high.diff()
    minus_dm = low.diff().abs() * -1
    
    plus_dm = plus_dm.where((plus_dm > minus_dm.abs()) & (plus_dm > 0), 0)
    minus_dm = minus_dm.abs().where((minus_dm.abs() > plus_dm) & (minus_dm < 0), 0)
    
    # True Range
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)

    # Wilder smoothing (RMA) of TR / +DM / -DM. The first row of each of these
    # is NaN (diff/shift), so drop it before seeding so the SMA seed uses real
    # values — matching TOS/TradingView/StockCharts ADX.
    smoothed_tr = _wilder_rma(tr.iloc[1:], period)
    smoothed_plus_dm = _wilder_rma(plus_dm.iloc[1:], period)
    smoothed_minus_dm = _wilder_rma(minus_dm.iloc[1:], period)

    plus_di = 100 * (smoothed_plus_dm / smoothed_tr.replace(0, 0.0001))
    minus_di = 100 * (smoothed_minus_dm / smoothed_tr.replace(0, 0.0001))

    # DX, then ADX = Wilder RMA of DX.
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 0.0001))
    adx = _wilder_rma(dx.dropna(), period)

    return float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 20.0


def calculate_macd(
    df: pd.DataFrame, 
    fast: int = 12, 
    slow: int = 26, 
    signal: int = 9
) -> Dict[str, float]:
    """Calculate MACD
    
    Args:
        df: DataFrame with 'close' column
        fast: Fast EMA period
        slow: Slow EMA period
        signal: Signal line period
        
    Returns:
        Dict with 'macd', 'signal', 'histogram' values
    """
    if df is None or len(df) < slow + signal:
        return {'macd': 0, 'signal': 0, 'histogram': 0}
    
    ema_fast = calculate_ema(df, fast)
    ema_slow = calculate_ema(df, slow)
    
    if ema_fast is None or ema_slow is None:
        return {'macd': 0, 'signal': 0, 'histogram': 0}
    
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    return {
        'macd': float(macd_line.iloc[-1]) if not pd.isna(macd_line.iloc[-1]) else 0,
        'signal': float(signal_line.iloc[-1]) if not pd.isna(signal_line.iloc[-1]) else 0,
        'histogram': float(histogram.iloc[-1]) if not pd.isna(histogram.iloc[-1]) else 0,
    }


def macd_histogram_series(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Optional[pd.Series]:
    """Full MACD histogram Series (macd_line − signal_line), or None if too short.

    Computing the series once lets a caller read the last TWO histogram values
    without recomputing MACD on a truncated copy of the frame. Because the EMAs
    use an ``adjust=False`` recurrence (value at i depends only on bars ≤ i), the
    series' ``iloc[-2]`` equals ``calculate_macd(df.iloc[:-1])['histogram']`` —
    so it's a drop-in for the old two-call pattern. NOTE the exact equivalence for
    ``iloc[-2]`` requires ``len(df) >= slow + signal + 1`` (so the truncated frame
    is also sufficient); at exactly ``slow + signal`` the old two-call returned 0
    for the prev value while this returns the true histogram. The only caller
    (trade analyze) gates ``len(daily) >= 50``, so it is always well past that."""
    if df is None or len(df) < slow + signal:
        return None
    ema_fast = calculate_ema(df, fast)
    ema_slow = calculate_ema(df, slow)
    if ema_fast is None or ema_slow is None:
        return None
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


#############################################
# VOLUME INDICATORS
#############################################

def calculate_vwap(df: pd.DataFrame) -> Optional[float]:
    """Calculate Volume Weighted Average Price
    
    Args:
        df: DataFrame with 'high', 'low', 'close', 'volume' columns
        
    Returns:
        Current VWAP value or None
    """
    if df is None or len(df) < 1 or 'volume' not in df.columns:
        return None

    typical_price = (df['high'] + df['low'] + df['close']) / 3
    tpv = typical_price * df['volume']

    # Session-anchored: VWAP resets at each session open. Derive a per-bar session
    # date (from a `datetime` column if present, else a DatetimeIndex) and cumsum
    # WITHIN each session so the value doesn't drift across a multi-day frame.
    session_key = None
    if 'datetime' in df.columns:
        session_key = pd.to_datetime(df['datetime']).dt.date
    elif isinstance(df.index, pd.DatetimeIndex):
        session_key = df.index.normalize()

    if session_key is not None:
        cum_tpv = tpv.groupby(session_key).cumsum()
        cum_vol = df['volume'].groupby(session_key).cumsum()
        vwap = cum_tpv / cum_vol
    else:
        # No session information available — fall back to a single cumulative
        # window (single-session assumption).
        vwap = tpv.cumsum() / df['volume'].cumsum()

    return float(vwap.iloc[-1]) if not pd.isna(vwap.iloc[-1]) else None


def calculate_relative_volume(df: pd.DataFrame, period: int = 20) -> Tuple[float, int]:
    """Calculate relative volume vs average
    
    Args:
        df: DataFrame with 'datetime' and 'volume' columns
        period: Lookback period for average
        
    Returns:
        Tuple of (relative_volume_ratio, today_volume)
    """
    if df is None or len(df) < period:
        return 1.0, 0
    
    try:
        df_copy = df.copy()
        df_copy['date'] = pd.to_datetime(df_copy['datetime']).dt.date
        daily_vols = df_copy.groupby('date')['volume'].sum()
        
        if len(daily_vols) == 0:
            return 1.0, 0
        
        today_vol = int(daily_vols.iloc[-1])
        
        if len(daily_vols) < 2:
            return 1.0, today_vol
        
        avg_vol = daily_vols.iloc[:-1].mean()
        rel_vol = today_vol / avg_vol if avg_vol > 0 else 1.0
        
        return round(rel_vol, 2), today_vol
    except Exception as e:
        logger.warning(f"Error calculating relative volume: {e}")
        return 1.0, 0


def calculate_volume_profile(
    df: pd.DataFrame, 
    num_bins: int = 20
) -> Dict[str, float]:
    """Calculate volume profile levels (POC, VAH, VAL)
    
    Args:
        df: DataFrame with 'high', 'low', 'close', 'volume' columns
        num_bins: Number of price bins
        
    Returns:
        Dict with 'poc', 'vah', 'val' (Point of Control, Value Area High/Low)
    """
    if df is None or len(df) < 10 or 'volume' not in df.columns:
        return {'poc': 0, 'vah': 0, 'val': 0}
    
    price_min = df['low'].min()
    price_max = df['high'].max()
    
    if price_max <= price_min:
        current_close = float(df['close'].iloc[-1])
        return {'poc': current_close, 'vah': float(df['high'].max()), 'val': float(df['low'].min())}
    
    bins = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bins[:-1] + bins[1:]) / 2
    
    # Vectorized bucketing: assign each bar's volume to its price bin in one pass
    # (np.digitize + np.bincount) instead of an O(bars × bins) nested Python loop.
    closes = df['close'].to_numpy(dtype=float)
    volumes = df['volume'].to_numpy(dtype=float)
    idx = np.clip(np.digitize(closes, bins) - 1, 0, num_bins - 1)
    volume_at_price = np.bincount(idx, weights=volumes, minlength=num_bins)
    
    # Point of Control - highest volume price
    poc_idx = np.argmax(volume_at_price)
    poc = float(bin_centers[poc_idx])
    
    # Value Area (70% of volume)
    total_volume = volume_at_price.sum()
    if total_volume == 0:
        return {'poc': poc, 'vah': float(price_max), 'val': float(price_min)}
    
    # Value area grows CONTIGUOUSLY outward from the POC: start at the POC bin,
    # then repeatedly annex whichever adjacent bin (one above the current high or
    # one below the current low) carries more volume, until the accumulated volume
    # reaches 70% of the total. VAH/VAL then bound this contiguous region.
    target = total_volume * 0.7
    low_i = high_i = int(poc_idx)
    cumulative = float(volume_at_price[poc_idx])
    n = num_bins
    while cumulative < target and (low_i > 0 or high_i < n - 1):
        vol_below = volume_at_price[low_i - 1] if low_i > 0 else -1.0
        vol_above = volume_at_price[high_i + 1] if high_i < n - 1 else -1.0
        if vol_above >= vol_below:
            high_i += 1
            cumulative += float(volume_at_price[high_i])
        else:
            low_i -= 1
            cumulative += float(volume_at_price[low_i])

    vah = float(bin_centers[high_i])
    val = float(bin_centers[low_i])

    return {'poc': poc, 'vah': vah, 'val': val}


#############################################
# VOLATILITY INDICATORS
#############################################

def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate Average True Range
    
    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: ATR period
        
    Returns:
        Current ATR value
    """
    if df is None or len(df) < period + 1:
        return 0.0
    
    high = df['high']
    low = df['low']
    close = df['close']
    
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    
    atr = tr.rolling(window=period).mean()
    
    return float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0


def calculate_atr_percent(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate ATR as percentage of price
    
    Args:
        df: DataFrame with OHLC columns
        period: ATR period
        
    Returns:
        ATR percentage (e.g., 0.03 for 3%)
    """
    atr = calculate_atr(df, period)
    if atr == 0 or df is None or len(df) == 0:
        return 0.0
    
    current_price = float(df['close'].iloc[-1])
    if current_price <= 0:
        return 0.0
    
    return atr / current_price


def calculate_bollinger_bands(
    df: pd.DataFrame, 
    period: int = 20, 
    std_dev: float = 2.0
) -> Dict[str, float]:
    """Calculate Bollinger Bands
    
    Args:
        df: DataFrame with 'close' column
        period: SMA period
        std_dev: Standard deviation multiplier
        
    Returns:
        Dict with 'upper', 'middle', 'lower', 'width' values
    """
    if df is None or len(df) < period:
        return {'upper': 0, 'middle': 0, 'lower': 0, 'width': 0}
    
    middle = df['close'].rolling(window=period).mean()
    std = df['close'].rolling(window=period).std()
    
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    width = (upper - lower) / middle * 100  # Width as percentage
    
    return {
        'upper': float(upper.iloc[-1]) if not pd.isna(upper.iloc[-1]) else 0,
        'middle': float(middle.iloc[-1]) if not pd.isna(middle.iloc[-1]) else 0,
        'lower': float(lower.iloc[-1]) if not pd.isna(lower.iloc[-1]) else 0,
        'width': float(width.iloc[-1]) if not pd.isna(width.iloc[-1]) else 0,
    }


#############################################
# TREND ANALYSIS
#############################################

def detect_trend_structure(
    df: pd.DataFrame,
    current_price: float
) -> Tuple[str, int]:
    """Detect trend structure based on MA alignment (Blueprint Section 6)
    
    Args:
        df: DataFrame with OHLC data (daily preferred)
        current_price: Current price
        
    Returns:
        Tuple of (trend_state_name, trend_score)
    """
    if df is None or len(df) < 200:
        return 'NEUTRAL', TREND_STATES['NEUTRAL']
    
    # Calculate MAs
    ema_50 = calculate_ema(df, 50)
    sma_200 = calculate_sma(df, 200)
    
    if ema_50 is None or sma_200 is None:
        return 'NEUTRAL', TREND_STATES['NEUTRAL']
    
    ema_50_val = float(ema_50.iloc[-1])
    sma_200_val = float(sma_200.iloc[-1])
    
    # Check if 200 SMA is rising or falling
    sma_200_prev = float(sma_200.iloc[-20]) if len(sma_200) > 20 else sma_200_val
    sma_200_rising = sma_200_val > sma_200_prev
    
    # Check if 50 EMA is rising or falling
    ema_50_prev = float(ema_50.iloc[-10]) if len(ema_50) > 10 else ema_50_val
    ema_50_rising = ema_50_val > ema_50_prev
    
    # Determine trend state
    if current_price < sma_200_val and ema_50_val < sma_200_val:
        # Double Death: Price below 200 AND death cross
        return 'DOUBLE_DEATH', TREND_STATES['DOUBLE_DEATH']
    
    elif current_price < ema_50_val < sma_200_val:
        # Downtrend
        return 'DOWNTREND', TREND_STATES['DOWNTREND']
    
    elif current_price > ema_50_val > sma_200_val:
        if ema_50_rising and sma_200_rising:
            # Strong uptrend - all rising
            return 'STRONG_UPTREND', TREND_STATES['STRONG_UPTREND']
        else:
            # Regular uptrend
            return 'UPTREND', TREND_STATES['UPTREND']
    
    elif current_price > sma_200_val and not ema_50_rising:
        # Weakening - above 200 but 50 rolling over
        return 'WEAKENING', TREND_STATES['WEAKENING']
    
    else:
        # Neutral - at or near 200 DMA
        return 'NEUTRAL', TREND_STATES['NEUTRAL']


def calculate_higher_highs_lows(df: pd.DataFrame, periods: int = 5) -> Dict[str, bool]:
    """Detect higher highs / higher lows pattern
    
    Args:
        df: DataFrame with 'high' and 'low' columns
        periods: Number of periods to check
        
    Returns:
        Dict with 'higher_highs', 'higher_lows', 'lower_highs', 'lower_lows'
    """
    if df is None or len(df) < periods * 2:
        return {
            'higher_highs': False,
            'higher_lows': False,
            'lower_highs': False,
            'lower_lows': False
        }
    
    # Get recent swing points
    recent_highs = df['high'].iloc[-periods:]
    recent_lows = df['low'].iloc[-periods:]
    prev_highs = df['high'].iloc[-periods*2:-periods]
    prev_lows = df['low'].iloc[-periods*2:-periods]
    
    higher_highs = recent_highs.max() > prev_highs.max()
    higher_lows = recent_lows.min() > prev_lows.min()
    lower_highs = recent_highs.max() < prev_highs.max()
    lower_lows = recent_lows.min() < prev_lows.min()
    
    return {
        'higher_highs': higher_highs,
        'higher_lows': higher_lows,
        'lower_highs': lower_highs,
        'lower_lows': lower_lows
    }


#############################################
# EMA ALIGNMENT ANALYSIS
#############################################

def calculate_ema_alignment(
    data: Dict[str, pd.DataFrame],
    current_price: float
) -> Dict:
    """Calculate multi-timeframe EMA alignment (Blueprint style)
    
    Args:
        data: Dict mapping timeframe name to DataFrame
        current_price: Current price
        
    Returns:
        Dict with alignment_percentage, bias, and timeframe details
    """
    timeframes = []
    total_weight = 0
    weighted_sum = 0
    
    for tf_name, df in data.items():
        if df is None or len(df) < 50:
            continue
        
        ema12 = calculate_ema(df, 12)
        ema21 = calculate_ema(df, 21)
        ema50 = calculate_ema(df, 50)
        
        if ema12 is None or ema21 is None or ema50 is None:
            continue
        
        ema12_val = float(ema12.iloc[-1])
        ema21_val = float(ema21.iloc[-1])
        ema50_val = float(ema50.iloc[-1])
        
        # Determine alignment
        bullish = current_price > ema12_val > ema21_val > ema50_val
        bearish = current_price < ema12_val < ema21_val < ema50_val
        
        if bullish:
            status = 'BULLISH'
            score = 1
        elif bearish:
            status = 'BEARISH'
            score = -1
        else:
            status = 'MIXED'
            score = 0
        
        weight = TIMEFRAME_WEIGHTS.get(tf_name, 1.0)
        weighted_sum += score * weight
        total_weight += weight
        
        timeframes.append({
            'timeframe': tf_name,
            'price': current_price,
            'ema12': ema12_val,
            'ema21': ema21_val,
            'ema50': ema50_val,
            'status': status,
        })
    
    alignment_pct = (weighted_sum / total_weight * 100) if total_weight > 0 else 0
    
    if alignment_pct >= 50:
        bias = 'BULLISH'
    elif alignment_pct <= -50:
        bias = 'BEARISH'
    else:
        bias = 'NEUTRAL'
    
    return {
        'alignment_percentage': alignment_pct,
        'bias': bias,
        'timeframes': timeframes,
    }


#############################################
# RELATIVE STRENGTH
#############################################

def calculate_relative_strength(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    periods: List[int] = None
) -> Dict[str, float]:
    """Calculate relative strength vs benchmark (e.g., SPY)
    
    Args:
        stock_df: Stock DataFrame with 'close' column
        benchmark_df: Benchmark DataFrame with 'close' column
        periods: List of lookback periods in days
        
    Returns:
        Dict mapping period label to RS value (100 = parity)
    """
    if periods is None:
        periods = [5, 21, 63, 126]  # 1W, 1M, 3M, 6M
    
    labels = ['1W', '1M', '3M', '6M']
    results = {}
    
    if stock_df is None or benchmark_df is None:
        return {label: 100.0 for label in labels[:len(periods)]}
    
    for i, period in enumerate(periods):
        label = labels[i] if i < len(labels) else f'{period}D'
        
        if len(stock_df) <= period or len(benchmark_df) <= period:
            results[label] = 100.0
            continue
        
        stock_return = (
            stock_df['close'].iloc[-1] / stock_df['close'].iloc[-period-1] - 1
        )

        benchmark_return = (
            benchmark_df['close'].iloc[-1] / benchmark_df['close'].iloc[-period-1] - 1
        )

        # Parity-preserving ratio: 100 = parity, >100 = outperformance. Stable in
        # down markets (no sign inversion when the benchmark return is negative),
        # unlike the old return/return quotient.
        results[label] = 100.0 * (1.0 + stock_return) / (1.0 + benchmark_return)
    
    # Calculate composite RS
    if results:
        weights = [0.10, 0.25, 0.35, 0.30][:len(results)]
        total_weight = sum(weights)
        composite = sum(
            results[label] * w / total_weight 
            for label, w in zip(results.keys(), weights)
        )
        results['composite'] = composite
    
    return results


def is_rs_new_high(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    lookback: int = 50
) -> bool:
    """Check if RS line is at new high (leader signal - Blueprint Section 5)
    
    Args:
        stock_df: Stock DataFrame
        benchmark_df: Benchmark DataFrame
        lookback: Lookback period for high check
        
    Returns:
        True if RS at new high
    """
    if stock_df is None or benchmark_df is None:
        return False
    
    min_len = min(len(stock_df), len(benchmark_df))
    if min_len < lookback:
        return False
    
    # Calculate RS line (ratio of closes)
    stock_closes = stock_df['close'].iloc[-min_len:].values
    benchmark_closes = benchmark_df['close'].iloc[-min_len:].values
    
    rs_line = stock_closes / benchmark_closes
    
    # Check if current RS is at lookback high
    current_rs = rs_line[-1]
    rs_high = np.max(rs_line[-lookback:])
    
    return current_rs >= rs_high * 0.99  # Within 1% of high


#############################################
# SUPPORT/RESISTANCE
#############################################

def find_support_resistance(
    df: pd.DataFrame,
    num_levels: int = 3
) -> Dict[str, List[float]]:
    """Find key support and resistance levels
    
    Args:
        df: DataFrame with OHLC data
        num_levels: Number of levels to find
        
    Returns:
        Dict with 'support' and 'resistance' lists
    """
    if df is None or len(df) < 20:
        return {'support': [], 'resistance': []}
    
    current_price = float(df['close'].iloc[-1])
    
    # Get swing highs and lows
    highs = df['high'].values
    lows = df['low'].values
    
    # Simple approach: use recent highs/lows and round numbers
    potential_resistance = []
    potential_support = []
    
    # Recent highs as resistance
    for i in range(-20, -1):
        if i >= -len(df):
            high = float(df['high'].iloc[i])
            if high > current_price:
                potential_resistance.append(high)
    
    # Recent lows as support
    for i in range(-20, -1):
        if i >= -len(df):
            low = float(df['low'].iloc[i])
            if low < current_price:
                potential_support.append(low)
    
    # Add moving averages as support/resistance
    ema_50 = calculate_ema(df, 50)
    sma_200 = calculate_sma(df, 200)
    
    if ema_50 is not None:
        ma_50 = float(ema_50.iloc[-1])
        if ma_50 < current_price:
            potential_support.append(ma_50)
        else:
            potential_resistance.append(ma_50)
    
    if sma_200 is not None:
        ma_200 = float(sma_200.iloc[-1])
        if ma_200 < current_price:
            potential_support.append(ma_200)
        else:
            potential_resistance.append(ma_200)
    
    # Sort and deduplicate
    support = sorted(set(potential_support), reverse=True)[:num_levels]
    resistance = sorted(set(potential_resistance))[:num_levels]
    
    return {
        'support': support,
        'resistance': resistance
    }
