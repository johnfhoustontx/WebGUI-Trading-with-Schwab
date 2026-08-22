"""Pure swing (1-8 wk) factor library.

Each factor is ``(daily_df) -> pd.Series`` over a daily OHLCV frame (columns:
datetime, open, high, low, close, volume), sign-corrected so HIGHER = more
bullish. Factors return **raw, causal** values: the value at bar *t* depends
only on data at or before *t* — no temporal look-ahead. Winsorization and
standardization are NOT applied per-factor; they are applied **cross-sectionally
at scoring time** (across symbols per date, which has no temporal look-ahead).
The live value is the Series' last element, so the same code feeds the offline
backtest and the online scorer (no drift).

(The earlier per-factor ``winsorize`` clipped each bar using its *entire*
history — including future bars — so a historical bar's value depended on what
happened later: a look-ahead bias that would inflate measured IC in the
backtest. The ``winsorize`` utility is retained for the cross-sectional use.)

Reference-relative factors (RS) take an extra reference close Series aligned by
date. The FACTORS registry is the single source of truth for what exists; the
backtest decides which actually carry predictive IC.
"""
from typing import Optional

import numpy as np
import pandas as pd


def winsorize(s: pd.Series, lower: float = 0.02, upper: float = 0.98) -> pd.Series:
    """Clip a series to its [lower, upper] quantiles (robustness to outliers).

    A degenerate band (lo == hi, e.g. an almost-constant series) is left
    untouched so winsorizing never annihilates the live signal it is meant to
    protect.
    """
    s = pd.Series(s, dtype="float64")
    if s.dropna().empty:
        return s
    lo, hi = s.quantile(lower), s.quantile(upper)
    if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
        return s
    return s.clip(lower=lo, upper=hi)


def _close(df: pd.DataFrame) -> pd.Series:
    return pd.Series(df["close"].to_numpy(dtype="float64"),
                     index=pd.to_datetime(df["datetime"]))


def _ret(close: pd.Series, lookback: int, skip: int = 0) -> pd.Series:
    """Total return over `lookback` bars ending `skip` bars ago."""
    end = close.shift(skip)
    start = close.shift(skip + lookback)
    return end / start - 1.0


FACTORS: dict = {}  # name -> {"fn", "direction", "needs_ref", "desc"}


def _register(name, fn, direction=1, needs_ref=False, desc=""):
    FACTORS[name] = {"fn": fn, "direction": direction, "needs_ref": needs_ref, "desc": desc}


def mom_12_1(df: pd.DataFrame) -> pd.Series:
    return _ret(_close(df), lookback=252, skip=21)


def mom_6_1(df: pd.DataFrame) -> pd.Series:
    return _ret(_close(df), lookback=126, skip=21)


def pth(df: pd.DataFrame) -> pd.Series:
    close = _close(df)
    high_252 = close.rolling(252, min_periods=252).max()
    return (close / high_252).clip(0, 1.5)


def str_5d(df: pd.DataFrame) -> pd.Series:
    close = _close(df)
    return -(close / close.shift(5) - 1.0)


def _realized_vol(close: pd.Series, window: int = 60) -> pd.Series:
    return close.pct_change().rolling(window, min_periods=window).std()


def low_vol(df: pd.DataFrame) -> pd.Series:
    return -_realized_vol(_close(df))


def vol_adj_mom(df: pd.DataFrame) -> pd.Series:
    close = _close(df)
    r3 = close / close.shift(63) - 1.0
    vol = _realized_vol(close).replace(0, np.nan)
    return r3 / vol


def trend_quality(df: pd.DataFrame) -> pd.Series:
    close = _close(df)
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    dist = (close - ema200) / ema200
    stack = ((close > ema50).astype(float) + (ema50 > ema200).astype(float) - 1.0)
    out = dist + 0.02 * stack
    return out.where(close.expanding().count() >= 200)   # warmup gate: first 200 bars NaN


def _excess_return(sym_close, ref_close, lookback):
    if ref_close is None:
        return pd.Series(np.nan, index=sym_close.index)
    ref = ref_close.reindex(sym_close.index).ffill()
    return (sym_close / sym_close.shift(lookback)) - (ref / ref.shift(lookback))


def rs_spy(df: pd.DataFrame, ref_close: Optional[pd.Series] = None) -> pd.Series:
    return _excess_return(_close(df), ref_close, 63)


def rs_sector(df: pd.DataFrame, ref_close: Optional[pd.Series] = None) -> pd.Series:
    return _excess_return(_close(df), ref_close, 63)


# --- short-side slate ------------------------------------------------------
# The ten factors above describe a RISING name and express a falling one only by
# inversion. These four are asymmetric by construction: each measures something
# about the downside that a symmetric factor cannot represent. All follow the
# module contract — raw, causal, HIGHER = MORE BULLISH.

MAX_WINDOW = 21          # the "past month" of the MAX-effect literature
SEMI_WINDOW = 60         # matches _realized_vol's window, so the two are comparable
DBETA_WINDOW = 252


def max_effect(df: pd.DataFrame) -> pd.Series:
    """-(largest single-day return in the past month).

    Bali/Cakici/Whitelaw: stocks with an extreme recent daily gain subsequently
    UNDERPERFORM — investors overpay for lottery-like payoffs. Negated, so a
    spiky name scores low."""
    r = _close(df).pct_change()
    return -r.rolling(MAX_WINDOW, min_periods=MAX_WINDOW).max()


def semivol(df: pd.DataFrame) -> pd.Series:
    """-(downside semi-deviation of daily returns).

    Distinct from `low_vol`, which counts an upward jump as risk. Here only
    negative returns contribute, so a name that moves violently UP is not
    penalised — which is the asymmetry the short side turns on."""
    r = _close(df).pct_change()
    downside = r.where(r < 0, 0.0)
    sd = downside.pow(2).rolling(SEMI_WINDOW, min_periods=SEMI_WINDOW).mean().pow(0.5)
    return -sd


def downside_beta(df: pd.DataFrame, ref_close: Optional[pd.Series] = None) -> pd.Series:
    """-(beta measured on DOWN-market days only).

    A name's average beta says little about what it does when the tape breaks;
    downside beta is the part a short cares about. Negated, so a name that falls
    harder than the market scores low. NaN without a reference — an unmeasurable
    beta must not read as a beta of zero."""
    close = _close(df)
    if ref_close is None:
        return pd.Series(np.nan, index=close.index)
    ref = ref_close.reindex(close.index).ffill()
    r, m = close.pct_change(), ref.pct_change()
    down = m < 0
    rd, md = r.where(down), m.where(down)
    cov = (rd * md).rolling(DBETA_WINDOW, min_periods=DBETA_WINDOW // 4).mean() \
        - rd.rolling(DBETA_WINDOW, min_periods=DBETA_WINDOW // 4).mean() \
        * md.rolling(DBETA_WINDOW, min_periods=DBETA_WINDOW // 4).mean()
    var = md.rolling(DBETA_WINDOW, min_periods=DBETA_WINDOW // 4).var()
    return -(cov / var.replace(0, np.nan))


def below_200ema(df: pd.DataFrame) -> pd.Series:
    """min(0, close/EMA200 - 1) — how far BELOW the long-term mean, or 0 above it.

    Deliberately one-sided: `trend_quality` already carries the upside, and a
    symmetric distance would pay this factor twice for the same information.
    What it adds is the broken-name tail, where the short candidates live."""
    close = _close(df)
    ema200 = close.ewm(span=200, adjust=False).mean()
    out = ((close - ema200) / ema200).clip(upper=0.0)
    return out.where(close.expanding().count() >= 200)   # same warmup as trend_quality


def turnover(df: pd.DataFrame) -> pd.Series:
    vol = pd.Series(df["volume"].to_numpy(dtype="float64"),
                    index=pd.to_datetime(df["datetime"]))
    return vol / vol.rolling(63, min_periods=63).mean()


_register("mom_12_1", mom_12_1, desc="12-1 intermediate momentum")
_register("mom_6_1", mom_6_1, desc="6-1 momentum")
_register("pth", pth, desc="price / 252d high (anchoring)")
_register("str_5d", str_5d, desc="short-term (5d) reversal, sign-corrected")
_register("low_vol", low_vol, desc="-(60d realized vol)")
_register("vol_adj_mom", vol_adj_mom, desc="3m return / realized vol")
_register("trend_quality", trend_quality, desc="distance above 50/200 EMA stack")
_register("rs_spy", rs_spy, needs_ref=True, desc="63d excess return vs SPY")
_register("rs_sector", rs_sector, needs_ref=True, desc="63d excess return vs sector ETF")
_register("turnover", turnover, desc="volume / 63d avg (conditioning var)")
_register("max_effect", max_effect, desc="-(max 1d return over 21d) — lottery preference")
_register("semivol", semivol, desc="-(60d downside semi-deviation)")
_register("downside_beta", downside_beta, needs_ref=True,
          desc="-(beta on down-market days, 252d)")
_register("below_200ema", below_200ema, desc="min(0, close/EMA200-1) — one-sided break")


def compute_factor_frame(df, spy_close=None, sector_close=None) -> pd.DataFrame:
    """Build a DataFrame (index = symbol dates, columns = every registered factor),
    each column sign-corrected (x direction). Reference-relative factors get SPY /
    sector closes; missing refs -> NaN column (the backtest/scorer tolerate NaN)."""
    out = {}
    for name, spec in FACTORS.items():
        try:
            if spec["needs_ref"]:
                ref = sector_close if name == "rs_sector" else spy_close
                s = spec["fn"](df, ref_close=ref)
            else:
                s = spec["fn"](df)
            out[name] = s * spec["direction"]
        except Exception:
            out[name] = pd.Series(np.nan, index=pd.to_datetime(df["datetime"]))
    return pd.DataFrame(out)
