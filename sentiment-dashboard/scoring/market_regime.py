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

The temporal layer lives here too (still pure — callers supply ``dt_sec``, never
wall-clock reads): wall-clock half-life EMA smoothing over the membership vector
(``alpha``/``smooth``), fast-vs-slow transition detection (``detect_transition``),
the hysteresis label commit (``CommitState``/``commit_label``), and the crisis
fast-attack bypass (``apply_crisis_attack``/``crisis_attacked``).
"""
from __future__ import annotations
import math
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
    "vix_level",                    # absolute VIX (the primary volatility tell)
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

# Volatility-stress regime (internal key "crisis"; DISPLAYED as "Stressed").
# max() over tells — any single tell is sufficient — so each tell must only fire
# at a genuinely elevated level or the label overstates a normal day.
# Absolute VIX is the PRIMARY tell (the most direct fear gauge): a VIX ~22 (just
# above its long-run norm) starts contributing, a stressed ~34 saturates and, at
# ~30, trips the fast-attack force-commit. Before this (2026-07-24) there was NO
# absolute-VIX input — the regime leaned on ATR percentile (a noisy proxy) and
# read ~34% on ordinary wide-range days.
VIX_STRESS_LO, VIX_STRESS_HI = 22.0, 34.0
VIX1D_SPIKE_LO, VIX1D_SPIKE_HI = 10.0, 35.0   # day-over-day %
# ATR percentile is now a CORROBORATING extreme-realized-vol tell, not a solo
# driver: floor raised 0.85 -> 0.92 so only a near-record-range day contributes
# (a merely-wide day at the ~90th pctile no longer pushes the regime up).
CRISIS_ATR_LO, CRISIS_ATR_HI = 0.92, 0.99
# Unfilled-gap crisis tell — a RAMP over genuinely extreme gaps, NOT a binary
# cliff. Routine 1-2% opening gaps happen weekly and are not crises; a
# crisis-grade gap is a tail event (~3%+ unfilled, holding). A 2.5-5% ramp means
# a 1.1% gap contributes 0, a ~3% gap a mild partial, and only a ~4.4%+ gap trips
# the crisis-attack force-commit. (The old binary at 1% pinned every gappy day to
# "Crisis" via max() + the force-commit — live-caught 2026-07-24.)
CRISIS_GAP_LO_PCT, CRISIS_GAP_HI_PCT = 2.5, 5.0

# Per-regime input weights (confidence weights for _wavg)
MR_W_ADX, MR_W_FLAT, MR_W_MIDBAND, MR_W_PROFILE, MR_W_FLIP = 0.25, 0.20, 0.15, 0.25, 0.15
TR_W_ADX, TR_W_SLOPE, TR_W_HUG, TR_W_VWAP, TR_W_OR = 0.30, 0.20, 0.20, 0.15, 0.15
CH_W_WICK, CH_W_ORFAIL, CH_W_WHIP, CH_W_EFFORT = 0.30, 0.25, 0.25, 0.20

EVIDENCE_MATERIALITY = 0.5                # input intensity >= this -> worth a string

# Temporal layer (smoothing / transition / commit / crisis attack)
FAST_HALF_LIFE_MIN = 15.0    # fast EMA half-life (wall-clock minutes)
SLOW_HALF_LIFE_MIN = 60.0    # slow EMA half-life
TRANSITION_FLOOR = 0.05      # min fast-vs-slow divergence to report a transition
TRANSITION_FULL = 0.25       # divergence at which progress reads 1.0
COMMIT_MARGIN = 0.10         # dominant must beat runner-up by this to flip the label
COMMIT_READS = 2             # ... for this many consecutive samples
CRISIS_ATTACK = 0.7          # raw crisis at/above this bypasses smoothing entirely
_FLOOR_EPS = 1e-9            # FP guard so a divergence exactly AT the floor reports

# Direction (DISPLAY ONLY — the intensity math above stays sign-blind, because
# "is this a trend day" is answered identically up or down). The sign is a
# separate axis, the same shape the five-state classifier uses (direction x
# aggression), and it is applied to the LABEL, never to the membership vector.
#
# The gate: the app has TWO direction reads — this module's own signed EMA slope
# (SPY price structure, 5-min cadence) and the Market Trend composite score
# (price+breadth+sector+VIX, 15-min, hysteresis-committed). They diverge on a
# real market condition (index up on narrow leadership while breadth is
# negative), so a word derived from either alone can contradict the other panel.
# ``direction_sign`` names a direction only when BOTH agree past their deadbands;
# otherwise the neutral base label renders, which is exactly today's behaviour.
DIRECTION_SLOPE_DEADBAND = EMA_TREND_LO    # below the trending ramp's own floor
DIRECTION_TREND_DEADBAND = 3.0             # points either side of the 50 neutral
DIRECTION_STRONG_SLOPE = 0.5 * (EMA_TREND_LO + EMA_TREND_HI)   # Rallying vs Firming
DIRECTION_COMMIT_READS = 2                 # consecutive reads to CLAIM a direction


@dataclass
class RegimeScores:
    raw: dict[str, float]           # per-regime intensity 0..1
    memberships: dict[str, float]   # normalized (sum 1.0) — raw-proportional;
                                    # uniform only when sum(raw) == 0
    confidence: float               # max(raw)
    unclear: bool                   # confidence < UNCLEAR_FLOOR
    evidence: list[str] = field(default_factory=list)  # human strings for the UI popup
    # The SAME strings, each paired with the regime whose scorer produced it:
    # ``[{"text": str, "regime": str}, ...]``, in the same order as ``evidence``.
    # Additive and derived — ``score_regimes`` already knows the attribution and
    # used to throw it away, flattening six lines from three different regimes
    # into one undifferentiated list. A renderer that wants to tell "ADX 36
    # rising" (trending, informational) from "11 EMA whipsaws" (choppy, adverse)
    # cannot recover that from the text without pattern-matching the copy.
    # ``evidence`` is left EXACTLY as it was — same strings, same order — so
    # every existing reader is untouched.
    evidence_detail: list[dict] = field(default_factory=list)


def _clamp(v, lo, hi):
    return float(max(lo, min(hi, v)))


def ramp(x, lo, hi):
    """clamp((x-lo)/(hi-lo), 0, 1). Works inverted too (lo > hi)."""
    if hi == lo:
        return 0.0
    return _clamp((float(x) - lo) / (hi - lo), 0.0, 1.0)


def _num(v):
    """Finite float(v) or None — a non-numeric / NaN / ±inf / missing input is
    ABSENT, never a default (a NaN warm-up value must not score as intensity)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _bool(v):
    """A real bool or None — anything else (e.g. the string "false") is ABSENT."""
    return v if isinstance(v, bool) else None


_OR_STATES = frozenset({"held", "failed", "none"})


def _or_state(v):
    """A whitelisted or_break_state or None — an unknown value is ABSENT, not
    an active "not held" 0.0."""
    return v if v in _OR_STATES else None


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
    above = _bool(ev.get("above_flip"))
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
    rising = _bool(ev.get("adx_rising"))
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
    orb = _or_state(ev.get("or_break_state"))
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
    orb = _or_state(ev.get("or_break_state"))
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
    vlev = _num(ev.get("vix_level"))
    if vlev is not None:
        tells.append((ramp(vlev, VIX_STRESS_LO, VIX_STRESS_HI), f"VIX {vlev:.0f}"))
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
    filled = _bool(ev.get("gap_filled"))
    if gap is not None and filled is not None:   # gap_filled None/non-bool -> absent
        # Only an UNFILLED gap counts (a recovered gap isn't stress); its severity
        # ramps over extreme magnitudes so routine gaps contribute ~0.
        unfilled = 1.0 if filled is False else 0.0
        mag = ramp(abs(gap), CRISIS_GAP_LO_PCT, CRISIS_GAP_HI_PCT) * unfilled
        tells.append((mag, f"Unfilled gap {gap:+.1f}%"))
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
    human-readable evidence strings from regimes that contributed — both as the
    flat ``evidence`` list and, additively, as ``evidence_detail`` pairing each
    string with the regime that produced it.
    """
    ev = ev if isinstance(ev, dict) else {}
    raw: dict[str, float] = {}
    evidence: list[str] = []
    detail: list[dict] = []
    for regime in REGIMES:
        intensity, strings = _SCORERS[regime](ev)
        raw[regime] = intensity
        if intensity > 0:
            evidence.extend(strings)
            detail.extend({"text": s, "regime": regime} for s in strings)

    confidence = max(raw.values())
    total = sum(raw.values())
    if total > 0:
        memberships = {r: raw[r] / total for r in REGIMES}
    else:
        memberships = {r: 1.0 / len(REGIMES) for r in REGIMES}
    unclear = confidence < UNCLEAR_FLOOR
    return RegimeScores(raw=raw, memberships=memberships, confidence=confidence,
                        unclear=unclear, evidence=evidence,
                        evidence_detail=detail)


# ---------------------------------------------------------------- temporal layer


def _normalize(vec):
    """A defensively-renormalized COPY of a memberships dict: junk/NaN/negative
    values read 0, the vector sums to 1.0, an all-zero vector -> uniform."""
    clean = {}
    for r in REGIMES:
        v = _num(vec.get(r)) if isinstance(vec, dict) else None
        clean[r] = v if v is not None and v > 0.0 else 0.0
    total = sum(clean.values())
    if total <= 0.0:
        return {r: 1.0 / len(REGIMES) for r in REGIMES}
    return {r: clean[r] / total for r in REGIMES}


def alpha(dt_sec, half_life_min):
    """EMA update weight for a sample ``dt_sec`` after the previous one, such
    that after exactly one half-life the OLD value's weight is 0.5.

    ``dt_sec`` <= 0 (or None) -> 0.0 (no update, retain the prior value). A
    degenerate ``half_life_min`` <= 0 -> 1.0 (no smoothing, the sample fully
    replaces)."""
    if dt_sec is None or dt_sec <= 0:
        return 0.0
    if half_life_min <= 0:
        return 1.0
    return 1.0 - 0.5 ** (float(dt_sec) / (float(half_life_min) * 60.0))


def smooth(fast, slow, sample, dt_sec):
    """EMA the (renormalized) sample into the fast + slow membership vectors.

    Cold start (either EMA is None) -> both initialize to the sample. Returns
    NEW ``(fast, slow)`` dicts, each renormalized (an EMA of normalized vectors
    can drift a hair numerically); inputs are never mutated.
    """
    s = _normalize(sample)
    if fast is None or slow is None:
        return dict(s), dict(s)
    a_fast = alpha(dt_sec, FAST_HALF_LIFE_MIN)
    a_slow = alpha(dt_sec, SLOW_HALF_LIFE_MIN)
    new_fast = {r: (1.0 - a_fast) * fast[r] + a_fast * s[r] for r in REGIMES}
    new_slow = {r: (1.0 - a_slow) * slow[r] + a_slow * s[r] for r in REGIMES}
    return _normalize(new_fast), _normalize(new_slow)


def detect_transition(fast, slow):
    """Fast-vs-slow divergence read as an in-progress regime transition.

    ``to`` = the regime with the largest positive (fast - slow) divergence,
    ``from`` = the largest negative. Divergence below TRANSITION_FLOOR (or a
    degenerate from == to) -> None (stable). progress = divergence scaled to
    TRANSITION_FULL, clamped to [0, 1].

    ``fast``/``slow`` must be full ``smooth``-produced membership vectors (all
    REGIMES keys); only ``smooth``'s ``sample`` argument is untrusted/normalized.
    """
    if not fast or not slow:
        return None
    d = {r: fast[r] - slow[r] for r in REGIMES}
    to = max(d, key=d.get)
    frm = min(d, key=d.get)
    if to == frm or d[to] < TRANSITION_FLOOR - _FLOOR_EPS:
        return None
    return {"from": frm, "to": to,
            "progress": _clamp(d[to] / TRANSITION_FULL, 0.0, 1.0)}


@dataclass
class CommitState:
    committed: str | None = None   # the currently displayed label key (regime name)
    streak: int = 0                # consecutive margin reads by the SAME challenger
    challenger: str | None = None  # the regime the current streak is being built for


def commit_label(fast, state):
    """Hysteresis label commit over the fast-EMA memberships.

    Cold start commits the dominant immediately. A SINGLE challenger must lead
    the runner-up by COMMIT_MARGIN for COMMIT_READS consecutive samples to flip
    (first margin-clearing read -> streak 1, committed holds; the next
    consecutive read BY THE SAME CHALLENGER flips). A no-margin read, the
    committed label re-dominating, or a DIFFERENT challenger taking the lead
    restarts the streak. Returns a NEW CommitState (no mutation).

    ``fast`` must be a full ``smooth``-produced membership vector (all REGIMES
    keys). Ties resolve in REGIMES order (``sorted`` is stable).
    """
    ranked = sorted(REGIMES, key=lambda r: fast[r], reverse=True)
    dominant, runner_up = ranked[0], ranked[1]
    if state.committed is None:
        return CommitState(committed=dominant, streak=0, challenger=None)
    if dominant == state.committed:
        return CommitState(committed=state.committed, streak=0, challenger=None)
    if fast[dominant] - fast[runner_up] >= COMMIT_MARGIN:
        # A challenger different from the one the streak was built for restarts it.
        streak = state.streak + 1 if dominant == state.challenger else 1
        if streak >= COMMIT_READS:
            return CommitState(committed=dominant, streak=0, challenger=None)
        return CommitState(committed=state.committed, streak=streak,
                           challenger=dominant)
    return CommitState(committed=state.committed, streak=0, challenger=None)


def apply_crisis_attack(fast, raw_crisis):
    """The one exception to smoothness: a raw crisis intensity at/above
    CRISIS_ATTACK bypasses the EMAs — crisis membership jumps straight to
    ``max(fast["crisis"], raw_crisis)`` and the other regimes scale
    proportionally into the remainder (vector still sums to 1). Below the
    threshold the fast vector is returned UNCHANGED (same object).

    ``fast`` must be a full ``smooth``-produced membership vector (all REGIMES
    keys)."""
    if not crisis_attacked(raw_crisis):
        return fast
    crisis = _clamp(max(fast["crisis"], raw_crisis), 0.0, 1.0)
    others_sum = sum(fast[r] for r in REGIMES if r != "crisis")
    if others_sum <= 0.0 or crisis >= 1.0:
        return {r: (1.0 if r == "crisis" else 0.0) for r in REGIMES}
    scale = (1.0 - crisis) / others_sum
    out = {r: fast[r] * scale for r in REGIMES}
    out["crisis"] = crisis
    return out


# ---------------------------------------------------------------- direction layer


# Base display names, keyed by the internal regime key. The KEYS are contract +
# storage (``cache:sentiment:regime`` memberships, the ``regime_intraday``
# columns), so they stay as they are; only these words are user-facing.
REGIME_DISPLAY = {
    "mean_reversion": "Balanced",   # the evidence is low ADX / flat EMA / mid-band
                                    # width / balanced profile / positive dealer
                                    # gamma — price AT the mean, not stretched from
                                    # it. Nothing here measures an extreme, so the
                                    # old "Mean Reversion" promised a fade the model
                                    # never tested.
    "trending": "Trending",
    "breakout": "Breakout",
    "choppy": "Whipsaw",            # vs Balanced: same "not trending" axis, but
                                    # high effort with no result, not quiet balance.
    "crisis": "Stressed",           # VIX_STRESS_LO is 22 and the attack fires near
                                    # 30 — stress, not a crisis; and "Volatile"
                                    # describes breakout/whipsaw days too.
}

# Only these regimes carry a direction. A balanced auction has none by
# definition, whipsaw is directionless by construction, and a stress read is
# about fear rather than sign.
_DIRECTIONAL = {
    "trending": {(1, True): "Rallying", (1, False): "Firming",
                 (-1, True): "Retreating", (-1, False): "Softening"},
    "breakout": {(-1, True): "Breakdown", (-1, False): "Breakdown"},
}


@dataclass
class DirectionState:
    committed: int = 0        # the displayed direction: -1 / 0 / +1
    streak: int = 0           # consecutive reads by the SAME challenger
    challenger: int = 0       # the direction the streak is being built for


def direction_sign(slope_atr, trend_score):
    """-1 / 0 / +1 — the direction word's sign, or 0 for "don't claim one".

    Returns non-zero only when the regime's OWN signed EMA slope and the Market
    Trend composite ``trend_score`` (0..100, 50 neutral) agree AND both clear
    their deadbands. Disagreement is a real read, not an error — it means the
    tape is trending while the composite can't say which way — so it renders the
    neutral label rather than picking a winner and contradicting the gauge.
    """
    slope = _num(slope_atr)
    score = _num(trend_score)
    if slope is None or score is None:
        return 0
    if slope >= DIRECTION_SLOPE_DEADBAND and score >= 50.0 + DIRECTION_TREND_DEADBAND:
        return 1
    if slope <= -DIRECTION_SLOPE_DEADBAND and score <= 50.0 - DIRECTION_TREND_DEADBAND:
        return -1
    return 0


def direction_strong(slope_atr):
    """True when the slope is steep enough for Rallying/Retreating rather than
    Firming/Softening. Read off |slope|, NOT the trending intensity: a slow grind
    with textbook ADX/VWAP structure scores high trending and is precisely what
    "Firming" should mean."""
    slope = _num(slope_atr)
    return slope is not None and abs(slope) >= DIRECTION_STRONG_SLOPE


def commit_direction(sign, state):
    """Hysteresis over the direction sign, so the word can't flicker at a
    boundary. Deliberately ASYMMETRIC: claiming a direction needs
    DIRECTION_COMMIT_READS consecutive reads, while dropping BACK to neutral
    takes one — never keep asserting a direction the evidence stopped backing.
    A flip to the opposite sign restarts the streak. Returns a NEW state.
    """
    sign = sign if sign in (-1, 0, 1) else 0
    if sign == 0:
        return DirectionState(committed=0, streak=0, challenger=0)
    if sign == state.committed:
        return DirectionState(committed=sign, streak=0, challenger=0)
    streak = state.streak + 1 if sign == state.challenger else 1
    if streak >= DIRECTION_COMMIT_READS:
        return DirectionState(committed=sign, streak=0, challenger=0)
    return DirectionState(committed=state.committed, streak=streak, challenger=sign)


def regime_label(key, direction=0, strong=False):
    """Display name for a regime key, adorned with the direction where one
    applies. An unknown/empty key reads "Unclear" — the model never fabricates a
    regime it can't see."""
    base = REGIME_DISPLAY.get(key)
    if base is None:
        return "Unclear"
    words = _DIRECTIONAL.get(key)
    if not words or direction not in (-1, 1):
        return base
    return words.get((direction, bool(strong)), base)


def crisis_attacked(raw_crisis):
    """True when the raw crisis intensity is at/above the fast-attack
    threshold (the service layer also uses this to force-commit the label +
    trigger the fast-path republish — one place holds the threshold)."""
    return raw_crisis is not None and raw_crisis >= CRISIS_ATTACK
