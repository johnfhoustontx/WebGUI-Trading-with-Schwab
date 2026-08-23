"""
gamma_tool.py - GEX (Gamma Exposure) Scanner Tool
Version: 1.0.0
Last Updated: 2026-04-12

Computes and visualizes Gamma Exposure (GEX) by strike for 0-DTE options.
Launched from the dashboard as a standalone Toplevel window.

GEX Formula:
    Standard:  gamma * open_interest * 100 * spot^2  (calls +, puts -)
    Volume:    gamma * total_volume * 100 * spot^2    (calls +, puts -)
"""

import math
import json
import logging
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import gex_history_db as _history_db
from gex_status import (
    classify_collector_status,
    TZ as STATUS_TZ,
    STALE_AFTER_SEC,
)

from options_calculator import (RISK_FREE_RATE, bs_charm, bs_delta, bs_gamma,
                                bs_vanna)
from dealer_pinch import evaluate_dealer_pinch, dominant_oi_node
from iv_percentile import percentile_rank, realized_vol_trend

# NOTE: this engine imports NO GUI toolkit and no plotting stack, at module
# scope or anywhere else. Every drawing/Tk helper was deleted 2026-08-20 with
# the Tk window that called it; keep it that way — options_svc imports this.
import numpy as np

log = logging.getLogger("scanner")
TZ = ZoneInfo("America/Chicago")
FONT = "Segoe UI"
FONT_MONO = "Consolas"



# 0-DTE contract settlement in Central Time. SPX/SPXW PM-settled 0-DTE
# options stop trading at 15:00 CT (4:00 PM ET).
CLOSE_HOUR_CT = 15
CLOSE_MIN_CT = 0


def project_0dte_pressure(
    contracts,
    spot,
    hours_to_close,
):
    """Compute (net_delta_0dte, projected_net_delta_close, hedge_pressure) in dollars.

    contracts: iterable of (contract_dict, option_type) pairs where each
        contract_dict has keys 'delta', 'charm', 'openInterest', and option_type
        is 'call' or 'put'.
    spot: underlying price (dollars).
    hours_to_close: non-negative hours from now until 15:00 CT. Clamped at 0.

    Returns (None, None, None) when `contracts` is empty (distinguishes "no data"
    from "zero pressure"). Otherwise returns three floats in dollars.

    Projection uses per-year charm (matches options_calculator.bs_charm):
        delta_projected = delta + charm × (hours_to_close / (365 × 24))
    clamped to [0, 1] for calls and [-1, 0] for puts so final-minutes
    over-shoot cannot produce physically impossible deltas.
    """
    if not contracts:
        return (None, None, None)

    hours_to_close = max(0.0, hours_to_close)
    dt_years = hours_to_close / (365.0 * 24.0)

    total_now = 0.0
    total_proj = 0.0
    for c, opt_type in contracts:
        delta = c.get("delta") or 0.0
        charm = c.get("charm") or 0.0
        oi = c.get("openInterest") or 0
        if oi <= 0 or delta == 0.0:
            continue

        if opt_type == "call":
            lo, hi = 0.0, 1.0
        else:
            lo, hi = -1.0, 0.0

        delta_proj = max(lo, min(hi, delta + charm * dt_years))

        contract_multiplier = 100.0
        total_now  += oi * delta      * contract_multiplier * spot
        total_proj += oi * delta_proj * contract_multiplier * spot

    return (total_now, total_proj, total_proj - total_now)


def project_0dte_drift_by_strike(contracts, spot, hours_to_close):
    """``{strike: dollar delta drift}`` for the 0-DTE book, kept PER STRIKE.

    Same per-contract projection as ``project_0dte_pressure`` (delta advanced by
    charm to the close, clamped), but attributed to the strike it came from instead
    of summed. Its total equals that function's ``hedge_pressure`` by construction —
    this is a REDISTRIBUTION, not a new number.

    Why it exists: ``compute_projected_flip`` used to spread the TOTAL pressure
    evenly over every strike (``hedge / n``). Charm drift is not uniform — it
    concentrates in near-the-money 0-DTE strikes — so averaging it across the whole
    chain (including deep wings holding no 0-DTE interest) lifted the entire DEX
    curve. Measured on live $SPX: that erased 56 of 57 negative strikes and moved
    the projected crossing to ~9,600 with spot at 7,791. Putting each strike's own
    drift where it belongs fixes it.

    ``contracts``: iterable of ``(contract_dict, option_type, strike)``. Strikes
    with no usable contract are omitted (rather than mapped to 0.0), so callers can
    tell "no 0-DTE interest here" from "drift happens to be zero". Never raises."""
    out = {}
    if not contracts:
        return out
    dt_years = max(0.0, hours_to_close) / (365.0 * 24.0)
    for c, opt_type, strike in contracts:
        delta = c.get("delta") or 0.0
        charm = c.get("charm") or 0.0
        oi = c.get("openInterest") or 0
        if oi <= 0 or delta == 0.0:
            continue
        lo, hi = (0.0, 1.0) if opt_type == "call" else (-1.0, 0.0)
        delta_proj = max(lo, min(hi, delta + charm * dt_years))
        drift = oi * (delta_proj - delta) * 100.0 * spot
        out[strike] = out.get(strike, 0.0) + drift
    return out


def iter_contracts(chain, dte=None):
    """Yield (contract_dict, option_type, strike_float) for every contract in a chain.

    Args:
        chain: Schwab-shaped chain dict with callExpDateMap / putExpDateMap.
        dte: Optional int; if provided, yields only contracts where
            contract['daysToExpiration'] == dte.

    Iterates across ALL expiries in both call and put maps. Consumers
    needing per-expiry grouping should filter as they iterate.
    """
    if not chain:
        return
    for side, map_key in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
        exp_map = chain.get(map_key) or {}
        for exp_key, strikes in exp_map.items():
            for strike_str, contracts in strikes.items():
                try:
                    strike = float(strike_str)
                except (TypeError, ValueError):
                    continue
                for c in contracts:
                    if dte is not None and c.get("daysToExpiration") != dte:
                        continue
                    yield c, side, strike


def find_key_gamma_strike(grid, spot, band_pct=0.01):
    """Return the strike with largest |net| within ±band_pct of spot, or None."""
    if not grid or spot <= 0:
        return None
    lo, hi = spot * (1 - band_pct), spot * (1 + band_pct)
    candidates = [(s, v.get("net", 0.0)) for s, v in grid.items() if lo <= s <= hi]
    if not candidates:
        return None
    return max(candidates, key=lambda sv: abs(sv[1]))[0]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GammaEngine — pure computation, no UI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# How many strikes either side of a zero crossing must keep their sign for it to
# count as a gamma flip. A genuine flip separates a SUSTAINED positive region
# from a sustained negative one; a profile that pops negative for one strike and
# returns is a lumpy strike, not a regime boundary.
#
# 2 is measured, not chosen for elegance. Over the 2026-08-19 session, against
# every alternative tried (strongest-crossing, cumulative totals, and re-binning
# the profile at 0.15/0.25/0.40% of spot, each crossed with k = 1/2/3):
#
#   k=2 leaves SPY and QQQ BIT-IDENTICAL to the old rule (range 0.17 / 0.09,
#       above-below flip rate 1% / 8%) - they never had the problem, and a fix
#       that moved them would be the worse defect;
#   k=2 fixes $SPX outright: flip range 32.56 -> 1.08, flip rate 25% -> 1%;
#   k=2 halves $NDX: range 401 -> 207, rate 31% -> 17%;
#   k=3 fixes $NDX but destroys $SPX - 323 of 324 snapshots report NO flip;
#   re-binning at any width widened the ETF ranges (SPY 0.17 -> 1.07+).
#
# $NDX therefore remains PARTLY affected. Its 25-wide, unevenly-spaced ladder
# oscillates even at 2-strike persistence, and no rule tested fixed it without
# damaging the symbols that work. That is a known, documented limitation, not an
# oversight: docs/plans/2026-08-19-gamma-flip-spot-tracking-design.md
_FLIP_PERSIST_STRIKES = 2


def _flip_sign_persists(strikes, gex, i, k=_FLIP_PERSIST_STRIKES):
    """True when the sign holds ``k`` strikes either side of the crossing
    between ``strikes[i]`` and ``strikes[i + 1]``.

    ZERO-NET STRIKES ARE SKIPPED, not counted against the run. A strike nobody
    traded is the absence of data - the same reason the crossing test itself is
    strict - so it can neither confirm persistence nor deny it. Counting one as a
    sign break would reject genuine flips that merely happen to sit beside an
    untraded strike, which on an index ladder carrying ~135 dead strikes is most
    of them.

    Returns False when the grid does not offer ``k`` live strikes on both sides:
    persistence that cannot be CHECKED is not persistence observed, and accepting
    an unverifiable crossing at the edge of the ladder is how an artifact gets
    promoted to a level.
    """
    def _run(start, step, ref):
        seen, j = 0, start
        while 0 <= j < len(strikes) and seen < k:
            v = gex[strikes[j]]["net"]
            if v != 0.0:
                if v * ref <= 0:
                    return False
                seen += 1
            j += step
        return seen >= k

    v1, v2 = gex[strikes[i]]["net"], gex[strikes[i + 1]]["net"]
    return _run(i, -1, v1) and _run(i + 1, 1, v2)


class GammaEngine:
    """Computes GEX from a Schwab option chain and manages snapshots."""

    def __init__(self):
        self.current = None       # Latest GEX snapshot
        self.previous = None      # Prior snapshot
        self.market_open = None   # First fetch of the day
        self._today_str = None    # Track which day market_open belongs to
        self._last_dte = 0        # DTE of the nearest expiration used
        self._last_chain = None    # retained for forward-projection (heatmap)

    # ── Public API ──

    def calc_from_chain(self, chain, use_volume=False):
        """Compute GEX by strike from an already-fetched option chain.

        Returns dict: {"spot": float, "gex": {strike: {"call": float, "put": float, "net": float}}, "strike_count": int}
        or None on failure.

        Units & scope (important — read before comparing to a reference):
          * Per-strike values are dollar-gamma per **1% spot move**
            (gamma * OI * 100 * spot^2 * 0.01 — the SqueezeMetrics/SpotGamma
            convention). The term-structure path (compute_term_grid) uses the
            SAME per-1% unit, so the two are directly comparable.
          * This aggregates the **NEAREST expiration only** (via
            _find_nearest_exp_key), NOT the full option surface. Absolute
            magnitudes are therefore an internally-consistent RELATIVE index and
            will NOT numerically match a full-surface reference such as
            SpotGamma. The sign, the gamma-flip location, and the call/put wall
            strikes ARE methodologically standard and comparable.
        """
        if not chain:
            return None

        spot = chain.get("underlyingPrice", 0)
        if spot <= 0:
            return None

        self._last_chain = chain

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})

        today = datetime.now(TZ).strftime("%Y-%m-%d")

        call_exp_key, call_dte = self._find_nearest_exp_key(call_map, today)
        put_exp_key, put_dte = self._find_nearest_exp_key(put_map, today)

        if not call_exp_key and not put_exp_key:
            log.warning("GEX: No expiration found for %s", today)
            return None

        # Track the DTE for display purposes
        dtes = [d for d in [call_dte, put_dte] if d is not None]
        self._last_dte = min(dtes) if dtes else 0

        gex = {}
        weight_field = "totalVolume" if use_volume else "openInterest"

        # Process calls (positive GEX)
        if call_exp_key:
            for strike_str, contracts in call_map.get(call_exp_key, {}).items():
                strike = float(strike_str)
                for c in contracts:
                    gamma = c.get("gamma", 0) or 0
                    weight = c.get(weight_field, 0) or 0
                    # GEX normalized to "per 1% move in underlying":
                    # OI * 100(contract multiplier) * gamma * spot^2 * 0.01
                    val = gamma * weight * 100 * spot * spot * 0.01
                    if strike not in gex:
                        gex[strike] = {"call": 0.0, "put": 0.0, "net": 0.0}
                    gex[strike]["call"] += val

        # Process puts (negative GEX)
        if put_exp_key:
            for strike_str, contracts in put_map.get(put_exp_key, {}).items():
                strike = float(strike_str)
                for c in contracts:
                    gamma = c.get("gamma", 0) or 0
                    weight = c.get(weight_field, 0) or 0
                    # GEX normalized to "per 1% move in underlying":
                    # OI * 100(contract multiplier) * gamma * spot^2 * 0.01
                    val = gamma * weight * 100 * spot * spot * 0.01
                    if strike not in gex:
                        gex[strike] = {"call": 0.0, "put": 0.0, "net": 0.0}
                    gex[strike]["put"] -= val

        # Compute net
        for strike in gex:
            gex[strike]["net"] = gex[strike]["call"] + gex[strike]["put"]

        result = {"spot": spot, "gex": gex, "strike_count": len(gex)}

        # Manage snapshots
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        if self._today_str != today_str:
            self._today_str = today_str
            self.market_open = None

        self.previous = self.current
        self.current = result
        if self.market_open is None:
            self.market_open = result

        return result

    def calc_expected_move_from_chain(self, chain):
        """Calculate 0-DTE expected move from ATM IV.

        EM = spot * atm_iv * sqrt(hours_left / (365 * 24))
        Returns float or None.
        """
        if not chain:
            return None

        spot = chain.get("underlyingPrice", 0)
        if spot <= 0:
            return None

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})
        today = datetime.now(TZ).strftime("%Y-%m-%d")

        strikes_data = {}
        exp_key, _ = self._find_nearest_exp_key(call_map, today)
        if exp_key:
            for sk, contracts in call_map[exp_key].items():
                strikes_data.setdefault(sk, []).extend(contracts)
        exp_key, _ = self._find_nearest_exp_key(put_map, today)
        if exp_key:
            for sk, contracts in put_map[exp_key].items():
                strikes_data.setdefault(sk, []).extend(contracts)

        atm_iv = self._get_atm_iv(strikes_data, spot)
        if atm_iv is None or atm_iv <= 0:
            return None

        # Hours left until close (4:15 PM ET = 3:15 PM CT)
        now = datetime.now(TZ)
        close_hour, close_min = 15, 15  # 3:15 PM CT
        hours_left = (close_hour - now.hour) + (close_min - now.minute) / 60.0
        if hours_left <= 0:
            hours_left = 0.1  # small default if past close

        # atm_iv from Schwab is in percentage (e.g. 25.5 for 25.5%)
        iv_decimal = atm_iv / 100.0
        em = spot * iv_decimal * math.sqrt(hours_left / (365 * 24))
        return round(em, 2)

    def calc_charm_from_chain(self, chain, use_volume=False):
        """Compute Charm Exposure (ChEX) by strike from an option chain.

        Charm (delta decay) = d(delta)/d(time). We compute it via Black-Scholes
        since Schwab doesn't return charm directly.

        ChEX per strike = charm * OI * 100 * spot
            Calls: positive charm → delta increasing → dealer sells → selling pressure
            Puts:  negative charm → delta decreasing → dealer buys → buying pressure

        Returns dict: {"spot": float, "gex": {strike: {"call": float, "put": float, "net": float}}}
        Uses the same key structure as GEX so the chart code can render either.
        """
        if not chain:
            return None

        spot = chain.get("underlyingPrice", 0)
        if spot <= 0:
            return None

        self._last_chain = chain

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})
        today = datetime.now(TZ).strftime("%Y-%m-%d")

        call_exp_key, call_dte = self._find_nearest_exp_key(call_map, today)
        put_exp_key, put_dte = self._find_nearest_exp_key(put_map, today)

        if not call_exp_key and not put_exp_key:
            return None

        dtes = [d for d in [call_dte, put_dte] if d is not None]
        dte = min(dtes) if dtes else 0

        # Time to expiration in years — use hours remaining for intraday precision.
        # Clamp hours_left to >= 0 so post-close runs (now.hour >= close_hour)
        # produce a T consistent with calc_all_from_chain and calc_dex_from_chain,
        # which already clamp. Without the clamp, T was ~percent-scale lower than
        # the bundled equivalent in calc_all, which broke equivalence checks.
        now = datetime.now(TZ)
        close_hour, close_min = 15, 15  # 3:15 PM CT
        hours_left = max(
            0.0, (close_hour - now.hour) + (close_min - now.minute) / 60.0,
        )
        T = max((dte * 24 + hours_left) / (365 * 24), 1e-6)

        r = RISK_FREE_RATE  # risk-free rate (single source: options_calculator)
        weight_field = "totalVolume" if use_volume else "openInterest"
        chex = {}

        # Process calls
        if call_exp_key:
            for strike_str, contracts in call_map.get(call_exp_key, {}).items():
                strike = float(strike_str)
                for c in contracts:
                    iv_pct = c.get("volatility", 0) or 0
                    if iv_pct <= 0:
                        continue
                    sigma = iv_pct / 100.0
                    weight = c.get(weight_field, 0) or 0
                    charm = bs_charm(spot, strike, T, r, sigma, "call")
                    val = charm * weight * 100 * spot
                    if strike not in chex:
                        chex[strike] = {"call": 0.0, "put": 0.0, "net": 0.0}
                    chex[strike]["call"] += val

        # Process puts
        if put_exp_key:
            for strike_str, contracts in put_map.get(put_exp_key, {}).items():
                strike = float(strike_str)
                for c in contracts:
                    iv_pct = c.get("volatility", 0) or 0
                    if iv_pct <= 0:
                        continue
                    sigma = iv_pct / 100.0
                    weight = c.get(weight_field, 0) or 0
                    charm = bs_charm(spot, strike, T, r, sigma, "put")
                    val = charm * weight * 100 * spot
                    if strike not in chex:
                        chex[strike] = {"call": 0.0, "put": 0.0, "net": 0.0}
                    chex[strike]["put"] += val

        # Compute net
        for strike in chex:
            chex[strike]["net"] = chex[strike]["call"] + chex[strike]["put"]

        return {"spot": spot, "gex": chex, "strike_count": len(chex)}

    def calc_dex_from_chain(self, chain, use_volume=False):
        """Compute Delta Exposure (DEX) by strike from an option chain.

        DEX per strike = OI × delta × 100 × spot
            Calls: delta > 0 → positive contribution (dealers long call delta)
            Puts:  delta < 0 → negative contribution
        Uses chain-provided delta when available, Black-Scholes fallback otherwise.
        Returns the same shape as calc_from_chain plus three 0-DTE pressure fields.
        """
        if not chain:
            return None

        spot = chain.get("underlyingPrice", 0)
        if spot <= 0:
            return None

        self._last_chain = chain

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})
        today = datetime.now(TZ).strftime("%Y-%m-%d")

        call_exp_key, call_dte = self._find_nearest_exp_key(call_map, today)
        put_exp_key, put_dte = self._find_nearest_exp_key(put_map, today)

        if not call_exp_key and not put_exp_key:
            return None

        dtes = [d for d in [call_dte, put_dte] if d is not None]
        dte = min(dtes) if dtes else 0
        self._last_dte = dte

        # Time to expiry in years, used both for the BS-delta fallback AND for the
        # charm computation on 0-DTE contracts that lack a chain-provided charm.
        now = datetime.now(TZ)
        hours_left_trading = (CLOSE_HOUR_CT - now.hour) + (CLOSE_MIN_CT - now.minute) / 60.0
        hours_left_trading = max(0.0, hours_left_trading)
        T = max((dte * 24 + hours_left_trading) / (365 * 24), 1e-6)
        r = RISK_FREE_RATE
        weight_field = "totalVolume" if use_volume else "openInterest"

        dex = {}
        # (contract, option_type, strike) — the strike is carried so the drift can
        # be attributed PER STRIKE, not just totalled.
        zero_dte_contracts = []

        def _process(exp_key, exp_map, option_type):
            if not exp_key:
                return
            for strike_str, contracts in exp_map.get(exp_key, {}).items():
                strike = float(strike_str)
                for c in contracts:
                    weight = c.get(weight_field, 0) or 0
                    delta = c.get("delta")
                    if delta is None or delta == 0:
                        iv_pct = c.get("volatility", 0) or 0
                        if iv_pct > 0:
                            delta = bs_delta(spot, strike, T, r, iv_pct / 100.0, option_type)
                        else:
                            delta = 0.0
                    val = weight * delta * 100 * spot
                    if strike not in dex:
                        dex[strike] = {"call": 0.0, "put": 0.0, "net": 0.0}
                    dex[strike][option_type] += val

                    # 0-DTE slice for pressure computation.
                    contract_dte = c.get("daysToExpiration")
                    if contract_dte == 0 and weight > 0:
                        iv_pct = c.get("volatility", 0) or 0
                        charm_val = c.get("charm")
                        if charm_val is None and iv_pct > 0:
                            charm_val = bs_charm(spot, strike, T, r, iv_pct / 100.0, option_type)
                        elif charm_val is None:
                            charm_val = 0.0
                        zero_dte_contracts.append(
                            ({"delta": delta, "charm": charm_val, "openInterest": weight},
                             option_type, strike),
                        )

        _process(call_exp_key, call_map, "call")
        _process(put_exp_key, put_map, "put")

        for strike in dex:
            dex[strike]["net"] = dex[strike]["call"] + dex[strike]["put"]

        # 0-DTE pressure — only populated if the nearest expiry is today AND we
        # actually collected 0-DTE contracts.
        if dte == 0 and zero_dte_contracts:
            hours_to_close = max(
                0.0,
                (CLOSE_HOUR_CT - now.hour) + (CLOSE_MIN_CT - now.minute) / 60.0,
            )
            net_now, net_proj, pressure = project_0dte_pressure(
                [(c, t) for c, t, _s in zero_dte_contracts], spot, hours_to_close,
            )
            drift_by_strike = project_0dte_drift_by_strike(
                zero_dte_contracts, spot, hours_to_close,
            )
        else:
            net_now = net_proj = pressure = None
            drift_by_strike = {}

        return {
            "spot": spot,
            "gex": dex,
            "strike_count": len(dex),
            "net_delta_0dte": net_now,
            "projected_net_delta_close": net_proj,
            "hedge_pressure": pressure,
            # Per-strike attribution of that same drift — powers the projected flip.
            "hedge_drift_by_strike": drift_by_strike,
        }

    def calc_vanna_from_chain(self, chain, use_volume=False):
        """Compute Vanna Exposure (VEX) by strike from an option chain.

        Vanna = d(delta)/d(sigma) — sensitivity of delta to vol changes.
        Computed via Black-Scholes since Schwab doesn't return vanna directly.

        VEX per strike = vanna * OI * 100 * spot  (dollars per 1-vol-point move)
            Calls and puts both contribute additively (Charm/DEX convention).

        Returns dict: {"spot": float, "gex": {strike: {"call": float, "put": float, "net": float}}}
        Uses the same key structure as GEX/Charm/DEX so the chart code can render either.
        """
        if not chain:
            return None

        spot = chain.get("underlyingPrice", 0)
        if spot <= 0:
            return None

        self._last_chain = chain

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})
        today = datetime.now(TZ).strftime("%Y-%m-%d")

        call_exp_key, call_dte = self._find_nearest_exp_key(call_map, today)
        put_exp_key, put_dte = self._find_nearest_exp_key(put_map, today)

        if not call_exp_key and not put_exp_key:
            return None

        dtes = [d for d in [call_dte, put_dte] if d is not None]
        dte = min(dtes) if dtes else 0

        now = datetime.now(TZ)
        # Clamp hours_left to >= 0: past close (now.hour >= CLOSE_HOUR_CT)
        # would otherwise yield a negative offset and a T that disagrees
        # with calc_all_from_chain (which clamps), producing ~percent-scale
        # drift in bundled-vs-standalone equivalence checks.
        hours_left = max(
            0.0, (CLOSE_HOUR_CT - now.hour) + (CLOSE_MIN_CT - now.minute) / 60.0,
        )
        T = max((dte * 24 + hours_left) / (365 * 24), 1e-6)

        r = RISK_FREE_RATE
        weight_field = "totalVolume" if use_volume else "openInterest"
        vex = {}

        if call_exp_key:
            for strike_str, contracts in call_map.get(call_exp_key, {}).items():
                strike = float(strike_str)
                for c in contracts:
                    iv_pct = c.get("volatility", 0) or 0
                    if iv_pct <= 0:
                        continue
                    sigma = iv_pct / 100.0
                    weight = c.get(weight_field, 0) or 0
                    vanna = bs_vanna(spot, strike, T, r, sigma, "call")
                    val = vanna * weight * 100 * spot
                    if strike not in vex:
                        vex[strike] = {"call": 0.0, "put": 0.0, "net": 0.0}
                    vex[strike]["call"] += val

        if put_exp_key:
            for strike_str, contracts in put_map.get(put_exp_key, {}).items():
                strike = float(strike_str)
                for c in contracts:
                    iv_pct = c.get("volatility", 0) or 0
                    if iv_pct <= 0:
                        continue
                    sigma = iv_pct / 100.0
                    weight = c.get(weight_field, 0) or 0
                    vanna = bs_vanna(spot, strike, T, r, sigma, "put")
                    val = vanna * weight * 100 * spot
                    if strike not in vex:
                        vex[strike] = {"call": 0.0, "put": 0.0, "net": 0.0}
                    vex[strike]["put"] += val

        for strike in vex:
            vex[strike]["net"] = vex[strike]["call"] + vex[strike]["put"]

        return {"spot": spot, "gex": vex, "strike_count": len(vex)}

    def compute_term_grid(self, chain: dict, top_n: int = 5) -> dict:
        """Return per-expiration per-strike Net GEX for the next `top_n` expirations.

        Uses Schwab-supplied gamma when present (falls back to 0.0 if missing -
        BS fallback can be added later if real data shows gaps). Sign convention
        matches the existing GammaEngine: call_gex positive, put_gex positive,
        net = call - put. A call-heavy strike yields positive net.

        Returns:
            {
              "underlying_price": float | None,
              "expirations": [str, ...],         # sorted ascending, top_n entries (YYYY-MM-DD)
              "cells": {exp_str: {strike_float: {
                  "call_gex_usd": float,
                  "put_gex_usd":  float,
                  "net_gex_usd":  float,
              }}}
            }
        """
        S = chain.get("underlyingPrice")
        if S is None:
            S = (chain.get("underlying") or {}).get("last")
        if S is None:
            return {"underlying_price": None, "expirations": [], "cells": {}}

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})

        def _exp_date(k: str) -> str:
            return k.split(":", 1)[0]

        # Sort expirations ascending by date prefix, take the first top_n.
        all_exp_keys = sorted(
            set(call_map.keys()) | set(put_map.keys()),
            key=_exp_date,
        )[:top_n]

        cells: dict = {}
        for exp_key in all_exp_keys:
            exp_date = _exp_date(exp_key)
            per_strike: dict = {}

            for strike_key, contracts in call_map.get(exp_key, {}).items():
                K = float(strike_key)
                c = contracts[0] if contracts else {}
                gamma_c = float(c.get("gamma") or 0.0)
                oi_c = float(c.get("openInterest") or 0)
                # Per-1% dollar-gamma (SqueezeMetrics/SpotGamma convention):
                # OI * gamma * 100(contract multiplier) * S^2 * 0.01 — the SAME
                # unit as the intraday GEX path in calc_from_chain. Without the
                # * 0.01 the term grid would be 100x (per-$1^2, not per-1%).
                call_gex = oi_c * gamma_c * (S ** 2) * 100 * 0.01
                cell = per_strike.setdefault(K, {
                    "call_gex_usd": 0.0, "put_gex_usd": 0.0, "net_gex_usd": 0.0,
                })
                cell["call_gex_usd"] = call_gex

            for strike_key, contracts in put_map.get(exp_key, {}).items():
                K = float(strike_key)
                p = contracts[0] if contracts else {}
                gamma_p = float(p.get("gamma") or 0.0)
                oi_p = float(p.get("openInterest") or 0)
                # Per-1% dollar-gamma — matches call_gex above and the intraday
                # calc_from_chain path (see the * 0.01 note there).
                put_gex = oi_p * gamma_p * (S ** 2) * 100 * 0.01
                cell = per_strike.setdefault(K, {
                    "call_gex_usd": 0.0, "put_gex_usd": 0.0, "net_gex_usd": 0.0,
                })
                cell["put_gex_usd"] = put_gex

            for K, cell in per_strike.items():
                cell["net_gex_usd"] = cell["call_gex_usd"] - cell["put_gex_usd"]

            cells[exp_date] = per_strike

        return {
            "underlying_price": S,
            "expirations": [_exp_date(k) for k in all_exp_keys],
            "cells": cells,
        }

    def calc_all_from_chain(self, chain, use_volume=False):
        """Compute GEX, Charm, DEX, and Vanna in a single pass over the option chain.

        Equivalent to calling calc_from_chain, calc_charm_from_chain,
        calc_dex_from_chain, and calc_vanna_from_chain in sequence, but
        iterates the call/put maps once instead of four times and resolves
        the expiration key + T once.

        Returns (gex_result, charm_result, dex_result, vanna_result). Each
        element matches the corresponding individual calc_* method's shape,
        or None on chain/spot validation failure. Also populates
        self._last_chain, self._last_dte, self.previous/current/market_open
        — matching the side effects of calc_from_chain.
        """
        if not chain:
            return None, None, None, None

        spot = chain.get("underlyingPrice", 0)
        if spot <= 0:
            return None, None, None, None

        self._last_chain = chain

        call_map = chain.get("callExpDateMap", {})
        put_map = chain.get("putExpDateMap", {})
        today = datetime.now(TZ).strftime("%Y-%m-%d")

        call_exp_key, call_dte = self._find_nearest_exp_key(call_map, today)
        put_exp_key, put_dte = self._find_nearest_exp_key(put_map, today)

        if not call_exp_key and not put_exp_key:
            return None, None, None, None

        dtes = [d for d in [call_dte, put_dte] if d is not None]
        dte = min(dtes) if dtes else 0
        self._last_dte = dte

        now = datetime.now(TZ)
        hours_left_trading = max(
            0.0, (CLOSE_HOUR_CT - now.hour) + (CLOSE_MIN_CT - now.minute) / 60.0,
        )
        T = max((dte * 24 + hours_left_trading) / (365 * 24), 1e-6)
        r = RISK_FREE_RATE
        weight_field = "totalVolume" if use_volume else "openInterest"

        gex = {}
        chex = {}
        dex = {}
        vex = {}
        zero_dte_contracts = []

        def _ensure(d, strike):
            if strike not in d:
                d[strike] = {"call": 0.0, "put": 0.0, "net": 0.0}

        def _process(exp_key, exp_map, option_type):
            if not exp_key:
                return
            for strike_str, contracts in exp_map.get(exp_key, {}).items():
                strike = float(strike_str)
                for c in contracts:
                    weight = c.get(weight_field, 0) or 0
                    iv_pct = c.get("volatility", 0) or 0
                    sigma = iv_pct / 100.0 if iv_pct > 0 else 0.0

                    # GEX — uses chain-provided gamma, no BS fallback.
                    gamma = c.get("gamma", 0) or 0
                    g_val = gamma * weight * 100 * spot * spot * 0.01
                    _ensure(gex, strike)
                    if option_type == "call":
                        gex[strike]["call"] += g_val
                    else:
                        gex[strike]["put"] -= g_val

                    # Charm — computed via BS. Skips contracts with iv<=0 (matches calc_charm_from_chain).
                    if iv_pct > 0:
                        charm = bs_charm(spot, strike, T, r, sigma, option_type)
                        ch_val = charm * weight * 100 * spot
                        _ensure(chex, strike)
                        chex[strike][option_type] += ch_val

                    # Vanna — computed via BS, same skip-when-iv<=0 rule as Charm.
                    if iv_pct > 0:
                        vanna = bs_vanna(spot, strike, T, r, sigma, option_type)
                        v_val = vanna * weight * 100 * spot
                        _ensure(vex, strike)
                        vex[strike][option_type] += v_val

                    # DEX — uses chain delta, BS fallback if missing.
                    delta = c.get("delta")
                    if delta is None or delta == 0:
                        delta = bs_delta(spot, strike, T, r, sigma, option_type) if iv_pct > 0 else 0.0
                    d_val = weight * delta * 100 * spot
                    _ensure(dex, strike)
                    dex[strike][option_type] += d_val

                    # 0-DTE slice for DEX pressure projection.
                    contract_dte = c.get("daysToExpiration")
                    if contract_dte == 0 and weight > 0:
                        charm_val = c.get("charm")
                        if charm_val is None:
                            charm_val = bs_charm(spot, strike, T, r, sigma, option_type) if iv_pct > 0 else 0.0
                        zero_dte_contracts.append(
                            ({"delta": delta, "charm": charm_val, "openInterest": weight},
                             option_type, strike),
                        )

        _process(call_exp_key, call_map, "call")
        _process(put_exp_key, put_map, "put")

        for strike in gex:
            gex[strike]["net"] = gex[strike]["call"] + gex[strike]["put"]
        for strike in chex:
            chex[strike]["net"] = chex[strike]["call"] + chex[strike]["put"]
        for strike in dex:
            dex[strike]["net"] = dex[strike]["call"] + dex[strike]["put"]
        for strike in vex:
            vex[strike]["net"] = vex[strike]["call"] + vex[strike]["put"]

        gex_result = {"spot": spot, "gex": gex, "strike_count": len(gex)}

        # Maintain snapshot side-effects to match calc_from_chain.
        today_str = now.strftime("%Y-%m-%d")
        if self._today_str != today_str:
            self._today_str = today_str
            self.market_open = None
        self.previous = self.current
        self.current = gex_result
        if self.market_open is None:
            self.market_open = gex_result

        charm_result = {"spot": spot, "gex": chex, "strike_count": len(chex)} if chex else None
        vanna_result = {"spot": spot, "gex": vex, "strike_count": len(vex)} if vex else None

        # DEX 0-DTE pressure — same logic as calc_dex_from_chain.
        if dte == 0 and zero_dte_contracts:
            hours_to_close = max(
                0.0,
                (CLOSE_HOUR_CT - now.hour) + (CLOSE_MIN_CT - now.minute) / 60.0,
            )
            net_now, net_proj, pressure = project_0dte_pressure(
                [(c, t) for c, t, _s in zero_dte_contracts], spot, hours_to_close,
            )
            drift_by_strike = project_0dte_drift_by_strike(
                zero_dte_contracts, spot, hours_to_close,
            )
        else:
            net_now = net_proj = pressure = None
            drift_by_strike = {}

        dex_result = {
            "spot": spot,
            "gex": dex,
            "strike_count": len(dex),
            "net_delta_0dte": net_now,
            "projected_net_delta_close": net_proj,
            "hedge_pressure": pressure,
            # Per-strike attribution of that same drift — powers the projected flip.
            "hedge_drift_by_strike": drift_by_strike,
        }

        return gex_result, charm_result, dex_result, vanna_result

    def project_exposure_forward(self, view, T_future, r=RISK_FREE_RATE):
        """Project per-strike exposure at a future time-to-expiry T_future.

        Args:
            view: 'gex' | 'charm' | 'dex' | 'vanna' — selects Greek and dollar formula.
            T_future: time-to-expiry in years at the projection slot.
            r: risk-free rate (annualized).

        Returns: {strike_float: dollar_exposure} using the same formula as
        calc_*_from_chain, with IV and OI held constant at their current chain
        values. Empty dict when self._last_chain is None.

        Contracts with iv<=0 or oi<=0 are skipped.
        """
        if not self._last_chain:
            return {}
        spot = self._last_chain.get("underlyingPrice", 0) or 0
        if spot <= 0:
            return {}

        result = {}
        for c, opt_type, strike in iter_contracts(self._last_chain):
            iv_pct = c.get("volatility", 0) or 0
            oi = c.get("openInterest", 0) or 0
            if iv_pct <= 0 or oi <= 0:
                continue
            sigma = iv_pct / 100.0
            if view == "gex":
                g = bs_gamma(spot, strike, T_future, r, sigma, opt_type)
                # Match calc_from_chain: OI * 100 * gamma * spot^2 * 0.01
                val = oi * g * 100 * spot * spot * 0.01
            elif view == "charm":
                g = bs_charm(spot, strike, T_future, r, sigma, opt_type)
                val = oi * g * 100 * spot
            elif view == "dex":
                d = bs_delta(spot, strike, T_future, r, sigma, opt_type)
                val = oi * d * 100 * spot
            elif view == "vanna":
                v = bs_vanna(spot, strike, T_future, r, sigma, opt_type)
                val = oi * v * 100 * spot
            else:
                continue
            result[strike] = result.get(strike, 0.0) + val
        return result

    @staticmethod
    def group_gex(gex, grouping):
        """Aggregate GEX strikes into buckets of size `grouping`.

        Args:
            gex: dict {strike: {"call": float, "put": float, "net": float}}
            grouping: float (0.1, 0.5, 1, 5, 10, 25)

        Returns: dict with same structure, strikes rounded to grouping.
        """
        grouping = float(grouping)
        if grouping <= 0:
            return gex

        # Determine rounding precision from grouping (e.g. 0.1 → 1 decimal)
        precision = max(0, -math.floor(math.log10(grouping))) if grouping < 1 else 0

        grouped = {}
        for strike, vals in gex.items():
            bucket = round(round(strike / grouping) * grouping, precision)
            if bucket not in grouped:
                grouped[bucket] = {"call": 0.0, "put": 0.0, "net": 0.0}
            grouped[bucket]["call"] += vals["call"]
            grouped[bucket]["put"] += vals["put"]
            grouped[bucket]["net"] += vals["net"]
        return grouped

    @staticmethod
    def snapshot_summary(data, view="gex"):
        """Extract headline metrics from a GEX/Charm/DEX result dict.

        Returns dict with: spot, flip, top_pos_strike, top_neg_strike, net_total.

        When ``view == "dex"``, three additional 0-DTE pressure fields are
        included (sourced from ``data`` via .get, defaulting to None):
        ``net_delta_0dte``, ``projected_net_delta_close``, ``hedge_pressure``.
        For the default view ("gex") and for "charm", the returned dict shape
        is unchanged for full back-compat.
        """
        spot = data.get("spot", 0.0)
        gex = data.get("gex", {})
        if not gex:
            result = {"spot": spot, "flip": None,
                      "top_pos_strike": None, "top_neg_strike": None,
                      "net_total": 0.0}
            if view == "dex":
                result["net_delta_0dte"] = data.get("net_delta_0dte")
                result["projected_net_delta_close"] = data.get("projected_net_delta_close")
                result["hedge_pressure"] = data.get("hedge_pressure")
                result["projected_flip"] = None      # no grid -> no crossing
            return result

        net_total = sum(v["net"] for v in gex.values())

        pos_items = [(s, v["net"]) for s, v in gex.items() if v["net"] > 0]
        neg_items = [(s, v["net"]) for s, v in gex.items() if v["net"] < 0]
        top_pos = max(pos_items, key=lambda x: x[1])[0] if pos_items else None
        top_neg = min(neg_items, key=lambda x: x[1])[0] if neg_items else None

        # Flip point: linear interpolation where net crosses zero near spot.
        # Collect all crossings within ±3% band, then pick nearest to spot.
        #
        # The comparison is STRICT (`< 0`, not `<= 0`) since 2026-08-19: a strike
        # whose net GEX is exactly zero is the ABSENCE of data, not a level, and
        # interpolating a "crossing" onto the boundary of a dead run invents a
        # structural feature out of an untraded strike.
        #
        # This is not hypothetical. Index chains list far more strikes than
        # trade: measured that day, $NDX carried ~135 zero-net strikes and $SPX
        # ~45 inside this ±3% band, while SPY and QQQ carried NONE. Under the old
        # non-strict test that manufactured 8.9 of $NDX's 23.6 candidates per
        # snapshot -- and since the selection below picks the crossing NEAREST
        # SPOT, an inflated candidate set degenerates into "report a level near
        # spot". That is why the index flip tracked spot (corr +0.85/+0.97) while
        # the ETFs did not (-0.10/-0.47), and why the above/below bit every
        # downstream consumer reads changed 31% of minutes for $NDX vs 1% for SPY.
        #
        # The filter is a no-op for SPY/QQQ (no dead strikes), which is what makes
        # it safe to ship alone. The selection rule is a SEPARATE question and is
        # deliberately unchanged -- every alternative tested made the ETFs worse.
        # docs/plans/2026-08-19-gamma-flip-spot-tracking-design.md
        strikes = sorted(gex.keys())
        flip = None
        candidates = []
        for i in range(len(strikes) - 1):
            s1, s2 = strikes[i], strikes[i + 1]
            v1, v2 = gex[s1]["net"], gex[s2]["net"]
            if v1 * v2 < 0 and (v2 - v1) != 0:
                if abs(s1 - spot) <= spot * 0.03 or abs(s2 - spot) <= spot * 0.03:
                    # The sign must HOLD either side, or this is oscillation
                    # rather than a regime boundary. Without it, "nearest to
                    # spot" below picks whichever wobble happens to sit closest
                    # to price - which is why the index flip tracked spot.
                    if not _flip_sign_persists(strikes, gex, i):
                        continue
                    interp = s1 + (s2 - s1) * (-v1) / (v2 - v1)
                    candidates.append(round(interp, 2))
        if candidates:
            flip = min(candidates, key=lambda x: abs(x - spot))

        result = {"spot": spot, "flip": flip,
                  "top_pos_strike": top_pos, "top_neg_strike": top_neg,
                  "net_total": net_total}
        if view == "dex":
            result["net_delta_0dte"] = data.get("net_delta_0dte")
            result["projected_net_delta_close"] = data.get("projected_net_delta_close")
            result["hedge_pressure"] = data.get("hedge_pressure")
            result["projected_flip"] = compute_projected_flip(data, spot)
        return result

    # ── Internal helpers ──

    @staticmethod
    def _find_nearest_exp_key(exp_map, today):
        """Find the nearest expiration key, preferring today (0-DTE).

        Handles standard format 'YYYY-MM-DD:DTE'.
        SPX has daily expirations so 0-DTE always exists.
        VIX only has Wed/Tue expirations — returns the nearest future exp.

        Returns (key, dte) tuple or (None, None).
        """
        if not exp_map:
            return None, None

        # First pass: exact match on today's date
        for key in exp_map:
            parts = key.split(":")
            if parts[0] == today:
                return key, 0

        # Second pass: find the key with the smallest non-negative DTE
        best_key, best_dte = None, float("inf")
        for key in exp_map:
            parts = key.split(":")
            if len(parts) >= 2:
                try:
                    dte = int(float(parts[1]))
                    if 0 <= dte < best_dte:
                        best_key, best_dte = key, dte
                except (ValueError, IndexError):
                    pass

        if best_key is not None:
            return best_key, best_dte
        return None, None

    @staticmethod
    def _get_atm_iv(strikes_data, spot):
        """Get IV of the strike closest to spot.

        strikes_data: {strike_str: [contract_dicts]}
        Returns IV as percentage (e.g. 25.5) or None.
        """
        if not strikes_data or spot <= 0:
            return None

        best_strike = None
        best_dist = float("inf")
        for sk in strikes_data:
            k = float(sk)
            d = abs(k - spot)
            if d < best_dist:
                best_dist = d
                best_strike = sk

        if best_strike is None:
            return None

        ivs = []
        for c in strikes_data[best_strike]:
            iv = c.get("volatility")
            if iv and iv > 0:
                ivs.append(iv)

        return sum(ivs) / len(ivs) if ivs else None


def get_gex_walls(gex_data, top_n=5):
    """Extract the top N GEX wall strikes from a GammaEngine result."""
    if not gex_data or "gex" not in gex_data:
        return []
    gex = gex_data["gex"]
    if not gex:
        return []
    sorted_strikes = sorted(gex.keys(), key=lambda s: abs(gex[s].get("net", 0)), reverse=True)
    return sorted_strikes[:top_n]


def calc_dex_from_chain(chain, use_volume=False):
    """Compute Delta Exposure (DEX) by strike from an option chain.

    DEX Formula: delta * weight * 100 * spot
        weight = open interest (default) or total volume
        Calls contribute positive values, puts negative (via delta's sign).

    Returns dict: {"spot": float, "dex": {strike: {"call", "put", "net"}}, "strike_count": int}
    or None on failure.
    """
    if not chain:
        return None

    spot = chain.get("underlyingPrice", 0)
    if spot <= 0:
        return None

    call_map = chain.get("callExpDateMap", {})
    put_map = chain.get("putExpDateMap", {})

    today = datetime.now(TZ).strftime("%Y-%m-%d")

    # Reuse GammaEngine's expiration picker
    engine = GammaEngine()
    call_exp_key, _ = engine._find_nearest_exp_key(call_map, today)
    put_exp_key, _ = engine._find_nearest_exp_key(put_map, today)

    if not call_exp_key and not put_exp_key:
        return None

    dex = {}
    weight_field = "totalVolume" if use_volume else "openInterest"

    if call_exp_key:
        for strike_str, contracts in call_map.get(call_exp_key, {}).items():
            strike = float(strike_str)
            for c in contracts:
                delta = c.get("delta") or 0
                weight = c.get(weight_field) or 0
                val = delta * weight * 100 * spot
                if strike not in dex:
                    dex[strike] = {"call": 0.0, "put": 0.0, "net": 0.0}
                dex[strike]["call"] += val

    if put_exp_key:
        for strike_str, contracts in put_map.get(put_exp_key, {}).items():
            strike = float(strike_str)
            for c in contracts:
                delta = c.get("delta") or 0
                weight = c.get(weight_field) or 0
                val = delta * weight * 100 * spot
                if strike not in dex:
                    dex[strike] = {"call": 0.0, "put": 0.0, "net": 0.0}
                dex[strike]["put"] += val

    for strike in dex:
        dex[strike]["net"] = dex[strike]["call"] + dex[strike]["put"]

    return {"spot": spot, "dex": dex, "strike_count": len(dex)}


def get_dex_walls(dex_data, top_n=5):
    """Extract the top N DEX wall strikes by absolute net DEX magnitude."""
    if not dex_data or "dex" not in dex_data:
        return []
    dex = dex_data["dex"]
    if not dex:
        return []
    sorted_strikes = sorted(dex.keys(), key=lambda s: abs(dex[s].get("net", 0)), reverse=True)
    return sorted_strikes[:top_n]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Directional call/put walls (FlashAlpha quick win #2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_directional_walls(gex_data, spot, max_pct=None):
    """Call wall (above spot) and put wall (below spot) by GEX magnitude.

    Call GEX is stored positive, put GEX negative. The call wall is the
    largest call-side strike above spot; the put wall is the largest put-side
    strike below spot (most-negative ``put`` entry). Either side is ``None``
    when no strike sits on the correct side of spot.

    ``max_pct`` bounds the search to strikes within that percentage of spot.
    Default ``None`` keeps the historical behaviour for every existing caller.

    WHY THE BOUND EXISTS: this picks the EXTREME strike on each side with no
    proximity constraint, which is correct on a full grid and badly wrong on a
    thin or stale one. Observed in production — a put wall of 14,000 against a
    spot of 29,722, because a deep tail strike carried the largest put entry
    once the near-the-money rows dropped out. A "wall" 53% away from spot is
    not a dealer barrier, it is an artifact, and it is indistinguishable from
    a real level by the time it reaches a chart.

    Returns ``{"call_wall": strike|None, "put_wall": strike|None}``.
    """
    out = {"call_wall": None, "put_wall": None}
    if not gex_data or spot is None or spot <= 0:
        return out
    grid = gex_data.get("gex", {})
    if not grid:
        return out

    def near(strike):
        if max_pct is None:
            return True
        return abs(strike - spot) / spot * 100.0 <= max_pct

    above = [(s, v.get("call", 0.0))
             for s, v in grid.items() if s > spot and near(s)]
    below = [(s, v.get("put", 0.0))
             for s, v in grid.items() if s < spot and near(s)]
    if above:
        out["call_wall"] = max(above, key=lambda sv: sv[1])[0]
    if below:
        # put GEX is negative — most-negative = largest put exposure.
        out["put_wall"] = min(below, key=lambda sv: sv[1])[0]
    return out


def get_oi_walls(chain, spot):
    """Call wall (above spot) and put wall (below spot) by open interest.

    FlashAlpha's literal definition — largest call OI above spot, largest put
    OI below spot, on the nearest expiration. Independent of gamma/IV quality.

    Returns ``{"call_wall": strike|None, "put_wall": strike|None}``.
    """
    out = {"call_wall": None, "put_wall": None}
    if not chain or spot is None or spot <= 0:
        return out
    call_map = chain.get("callExpDateMap", {})
    put_map = chain.get("putExpDateMap", {})
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    call_exp_key, _ = GammaEngine._find_nearest_exp_key(call_map, today)
    put_exp_key, _ = GammaEngine._find_nearest_exp_key(put_map, today)

    def _oi_by_strike(exp_map, exp_key):
        result = {}
        if not exp_key:
            return result
        for strike_str, contracts in exp_map.get(exp_key, {}).items():
            strike = float(strike_str)
            result[strike] = result.get(strike, 0) + sum(
                (c.get("openInterest") or 0) for c in contracts)
        return result

    call_oi = _oi_by_strike(call_map, call_exp_key)
    put_oi = _oi_by_strike(put_map, put_exp_key)
    above = [(s, oi) for s, oi in call_oi.items() if s > spot]
    below = [(s, oi) for s, oi in put_oi.items() if s < spot]
    if above:
        out["call_wall"] = max(above, key=lambda sv: sv[1])[0]
    if below:
        out["put_wall"] = max(below, key=lambda sv: sv[1])[0]
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chain-native put/call ratios (FlashAlpha quick win #3)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def calc_pc_ratios(chain):
    """Per-symbol put/call OI and volume ratios from the nearest expiration.

    ``pc_oi = Σ put_OI / Σ call_OI``; ``pc_volume = Σ put_vol / Σ call_vol``.
    Each is ``None`` when its denominator is 0. Distinct from the broad-market
    CPCE index — this is the scanned symbol's own positioning.

    Returns ``{"pc_oi": float|None, "pc_volume": float|None}``.
    """
    out = {"pc_oi": None, "pc_volume": None}
    if not chain:
        return out
    call_map = chain.get("callExpDateMap", {})
    put_map = chain.get("putExpDateMap", {})
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    call_exp_key, _ = GammaEngine._find_nearest_exp_key(call_map, today)
    put_exp_key, _ = GammaEngine._find_nearest_exp_key(put_map, today)

    def _sum(exp_map, exp_key, field):
        total = 0
        if not exp_key:
            return total
        for contracts in exp_map.get(exp_key, {}).values():
            total += sum((c.get(field) or 0) for c in contracts)
        return total

    call_oi = _sum(call_map, call_exp_key, "openInterest")
    put_oi = _sum(put_map, put_exp_key, "openInterest")
    call_vol = _sum(call_map, call_exp_key, "totalVolume")
    put_vol = _sum(put_map, put_exp_key, "totalVolume")

    if call_oi > 0:
        out["pc_oi"] = put_oi / call_oi
    if call_vol > 0:
        out["pc_volume"] = put_vol / call_vol
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Gamma acceleration: 0DTE / 7DTE (FlashAlpha quick win #4)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def calc_gamma_acceleration(chain):
    """Ratio of front-expiry gamma exposure to the ~7DTE baseline.

    ``G(e) = Σ gamma·OI`` over all call+put contracts at expiry ``e`` (the
    ``·100·spot²·0.01`` scaling cancels in the ratio). DTE is parsed from the
    Schwab expiry key (``"YYYY-MM-DD:DTE"``) so the metric is date-independent.

    ``ratio = G(nearest) / G(closest-to-7DTE)``. ``None`` when there is no
    distinct second expiry or the denominator is 0.

    Returns ``{"ratio": float|None, "dte_near": int|None, "dte_far": int|None}``.
    """
    none = {"ratio": None, "dte_near": None, "dte_far": None}
    if not chain:
        return none
    call_map = chain.get("callExpDateMap", {})
    put_map = chain.get("putExpDateMap", {})

    # Sum gamma·OI per DTE across both call and put maps.
    g_by_dte = {}
    for exp_map in (call_map, put_map):
        for key, strikes in exp_map.items():
            parts = key.split(":")
            if len(parts) < 2:
                continue
            try:
                dte = int(float(parts[1]))
            except (ValueError, IndexError):
                continue
            total = 0.0
            for contracts in strikes.values():
                for c in contracts:
                    gamma = c.get("gamma") or 0.0
                    oi = c.get("openInterest") or 0
                    total += gamma * oi
            g_by_dte[dte] = g_by_dte.get(dte, 0.0) + total

    if len(g_by_dte) < 2:
        return none

    dtes = sorted(g_by_dte)
    dte_near = dtes[0]
    # 7DTE baseline: among the remaining expiries, the DTE closest to 7.
    far_candidates = [d for d in dtes if d != dte_near]
    dte_far = min(far_candidates, key=lambda d: abs(d - 7))

    g_near = g_by_dte[dte_near]
    g_far = g_by_dte[dte_far]
    if g_far == 0:
        return {"ratio": None, "dte_near": dte_near, "dte_far": dte_far}
    return {"ratio": g_near / g_far, "dte_near": dte_near, "dte_far": dte_far}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dealer hedging shares per 1% move (FlashAlpha quick win #6)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def dealer_hedge_shares(net_total_gex, spot):
    """Shares dealers must trade per 1% underlying move = net GEX / spot.

    The engine's net GEX is already dollars of delta change per 1% move (see
    ``calc_from_chain``), so dividing by spot yields shares. Positive =
    long-gamma (buy dips / sell rallies, stabilising); negative = short-gamma
    (sell rallies / buy dips, destabilising). ``None`` when ``spot<=0``.
    """
    if not spot or spot <= 0 or net_total_gex is None:
        return None
    return net_total_gex / spot


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# OI concentration / Herfindahl index (FlashAlpha quick win #5)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def calc_oi_concentration(chain):
    """Herfindahl–Hirschman Index of open-interest concentration.

    Combines call+put OI per strike on the nearest expiration into ``oi_i``,
    then ``HHI = Σ (oi_i / Σoi)²``. Ranges (0, 1]: → 1 = all OI on one strike
    (crowded/pinnable); → 1/N = evenly spread. ``None`` when total OI is 0.

    Returns ``{"hhi": float|None, "n_strikes": int}``.
    """
    if not chain:
        return {"hhi": None, "n_strikes": 0}
    call_map = chain.get("callExpDateMap", {})
    put_map = chain.get("putExpDateMap", {})
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    call_exp_key, _ = GammaEngine._find_nearest_exp_key(call_map, today)
    put_exp_key, _ = GammaEngine._find_nearest_exp_key(put_map, today)

    oi_by_strike = {}
    for exp_map, exp_key in ((call_map, call_exp_key), (put_map, put_exp_key)):
        if not exp_key:
            continue
        for strike_str, contracts in exp_map.get(exp_key, {}).items():
            strike = float(strike_str)
            oi_by_strike[strike] = oi_by_strike.get(strike, 0) + sum(
                (c.get("openInterest") or 0) for c in contracts)

    oi_by_strike = {s: oi for s, oi in oi_by_strike.items() if oi > 0}
    total = sum(oi_by_strike.values())
    if total <= 0:
        return {"hhi": None, "n_strikes": 0}
    hhi = sum((oi / total) ** 2 for oi in oi_by_strike.values())
    return {"hhi": hhi, "n_strikes": len(oi_by_strike)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Max pain / pin risk / zero-DTE magnet (FlashAlpha quick win #1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def calc_max_pain_from_chain(chain):
    """Compute max pain (min-loss strike) from an option chain.

    For each candidate settlement strike K on the nearest expiration, total
    option-writer payout is::

        pain(K) = Σ_call call_OI[Kc]·max(K − Kc, 0)
                + Σ_put  put_OI[Kp]·max(Kp − K, 0)

    Max pain is the strike that minimises ``pain(K)``.

    Returns ``{"spot", "exp_key", "max_pain", "pain_curve": {strike: loss},
    "total_call_oi", "total_put_oi"}`` or ``None`` on an empty/invalid chain.
    """
    if not chain:
        return None
    spot = chain.get("underlyingPrice", 0)
    if spot <= 0:
        return None

    call_map = chain.get("callExpDateMap", {})
    put_map = chain.get("putExpDateMap", {})
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    call_exp_key, _ = GammaEngine._find_nearest_exp_key(call_map, today)
    put_exp_key, _ = GammaEngine._find_nearest_exp_key(put_map, today)
    if not call_exp_key and not put_exp_key:
        return None

    def _oi_by_strike(exp_map, exp_key):
        out = {}
        if not exp_key:
            return out
        for strike_str, contracts in exp_map.get(exp_key, {}).items():
            strike = float(strike_str)
            oi = sum((c.get("openInterest") or 0) for c in contracts)
            out[strike] = out.get(strike, 0) + oi
        return out

    call_oi = _oi_by_strike(call_map, call_exp_key)
    put_oi = _oi_by_strike(put_map, put_exp_key)

    candidate_strikes = sorted(set(call_oi) | set(put_oi))
    if not candidate_strikes:
        return None

    pain_curve = {}
    for K in candidate_strikes:
        call_pain = sum(oi * max(K - Kc, 0.0) for Kc, oi in call_oi.items())
        put_pain = sum(oi * max(Kp - K, 0.0) for Kp, oi in put_oi.items())
        pain_curve[K] = call_pain + put_pain

    max_pain = min(candidate_strikes, key=lambda K: pain_curve[K])
    return {
        "spot": spot,
        "exp_key": call_exp_key or put_exp_key,
        "max_pain": max_pain,
        "pain_curve": pain_curve,
        "total_call_oi": sum(call_oi.values()),
        "total_put_oi": sum(put_oi.values()),
    }


def pin_risk(spot, max_pain, expected_move):
    """0–1 pin-risk score: 1.0 when spot sits on max pain, 0 at ≥1 EM away.

    Returns ``None`` when the expected move is unavailable or non-positive.
    """
    if not expected_move or expected_move <= 0 or spot is None or max_pain is None:
        return None
    return max(0.0, 1.0 - abs(spot - max_pain) / expected_move)


def zero_dte_magnet(spot, max_pain, key_gamma_strike, band_pct=0.0015):
    """Composite magnet from max pain + the key-gamma strike.

    When the two strikes agree within ``band_pct`` of spot, returns a single
    ``level`` (their midpoint) with ``agree=True`` and higher ``confidence``.
    Otherwise returns both strikes with ``agree=False``. Tolerates ``None``
    inputs by falling back to whichever strike is available.
    """
    result = {
        "level": None, "agree": False, "confidence": 0.0,
        "max_pain": max_pain, "key_gamma": key_gamma_strike,
    }
    if max_pain is None and key_gamma_strike is None:
        return result
    if max_pain is None or key_gamma_strike is None:
        result["level"] = max_pain if max_pain is not None else key_gamma_strike
        result["confidence"] = 0.4
        return result

    band = (band_pct * spot) if spot else 0.0
    if abs(max_pain - key_gamma_strike) <= band:
        result["level"] = (max_pain + key_gamma_strike) / 2.0
        result["agree"] = True
        result["confidence"] = 0.8
    else:
        # Pick the strike nearest spot as the headline level, but flag disagreement.
        result["level"] = min(
            (max_pain, key_gamma_strike),
            key=lambda s: abs(s - spot) if spot else 0.0,
        )
        result["confidence"] = 0.45
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pure helpers feeding the AI Analyze prompt
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def top_strikes_with_tail(grid, n=20):
    """Return (top_positive, top_negative, tail_summary) for a GEX/Charm/DEX grid.

    grid: {strike: {"call":..., "put":..., "net":...}}

    top_positive: list[{"strike", "value"}] sorted by net descending, length<=n
    top_negative: list[{"strike", "value"}] sorted by net ascending, length<=n
    tail_summary: {"count_pos","sum_pos","count_neg","sum_neg"} aggregating
                  strikes beyond the top-n on each side. Captures the long-tail
                  shape that vision-only inspection of the chart provides.
    """
    pos_pairs = sorted(
        ((s, g["net"]) for s, g in grid.items() if g["net"] > 0),
        key=lambda x: x[1], reverse=True,
    )
    neg_pairs = sorted(
        ((s, g["net"]) for s, g in grid.items() if g["net"] < 0),
        key=lambda x: x[1],
    )
    top_pos = [{"strike": s, "value": v} for s, v in pos_pairs[:n]]
    top_neg = [{"strike": s, "value": v} for s, v in neg_pairs[:n]]
    tail_pos = pos_pairs[n:]
    tail_neg = neg_pairs[n:]
    tail = {
        "count_pos": len(tail_pos),
        "sum_pos": sum(v for _, v in tail_pos),
        "count_neg": len(tail_neg),
        "sum_neg": sum(v for _, v in tail_neg),
    }
    return top_pos, top_neg, tail


def delta_change_for_strikes(strikes, current_grid, prev_grid):
    """Return {strike: current_net - prev_net} for the given strikes.

    Missing strikes in prev_grid are treated as 0 (newly emerged exposure).
    Returns {} if prev_grid is None — caller should suppress the section.
    """
    if prev_grid is None:
        return {}
    out = {}
    for s in strikes:
        curr = current_grid.get(s, {}).get("net", 0) if current_grid else 0
        prev = prev_grid.get(s, {}).get("net", 0)
        out[s] = curr - prev
    return out


def value_at_open_for_strikes(strikes, open_grid):
    """Return {strike: open_net or None} for the given strikes.

    Missing strikes (e.g. didn't exist at the open) → None so the prompt can
    distinguish "absent at open" from "zero at open".
    Returns {} if open_grid is None.
    """
    if open_grid is None:
        return {}
    out = {}
    for s in strikes:
        if s in open_grid:
            out[s] = open_grid[s].get("net", 0)
        else:
            out[s] = None
    return out


def compute_projected_flip(data, spot):
    """Return the strike where the DEX curve crosses zero once each strike's own
    0-DTE charm drift is applied — i.e. the projected EOD delta-flip.

    Uses ``hedge_drift_by_strike`` (see ``project_0dte_drift_by_strike``). It does
    NOT fall back to spreading the total ``hedge_pressure`` evenly across the chain:
    that is what the old implementation did, and on live $SPX data it erased 56 of
    57 negative strikes and returned a crossing ~1,800 points past spot. Without a
    per-strike drift map there is no honest projection, so this returns None —
    which is the normal case, since only symbols whose nearest expiry is TODAY have
    a 0-DTE book at all.

    Returns the crossing closest to ``spot``, or None if no crossing / no inputs.
    """
    grid = data.get("gex") or {} if data else {}
    drift = (data.get("hedge_drift_by_strike") or {}) if data else {}
    if not grid or not drift:
        return None
    shifted = {k: v["net"] + drift.get(k, 0.0) for k, v in grid.items()}
    strikes = sorted(shifted.keys())
    crossings = []
    for i in range(len(strikes) - 1):
        s1, s2 = strikes[i], strikes[i + 1]
        v1, v2 = shifted[s1], shifted[s2]
        if v1 == 0:
            crossings.append(s1)
            continue
        if v1 * v2 < 0:
            t = v1 / (v1 - v2)
            crossings.append(s1 + t * (s2 - s1))
    if not crossings:
        return None
    return min(crossings, key=lambda s: abs(s - spot))


# Spec for the v1 DEX 0-DTE pressure panel. ``fields`` is a list of
# ``(output_key, snapshot_key)`` pairs describing the direct value lookups
# the renderer performs. ``required_key`` is the snapshot field whose
# presence (non-None) gates the panel — when missing, the renderer returns
# None. ``pressure_key`` names the snapshot field used to derive the
# ``hedge_direction`` label ("buy"/"sell"/"neutral"/None). Future panels
# (e.g. Drift Pressure) supply their own spec to reuse this renderer.
# (Titles are rendered by the caller — not part of the spec.)
_DEX_PRESSURE_SPEC = {
    "required_key": "net_delta_0dte",
    "pressure_key": "hedge_pressure",
    "fields": [
        ("delta_now", "net_delta_0dte"),
        ("projected_close", "projected_net_delta_close"),
        ("hedge_pressure", "hedge_pressure"),
    ],
}


def format_pressure_panel(dex_data, spot, spec=None):
    """Return the 0-DTE pressure panel as a dict for the prompt, or None.

    Mirrors the side-panel rendering: now / projected-close / hedge_pressure
    plus the projected EOD flip strike. Returns None when there's no 0-DTE
    data (panel hidden in the UI for the same reason).

    When ``spec`` is None, uses ``_DEX_PRESSURE_SPEC`` — preserves v1
    behavior byte-identically. Custom specs let future panels (e.g. Drift
    Pressure) reuse this renderer without subclassing.
    """
    if spec is None:
        spec = _DEX_PRESSURE_SPEC
    if not dex_data:
        return None
    if dex_data.get(spec["required_key"]) is None:
        return None
    out = {out_key: dex_data.get(src_key) for out_key, src_key in spec["fields"]}
    hedge = dex_data.get(spec["pressure_key"])
    if hedge is None:
        direction = None
    elif hedge > 0:
        direction = "buy"
    elif hedge < 0:
        direction = "sell"
    else:
        direction = "neutral"
    out["hedge_direction"] = direction
    out["projected_flip"] = compute_projected_flip(dex_data, spot)
    return out


_INTERNAL_SYMBOLS = {"cpce": "$CPCE", "ad": "$ADD", "skew": "SKEW"}


_REGIME_NOTES = {
    ("VANNA_DOMINANT", "down"): "vol crush tailwind",
    ("VANNA_DOMINANT", "up"):   "vol expansion headwind",
    ("VANNA_DOMINANT", None):   "vol-driven flow",
    ("CHARM_DOMINANT", "down"): "time-decay drift dominant",
    ("CHARM_DOMINANT", "up"):   "time-decay drift dominant",
    ("CHARM_DOMINANT", None):   "time-decay drift dominant",
    ("BALANCED", "down"):       "both flows in play",
    ("BALANCED", "up"):         "both flows in play",
    ("BALANCED", None):         "both flows in play",
}


# ── Explain popup: plain-English view narration ─────────────────────────

_EXPLAIN_WHAT = {
    "gex": [
        "Net dealer gamma across the chain. It tells you HOW price moves "
        "(dampened vs amplified), not WHICH way.",
        "",
        "• Positive (dealers long gamma): they buy dips and sell rips — "
        "mean-reverting, dampened vol. Good for premium selling / condors.",
        "• Negative (dealers short gamma): they sell dips and buy rips — "
        "trending, amplified vol. Favors breakouts / long straddles.",
        "",
        "Sign matters more than size, and regimes firm up away from the flip. "
        "Ignore GEX around CPI / FOMC / earnings — event flow overwhelms it.",
    ],
    "charm": [
        "Time-decay hedging flow: delta that bleeds out of options as the "
        "session decays — a slow drift force, strongest 0-DTE.",
        "",
        "• Positive charm: dealers accumulate long delta into the close → "
        "buying / upward drift.",
        "• Negative charm: dealers distribute delta → selling / downward drift.",
        "",
        "Strongest in the last 60 minutes and on Thu–Fri of OPEX week; it's "
        "mostly noise midweek.",
    ],
    "dex": [
        "Net directional inventory dealers must hedge. Unlike GEX, this tells "
        "you WHERE dealers are positioned directionally.",
        "",
        "• Positive DEX: dealers long delta (from hedging puts) → latent "
        "supply; a bearish headwind into expiry.",
        "• Negative DEX: dealers short delta (from calls) → latent demand; "
        "bullish into expiry.",
        "",
        "Negative GEX + large positive DEX = an amplifying regime with shares "
        "to sell — can accelerate selloffs. Ignore across catalysts.",
    ],
    "vanna": [
        "How dealer delta shifts when volatility (VIX) moves — the vol-driven "
        "drift bias, paired with charm.",
        "",
        "• Positive VEX: IV falling pushes dealer delta long → buying (the "
        "'vanna rally'); IV rising → selling.",
        "• Negative VEX: a vol spike forces dealers to chase — selling into "
        "declines, buying into rallies (waterfall risk).",
        "",
        "Matters most around IV shocks (FOMC / CPI / earnings): it times HOW a "
        "vol shock propagates, not which way price goes.",
    ],
    "term": [
        "How gamma stacks across expirations (SPXW only) — near-term walls vs. "
        "longer-dated positioning.",
        "",
        "Near-term walls are the day's rails, but a shift in longer-dated "
        "positioning can overrun them — confirm with the GEX view.",
    ],
}


_EXPLAIN_VIEW_TITLES = {
    "gex": "GAMMA EXPOSURE (GEX)",
    "charm": "CHARM PRESSURE (Δ-decay)",
    "dex": "DELTA EXPOSURE (DEX)",
    "vanna": "VANNA EXPOSURE (VEX)",
}


def build_internals_block(internals):
    """Format the MARKET INTERNALS section. ``internals`` is the dict
    returned by ``_fetch_market_internals`` — each value may be None."""
    lines = ["=== MARKET INTERNALS ==="]

    cpce = internals.get("cpce")
    if cpce is None:
        lines.append("Put/Call (CPCE): fetch failed")
    else:
        hint = ">1.0 = fear, <0.7 = greed"
        lines.append(f"Put/Call ratio (CPCE):  {cpce:.2f}    ({hint})")

    ad = internals.get("ad")
    if ad is None:
        lines.append("NYSE A/D: fetch failed")
    else:
        # $ADD is the daily NYSE advance-decline net. Thresholds picked at
        # roughly half the typical NYSE listed count (~3000): |1500| is a
        # decisive 75/25 split, |500| is near-balanced.
        if ad > 1500:
            hint = "broadly bullish (advancers dominate)"
        elif ad < -1500:
            hint = "broadly bearish (decliners dominate)"
        elif abs(ad) < 500:
            hint = "mixed / balanced"
        else:
            hint = "leaning bullish" if ad > 0 else "leaning bearish"
        lines.append(f"NYSE A/D:               {ad:+d}    ({hint})")

    skew = internals.get("skew")
    if skew is None:
        lines.append("SKEW Index: fetch failed")
    else:
        if skew > 140:
            hint = ">140 = elevated tail risk"
        elif skew < 120:
            hint = "<120 = complacent"
        else:
            hint = "normal range"
        lines.append(f"SKEW Index:             {skew:.1f}   ({hint})")

    return "\n".join(lines)


def build_eod_probabilities(chain, em_upper, em_lower,
                            pos_wall_strike, neg_wall_strike):
    """Estimate end-of-day touch probability for four target strikes.

    Uses the standard approximation: touch_prob ~= min(1.0, 2 * |delta|)
    of the OTM option at the target strike. For upside targets we use the
    OTM call; for downside targets we use the OTM put.

    Returns a dict with keys touch_em_upper, touch_em_lower,
    reach_pos_wall, reach_neg_wall. Each value is a float in [0, 1] or
    None if no contract exists at the target strike.
    """
    call_map = chain.get("callExpDateMap", {})
    put_map = chain.get("putExpDateMap", {})

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    call_exp_key, _ = GammaEngine._find_nearest_exp_key(call_map, today)
    put_exp_key, _ = GammaEngine._find_nearest_exp_key(put_map, today)
    call_exp = call_map[call_exp_key] if call_exp_key else {}
    put_exp = put_map[put_exp_key] if put_exp_key else {}

    def _delta_at(side_exp, strike):
        for key in (f"{strike:.1f}", f"{int(strike)}.0", f"{strike}"):
            if key in side_exp:
                contracts = side_exp[key]
                if contracts:
                    d = contracts[0].get("delta")
                    if d is not None:
                        return d
        return None

    def _prob_from_delta(d):
        if d is None:
            return None
        return min(1.0, 2.0 * abs(d))

    return {
        "touch_em_upper": _prob_from_delta(_delta_at(call_exp, em_upper)),
        "touch_em_lower": _prob_from_delta(_delta_at(put_exp, em_lower)),
        "reach_pos_wall": _prob_from_delta(_delta_at(call_exp, pos_wall_strike)),
        "reach_neg_wall": _prob_from_delta(_delta_at(put_exp, neg_wall_strike)),
    }


def calc_flip_point(gex, spot):
    """Find the strike near spot where net GEX/Charm crosses from positive to negative.

    Pure. Was a ``GammaWindow`` staticmethod, which forced pure engine code
    (``build_analysis_dict``) to reach forward into the Tk GUI class; moved to
    module scope when the window was split out to ``gamma_window_legacy.py``.
    """
    strikes = sorted(gex.keys())
    if len(strikes) < 2:
        return None

    # Look for zero-crossing near spot (within +/-3%)
    lo, hi = spot * 0.97, spot * 1.03
    nearby = [(s, gex[s]["net"]) for s in strikes if lo <= s <= hi]
    if len(nearby) < 2:
        return None

    # Find where sign changes
    for i in range(len(nearby) - 1):
        s1, v1 = nearby[i]
        s2, v2 = nearby[i + 1]
        if v1 * v2 < 0:  # sign change
            # Linear interpolation
            if v2 - v1 != 0:
                flip = s1 + (s2 - s1) * (-v1) / (v2 - v1)
                return round(flip, 1)
            return round((s1 + s2) / 2, 1)

    return None


def build_analysis_dict(
    snapshot,
    view,
    symbol,
    dte,
    expected_move=None,
    grouping=1,
    prev_snapshot=None,
    open_snapshot=None,
    chain=None,
    iv_pctile=None,
    rv_trend=None,
):
    """Build the structured analysis dict that the prompt builder consumes.

    Returns the same shape as GammaWindow._build_analysis_data, but pure —
    no Tk state, no window instance. Used for SPY/QQQ one-shot fetches and
    callable from tests.
    """
    spot = snapshot["spot"]
    gex_raw = snapshot["gex"]
    gex = GammaEngine.group_gex(gex_raw, grouping)

    now = datetime.now(TZ)
    close_hour, close_min = 15, 15
    hours_left = max(0, (close_hour - now.hour) + (close_min - now.minute) / 60.0)

    top_pos, top_neg, tail = top_strikes_with_tail(gex, n=20)

    eod_probs = None
    if chain is not None and expected_move is not None and top_pos and top_neg:
        pos_wall_strike = top_pos[0]["strike"]
        neg_wall_strike = top_neg[0]["strike"]
        em_upper_strike = spot + expected_move
        em_lower_strike = spot - expected_move
        eod_probs = build_eod_probabilities(
            chain, em_upper_strike, em_lower_strike,
            pos_wall_strike, neg_wall_strike)

    prev_grid = (
        GammaEngine.group_gex(prev_snapshot["gex"], grouping)
        if prev_snapshot and prev_snapshot.get("gex") else None
    )
    open_grid = (
        GammaEngine.group_gex(open_snapshot["gex"], grouping)
        if open_snapshot and open_snapshot.get("gex") else None
    )
    top_strikes = [item["strike"] for item in top_pos] + \
                  [item["strike"] for item in top_neg]
    delta_change = delta_change_for_strikes(top_strikes, gex, prev_grid)
    value_at_open = value_at_open_for_strikes(top_strikes, open_grid)

    pressure_panel = format_pressure_panel(snapshot, spot) if view == "dex" else None

    # Flip point: where net crosses zero near spot.
    flip_point = calc_flip_point(gex, spot)

    zones = {"above_0_2pct": 0, "below_0_2pct": 0, "below_2_5pct": 0}
    for s, vals in gex.items():
        net = vals["net"]
        if s > spot and s <= spot * 1.02:
            zones["above_0_2pct"] += net
        elif s < spot and s >= spot * 0.98:
            zones["below_0_2pct"] += net
        elif s < spot * 0.98 and s >= spot * 0.95:
            zones["below_2_5pct"] += net

    near_strikes = sorted(gex.keys(), key=lambda s: abs(s - spot))[:5]
    atm_breakdown = []
    for s in sorted(near_strikes):
        d = gex[s]
        atm_breakdown.append({
            "strike": s, "call": d["call"], "put": d["put"], "net": d["net"]
        })

    # Max pain / pin risk / zero-DTE magnet (FlashAlpha quick win #1).
    # Computed only when the raw chain is available (mirrors eod_probs above).
    max_pain_block = None
    if chain is not None:
        mp = calc_max_pain_from_chain(chain)
        if mp is not None:
            kg = find_key_gamma_strike(gex, spot)
            max_pain_block = {
                "max_pain": mp["max_pain"],
                "pin_risk": pin_risk(spot, mp["max_pain"], expected_move),
                "magnet": zero_dte_magnet(spot, mp["max_pain"], kg),
                "total_call_oi": mp["total_call_oi"],
                "total_put_oi": mp["total_put_oi"],
            }

    # Chain-native put/call ratios (FlashAlpha quick win #3).
    pc_ratios_block = calc_pc_ratios(chain) if chain is not None else \
        {"pc_oi": None, "pc_volume": None}

    # OI concentration / Herfindahl (FlashAlpha quick win #5).
    oi_conc_block = calc_oi_concentration(chain) if chain is not None else \
        {"hhi": None, "n_strikes": 0}

    # Dealer hedging shares per 1% move = net GEX / spot (FlashAlpha quick win #6).
    net_total_gex = sum(v.get("net", 0.0) for v in gex.values())
    hedge_shares_val = dealer_hedge_shares(net_total_gex, spot)

    # Gamma acceleration: 0DTE / 7DTE gamma exposure (FlashAlpha quick win #4).
    gamma_accel_block = calc_gamma_acceleration(chain) if chain is not None else \
        {"ratio": None, "dte_near": None, "dte_far": None}

    # Directional call/put walls — GEX basis always, OI basis when chain given
    # (FlashAlpha quick win #2).
    walls_block = {
        "gex": get_directional_walls({"gex": gex, "spot": spot}, spot),
        "oi": get_oi_walls(chain, spot) if chain is not None
        else {"call_wall": None, "put_wall": None},
    }

    # Dealer Pinch detector (Vanna/Charm exhaustion). Computed only with the raw
    # chain; iv_pctile/rv_trend are optional (fetched on the worker thread) — when
    # absent the detector reports WATCHING rather than arming.
    pinch_block = None
    if chain is not None:
        node = dominant_oi_node(chain).get("node")
        pinch_pin_risk = (pin_risk(spot, node, expected_move)
                          if node is not None else None)
        pinch_block = evaluate_dealer_pinch(
            symbol=symbol, chain=chain, spot=spot, dte=dte,
            iv_pctile=iv_pctile, rv_trend=rv_trend, gex_flip=flip_point,
            pin_risk_score=pinch_pin_risk, forced_hedge_dir=None,
            hours_to_close=round(hours_left, 2),
        )

    view_label = {"gex": "GEX", "charm": "Charm", "dex": "DEX", "vanna": "Vanna"}.get(view, "GEX")
    return {
        "view": view_label,
        "symbol": symbol,
        "spot": spot,
        "dte": dte,
        "expected_move": expected_move,
        "em_upper": round(spot + expected_move, 2) if expected_move else None,
        "em_lower": round(spot - expected_move, 2) if expected_move else None,
        "timestamp": now.strftime("%I:%M %p CT"),
        "hours_to_close": round(hours_left, 2),
        "top_positive": top_pos,
        "top_negative": top_neg,
        "tail_summary": tail,
        "delta_change": delta_change,
        "value_at_open": value_at_open,
        "pressure_panel": pressure_panel,
        "flip_point": flip_point,
        "net_by_zone": zones,
        "atm_breakdown": atm_breakdown,
        "grouping": grouping,
        "eod_probabilities": eod_probs,
        "max_pain": max_pain_block,
        "walls": walls_block,
        "pc_ratios": pc_ratios_block,
        "oi_concentration": oi_conc_block,
        "hedge_shares": hedge_shares_val,
        "gamma_acceleration": gamma_accel_block,
        "dealer_pinch": pinch_block,
    }


def _bundled_fmt_val(v):
    """Compact $-magnitude formatter for bundled-prompt block rendering."""
    av = abs(v)
    if av >= 1e9:
        return f"{v/1e9:+,.1f}B"
    if av >= 1e6:
        return f"{v/1e6:+,.1f}M"
    if av >= 1e3:
        return f"{v/1e3:+,.0f}K"
    return f"{v:+,.0f}"


def _bundled_dealer_positioning_compact(data):
    """One-line condensed dealer-positioning summary (max pain, pin risk,
    walls, P/C OI) for the summary prompt. Returns ``""`` when unavailable."""
    def _strike(s):
        return f"{s:,.0f}" if s is not None else "--"

    parts = []
    mp = data.get("max_pain") or {}
    if mp.get("max_pain") is not None:
        seg = f"Max pain {_strike(mp.get('max_pain'))}"
        if mp.get("pin_risk") is not None:
            seg += f" (pin {mp['pin_risk']:.0%})"
        parts.append(seg)
    walls = data.get("walls") or {}
    gw = walls.get("gex") or {}
    if gw.get("call_wall") is not None or gw.get("put_wall") is not None:
        parts.append(f"walls {_strike(gw.get('call_wall'))}/{_strike(gw.get('put_wall'))}")
    pc = data.get("pc_ratios") or {}
    if pc.get("pc_oi") is not None:
        parts.append(f"P/C OI {pc['pc_oi']:.2f}")
    if not parts:
        return ""
    return "POSITIONING: " + " | ".join(parts)


_INTRADAY_ASK = """\
You are explaining today's market setup to a regular investor — not a
quant. Use plain English throughout. Avoid jargon. When you must use a
technical term, explain it in one short sentence the first time.

Structure your answer in these sections, in order:

1. BIG PICTURE (2-3 sentences)
   What is the market mood right now — greedy, fearful, calm, or
   nervous? Use the Put/Call ratio (CPCE), advancers/decliners, and
   SKEW Index to answer. Translate the indicators into plain words
   ("the fear gauge is elevated", "more stocks are falling than
   rising", etc.).

2. WHY IS THIS HAPPENING
   Two parts, both in plain English:
   (a) MARKET CONTEXT — why is the price action, IV regime, and gamma
       posture showing what they're showing right now? What macro flows,
       dealer hedging, or catalysts explain it?
   (b) SESSION CONTEXT — how did the tape get to where it is today?
       What was the overnight setup, the opening flow, and the path
       since the open? Tell the story of the session so far.

3. WHERE PRICE IS LIKELY TO GO TODAY
   Use the GEX walls and the EOD probabilities to describe today's
   most likely price range, the "magnet" levels price will pull toward,
   and the breakout zones. Phrase probabilities as plain percentages
   ("about a 65% chance of touching X by the close").

4. KEY LEVELS — UP AND DOWN
   List the most important price levels for each of SPX, SPY, and QQQ.
   For each level, say one sentence on what to expect if price gets
   there.

5. WHAT IF
   Walk through 2-3 plausible scenarios for the rest of the session.
   You pick the scenarios (e.g. an upside surprise, a downside flush,
   a sideways grind, a late-day pin, a catalyst). For each, in plain
   English: what does price / IV / GEX do, which levels matter, and
   what should a regular investor watch or do?

6. RED FLAGS / GREEN FLAGS
   Two short lists. RED FLAGS = anything in the data warning of
   trouble (elevated SKEW, extreme put/call, narrow advancers).
   GREEN FLAGS = anything bullish (broad advance/decline, walls
   supporting price, calm sentiment).

Keep each section under 200 words. No more than 6 bullets per section.
Bold the key numbers. The first time you use a technical name (CPCE,
SKEW, GEX wall, etc.), give a one-sentence plain-English explanation.
"""


_PREMARKET_ASK = """\
You are briefing a regular investor before the market opens. Use plain
English throughout. Explain technical terms in one short sentence the
first time.

The data above is from yesterday's close. Today's market hasn't opened
yet, so you also need to consider OVERNIGHT activity (ES, NQ, RTY
futures), macro news, and scheduled events. Look those up yourself
when answering.

Structure your answer in these sections:

1. BIG PICTURE (2-3 sentences)
   What happened overnight? What is the market mood heading into the
   open — risk-on, risk-off, cautious? Use the Put/Call ratio,
   advancers/decliners, and SKEW Index from yesterday's close together
   with what you know about overnight futures.

2. WHY IS THIS HAPPENING
   Two parts, both in plain English:
   (a) MARKET CONTEXT — why does yesterday's close look the way it
       does, and why are overnight futures and IV sitting where they
       are? What dealer positioning, macro flows, or recent catalysts
       set up this opening?
   (b) WHAT GOT US HERE — what story has the tape been telling over
       the last few sessions that frames today's open? Recent
       breakouts, failed tests of key levels, regime shifts in
       volatility, etc.

3. SCHEDULED EVENTS TODAY
   List today's market-moving scheduled events (CPI, FOMC, PPI, jobs
   data, major earnings). Note the time and likely market impact for
   each. If none are scheduled, say so.

4. WHERE PRICE IS LIKELY TO OPEN AND TRADE
   Use yesterday's GEX walls and EOD probabilities to describe the
   likely opening range, magnet levels, and breakout zones. Reference
   how pre-market quotes for SPY/QQQ compare to yesterday's flip and
   walls (numbers above).

5. KEY LEVELS — UP AND DOWN
   For each of SPX, SPY, QQQ, list the most important price levels
   carrying over from yesterday's close. One sentence per level.

6. WHAT IF
   Walk through 2-3 plausible scenarios for today's session. You pick
   the scenarios (gap up, gap down, in-line open then fade, catalyst
   reaction, IV crush, etc.). For each, in plain English: what does
   price / IV / GEX do, which levels matter, and what should a regular
   investor watch or do?

7. RED FLAGS / GREEN FLAGS
   Two short lists. RED FLAGS = warning signs from overnight or
   scheduled events. GREEN FLAGS = anything supportive.

Keep each section under 200 words. Plain English. Explain SKEW, CPCE,
"GEX wall", etc. in one sentence the first time.
"""


_ANALYSIS_REVIEW_ASK = """\

6. ANALYSIS REVIEW
   Look at the intraday path data above (if provided) and compare it to
   the levels and ranges from earlier in the day.

   - Which key levels we identified at the open actually held as
     support or resistance?
   - Which broke?
   - Did price stay inside the morning's expected range, or did it
     break out?
   - In one or two sentences: what was the single most important thing
     the morning setup got right, and what did it miss?

   Keep it honest and in plain English. No score, no grade — just what
   actually happened versus what the structure suggested.
"""


def _bundled_build_summary_block(data, view):
    """Render a compact one-view block for the bundled SUMMARY prompt.

    Compared to `_bundled_build_block`: keeps top 5 strikes (caller pre-trims
    typically already <=5), flip point, and net-by-zone. Drops pressure_panel,
    tail summary, and atm_breakdown to stay 1-page glanceable.
    """
    lines = []
    lines.append(f"TOP+ {view}:")
    for item in data["top_positive"][:5]:
        lines.append(f"  {item['strike']:>8,.0f}: {_bundled_fmt_val(item['value'])}")
    lines.append(f"TOP- {view}:")
    for item in data["top_negative"][:5]:
        lines.append(f"  {item['strike']:>8,.0f}: {_bundled_fmt_val(item['value'])}")
    if data.get("flip_point"):
        lines.append(f"{view} FLIP: ~{data['flip_point']:,.0f}")
    zones = data.get("net_by_zone") or {}
    lines.append(
        f"NET {view} ZONES: "
        f"+0-2% {_bundled_fmt_val(zones.get('above_0_2pct', 0))} | "
        f"-0-2% {_bundled_fmt_val(zones.get('below_0_2pct', 0))} | "
        f"-2-5% {_bundled_fmt_val(zones.get('below_2_5pct', 0))}"
    )
    return "\n".join(lines)


def _per_symbol_summary_section(symbol_label, blocks, premarket):
    """Compact per-symbol section for the bundled summary prompt."""
    gex_data = blocks.get("gex")
    charm_data = blocks.get("charm")
    dex_data = blocks.get("dex")
    vanna_data = blocks.get("vanna")
    header_data = gex_data or charm_data or dex_data or vanna_data
    if header_data is None:
        return None

    spot = header_data["spot"]
    dte = header_data["dte"]
    dte_str = "0-DTE" if dte == 0 else f"{dte}-DTE"

    suffix = " (yesterday's closing gamma profile)" if premarket else ""
    lines = [f"=== {symbol_label}{suffix} ==="]
    lines.append(f"Symbol: {header_data['symbol']} | Spot: {spot:,.2f} | {dte_str}")
    if header_data.get("expected_move"):
        lines.append(
            f"Expected Move: +/-{header_data['expected_move']:,.2f} "
            f"({header_data['em_lower']:,.2f} - {header_data['em_upper']:,.2f})")
    lines.append(
        f"Time: {header_data['timestamp']} | "
        f"Hours to close: {header_data['hours_to_close']}")
    pos_line = _bundled_dealer_positioning_compact(header_data)
    if pos_line:
        lines.append(pos_line)

    sub = []
    if gex_data:
        sub.append("--- GEX ---\n" + _bundled_build_summary_block(gex_data, "GEX"))
    if charm_data:
        sub.append("--- Charm ---\n" + _bundled_build_summary_block(charm_data, "Charm"))
    if dex_data:
        sub.append("--- DEX ---\n" + _bundled_build_summary_block(dex_data, "DEX"))
    if vanna_data:
        sub.append("--- Vanna ---\n" + _bundled_build_summary_block(vanna_data, "Vanna"))

    return "\n".join(lines) + "\n\n" + "\n\n".join(sub)


# The closing ask. Deliberately ONE line: both production consumers
# (`options_svc.compute.gamma_analyze` / `eod_briefing`) call with
# `tool_choice` forcing `submit_analysis` / `submit_eod`, so the output
# contract is the TOOL SCHEMA and the caller's system prompt — the model never
# free-writes here. This used to carry a numbered free-text structure
# ("1. BIG PICTURE ... 4. WHAT IF") and a "Cap the whole reply at 350 words"
# ceiling, both unreachable under a forced tool and both billed on every call.
_INTRADAY_SUMMARY_ASK = (
    "Write the reader's cross-index read of this session from the data above."
)

_PREMARKET_SUMMARY_ASK = (
    "Write the reader's read of the open, carrying these closing levels into "
    "today's session; consider overnight futures action and today's scheduled "
    "events."
)


def build_summary_prompt_bundled(spx_blocks, spy_blocks, qqq_blocks,
                                 *, premarket=False, internals=None):
    """Build a multi-symbol bundled SUMMARY prompt covering SPX, SPY, and QQQ.

    Concise 1-page-glance counterpart to `build_combined_prompt_bundled`.
    Each *_blocks argument is a dict {"gex": ..., "charm": ..., "dex": ...} or
    None. Failed-fetch symbols emit a one-line note instead of a full section.

    Raises ValueError if all three symbols are None.
    """
    if spx_blocks is None and spy_blocks is None and qqq_blocks is None:
        raise ValueError("build_summary_prompt_bundled: all three symbol bundles are None")

    parts = []
    anchor = spx_blocks or spy_blocks or qqq_blocks
    anchor_view = (anchor.get("gex") or anchor.get("charm") or anchor.get("dex") or anchor.get("vanna"))
    dte = anchor_view.get("dte", 0) if anchor_view else 0
    dte_str = "0-DTE" if dte == 0 else f"{dte}-DTE"
    # Describes what the DATA is (which the model cannot know otherwise). The
    # role line that used to open each of these ("You are an options trader's
    # ... briefer") is gone: the caller's system prompt owns the role, and a
    # second, differently-worded identity in the user turn only competes with it.
    if premarket:
        intro = ("Yesterday's CLOSING gamma profile for SPX, SPY and QQQ, carried "
                 "into today's open. Overnight futures action and today's scheduled "
                 "events are not in the data below.")
    else:
        intro = (f"Structured intraday data for SPX, SPY and QQQ {dte_str} options.")
    parts.append(intro)
    parts.append("=== STRUCTURED DATA ===")

    if internals:
        parts.append(build_internals_block(internals))

    for label, blocks in (("SPX", spx_blocks), ("SPY", spy_blocks), ("QQQ", qqq_blocks)):
        if blocks is None:
            parts.append(f"=== {label} ===\n{label}: fetch failed - section omitted")
            continue
        section = _per_symbol_summary_section(label, blocks, premarket)
        if section is None:
            parts.append(f"=== {label} ===\n{label}: fetch failed - section omitted")
        else:
            parts.append(section)

    parts.append(_PREMARKET_SUMMARY_ASK if premarket else _INTRADAY_SUMMARY_ASK)

    return "\n\n".join(parts)


_VALID_SLOTS = {"0820", "0845", "1000", "1300", "1500", "manual"}


_RETROSPECTIVE_SLOTS = ["0820", "0845", "1000", "1300"]


_FIRE_TIME_TO_SLOT = {
    (8, 19): "0820",
    (8, 44): "0845",
    (9, 59): "1000",
    (12, 59): "1300",
    (14, 59): "1500",
}
