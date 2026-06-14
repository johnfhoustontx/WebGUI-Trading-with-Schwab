"""
feature_engineer.py - Compute F1-F25 feature vectors from OHLCV bars
Version: 1.0.1
Last Updated: 2026-05-24

Replicates FeatureExtractor.cs from the NinjaTrader ML ensemble system.
Used by morning_agent.py to compute features for /warmup and /ensemble calls.

Feature set (25 features):
  F1  RSI(14) smoothed EMA(3)     F14 ROC(9)
  F2  MACD Histogram              F15 MFI(14)
  F3  ADX(14)                     F16 Williams %R(14)
  F4  ATR Norm %                  F17 Volume Oscillator %
  F5  Relative Volume             F18 MACD Signal line
  F6  Price vs SMA20 %            F19 Dist SMA50 %
  F7  Intraday Range %            F20 Keltner Channel Position
  F8  Bar Direction               F21 VWAP Dist %
  F9  Bar Return %                F22 Order Flow Imbalance
  F10 Bollinger Band Width %      F23 HTF Trend (SMA200)
  F11 Stochastic %K               F24 Momentum Divergence(6)
  F12 Stochastic %D               F25 Session Progress
  F13 CCI(20)

Version 1.0.1 Changes:
- Fixed RSI: wrap avg_gain/avg_loss divide with np.errstate to suppress RuntimeWarning
- Fixed F25: normalise datetimes param to DatetimeIndex before .tz access (Series vs Index)

Version 1.0.0 Changes:
- Initial implementation matching FeatureExtractor.cs definitions
"""

import numpy as np
import pandas as pd

#############################################
# SESSION CONSTANTS
#############################################

# RTH: 9:30 ET = 8:30 CT
RTH_START_MINUTES = 9 * 60 + 30   # minutes from midnight ET
RTH_END_MINUTES   = 16 * 60        # 16:00 ET

# Approx session length in 5-min bars
RTH_BARS = 78    # 390 min / 5 = 78 bars


#############################################
# INDICATOR HELPERS
#############################################

def _ema(series: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(series).ewm(span=span, adjust=False).mean().values


def _sma(series: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(series).rolling(period, min_periods=1).mean().values


def _rolling_min(series: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(series).rolling(period, min_periods=1).min().values


def _rolling_max(series: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(series).rolling(period, min_periods=1).max().values


def _rolling_std(series: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(series).rolling(period, min_periods=1).std().values


def _prev(series: np.ndarray, n: int = 1) -> np.ndarray:
    """Shift series forward by n, filling start with first value."""
    shifted     = np.roll(series, n)
    shifted[:n] = series[0]
    return shifted


def _true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    prev_c = _prev(c)
    return np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))


def _atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int) -> np.ndarray:
    tr = _true_range(h, l, c)
    return pd.Series(tr).ewm(alpha=1 / period, adjust=False).mean().values


def _rsi(c: np.ndarray, period: int = 14) -> np.ndarray:
    delta    = np.diff(c, prepend=c[0])
    gain     = np.where(delta > 0, delta, 0.0)
    loss     = np.where(delta < 0, -delta, 0.0)
    avg_gain = pd.Series(gain).ewm(alpha=1 / period, adjust=False).mean().values
    avg_loss = pd.Series(loss).ewm(alpha=1 / period, adjust=False).mean().values
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss < 1e-10, 100.0, avg_gain / avg_loss)
    return 100.0 - (100.0 / (1.0 + rs))


#############################################
# FEATURE COMPUTATION
#############################################

def compute_features(
    opens:     np.ndarray,
    highs:     np.ndarray,
    lows:      np.ndarray,
    closes:    np.ndarray,
    volumes:   np.ndarray,
    datetimes: pd.DatetimeIndex | None = None,
) -> np.ndarray:
    """
    Compute F1-F25 feature matrix from raw OHLCV arrays.

    Args:
        opens, highs, lows, closes, volumes: 1-D arrays of equal length
        datetimes: optional Series or DatetimeIndex (UTC) for F25 session progress

    Returns:
        np.ndarray of shape (n_bars, 25) — NaN in early rows where lookback insufficient.
        Caller should slice off NaN rows before sending to ML server.
    """
    o, h, l, c, v = opens, highs, lows, closes, volumes.astype(float)
    n    = len(c)
    safe = lambda x: np.where(np.abs(x) < 1e-10, 1e-10, x)

    # ---- F1: RSI(14) smoothed with EMA(3) ----
    rsi14 = _rsi(c, 14)
    f1    = _ema(rsi14, 3)

    # ---- F2: MACD Histogram,  F18: MACD Signal ----
    ema12       = _ema(c, 12)
    ema26       = _ema(c, 26)
    macd_line   = ema12 - ema26
    signal_line = _ema(macd_line, 9)
    f2  = macd_line - signal_line
    f18 = signal_line

    # ---- F3: ADX(14) ----
    tr       = _true_range(h, l, c)
    prev_h   = _prev(h);  prev_l = _prev(l)
    dm_plus  = np.where((h - prev_h) > (prev_l - l), np.maximum(h - prev_h, 0.0), 0.0)
    dm_minus = np.where((prev_l - l) > (h - prev_h), np.maximum(prev_l - l, 0.0), 0.0)
    dm_plus[0] = dm_minus[0] = 0.0
    atr14    = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values
    di_plus  = 100.0 * pd.Series(dm_plus).ewm(alpha=1/14, adjust=False).mean().values / safe(atr14)
    di_minus = 100.0 * pd.Series(dm_minus).ewm(alpha=1/14, adjust=False).mean().values / safe(atr14)
    dx       = 100.0 * np.abs(di_plus - di_minus) / safe(di_plus + di_minus)
    f3       = pd.Series(dx).ewm(alpha=1/14, adjust=False).mean().values

    # ---- F4: ATR Norm % ----
    f4 = atr14 / safe(c) * 100.0

    # ---- F5: Relative Volume ----
    vol_sma20 = _sma(v, 20)
    f5 = v / safe(vol_sma20)

    # ---- F6: Price vs SMA20 % ----
    sma20 = _sma(c, 20)
    f6    = (c - sma20) / safe(sma20) * 100.0

    # ---- F7: Intraday Range % ----
    f7 = (h - l) / safe(c) * 100.0

    # ---- F8: Bar Direction ----
    f8 = np.sign(c - o).astype(float)

    # ---- F9: Bar Return % ----
    f9    = (c - _prev(c)) / safe(np.abs(_prev(c))) * 100.0
    f9[0] = 0.0

    # ---- F10: Bollinger Band Width % ----
    bb_std   = _rolling_std(c, 20)
    bb_upper = sma20 + 2.0 * bb_std
    bb_lower = sma20 - 2.0 * bb_std
    f10 = (bb_upper - bb_lower) / safe(sma20) * 100.0

    # ---- F11: Stochastic %K,  F12: %D ----
    ll14    = _rolling_min(l, 14)
    hh14    = _rolling_max(h, 14)
    stoch_k = 100.0 * (c - ll14) / safe(hh14 - ll14)
    f11     = stoch_k
    f12     = _sma(stoch_k, 3)

    # ---- F13: CCI(20) ----
    tp      = (h + l + c) / 3.0
    sma_tp  = _sma(tp, 20)
    mean_dev = pd.Series(tp).rolling(20).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
    ).values
    f13 = (tp - sma_tp) / safe(0.015 * mean_dev)

    # ---- F14: ROC(9) ----
    c_9 = _prev(c, 9)
    f14 = (c - c_9) / safe(np.abs(c_9)) * 100.0

    # ---- F15: MFI(14) ----
    prev_tp  = _prev(tp)
    raw_mf   = tp * v
    up_mf    = np.where(tp > prev_tp, raw_mf, 0.0)
    dn_mf    = np.where(tp < prev_tp, raw_mf, 0.0)
    up_mf[0] = dn_mf[0] = 0.0
    sum_up   = pd.Series(up_mf).rolling(14, min_periods=1).sum().values
    sum_dn   = pd.Series(dn_mf).rolling(14, min_periods=1).sum().values
    f15 = 100.0 - (100.0 / (1.0 + sum_up / safe(sum_dn)))

    # ---- F16: Williams %R(14) ----
    hh14_w = _rolling_max(h, 14)
    ll14_w = _rolling_min(l, 14)
    f16 = (hh14_w - c) / safe(hh14_w - ll14_w) * -100.0

    # ---- F17: Volume Oscillator % ----
    vol_ema5  = _ema(v, 5)
    vol_ema20 = _ema(v, 20)
    f17 = (vol_ema5 - vol_ema20) / safe(vol_ema20) * 100.0

    # F18 computed above

    # ---- F19: Dist SMA50 % ----
    sma50 = _sma(c, 50)
    f19   = (c - sma50) / safe(sma50) * 100.0

    # ---- F20: Keltner Channel Position (-1 to 1) ----
    kc_mid   = _ema(c, 20)
    atr10    = _atr(h, l, c, 10)
    kc_upper = kc_mid + 2.0 * atr10
    kc_lower = kc_mid - 2.0 * atr10
    kc_half  = safe((kc_upper - kc_lower) / 2.0)
    f20 = np.clip((c - kc_mid) / kc_half, -1.0, 1.0)

    # ---- F21: VWAP Dist % (rolling session-length window) ----
    vwap_win = min(RTH_BARS, n)
    cum_pv   = pd.Series(tp * v).rolling(vwap_win, min_periods=1).sum().values
    cum_v    = pd.Series(v).rolling(vwap_win, min_periods=1).sum().values
    vwap     = cum_pv / safe(cum_v)
    f21      = (c - vwap) / safe(vwap) * 100.0

    # ---- F22: Order Flow Imbalance ----
    hl_range = h - l
    f22 = np.where(hl_range > 1e-10, (c - o) / hl_range, 0.0)

    # ---- F23: HTF Trend (Close vs SMA200) ----
    sma200 = _sma(c, 200)
    f23    = np.where(c > sma200, 1.0, -1.0).astype(float)

    # ---- F24: Momentum Divergence(6) ----
    c_6           = _prev(c, 6)
    rsi_6         = _prev(rsi14, 6)
    price_mom     = c - c_6
    rsi_mom       = rsi14 - rsi_6
    price_mom[:6] = rsi_mom[:6] = 0.0
    f24 = np.where(np.sign(price_mom) == np.sign(rsi_mom), 1.0, -1.0).astype(float)

    # ---- F25: Session Progress (0.0=open, 1.0=close) ----
    if datetimes is not None:
        # Accept both Series and DatetimeIndex; datetimes come in as UTC from Schwab
        dti = pd.DatetimeIndex(datetimes)
        dti = (dti.tz_convert("America/New_York")
               if dti.tz is not None
               else dti.tz_localize("UTC").tz_convert("America/New_York"))
        mins = dti.hour * 60 + dti.minute
        prog = np.clip(
            (mins - RTH_START_MINUTES) / (RTH_END_MINUTES - RTH_START_MINUTES),
            0.0, 1.0
        )
        f25 = prog.to_numpy().astype(float)
    else:
        f25 = np.array([(i % RTH_BARS) / RTH_BARS for i in range(n)])

    # ---- Stack into (n, 25) matrix ----
    return np.column_stack([
        f1, f2, f3, f4, f5,
        f6, f7, f8, f9, f10,
        f11, f12, f13, f14, f15,
        f16, f17, f18, f19, f20,
        f21, f22, f23, f24, f25,
    ])


def get_valid_features(features: np.ndarray, n_bars: int = 65) -> np.ndarray:
    """
    Remove rows with NaN/Inf and return the last n_bars valid rows.
    Used to prepare the warmup array and the current feature vector.
    """
    mask  = np.all(np.isfinite(features), axis=1)
    valid = features[mask]
    return valid if len(valid) <= n_bars else valid[-n_bars:]
