"""Market-regime blended classifier — evidence ramps -> raw intensities + memberships.

Pure functions (no tk, no I/O). Phase 1 of the soft-membership structural
regime classifier (design: docs/plans/2026-07-23-market-regime-blended-classifier-design.md):
five regimes — mean_reversion / trending / breakout / choppy / crisis — each scored
CONTINUOUSLY from evidence ramps (``ramp(x, lo, hi)``), never step thresholds, so
regime transitions are gradual. The primary output is the raw intensity vector plus
its normalized membership vector; the hard label is derived downstream for display
only. Every evidence input is OPTIONAL — a missing (None) input DROPS OUT of its
regime's confidence-weighted average (the ``blend_aggression`` idiom) rather than
defaulting, so degradation trends toward "Unclear", never toward a fabricated read.

Smoothing / transition / commit (the temporal layer) is deliberately NOT here —
that is the next task on this module.
"""
from __future__ import annotations
from dataclasses import dataclass, field


# The evidence contract: one flat dict, every key OPTIONAL (None = unavailable ->
# that input drops out of its regime's average rather than defaulting).
EVIDENCE_KEYS = frozenset({
    "adx", "adx_rising",            # float, bool — ADX-14 on 5-min bars
    "ema_slope_atr",                # EMA20 slope per bar, in ATR units (signed)
    "bb_width_pctile",              # 0..1 percentile vs trailing sessions
    "bb_width_expansion",           # width now / width ~30 min ago (ratio)
    "band_hug_frac",                # fraction of last 12 closes in outer BB quartile
    "vwap_hold_frac",               # fraction of session on one side of VWAP (0.5..1)
    "or_break_state",               # "held" | "failed" | "none"
    "or_failed_count",              # int — break-then-recross count today
    "wick_two_sided",               # 0..1 both-direction rejection score
    "whipsaw_count",                # EMA20 cross count today
    "profile_balance",              # 0..1 (1 = balanced single-HVN profile)
    "rel_vol",                      # relative volume
    "atr_pctile",                   # 0..1
    "vix1d_spike_pct",              # day-over-day %
    "term_inversion",               # 0..1 inversion depth
    "gap_open_pct", "gap_filled",   # float, bool
    "above_flip",                   # bool — dealer gamma positive (spot above flip)
    "below_flip_deep",              # 0..1 — below flip, scaled by GEX depth
})

REGIMES = ("mean_reversion", "trending", "breakout", "choppy", "crisis")

# ---------------------------------------------------------------------------
# Tunables — initial values from the design doc's "Key tunables" table
# (docs/plans/2026-07-23-market-regime-blended-classifier-design.md). Named
# module constants so a backtest / the recorded history can sweep them;
# promote to a TOML only if live tuning demands it (flow_alerts.toml precedent).
# ---------------------------------------------------------------------------
UNCLEAR_FLOOR = 0.25                      # max(raw) below this -> "Unclear"

# Mean reversion
MR_ADX_QUIET_PIVOT = 20.0                 # quiet = ramp(20 - adx, 0, 8)
MR_ADX_QUIET_SPAN = 8.0
EMA_FLAT_LO, EMA_FLAT_HI = 0.05, 0.25     # flat-EMA = 1 - ramp(|slope|, lo, hi)

# Trending
ADX_TREND_LO, ADX_TREND_HI = 18.0, 30.0
ADX_NOT_RISING_FACTOR = 0.7               # ADX strong but falling -> discounted
EMA_TREND_LO, EMA_TREND_HI = 0.05, 0.30
VWAP_HOLD_LO, VWAP_HOLD_HI = 0.6, 0.9

# Breakout (multiplicative — a breakout claim needs all its legs)
SQUEEZE_PCTILE_LO, SQUEEZE_PCTILE_HI = 0.10, 0.35   # prior squeeze = 1 - ramp(pctile)
EXPANSION_LO, EXPANSION_HI = 1.1, 1.8
RELVOL_LO, RELVOL_HI = 1.2, 2.0
OR_NONE_FACTOR = 0.3                      # no OR break yet -> weak but possible

# Choppy
OR_FAILED_LO, OR_FAILED_HI = 1.0, 3.0
WHIPSAW_LO, WHIPSAW_HI = 3.0, 8.0
CHOP_ATR_LO, CHOP_ATR_HI = 0.5, 0.9       # effort-without-direction: high ATR ...
CHOP_ADX_LO, CHOP_ADX_HI = 15.0, 25.0     # ... x (1 - ramp(adx)) low ADX

# Crisis (max over tells — any single tell is sufficient)
VIX1D_SPIKE_LO, VIX1D_SPIKE_HI = 10.0, 35.0   # day-over-day %
CRISIS_ATR_LO, CRISIS_ATR_HI = 0.85, 0.97
CRISIS_GAP_MIN_PCT = 1.0                  # unfilled gap larger than this fires

# Per-regime input weights (confidence weights for _wavg)
MR_W_ADX, MR_W_FLAT, MR_W_MIDBAND, MR_W_PROFILE, MR_W_FLIP = 0.25, 0.20, 0.15, 0.25, 0.15
TR_W_ADX, TR_W_SLOPE, TR_W_HUG, TR_W_VWAP, TR_W_OR = 0.30, 0.20, 0.20, 0.15, 0.15
CH_W_WICK, CH_W_ORFAIL, CH_W_WHIP, CH_W_EFFORT = 0.30, 0.25, 0.25, 0.20

EVIDENCE_MATERIALITY = 0.5                # input intensity >= this -> worth a string


@dataclass
class RegimeScores:
    raw: dict[str, float]           # per-regime intensity 0..1
    memberships: dict[str, float]   # normalized (sum 1.0) — raw-proportional;
                                    # uniform only when sum(raw) == 0
    confidence: float               # max(raw)
    unclear: bool                   # confidence < UNCLEAR_FLOOR
    evidence: list[str] = field(default_factory=list)  # human strings for the UI popup


def _clamp(v, lo, hi):
    return float(max(lo, min(hi, v)))


def ramp(x, lo, hi):
    """clamp((x-lo)/(hi-lo), 0, 1). Works inverted too (lo > hi)."""
    if hi == lo:
        return 0.0
    return _clamp((float(x) - lo) / (hi - lo), 0.0, 1.0)


def _num(v):
    """float(v) or None — a non-numeric / missing input is ABSENT, never a default."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _wavg(pairs):
    """[(value_or_None, weight)] -> confidence-weighted mean over PRESENT values.

    A None value drops out (its weight leaves the denominator); nothing
    present -> 0.0. Values clamped to [0, 1].
    """
    num = den = 0.0
    for v, w in pairs:
        if v is None:
            continue
        num += w * _clamp(v, 0.0, 1.0)
        den += w
    return num / den if den > 0 else 0.0


# ---------------------------------------------------------------- per-regime scoring


def _mean_reversion(ev):
    adx = _num(ev.get("adx"))
    quiet = None if adx is None else ramp(MR_ADX_QUIET_PIVOT - adx, 0.0, MR_ADX_QUIET_SPAN)
    slope = _num(ev.get("ema_slope_atr"))
    flat = None if slope is None else 1.0 - ramp(abs(slope), EMA_FLAT_LO, EMA_FLAT_HI)
    wp = _num(ev.get("bb_width_pctile"))
    mid = None if wp is None else _clamp(1.0 - 2.0 * abs(wp - 0.5), 0.0, 1.0)
    bal = _num(ev.get("profile_balance"))
    above = ev.get("above_flip")
    flip = None if above is None else (1.0 if above else 0.0)

    intensity = _wavg([(quiet, MR_W_ADX), (flat, MR_W_FLAT), (mid, MR_W_MIDBAND),
                       (bal, MR_W_PROFILE), (flip, MR_W_FLIP)])
    ss = []
    if quiet is not None and quiet >= EVIDENCE_MATERIALITY:
        ss.append(f"ADX {adx:.0f} (quiet)")
    if flat is not None and flat >= EVIDENCE_MATERIALITY:
        ss.append("EMA flat")
    if bal is not None and bal >= EVIDENCE_MATERIALITY:
        ss.append(f"Balanced profile {bal:.2f}")
    if flip is not None and flip >= EVIDENCE_MATERIALITY:
        ss.append("Above gamma flip")
    return intensity, ss


def _trending(ev):
    adx = _num(ev.get("adx"))
    rising = ev.get("adx_rising")
    if adx is None:
        adx_term = None
    else:
        # adx_rising=None -> factor 1.0, but only when adx itself is present
        factor = ADX_NOT_RISING_FACTOR if rising is False else 1.0
        adx_term = ramp(adx, ADX_TREND_LO, ADX_TREND_HI) * factor
    slope = _num(ev.get("ema_slope_atr"))
    slope_term = None if slope is None else ramp(abs(slope), EMA_TREND_LO, EMA_TREND_HI)
    hug = _num(ev.get("band_hug_frac"))
    vwap = _num(ev.get("vwap_hold_frac"))
    vwap_term = None if vwap is None else ramp(vwap, VWAP_HOLD_LO, VWAP_HOLD_HI)
    orb = ev.get("or_break_state")
    or_term = None if orb is None else (1.0 if orb == "held" else 0.0)

    intensity = _wavg([(adx_term, TR_W_ADX), (slope_term, TR_W_SLOPE), (hug, TR_W_HUG),
                       (vwap_term, TR_W_VWAP), (or_term, TR_W_OR)])
    ss = []
    if adx_term is not None and adx_term >= EVIDENCE_MATERIALITY:
        ss.append(f"ADX {adx:.0f}" + (" rising" if rising else ""))
    if slope_term is not None and slope_term >= EVIDENCE_MATERIALITY:
        ss.append(f"EMA slope {slope:+.2f} ATR/bar")
    if hug is not None and hug >= EVIDENCE_MATERIALITY:
        ss.append(f"Band-hug {hug:.0%}")
    if vwap_term is not None and vwap_term >= EVIDENCE_MATERIALITY:
        ss.append(f"VWAP held {vwap:.0%}")
    if or_term is not None and or_term >= EVIDENCE_MATERIALITY:
        ss.append("OR break held")
    return intensity, ss


def _breakout(ev):
    # Multiplicative, not averaged — a breakout claim needs ALL its legs, so any
    # missing factor zeroes the intensity instead of dropping out.
    wp = _num(ev.get("bb_width_pctile"))
    exp_ = _num(ev.get("bb_width_expansion"))
    rv = _num(ev.get("rel_vol"))
    orb = ev.get("or_break_state")
    if wp is None or exp_ is None or rv is None or orb is None:
        return 0.0, []
    squeeze = 1.0 - ramp(wp, SQUEEZE_PCTILE_LO, SQUEEZE_PCTILE_HI)
    expansion = ramp(exp_, EXPANSION_LO, EXPANSION_HI)
    vol = ramp(rv, RELVOL_LO, RELVOL_HI)
    or_fresh = 1.0 if orb == "held" else (OR_NONE_FACTOR if orb == "none" else 0.0)
    intensity = squeeze * expansion * vol * or_fresh
    ss = []
    if intensity > 0:
        if squeeze >= EVIDENCE_MATERIALITY:
            ss.append(f"Squeeze release (width pctile {wp:.2f})")
        if expansion >= EVIDENCE_MATERIALITY:
            ss.append(f"Width expansion {exp_:.1f}x")
        if vol >= EVIDENCE_MATERIALITY:
            ss.append(f"Rel vol {rv:.1f}x")
    return intensity, ss


def _choppy(ev):
    wick = _num(ev.get("wick_two_sided"))
    orf = _num(ev.get("or_failed_count"))
    orf_term = None if orf is None else ramp(orf, OR_FAILED_LO, OR_FAILED_HI)
    whip = _num(ev.get("whipsaw_count"))
    whip_term = None if whip is None else ramp(whip, WHIPSAW_LO, WHIPSAW_HI)
    atr = _num(ev.get("atr_pctile"))
    adx = _num(ev.get("adx"))
    # effort-without-direction needs BOTH inputs, else the pair drops out
    if atr is None or adx is None:
        effort = None
    else:
        effort = ramp(atr, CHOP_ATR_LO, CHOP_ATR_HI) * (1.0 - ramp(adx, CHOP_ADX_LO, CHOP_ADX_HI))

    intensity = _wavg([(wick, CH_W_WICK), (orf_term, CH_W_ORFAIL),
                       (whip_term, CH_W_WHIP), (effort, CH_W_EFFORT)])
    ss = []
    if wick is not None and wick >= EVIDENCE_MATERIALITY:
        ss.append(f"Two-sided wicks {wick:.2f}")
    if orf_term is not None and orf_term >= EVIDENCE_MATERIALITY:
        ss.append(f"{orf:.0f} failed OR breaks")
    if whip_term is not None and whip_term >= EVIDENCE_MATERIALITY:
        ss.append(f"{whip:.0f} EMA whipsaws")
    if effort is not None and effort >= EVIDENCE_MATERIALITY:
        ss.append("High ATR, low ADX")
    return intensity, ss


def _crisis(ev):
    # max(), not average — any single crisis tell is sufficient; a missing input
    # simply doesn't contribute a candidate to the max.
    tells = []   # (value, string_or_None)
    spike = _num(ev.get("vix1d_spike_pct"))
    if spike is not None:
        tells.append((ramp(spike, VIX1D_SPIKE_LO, VIX1D_SPIKE_HI), f"VIX1D {spike:+.0f}%"))
    term = _num(ev.get("term_inversion"))
    if term is not None:
        tells.append((_clamp(term, 0.0, 1.0), f"Term structure inverted {term:.2f}"))
    atr = _num(ev.get("atr_pctile"))
    if atr is not None:
        tells.append((ramp(atr, CRISIS_ATR_LO, CRISIS_ATR_HI), f"ATR pctile {atr:.2f}"))
    gap = _num(ev.get("gap_open_pct"))
    filled = ev.get("gap_filled")
    if gap is not None and filled is not None:   # gap_filled None -> factor absent
        fired = abs(gap) > CRISIS_GAP_MIN_PCT and filled is False
        tells.append((1.0 if fired else 0.0, f"Unfilled gap {gap:+.1f}%"))
    deep = _num(ev.get("below_flip_deep"))
    if deep is not None:
        tells.append((_clamp(deep, 0.0, 1.0), "Deep below gamma flip"))

    if not tells:
        return 0.0, []
    intensity = max(v for v, _ in tells)
    ss = [s for v, s in tells if v >= EVIDENCE_MATERIALITY]
    return intensity, ss


_SCORERS = {
    "mean_reversion": _mean_reversion,
    "trending": _trending,
    "breakout": _breakout,
    "choppy": _choppy,
    "crisis": _crisis,
}


def score_regimes(ev) -> RegimeScores:
    """Score the five regime intensities from an evidence dict (all keys optional).

    Returns raw intensities, the raw-proportional membership vector (uniform 0.2
    only when every raw is 0), confidence = max(raw), the Unclear flag, and the
    human-readable evidence strings from regimes that contributed.
    """
    ev = ev if isinstance(ev, dict) else {}
    raw: dict[str, float] = {}
    evidence: list[str] = []
    for regime in REGIMES:
        intensity, strings = _SCORERS[regime](ev)
        raw[regime] = intensity
        if intensity > 0:
            evidence.extend(strings)

    confidence = max(raw.values())
    total = sum(raw.values())
    if total > 0:
        memberships = {r: raw[r] / total for r in REGIMES}
    else:
        memberships = {r: 1.0 / len(REGIMES) for r in REGIMES}
    unclear = confidence < UNCLEAR_FLOOR
    return RegimeScores(raw=raw, memberships=memberships, confidence=confidence,
                        unclear=unclear, evidence=evidence)
