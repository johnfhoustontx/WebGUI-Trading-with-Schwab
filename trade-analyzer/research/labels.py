"""The prediction label — and the beta adjustment that makes it mean something.

The shipping label is ``r_symbol - r_SPY``, a RAW excess return. A high-beta
stock earns positive raw excess whenever the market rises, mechanically: no
skill, just leverage. Fit over a window that was mostly a bull market, ANY model
built on this label will discover that high-volatility names "outperform" —
because on this label, they must.

Measured on the 173-name panel: the composite's cross-sectional IC is **+0.16
when SPY's forward 20 days are up and -0.11 when they are down**, and nine of
fourteen factors flip sign with the market, including every member of the risk
cluster that carries 68% of the model's weight. The down-market weight set is
nearly the negation of the up-market one. That is not an edge with a caveat.

``r_symbol - beta * r_market`` prices out exactly the part leverage explains, so
what is left is the cross-sectional question the model was supposed to be
asking. Beta is estimated on a TRAILING window — a full-sample beta would leak
the future into the label itself, which is the one place a leak inflates
everything downstream at once.
"""
import numpy as np
import pandas as pd

BETA_WINDOW = 252          # one trading year of daily returns
BETA_MIN_PERIODS = 126     # half a year before a beta is trusted


def rolling_beta(close, ref_close, window=BETA_WINDOW,
                 min_periods=BETA_MIN_PERIODS) -> pd.Series:
    """Trailing beta of ``close`` against ``ref_close``. Causal; NaN in warmup.

    NaN rather than a default of 1.0: an unmeasured beta must not silently
    become "market-like", which would leave a leveraged name's exposure in the
    label under a number that claims to have removed it."""
    s = pd.Series(close, dtype="float64")
    ref = pd.Series(ref_close, dtype="float64").reindex(s.index).ffill()
    r, m = s.pct_change(), ref.pct_change()
    cov = r.rolling(window, min_periods=min_periods).cov(m)
    var = m.rolling(window, min_periods=min_periods).var()
    return cov / var.replace(0, np.nan)


def forward_excess(close, ref_close, horizon=20, beta_adjust=False) -> pd.Series:
    """The prediction label over ``horizon`` bars.

    ``beta_adjust=False`` reproduces the shipping definition exactly, so both
    labels can be built on one panel and compared. ``True`` subtracts
    ``beta * market_forward`` instead of the market's forward return outright."""
    s = pd.Series(close, dtype="float64")
    ref = pd.Series(ref_close, dtype="float64").reindex(s.index).ffill()
    sym_fwd = s.shift(-horizon) / s - 1.0            # FUTURE return (the label)
    ref_fwd = ref.shift(-horizon) / ref - 1.0
    if not beta_adjust:
        return sym_fwd - ref_fwd
    beta = rolling_beta(s, ref)                      # trailing: known at t
    return sym_fwd - beta * ref_fwd
