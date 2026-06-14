"""
scoring.py - Composite Scoring Model for Credit Spread Selection
Version: 1.0.0
Last Updated: 2026-04-03

Combines 9 normalized factors into a single 0-100 composite score.
Factors span three dimensions: Value (35%), Context (42%), Execution (23%).

Score = sum(weight_i * normalized_factor_i) for i in 1..9

Version 1.0.0 Changes:
- Initial implementation
- 9-factor model with configurable weights
- Per-factor normalization to 0-100 scale
- Iron Condor scoring with delta-neutrality bonus
"""

import math
import logging

log = logging.getLogger("scanner")

#############################################
# DEFAULT WEIGHTS (must sum to 100)
#############################################

DEFAULT_WEIGHTS = {
    "rr":     15,   # R:R ratio              (Value)
    "pop":    10,   # Probability of profit  (Value)
    "theta":  10,   # Theta efficiency       (Value)
    "iv":     12,   # IV Rank                (Context)
    "iv_hv":  10,   # IV/HV ratio            (Context)
    "vega":    8,   # Vega risk              (Context)
    "em":     12,   # Expected move buffer   (Context)
    "liq":     5,   # Liquidity              (Execution) — reduced 8 -> 5
    "trend":  10,   # Trend alignment        (Execution) — reduced 15 -> 10
    "gex":     4,   # GEX wall proximity     (Execution) — NEW
    "dex":     4,   # DEX wall proximity     (Execution) — NEW
}
# Sum: 15+10+10+12+10+8+12+5+10+4+4 = 100

#############################################
# NORMALIZATION FUNCTIONS (each returns 0-100)
#############################################

def norm_rr(rr_pct):
    """R:R ratio normalized. Linear: 0 at 0%, 100 at 50%+."""
    if rr_pct is None or rr_pct <= 0:
        return 0.0
    return min(100.0, rr_pct / 50.0 * 100.0)


def norm_pop(pop_pct):
    """Probability of profit normalized. Linear: 0 at PoP=50%, 100 at PoP=95%."""
    if pop_pct is None or pop_pct <= 50:
        return 0.0
    return min(100.0, (pop_pct - 50.0) / 45.0 * 100.0)


def norm_theta(net_theta, max_loss, all_theta_efficiencies=None):
    """Theta efficiency normalized.

    Primary: percentile rank of (net_theta / max_loss) among all candidates.
    Fallback: linear scale if no peer data available.
    """
    if net_theta is None or max_loss is None or max_loss <= 0:
        return 0.0

    efficiency = abs(net_theta) / max_loss * 100  # daily decay as % of risk

    if all_theta_efficiencies and len(all_theta_efficiencies) > 1:
        # Percentile rank among peers
        below = sum(1 for e in all_theta_efficiencies if e < efficiency)
        return below / len(all_theta_efficiencies) * 100.0
    else:
        # Fallback: linear scale. 0% at 0, 100% at 0.5% daily decay per risk dollar
        return min(100.0, efficiency / 0.5 * 100.0)


def norm_iv_rank(iv_rank):
    """IV Rank normalized. Already 0-100, pass through with floor at 0."""
    if iv_rank is None:
        return 50.0  # Neutral if unavailable
    return max(0.0, min(100.0, iv_rank))


def norm_iv_hv_ratio(iv_hv_ratio):
    """IV/HV ratio normalized. Measures volatility premium being sold.

    Ratio > 1.0 = IV exceeds realized vol (premium exists to capture).
    Ratio < 1.0 = IV below realized (selling cheap premium — danger).

    Scale: 0 at ratio 0.5, 50 at ratio 1.0, 100 at ratio 1.5+.
    """
    if iv_hv_ratio is None or iv_hv_ratio <= 0:
        return 50.0  # Neutral if unavailable
    score = (iv_hv_ratio - 0.5) / 1.0 * 100.0
    return max(0.0, min(100.0, score))


def norm_vega_risk(net_vega, max_loss, iv_rank):
    """Vega risk normalized. Penalizes high vega exposure in low IV environments.

    High vega + low IV Rank = expansion risk (worst case for credit sellers).
    High vega + high IV Rank = less dangerous (IV already elevated, likely to contract).

    vega_exposure = |net_vega| / max_loss (per-dollar vega sensitivity)

    Returns 0-100 where 100 = low risk, 0 = high risk.
    """
    if net_vega is None or max_loss is None or max_loss <= 0:
        return 50.0

    vega_exposure = abs(net_vega) / max_loss
    iv_safety = (iv_rank or 50) / 100.0  # 0.0 to 1.0

    # vega_exposure typically 0.001 to 0.05 range
    # Normalize: 0 at exposure 0.05+, 100 at exposure 0
    vega_score = max(0, min(100, (1 - vega_exposure / 0.05) * 100))

    # Blend: 60% vega exposure + 40% IV safety
    score = vega_score * 0.6 + iv_safety * 100 * 0.4
    return max(0.0, min(100.0, round(score, 1)))


def norm_gex_proximity(short_strike, spot, gex_walls):
    """GEX wall proximity normalized. Penalizes short strikes near major GEX walls.
    100 = far from all walls, 0 = on a wall."""
    if not gex_walls or not short_strike or not spot:
        return 50.0
    if spot <= 0:
        return 50.0
    min_dist_pct = float("inf")
    for wall in gex_walls:
        dist_pct = abs(short_strike - wall) / spot * 100
        min_dist_pct = min(min_dist_pct, dist_pct)
    if min_dist_pct >= 1.0:
        return 100.0
    return round(min_dist_pct / 1.0 * 100.0, 1)


def norm_dex_proximity(short_strike, spot, dex_walls):
    """DEX wall proximity normalized. Penalizes short strikes near major DEX walls.

    DEX walls represent concentrated delta hedging flow - strikes where dealer
    delta-hedge rebalancing creates directional pressure near expiration.

    100 = far from all walls (>= 1% of spot), 0 = on a wall.
    """
    if not dex_walls or not short_strike or not spot:
        return 50.0
    if spot <= 0:
        return 50.0
    min_dist_pct = float("inf")
    for wall in dex_walls:
        dist_pct = abs(short_strike - wall) / spot * 100
        min_dist_pct = min(min_dist_pct, dist_pct)
    if min_dist_pct >= 1.0:
        return 100.0
    return round(min_dist_pct / 1.0 * 100.0, 1)


def norm_em_buffer(short_strike, underlying_price, expected_move_1sd, spread_type):
    """Expected move buffer normalized.

    Measures how far the short strike sits outside the +/-1 sigma expected move.
    0 = at the EM boundary, 100 = at 2x the EM distance from underlying.

    For PCS: buffer = (underlying - short_strike) / expected_move - 1
    For CCS: buffer = (short_strike - underlying) / expected_move - 1
    """
    if (not short_strike or not underlying_price or not expected_move_1sd
            or expected_move_1sd <= 0):
        return 50.0  # Neutral if unavailable

    if spread_type == "PCS":
        distance = underlying_price - short_strike
    else:  # CCS
        distance = short_strike - underlying_price

    # How many expected moves away is the short strike?
    em_ratio = distance / expected_move_1sd

    # 0 at the 1-sigma boundary (ratio=1.0), 100 at 2x distance (ratio=2.0)
    if em_ratio <= 0:
        return 0.0  # Short strike is ITM or ATM
    elif em_ratio < 1.0:
        # Inside the expected move — penalize but don't zero out
        return em_ratio * 50.0  # 0-50 range when inside EM
    else:
        # Outside the expected move — reward
        buffer = em_ratio - 1.0
        return min(100.0, 50.0 + buffer * 50.0)


def norm_liquidity(bid, ask, mark):
    """Liquidity normalized from bid-ask spread tightness.

    100 at spread <= 1% of mark, 0 at spread >= 5% of mark.
    """
    if not mark or mark <= 0 or bid is None or ask is None:
        return 50.0  # Neutral if unavailable

    spread = ask - bid
    if spread <= 0:
        return 100.0

    spread_pct = spread / mark * 100.0

    if spread_pct <= 1.0:
        return 100.0
    elif spread_pct >= 5.0:
        return 0.0
    else:
        return (5.0 - spread_pct) / 4.0 * 100.0


def norm_trend(trend, spread_type, rsi=None):
    """Trend alignment normalized.

    PCS (bullish) wants BULLISH/RECOVERING trend.
    CCS (bearish) wants BEARISH/WEAKENING trend.
    NEUTRAL is always 50.

    RSI penalty: overbought (>70) hurts PCS, oversold (<30) hurts CCS.

    Returns: 100=aligned, 75=partially aligned, 50=neutral, 25=partially against, 0=against
    """
    if not trend or trend == "NEUTRAL":
        return 50.0

    if spread_type == "IC":
        return 50.0  # Iron Condors are direction-neutral

    alignment_map = {
        "PCS": {
            "BULLISH": 100.0,
            "RECOVERING": 75.0,
            "NEUTRAL": 50.0,
            "WEAKENING": 25.0,
            "BEARISH": 0.0,
        },
        "CCS": {
            "BEARISH": 100.0,
            "WEAKENING": 75.0,
            "NEUTRAL": 50.0,
            "RECOVERING": 25.0,
            "BULLISH": 0.0,
        },
    }

    stype = "PCS" if spread_type == "PCS" else "CCS"
    base_score = alignment_map.get(stype, {}).get(trend, 50.0)

    # RSI penalty: overbought hurts PCS, oversold hurts CCS
    if rsi is not None:
        if spread_type == "PCS" and rsi > 70:
            penalty = min(0.5, (rsi - 70) / 40)
            base_score *= (1 - penalty)
        elif spread_type == "CCS" and rsi < 30:
            penalty = min(0.5, (30 - rsi) / 40)
            base_score *= (1 - penalty)

    return round(base_score, 1)

#############################################
# COMPOSITE SCORE
#############################################

def calc_composite_score(signal, iv_data=None, technicals=None,
                         all_theta_efficiencies=None, weights=None):
    """Calculate the composite score for a single spread signal.

    Args:
        signal: dict with keys from screen_spreads() output
        iv_data: dict from iv_analysis.run_iv_analysis() for this symbol
        technicals: dict from calc_technicals() for this symbol
        all_theta_efficiencies: list of theta/maxloss ratios for percentile ranking
        weights: dict overriding DEFAULT_WEIGHTS

    Returns:
        dict with composite_score (0-100), grade, factor_scores, and factor_details
    """
    w = weights or DEFAULT_WEIGHTS
    total_weight = sum(w.values())
    if total_weight == 0:
        total_weight = 100

    # Extract signal fields
    rr_pct = signal.get("rr_pct", 0)
    pop_pct = signal.get("pop_pct", 0)
    net_theta = signal.get("net_theta", 0)
    max_loss = signal.get("max_loss", 0)
    short_strike = signal.get("short_strike", 0)
    underlying = signal.get("underlying_price", 0)
    spread_type = signal.get("type", "PCS")

    # IV data
    iv_rank = iv_data.get("iv_rank") if iv_data else None
    em_data = iv_data.get("expected_moves", {}) if iv_data else {}
    em_monthly = em_data.get("monthly", {})
    em_1sd = em_monthly.get("move_dollars", 0) if em_monthly else 0

    # IV/HV ratio
    hv_current = iv_data.get("hv_current") if iv_data else None
    iv_hv_ratio = None
    if iv_rank is not None and hv_current and hv_current > 0:
        current_iv = iv_data.get("current_iv", 0) or 0
        if current_iv > 0:
            iv_hv_ratio = current_iv / hv_current

    # Vega risk
    net_vega = signal.get("net_vega", 0)

    # Technicals
    trend = technicals.get("trend", "NEUTRAL") if technicals else "NEUTRAL"
    rsi = technicals.get("rsi14") if technicals else None

    # Prefer spread-level bid/ask when present (scanner emits these post-2026-05-27).
    # Falls back to leg-level bid/ask, then synthetic estimate.
    spread_bid = signal.get("spread_bid")
    spread_ask = signal.get("spread_ask")
    if spread_bid is not None and spread_ask is not None and spread_bid > 0 and spread_ask > 0:
        bid_actual = spread_bid
        ask_actual = spread_ask
        mark_actual = (spread_bid + spread_ask) / 2
    else:
        bid_actual = signal.get("bid", 0) or 0
        ask_actual = signal.get("ask", 0) or 0
        if bid_actual > 0 and ask_actual > 0:
            mark_actual = (bid_actual + ask_actual) / 2
        else:
            # Fallback to estimate only if actual data is missing
            bid_actual = signal.get("credit", 0) * 0.92
            ask_actual = signal.get("credit", 0) * 1.08
            mark_actual = signal.get("credit", 0)

    # Normalize each factor
    scores = {
        "rr":    norm_rr(rr_pct),
        "pop":   norm_pop(pop_pct),
        "theta": norm_theta(net_theta, max_loss, all_theta_efficiencies),
        "iv":    norm_iv_rank(iv_rank),
        "iv_hv": norm_iv_hv_ratio(iv_hv_ratio),
        "vega":  norm_vega_risk(net_vega, max_loss, iv_rank),
        "em":    norm_em_buffer(short_strike, underlying, em_1sd, spread_type),
        "liq":   norm_liquidity(bid_actual, ask_actual, mark_actual),
        "trend": norm_trend(trend, spread_type, rsi=rsi),
    }

    # GEX + DEX proximity — 50 neutral when walls absent (e.g., swing trades).
    gex_walls = signal.get("gex_walls")
    dex_walls = signal.get("dex_walls")
    scores["gex"] = norm_gex_proximity(short_strike, underlying, gex_walls)
    scores["dex"] = norm_dex_proximity(short_strike, underlying, dex_walls)

    # Weighted sum
    composite = sum(w.get(k, 0) / total_weight * v for k, v in scores.items())
    composite = round(max(0, min(100, composite)), 1)

    # Grade
    if composite >= 80:
        grade = "Strong"
    elif composite >= 60:
        grade = "Good"
    elif composite >= 40:
        grade = "Marginal"
    else:
        grade = "Weak"

    return {
        "composite_score": composite,
        "grade": grade,
        "factor_scores": {k: round(v, 1) for k, v in scores.items()},
        "weights_used": w,
    }


def score_iron_condor(pcs_score_data, ccs_score_data, net_delta):
    """Score an Iron Condor by averaging leg scores + delta neutrality bonus.

    Bonus: 0-5 points for delta neutrality (5 when |net_delta| < 0.01).
    """
    pcs_composite = pcs_score_data.get("composite_score", 0) if pcs_score_data else 0
    ccs_composite = ccs_score_data.get("composite_score", 0) if ccs_score_data else 0

    avg = (pcs_composite + ccs_composite) / 2.0

    # Delta neutrality bonus: 5 points at perfect neutral, 0 at |delta| >= 0.10
    if net_delta is not None:
        abs_d = abs(net_delta)
        if abs_d < 0.01:
            bonus = 5.0
        elif abs_d < 0.10:
            bonus = 5.0 * (1.0 - (abs_d - 0.01) / 0.09)
        else:
            bonus = 0.0
    else:
        bonus = 0.0

    composite = round(min(100, avg + bonus), 1)

    if composite >= 80:
        grade = "Strong"
    elif composite >= 60:
        grade = "Good"
    elif composite >= 40:
        grade = "Marginal"
    else:
        grade = "Weak"

    return {
        "composite_score": composite,
        "grade": grade,
        "pcs_score": pcs_composite,
        "ccs_score": ccs_composite,
        "delta_bonus": round(bonus, 1),
    }


#############################################
# BATCH SCORING
#############################################

def score_all_signals(signals, iv_data_map, technicals_map, weights=None):
    """Score all signals in a list, injecting composite_score into each signal dict.

    Args:
        signals: list of signal dicts from screen_spreads()
        iv_data_map: {symbol: iv_data_dict}
        technicals_map: {symbol: technicals_dict}
        weights: optional weight overrides

    Returns:
        signals list (mutated with composite_score, grade, factor_scores added)
    """
    # Pre-compute theta efficiencies for percentile ranking
    theta_effs = []
    for s in signals:
        ml = s.get("max_loss", 0)
        nt = s.get("net_theta", 0)
        if ml > 0:
            theta_effs.append(abs(nt) / ml * 100)

    for s in signals:
        sym = s.get("symbol", "")
        iv = iv_data_map.get(sym, {})
        tech = technicals_map.get(sym, {})
        tech_vals = tech.get("technicals") if isinstance(tech, dict) and "technicals" in tech else tech

        result = calc_composite_score(s, iv_data=iv, technicals=tech_vals,
                                       all_theta_efficiencies=theta_effs, weights=weights)
        s["composite_score"] = result["composite_score"]
        s["grade"] = result["grade"]
        s["factor_scores"] = result["factor_scores"]

    # Re-sort by composite score (primary), R:R (secondary)
    signals.sort(key=lambda x: (x.get("composite_score", 0), x.get("rr_pct", 0)), reverse=True)
    return signals
