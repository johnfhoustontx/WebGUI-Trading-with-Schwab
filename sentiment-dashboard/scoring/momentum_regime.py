"""Momentum regime gate — is momentum tradeable right now (PURE).

Momentum is not one strategy: both the profitable lookback and the sign
change with conditions, so the score is gated rather than just displayed.

``classify`` returns one of three states and the lookback that state implies.
Rule order matters — ``suppressed`` is checked first, because a rebound off a
low can otherwise present as contango + high dispersion and talk the gate out
of the one warning that matters.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

# Tunables — a backtest can sweep these without touching the rules.
SMA_LONG = 200
CRASH_VOL_PCT = 0.80        # realized-vol percentile above which a rebound is a crash risk
CRASH_RETURN_WINDOW = 21
DISPERSION_FLOOR = 0.40     # below this there is nothing for a relative-strength screen
CONTANGO_MAX = 1.0          # vix_term is near/far; < 1.0 is contango
REALIZED_VOL_WINDOW = 21
DISPERSION_MIN_HISTORY = 60
MIN_BARS = SMA_LONG

LOOKBACK_LONG = "63/126"
LOOKBACK_SHORT = "21/63"

STATE_LABELS = {
    "favorable":  "Favorable",
    "neutral":    "Neutral",
    "suppressed": "Suppressed",
}

STATE_DESCRIPTIONS = {
    "favorable":  "Momentum's home turf — expect rank persistence week to week.",
    "neutral":    "Chop — momentum decays and whipsaws; shorten the lookback.",
    "suppressed": "Momentum-crash risk — the biggest losers rip hardest here.",
}


@dataclass(frozen=True)
class RegimeVerdict:
    state: str
    label: str
    description: str
    lookback: str
    crash_risk: bool
    dispersion_pct: float | None
    vol_pct: float | None
    reasons: tuple[str, ...]


def _finite(values):
    out = []
    for v in values or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def dispersion(returns_by_symbol):
    """Cross-sectional stdev of the constituents' 5d returns.

    Accepts a ``{symbol: return}`` mapping or a plain sequence of returns.
    None below two present values — a stdev over one point is 0.0, which
    would read as "no dispersion" rather than "not measurable".
    """
    if returns_by_symbol is None:
        return None
    values = returns_by_symbol.values() if hasattr(returns_by_symbol, "values") \
        else returns_by_symbol
    present = _finite(values)
    if len(present) < 2:
        return None
    out = float(np.asarray(present, dtype=float).std(ddof=0))
    return out if math.isfinite(out) else None


def dispersion_percentile(current, history):
    """Rank of ``current`` within its own history, 0..1.

    None until there are ``DISPERSION_MIN_HISTORY`` observations — a
    percentile off a handful of readings is noise dressed as a regime call.
    """
    if current is None or not math.isfinite(float(current)):
        return None
    prior = _finite(history)
    if len(prior) < DISPERSION_MIN_HISTORY:
        return None
    return float(sum(1 for v in prior if v <= float(current)) / len(prior))


def realized_vol_percentile(closes, n=REALIZED_VOL_WINDOW):
    """Rank of the latest n-day realized vol against its own trailing history."""
    arr = _finite(closes)
    if len(arr) < n * 2 + 2:
        return None
    a = np.asarray(arr, dtype=float)
    if np.any(a <= 0):
        return None
    log_rets = np.diff(np.log(a))
    if len(log_rets) < n + 1 or not np.all(np.isfinite(log_rets)):
        return None
    vols = [float(log_rets[i - n:i].std(ddof=0))
            for i in range(n, len(log_rets) + 1)]
    if len(vols) < 2:
        return None
    last, prior = vols[-1], vols[:-1]
    return float(sum(1 for v in prior if v <= last) / len(prior))


def _pct_return(arr, n):
    if len(arr) < n + 1 or arr[-1 - n] <= 0:
        return None
    return arr[-1] / arr[-1 - n] - 1.0


def _verdict(state, lookback, crash_risk, dispersion_pct, vol_pct, reasons):
    return RegimeVerdict(
        state=state,
        label=STATE_LABELS[state],
        description=STATE_DESCRIPTIONS[state],
        lookback=lookback,
        crash_risk=crash_risk,
        dispersion_pct=dispersion_pct,
        vol_pct=vol_pct,
        reasons=tuple(reasons),
    )


def classify(spy_closes, vix_term=None, dispersion_pct=None, vol_pct=None) -> RegimeVerdict:
    """Gate momentum on the current regime.

    ``vix_term`` is the near/far VIX ratio (e.g. VIX9D/VIX); below
    ``CONTANGO_MAX`` is contango. ``vol_pct`` is normally derived from
    ``spy_closes`` — pass it to reuse an already-computed value.
    """
    reasons: list[str] = []
    closes = _finite(spy_closes)

    if len(closes) < MIN_BARS:
        reasons.append(
            f"Only {len(closes)} SPY bars — {MIN_BARS} needed for the 200 DMA; "
            "regime history is insufficient")
        return _verdict("neutral", LOOKBACK_SHORT, False,
                        dispersion_pct, vol_pct, reasons)

    arr = np.asarray(closes, dtype=float)
    close = float(arr[-1])
    sma_long = float(arr[-SMA_LONG:].mean())
    above = close > sma_long
    below = close < sma_long
    r_short = _pct_return(arr, CRASH_RETURN_WINDOW)
    if vol_pct is None:
        vol_pct = realized_vol_percentile(closes)

    # suppressed first — see the module docstring.
    if below and r_short is not None and r_short > 0 \
            and vol_pct is not None and vol_pct > CRASH_VOL_PCT:
        reasons.append(
            f"SPY {close:.2f} is below its {SMA_LONG} DMA ({sma_long:.2f}) "
            f"but up {r_short * 100:.1f}% over {CRASH_RETURN_WINDOW}d")
        reasons.append(
            f"Realized vol in the {vol_pct * 100:.0f}th percentile — "
            "a rebound off a low, where losers rip hardest")
        return _verdict("suppressed", LOOKBACK_SHORT, True,
                        dispersion_pct, vol_pct, reasons)

    contango = vix_term is not None and math.isfinite(float(vix_term)) \
        and float(vix_term) < CONTANGO_MAX
    has_dispersion = dispersion_pct is not None \
        and float(dispersion_pct) > DISPERSION_FLOOR

    if above and contango and has_dispersion:
        reasons.append(f"SPY {close:.2f} above its {SMA_LONG} DMA ({sma_long:.2f})")
        reasons.append(f"VIX term in contango ({float(vix_term):.2f} near/far)")
        reasons.append(
            f"Dispersion in the {float(dispersion_pct) * 100:.0f}th percentile — "
            "names are moving apart, so relative strength has something to rank")
        return _verdict("favorable", LOOKBACK_LONG, False,
                        dispersion_pct, vol_pct, reasons)

    if not above:
        reasons.append(f"SPY {close:.2f} is not above its {SMA_LONG} DMA ({sma_long:.2f})")
    if vix_term is None:
        reasons.append("VIX term structure unavailable")
    elif not contango:
        reasons.append(f"VIX term not in contango ({float(vix_term):.2f} near/far)")
    if dispersion_pct is None:
        reasons.append("Dispersion percentile unavailable — needs 60 sessions of history")
    elif not has_dispersion:
        reasons.append(
            f"Dispersion only in the {float(dispersion_pct) * 100:.0f}th percentile — "
            "correlations converging, little for a relative-strength screen to pick up")
    return _verdict("neutral", LOOKBACK_SHORT, False,
                    dispersion_pct, vol_pct, reasons)
