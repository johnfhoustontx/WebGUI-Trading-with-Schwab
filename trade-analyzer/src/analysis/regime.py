"""Daily-bar market regime, from SPY closes alone. PURE and CAUSAL.

Shared by the offline fit (which estimates weights per regime) and the live
scorer (which asks what regime today is). One implementation, so the label a
symbol is scored under is the same label its weights were fitted on.

**Why this exists.** `low_vol` carries 39% of the model's absolute weight on an
INVERTED sign — the model rewards high volatility. That is a regime artifact:
the fit window is dominated by tapes where high-beta names led. Estimating
weights per regime is the documented fix (C13), and the artifact has carried
empty regime keys waiting for it since the model shipped.

**Causality is the invariant.** The label at bar t may use only data at or
before t, so the volatility reference is a TRAILING quantile, never a
full-sample one. A full-sample quantile would classify 2022 using 2026's
distribution and inflate every per-regime IC in the backtest — the same
look-ahead already removed from per-factor winsorization once.

The three regimes are deliberately coarse. Per-regime fits split the sample, so
each extra regime costs statistical power in a model whose whole edge is thin;
three is the most this sample size supports.

⚠ This is NOT the sentiment stack's five-state `market_regime` classifier. That
one reads 5-minute bars plus breadth, VIX and dealer positioning and answers
"what is the tape doing right now"; this one reads daily SPY and answers "which
weight set should score a 20-day forward view". Different inputs, different
horizon, different consumer — and it cannot import that one anyway (different
tier, and the documented cross-app `scoring` collision).
"""
import numpy as np
import pandas as pd

REGIMES = ("trend", "chop", "highvol")

VOL_WINDOW = 20            # realized-vol lookback (trading days)
VOL_REF_WINDOW = 252       # trailing window the vol is judged against
VOL_HI_Q = 0.80            # above its own trailing 80th percentile = elevated
EMA_SPAN = 200             # the long-term mean price is measured against
TREND_DIST = 0.03          # |close/EMA200 - 1| beyond this = displaced, either side

# Enough history for the 200-EMA to mean anything AND for the trailing vol
# quantile to have a full reference window.
WARMUP = VOL_REF_WINDOW + VOL_WINDOW


def classify(close) -> pd.Series:
    """A regime label per bar, NaN through warmup. Causal at every bar."""
    s = pd.Series(close, dtype="float64").dropna()
    out = pd.Series(np.nan, index=s.index, dtype="object")
    if len(s) < WARMUP:
        return out

    vol = s.pct_change().rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std()
    # TRAILING quantile: bar t is compared against the VOL_REF_WINDOW bars up to
    # and including t. `rolling(...).quantile` is causal by construction.
    vol_ref = vol.rolling(VOL_REF_WINDOW, min_periods=VOL_REF_WINDOW).quantile(VOL_HI_Q)
    ema = s.ewm(span=EMA_SPAN, adjust=False).mean()
    dist = (s - ema) / ema

    ready = vol.notna() & vol_ref.notna() & (s.expanding().count() >= WARMUP)
    highvol = ready & (vol > vol_ref)
    trend = ready & ~highvol & (dist.abs() > TREND_DIST)

    out[ready] = "chop"
    out[trend] = "trend"
    out[highvol] = "highvol"          # a panic is volatile FIRST — see the tests
    return out


def current_regime(close):
    """Today's regime label, or None when there is not enough history.

    None is a real answer: the caller falls back to the pooled ``"all"`` weights
    rather than guessing a regime."""
    try:
        lab = classify(close).dropna()
        return str(lab.iloc[-1]) if len(lab) else None
    except Exception:
        return None
