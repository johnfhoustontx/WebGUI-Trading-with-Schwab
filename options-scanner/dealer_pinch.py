"""
dealer_pinch.py - Vanna/Charm Exhaustion ("Dealer Pinch") detector
Version: 1.0.0
Last Updated: 2026-06-09

Pure detector for the dealer-pinch setup: near expiry, with price on the
dominant open-interest node and IV elevated-but-falling, Charm/Vanna re-hedging
tends to PIN price to the node — or, if the node breaks, drive a localized
squeeze. Consumes already-computed engine outputs + IV-percentile / RV-trend
inputs; never fetches or touches Tk, so it is fully unit-testable.

See docs/plans/2026-06-09-dealer-pinch-detector-design.md.

Version 1.0.0 Changes:
- Initial implementation: dominant_oi_node, classify_pinch_regime,
  build_pinch_playbook, evaluate_dealer_pinch.
"""

#############################################
# TUNABLE THRESHOLDS
#############################################

DTE_MAX = 5            # C1: days to expiration strictly below this
NODE_BAND_PCT = 0.01   # C2: |spot - node| / spot must be within this
IV_PCTILE_MIN = 80.0   # C3a: vol-index percentile floor
IV_INVALIDATE = 60.0   # pin thesis invalid once IV percentile falls below this


#############################################
# DOMINANT OI NODE
#############################################

def _nearest_exp_key(exp_map):
    """Smallest non-negative DTE key in a Schwab '<date>:<dte>' exp map."""
    best_key, best_dte = None, float("inf")
    for key in exp_map or {}:
        parts = key.split(":")
        if len(parts) >= 2:
            try:
                dte = int(float(parts[1]))
            except (ValueError, IndexError):
                continue
            if 0 <= dte < best_dte:
                best_key, best_dte = key, dte
    if best_key is None and exp_map:
        best_key = next(iter(exp_map))
    return best_key


def dominant_oi_node(chain):
    """Highest combined (call+put) open-interest strike on the nearest expiry.

    Returns ``{"node": strike|None, "secondary": strike|None,
    "dominance": float}`` where dominance is the node's share of total OI
    (0-1) — a proxy for how concentrated/pinnable the node is.
    """
    none = {"node": None, "secondary": None, "dominance": 0.0}
    if not chain:
        return none
    call_map = chain.get("callExpDateMap", {})
    put_map = chain.get("putExpDateMap", {})
    combined = {}
    for exp_map in (call_map, put_map):
        key = _nearest_exp_key(exp_map)
        if not key:
            continue
        for strike_str, contracts in exp_map.get(key, {}).items():
            strike = float(strike_str)
            oi = sum((c.get("openInterest") or 0) for c in contracts)
            combined[strike] = combined.get(strike, 0) + oi
    combined = {s: oi for s, oi in combined.items() if oi > 0}
    if not combined:
        return none
    total = sum(combined.values())
    ranked = sorted(combined, key=lambda s: combined[s], reverse=True)
    node = ranked[0]
    secondary = ranked[1] if len(ranked) > 1 else None
    return {
        "node": node,
        "secondary": secondary,
        "dominance": combined[node] / total if total else 0.0,
    }


#############################################
# REGIME + PLAYBOOK
#############################################

def _break_buffer(spot):
    return max(NODE_BAND_PCT / 3.0 * spot, 1.0)


def classify_pinch_regime(armed, spot, node, secondary, gex_flip):
    """Return ``{"regime", "levels"}``.

    PIN  — armed and spot inside the node band: mean-revert to the node.
    BREAK — armed but spot has moved beyond node ± buffer: momentum toward the
            secondary node (amplified below the GEX flip).
    WATCHING — not armed.
    """
    levels = {
        "pin_target": node,
        "break_trigger": None,
        "invalidation": f"IV %ile < {IV_INVALIDATE:.0f}, or spot > "
                        f"{NODE_BAND_PCT:.0%} from node",
    }
    if node is not None and spot:
        buf = _break_buffer(spot)
        levels["break_trigger"] = (node - buf) if spot <= node else (node + buf)

    if not armed or node is None or not spot:
        return {"regime": "WATCHING", "levels": levels}

    if abs(spot - node) > _break_buffer(spot):
        return {"regime": "BREAK", "levels": levels}
    return {"regime": "PIN", "levels": levels}


def build_pinch_playbook(regime):
    """Plain-English scenario playbook for a regime."""
    if regime == "PIN":
        return ("PIN: dealer hedging mean-reverts price to the node. Fade the "
                "edges of the range and sell premium centered on the node "
                "(condor / straddle); take profit as price decays into it.")
    if regime == "BREAK":
        return ("BREAK: a decisive close past the trigger releases the pin — "
                "trade momentum toward the secondary node; the move squeezes "
                "harder if price is below the GEX flip (negative gamma).")
    return ("WATCHING: not all pinch conditions are met yet. Watch the checklist "
            "— when the last condition flips, the pin/break setup arms.")


#############################################
# EVALUATOR
#############################################

def _clamp01(x):
    return max(0.0, min(1.0, x))


def evaluate_dealer_pinch(symbol, chain, spot, dte, iv_pctile, rv_trend,
                          gex_flip=None, pin_risk_score=None,
                          forced_hedge_dir=None, hours_to_close=None):
    """Evaluate the dealer-pinch setup. Returns the full state dict that drives
    the gamma panel, the AI prompt block, and the HTML page. Never raises;
    missing inputs become ``None`` conditions and force regime WATCHING.
    """
    node_info = dominant_oi_node(chain)
    node = node_info["node"]
    dominance = node_info["dominance"]

    # ── Conditions (None = data unavailable / "n/a") ──
    c1 = (dte < DTE_MAX) if dte is not None else None
    if node is not None and spot:
        c2 = abs(spot - node) / spot < NODE_BAND_PCT
    else:
        c2 = None
    c3a = (iv_pctile >= IV_PCTILE_MIN) if iv_pctile is not None else None
    c3b = (rv_trend or {}).get("falling")  # bool or None

    conditions = {"c1": c1, "c2": c2, "c3a": c3a, "c3b": c3b}
    armed = all(v is True for v in conditions.values())

    regime_info = classify_pinch_regime(armed, spot, node,
                                        node_info["secondary"], gex_flip)
    regime = regime_info["regime"]

    # ── Confidence (0-100), monotonic in pin risk ──
    pin = _clamp01(pin_risk_score) if pin_risk_score is not None else 0.0
    iv_margin = (_clamp01((iv_pctile - IV_PCTILE_MIN) / (100.0 - IV_PCTILE_MIN))
                 if iv_pctile is not None else 0.0)
    rv_bonus = 1.0 if c3b else 0.0
    dte_prox = _clamp01((DTE_MAX - dte) / DTE_MAX) if dte is not None else 0.0
    confidence = round(100.0 * (
        0.40 * pin + 0.20 * iv_margin + 0.15 * rv_bonus
        + 0.15 * _clamp01(dominance) + 0.10 * dte_prox), 1)

    n_met = sum(1 for v in conditions.values() if v is True)
    reason = ("All 4 conditions met — pinch armed."
              if armed else f"{n_met} of 4 conditions met.")

    return {
        "symbol": symbol,
        "armed": armed,
        "confidence": confidence,
        "regime": regime,
        "conditions": conditions,
        "node": {
            "strike": node,
            "dist_pts": (spot - node) if (node is not None and spot) else None,
            "dist_pct": ((spot - node) / spot)
            if (node is not None and spot) else None,
        },
        "node_dominance": dominance,
        "secondary_node": node_info["secondary"],
        "pin_risk": pin_risk_score,
        "iv_pctile": iv_pctile,
        "rv_trend": rv_trend,
        "forced_hedge_dir": forced_hedge_dir,
        "levels": regime_info["levels"],
        "time_to_resolve": {"dte": dte, "hours_to_close": hours_to_close},
        "playbook": build_pinch_playbook(regime),
        "reason": reason,
    }
