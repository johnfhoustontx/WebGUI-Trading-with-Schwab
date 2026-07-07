"""
strategy_scoring.py - Multi-Strategy Swing Scanner: unified Fit + Quality scoring.

Unit B of the multi-strategy swing scanner. Consumes the NORMALIZED candidate
signals produced by `strategy_scanner.py` (Unit A) and scores each one on a
single 0-100 scale.

The pipeline is two-stage:

1. ``infer_market_view(technicals, iv_analysis)`` distills the symbol's
   technicals + IV regime into a compact market view: a *direction*
   (bullish/bearish/neutral), a *conviction* in [0, 1], and a *vol_regime*
   (low/mid/high).

2. ``score_strategy(signal, view, atm_iv, em_1sd)`` scores a single candidate:
     - **Fit** (0-100) — how well the structure's net delta / net vega match the
       inferred view (directional fit + volatility fit).
     - **Quality** (0-100) — execution-agnostic trade quality (R:R or capital
       efficiency, breakeven vs. expected move, probability of profit,
       liquidity).
     - **Composite** = QUALITY-DOMINANT ``0.7 * quality + 0.3 * fit`` (view-fit
       demoted to a ranking tiebreaker), plus a per-factor breakdown.
     - **Grade** is driven by the per-family HARD GATES (liquidity, R:R/capital
       efficiency, PoP): a gate-min failure caps the composite at ``GATE_FAIL_CAP``
       and forces Weak (with a ``grade_reason``); clearing the excellent bars +
       ``composite >= STRONG_MIN`` earns Strong, ``>= GOOD_MIN`` Good, else Marginal.

   ``score_all`` scores a list and returns it sorted by composite (desc), so
   gate-failures (capped composite) sink to the bottom.

SCALE NOTE: net_delta / net_vega are PER-SHARE Schwab sums (a single long call
~ +0.55 delta, a vertical ~ +0.30, net_vega ~ +/-0.1..0.5). The normalizers are
tuned for that [-1, +1] scale, NOT a x100 scale.

Defensive throughout: any missing/None upstream field falls back to a neutral
value; a bad signal gets a neutral/0 score rather than raising.
"""

import math
import logging

log = logging.getLogger("scanner")


#############################################
# CONSTANTS
#############################################

# Composite blend — QUALITY-DOMINANT (view-fit demoted to a ranking tiebreaker).
QUALITY_WEIGHT = 0.7
FIT_WEIGHT = 0.3

# fit_score = FIT_DIR_W * directional fit + FIT_VOL_W * volatility fit
FIT_DIR_W = 0.6
FIT_VOL_W = 0.4

# Quality sub-weights (need not sum to 1 — normalized at use site).
QUALITY_WEIGHTS = {
    "q_rr":  0.30,   # R:R (or capital efficiency when unbounded)
    "q_be":  0.25,   # breakeven vs. expected move
    "q_pop": 0.25,   # probability of profit
    "q_liq": 0.20,   # liquidity (bid/ask tightness)
}

# Soft-clamp scales for the per-share Greek sums.
DELTA_SCALE = 0.5    # |net_delta| ~ 0.5 -> tanh ~ 0.76
VEGA_SCALE = 0.3     # |net_vega| ~ 0.3 -> tanh ~ 0.76

# E1 quality-gated grade thresholds. The grade is driven by structural quality
# and gated by the per-family hard gates: a gate-failure caps the composite +
# forces Weak; passing the excellent gates + a high composite earns Strong.
GATE_FAIL_CAP = 39
STRONG_MIN = 78
GOOD_MIN = 58


#############################################
# E1 — quality gate config (per-family hard gates)
#############################################

# Per-profile hard-gate bars: a ``min`` level (must clear all three to avoid a
# Weak/capped grade) and an ``excellent`` level (clear all three for Strong).
# Each level carries a ``liq`` bar, a reward bar (``rr`` capital-efficiency for
# most; ``capeff`` for NAKED whose R:R is undefined under unbounded loss), and a
# ``pop`` bar. Sourced from the design table.
GATE_BARS = {
    "LONG":    {"min": {"liq": 40, "rr": 0.8,  "pop": 30},
                "excellent": {"liq": 70, "rr": 1.5, "pop": 45}},
    # NAKED: by design, a naked short's low capital-efficiency (max_profit is the
    # credit against a large margin-based capital) keeps its composite below
    # STRONG_MIN, so "Strong" is effectively unreachable for naked shorts —
    # intended (a naked short is rarely your best trade).
    "NAKED":   {"min": {"liq": 40, "capeff": 0.10, "pop": 65},
                "excellent": {"liq": 70, "capeff": 0.20, "pop": 78}},
    "DEBIT":   {"min": {"liq": 45, "rr": 0.6,  "pop": 30},
                "excellent": {"liq": 75, "rr": 1.2, "pop": 45}},
    "CREDIT":  {"min": {"liq": 45, "rr": 0.15, "pop": 60},
                "excellent": {"liq": 75, "rr": 0.33, "pop": 72}},
    "NEUTRAL": {"min": {"liq": 45, "rr": 0.12, "pop": 55},
                "excellent": {"liq": 75, "rr": 0.25, "pop": 68}},
}

# Map normalized signal ``type`` -> gate profile. Unknown -> safe DEBIT default.
_TYPE_PROFILE = {
    "LONG_CALL": "LONG", "LONG_PUT": "LONG",
    "SHORT_CALL": "NAKED", "SHORT_PUT": "NAKED",
    "BULL_CALL": "DEBIT", "BEAR_PUT": "DEBIT",
    "PCS": "CREDIT", "CCS": "CREDIT",
    "IRON_CONDOR": "NEUTRAL", "IC": "NEUTRAL",
}

# Lenient per-leg liquidity floors (skipped for any leg missing that field, so
# absent data never false-fails the liquidity gate).
OI_FLOOR = 50
VOL_FLOOR = 5


def gate_profile(signal):
    """Map a normalized signal to its gate profile (safe default 'DEBIT')."""
    t = signal.get("type") if isinstance(signal, dict) else None
    return _TYPE_PROFILE.get(str(t).upper(), "DEBIT")


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


#############################################
# Task 1 — market-state family tilt
#############################################

# A LOW-WEIGHT, bounded ranking nudge from the five-state market classifier
# (services publish `trend_regime.state`). A validation study found the edge is
# MODEST and CONCENTRATED IN THE TWO MIDDLE STATES (lack_of_bullishness /
# lack_of_bearishness); the extremes are unreliable, so their leans are small.
# This nudges family RANKING only — it is applied to `composite_score` AFTER the
# hard-gated grade is decided, so it can NEVER flip a gate (a Weak trade stays
# Weak). Values are per-family points in the [-STATE_TILT_MAX, +STATE_TILT_MAX]
# band; a family absent from a state's row contributes 0.
STATE_TILT_MAX = 6.0

_STATE_TILT = {
    "neutral": {
        "IC": 3, "PCS": 3, "CCS": 3,
        "LONG_CALL": -2, "LONG_PUT": -2,
    },
    "lack_of_bearishness": {  # resilient, puts undefended -> favor put credit
        "PCS": 5, "BULL_CALL": 2,
        "LONG_PUT": -3, "BEAR_PUT": -2,
    },
    "lack_of_bullishness": {  # exhaustion at highs -> favor call credit
        "CCS": 5, "BEAR_PUT": 2,
        "LONG_CALL": -4, "BULL_CALL": -3,
    },
    "bullish": {  # small — the exhaustion caveat keeps the extreme modest
        "LONG_CALL": 2, "BULL_CALL": 2, "PCS": 2, "CCS": -2,
    },
    "bearish": {  # defensive; rarely fires
        "LONG_PUT": 3, "BEAR_PUT": 3, "PCS": -3, "CCS": 1,
    },
}


def state_family_tilt(state, structure_type):
    """Bounded family-ranking tilt for a market state + structure type.

    Looks up ``_STATE_TILT[state][family_key]`` where ``family_key`` is the
    signal's structure ``type`` (uppercased; ``IRON_CONDOR`` folds to ``IC``),
    clamps the result to +/-``STATE_TILT_MAX``, and returns ``0.0`` for an
    unknown/None state, an unknown/None type, or a family absent from the row.
    """
    if not state or not structure_type:
        return 0.0
    row = _STATE_TILT.get(str(state))
    if not row:
        return 0.0
    key = str(structure_type).upper()
    if key == "IRON_CONDOR":
        key = "IC"
    pts = row.get(key)
    if not isinstance(pts, (int, float)):
        return 0.0
    return float(_clamp(pts, -STATE_TILT_MAX, STATE_TILT_MAX))


#############################################
# Task 8 — infer_market_view
#############################################

def infer_market_view(technicals, iv_analysis):
    """Infer a market view from technicals + IV regime.

    Returns ``{"direction": ..., "conviction": 0..1, "vol_regime": ...}``.

    direction: from ``technicals['trend']`` (UPPERCASE) —
        BULLISH/RECOVERING -> bullish, BEARISH/WEAKENING -> bearish,
        NEUTRAL/unknown -> neutral.
    conviction: base by trend strength, nudged by RSI extremity and
        price-vs-sma20 separation; clamped to [0, 1].
    vol_regime: PRIMARY ``iv_analysis['iv_rank']`` — <35 low, >65 high, else
        mid. FALLBACK when iv_rank is None (insufficient HV history): the IV/HV
        ratio ``current_iv / hv_current`` (>=1.2 high, <=0.9 low, else mid). A
        strongly-disagreeing IV/HV (>1.3 / <0.85) may nudge a mid iv_rank off
        mid. None of either signal -> mid.
    """
    technicals = technicals or {}
    iv_analysis = iv_analysis or {}

    trend = str(technicals.get("trend") or "").upper()

    _DIRECTION = {
        "BULLISH": "bullish",
        "RECOVERING": "bullish",
        "BEARISH": "bearish",
        "WEAKENING": "bearish",
        "NEUTRAL": "neutral",
    }
    direction = _DIRECTION.get(trend, "neutral")

    # Base conviction by trend strength.
    _BASE = {
        "BULLISH": 0.6,
        "BEARISH": 0.6,
        "RECOVERING": 0.35,
        "WEAKENING": 0.35,
        "NEUTRAL": 0.1,
    }
    conviction = _BASE.get(trend, 0.1)

    # Nudge by RSI extremity: |rsi - 50| / 50 in [0, 1].
    rsi = technicals.get("rsi14")
    if isinstance(rsi, (int, float)):
        conviction += 0.15 * (abs(rsi - 50.0) / 50.0)

    # Nudge by price-vs-sma20 separation (fractional), capped.
    price = technicals.get("price")
    sma20 = technicals.get("sma20")
    if isinstance(price, (int, float)) and isinstance(sma20, (int, float)) and sma20:
        sep = abs(price - sma20) / abs(sma20)
        conviction += 0.15 * min(1.0, sep / 0.03)  # ~3% separation = full nudge

    conviction = max(0.0, min(1.0, conviction))

    # vol regime — IV Rank (primary) + IV/HV ratio (fallback / light confirmation).
    iv_rank = iv_analysis.get("iv_rank")

    # IV/HV ratio (defensive — both present and hv_current > 0; same units cancel).
    current_iv = iv_analysis.get("current_iv")
    hv_current = iv_analysis.get("hv_current")
    iv_hv = None
    if (isinstance(current_iv, (int, float)) and isinstance(hv_current, (int, float))
            and hv_current > 0):
        iv_hv = current_iv / hv_current

    if iv_rank is not None:
        # PRIMARY: iv_rank thresholds.
        if iv_rank < 35:
            vol_regime = "low"
        elif iv_rank > 65:
            vol_regime = "high"
        else:
            vol_regime = "mid"
            # Light confirmation: a strongly-disagreeing IV/HV ratio nudges off mid.
            if iv_hv is not None:
                if iv_hv > 1.3:
                    vol_regime = "high"
                elif iv_hv < 0.85:
                    vol_regime = "low"
    elif iv_hv is not None:
        # FALLBACK: iv_rank missing (insufficient HV history) — derive from IV/HV.
        if iv_hv >= 1.2:
            vol_regime = "high"
        elif iv_hv <= 0.9:
            vol_regime = "low"
        else:
            vol_regime = "mid"
    else:
        vol_regime = "mid"

    return {"direction": direction, "conviction": conviction, "vol_regime": vol_regime}


#############################################
# Task 9 — fit normalizers (0-100)
#############################################

def fit_directional(net_delta, view):
    """How well a structure's net delta matches the directional view (0-100).

    Directional view: a structure whose delta sign matches the view scores
    above 50 (scaled by conviction); an opposing structure scores below 50.
    Neutral view: a delta-neutral structure scores high *only when conviction
    is low* (a flat market wants flat exposure).
    """
    net_delta = net_delta if isinstance(net_delta, (int, float)) else 0.0
    conviction = float(view.get("conviction", 0.0) or 0.0)
    direction = view.get("direction", "neutral")

    d = math.tanh(net_delta / DELTA_SCALE)  # ~ +/-1 for |net_delta| >= 0.5

    if direction in ("bullish", "bearish"):
        dir_sign = 1.0 if direction == "bullish" else -1.0
        score = 50.0 + 50.0 * conviction * (dir_sign * d)
    else:  # neutral
        score = 50.0 + 50.0 * (1.0 - conviction) * (1.0 - abs(d))

    return _clamp(score)


def fit_vol(net_vega, vol_regime):
    """How well a structure's net vega matches the vol regime (0-100).

    low regime rewards +vega (long premium), high regime rewards -vega
    (short premium), mid is ~neutral.
    """
    net_vega = net_vega if isinstance(net_vega, (int, float)) else 0.0
    v = math.tanh(net_vega / VEGA_SCALE)

    if vol_regime == "low":
        score = 50.0 + 50.0 * v
    elif vol_regime == "high":
        score = 50.0 - 50.0 * v
    else:  # mid — tiny tilt toward short premium (mean-reversion), kept near 50
        score = 50.0 - 5.0 * v

    return _clamp(score)


#############################################
# Task 9 — quality normalizers (0-100)
#############################################

def q_rr(signal):
    """R:R quality. 0 at rr=0, ~100 at rr>=1.0 (capped).

    When ``rr`` is None (unbounded-profit long), fall back to a PoP-weighted
    proxy so we don't crash or unfairly zero out long premium structures.
    """
    rr = signal.get("rr")
    if rr is None:
        # unbounded profit: use PoP as a neutral-ish proxy.
        pop = signal.get("pop_pct")
        if isinstance(pop, (int, float)):
            return _clamp(pop)
        return 50.0
    if not isinstance(rr, (int, float)) or rr <= 0:
        return 0.0
    # `rr` here is the RATIO form (1.0 = 1:1) -> 100 at rr=1.0, capped. This is
    # distinct from scoring.norm_rr's `rr_pct` PERCENTAGE convention (100 at 50%).
    return _clamp(rr * 100.0)


def q_capital_eff(signal):
    """Capital efficiency: max_profit / capital mapped to 0-100.

    ~100 at max_profit/capital >= 1.0 (a 1:1 return on risked capital).
    None max_profit -> PoP fallback, then neutral.
    """
    mp = signal.get("max_profit")
    cap = signal.get("capital")
    if not isinstance(mp, (int, float)) or not isinstance(cap, (int, float)) or cap <= 0:
        pop = signal.get("pop_pct")
        if isinstance(pop, (int, float)):
            return _clamp(pop)
        return 50.0
    return _clamp(mp / cap * 100.0)


def q_breakeven_vs_em(signal, em_1sd):
    """Breakeven location vs. the 1-sigma expected move (0-100).

    Directional families: reward a breakeven WITHIN reach of the 1-sigma move
    (closer / inside EM = higher — the move only has to be small to win).
    Neutral families: reward a WIDE profit zone relative to EM (breakevens far
    apart = more room before either side is breached).
    Defensive: falsy em_1sd -> 50.
    """
    if not em_1sd:
        return 50.0

    bes = signal.get("breakevens") or []
    spot = signal.get("underlying_price")
    if not bes or not isinstance(spot, (int, float)):
        return 50.0

    family = str(signal.get("family") or "").upper()
    neutral_family = family in ("NEUTRAL",)

    if neutral_family and len(bes) >= 2:
        # width of the profit zone in EM units; wider = better.
        width = abs(max(bes) - min(bes))
        ratio = width / em_1sd  # 2*EM-wide zone -> ratio 2 -> high score
        return _clamp(ratio / 2.0 * 100.0)

    # directional: nearest breakeven distance from spot, in EM units; closer = better.
    dist = min(abs(be - spot) for be in bes)
    ratio = dist / em_1sd  # 0 -> at spot (100), >=1 EM away -> 0.
    return _clamp((1.0 - ratio) * 100.0)


def q_pop(signal):
    """Probability of profit pass-through (already 0-100). None -> 50."""
    pop = signal.get("pop_pct")
    if not isinstance(pop, (int, float)):
        return 50.0
    return _clamp(pop)


def q_liq(signal):
    """Average leg liquidity via scoring.norm_liquidity (0-100).

    Legs may lack bid/ask -> norm_liquidity returns 50. Lazy-imports scoring to
    avoid binding sys.modules['scoring'] at module-import time (the cross-app
    collision documented in the root CLAUDE.md).
    """
    import scoring  # lazy — see docstring

    legs = signal.get("legs")
    if not isinstance(legs, (list, tuple)) or not legs:
        return 50.0

    vals = []
    for leg in legs:
        if not isinstance(leg, dict):
            vals.append(50.0)
            continue
        vals.append(scoring.norm_liquidity(leg.get("bid"), leg.get("ask"), leg.get("mark")))
    return sum(vals) / len(vals) if vals else 50.0


#############################################
# E1 Task 3 — evaluate_gates
#############################################

def _reward_metric(signal, profile):
    """Resolve the reward value for the gate compare, per profile.

    LONG:  R:R; but None R:R with a set net_debit == unbounded profit -> AUTO-PASS
           (infinite upside clears any R:R bar), signalled by returning +inf.
    NAKED: capital efficiency = max_profit / capital (R:R undefined under
           unbounded loss). Missing/invalid -> None (fail).
    else:  R:R. None/<=0 -> None (fail).
    """
    if profile == "NAKED":
        mp = signal.get("max_profit")
        cap = signal.get("capital")
        if isinstance(mp, (int, float)) and isinstance(cap, (int, float)) and cap > 0:
            return mp / cap
        return None

    rr = signal.get("rr")
    if profile == "LONG" and rr is None and signal.get("net_debit") is not None:
        return float("inf")   # unbounded profit -> auto-pass
    if isinstance(rr, (int, float)) and rr > 0:
        return rr
    return None


def _liquidity_ok(signal, liq_bar):
    """q_liq >= bar AND (present-only) min leg oi/volume >= floors."""
    if q_liq(signal) < liq_bar:
        return False
    legs = signal.get("legs")
    if isinstance(legs, (list, tuple)):
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            oi = leg.get("oi")
            if isinstance(oi, (int, float)) and oi < OI_FLOOR:
                return False
            vol = leg.get("volume")
            if isinstance(vol, (int, float)) and vol < VOL_FLOOR:
                return False
    return True


def evaluate_gates(signal):
    """Evaluate the per-family hard gates for a signal.

    Returns ``{"passed_min": bool, "passed_excellent": bool, "reasons": [...]}``
    where ``reasons`` lists the dimensions that failed the MIN bars ("liquidity",
    "R:R", "PoP"). Breakeven-vs-EM is intentionally NOT a gate (it's a ranking
    quality factor, not a hard filter). Defensive: a missing key -> that dimension
    treated as a fail (reward/pop); liquidity uses the already-defensive q_liq.
    """
    if not isinstance(signal, dict):
        return {"passed_min": False, "passed_excellent": False,
                "reasons": ["liquidity", "R:R", "PoP"]}

    profile = gate_profile(signal)
    bars = GATE_BARS[profile]
    reward_key = "capeff" if profile == "NAKED" else "rr"

    reward = _reward_metric(signal, profile)
    pop = signal.get("pop_pct")
    pop_ok_val = pop if isinstance(pop, (int, float)) else None

    def _check(level):
        liq_ok = _liquidity_ok(signal, level["liq"])
        reward_ok = reward is not None and reward >= level[reward_key]
        pop_ok = pop_ok_val is not None and pop_ok_val >= level["pop"]
        return liq_ok, reward_ok, pop_ok

    liq_min, reward_min, pop_min = _check(bars["min"])
    reasons = []
    if not liq_min:
        reasons.append("liquidity")
    if not reward_min:
        reasons.append("R:R")
    if not pop_min:
        reasons.append("PoP")
    passed_min = liq_min and reward_min and pop_min

    liq_ex, reward_ex, pop_ex = _check(bars["excellent"])
    passed_excellent = liq_ex and reward_ex and pop_ex

    return {"passed_min": passed_min, "passed_excellent": passed_excellent,
            "reasons": reasons}


#############################################
# Task 10 — score_strategy / score_all
#############################################

def score_strategy(signal, view, atm_iv, em_1sd, market_state=None):
    """Score one candidate signal in place and return it.

    Adds: fit_score, quality_score, composite_score, grade, grade_reason,
    factor_scores, state_tilt. The composite is quality-DOMINANT
    (0.7*quality + 0.3*fit) and the grade is gated by the per-family hard gates
    (a gate-min failure caps the composite at GATE_FAIL_CAP + forces Weak with a
    'Fails: ...' reason).

    ``market_state`` (optional) is the five-state classifier label; when given, a
    LOW-WEIGHT, bounded ``state_family_tilt`` is added to ``composite_score``
    AFTER the grade is decided — a pure ranking nudge that can NEVER flip a
    hard-gated grade (a Weak trade stays Weak).

    Defensive: a bad signal gets a neutral/0 composite + 'unscored' reason
    rather than raising.
    """
    try:
        fit_dir = fit_directional(signal.get("net_delta"), view)
        fit_v = fit_vol(signal.get("net_vega"), view.get("vol_regime", "mid"))
        fit_score = FIT_DIR_W * fit_dir + FIT_VOL_W * fit_v

        # R:R quality, falling back to capital efficiency when unbounded. NOTE:
        # the factor_scores["q_rr"] slot reports capital-efficiency for
        # unbounded-profit longs (no defined R:R) — the label is shared.
        if signal.get("rr") is None:
            q_rr_val = q_capital_eff(signal)
        else:
            q_rr_val = q_rr(signal)
        q_be_val = q_breakeven_vs_em(signal, em_1sd)
        q_pop_val = q_pop(signal)
        q_liq_val = q_liq(signal)

        w = QUALITY_WEIGHTS
        wsum = w["q_rr"] + w["q_be"] + w["q_pop"] + w["q_liq"]
        quality_score = (
            w["q_rr"] * q_rr_val
            + w["q_be"] * q_be_val
            + w["q_pop"] * q_pop_val
            + w["q_liq"] * q_liq_val
        ) / wsum

        # Quality-DOMINANT composite (view-fit demoted to a tiebreaker).
        composite = round(QUALITY_WEIGHT * quality_score + FIT_WEIGHT * fit_score, 1)

        # Per-family HARD GATES cap + grade the trade.
        gates = evaluate_gates(signal)
        if not gates["passed_min"]:
            grade = "Weak"
            composite = min(composite, GATE_FAIL_CAP)
            grade_reason = "Fails: " + ", ".join(gates["reasons"])
        elif gates["passed_excellent"] and composite >= STRONG_MIN:
            grade = "Strong"
            grade_reason = "Excellent on all quality gates"
        elif composite >= GOOD_MIN:
            grade = "Good"
            grade_reason = "Passes all quality gates"
        else:
            grade = "Marginal"
            grade_reason = "Fillable but middling quality"

        factor_scores = {
            "fit_dir": round(fit_dir, 1),
            "fit_vol": round(fit_v, 1),
            "q_rr": round(q_rr_val, 1),
            "q_be": round(q_be_val, 1),
            "q_pop": round(q_pop_val, 1),
            "q_liq": round(q_liq_val, 1),
        }
    except Exception:
        # Safe even when `signal` isn't a dict — never call .get on it here.
        _label = signal.get("type") if isinstance(signal, dict) else signal
        log.exception("score_strategy failed for signal %s", _label)
        fit_score = 0.0
        quality_score = 0.0
        composite = 0.0
        grade = "Weak"
        grade_reason = "unscored"
        factor_scores = {
            "fit_dir": 0.0, "fit_vol": 0.0, "q_rr": 0.0,
            "q_be": 0.0, "q_pop": 0.0, "q_liq": 0.0,
        }

    # A non-dict signal can't be mutated — return a fresh neutral result so the
    # docstring's "returns a neutral signal rather than raising" holds.
    if not isinstance(signal, dict):
        return {
            "fit_score": round(fit_score, 1),
            "quality_score": round(quality_score, 1),
            "composite_score": round(composite, 1),
            "grade": grade,
            "grade_reason": grade_reason,
            "factor_scores": factor_scores,
            "state_tilt": 0.0,
        }

    # Market-state family tilt — a LOW-WEIGHT ranking nudge applied AFTER the
    # grade is decided, so it can NEVER flip a hard-gated grade (a Weak trade
    # stays Weak). 0.0 when market_state/type is unknown or None.
    tilt = state_family_tilt(market_state, signal.get("type"))
    composite = _clamp(composite + tilt, 0.0, 100.0)

    signal["fit_score"] = round(fit_score, 1)
    signal["quality_score"] = round(quality_score, 1)
    signal["composite_score"] = round(composite, 1)
    signal["grade"] = grade
    signal["grade_reason"] = grade_reason
    signal["factor_scores"] = factor_scores
    signal["state_tilt"] = tilt
    return signal


def score_all(signals, view, atm_iv, em_1sd, market_state=None):
    """Score each signal (neutralizing on exception) and return sorted by
    composite_score descending. ``market_state`` is threaded to each
    ``score_strategy`` for the low-weight market-state family tilt.
    """
    scored = []
    for sig in signals or []:
        try:
            scored.append(score_strategy(sig, view, atm_iv, em_1sd, market_state=market_state))
        except Exception:
            log.exception("score_all: skipping unscorable signal")
            if isinstance(sig, dict):
                sig["composite_score"] = 0.0
                sig["grade"] = "Weak"
                scored.append(sig)
    scored.sort(key=lambda s: s.get("composite_score", 0.0), reverse=True)
    return scored
