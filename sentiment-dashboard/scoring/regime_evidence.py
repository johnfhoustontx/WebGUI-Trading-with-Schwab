"""Bar-derived evidence assembler for the market-regime blended classifier.

Turns ALREADY-FETCHED market data — today's 5-min OHLCV bars, ~10 recent daily
bars, a VIX snapshot, one index matrix row, and the trailing Bollinger-width
series — into the flat evidence dict that ``market_regime.score_regimes``
consumes (exactly the ``market_regime.EVIDENCE_KEYS``). PURE: no network, no
Redis; a caller supplies the data. DEFENSIVE: every key is wrapped in its own
try/except and degrades to ``None`` on thin/missing/malformed input — the
function never raises, so one bad input can only blank its own key.

Reuse map (kept clean — these map directly to evidence keys):
- ``technical`` (shared/analysis_lib, imported standalone to dodge the package
  ``__init__`` that eagerly loads a broken schwab_client): ADX, EMA, VWAP,
  relative volume.
- ``scoring.volatility``: ATR, Bollinger-width %, percentile-of-last.
- ``scoring.profile_shape``: ``classify_profile_shape(...).balance_strength``.

Deliberately NOT reused: ``session_structure`` / ``rejection_defense`` — those
return SIGNED BLENDED scores (resilience − rejection etc.) that don't map to the
raw magnitudes this evidence needs (``vwap_hold_frac`` a 0.5..1 magnitude,
``or_break_state`` an enum, ``wick_two_sided`` a both-sides score that must not
cancel). The concepts are re-derived here as small LOCAL helpers instead.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

from scoring import profile_shape as _profile
from scoring import volatility as _vol

# ── isolated standalone import of the shared indicator lib ──
# shared/analysis_lib/__init__.py eagerly imports a broken schwab_client, so the
# package import path fails; add the dir to sys.path and import `technical`
# directly (the same pattern services/trade_svc/compute.py uses). `technical`
# itself imports `config`, resolved from the same dir. Defensive: on ANY failure
# `technical` stays None and every technical-derived key degrades to None.
try:  # pragma: no cover - exercised implicitly, guarded for robustness
    _ANALYSIS_LIB = Path(__file__).resolve().parents[2] / "shared" / "analysis_lib"
    if str(_ANALYSIS_LIB) not in sys.path:
        sys.path.insert(0, str(_ANALYSIS_LIB))
    import technical  # noqa: E402
except Exception:  # pragma: no cover
    technical = None  # type: ignore[assignment]


_BOLL_N = _vol.BOLL_PERIOD          # 20
_BOLL_K = _vol.BOLL_K               # 2.0
_ADX_MIN = 28                       # calculate_adx returns a 20.0 DEFAULT below 2*period
_RELVOL_N = 20                      # calculate_relative_volume default look-back
_OR_BARS = 6                        # opening-range = first N 5-min bars (~30 min)
_HUG_LAST = 12                      # band_hug over the last 12 closes
_TERM_TOL = 0.1                     # or_break "held" cushion, fraction of OR height
_VWAP_MIN = 10                      # min bars for a meaningful session VWAP-hold read


def _cols(df):
    """(highs, lows, closes) float lists from a bars frame, or None if unusable."""
    try:
        return (df["high"].astype(float).tolist(),
                df["low"].astype(float).tolist(),
                df["close"].astype(float).tolist())
    except Exception:
        return None


def _finite(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------- ADX


def _adx(df):
    if technical is None or df is None or len(df) < _ADX_MIN:
        return None
    return _finite(technical.calculate_adx(df))


def _adx_rising(df):
    # ADX "now" vs ADX ~30 min (6 bars) earlier — both need the full ADX window.
    if technical is None or df is None or len(df) < _ADX_MIN + _OR_BARS:
        return None
    now = _finite(technical.calculate_adx(df))
    prev = _finite(technical.calculate_adx(df.iloc[:-_OR_BARS]))
    if now is None or prev is None:
        return None
    return bool(now > prev)


# ---------------------------------------------------------------- EMA slope


def _ema_slope_atr(df):
    if technical is None or df is None or len(df) < _BOLL_N + 5:
        return None
    ema = technical.calculate_ema(df, 20)
    if ema is None or len(ema) < 6:
        return None
    last = _finite(ema.iloc[-1])
    back = _finite(ema.iloc[-6])
    if last is None or back is None:
        return None
    cols = _cols(df)
    if cols is None:
        return None
    a = _vol.atr(*cols)
    if a is None or a <= 0:
        return None
    return ((last - back) / 5.0) / a


# ---------------------------------------------------------------- Bollinger width


def _closes(df):
    try:
        return df["close"].astype(float).tolist()
    except Exception:
        return None


def _bb_width_pctile(df, prior_widths):
    if prior_widths is None:
        return None
    closes = _closes(df)
    if closes is None:
        return None
    today = _vol.bollinger_width_pct(closes)
    if today is None:
        return None
    try:
        series = list(prior_widths) + [today]
    except TypeError:
        return None
    return _vol.percentile_of_last(series)


def _bb_width_expansion(df):
    closes = _closes(df)
    if closes is None or len(closes) < _BOLL_N + _OR_BARS:
        return None
    now = _vol.bollinger_width_pct(closes)
    then = _vol.bollinger_width_pct(closes[:-_OR_BARS])
    if now is None or then is None or then <= 0:
        return None
    return now / then


def _band_hug_frac(df):
    closes = _closes(df)
    if closes is None or len(closes) < _BOLL_N + _HUG_LAST:
        return None
    arr = np.asarray(closes, dtype=float)
    hits = 0
    for i in range(len(arr) - _HUG_LAST, len(arr)):
        window = arr[i - _BOLL_N + 1:i + 1]
        mid = float(window.mean())
        sd = float(window.std(ddof=0))
        # |close - mid| > 0.5 * (k * std) == > k/2 * std (outer half of the band).
        if abs(arr[i] - mid) > 0.5 * _BOLL_K * sd:
            hits += 1
    return hits / _HUG_LAST


# ---------------------------------------------------------------- VWAP hold


def _vwap_hold_frac(df):
    if technical is None or df is None or len(df) < _VWAP_MIN:
        return None
    vwap = technical.calculate_vwap(df)
    if vwap is None:
        return None
    closes = _closes(df)
    if not closes:
        return None
    n = len(closes)
    above = sum(1 for c in closes if c > vwap)
    below = sum(1 for c in closes if c < vwap)
    return max(above, below) / n


# ---------------------------------------------------------------- opening range


def _opening_range(df):
    """(or_high, or_low, height, closes) or None. height <= 0 -> degenerate None."""
    if df is None or len(df) < _OR_BARS + 2:
        return None
    cols = _cols(df)
    if cols is None:
        return None
    highs, lows, closes = cols
    or_high = max(highs[:_OR_BARS])
    or_low = min(lows[:_OR_BARS])
    height = or_high - or_low
    if not math.isfinite(height) or height <= 0:
        return None
    return or_high, or_low, height, closes


def _or_break_state(df):
    parsed = _opening_range(df)
    if parsed is None:
        return None
    or_high, or_low, height, closes = parsed
    post = closes[_OR_BARS:]
    broke = any(c > or_high or c < or_low for c in post)
    last = closes[-1]
    final = closes[-3:]
    held_up = last > or_high + _TERM_TOL * height and all(c > or_high for c in final)
    held_down = last < or_low - _TERM_TOL * height and all(c < or_low for c in final)
    if held_up or held_down:
        return "held"
    if broke and or_low <= last <= or_high:
        return "failed"
    return "none"


def _or_failed_count(df):
    parsed = _opening_range(df)
    if parsed is None:
        return None
    or_high, or_low, _height, closes = parsed
    post = closes[_OR_BARS:]
    count = 0
    in_break = False
    for c in post:
        beyond = c > or_high or c < or_low
        if beyond and not in_break:
            in_break = True
        elif not beyond and in_break:
            count += 1        # broke out then closed back inside -> a failed break
            in_break = False
    return count


# ---------------------------------------------------------------- wicks


def _wick_two_sided(df):
    if df is None or len(df) < 5:
        return None
    cols = _cols(df)
    if cols is None:
        return None
    highs, lows, closes = cols
    try:
        opens = df["open"].astype(float).tolist()
    except Exception:
        opens = list(closes)
    sess_hi, sess_lo = max(highs), min(lows)
    rng = sess_hi - sess_lo
    if not math.isfinite(rng) or rng <= 0:
        return None
    near_hi_zone = sess_hi - 0.25 * rng
    near_lo_zone = sess_lo + 0.25 * rng
    upper, lower = [], []
    for o, h, lo, c in zip(opens, highs, lows, closes):
        bar = h - lo
        if bar <= 0:
            continue
        if h >= near_hi_zone:
            upper.append((h - max(o, c)) / bar)      # rejection wick above
        if lo <= near_lo_zone:
            lower.append((min(o, c) - lo) / bar)     # defended tail below
    mean_up = sum(upper) / len(upper) if upper else 0.0
    mean_lo = sum(lower) / len(lower) if lower else 0.0
    # min() -> only HIGH when BOTH tails are active (both-sided churn), never
    # cancelling as a signed difference would.
    return max(0.0, min(1.0, min(mean_up, mean_lo)))


# ---------------------------------------------------------------- whipsaw


def _whipsaw_count(df):
    if technical is None or df is None or len(df) < _BOLL_N:
        return None
    ema = technical.calculate_ema(df, 20)
    if ema is None:
        return None
    closes = _closes(df)
    if closes is None:
        return None
    prev_sign = 0
    crosses = 0
    for c, e in zip(closes, ema.tolist()):
        ev = _finite(e)
        if ev is None:
            continue
        d = c - ev
        sign = 1 if d > 0 else (-1 if d < 0 else 0)
        if sign != 0 and prev_sign != 0 and sign != prev_sign:
            crosses += 1
        if sign != 0:
            prev_sign = sign
    return crosses


# ---------------------------------------------------------------- profile


def _profile_balance(df):
    if df is None or len(df) < 10:
        return None
    try:
        bars = [{"high": float(h), "low": float(lo), "close": float(c),
                 "volume": float(v)}
                for h, lo, c, v in zip(df["high"], df["low"], df["close"],
                                       df["volume"])]
    except Exception:
        return None
    shape = _profile.classify_profile_shape(bars, n_bins=20)
    return _finite(shape.balance_strength)


# ---------------------------------------------------------------- relative volume


def _rel_vol(df):
    if technical is None or df is None or len(df) < _RELVOL_N:
        return None
    ratio, _vol_today = technical.calculate_relative_volume(df)
    return _finite(ratio)


# ---------------------------------------------------------------- daily-derived


def _daily_true_ranges(daily):
    cols = _cols(daily)
    if cols is None:
        return None
    highs, lows, closes = cols
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        pc = closes[i - 1]
        trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return trs


def _atr_pctile(daily):
    if daily is None or len(daily) < _vol.PCTL_MIN_N:
        return None
    trs = _daily_true_ranges(daily)
    if trs is None:
        return None
    return _vol.percentile_of_last(trs)


def _gap(daily):
    """(gap_open_pct, gap_filled) or (None, None) from the last two daily bars."""
    if daily is None or len(daily) < 2:
        return None, None
    try:
        prior_close = float(daily["close"].iloc[-2])
        today_open = float(daily["open"].iloc[-1])
        today_high = float(daily["high"].iloc[-1])
        today_low = float(daily["low"].iloc[-1])
    except Exception:
        return None, None
    if not (math.isfinite(prior_close) and prior_close != 0):
        return None, None
    pct = (today_open - prior_close) / prior_close * 100.0
    if not math.isfinite(pct):
        return None, None
    filled = bool(today_low <= prior_close <= today_high)
    return pct, filled


# ---------------------------------------------------------------- vix-derived


def _term_inversion(vix):
    if not isinstance(vix, dict):
        return None
    v = _finite(vix.get("vix"))
    v1 = _finite(vix.get("vix1d"))
    v3 = _finite(vix.get("vix3m"))
    if v is None or v1 is None or v3 is None or v <= 0:
        return None
    # 0..1 inversion depth: front-month fear (vix1d>vix) and/or term inversion
    # (vix>vix3m). Each leg clamped at 0 so normal contango reads 0 and the legs
    # never cancel; summed then clamped to 1.
    front = max(0.0, (v1 - v) / v)
    back = max(0.0, (v - v3) / v)
    return max(0.0, min(1.0, front + back))


def _vix1d_spike_pct(vix):
    if not isinstance(vix, dict):
        return None
    v1 = _finite(vix.get("vix1d"))
    prev = _finite(vix.get("vix1d_prev"))
    if v1 is None or prev is None or prev <= 0:
        return None
    return (v1 - prev) / prev * 100.0


# ---------------------------------------------------------------- matrix-derived


def _above_flip(matrix_row):
    if not isinstance(matrix_row, dict):
        return None
    regime = matrix_row.get("gex_regime")
    if regime == "above":
        return True
    if regime == "below":
        return False
    return None


def _below_flip_deep(matrix_row):
    if not isinstance(matrix_row, dict):
        return None
    return _finite(matrix_row.get("below_flip_deep"))


# ---------------------------------------------------------------- assembler


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def evidence_from_bars(bars_5m, daily, vix, matrix_row, prior_widths):
    """Assemble the flat regime-evidence dict from already-fetched market data.

    Returns a dict with EXACTLY ``market_regime.EVIDENCE_KEYS`` (20 keys); every
    key degrades to ``None`` when its inputs are missing/thin. Never raises.
    """
    gap = _safe(_gap, daily)
    gap_pct, gap_filled = gap if isinstance(gap, tuple) and len(gap) == 2 else (None, None)
    return {
        "adx": _safe(_adx, bars_5m),
        "adx_rising": _safe(_adx_rising, bars_5m),
        "ema_slope_atr": _safe(_ema_slope_atr, bars_5m),
        "bb_width_pctile": _safe(_bb_width_pctile, bars_5m, prior_widths),
        "bb_width_expansion": _safe(_bb_width_expansion, bars_5m),
        "band_hug_frac": _safe(_band_hug_frac, bars_5m),
        "vwap_hold_frac": _safe(_vwap_hold_frac, bars_5m),
        "or_break_state": _safe(_or_break_state, bars_5m),
        "or_failed_count": _safe(_or_failed_count, bars_5m),
        "wick_two_sided": _safe(_wick_two_sided, bars_5m),
        "whipsaw_count": _safe(_whipsaw_count, bars_5m),
        "profile_balance": _safe(_profile_balance, bars_5m),
        "rel_vol": _safe(_rel_vol, bars_5m),
        "atr_pctile": _safe(_atr_pctile, daily),
        "vix1d_spike_pct": _safe(_vix1d_spike_pct, vix),
        "term_inversion": _safe(_term_inversion, vix),
        "gap_open_pct": gap_pct,
        "gap_filled": gap_filled,
        "above_flip": _safe(_above_flip, matrix_row),
        "below_flip_deep": _safe(_below_flip_deep, matrix_row),
    }
