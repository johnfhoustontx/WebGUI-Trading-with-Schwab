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

from options_calculator import bs_charm, bs_delta, bs_gamma, bs_vanna
from dealer_pinch import evaluate_dealer_pinch, dominant_oi_node
from iv_percentile import percentile_rank, realized_vol_trend
import theme

# NOTE: matplotlib is deliberately NOT imported at module scope. The only
# plotting helper left here (``draw_term_heatmap``) imports what it needs
# locally, so headless importers of this engine never load a GUI toolkit and
# never have a backend forced on them. The legacy Tk window that did need it at
# module scope now lives in ``gamma_window_legacy.py``.
import numpy as np

log = logging.getLogger("scanner")
TZ = ZoneInfo("America/Chicago")
FONT = "Segoe UI"
FONT_MONO = "Consolas"

# ── Chrome constants — driven by Windows light/dark theme via theme.chrome().
# Backward-compat module-level aliases so existing references keep working.
# Each is evaluated once at module import (theme detection is cached, so
# subsequent reads in long-running processes return the same dict).
_chrome = theme.chrome()
BG_MAIN    = _chrome["bg"]
BG_PANEL   = _chrome["bg_panel"]
BG_INPUT   = _chrome["bg_input"]
FG_PRIMARY = _chrome["fg"]
FG_DIM     = _chrome["fg_dim"]

# Trading semantic constants — driven by theme.trading(). Backward-compat
# module-level aliases so existing references keep working. Evaluated once
# at module import (theme detection is cached).
_trading = theme.trading()
CYAN       = _trading["gex_pos"]    # GEX+/Top+ — royal blue
AMBER      = _trading["gex_neg"]    # Negative/Top- — firebrick
PINK       = _trading["charm_pos"]  # Charm+ — medium purple
GOLD       = _trading["dex_pos"]    # DEX+/Ghost — gold
EM_COLOR   = _trading["em_line"]    # Expected-move band — light sky blue
GRAY       = "#888888"              # Previous-snapshot comparison dots — neutral
WHITE      = "#ffffff"              # literal — not themed


def extrapolate_linear(ts, ys, to_t):
    """Linear-fit (ts, ys) and project to to_t.

    Returns the projected y value, or None if ts/ys are empty.
    If only one point or a degenerate time axis, returns the last y value.
    """
    if not ts or not ys or len(ts) != len(ys):
        return None
    if len(ts) < 2:
        return ys[-1]
    t0 = ts[0]
    dxs = [t - t0 for t in ts]
    n = len(dxs)
    mean_x = sum(dxs) / n
    mean_y = sum(ys) / n
    num = sum((dxs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((dxs[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return ys[-1]
    slope = num / den
    intercept = mean_y - slope * mean_x
    return slope * (to_t - t0) + intercept


def eod_narrative(history, is_charm=False, proj_close=None):
    """Classify end-of-day scenario from a list of snapshot summaries.

    history: list of dicts (oldest -> newest) from GammaEngine.snapshot_summary.
    is_charm: True when the history is the Charm view. For charm, the top-
        positive / top-negative strikes sit on the opposite side of spot from
        where pressure actually pushes price, so the target-strike text is
        omitted and only direction + magnitude are reported.
    proj_close: optional linear-extrapolated flip value at 15:15 CT close. When
        provided, gets woven into the verbose interpretation.
    Returns: {"scenario": str, "detail": str, "verbose": str}
    """
    if not history or len(history) < 2:
        return {
            "scenario": "NEUTRAL - insufficient history",
            "detail": "Need at least 2 snapshots to project.",
            "verbose": (
                "Not enough intraday data yet to make a projection.\n"
                "The collector needs at least two 5-minute snapshots\n"
                "before the rules engine can classify regime."
            ),
        }

    first = history[0]
    last = history[-1]

    flips = [h["flip"] for h in history if h["flip"] is not None]
    flip_delta = (flips[-1] - flips[0]) if len(flips) >= 2 else 0.0

    net_first = first.get("net_total", 0.0)
    net_last = last.get("net_total", 0.0)
    flip_last = last.get("flip")
    view_name = "Charm" if is_charm else "GEX"

    def _fmt_time(h):
        ts = h.get("ts")
        if ts is None:
            return "?"
        try:
            return ts.strftime("%H:%M")
        except Exception:
            return str(ts)
    t0, t1 = _fmt_time(first), _fmt_time(last)

    def _proj_line():
        if proj_close is None or flip_last is None:
            return ""
        drift = proj_close - flip_last
        return (f"\nLinear projection: flip at 15:15 ≈ {proj_close:,.1f} "
                f"(drift {drift:+.1f}pts from now).")

    # Rule 4: Net GEX flipping negative (GEX view only — charm net is unrelated)
    if net_first >= 0 and net_last < 0 and not is_charm:
        return {
            "scenario": "VOLATILITY EXPANSION - breakout risk",
            "detail": f"Net GEX flipped negative ({net_last:+.2e}).",
            "verbose": (
                f"Net gamma went from {net_first:+.2e} at {t0} to "
                f"{net_last:+.2e} at {t1}.\n"
                "Dealers have lost their long-gamma cushion — instead of\n"
                "dampening moves, they now AMPLIFY them. Expect faster\n"
                "swings into the close, and break-outs / -downs to extend\n"
                "rather than reverse."
                f"{_proj_line()}"
            ),
        }

    # Rules 2 / 3: Flip migrating
    if flip_delta > 5:
        if is_charm:
            scenario = f"UPWARD DRIFT (flip +{flip_delta:.1f}pts)"
            verbose = (
                f"Charm flip rose from {flips[0]:,.1f} at {t0} to "
                f"{flips[-1]:,.1f} at {t1} (+{flip_delta:.1f}pts).\n"
                "Time decay is shifting positioning upward: call gamma is\n"
                "strengthening below spot / put gamma is bleeding off above.\n"
                "Typical playbook: expect an upward grind into close as\n"
                "dealers buy to rehedge decaying short-put deltas."
                f"{_proj_line()}"
            )
        else:
            target = last.get("top_pos_strike")
            scenario = f"UPWARD PIN / DRIFT HIGHER toward {target}"
            verbose = (
                f"GEX flip rose from {flips[0]:,.1f} at {t0} to "
                f"{flips[-1]:,.1f} at {t1} (+{flip_delta:.1f}pts).\n"
                f"Dealers are accumulating long gamma above spot; the\n"
                f"dominant positive wall sits at {target}. This acts\n"
                f"as a magnet: expect price to pin or drift higher\n"
                f"toward {target} into close."
                f"{_proj_line()}"
            )
        return {
            "scenario": scenario,
            "detail": f"Flip migrating up (+{flip_delta:.1f} pts across window).",
            "verbose": verbose,
        }
    if flip_delta < -5:
        if is_charm:
            scenario = f"DOWNWARD PRESSURE (flip {flip_delta:.1f}pts)"
            verbose = (
                f"Charm flip fell from {flips[0]:,.1f} at {t0} to "
                f"{flips[-1]:,.1f} at {t1} ({flip_delta:.1f}pts).\n"
                "Time decay is shifting positioning downward: put gamma\n"
                "is strengthening above spot / call gamma is bleeding off\n"
                "below. Typical playbook: dealers are increasingly short\n"
                "delta into close — any downward push gets amplified\n"
                "rather than absorbed."
                f"{_proj_line()}"
            )
        else:
            target = last.get("top_neg_strike")
            scenario = f"DOWNWARD PRESSURE toward {target}"
            flip_last_str = f"{flip_last:,.1f}" if flip_last is not None else "flip"
            verbose = (
                f"GEX flip fell from {flips[0]:,.1f} at {t0} to "
                f"{flips[-1]:,.1f} at {t1} ({flip_delta:.1f}pts).\n"
                f"The dominant negative-gamma wall sits at {target} — this\n"
                f"strike attracts price once the flip gives way. Expect\n"
                f"downside pressure and possible acceleration toward\n"
                f"{target} if spot loses the current flip ({flip_last_str})."
                f"{_proj_line()}"
            )
        return {
            "scenario": scenario,
            "detail": f"Flip migrating down ({flip_delta:.1f} pts across window).",
            "verbose": verbose,
        }

    # Rule 1: Flat flip (PIN)
    if abs(flip_delta) <= 5 and flip_last is not None:
        return {
            "scenario": f"PIN @ {flip_last:,.1f}",
            "detail": "Flip stable; symmetric positioning -> low vol.",
            "verbose": (
                f"Flip has been stable around {flip_last:,.1f} "
                f"(±{abs(flip_delta):.1f}pts) between {t0} and {t1}.\n"
                "Dealers have a symmetric book; any move in either\n"
                "direction triggers hedging that pulls price back.\n"
                "Typical playbook: range-bound / pinned action into\n"
                "close. Avoid trend entries; fade extensions instead."
                f"{_proj_line()}"
            ),
        }

    return {
        "scenario": "NEUTRAL - insufficient trend",
        "detail": "No dominant signal in the trajectory.",
        "verbose": (
            f"No dominant signal in the {view_name.lower()} trajectory\n"
            f"between {t0} and {t1}.\n"
            f"Flip moved {flip_delta:+.1f}pts (below ±5 threshold).\n"
            "Watch for this to resolve into one of: pin, drift, or\n"
            "breakout as EOD approaches."
            f"{_proj_line()}"
        ),
    }


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


def build_historical_matrix(rows, current_spot, display="net"):
    """Build (strikes, times, matrix) for the heatmap historical side.

    Args:
        rows: list of (ts, spot, flip, top_pos, top_neg, net_total, grid)
            as returned by gex_history_db.load_today_with_grid.
        current_spot: used for the ±5% strike filter.
        display: which field from grid[strike] to use ('net', 'call', 'put').

    Returns: (strikes_list, times_list, numpy_matrix). Matrix shape is
    (len(strikes), len(times)); missing (strike, ts) cells are np.nan.
    """
    import numpy as np
    if not rows or current_spot is None:
        return [], [], np.zeros((0, 0))
    lo, hi = current_spot * 0.95, current_spot * 1.05
    # Union of strikes across all snapshots, filtered to ±5%.
    all_strikes = set()
    for row in rows:
        grid = row[6]
        all_strikes.update(grid.keys())
    strikes = sorted(s for s in all_strikes if lo <= s <= hi)
    times = [row[0] for row in rows]
    matrix = np.full((len(strikes), len(rows)), np.nan)
    for col_idx, row in enumerate(rows):
        grid = row[6]
        for row_idx, strike in enumerate(strikes):
            if strike in grid:
                matrix[row_idx, col_idx] = grid[strike].get(display, np.nan)
    return strikes, times, matrix


def find_key_gamma_strike(grid, spot, band_pct=0.01):
    """Return the strike with largest |net| within ±band_pct of spot, or None."""
    if not grid or spot <= 0:
        return None
    lo, hi = spot * (1 - band_pct), spot * (1 + band_pct)
    candidates = [(s, v.get("net", 0.0)) for s, v in grid.items() if lo <= s <= hi]
    if not candidates:
        return None
    return max(candidates, key=lambda sv: abs(sv[1]))[0]


def _fmt_dollar_magnitude(val):
    """Format a signed dollar magnitude as +/-$X.XX{M|B}. None → 'n/a'.

    Examples:
        None          -> 'n/a'
        -1.24e9       -> '-$1.24B'
        3.3e8         -> '+$330M'
        12345         -> '+$12,345'
    """
    if val is None:
        return "n/a"
    sign = "-" if val < 0 else "+"
    mag = abs(val)
    if mag >= 1e9:
        return f"{sign}${mag / 1e9:.2f}B"
    if mag >= 1e6:
        return f"{sign}${mag / 1e6:.0f}M"
    return f"{sign}${mag:,.0f}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GammaEngine — pure computation, no UI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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

        r = 0.045  # risk-free rate
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
        r = 0.045
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

        r = 0.045
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
        r = 0.045
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

    def project_exposure_forward(self, view, T_future, r=0.045):
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


def _fetch_market_internals(client):
    """Fetch the three market-internals tickers via ``client.get_quote``.

    Returns ``{"cpce": ..., "ad": ..., "skew": ...}``. ``cpce`` and ``skew``
    are floats; ``ad`` is a signed int (NYSE daily advance-decline net —
    today's advancers minus decliners; positive = bullish breadth).
    Each value is ``None`` on per-ticker failure.
    """
    out = {}
    for key, sym in _INTERNAL_SYMBOLS.items():
        try:
            r = client.get_quote(sym)
            if r.status_code != 200:
                out[key] = None
                continue
            data = r.json() or {}
            quote_block = data.get(sym, {}).get("quote", {})
            val = quote_block.get("lastPrice")
            if val is None:
                out[key] = None
            elif key == "ad":
                out[key] = int(val)
            else:
                out[key] = float(val)
        except Exception as e:
            log.warning("Fetch failed for %s: %s", sym, e)
            out[key] = None
    return out


def _hedge_pressure_interpretation(value, direction):
    """Plain-English interpretation of a signed hedge-pressure value.

    value: signed float. Positive = dealers must buy shares to stay
        delta-neutral. Negative = dealers must sell.
    direction: short label string from the pressure_panel ('buying',
        'selling', 'neutral', or '').

    Returns a single sentence suitable for a regular investor.
    """
    def _fmt(v):
        av = abs(v)
        if av >= 1e9:
            return f"{av/1e9:.1f}B"
        if av >= 1e6:
            return f"{av/1e6:.0f}M"
        if av >= 1e3:
            return f"{av/1e3:.0f}K"
        return f"{av:.0f}"

    size = _fmt(value)
    d = (direction or "").lower()

    if value > 0 or d == "buying":
        return (f"Dealers need to BUY about {size} of shares to stay "
                f"hedged - that flow is supportive of upward price action.")
    if value < 0 or d == "selling":
        return (f"Dealers need to SELL about {size} of shares to stay "
                f"hedged - that flow can weigh on price (bearish drag).")
    return ("Dealer hedge flows are roughly neutral - neither side is "
            "being forced to push price.")


def _classify_pair_state(net_vanna, net_charm, vix_delta,
                         spot, charm_flip):
    """Classify the vanna/charm pair state.

    Returns one of: 'AGREE_UP', 'AGREE_DOWN', 'CONFLICT', 'FLAT', 'AGREE'.

    'AGREE' is the degraded result when vix_delta is None — we can compare
    vanna vs charm signs but can't infer direction.

    Asymmetric rule (see design §5, to be corrected in a later commit):
    The (short vanna, VIX↓) and (long vanna, VIX↑) same-sign patterns are
    BOTH bullish "vanna flow" per the codebase convention. But only the
    (long vanna, VIX↑) case can pair with bearish charm to produce
    AGREE_DOWN — the (short vanna, VIX↓) setup is the canonical dealer
    supportive bid and never resolves to bearish (it produces AGREE_UP
    when charm agrees, CONFLICT when charm disagrees).
    """
    FLAT_THRESHOLD = 5e7  # $50M

    if abs(net_vanna) < FLAT_THRESHOLD and abs(net_charm) < FLAT_THRESHOLD:
        return "FLAT"

    # Charm-direction proxy: spot above flip OR (no flip → use charm sign)
    if charm_flip is not None:
        post_call_flip = spot > charm_flip
    else:
        post_call_flip = net_charm > 0

    # Degraded mode when VIX delta is unknown
    if vix_delta is None:
        same_sign = (net_vanna > 0) == (net_charm > 0)
        return "AGREE" if same_sign else "CONFLICT"

    vanna_same_sign_as_vix = (net_vanna * vix_delta) > 0

    if not vanna_same_sign_as_vix:
        # Opposite-sign vanna×vix is "vanna bearish flow" — currently no test
        # exercises this path; safest default is CONFLICT.
        return "CONFLICT"

    # Same-sign vanna×vix = "vanna bullish flow" per design convention.
    if net_charm > 0 and post_call_flip:
        return "AGREE_UP"

    # AGREE_DOWN only when both vanna AND vix are positive (long-vanna setup)
    # AND charm is bearish AND we're below the flip. The (neg, neg) mirror
    # does NOT produce AGREE_DOWN — it's the canonical bullish setup.
    if (net_vanna > 0 and vix_delta > 0
            and net_charm < 0 and not post_call_flip):
        return "AGREE_DOWN"

    return "CONFLICT"


def _compute_drift_confidence(state, net_vanna, net_charm, vix_delta,
                              spot, charm_flip, expected_move,
                              dte, hours_to_close, top5_oi,
                              vix_open=None):
    """Compute confidence 0.0–1.0 for the drift signal."""
    conf = 0.20

    if state in ("AGREE_UP", "AGREE_DOWN", "AGREE"):
        conf += 0.40

    # Vol-move weight per design §4: normalize against session-open VIX so a
    # 0.5pt move is sized correctly whether VIX opens at 12 or 30. Caps at
    # 10% relative move (typical of session extremes).
    if vix_delta is not None and vix_open:
        vol_weight = min(abs(vix_delta) / vix_open, 0.10) / 0.10 * 0.10
        conf += vol_weight

    if charm_flip is not None and expected_move and expected_move > 0:
        flip_weight = min(abs(spot - charm_flip) / expected_move, 1.0) * 0.10
        conf += flip_weight

    # Top-5 VEX magnitude proxy (in $M). Boost when concentrated dealer
    # positions are large enough to imply active hedging — typical SPX
    # 0-DTE sessions cluster top-5 net VEX in the $100M–$500M range.
    if top5_oi > 200:  # $200M of top-5 net VEX magnitude
        conf += 0.10

    if dte == 0 and hours_to_close < 1.0:
        conf -= 0.10

    return max(0.0, min(1.0, conf))


def _classify_pair_regime(net_vanna, vix_delta, net_charm, hours_to_close):
    """Classify which flow dominates: VANNA_DOMINANT / CHARM_DOMINANT / BALANCED."""
    if vix_delta is None:
        vanna_impact = 0.0
    else:
        vanna_impact = abs(net_vanna * vix_delta)

    charm_impact = abs(net_charm * hours_to_close / 24.0)

    if vanna_impact == 0 and charm_impact == 0:
        return "BALANCED"

    if vanna_impact > 1.5 * charm_impact:
        return "VANNA_DOMINANT"
    if charm_impact > 1.5 * vanna_impact:
        return "CHARM_DOMINANT"
    return "BALANCED"


def _load_vix_today(db_conn):
    """Return (vix_now, vix_open) tuple from today's $VIX snapshots.

    Pulls from gex_history.db using the existing load_today() function.
    Either or both can be None if the collector has no rows today.
    """
    if db_conn is None:
        return (None, None)
    try:
        import gex_history_db as gdb
        rows = gdb.load_today(db_conn, "$VIX", "gex")
    except Exception:
        return (None, None)
    if not rows:
        return (None, None)
    # Row schema: (ts, spot, flip, top_pos_strike, top_neg_strike, net_total)
    vix_open = rows[0][1]
    vix_now = rows[-1][1]
    return (vix_now, vix_open)


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


def format_drift_pressure_panel(vanna_data, charm_data, vix_now, vix_open,
                                spot, dte, expected_move, hours_to_close,
                                top5_oi, charm_flip):
    """Build the Drift Pressure panel content dict.

    See design §3 for the output shape.
    """
    # Aggregate per-strike to net flows
    net_vanna = 0.0
    if vanna_data and vanna_data.get("gex"):
        net_vanna = sum(v["net"] for v in vanna_data["gex"].values())

    net_charm = 0.0
    if charm_data and charm_data.get("gex"):
        net_charm = sum(v["net"] for v in charm_data["gex"].values())

    vix_delta = None
    if vix_now is not None and vix_open is not None:
        vix_delta = vix_now - vix_open

    state = _classify_pair_state(net_vanna, net_charm, vix_delta,
                                 spot, charm_flip)
    confidence = _compute_drift_confidence(
        state, net_vanna, net_charm, vix_delta,
        spot, charm_flip, expected_move,
        dte, hours_to_close, top5_oi,
        vix_open=vix_open,
    )

    # Degraded-data guard: if either flow source is entirely missing, cap
    # confidence to LOW regardless of secondary weights — the agreement signal
    # is fundamentally unverifiable without both sides.
    if vanna_data is None or charm_data is None:
        confidence = min(confidence, 0.34)

    if confidence < 0.35:
        band = "LOW"
    elif confidence < 0.65:
        band = "MED"
    else:
        band = "HIGH"

    regime = _classify_pair_regime(net_vanna, vix_delta, net_charm, hours_to_close)
    if vix_delta is None:
        vix_dir = None
    elif vix_delta < 0:
        vix_dir = "down"
    else:
        vix_dir = "up"
    regime_note = _REGIME_NOTES.get((regime, vix_dir), "")

    return {
        "net_vanna": net_vanna,
        "net_charm": net_charm,
        "vix_now": vix_now,
        "vix_delta": vix_delta,
        "charm_flip": charm_flip,
        "pair_state": state,
        "confidence": confidence,
        "confidence_band": band,
        "regime": regime,
        "regime_note": regime_note,
    }


def _drift_headline_text(view, gex_summary, charm_summary, dex_data,
                         drift_panel):
    """Single-line view-aware key-levels headline for the status strip.

    Each summary dict comes from GammaEngine.snapshot_summary().
    drift_panel is the format_drift_pressure_panel output (vanna view only).
    Returns an empty string when nothing actionable is available.
    """
    if view == "gex" and gex_summary:
        pos = gex_summary.get("top_pos_strike")
        neg = gex_summary.get("top_neg_strike")
        flip = gex_summary.get("flip")
        return (f"Call wall @ {pos if pos else '--'}   "
                f"Put wall @ {neg if neg else '--'}   "
                f"Flip @ {flip if flip else '--'}")
    if view == "charm" and charm_summary:
        flip = charm_summary.get("flip")
        pos = charm_summary.get("top_pos_strike")
        neg = charm_summary.get("top_neg_strike")
        return (f"Charm flip @ {flip if flip else '--'}   "
                f"Max+ @ {pos if pos else '--'}   "
                f"Max− @ {neg if neg else '--'}")
    if view == "dex" and dex_data:
        hedge = dex_data.get("hedge_pressure")
        if hedge is None:
            hedge_str = "--"
        elif hedge > 0:
            hedge_str = f"BUY ${_fmt_dollar_magnitude(hedge)}"
        else:
            hedge_str = f"SELL ${_fmt_dollar_magnitude(abs(hedge))}"
        return f"Δ hedge: {hedge_str}"
    if view == "vanna" and drift_panel:
        state = drift_panel.get("pair_state", "--")
        regime = drift_panel.get("regime", "--").replace("_", "-")
        band = drift_panel.get("confidence_band", "--")
        return f"Pair {state}   Regime: {regime}   Conf: {band}"
    return ""


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


def _explain_what(view):
    return list(_EXPLAIN_WHAT.get(view, _EXPLAIN_WHAT["gex"]))


def _explain_section(title, body):
    """One ━━ TITLE ━━ block; body is a list of lines (already strings)."""
    bar = f"━━ {title} ━━"
    return "\n".join([bar, *body, ""])


def _term_walls_from_rows(rows):
    """Derive the near/far dominant Net-GEX walls from term snapshot rows.

    ``rows`` is the list of dicts stored on ``GammaWindow._term_last_rows``
    (from ``gex_history_db.load_term_snapshot``); each has at least
    ``expiration_date``, ``strike``, and ``net_gex_usd``. The 'wall' for an
    expiration is the strike with the largest |Net GEX| (the most significant
    gamma level). near = nearest expiration, far = latest.

    Returns ``{"near_wall": float|None, "far_wall": float|None}`` or ``None``
    when there are no usable rows. ``far_wall`` is None when only one
    expiration is present (no genuinely longer-dated wall to show).
    """
    if not rows:
        return None
    exps = sorted({r["expiration_date"] for r in rows
                   if r.get("expiration_date")})
    if not exps:
        return None

    def _wall_for(exp):
        cand = [r for r in rows if r.get("expiration_date") == exp]
        if not cand:
            return None
        top = max(cand, key=lambda r: abs(r.get("net_gex_usd", 0) or 0))
        return top.get("strike")

    near = _wall_for(exps[0])
    far = _wall_for(exps[-1]) if len(exps) > 1 else None
    if near is None and far is None:
        return None
    return {"near_wall": near, "far_wall": far}


def build_explain_text(view, ctx):
    """Build the full plain-English Explain popup string for ``view``.

    Pure: takes already-computed snapshots in ``ctx`` (see plan), returns a
    string. Tolerates missing/None data with honest 'no data' lines.
    """
    parts = [
        _explain_section("WHAT YOU'RE LOOKING AT", _explain_what(view)),
        _explain_section("RIGHT NOW", _explain_right_now(view, ctx)),
        _explain_section("HOW TO PLAY IT", _explain_how_to_play(view, ctx)),
        _explain_section("GLOBAL SENTIMENT", _explain_global_sentiment(view, ctx)),
        _explain_footer(ctx),
    ]
    return "\n".join(parts).rstrip() + "\n"


_EXPLAIN_VIEW_TITLES = {
    "gex": "GAMMA EXPOSURE (GEX)",
    "charm": "CHARM PRESSURE (Δ-decay)",
    "dex": "DELTA EXPOSURE (DEX)",
    "vanna": "VANNA EXPOSURE (VEX)",
}


def _explain_subsection(title, body):
    """An h3-level ── TITLE ── block; body is a list of already-string lines."""
    return "\n".join([f"── {title} ──", *body, ""])


def build_explain_html_text(ctx):
    """Combined Explain text covering ALL exposure views in one document.

    Each view gets a ━━ header with What / Right now / How-to-play sub-sections;
    a single shared Global Sentiment + footer close the page. Feeds
    ``html_render.render_explain_html`` (the Dealer Pinch section is prepended
    separately there). Pure — reuses the per-view explain helpers.
    """
    parts = []
    for view in ("gex", "charm", "dex", "vanna"):
        parts.append(f"━━ {_EXPLAIN_VIEW_TITLES[view]} ━━")
        parts.extend(_explain_what(view))
        parts.append("")
        parts.append(_explain_subsection("Right now", _explain_right_now(view, ctx)))
        parts.append(_explain_subsection("How to play it",
                                         _explain_how_to_play(view, ctx)))
    parts.append(_explain_section("GLOBAL SENTIMENT",
                                  _explain_global_sentiment("gex", ctx)))
    parts.append(_explain_footer(ctx))
    return "\n".join(parts).rstrip() + "\n"


def _fmt_strike(s):
    return f"{s:,.0f}" if s is not None else "--"


def _gex_bias(spot, flip, net_total):
    """Return (label, headline) for the GEX regime."""
    if flip is None or not spot:
        pos = (net_total or 0) > 0
        return ("long-gamma" if pos else "short-gamma",
                "Dealers are net %s gamma."
                % ("long — expect pinning / mean-reversion" if pos
                   else "short — expect moves to accelerate"))
    if spot >= flip:
        return ("long-gamma",
                "Spot is ABOVE the flip — dealers long gamma, so moves get "
                "dampened and price tends to pin between the walls.")
    return ("short-gamma",
            "Spot is BELOW the flip — dealers short gamma, so hedging "
            "amplifies moves; breakouts run and dips overshoot.")


def _explain_max_pain_lines(mp):
    """Render the max-pain / pin-risk / magnet lines for the Explain popup.

    ``mp`` is the ``max_pain`` block from ``build_analysis_dict`` (or the live
    ctx). Returns ``[]`` when unavailable so callers can append unconditionally.
    """
    if not mp or mp.get("max_pain") is None:
        return []
    out = ["", f"Max pain:               {_fmt_strike(mp.get('max_pain'))}"]
    pr = mp.get("pin_risk")
    if pr is not None:
        if pr >= 0.66:
            tag = "HIGH — spot is hugging max pain, expect pinning"
        elif pr >= 0.33:
            tag = "moderate"
        else:
            tag = "low — spot is far from max pain"
        out.append(f"Pin risk:               {pr:.0%}  ({tag})")
    magnet = mp.get("magnet") or {}
    if magnet.get("agree") and magnet.get("level") is not None:
        out.append(
            f"0-DTE magnet:           {_fmt_strike(magnet.get('level'))}  "
            f"(max pain & gamma agree — strong pull)")
    elif magnet.get("level") is not None:
        out.append(
            f"0-DTE magnet:           {_fmt_strike(magnet.get('level'))}  "
            f"(max pain & gamma diverge — weak/contested)")
    return out


def _explain_flow_metric_lines(ctx):
    """Render the scalar flow metrics (P/C ratios, OI concentration, dealer
    hedge shares, gamma acceleration) for the Explain popup.

    Each metric prints only when present in ``ctx``; returns ``[]`` when none
    are available so callers can append unconditionally. (FlashAlpha quick
    wins #3, #5, #6, #4.)
    """
    out = []
    pc = ctx.get("pc_ratios") or {}
    pc_oi, pc_vol = pc.get("pc_oi"), pc.get("pc_volume")
    if pc_oi is not None or pc_vol is not None:
        def _pc(v):
            return f"{v:.2f}" if v is not None else "--"
        out.append(f"P/C OI: {_pc(pc_oi)}   P/C vol: {_pc(pc_vol)}  "
                   f"({'put-heavy/defensive' if (pc_oi or 0) > 1 else 'call-heavy/bullish'})")

    conc = ctx.get("oi_concentration")
    if conc is not None and conc.get("hhi") is not None:
        hhi = conc["hhi"]
        crowd = ("crowded — few strikes dominate" if hhi >= 0.18
                 else "broad — spread across strikes")
        out.append(f"OI concentration (HHI): {hhi:.3f}  ({crowd})")

    hs = ctx.get("hedge_shares")
    if hs is not None:
        side = "BUY into rallies / SELL dips" if hs >= 0 else "SELL into rallies / BUY dips (destabilising)"
        out.append(f"Dealer hedging: {hs:,.0f} sh / 1% move  ({side})")

    ga = ctx.get("gamma_acceleration")
    if ga is not None:
        tag = ("intense — 0DTE convexity dominates" if ga >= 2.0
               else "elevated" if ga >= 1.0 else "muted")
        out.append(f"Gamma acceleration (0DTE/7DTE): {ga:.2f}x  ({tag})")

    if out:
        out = [""] + out
    return out


def _explain_right_now(view, ctx):
    if view == "gex":
        s = ctx.get("gex_summary")
        if not s:
            return ["No GEX data available yet (chain not loaded / pre-market)."]
        spot = ctx.get("spot") or s.get("spot")
        flip = s.get("flip")
        _, headline = _gex_bias(spot, flip, s.get("net_total"))
        # Prefer directional walls (call above spot / put below spot); fall back
        # to the net-derived top strikes when the walls block is unavailable.
        walls = ctx.get("walls") or {}
        gex_walls = walls.get("gex") or {}
        oi_walls = walls.get("oi") or {}
        call_wall = gex_walls.get("call_wall", s.get("top_pos_strike"))
        put_wall = gex_walls.get("put_wall", s.get("top_neg_strike"))
        lines = [
            headline,
            "",
            f"Call wall (resistance): {_fmt_strike(call_wall)}",
            f"Put wall  (support):    {_fmt_strike(put_wall)}",
            f"Flip point:             {_fmt_strike(flip)}  "
            f"(spot {_fmt_strike(spot)})",
        ]
        if oi_walls.get("call_wall") is not None or oi_walls.get("put_wall") is not None:
            lines.append(
                f"By OI:  call {_fmt_strike(oi_walls.get('call_wall'))} / "
                f"put {_fmt_strike(oi_walls.get('put_wall'))}")
        lines += _explain_max_pain_lines(ctx.get("max_pain"))
        lines += _explain_flow_metric_lines(ctx)
        return lines
    if view == "charm":
        s = ctx.get("charm_summary")
        if not s:
            return ["No Charm data available yet."]
        spot, flip = ctx.get("spot") or s.get("spot"), s.get("flip")
        above = (spot is not None and flip is not None and spot >= flip)
        headline = ("Spot above the charm flip — calls bleeding delta should "
                    "drift price UP into the close." if above else
                    "Spot below the charm flip — puts bleeding delta should "
                    "drift price DOWN into the close.") if flip is not None else \
                   "Charm flip not well-defined right now (flat decay field)."
        return [headline, "",
                f"Charm flip:        {_fmt_strike(flip)}  (spot {_fmt_strike(spot)})",
                f"Max + decay strike: {_fmt_strike(s.get('top_pos_strike'))}",
                f"Max - decay strike: {_fmt_strike(s.get('top_neg_strike'))}"]
    if view == "dex":
        s = ctx.get("dex_summary")
        if not s:
            return ["No DEX data available yet."]
        hp = s.get("hedge_pressure")
        if hp is None:
            return ["DEX hedge pressure not available for this snapshot."]
        direction = "buying" if hp > 0 else "selling"
        flow = _fmt_dollar_magnitude(abs(hp)).lstrip("+")  # -> "$330M"
        return [_hedge_pressure_interpretation(hp, direction), "",
                f"Net dealer hedge flow: "
                f"{'BUY' if hp > 0 else 'SELL'} {flow}"]
    if view == "vanna":
        d = ctx.get("drift_panel")
        if not d:
            return ["No Vanna/drift data available yet."]
        vix_now, vd = d.get("vix_now"), d.get("vix_delta")
        vix_line = ("VIX --" if vix_now is None else
                    f"VIX {vix_now:.2f}" +
                    ("" if vd is None else f" {'↓' if vd < 0 else '↑'} {vd:+.2f}"))
        state = d.get("pair_state", "--")
        pretty = {"AGREE_UP": "AGREE ↑", "AGREE_DOWN": "AGREE ↓",
                  "AGREE": "AGREE", "CONFLICT": "CONFLICT"}.get(state, state)
        conf = d.get("confidence")
        conf_str = f"{d.get('confidence_band','--')} ({conf:.0%})" \
            if conf is not None else d.get("confidence_band", "--")
        flip = d.get("charm_flip")
        return [
            f"Net Vanna:  {_fmt_dollar_magnitude(d.get('net_vanna',0))} / 1vol"
            f"     {vix_line}",
            f"Net Charm:  {_fmt_dollar_magnitude(d.get('net_charm',0))} / day"
            f"     flip @ {_fmt_strike(flip)}",
            f"Pair:       {pretty}    confidence: {conf_str}",
            f"Regime:     {d.get('regime','--')} ({d.get('regime_note','')})",
        ]
    if view == "term":
        td = ctx.get("term_data")
        if not td:
            return ["Term structure is SPXW-only — no term data for this "
                    "symbol/snapshot."]
        return [f"Nearest-expiry wall: {_fmt_strike(td.get('near_wall'))}",
                f"Longer-dated wall:   {_fmt_strike(td.get('far_wall'))}"]
    return []  # all five views handled above


def _sentiment_dir(sentiment):
    """Map evaluate_regime() output -> 'bull' / 'bear' / 'neutral' / None."""
    if not sentiment or not sentiment.get("active"):
        return None
    score = sentiment.get("composite_score")
    if score is None:
        return "neutral"
    if score >= 6.5:
        return "bull"
    if score <= 3.5:
        return "bear"
    return "neutral"


def _view_dir(view, ctx):
    """Directional lean implied by the current view state: 'bull'/'bear'/'neutral'."""
    if view == "term":
        return "neutral"
    if view == "gex":
        # GEX is a VOLATILITY-REGIME axis (long-gamma = pinning/dampening;
        # short-gamma = acceleration), not a directional bull/bear axis. The
        # short-gamma posture is "trade WITH the trend, use the flip as the
        # line" — direction-agnostic — so GEX makes no agree/conflict claim
        # against directional sentiment. Always neutral.
        return "neutral"
    if view == "charm":
        s = ctx.get("charm_summary") or {}
        spot, flip = ctx.get("spot"), s.get("flip")
        if flip is None or not spot:
            return "neutral"
        return "bull" if spot >= flip else "bear"
    if view == "dex":
        hp = (ctx.get("dex_summary") or {}).get("hedge_pressure")
        if hp is None:
            return "neutral"
        return "bull" if hp > 0 else "bear"
    if view == "vanna":
        st = (ctx.get("drift_panel") or {}).get("pair_state", "")
        if st == "AGREE_UP":
            return "bull"
        if st == "AGREE_DOWN":
            return "bear"
        return "neutral"
    return "neutral"


def _agree_tag(view, ctx):
    """One-line agree/conflict tag vs overall sentiment, or '' if unavailable."""
    sdir = _sentiment_dir(ctx.get("sentiment"))
    vdir = _view_dir(view, ctx)
    if sdir is None:
        return "Sentiment: overall-market read unavailable — treat as standalone."
    if sdir == "neutral" or vdir == "neutral":
        return ("Sentiment: overall market is %s — no strong agree/conflict "
                "with this view." % sdir)
    if sdir == vdir:
        return ("✓ Overall sentiment AGREES with this view — higher "
                "conviction.")
    return ("⚠ Overall sentiment CONFLICTS with this view — lower "
            "conviction, treat the posture cautiously.")


def _vix_risk_label(vix_now):
    if vix_now is None:
        return "VIX unknown"
    if vix_now < 15:
        return f"VIX {vix_now:.1f} (calm)"
    if vix_now < 22:
        return f"VIX {vix_now:.1f} (normal)"
    return f"VIX {vix_now:.1f} (stressed)"


def _explain_how_to_play(view, ctx):
    if view == "gex":
        s = ctx.get("gex_summary")
        if not s:
            return ["Wait for the chain to load before acting on GEX."]
        spot, flip = ctx.get("spot") or s.get("spot"), s.get("flip")
        long_gamma = flip is None or (spot and spot >= flip)
        vixr = _vix_risk_label(ctx.get("vix_now"))
        if long_gamma:
            posture = ("Posture: lean toward FADING pushes into the call wall "
                       f"({_fmt_strike(s.get('top_pos_strike'))}) and buying "
                       f"dips toward the put wall "
                       f"({_fmt_strike(s.get('top_neg_strike'))}) — dealers pin.")
            risk = (f"Risk: low-risk mean-reversion zone while {vixr} and price "
                    f"holds between the walls. It BREAKS DOWN if the flip "
                    f"({_fmt_strike(flip)}) goes on volume — gamma turns "
                    f"negative and moves accelerate.")
        else:
            posture = ("Posture: respect momentum — below the flip dealers "
                       "amplify moves, so don't fade; trade with the trend and "
                       "use the flip as the line.")
            risk = (f"Risk: HIGHER. {vixr}, short-gamma regime — expect "
                    f"overshoot. Use smaller size and wider stops; a reclaim of "
                    f"the flip ({_fmt_strike(flip)}) flips the regime back.")
        return [posture, "", risk, "", _agree_tag(view, ctx)]
    if view == "charm":
        s = ctx.get("charm_summary")
        if not s:
            return ["Wait for Charm data."]
        spot, flip = ctx.get("spot"), s.get("flip")
        vixr = _vix_risk_label(ctx.get("vix_now"))
        if flip is None:
            posture = ("Posture: charm flip is undefined (flat decay field) — no "
                       "clean pin-drift edge right now; wait for the field to "
                       "define before leaning on it.")
        else:
            up = (spot is not None and spot >= flip)
            posture = ("Posture: lean with the pin-drift — bias toward the upside "
                       "into the afternoon." if up else
                       "Posture: lean with the pin-drift — bias toward the downside "
                       "into the afternoon.")
        risk = (f"Risk: charm is a SLOW force; {vixr}. A real catalyst (news, "
                f"data) overrides it instantly — size down near scheduled "
                f"events and don't hold the drift through them.")
        return [posture, "", risk, "", _agree_tag(view, ctx)]
    if view == "dex":
        s = ctx.get("dex_summary")
        hp = (s or {}).get("hedge_pressure")
        if hp is None:
            return ["Wait for DEX hedge-pressure data."]
        vixr = _vix_risk_label(ctx.get("vix_now"))
        up = hp > 0
        posture = ("Posture: dealer buying is a TAILWIND — favor the long side / "
                   "don't fight strength." if up else
                   "Posture: dealer selling is a HEADWIND — favor the short side / "
                   "don't chase strength.")
        risk = (f"Risk: this flow is supportive {vixr} UNTIL it flips. Watch the "
                f"projected EOD flip — once hedge pressure crosses zero the "
                f"tailwind becomes a headwind.")
        return [posture, "", risk, "", _agree_tag(view, ctx)]
    if view == "vanna":
        d = ctx.get("drift_panel")
        if not d:
            return ["Wait for Vanna/drift data."]
        st = d.get("pair_state", "")
        band = d.get("confidence_band", "--")
        vixr = _vix_risk_label(ctx.get("vix_now") or d.get("vix_now"))
        if st == "AGREE_UP":
            posture = ("Posture: vanna + charm AGREE to the upside — lean long, "
                       "vol-supported drift higher.")
        elif st == "AGREE_DOWN":
            posture = ("Posture: vanna + charm AGREE to the downside — lean "
                       "short / defensive.")
        elif st == "CONFLICT":
            posture = ("Posture: vanna and charm CONFLICT — no clean drift edge; "
                       "stand aside or trade levels, not direction.")
        else:
            posture = ("Posture: flows roughly balanced — no strong vol-drift "
                       "edge right now.")
        risk = (f"Risk: conviction scales with the confidence band — current "
                f"band is {band}. {vixr}. Low band = treat as a weak lean only.")
        return [posture, "", risk, "", _agree_tag(view, ctx)]
    if view == "term":
        return ["Posture: use near-term walls as the day's rails.",
                "",
                "Risk: a near-term wall can be OVERRUN if longer-dated "
                "positioning shifts — confirm with the GEX view before leaning "
                "hard on a term level."]
    return []  # all five views handled above


def _explain_global_sentiment(view, ctx):
    sent = ctx.get("sentiment")
    if not sent or not sent.get("active"):
        return ["Overall sentiment unavailable (bridge stale/offline) — this "
                "view's read stands on its own."]
    score = sent.get("composite_score")
    trend = sent.get("trend_state") or "n/a"
    conf = sent.get("trend_confidence")
    if score is None:
        mood = "mixed / neutral"
    elif score >= 6.5:
        mood = "risk-on / bullish"
    elif score <= 3.5:
        mood = "risk-off / bearish"
    else:
        mood = "mixed / neutral"
    score_str = f"{score:.0f}/10" if score is not None else "n/a"
    conf_str = f"{conf:.0%}" if conf is not None else "n/a"
    lines = [
        f"Overall market sentiment: {mood} "
        f"(score {score_str}, trend {trend}, confidence {conf_str}).",
        "",
    ]
    sdir, vdir = _sentiment_dir(sent), _view_dir(view, ctx)
    if vdir == "neutral" or sdir == "neutral":
        lines.append("This view is directionally neutral right now, so it "
                     "neither confirms nor fights the broad backdrop.")
    elif sdir == vdir:
        lines.append("This view sits WITH the grain of that backdrop — the "
                     "agreeing scenario is the lower-risk one.")
    else:
        lines.append("This view sits AGAINST that backdrop — the conflict is "
                     "the higher-risk scenario to watch.")
    return lines


def _explain_footer(ctx):
    vixr = _vix_risk_label(ctx.get("vix_now"))
    sent = ctx.get("sentiment") or {}
    if sent.get("active") and sent.get("composite_score") is not None:
        sc = sent["composite_score"]
        lab = "bullish" if sc >= 6.5 else "bearish" if sc <= 3.5 else "neutral"
        sent_str = f"Sentiment {sc:.0f}/10 {lab}"
    else:
        sent_str = "Sentiment n/a"
    return f"━━━\n{vixr} · {sent_str}"


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


def fetch_symbol_analysis(client, symbol, use_volume=False, grouping=1):
    """Fetch chain + compute analysis blocks for any symbol.

    Returns dict {'gex': ..., 'charm': ..., 'dex': ..., 'vanna': ...} where each
    value is the output of build_analysis_dict, or None if the fetch fails.

    Pure engine work (chain fetch + GammaEngine + build_analysis_dict) that used
    to live as ``GammaWindow._fetch_symbol_analysis_impl`` on the Tk window class;
    moved to module scope when the window was split out to
    ``gamma_window_legacy.py``. Covered by tests/test_fetch_symbol_analysis.py.
    """
    try:
        today = datetime.now(TZ).date()
        to_date = today + timedelta(days=7)
        r = client.get_option_chain(
            symbol,
            contract_type=client.Options.ContractType.ALL,
            from_date=today, to_date=to_date,
        )
        if r.status_code != 200:
            return None
        chain = r.json()
    except Exception as e:
        log.error("Fetch failed for %s: %s", symbol, e)
        return None
    if not chain:
        return None

    engine = GammaEngine()
    gex = engine.calc_from_chain(chain, use_volume=use_volume)
    charm = engine.calc_charm_from_chain(chain, use_volume=use_volume)
    dex = engine.calc_dex_from_chain(chain, use_volume=use_volume)
    vanna = engine.calc_vanna_from_chain(chain, use_volume=use_volume)
    em = engine.calc_expected_move_from_chain(chain)
    dte = engine._last_dte

    if gex is None and charm is None and dex is None and vanna is None:
        return None

    return {
        "gex":   build_analysis_dict(gex, "gex", symbol, dte, expected_move=em, grouping=grouping, chain=chain)     if gex   else None,
        "charm": build_analysis_dict(charm, "charm", symbol, dte, expected_move=em, grouping=grouping, chain=chain) if charm else None,
        "dex":   build_analysis_dict(dex, "dex", symbol, dte, expected_move=em, grouping=grouping, chain=chain)     if dex   else None,
        "vanna": build_analysis_dict(vanna, "vanna", symbol, dte, expected_move=em, grouping=grouping, chain=chain) if vanna else None,
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


def _bundled_fmt_strike_line(item, data):
    """Format one top-strike line with optional delta-from-prev and open value."""
    base = f"  {item['strike']:>8,.0f}: {_bundled_fmt_val(item['value'])}"
    extras = []
    chg = (data.get("delta_change") or {}).get(item["strike"])
    if chg is not None and abs(chg) > 0:
        extras.append(f"Δ {_bundled_fmt_val(chg)}")
    opn = (data.get("value_at_open") or {}).get(item["strike"])
    if opn is not None:
        extras.append(f"open {_bundled_fmt_val(opn)}")
    if extras:
        base += "   (" + ", ".join(extras) + ")"
    return base


def _bundled_build_block(data, view):
    """Render one view's data block (GEX / Charm / DEX) for a symbol section."""
    pos_desc = {
        "GEX":   "dealer long gamma",
        "Charm": "positive delta-decay (calls losing delta into close)",
        "DEX":   "net long-delta exposure (call-heavy)",
        "Vanna": "delta rises when IV rises (call-heavy vol sensitivity)",
    }.get(view, "")
    neg_desc = {
        "GEX":   "dealer short gamma",
        "Charm": "negative delta-decay (puts losing delta into close)",
        "DEX":   "net short-delta exposure (put-heavy)",
        "Vanna": "delta falls when IV rises (put-heavy vol sensitivity)",
    }.get(view, "")
    lines = []
    lines.append(f"TOP POSITIVE {view} STRIKES ({pos_desc}):")
    for item in data["top_positive"]:
        lines.append(_bundled_fmt_strike_line(item, data))
    lines.append("")
    lines.append(f"TOP NEGATIVE {view} STRIKES ({neg_desc}):")
    for item in data["top_negative"]:
        lines.append(_bundled_fmt_strike_line(item, data))
    lines.append("")
    tail = data.get("tail_summary") or {}
    if tail.get("count_pos") or tail.get("count_neg"):
        lines.append(f"TAIL ({view}, beyond top 20):")
        if tail.get("count_pos"):
            lines.append(
                f"  +{tail['count_pos']} more positive strikes summing "
                f"{_bundled_fmt_val(tail['sum_pos'])}")
        if tail.get("count_neg"):
            lines.append(
                f"  {tail['count_neg']} more negative strikes summing "
                f"{_bundled_fmt_val(tail['sum_neg'])}")
        lines.append("")
    if data.get("flip_point"):
        lines.append(f"{view} FLIP POINT: ~{data['flip_point']:,.0f} "
                     f"(positive above, negative below)")
        lines.append("")
    pp = data.get("pressure_panel")
    if pp:
        lines.append("0-DTE DELTA PRESSURE:")
        lines.append(f"  Delta now:        {_bundled_fmt_val(pp['delta_now'])}")
        if pp.get("projected_close") is not None:
            lines.append(f"  Projected 15:00:  {_bundled_fmt_val(pp['projected_close'])}")
        if pp.get("hedge_pressure") is not None:
            direction = pp.get("hedge_direction") or ""
            suffix = f" ({direction})" if direction else ""
            lines.append(f"  Hedge pressure:   {_bundled_fmt_val(pp['hedge_pressure'])}{suffix}")
        if pp.get("projected_flip") is not None:
            lines.append(
                f"  Projected EOD flip: ~{pp['projected_flip']:,.1f} "
                f"(charm-shifted DEX zero-cross)")
        lines.append("")
    zones = data.get("net_by_zone") or {}
    lines.append(f"NET {view} BY ZONE:")
    lines.append(f"  Above spot (0-2%):  {_bundled_fmt_val(zones.get('above_0_2pct', 0))}")
    lines.append(f"  Below spot (0-2%):  {_bundled_fmt_val(zones.get('below_0_2pct', 0))}")
    lines.append(f"  Below spot (2-5%):  {_bundled_fmt_val(zones.get('below_2_5pct', 0))}")
    lines.append("")
    lines.append(f"CALL vs PUT breakdown at key strikes ({view}):")
    for item in data.get("atm_breakdown", []):
        lines.append(f"  {item['strike']:>8,.0f}: "
                     f"Call {_bundled_fmt_val(item['call'])} | "
                     f"Put {_bundled_fmt_val(item['put'])} | "
                     f"Net {_bundled_fmt_val(item['net'])}")
    return "\n".join(lines)


def _bundled_dealer_positioning_lines(data):
    """Render the symbol-level dealer-positioning metrics for the combined
    prompt (FlashAlpha quick wins #1–#6). Reads max pain, directional walls,
    P/C ratios, OI concentration, dealer hedge shares, and gamma acceleration
    via ``.get`` — returns ``[]`` when none are present so the caller can append
    unconditionally.
    """
    def _strike(s):
        return f"{s:,.0f}" if s is not None else "--"

    body = []

    mp = data.get("max_pain") or {}
    if mp.get("max_pain") is not None:
        line = f"Max pain: {_strike(mp.get('max_pain'))}"
        pr = mp.get("pin_risk")
        if pr is not None:
            line += f" | Pin risk: {pr:.0%}"
        magnet = mp.get("magnet") or {}
        if magnet.get("level") is not None:
            agree = "agree" if magnet.get("agree") else "diverge"
            line += f" | 0-DTE magnet: {_strike(magnet.get('level'))} (max pain & gamma {agree})"
        body.append(line)

    walls = data.get("walls") or {}
    gw, ow = walls.get("gex") or {}, walls.get("oi") or {}
    if gw.get("call_wall") is not None or gw.get("put_wall") is not None:
        body.append(
            f"Call wall (resistance): {_strike(gw.get('call_wall'))} | "
            f"Put wall (support): {_strike(gw.get('put_wall'))}  [by GEX]")
    if ow.get("call_wall") is not None or ow.get("put_wall") is not None:
        body.append(
            f"Call wall: {_strike(ow.get('call_wall'))} | "
            f"Put wall: {_strike(ow.get('put_wall'))}  [by open interest]")

    pc = data.get("pc_ratios") or {}
    if pc.get("pc_oi") is not None or pc.get("pc_volume") is not None:
        def _pc(v):
            return f"{v:.2f}" if v is not None else "--"
        body.append(f"P/C OI: {_pc(pc.get('pc_oi'))} | P/C volume: {_pc(pc.get('pc_volume'))}")

    conc = data.get("oi_concentration") or {}
    if conc.get("hhi") is not None:
        body.append(f"OI concentration (HHI): {conc['hhi']:.3f} "
                    f"across {conc.get('n_strikes', 0)} strikes")

    hs = data.get("hedge_shares")
    if hs is not None:
        body.append(f"Dealer hedging: {hs:,.0f} shares per 1% move")

    ga = data.get("gamma_acceleration") or {}
    ratio = ga.get("ratio") if isinstance(ga, dict) else ga
    if ratio is not None:
        near = ga.get("dte_near") if isinstance(ga, dict) else None
        far = ga.get("dte_far") if isinstance(ga, dict) else None
        suffix = f" ({near}DTE vs {far}DTE gamma)" if near is not None and far is not None else ""
        body.append(f"Gamma acceleration: {ratio:.2f}x{suffix}")

    if not body:
        return []
    return ["DEALER POSITIONING (symbol-level):"] + [f"  {b}" for b in body]


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


def _bundled_pinch_lines(data):
    """Render the Dealer Pinch block for the combined prompt from a symbol's
    ``dealer_pinch`` state dict. Returns ``[]`` when absent or no node, so the
    caller can append unconditionally. (Vanna/Charm exhaustion detector.)"""
    p = data.get("dealer_pinch")
    if not p or not (p.get("node") or {}).get("strike"):
        return []

    def _ck(v):
        return "n/a" if v is None else ("✓" if v else "✗")

    def _strike(s):
        return f"{s:,.0f}" if s is not None else "--"

    c = p.get("conditions", {})
    node = p.get("node", {})
    lv = p.get("levels", {})
    state = "ARMED" if p.get("armed") else "watching"
    lines = [
        f"DEALER PINCH ({p.get('regime', '--')}, {state}, "
        f"conf {p.get('confidence', 0):.0f}%):",
        f"  Node {_strike(node.get('strike'))} "
        f"(dominance {p.get('node_dominance', 0):.0%}) | "
        f"secondary {_strike(p.get('secondary_node'))}",
        f"  Conditions: DTE<5 {_ck(c.get('c1'))} | spot@node {_ck(c.get('c2'))} | "
        f"IV elevated {_ck(c.get('c3a'))} | RV falling {_ck(c.get('c3b'))}",
        f"  Levels: pin_target {_strike(lv.get('pin_target'))} | "
        f"break_trigger {_strike(lv.get('break_trigger'))} | "
        f"invalidation: {lv.get('invalidation', '--')}",
        f"  {p.get('playbook', '')}",
    ]
    return lines


def _per_symbol_section(symbol_label, blocks, premarket):
    """Build the per-symbol section (header + GEX/Charm/DEX blocks)."""
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
    lines.append(
        f"Symbol: {header_data['symbol']} | Spot: {spot:,.2f} | {dte_str}")
    if header_data.get("expected_move"):
        lines.append(
            f"Expected Move: +/-{header_data['expected_move']:,.2f} "
            f"({header_data['em_lower']:,.2f} - {header_data['em_upper']:,.2f})")
    lines.append(
        f"Time: {header_data['timestamp']} | "
        f"Hours to close: {header_data['hours_to_close']}")
    lines.append(f"Strike grouping: {header_data['grouping']}")

    # Hedge pressure (from DEX view's pressure_panel) - promote to its
    # own line per symbol, with a plain-English interpretation.
    pp = dex_data.get("pressure_panel") if dex_data else None
    if pp and pp.get("hedge_pressure") is not None:
        hp = pp["hedge_pressure"]
        direction = pp.get("hedge_direction") or ""

        def _fmt_signed(v):
            av = abs(v)
            if av >= 1e9:
                return f"{v/1e9:+.1f}B"
            if av >= 1e6:
                return f"{v/1e6:+.0f}M"
            if av >= 1e3:
                return f"{v/1e3:+.0f}K"
            return f"{v:+.0f}"

        lines.append(
            f"Hedge pressure: {_fmt_signed(hp)}"
            + (f" ({direction})" if direction else ""))
        lines.append(_hedge_pressure_interpretation(hp, direction))

    # Symbol-level dealer-positioning metrics (FlashAlpha quick wins #1–#6).
    lines += _bundled_dealer_positioning_lines(header_data)
    # Dealer Pinch detector (Vanna/Charm exhaustion).
    pinch_lines = _bundled_pinch_lines(header_data)
    if pinch_lines:
        lines.append("")
        lines += pinch_lines

    sub = []
    if gex_data:
        sub.append("--- GAMMA EXPOSURE (GEX) ---\n" + _bundled_build_block(gex_data, "GEX"))
    if charm_data:
        sub.append("--- CHARM PRESSURE (delta decay) ---\n" + _bundled_build_block(charm_data, "Charm"))
    if dex_data:
        sub.append("--- DELTA EXPOSURE (DEX) ---\n" + _bundled_build_block(dex_data, "DEX"))
    if vanna_data:
        sub.append("--- VANNA EXPOSURE (VEX, $/1vol-point) ---\n" + _bundled_build_block(vanna_data, "Vanna"))

    # Pull eod_probabilities from whichever view has it (they all share)
    eod = None
    for view_data in (gex_data, charm_data, dex_data, vanna_data):
        if view_data and view_data.get("eod_probabilities"):
            eod = view_data["eod_probabilities"]
            break

    tail_lines = []
    if eod and any(v is not None for v in eod.values()):
        tail_lines.append("")
        tail_lines.append("EOD probability of touching:")
        def _pct(v):
            return f"{v*100:.0f}%" if v is not None else "n/a"
        tail_lines.append(f"  EM upper:       {_pct(eod.get('touch_em_upper'))}")
        tail_lines.append(f"  EM lower:       {_pct(eod.get('touch_em_lower'))}")
        tail_lines.append(f"  Top + GEX wall: {_pct(eod.get('reach_pos_wall'))}")
        tail_lines.append(f"  Top - GEX wall: {_pct(eod.get('reach_neg_wall'))}")

    result = "\n".join(lines) + "\n\n" + "\n\n".join(sub)
    if tail_lines:
        result += "\n" + "\n".join(tail_lines)
    return result


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


def build_combined_prompt_bundled(spx_blocks, spy_blocks, qqq_blocks,
                                  *, premarket=False, spx_history=None,
                                  internals=None,
                                  slot_tag=None,
                                  todays_path_block=None):
    """Build a multi-symbol bundled prompt covering SPX, SPY, and QQQ.

    Each *_blocks argument is a dict {"gex": ..., "charm": ..., "dex": ...} or
    None. Each inner value is an analysis dict (output of build_analysis_dict)
    or None.

    spx_history (optional): list of (label, history_list) tuples for intraday
    evolution rendering. If None, the evolution block is skipped.

    Raises ValueError if all three symbols are None.
    """
    if spx_blocks is None and spy_blocks is None and qqq_blocks is None:
        raise ValueError("build_combined_prompt_bundled: all three symbol bundles are None")

    parts = []

    # Intro line - prefer SPX as the anchor symbol.
    anchor = spx_blocks or spy_blocks or qqq_blocks
    anchor_view = (anchor.get("gex") or anchor.get("charm") or anchor.get("dex") or anchor.get("vanna"))
    dte = anchor_view.get("dte", 0) if anchor_view else 0
    dte_str = "0-DTE" if dte == 0 else f"{dte}-DTE"
    if premarket:
        intro = (
            "You are an expert options market structure analyst. Below is structured "
            "data covering SPX, SPY, and QQQ options - YESTERDAY'S CLOSING gamma "
            "profile being carried into today's open. You will need to research "
            "overnight futures action and scheduled events yourself."
        )
    else:
        intro = (
            "You are an expert options market structure analyst. Below is structured "
            f"data covering SPX, SPY, and QQQ {dte_str} options. Compare them as a "
            "cross-index readout - agreement strengthens conviction, divergence flags risk."
        )
    parts.append(intro)
    parts.append("=== STRUCTURED DATA ===")

    if internals:
        parts.append(build_internals_block(internals))

    # Per-symbol sections.
    for label, blocks in (("SPX", spx_blocks), ("SPY", spy_blocks), ("QQQ", qqq_blocks)):
        if blocks is None:
            parts.append(f"=== {label} ===\n{label}: fetch failed - section omitted")
            continue
        section = _per_symbol_section(label, blocks, premarket)
        if section is None:
            parts.append(f"=== {label} ===\n{label}: fetch failed - section omitted")
        else:
            parts.append(section)

    # Optional intraday evolution block (SPX only).
    if spx_history and not premarket:
        hist_lines = []
        for label, hist in spx_history:
            if not hist or len(hist) < 2:
                continue
            first, last = hist[0], hist[-1]
            try:
                t0 = first["ts"].strftime("%H:%M")
                t1 = last["ts"].strftime("%H:%M")
            except Exception:
                continue
            if first.get("flip") is not None and last.get("flip") is not None:
                hist_lines.append(
                    f"{label} flip: {first['flip']:.1f} ({t0}) -> {last['flip']:.1f} ({t1})")
            if first.get("top_pos_strike") and last.get("top_pos_strike"):
                hist_lines.append(
                    f"{label} top+ wall: {first['top_pos_strike']:.0f} ({t0}) -> "
                    f"{last['top_pos_strike']:.0f} ({t1})")
            try:
                narrative = eod_narrative(list(hist), is_charm=(label == "Charm"))
                hist_lines.append(f"{label} EOD projection: {narrative['scenario']}")
            except Exception:
                pass
        if hist_lines:
            parts.append("=== SPX INTRADAY EVOLUTION ===\n" + "\n".join(hist_lines))

    # Optional today's path retrospective block (1500 slot).
    if todays_path_block:
        parts.append(todays_path_block)

    # Final ASK.
    ask = _PREMARKET_ASK if premarket else _INTRADAY_ASK
    if slot_tag == "1500":
        ask = ask + _ANALYSIS_REVIEW_ASK
    parts.append(ask)

    return "\n\n".join(parts)


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


_INTRADAY_SUMMARY_ASK = """\
You are giving a regular investor a 1-page glance at today's market.
Plain English throughout. No jargon.

Structure:

1. BIG PICTURE (1-2 sentences)
   What is the market mood right now? Use the Put/Call ratio,
   advancers/decliners, and SKEW Index. Plain words.

2. WHY IS THIS HAPPENING (1-2 sentences)
   In one or two lines: why is the tape acting this way today?
   Mention the macro context AND the session's path so far.

3. KEY LEVELS
   Three key price levels for SPX, SPY, QQQ each — one upside, one
   downside, one "must hold". One short sentence per level.

4. WHAT IF (3 short bullets)
   Three plausible scenarios for the rest of the session — one
   upside path, one downside path, one chop/sideways path. One
   sentence each, plain English.

Cap the whole reply at 350 words.
"""


_PREMARKET_SUMMARY_ASK = """\
You are giving a regular investor a 1-page premarket brief. Plain
English throughout. No jargon.

The data above is yesterday's close. Consider OVERNIGHT futures action
and any scheduled events today.

Structure:

1. BIG PICTURE (1-2 sentences)
   Overnight mood + today's scheduled events at a glance.

2. WHY IS THIS HAPPENING (1-2 sentences)
   In one or two lines: what macro/dealer/recent-tape context
   explains why we're opening from yesterday's close in this shape?

3. KEY LEVELS
   Three carry-over price levels for SPX, SPY, QQQ each — one upside,
   one downside, one "must hold". One short sentence per level.

4. WHAT IF (3 short bullets)
   Three plausible opens — gap up, gap down, in-line open. One
   sentence each, plain English.

Cap the whole reply at 350 words.
"""


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
    if premarket:
        intro = (
            "You are an options trader's premarket briefer. Below is YESTERDAY'S "
            "CLOSING gamma profile for SPX, SPY, and QQQ being carried into today's "
            "open. Research overnight futures action and today's scheduled events "
            "yourself."
        )
    else:
        intro = (
            "You are an options trader's intraday briefer. Below is structured "
            f"data covering SPX, SPY, and QQQ {dte_str} options. Compare them as a "
            "cross-index readout for a 1-page summary."
        )
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


def slot_filenames(slot_tag: str) -> tuple[str, str]:
    """Return (detail_filename, summary_filename) for a slot tag."""
    if slot_tag not in _VALID_SLOTS:
        raise ValueError(f"Invalid slot tag: {slot_tag!r}")
    return (
        f"gex_analysis_prompt_{slot_tag}.txt",
        f"gex_analysis_summary_prompt_{slot_tag}.txt",
    )


def slot_data_filename(slot_tag: str) -> str:
    """Return JSON sidecar filename for a slot tag."""
    if slot_tag not in _VALID_SLOTS:
        raise ValueError(f"Invalid slot tag: {slot_tag!r}")
    return f"gex_analysis_data_{slot_tag}.json"


def _condense_symbol_blocks(blocks):
    """Pull the small set of fields useful for retrospective comparison.
    Returns None for a None/empty blocks dict."""
    if blocks is None:
        return None
    view = blocks.get("gex") or blocks.get("charm") or blocks.get("dex") or blocks.get("vanna")
    if view is None:
        return None
    return {
        "spot": view.get("spot"),
        "dte": view.get("dte"),
        "expected_move": view.get("expected_move"),
        "em_upper": view.get("em_upper"),
        "em_lower": view.get("em_lower"),
        "flip_point": view.get("flip_point"),
        "top_positive_walls": [
            {"strike": item["strike"], "value": item["value"]}
            for item in (view.get("top_positive") or [])[:5]
        ],
        "top_negative_walls": [
            {"strike": item["strike"], "value": item["value"]}
            for item in (view.get("top_negative") or [])[:5]
        ],
        "eod_probabilities": view.get("eod_probabilities"),
    }


def write_slot_data_json(data_dir, slot_tag, spx_blocks, spy_blocks,
                         qqq_blocks, internals):
    """Write the JSON sidecar for a slot. Returns the pathlib.Path."""
    path = Path(data_dir) / slot_data_filename(slot_tag)
    payload = {
        "slot": slot_tag,
        "captured_at": datetime.now(TZ).isoformat(),
        "symbols": {
            "SPX": _condense_symbol_blocks(spx_blocks),
            "SPY": _condense_symbol_blocks(spy_blocks),
            "QQQ": _condense_symbol_blocks(qqq_blocks),
        },
        "internals": internals or {},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def read_today_slot_data(data_dir, slot_tag):
    """Read a slot's JSON sidecar IF it exists AND was captured today.

    Returns the parsed dict or None.
    """
    path = Path(data_dir) / slot_data_filename(slot_tag)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    captured = data.get("captured_at", "")
    today = datetime.now(TZ).date().isoformat()
    if not captured.startswith(today):
        return None
    return data


_RETROSPECTIVE_SLOTS = ["0820", "0845", "1000", "1300"]


def build_todays_path_block(jsons_by_slot, current_spots):
    """Format the 1500-slot retrospective TODAY'S PATH block.

    jsons_by_slot: dict mapping slot tag ("0820" etc.) to parsed JSON dict.
                   Slots not present (missing files) are simply omitted.
    current_spots: dict mapping symbol ("SPX", "SPY", "QQQ") to current spot.
    """
    lines = ["=== TODAY'S PATH (08:20 -> 15:00) ==="]

    for symbol in ("SPX", "SPY", "QQQ"):
        sym_lines = [f"\n{symbol}:"]
        spots = []
        for slot in _RETROSPECTIVE_SLOTS:
            j = jsons_by_slot.get(slot)
            if not j:
                continue
            sym_data = j.get("symbols", {}).get(symbol)
            if not sym_data:
                continue
            spot_val = sym_data.get("spot")
            if spot_val is not None:
                spots.append((slot, spot_val))

        current_spot = current_spots.get(symbol)
        if current_spot is not None:
            spots.append(("1500", current_spot))

        if not spots:
            sym_lines.append("  (no data this day)")
            lines.extend(sym_lines)
            continue

        trail = " -> ".join(f"{s[1]:.2f} ({s[0]})" for s in spots)
        sym_lines.append(f"  spot trail: {trail}")

        spot_vals = [s[1] for s in spots]
        sym_lines.append(f"  intraday high: {max(spot_vals):.2f}  "
                         f"low: {min(spot_vals):.2f}")

        # Compare opening expected range to realized
        opening = jsons_by_slot.get("0820") or jsons_by_slot.get("0845")
        if opening:
            o = opening.get("symbols", {}).get(symbol)
            if o:
                em_u, em_l = o.get("em_upper"), o.get("em_lower")
                if em_u is not None and em_l is not None and spot_vals:
                    realized_hi, realized_lo = max(spot_vals), min(spot_vals)
                    inside = realized_hi <= em_u and realized_lo >= em_l
                    status = "stayed inside" if inside else "broke out"
                    sym_lines.append(
                        f"  opening expected range: {em_l:.2f} - {em_u:.2f}  "
                        f"realized: {realized_lo:.2f} - {realized_hi:.2f}  "
                        f"({status})")

                # Walls touched
                pos_walls = o.get("top_positive_walls") or []
                neg_walls = o.get("top_negative_walls") or []
                pos_wall = pos_walls[0].get("strike") if pos_walls else None
                neg_wall = neg_walls[0].get("strike") if neg_walls else None
                if pos_wall is not None:
                    touched = max(spot_vals) >= pos_wall
                    sym_lines.append(
                        f"  opening top + wall: {pos_wall}  "
                        f"=> touched: {'yes' if touched else 'no'}")
                if neg_wall is not None:
                    touched = min(spot_vals) <= neg_wall
                    sym_lines.append(
                        f"  opening top - wall: {neg_wall}  "
                        f"=> touched: {'yes' if touched else 'no'}")

        lines.extend(sym_lines)

    # Internals trail
    int_lines = []
    for key, label in (("skew", "SKEW"), ("cpce", "CPCE"),
                       ("ad", "NYSE A/D")):
        vals = []
        for slot in _RETROSPECTIVE_SLOTS:
            j = jsons_by_slot.get(slot)
            if not j:
                continue
            v = (j.get("internals") or {}).get(key)
            if v is not None:
                vals.append((slot, v))
        if len(vals) >= 2:
            int_lines.append(
                f"  {label}: {vals[0][1]} ({vals[0][0]}) -> "
                f"{vals[-1][1]} ({vals[-1][0]})")

    if int_lines:
        lines.append("\nInternals trail:")
        lines.extend(int_lines)

    return "\n".join(lines)


_FIRE_TIME_TO_SLOT = {
    (8, 19): "0820",
    (8, 44): "0845",
    (9, 59): "1000",
    (12, 59): "1300",
    (14, 59): "1500",
}


def slot_tag_for_time(hour: int, minute: int) -> str | None:
    """Return slot tag for a scheduler fire time, or None if unknown."""
    return _FIRE_TIME_TO_SLOT.get((hour, minute))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Net GEX Term Heatmap — module-level renderer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _format_dollars(v: float) -> str:
    """Format a dollar amount as a compact label like '530.34 M', '-1.23 B'."""
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.2f} B"
    if a >= 1e6:
        return f"{v / 1e6:.2f} M"
    return f"{v / 1e3:.0f} K"


def _short_date(yyyy_mm_dd: str) -> str:
    """Format '2026-04-29' as 'Apr 29' for axis labels."""
    from datetime import datetime
    return datetime.strptime(yyyy_mm_dd, "%Y-%m-%d").strftime("%b %d")


def term_slider_positions(conn, date_str: str, symbol: str) -> list:
    """Return ordered list of available snapshot timestamps for the slider.

    Thin wrapper over db.list_term_timestamps_for_date — kept as a public
    module-level function so the slider behavior can be unit-tested
    independently of Tk.
    """
    import gex_history_db as db
    return db.list_term_timestamps_for_date(conn, date_str, symbol)


def draw_term_heatmap(ax, rows: list, *,
                     colormap_neg: str,
                     colormap_mid: str,
                     colormap_pos: str,
                     text_color_threshold: float = 0.4,
                     strike_band_pct: float = 0.012,
                     chrome: dict | None = None) -> None:
    """Render a strike x expiration Net GEX heatmap on `ax`.

    rows: list of dicts from db.load_term_snapshot() — each row has keys
        expiration_date, strike, call_gex_usd, put_gex_usd, net_gex_usd,
        underlying_price.

    On empty `rows`, draws a centered placeholder and returns.

    Color scheme: diverging colormap from negative -> midpoint -> positive,
    with TwoSlopeNorm centered on 0 so white = $0 regardless of asymmetric
    +/- ranges. Cell text uses white on saturated cells (|v| > threshold * max),
    black on faded cells.

    Strike axis reversed (high strikes at top, matching the SpotGamma
    reference).

    `strike_band_pct`: keep only strikes within ±band of the underlying.
    Schwab returns ~250+ strikes per expiration (well-OTM included), and
    most of them have OI=0 — rendering all of them collapses the
    visible cells into an unreadable strip. Default 1.2% gives ~30
    strikes for an SPX-sized underlying.
    """
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

    ax.clear()
    if not rows:
        ax.text(0.5, 0.5, "No data for selected snapshot",
                ha="center", va="center", transform=ax.transAxes,
                color="#888", fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
        return

    # Filter strikes to a band around the underlying. Use a generous
    # absolute floor (30 points) so low-priced symbols still get a
    # readable window.
    underlying = rows[0]["underlying_price"]
    band = max(underlying * strike_band_pct, 30.0)
    lo, hi = underlying - band, underlying + band
    rows = [r for r in rows if lo <= r["strike"] <= hi]
    if not rows:
        ax.text(0.5, 0.5,
                f"No strikes within ±{band:.0f} of {underlying:,.1f}",
                ha="center", va="center", transform=ax.transAxes,
                color="#888", fontsize=11)
        ax.set_xticks([]); ax.set_yticks([])
        return

    exps = sorted({r["expiration_date"] for r in rows})
    strikes = sorted({r["strike"] for r in rows}, reverse=True)  # high at top
    by_key = {(r["expiration_date"], r["strike"]): r for r in rows}

    M = np.zeros((len(strikes), len(exps)))
    for i, K in enumerate(strikes):
        for j, e in enumerate(exps):
            r = by_key.get((e, K))
            if r is not None:
                M[i, j] = r["net_gex_usd"]

    mx = max(abs(M.min()), abs(M.max())) or 1.0
    cmap = LinearSegmentedColormap.from_list(
        "term_div", [colormap_neg, colormap_mid, colormap_pos])
    norm = TwoSlopeNorm(vmin=-mx, vcenter=0.0, vmax=mx)

    x_edges = np.arange(len(exps) + 1)
    y_edges = np.arange(len(strikes) + 1)
    ax.pcolormesh(x_edges, y_edges, M, cmap=cmap, norm=norm, shading="flat")

    # Cell text labels
    for i, K in enumerate(strikes):
        for j, e in enumerate(exps):
            v = M[i, j]
            if v == 0:
                continue
            label = _format_dollars(v)
            text_color = ("#ffffff" if abs(v) > text_color_threshold * mx
                          else "#101010")
            ax.text(j + 0.5, i + 0.5, label,
                    ha="center", va="center",
                    color=text_color, fontsize=9)

    ax.set_xticks(np.arange(len(exps)) + 0.5)
    ax.set_xticklabels([_short_date(e) for e in exps])
    ax.set_yticks(np.arange(len(strikes)) + 0.5)
    ax.set_yticklabels([f"{int(K)}" for K in strikes])
    ax.set_xlim(0, len(exps))
    ax.set_ylim(0, len(strikes))
    c = chrome or {"plot_bg": "#0d1730", "fg_dim": "#cccccc", "border": "#555"}
    ax.set_ylabel("Strike Price", color=c["fg_dim"], fontsize=10)

    # Theme-aware tick + spine colors — matplotlib defaults are black, which
    # is invisible against the dark-navy figure background.
    ax.tick_params(axis="both", colors=c["fg_dim"], labelsize=9)
    ax.set_facecolor(c["plot_bg"])
    for spine in ax.spines.values():
        spine.set_color(c["border"])


def compute_mvc(rows: list, expiration: str | None = None) -> float | None:
    """Return the strike with the largest |net_gex_usd|.

    If `expiration` is given, restrict to rows for that expiration only.
    Otherwise sum net_gex per strike across all expirations and pick
    the strike with the largest absolute sum (the "Most Valuable
    Contract" across the term structure).

    Returns None when there are no matching rows.
    """
    if not rows:
        return None
    if expiration is not None:
        relevant = [r for r in rows if r["expiration_date"] == expiration]
        if not relevant:
            return None
        return max(relevant, key=lambda r: abs(r["net_gex_usd"]))["strike"]
    by_strike: dict = {}
    for r in rows:
        by_strike[r["strike"]] = by_strike.get(r["strike"], 0.0) + r["net_gex_usd"]
    return max(by_strike.items(), key=lambda kv: abs(kv[1]))[0]


def pinch_flag_text(state):
    """Compact status-flag text + color for the Dealer Pinch panel.

    Returns ``("", None)`` when there's no usable state. Armed → a gold
    headline with regime/node/confidence; otherwise a dim "watching N/4" with
    the per-condition marks.
    """
    if not state or not (state.get("node") or {}).get("strike"):
        return "", None

    def _m(v):
        return "✓" if v is True else ("·" if v is None else "✗")

    c = state.get("conditions", {})
    chk = (f"D{_m(c.get('c1'))} N{_m(c.get('c2'))} "
           f"IV{_m(c.get('c3a'))} RV{_m(c.get('c3b'))}")
    node = state["node"]["strike"]
    if state.get("armed"):
        return (f"🧲 PINCH {state.get('regime', '')} @ {node:,.0f} · "
                f"{state.get('confidence', 0):.0f}%  [{chk}]", GOLD)
    n_met = sum(1 for v in c.values() if v is True)
    return (f"🧲 Pinch: watching {n_met}/4  [{chk}]", FG_DIM)


def build_chart_style_vars(defaults):
    """Build the chart-style tk-var dict from a ``defaults`` spec.

    Each value is a dict that may contain any of ``color``/``size``/
    ``thickness``/``linestyle`` — all OPTIONAL. Entries omit keys they don't
    use (e.g. a size-only "Level Label Text"), so every prop is built
    conditionally. Building ``color`` unconditionally used to raise
    ``KeyError: 'color'`` for size-only entries and crash GammaWindow init.

    Returns ``{key: {prop: tk.Variable}}``. Requires a live Tk root.

    ``tkinter`` is imported LAZILY here on purpose: this is the only GUI-bound
    helper left in the engine module, and headless importers
    (``services/options_svc``, ``gex_collector``, ``scanner_engine``) must not
    pay for a GUI toolkit just to import ``gamma_tool``. See
    ``tests/test_gamma_tool_headless.py``.
    """
    import tkinter as tk

    stl = {}
    for key, defs in defaults.items():
        entry = {}
        if "color" in defs:
            entry["color"] = tk.StringVar(value=defs["color"])
        if "size" in defs:
            entry["size"] = tk.DoubleVar(value=defs["size"])
        if "thickness" in defs:
            entry["thickness"] = tk.DoubleVar(value=defs["thickness"])
        if "linestyle" in defs:
            entry["linestyle"] = tk.StringVar(value=defs["linestyle"])
        stl[key] = entry
    return stl
