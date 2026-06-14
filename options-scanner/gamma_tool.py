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
import tkinter as tk
from tkinter import ttk, colorchooser
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

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.ticker as mticker
import matplotlib.patheffects as pe
from matplotlib.patches import FancyBboxPatch
from matplotlib.colors import LinearSegmentedColormap, to_rgba
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
        zero_dte_contracts = []  # list[tuple[dict, str]] for project_0dte_pressure

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
                             option_type),
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
                zero_dte_contracts, spot, hours_to_close,
            )
        else:
            net_now = net_proj = pressure = None

        return {
            "spot": spot,
            "gex": dex,
            "strike_count": len(dex),
            "net_delta_0dte": net_now,
            "projected_net_delta_close": net_proj,
            "hedge_pressure": pressure,
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
                call_gex = oi_c * gamma_c * (S ** 2) * 100
                cell = per_strike.setdefault(K, {
                    "call_gex_usd": 0.0, "put_gex_usd": 0.0, "net_gex_usd": 0.0,
                })
                cell["call_gex_usd"] = call_gex

            for strike_key, contracts in put_map.get(exp_key, {}).items():
                K = float(strike_key)
                p = contracts[0] if contracts else {}
                gamma_p = float(p.get("gamma") or 0.0)
                oi_p = float(p.get("openInterest") or 0)
                put_gex = oi_p * gamma_p * (S ** 2) * 100
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
                             option_type),
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
                zero_dte_contracts, spot, hours_to_close,
            )
        else:
            net_now = net_proj = pressure = None

        dex_result = {
            "spot": spot,
            "gex": dex,
            "strike_count": len(dex),
            "net_delta_0dte": net_now,
            "projected_net_delta_close": net_proj,
            "hedge_pressure": pressure,
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
            return result

        net_total = sum(v["net"] for v in gex.values())

        pos_items = [(s, v["net"]) for s, v in gex.items() if v["net"] > 0]
        neg_items = [(s, v["net"]) for s, v in gex.items() if v["net"] < 0]
        top_pos = max(pos_items, key=lambda x: x[1])[0] if pos_items else None
        top_neg = min(neg_items, key=lambda x: x[1])[0] if neg_items else None

        # Flip point: linear interpolation where net crosses zero near spot.
        # Collect all crossings within ±3% band, then pick nearest to spot.
        strikes = sorted(gex.keys())
        flip = None
        candidates = []
        for i in range(len(strikes) - 1):
            s1, s2 = strikes[i], strikes[i + 1]
            v1, v2 = gex[s1]["net"], gex[s2]["net"]
            if v1 * v2 <= 0 and (v2 - v1) != 0:
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

def get_directional_walls(gex_data, spot):
    """Call wall (above spot) and put wall (below spot) by GEX magnitude.

    Call GEX is stored positive, put GEX negative. The call wall is the
    largest call-side strike above spot; the put wall is the largest put-side
    strike below spot (most-negative ``put`` entry). Either side is ``None``
    when no strike sits on the correct side of spot.

    Returns ``{"call_wall": strike|None, "put_wall": strike|None}``.
    """
    out = {"call_wall": None, "put_wall": None}
    if not gex_data or spot is None or spot <= 0:
        return out
    grid = gex_data.get("gex", {})
    if not grid:
        return out

    above = [(s, v.get("call", 0.0)) for s, v in grid.items() if s > spot]
    below = [(s, v.get("put", 0.0)) for s, v in grid.items() if s < spot]
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
    """Return the strike where the DEX curve, uniformly shifted by hedge_pressure,
    crosses zero — i.e. the projected EOD delta-flip.

    Same algorithm as the chart's dashed projected-flip line. Returns the
    crossing closest to ``spot``, or None if no crossing or inputs missing.
    """
    grid = data.get("gex") or {} if data else {}
    hedge = data.get("hedge_pressure") if data else None
    if not grid or hedge is None:
        return None
    n = len(grid)
    if n == 0:
        return None
    per_strike_shift = hedge / n
    shifted = {k: v["net"] + per_strike_shift for k, v in grid.items()}
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

    # Flip point: where net crosses zero near spot. _calc_flip_point is a
    # staticmethod on GammaWindow defined later in the module; this call is
    # resolved at runtime so the forward reference is fine.
    flip_point = GammaWindow._calc_flip_point(gex, spot)

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
    """
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GammaWindow — Toplevel GUI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class GammaWindow(tk.Toplevel):
    """GEX visualization window spawned from the dashboard."""

    REFRESH_INTERVAL = 300  # seconds

    def __init__(self, master, client, symbol="$SPX"):
        super().__init__(master)
        self.title("GEX Scanner — Gamma Exposure by Strike")
        self.geometry("1200x720")
        self.configure(bg=BG_MAIN)
        self.minsize(900, 500)
        self._chrome = theme.chrome()
        self._trading = theme.trading()

        self._client = client
        self._engine = GammaEngine()
        self._stop_event = threading.Event()
        self._refresh_event = threading.Event()
        self._client_lock = threading.Lock()
        self._collector_thread = None
        self._collector_owner = f"gamma:{os.getpid()}"
        self._collector_external = False
        # Dealer Pinch detector: cached daily candles (vol index + underlying)
        # and the latest computed state (rendered by the status panel).
        self._pinch_hist_cache = {}
        self._last_pinch_state = None
        self._countdown = self.REFRESH_INTERVAL
        self._last_em = None

        # ── Intraday history (read from SQLite, written by gex_collector) ──
        try:
            self._db = _history_db.connect(read_only=True)
        except sqlite3.OperationalError:
            self._db = None  # collector DB doesn't exist yet
        self._show_history_var = tk.BooleanVar(value=True)
        self._show_heatmap_var = tk.BooleanVar(value=True)

        # ── Config vars ──
        self._symbol_var = tk.StringVar(value=symbol)
        self._view_var = tk.StringVar(value="gex")   # "gex" | "charm" | "dex" | "vanna" | "term"
        self._display_var = tk.StringVar(value="net")
        self._grouping_var = tk.DoubleVar(value=1)
        self._show_prev_var = tk.BooleanVar(value=False)
        self._show_open_var = tk.BooleanVar(value=False)
        self._show_em_var = tk.BooleanVar(value=True)
        self._formula_var = tk.StringVar(value="oi")
        self._charm_data = None  # charm snapshot (same structure as GEX)
        self._analyze_inflight = False  # guard against overlapping _analyze runs
        self._dex_data = None   # DEX snapshot (same structure as GEX)
        # Prior + open snapshots for charm/DEX, parallelling engine.previous /
        # engine.market_open (which only track GEX). Used by the Analyze prompt
        # to surface intraday delta-change and open-vs-now context.
        self._prev_charm_data = None
        self._open_charm_data = None
        self._prev_dex_data = None
        self._open_dex_data = None
        self._vanna_data = None  # vanna snapshot (same structure as GEX)
        self._prev_vanna_data = None
        self._open_vanna_data = None
        self._last_close_cache = {}        # {symbol: close_float or None}
        self._last_close_attempted = set() # {symbol} — prevents retry spam on failure
        # Forward-band cache: {(symbol, view): (last_fetch_ts, strikes, times, matrix)}
        self._fwd_cache = {}

        # Scheduled auto-analyze — fires _analyze(auto=True) at fixed times CT.
        # Instance list (not module constant) so future UI work can expose it.
        self._auto_analyze_times = [(8, 19), (8, 44), (9, 59), (12, 59), (14, 59)]
        self._auto_analyze_timer_id = None

        # Bar hover state — populated by _redraw's successful path, consumed by
        # _on_bar_hover. Empty when no data is rendered.
        self._hover_strikes = []       # list[float]
        self._hover_grid = {}          # {strike: {"call", "put", "net"}}
        self._hover_bar_height = 1.0   # float — hit-test tolerance
        self._hover_view = "gex"       # "gex" | "charm" | "dex" | "vanna"
        self._hover_annotation = None  # matplotlib annotation artist (lazy)

        self._setup_win = None  # Tracks the Chart Setup Toplevel for single-instance

        # ── Chart style vars (configurable via Setup popup) ──
        self._stl = {}
        _defaults = {
            "GEX+ Bars":        {"color": self._trading["gex_pos"],   "size": 0.85},
            "Charm+ Bars":      {"color": self._trading["charm_pos"], "size": 0.85},
            "DEX+ Bars":        {"color": self._trading["dex_pos"],   "size": 0.85},
            "Vanna+ Bars":      {"color": self._trading["vanna_pos"], "size": 0.85},
            "Negative Bars":    {"color": self._trading["gex_neg"],   "size": 0.85},
            "Ghost Bars":       {"color": self._trading["dex_pos"],   "size": 0.25},  # size = alpha
            "Spot Line":        {"color": self._trading["spot"],          "thickness": 0.3, "linestyle": "--"},
            "Proj Flip Line":   {"color": self._trading["proj_flip"],     "thickness": 0.6, "linestyle": ":"},
            "DEX Proj Flip":    {"color": self._trading["dex_proj_flip"], "thickness": 0.6, "linestyle": "--"},
            "EM Lines":         {"color": self._trading["em_line"],       "thickness": 0.5, "linestyle": "--"},
            "Max Pain Line":    {"color": "#3fd0c9",                       "thickness": 0.7, "linestyle": "-."},
            "Max Pain Text":    {"color": "#3fd0c9",                       "size": 9},
            "Call Wall Line":   {"color": self._trading["gex_pos"],        "thickness": 0.7, "linestyle": "-"},
            "Put Wall Line":    {"color": self._trading["gex_neg"],        "thickness": 0.7, "linestyle": "-"},
            "Level Label Text": {"size": 7},
            "Term Hover Text":  {"color": "#ffffff",                       "size": 8},
            "Spot Text":        {"color": self._trading["spot"],          "size": 12},
            "Proj Flip Text":   {"color": self._trading["proj_flip"],     "size": 12},
            "DEX Flip Text":    {"color": self._trading["dex_proj_flip"], "size": 12},
            "EM Text":          {"color": self._trading["em_text"],       "size": 7},
            "Title":            {"color": FG_PRIMARY, "size": 12},
            "Axis Ticks":       {"color": FG_DIM,     "size": 10},
            "Axis Labels":      {"color": FG_DIM,     "size": 9},
            "Flip Line":        {"color": WHITE,      "thickness": 1.6, "linestyle": "-"},
            "Top+ Line":        {"color": self._trading["gex_pos"], "thickness": 1.3, "linestyle": ":"},
            "Top- Line":        {"color": self._trading["gex_neg"], "thickness": 1.3, "linestyle": ":"},
            "Zero Line":        {"color": FG_DIM,     "thickness": 0.8},
            "Grid Lines":       {"color": FG_DIM,     "thickness": 0.5},
            "Heatmap Positive": {"color": self._trading["heatmap_pos"]},
            "Heatmap Negative": {"color": self._trading["heatmap_neg"]},
            "Heatmap Midpoint": {"color": self._trading["heatmap_mid"]},
            # Term-structure heatmap — separate palette from the time-evolution
            # heatmap above so users can tune the two views independently.
            "Term Heatmap Negative":  {"color": self._trading["term_heatmap_neg"]},
            "Term Heatmap Midpoint":  {"color": self._trading["term_heatmap_mid"]},
            "Term Heatmap Positive":  {"color": self._trading["term_heatmap_pos"]},
        }
        # Snapshot defaults (shallow-copy each entry) for Reset-to-Defaults
        # support in the Chart Setup popup (Task 3). We copy values because
        # _defaults is a local variable; we need it to survive after the
        # population loop finishes and __init__ returns.
        self._stl_defaults = {
            key: dict(defs) for key, defs in _defaults.items()
        }
        self._stl.update(build_chart_style_vars(_defaults))

        # Convenience aliases
        self._clr_gex_pos = self._stl["GEX+ Bars"]["color"]
        self._clr_charm_pos = self._stl["Charm+ Bars"]["color"]
        self._clr_dex_pos = self._stl["DEX+ Bars"]["color"]
        self._clr_vanna_pos = self._stl["Vanna+ Bars"]["color"]
        self._clr_neg = self._stl["Negative Bars"]["color"]
        self._clr_em = self._stl["EM Lines"]["color"]
        self._clr_spot = self._stl["Spot Line"]["color"]
        self._clr_proj = self._stl["Proj Flip Line"]["color"]

        # Overlay any saved chart-style values from data/chart_style.json
        # over the defaults we just populated. Silent on first-run (no file).
        self._load_chart_style()

        self._build_ui()
        self._start_worker()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Arm the daily auto-analyze scheduler.
        self._schedule_next_auto_analyze()

    # ── UI Construction ──

    def _build_ui(self):
        container = tk.Frame(self, bg=BG_MAIN)
        container.pack(fill="both", expand=True, padx=3, pady=3)

        # Pack all four frames first in visual order so Tk's layout is correct
        # regardless of the order we populate children below.
        top_bar = tk.Frame(container, bg=BG_PANEL)
        top_bar.pack(side="top", fill="x", pady=(0, 2))
        view_bar = tk.Frame(container, bg=BG_PANEL)
        view_bar.pack(side="top", fill="x", pady=(0, 2))
        bottom = tk.Frame(container, bg=BG_PANEL)
        bottom.pack(side="bottom", fill="x", pady=(2, 0))
        chart_frame = tk.Frame(container, bg=BG_MAIN)
        chart_frame.pack(side="top", fill="both", expand=True)

        # Populate in DEPENDENCY order, not visual order. _build_view_toggle
        # ends with self._set_view(...) which fires a full _redraw, and
        # _redraw touches:
        #   - self._ax_bars / self._ax_heat   (from _build_chart)
        #   - self._pressure_frame + _pressure_label_*  (from _build_bottom_strip)
        #   - self._status_label               (from _build_bottom_strip)
        # So chart AND bottom strip must be built before view toggle fires.
        self._build_top_bar(top_bar)
        self._build_chart(chart_frame)     # creates _ax_bars, _ax_heat
        self._build_bottom_strip(bottom)   # creates _pressure_frame, _status_label, buttons
        self._build_view_toggle(view_bar)  # LAST — its _set_view → _redraw is now safe

    def _save_chart_style(self):
        """Flatten self._stl's tk vars into a dict, dump to data/chart_style.json.

        Called from every Chart Setup popup edit callback (Task 3). Failures
        are logged (warning level) but never raised — persistence is
        best-effort and must not break the UI edit flow.
        """
        path = Path(__file__).parent / "data" / "chart_style.json"
        path.parent.mkdir(exist_ok=True)
        dump = {
            key: {prop: var.get() for prop, var in entry.items()}
            for key, entry in self._stl.items()
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(dump, f, indent=2)
        except Exception as e:
            log.warning("Failed to save chart style: %s", e)

    def _load_chart_style(self):
        """Overlay saved values from data/chart_style.json onto _stl defaults.

        Silent on first-run (no file). Tolerant to:
          - Missing keys (defaults added after file was saved) — skip, keep default
          - Unknown keys (future file loaded by older code) — skip silently
          - Type mismatches on individual props — skip that prop, continue
          - Corrupt JSON — log warning, fall back to defaults entirely

        Never raises — __init__ must not fail because of a bad config file.
        """
        path = Path(__file__).parent / "data" / "chart_style.json"
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
        except Exception as e:
            log.warning("Failed to load chart style (using defaults): %s", e)
            return
        if not isinstance(saved, dict):
            log.warning("chart_style.json is not a dict; using defaults")
            return
        for key, props in saved.items():
            if key not in self._stl:
                continue  # Unknown element — skip silently
            if not isinstance(props, dict):
                continue  # Malformed entry — skip
            for prop, value in props.items():
                if prop in self._stl[key]:
                    try:
                        self._stl[key][prop].set(value)
                    except Exception:
                        pass  # Type mismatch or tk error — skip this prop

    def _reset_chart_style(self):
        """Restore every _stl var to its default value, redraw, and persist.

        Used by the Reset-to-Defaults button in the Chart Setup popup.
        Overwrites data/chart_style.json with default values so the next
        launch also loads defaults.
        """
        for key, defs in self._stl_defaults.items():
            if key not in self._stl:
                continue
            for prop, value in defs.items():
                if prop in self._stl[key]:
                    try:
                        self._stl[key][prop].set(value)
                    except Exception:
                        pass
        self._redraw()
        self._save_chart_style()

    def _open_chart_setup(self):
        """Open a popup window with dropdown-driven style controls.

        Single-instance: subsequent opens while a popup exists lift the
        existing window. Every edit live-saves to data/chart_style.json
        via self._save_chart_style() in each callback.
        """
        if getattr(self, "_setup_win", None) is not None \
                and self._setup_win.winfo_exists():
            self._setup_win.lift()
            return

        win = tk.Toplevel(self)
        win.title("Chart Setup")
        win.configure(bg=BG_MAIN)
        win.geometry("340x420")
        win.resizable(False, False)
        self._setup_win = win

        tk.Label(win, text="Element:", bg=BG_MAIN, fg=FG_PRIMARY,
                 font=(FONT, 10)).pack(anchor="w", padx=12, pady=(12, 2))
        element_var = tk.StringVar(value=list(self._stl.keys())[0])
        element_cb = ttk.Combobox(win, textvariable=element_var,
                                  values=list(self._stl.keys()),
                                  state="readonly", width=30)
        element_cb.pack(padx=12, pady=(0, 8))

        controls = tk.Frame(win, bg=BG_PANEL)
        controls.pack(fill="both", expand=True, padx=12, pady=4)

        def _populate(event=None):
            """Rebuild the controls frame for the currently-selected element."""
            for w in controls.winfo_children():
                w.destroy()
            key = element_var.get()
            entry = self._stl[key]
            row_pad = {"padx": 8, "pady": 6}
            lbl_kw = {"bg": BG_PANEL, "fg": FG_PRIMARY, "font": (FONT, 9)}

            # Color picker
            if "color" in entry:
                row = tk.Frame(controls, bg=BG_PANEL)
                row.pack(fill="x", **row_pad)
                tk.Label(row, text="Color", **lbl_kw).pack(side="left")
                swatch = tk.Button(row, width=4, bg=entry["color"].get(),
                                   relief="flat", cursor="hand2",
                                   activebackground=entry["color"].get())
                swatch.pack(side="right")
                cvar = entry["color"]

                def _pick(v=cvar, s=swatch):
                    result = colorchooser.askcolor(
                        color=v.get(), title="Choose color")
                    if result and result[1]:
                        v.set(result[1])
                        s.configure(bg=result[1], activebackground=result[1])
                        self._redraw()
                        self._save_chart_style()

                swatch.configure(command=_pick)

            # Size / Alpha slider
            if "size" in entry:
                row = tk.Frame(controls, bg=BG_PANEL)
                row.pack(fill="x", **row_pad)
                is_bar = "Bars" in key
                is_ghost = (key == "Ghost Bars")
                lbl_text = "Alpha" if is_ghost else "Size"
                tk.Label(row, text=lbl_text, **lbl_kw).pack(side="left")
                size_var = entry["size"]
                if is_ghost:
                    from_, to_, res = 0.05, 1.0, 0.05
                elif is_bar:
                    from_, to_, res = 0.5, 1.5, 0.05
                else:
                    from_, to_, res = 4, 24, 1
                scale = tk.Scale(
                    row, from_=from_, to=to_, resolution=res,
                    orient="horizontal", variable=size_var,
                    bg=BG_PANEL, fg=FG_PRIMARY, troughcolor=BG_INPUT,
                    highlightthickness=0, length=150,
                    command=lambda v: (self._redraw(),
                                       self._save_chart_style()),
                )
                scale.pack(side="right")

            # Thickness slider
            if "thickness" in entry:
                row = tk.Frame(controls, bg=BG_PANEL)
                row.pack(fill="x", **row_pad)
                tk.Label(row, text="Thickness", **lbl_kw).pack(side="left")
                thick_var = entry["thickness"]
                scale = tk.Scale(
                    row, from_=0.1, to=4.0, resolution=0.1,
                    orient="horizontal", variable=thick_var,
                    bg=BG_PANEL, fg=FG_PRIMARY, troughcolor=BG_INPUT,
                    highlightthickness=0, length=150,
                    command=lambda v: (self._redraw(),
                                       self._save_chart_style()),
                )
                scale.pack(side="right")

            # Line-style radios
            if "linestyle" in entry:
                row = tk.Frame(controls, bg=BG_PANEL)
                row.pack(fill="x", **row_pad)
                tk.Label(row, text="Line Style", **lbl_kw).pack(side="left")
                ls_var = entry["linestyle"]
                ls_options = [("-", "Solid"), ("--", "Dashed"),
                              (":", "Dotted"), ("-.", "Dash-Dot")]
                ls_frame = tk.Frame(row, bg=BG_PANEL)
                ls_frame.pack(side="right")
                for val, label in ls_options:
                    tk.Radiobutton(
                        ls_frame, text=label, variable=ls_var, value=val,
                        bg=BG_PANEL, fg=FG_PRIMARY, selectcolor=BG_INPUT,
                        activebackground=BG_PANEL, activeforeground=WHITE,
                        font=(FONT, 8),
                        command=lambda: (self._redraw(),
                                         self._save_chart_style()),
                    ).pack(side="left", padx=2)

        element_cb.bind("<<ComboboxSelected>>", _populate)
        _populate()

        # Bottom button row — Reset on the left, Close on the right.
        btn_row = tk.Frame(win, bg=BG_MAIN)
        btn_row.pack(fill="x", padx=12, pady=(8, 12))
        tk.Button(
            btn_row, text="Reset to Defaults",
            command=lambda: (self._reset_chart_style(), _populate()),
            bg=BG_INPUT, fg=FG_PRIMARY, activebackground=BG_PANEL,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        ).pack(side="left")
        tk.Button(
            btn_row, text="Close", command=win.destroy,
            bg=BG_INPUT, fg=FG_PRIMARY, activebackground=BG_PANEL,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        ).pack(side="right")

    def _build_top_bar(self, parent):
        # Hamburger menu — collapses the former config panel into a single
        # dropdown attached to the top-left menubutton.
        self._hamburger_menu = tk.Menu(
            parent, tearoff=0,
            bg=BG_PANEL, fg=FG_PRIMARY,
            activebackground=BG_INPUT, activeforeground=WHITE,
        )

        # selectcolor for radio/check indicators — Tk defaults to black on
        # every entry, which is invisible against BG_PANEL (theme chrome panel).
        # Must be set per-entry on tk.Menu (not widget-wide). WHITE keeps
        # the currently-selected option clearly marked against the dark theme.
        _sel = {"selectcolor": WHITE}

        # Display radios.
        for text, val in [("Display: Net", "net"),
                          ("Display: Calls Only", "call"),
                          ("Display: Puts Only", "put")]:
            self._hamburger_menu.add_radiobutton(
                label=text, variable=self._display_var, value=val,
                command=self._redraw, **_sel,
            )
        self._hamburger_menu.add_separator()

        # Grouping radios.
        for g in (0.1, 0.5, 1, 5, 10, 25):
            self._hamburger_menu.add_radiobutton(
                label=f"Grouping: {g}", variable=self._grouping_var,
                value=float(g), command=self._redraw, **_sel,
            )
        self._hamburger_menu.add_separator()

        # GEX Formula radios — formula changes require a refresh, not just a
        # redraw, since the underlying exposure numbers are recomputed.
        self._hamburger_menu.add_radiobutton(
            label="GEX Formula: OI", variable=self._formula_var,
            value="oi", command=self._on_formula_change, **_sel,
        )
        self._hamburger_menu.add_radiobutton(
            label="GEX Formula: Volume", variable=self._formula_var,
            value="volume", command=self._on_formula_change, **_sel,
        )
        self._hamburger_menu.add_separator()

        # Overlay toggles — preserve every checkbox from the old config panel
        # so all existing redraw branches keep firing.
        self._hamburger_menu.add_checkbutton(
            label="Show Previous", variable=self._show_prev_var,
            command=self._redraw, **_sel,
        )
        self._hamburger_menu.add_checkbutton(
            label="Show Market Open", variable=self._show_open_var,
            command=self._redraw, **_sel,
        )
        self._hamburger_menu.add_checkbutton(
            label="Show +/-1s Straddle", variable=self._show_em_var,
            command=self._redraw, **_sel,
        )
        self._hamburger_menu.add_checkbutton(
            label="Show History Overlay", variable=self._show_history_var,
            command=self._redraw, **_sel,
        )
        self._hamburger_menu.add_checkbutton(
            label="Show Heatmap", variable=self._show_heatmap_var,
            command=self._redraw, **_sel,
        )
        self._hamburger_menu.add_separator()
        self._hamburger_menu.add_command(
            label="\u2699 Chart Setup\u2026",
            command=self._open_chart_setup,
        )
        self._hamburger_menu.add_separator()
        self._hamburger_menu.add_command(
            label="\u2139  What do these levels mean?",
            command=self._open_key_levels_doc,
        )

        # tk.Menubutton + menu= is flaky on Windows (clicking doesn't post the
        # menu on recent Tk builds). Use a regular Button that calls tk_popup()
        # with the button's screen coords — reliable across platforms.
        self._hamburger_btn = tk.Button(
            parent, text="\u2630",
            bg=BG_PANEL, fg=FG_PRIMARY, activebackground=BG_INPUT,
            activeforeground=WHITE, font=(FONT, 14, "bold"),
            relief="flat", cursor="hand2", width=3,
            command=self._show_hamburger_menu,
        )
        self._hamburger_btn.pack(side="left", padx=(6, 4), pady=3)

        # Symbol combobox.
        sym_cb = ttk.Combobox(
            parent, textvariable=self._symbol_var,
            values=["$SPX", "$VIX", "SPY", "QQQ"],
            state="readonly", width=8,
        )
        sym_cb.pack(side="left", padx=4, pady=3)
        sym_cb.bind("<<ComboboxSelected>>", lambda e: self._on_symbol_change())

        # Header label doubles as the former "config_title" + status summary.
        # _set_view still configures ._config_title.text on view change, so keep
        # that attribute pointing at this label.
        self._config_title = tk.Label(
            parent, text="GEX Settings", bg=BG_PANEL, fg=CYAN,
            font=(FONT, 10, "bold"),
        )
        self._config_title.pack(side="left", padx=(8, 0), pady=3)

        # Countdown on the far right of the top bar.
        self._countdown_lbl = tk.Label(
            parent, text="Next refresh: --:--", bg=BG_PANEL, fg=FG_DIM,
            font=(FONT, 9),
        )
        self._countdown_lbl.pack(side="right", padx=(4, 8), pady=3)

        # Explain action button — a raised, gold 3D button (deliberately NOT
        # styled like the flat view-toggle tabs, since it's an action, not a
        # view). Sits just left of the refresh countdown.
        self._btn_explain = tk.Button(
            parent, text="\u2753 Explain",
            command=self._show_explain,
            bg="#FFD700", fg="#1a1a1a",
            activebackground="#FFE34D", activeforeground="#1a1a1a",
            font=(FONT, 9, "bold"), relief="raised", bd=3,
            padx=10, pady=2, cursor="hand2",
        )
        self._btn_explain.pack(side="right", padx=(0, 4), pady=3)
        # (Dealer Pinch is folded into the Explain page; no separate button.)

        # Status rows promoted from the old bottom strip — packed below the
        # main top-bar controls so the chart can reclaim the vertical space.
        status_frame = tk.Frame(parent, bg=BG_PANEL)
        status_frame.pack(side="bottom", fill="x")

        # Row 1: free-form status (symbol | spot | DTE | strike count | formula)
        # and refresh/analyze feedback. Right-aligned: 0-DTE pressure panel
        # (DEX view only).
        row1 = tk.Frame(status_frame, bg=BG_PANEL)
        row1.pack(side="top", fill="x")

        self._status_lbl = tk.Label(
            row1, text="Initializing...", bg=BG_PANEL, fg=FG_DIM,
            font=(FONT, 9), anchor="w", justify="left",
        )
        self._status_lbl.pack(side="left", anchor="w", padx=8, pady=(2, 0))

        # 0-DTE pressure panel lives on the right side of row 1 (DEX view
        # only). _update_pressure_panel handles pack/pack_forget.
        self._pressure_frame = tk.Frame(row1, bg=BG_PANEL)
        self._pressure_label_hedge = tk.Label(
            self._pressure_frame, text="", bg=BG_PANEL, fg=FG_PRIMARY,
            font=(FONT, 9, "bold"),
        )
        self._pressure_label_proj = tk.Label(
            self._pressure_frame, text="", bg=BG_PANEL, fg=FG_PRIMARY,
            font=(FONT, 9),
        )
        self._pressure_label_now = tk.Label(
            self._pressure_frame, text="", bg=BG_PANEL, fg=FG_PRIMARY,
            font=(FONT, 9),
        )
        # Pack right-to-left so they read left-to-right: now | proj | hedge.
        self._pressure_label_hedge.pack(side="right", padx=(6, 8))
        self._pressure_label_proj.pack(side="right", padx=6)
        self._pressure_label_now.pack(side="right", padx=6)
        # Do NOT pack self._pressure_frame here — deferred to
        # _update_pressure_panel.

        # Term-view header label: 'Underlying X | MVC Y'. Only packed when
        # _show_term_view is active (see _show_term_header / _hide_term_header).
        self._term_header_lbl = tk.Label(
            row1, text="", bg=BG_PANEL, fg=CYAN,
            font=(FONT, 10, "bold"),
        )
        # Not packed by default — only shown in term view.

        # Row 2: collector health status (left) + view-aware key-levels
        # headline (right). Both packed into a shared sub-frame so the
        # headline can sit beside the status label on the same line.
        row2 = tk.Frame(status_frame, bg=BG_PANEL)
        row2.pack(side="top", fill="x")

        self._status_label = tk.Label(
            row2, text="", bg=BG_PANEL, fg=FG_DIM,
            font=(FONT, 9), anchor="w", justify="left",
        )
        self._status_label.pack(side="left", anchor="w", padx=8, pady=(0, 2))

        # Key-levels headline strip retired (values now render on the chart).
        # Reuse the right side of row 2 for the Dealer Pinch flag.
        self._pinch_lbl = tk.Label(
            row2, text="", bg=BG_PANEL, fg=FG_DIM,
            font=(FONT, 9), anchor="e", justify="right",
        )
        self._pinch_lbl.pack(side="right", anchor="e", padx=(6, 12), pady=(0, 2))

    def _pinch_candles(self, symbol):
        """Cached daily candles for ``symbol`` (refetched at most hourly).

        Used for the vol-index IV percentile and the underlying realized-vol
        trend. Network fetch is serialized on the shared client lock; callers
        run on the worker thread so the UI never blocks.
        """
        import time as _t
        ent = self._pinch_hist_cache.get(symbol)
        now = _t.time()
        if ent and (now - ent[0]) < 3600:
            return ent[1]
        candles = []
        try:
            from scanner_engine import fetch_price_history
            with self._client_lock:
                hist = fetch_price_history(self._client, symbol)
            candles = (hist or {}).get("candles") or []
        except Exception:
            log.debug("pinch candle fetch failed for %s", symbol, exc_info=True)
        self._pinch_hist_cache[symbol] = (now, candles)
        return candles

    def _compute_pinch_state(self, chain, spot, dte, expected_move,
                             gex_result=None, forced_hedge_dir=None):
        """Worker-thread Dealer Pinch evaluation. Fetches the vol-index IV
        percentile (SPX/SPY→$VIX, QQQ→$VXN) and the underlying RV trend, then
        calls the pure evaluator. Returns the state dict or None; never raises.
        """
        if chain is None or not spot:
            return None
        try:
            symbol = self._symbol_var.get()
            vix_sym = "$VXN" if symbol.upper() == "QQQ" else "$VIX"
            iv_pctile = None
            vc = self._pinch_candles(vix_sym)
            closes = [c["close"] for c in vc][-30:]
            if closes:
                iv_pctile = percentile_rank(closes, closes[-1])
            rv_trend = None
            uc = self._pinch_candles(symbol)
            if uc:
                rv_trend = realized_vol_trend(uc)
            node = dominant_oi_node(chain).get("node")
            pr = pin_risk(spot, node, expected_move) if node is not None else None
            flip = None
            if gex_result:
                try:
                    flip = GammaEngine.snapshot_summary(gex_result).get("flip")
                except Exception:
                    flip = None
            return evaluate_dealer_pinch(
                symbol=symbol, chain=chain, spot=spot, dte=dte,
                iv_pctile=iv_pctile, rv_trend=rv_trend, gex_flip=flip,
                pin_risk_score=pr, forced_hedge_dir=forced_hedge_dir)
        except Exception:
            log.debug("pinch state compute failed", exc_info=True)
            return None

    def _open_key_levels_doc(self):
        """Render docs/KEY_LEVELS.md to a styled HTML page and open it.

        Falls back to opening the raw markdown if rendering/writing fails.
        """
        import webbrowser
        from pathlib import Path
        md_path = Path(__file__).parent / "docs" / "KEY_LEVELS.md"
        if not md_path.exists():
            return
        try:
            import html_render
            md_text = md_path.read_text(encoding="utf-8")
            out_path = Path(__file__).parent / "data" / "key_levels.html"
            out_path.parent.mkdir(exist_ok=True)
            out_path.write_text(html_render.render_key_levels_html(md_text),
                                encoding="utf-8")
            webbrowser.open(out_path.as_uri())
        except Exception:
            log.exception("Key-levels HTML render failed; opening raw markdown")
            webbrowser.open(md_path.as_uri())

    def _show_hamburger_menu(self):
        """Post the hamburger menu at the bottom-left corner of its button.

        Called from self._hamburger_btn's command callback. Using tk_popup()
        explicitly is more reliable than tk.Menubutton on Windows Tk builds.
        """
        btn = self._hamburger_btn
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height()
        try:
            self._hamburger_menu.tk_popup(x, y)
        finally:
            # Release the grab so keyboard/focus returns to the window even
            # if the user dismisses the menu by clicking outside it.
            self._hamburger_menu.grab_release()

    def _build_view_toggle(self, parent):
        # Reparent GEX/Charm/DEX buttons into the top-level view bar. Styling
        # kwargs copy-pasted verbatim from the old _build_config so _set_view()
        # still toggles bg/fg correctly.
        self._btn_gex = tk.Button(
            parent, text="\u0393 GEX", width=10,
            command=lambda: self._set_view("gex"),
            bg=CYAN, fg=BG_MAIN, activebackground=CYAN,
            activeforeground=BG_MAIN, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._btn_gex.pack(side="left", expand=True, fill="x", padx=(6, 2), pady=3)

        self._btn_charm = tk.Button(
            parent, text="\u2202\u0394 Charm", width=10,
            command=lambda: self._set_view("charm"),
            bg=BG_INPUT, fg=FG_DIM, activebackground=BG_INPUT,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._btn_charm.pack(side="left", expand=True, fill="x", padx=2, pady=3)

        self._btn_dex = tk.Button(
            parent, text="\u0394 DEX", width=10,
            command=lambda: self._set_view("dex"),
            bg=BG_INPUT, fg=FG_DIM, activebackground=BG_INPUT,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._btn_dex.pack(side="left", expand=True, fill="x", padx=(2, 6), pady=3)

        self._btn_vanna = tk.Button(
            parent, text="\U0001D4B1 Vanna", width=10,
            command=lambda: self._set_view("vanna"),
            bg=BG_INPUT, fg=FG_DIM, activebackground=BG_INPUT,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._btn_vanna.pack(side="left", expand=True, fill="x", padx=2, pady=3)

        self._btn_term = tk.Button(
            parent, text="Term", width=10,
            command=lambda: self._set_view("term"),
            bg=BG_INPUT, fg=FG_DIM, activebackground=BG_INPUT,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._btn_term.pack(side="left", expand=True, fill="x", padx=(2, 6), pady=3)
        # Fix the _btn_dex padding so Term sits at the right edge
        self._btn_dex.pack_configure(padx=2)
        # Disabled when not on SPXW (the only symbol with collected term data)
        self._refresh_term_button_state()

        # Apply initial active/inactive styling.
        self._set_view(self._view_var.get())

    def _build_bottom_strip(self, parent):
        # Status rows were previously here; they've been promoted into the top
        # status bar so the chart can reclaim the vertical space. This strip
        # now holds only the action buttons.
        btn_frame = tk.Frame(parent, bg=BG_PANEL)
        btn_frame.pack(side="bottom", fill="x", pady=(2, 2))

        self._analyze_btn = tk.Button(
            btn_frame, text="\U0001f916 Analyze", command=self._analyze,
            bg="#2a1a4a", fg="#e0b0ff", activebackground="#3a2a6a",
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._analyze_btn.pack(side="right", padx=(4, 8), pady=2)

        self._refresh_btn = tk.Button(
            btn_frame, text="Refresh Now", command=self._trigger_refresh,
            bg=BG_INPUT, fg=CYAN, activebackground=BG_PANEL,
            activeforeground=WHITE, font=(FONT, 9, "bold"),
            relief="flat", cursor="hand2",
        )
        self._refresh_btn.pack(side="right", padx=(4, 2), pady=2)

    def _build_chart(self, parent):
        # High-DPI for crisp text/lines. Side-by-side subplots: bars on the
        # left (narrow), heatmap on the right (wide), sharing the price Y axis.
        self._fig = Figure(figsize=(14, 6), dpi=150, facecolor=BG_MAIN)
        self._ax_bars, self._ax_heat = self._fig.subplots(
            1, 2, sharey=True,
            gridspec_kw={"width_ratios": [1, 3], "wspace": 0.02},
        )
        # Preserve self._ax as alias for _ax_bars so legacy references
        # (save-analysis, pressure panel hooks, etc.) keep working.
        self._ax = self._ax_bars
        # Twin axis removed — time dimension now belongs on the heatmap (Task 9+).
        self._ax2 = None
        self._fig.subplots_adjust(left=0.05, right=0.93, top=0.94, bottom=0.10)

        self._canvas = FigureCanvasTkAgg(self._fig, master=parent)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)

        # Bar hover tooltip wiring — handler is idempotent against redraws.
        self._canvas.mpl_connect("motion_notify_event", self._on_bar_hover)

    # ── View Toggle ──

    def _set_view(self, view):
        """Toggle between GEX, Charm, DEX, and Term views."""
        if view == "term":
            self._view_var.set(view)
            # Term button highlighted, others dim
            self._btn_gex.configure(bg=BG_INPUT, fg=FG_DIM)
            self._btn_charm.configure(bg=BG_INPUT, fg=FG_DIM)
            self._btn_dex.configure(bg=BG_INPUT, fg=FG_DIM)
            self._btn_vanna.configure(bg=BG_INPUT, fg=FG_DIM)
            self._btn_term.configure(bg=CYAN, fg=BG_MAIN)
            self._show_term_view()
            return
        # Non-term views: ensure term axis is hidden, side-by-side restored
        self._restore_non_term_view()
        # Term button back to dim
        if hasattr(self, "_btn_term"):
            self._btn_term.configure(bg=BG_INPUT, fg=FG_DIM)
        self._view_var.set(view)
        # Reset all to inactive styling, then highlight the active one.
        self._btn_gex.configure(bg=BG_INPUT, fg=FG_DIM)
        self._btn_charm.configure(bg=BG_INPUT, fg=FG_DIM)
        self._btn_dex.configure(bg=BG_INPUT, fg=FG_DIM)
        self._btn_vanna.configure(bg=BG_INPUT, fg=FG_DIM)
        if view == "gex":
            self._btn_gex.configure(bg=self._clr_gex_pos.get(), fg=BG_MAIN)
            self._config_title.configure(text="GEX Settings")
        elif view == "charm":
            self._btn_charm.configure(bg=self._clr_charm_pos.get(), fg=BG_MAIN)
            self._config_title.configure(text="Charm Settings")
        elif view == "dex":
            self._btn_dex.configure(bg=self._clr_dex_pos.get(), fg=BG_MAIN)
            self._config_title.configure(text="DEX Settings")
        elif view == "vanna":
            self._btn_vanna.configure(bg=self._clr_vanna_pos.get(), fg=BG_MAIN)
            self._config_title.configure(text="Vanna Settings")
        self._redraw()

    def _is_spxw(self) -> bool:
        """Term view applies when the symbol picker is on the SPX-family
        underlying. The collector queries the $SPX chain (which includes
        SPXW weeklies); the symbol dropdown doesn't surface a separate
        $SPXW entry, so we enable Term whenever the picker is on $SPX."""
        sym = (self._symbol_var.get() or "").upper()
        return sym in ("$SPX", "$SPXW.X", "SPXW", "$SPXW")

    def _refresh_term_button_state(self):
        if not hasattr(self, "_btn_term"):
            return
        state = "normal" if self._is_spxw() else "disabled"
        self._btn_term.configure(state=state)
        # If currently showing term view for a non-SPX symbol, kick back to gex
        if state == "disabled" and self._view_var.get() == "term":
            self._set_view("gex")

    def _term_colors(self) -> dict:
        """Pull current Term Heatmap colors from the _stl style-var system so
        Chart Setup edits + persistence flow through automatically."""
        return dict(
            colormap_neg=self._stl["Term Heatmap Negative"]["color"].get(),
            colormap_mid=self._stl["Term Heatmap Midpoint"]["color"].get(),
            colormap_pos=self._stl["Term Heatmap Positive"]["color"].get(),
        )

    def _show_term_view(self):
        # Hide the side-by-side bars+heat axes
        if hasattr(self, "_ax_bars"):
            self._ax_bars.set_visible(False)
        if hasattr(self, "_ax_heat"):
            self._ax_heat.set_visible(False)
        # Lazy-create the term axis on first entry
        if not hasattr(self, "_ax_term") or self._ax_term is None:
            self._ax_term = self._fig.add_subplot(111)
        self._ax_term.set_visible(True)
        # Slider: build + show + refresh + render-at-slider-pos
        self._show_term_slider()
        self._refresh_term_slider()
        self._show_term_header()
        self._ensure_term_hover_connected()
        # Render at the current slider position (will be max after refresh)
        idx = self._term_slider_var.get()
        positions = getattr(self, "_term_positions", [])
        if 0 <= idx < len(positions):
            self._load_term_snapshot_at(positions[idx])
        else:
            # No snapshots — draw empty placeholder
            self._term_last_rows = []
            draw_term_heatmap(self._ax_term, [], **self._term_colors(),
                              chrome=self._chrome)
            self._update_term_header([])
            self._canvas.draw_idle()

    def _restore_non_term_view(self):
        """Hide _ax_term and restore _ax_bars/_ax_heat for gex/charm/dex views."""
        if hasattr(self, "_ax_term") and self._ax_term is not None:
            self._ax_term.set_visible(False)
        if hasattr(self, "_ax_bars"):
            self._ax_bars.set_visible(True)
        if hasattr(self, "_ax_heat"):
            self._ax_heat.set_visible(True)
        self._hide_term_slider()
        self._hide_term_header()
        if hasattr(self, "_term_tip") and self._term_tip is not None:
            self._term_tip.set_visible(False)

    def _show_term_header(self):
        if not self._term_header_lbl.winfo_ismapped():
            self._term_header_lbl.pack(side="right", padx=(8, 12), pady=2)

    def _hide_term_header(self):
        self._term_header_lbl.pack_forget()

    def _update_term_header(self, rows: list, hovered_exp: str | None = None):
        if not rows:
            self._term_header_lbl.configure(text="No snapshot data")
            return
        underlying = rows[0]["underlying_price"]
        mvc = compute_mvc(rows, expiration=hovered_exp)
        mvc_txt = f"{int(mvc)}" if mvc is not None else "--"
        self._term_header_lbl.configure(
            text=f"Underlying {underlying:,.1f}  |  MVC {mvc_txt}"
        )

    def _ensure_term_hover_connected(self):
        if getattr(self, "_term_hover_cid", None) is not None:
            return
        self._term_hover_cid = self._canvas.mpl_connect(
            "motion_notify_event", self._on_term_hover)

    def _on_term_hover(self, event):
        # Only active when viewing term and cursor is on _ax_term
        if self._view_var.get() != "term":
            return
        if event.inaxes is not getattr(self, "_ax_term", None):
            if hasattr(self, "_term_tip") and self._term_tip is not None:
                self._term_tip.set_visible(False)
                self._canvas.draw_idle()
            return
        rows = getattr(self, "_term_last_rows", None) or []
        if not rows:
            return
        # Apply the same strike-band filter the renderer uses so cursor
        # coordinates match the displayed grid (renderer drops out-of-band
        # strikes, so the un-filtered rows list has a different y-extent).
        underlying = rows[0]["underlying_price"]
        band = max(underlying * 0.012, 30.0)
        lo, hi = underlying - band, underlying + band
        rows = [r for r in rows if lo <= r["strike"] <= hi]
        if not rows:
            return
        exps = sorted({r["expiration_date"] for r in rows})
        strikes = sorted({r["strike"] for r in rows}, reverse=True)
        if not exps or not strikes or event.xdata is None or event.ydata is None:
            return
        j = int(event.xdata)
        i = int(event.ydata)
        if not (0 <= j < len(exps) and 0 <= i < len(strikes)):
            return
        exp = exps[j]
        K = strikes[i]
        cell = next(
            (r for r in rows
             if r["expiration_date"] == exp and r["strike"] == K),
            None,
        )
        if not cell:
            return
        from datetime import datetime, date
        try:
            dte = (datetime.strptime(exp, "%Y-%m-%d").date()
                   - date.today()).days
        except Exception:
            dte = "?"
        exp_short = datetime.strptime(exp, "%Y-%m-%d").strftime("%b %d")
        txt = (
            f"K {int(K)} | Exp {exp_short} ({dte}d) | "
            f"Net {_format_dollars(cell['net_gex_usd'])}   "
            f"Call {_format_dollars(cell['call_gex_usd'])}   "
            f"Put {_format_dollars(-cell['put_gex_usd'])}"
        )
        # Tooltip size/color are configurable via Chart Setup ("Term Hover
        # Text"); applied every hover so edits take effect immediately.
        hov = self._stl.get("Term Hover Text") if hasattr(self, "_stl") else None
        hov_sz = int(hov["size"].get()) if hov else 8
        hov_clr = hov["color"].get() if hov else "white"
        if not hasattr(self, "_term_tip") or self._term_tip is None:
            self._term_tip = self._ax_term.text(
                0, 0, "", color=hov_clr, fontsize=hov_sz,
                bbox=dict(facecolor="#222", alpha=0.92, edgecolor="#555",
                          boxstyle="round,pad=0.3"),
                zorder=10,
            )
        self._term_tip.set_fontsize(hov_sz)
        self._term_tip.set_color(hov_clr)
        self._term_tip.set_position((event.xdata + 0.4, event.ydata + 0.4))
        self._term_tip.set_text(txt)
        self._term_tip.set_visible(True)
        # Also update MVC header for the hovered expiration
        self._update_term_header(rows, hovered_exp=exp)
        self._canvas.draw_idle()

    def _build_term_slider(self):
        """Build (once) the time-slider strip used by term view. Hidden when
        not in term view; re-packed on _show_term_view entry."""
        if hasattr(self, "_term_slider_frame"):
            return
        import tkinter as tk
        f = tk.Frame(self, bg=BG_PANEL)
        tk.Label(f, text="Time:", bg=BG_PANEL, fg=FG_DIM,
                 font=(FONT, 9)).pack(side="left", padx=(8, 4))
        self._term_slider_var = tk.IntVar(value=0)
        self._term_slider = tk.Scale(
            f, from_=0, to=0, orient="horizontal",
            variable=self._term_slider_var,
            showvalue=False,
            bg=BG_PANEL, fg=FG_PRIMARY, troughcolor=BG_INPUT,
            highlightthickness=0,
            activebackground=CYAN,
            command=self._on_term_slider_change,
        )
        self._term_slider.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._term_slider_lbl = tk.Label(
            f, text="--:--", bg=BG_PANEL, fg=FG_PRIMARY,
            font=(FONT, 9), width=10, anchor="e",
        )
        self._term_slider_lbl.pack(side="right", padx=(0, 8))
        self._term_slider_frame = f
        self._term_positions = []

    def _show_term_slider(self):
        self._build_term_slider()
        # Pack at the bottom of the main window. Use side="bottom" so it
        # sits beneath the chart canvas regardless of other UI.
        if not self._term_slider_frame.winfo_ismapped():
            self._term_slider_frame.pack(side="bottom", fill="x", pady=(2, 6))

    def _hide_term_slider(self):
        if hasattr(self, "_term_slider_frame"):
            self._term_slider_frame.pack_forget()

    def _refresh_term_slider(self):
        """Reload available positions and update the slider extent.

        Live-follow rule: if the user was parked at the previous max
        position, auto-advance to the new max. Otherwise preserve the
        user's scrubbed position.
        """
        import gex_history_db as db
        from datetime import datetime
        self._build_term_slider()
        conn = db.connect(read_only=True)
        try:
            today = datetime.now(TZ).strftime("%Y-%m-%d")
            positions = term_slider_positions(conn, today, "SPX")
        finally:
            conn.close()
        prev_positions = getattr(self, "_term_positions", [])
        prev_max_idx = max(0, len(prev_positions) - 1)
        was_at_max = (
            len(prev_positions) > 0
            and self._term_slider_var.get() == prev_max_idx
        )
        self._term_positions = positions
        if not positions:
            self._term_slider.configure(from_=0, to=0)
            self._term_slider_lbl.configure(text="--:--")
            return
        new_max_idx = len(positions) - 1
        self._term_slider.configure(from_=0, to=new_max_idx)
        if was_at_max or len(prev_positions) == 0:
            self._term_slider_var.set(new_max_idx)
        else:
            # Clamp existing position to the new range
            cur = self._term_slider_var.get()
            if cur > new_max_idx:
                self._term_slider_var.set(new_max_idx)
        self._update_term_slider_label()

    def _update_term_slider_label(self):
        idx = self._term_slider_var.get()
        positions = getattr(self, "_term_positions", [])
        if 0 <= idx < len(positions):
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(positions[idx])
                self._term_slider_lbl.configure(text=dt.strftime("%H:%M CT"))
            except ValueError:
                self._term_slider_lbl.configure(text="--:--")

    def _on_term_slider_change(self, _value):
        """Slider command callback. Re-renders the heatmap at the new position."""
        self._update_term_slider_label()
        idx = self._term_slider_var.get()
        positions = getattr(self, "_term_positions", [])
        if 0 <= idx < len(positions):
            self._load_term_snapshot_at(positions[idx])

    def _load_term_snapshot_at(self, ts_iso: str):
        """Load rows for `ts_iso` and re-render. Stores the current rows on
        self._term_last_rows so Task 6 (hover/MVC) can read them without
        re-querying."""
        import gex_history_db as db
        conn = db.connect(read_only=True)
        try:
            rows = db.load_term_snapshot(conn, ts_iso, "SPX")
        finally:
            conn.close()
        self._term_last_rows = rows
        draw_term_heatmap(self._ax_term, rows, **self._term_colors(),
                          chrome=self._chrome)
        self._update_term_header(rows)
        # Tooltip is bound to the previous axis state; clear it so a stale
        # cell doesn't linger after a snapshot reload.
        if hasattr(self, "_term_tip") and self._term_tip is not None:
            self._term_tip = None
        theme.apply_matplotlib(self._fig, [self._ax_term])
        self._canvas.draw_idle()

    def _render_term_now(self):
        import gex_history_db as db
        from datetime import datetime
        conn = db.connect(read_only=True)
        try:
            today = datetime.now(TZ).strftime("%Y-%m-%d")
            timestamps = db.list_term_timestamps_for_date(conn, today, "SPX")
            if not timestamps:
                draw_term_heatmap(self._ax_term, [], **self._term_colors(),
                                  chrome=self._chrome)
            else:
                rows = db.load_term_snapshot(conn, timestamps[-1], "SPX")
                draw_term_heatmap(self._ax_term, rows, **self._term_colors(),
                                  chrome=self._chrome)
        finally:
            conn.close()
        theme.apply_matplotlib(self._fig, [self._ax_term])
        self._canvas.draw_idle()

    # ── Chart Drawing ──

    def _redraw(self):
        """Redraw chart from current engine snapshot.

        Side-by-side layout: horizontal bars on the left, heatmap on the right,
        sharing a price Y axis. Task 9 replaces the heatmap stub with the real
        OI / gamma-profile rendering.
        """
        ax = self._ax_bars
        ax.clear()
        self._ax_heat.clear()

        # Task 11: Show Heatmap toggle — when off, hide the heatmap axis and
        # stretch the bars axis across the full figure width. When on, restore
        # the original 1:3 side-by-side layout.
        show_heatmap_on = (
            self._show_heatmap_var.get()
            if hasattr(self, "_show_heatmap_var") else True
        )
        if not show_heatmap_on:
            self._ax_heat.set_visible(False)
            self._fig.subplots_adjust(right=0.97)
            self._ax_bars.set_position([0.05, 0.1, 0.92, 0.84])
        else:
            self._ax_heat.set_visible(True)
            self._fig.subplots_adjust(right=0.93)
            self._ax_bars.set_position([0.05, 0.1, 0.22, 0.84])

        view = self._view_var.get()
        self._update_pressure_panel()  # pack/unpack based on current view
        hist = self._load_history_dicts(view)

        # Select dataset
        if view == "charm":
            data = self._charm_data
            is_charm = True  # used by downstream color/label branches
        elif view == "dex":
            data = self._dex_data
            is_charm = False
        elif view == "vanna":
            data = self._vanna_data
            is_charm = False
        else:
            data = self._engine.current
            is_charm = False

        if not data:
            ax.set_facecolor(BG_MAIN)
            if view == "dex":
                msg = "No DEX data — waiting for fetch..."
            elif view == "vanna":
                msg = "No vanna data — waiting for fetch..."
            elif is_charm:
                msg = "No charm data — waiting for fetch..."
            else:
                msg = "No data — waiting for first fetch..."
            ax.text(0.5, 0.5, msg,
                    ha="center", va="center", color=FG_DIM, fontsize=12,
                    transform=ax.transAxes)
            self._draw_heatmap(self._ax_heat, self._symbol_var.get(), view, None)
            self._hover_strikes = []
            self._hover_grid = {}
            self._hover_annotation = None
            theme.apply_matplotlib(self._fig, [self._ax_bars, self._ax_heat])
            self._canvas.draw_idle()
            return

        spot = data["spot"]
        gex_raw = data["gex"]
        grouping = self._grouping_var.get()
        display = self._display_var.get()

        gex = GammaEngine.group_gex(gex_raw, grouping)

        # Filter to strikes near spot with non-zero values.
        # Use +/-2% for a tight, consistent window across GEX, Charm, and DEX.
        pct = 0.02
        lo, hi = spot * (1 - pct), spot * (1 + pct)
        strikes = sorted([s for s in gex if lo <= s <= hi and gex[s][display] != 0])

        if not strikes:
            ax.set_facecolor(BG_MAIN)
            if view == "dex":
                label = "DEX"
            elif view == "vanna":
                label = "Vanna"
            elif is_charm:
                label = "Charm"
            else:
                label = "GEX"
            ax.text(0.5, 0.5, f"No non-zero {label} within +/-{int(pct*100)}% of spot",
                    ha="center", va="center", color=FG_DIM, fontsize=12,
                    transform=ax.transAxes)
            self._draw_heatmap(self._ax_heat, self._symbol_var.get(), view, spot)
            self._hover_strikes = []
            self._hover_grid = {}
            self._hover_annotation = None
            theme.apply_matplotlib(self._fig, [self._ax_bars, self._ax_heat])
            self._canvas.draw_idle()
            return

        values = [gex[s][display] for s in strikes]
        if view == "dex":
            pos_color = self._clr_dex_pos.get()
            bar_key = "DEX+ Bars"
        elif view == "vanna":
            pos_color = self._clr_vanna_pos.get()
            bar_key = "Vanna+ Bars"
        elif is_charm:
            pos_color = self._clr_charm_pos.get()
            bar_key = "Charm+ Bars"
        else:
            pos_color = self._clr_gex_pos.get()
            bar_key = "GEX+ Bars"
        neg_color = self._clr_neg.get()
        colors = [pos_color if v >= 0 else neg_color for v in values]

        ax.set_facecolor(BG_MAIN)
        if len(strikes) >= 2:
            diffs = sorted(strikes[i + 1] - strikes[i]
                           for i in range(len(strikes) - 1))
            typical_spacing = diffs[len(diffs) // 2]
        else:
            typical_spacing = max(grouping, 1.0)
        bar_ratio = self._stl[bar_key]["size"].get()
        bar_height = typical_spacing * bar_ratio

        # ΔDEX ghost overlay: flat low-alpha bars behind premium bars (DEX only)
        if view == "dex" and self._db is not None:
            try:
                from gex_history_db import first_snapshot_today
                open_grid = first_snapshot_today(
                    self._db, self._symbol_var.get(), "dex",
                )
            except sqlite3.OperationalError:
                open_grid = {}
            if open_grid:
                open_gex = GammaEngine.group_gex(open_grid, grouping)
                open_values = [open_gex.get(s, {}).get(display, 0.0) for s in strikes]
                ghost_clr = self._stl["Ghost Bars"]["color"].get()
                ghost_alpha = self._stl["Ghost Bars"]["size"].get()
                ghost_colors = [ghost_clr if v >= 0 else neg_color for v in open_values]
                ax.barh(strikes, open_values, color=ghost_colors, height=bar_height,
                        alpha=ghost_alpha, edgecolor="none", zorder=1)

        # Premium cylindrical embossed bars (main solid)
        for strike, val, clr in zip(strikes, values, colors):
            self._draw_premium_bar(ax, strike, val, bar_height, clr)

        # Zero-line spine with subtle glow
        zl = self._stl["Zero Line"]
        ax.axvline(x=0, color=zl["color"].get(),
                   linewidth=zl["thickness"].get(), alpha=0.5, zorder=1)
        ax.axvline(x=0, color=zl["color"].get(), linewidth=2.5, alpha=0.08, zorder=0)
        # Faint horizontal gridlines
        gl = self._stl["Grid Lines"]
        ax.yaxis.grid(True, color=gl["color"].get(), alpha=0.06,
                      linewidth=gl["thickness"].get(), linestyle="-")
        ax.set_axisbelow(True)

        # Cache per-redraw state for hover tooltip (_on_bar_hover consumes).
        self._hover_strikes = strikes
        self._hover_grid = gex  # {strike: {"call", "put", "net"}}
        self._hover_bar_height = bar_height
        self._hover_view = view
        # Create/reset the hover annotation artist on the current ax.
        # ax.clear() in the next _redraw destroys it; recreate each time.
        self._hover_annotation = ax.annotate(
            "", xy=(0, 0), xycoords="data",
            xytext=(8, 0), textcoords="offset points",
            ha="left", va="center", fontsize=8, color=FG_PRIMARY,
            bbox=dict(facecolor=BG_PANEL, edgecolor=FG_DIM,
                      boxstyle="round,pad=0.3", alpha=0.92),
            visible=False, zorder=100,
        )

        # Ghost-bar history overlay (uses strike prices directly).
        if self._show_history_var.get() and len(hist) >= 2:
            self._draw_history_overlay(ax, strikes, hist, display, grouping)

        # Y-axis: strike prices with tight bounds.
        y_margin = typical_spacing * 2
        ax.set_ylim(min(strikes) - y_margin, max(strikes) + y_margin)
        ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=14, steps=[1, 2, 5, 10]))
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v, _p: f"{v:,.0f}"))
        tick_sz = int(self._stl["Axis Ticks"]["size"].get())
        tick_clr = self._stl["Axis Ticks"]["color"].get()
        ax.tick_params(axis="x", colors=tick_clr, labelsize=tick_sz)
        ax.tick_params(axis="y", colors=tick_clr, labelsize=tick_sz)

        # Primary X formatting (K/M/B)
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(self._fmt_gex))

        # X-axis headroom: 2% padding so longest bars don't butt against the spine.
        finite_vals = [float(v) for v in values if v is not None]
        if finite_vals:
            v_min = min(finite_vals + [0.0])
            v_max = max(finite_vals + [0.0])
            v_span = v_max - v_min
            if v_span > 0:
                pad = v_span * 0.02
                ax.set_xlim(v_min - (pad if v_min < 0 else 0),
                            v_max + (pad if v_max > 0 else 0))

        # ── Reference lines with right-justified labels ──
        text_glow = [pe.withStroke(linewidth=3, foreground=BG_MAIN, alpha=0.9)]
        ref_labels = []

        # Spot line — thin, always on top
        sl = self._stl["Spot Line"]
        spot_clr = sl["color"].get()
        ax.axhline(y=spot, color=spot_clr, linestyle=sl["linestyle"].get(),
                   linewidth=sl["thickness"].get(), alpha=0.9, zorder=50)
        st = self._stl["Spot Text"]
        ref_labels.append((spot, f"Spot: {spot:,.1f}",
                           st["color"].get(), int(st["size"].get())))

        # On-chart level labels: (y, text, color) drawn just above each line.
        # These replace the retired top headline strip.
        level_labels = []

        # Current zero-gamma flip line (GEX / Charm / DEX) + label. This is the
        # "Flip" value the headline used to show. (Vanna has no strike flip.)
        if view in ("gex", "charm", "dex"):
            cur_flip = self._calc_flip_point(gex, spot)
            if cur_flip is not None and lo <= cur_flip <= hi:
                fl = self._stl["Flip Line"]
                ax.axhline(y=cur_flip, color=fl["color"].get(),
                           linestyle=fl["linestyle"].get(),
                           linewidth=fl["thickness"].get(), alpha=0.6, zorder=47)
                level_labels.append((cur_flip, f"Flip {cur_flip:,.0f}",
                                     fl["color"].get()))

        # Projected EOD flip (GEX/Charm only)
        if view != "dex":
            proj_flip = self._eod_flip_projection(hist)
            if proj_flip is not None and lo <= proj_flip <= hi:
                pl = self._stl["Proj Flip Line"]
                ax.axhline(y=proj_flip, color=pl["color"].get(),
                           linestyle=pl["linestyle"].get(),
                           linewidth=pl["thickness"].get(), alpha=0.9)
                pt = self._stl["Proj Flip Text"]
                ref_labels.append((proj_flip, f"Proj Flip 15:15  {proj_flip:,.1f}",
                                   pt["color"].get(), int(pt["size"].get())))

        # 0-DTE charm-projected flip line (DEX view only)
        if view == "dex" and data.get("hedge_pressure") is not None:
            projected_flip = self._compute_projected_flip(data, spot)
            if projected_flip is not None and lo <= projected_flip <= hi:
                dpl = self._stl["DEX Proj Flip"]
                ax.axhline(y=projected_flip, color=dpl["color"].get(),
                           linestyle=dpl["linestyle"].get(),
                           linewidth=dpl["thickness"].get(), alpha=0.9)
                dpt = self._stl["DEX Flip Text"]
                ref_labels.append((projected_flip,
                                   f"Proj Flip 15:00  {projected_flip:,.1f}",
                                   dpt["color"].get(), int(dpt["size"].get())))
                level_labels.append((projected_flip,
                                     f"Proj Flip {projected_flip:,.0f}",
                                     dpl["color"].get()))

        # Expected move lines
        if self._show_em_var.get() and self._last_em:
            el = self._stl["EM Lines"]
            em = self._last_em
            et = self._stl["EM Text"]
            for em_price, label in [(spot + em, f"+1s {spot + em:,.1f}"),
                                     (spot - em, f"-1s {spot - em:,.1f}")]:
                if lo <= em_price <= hi:
                    ax.axhline(y=em_price, color=el["color"].get(),
                               linestyle=el["linestyle"].get(),
                               linewidth=el["thickness"].get(), alpha=0.7)
                    ref_labels.append((em_price, label,
                                       et["color"].get(), int(et["size"].get())))

        # Max-pain line (GEX view only) — quick win #1. Computed from the
        # retained chain; gated to the visible ±2% window.
        if view == "gex":
            chain = getattr(self._engine, "_last_chain", None)
            mp_res = calc_max_pain_from_chain(chain) if chain else None
            if mp_res is not None:
                mp_strike = mp_res["max_pain"]
                if lo <= mp_strike <= hi:
                    mpl = self._stl["Max Pain Line"]
                    ax.axhline(y=mp_strike, color=mpl["color"].get(),
                               linestyle=mpl["linestyle"].get(),
                               linewidth=mpl["thickness"].get(), alpha=0.9,
                               zorder=49)
                    mpt = self._stl["Max Pain Text"]
                    ref_labels.append((mp_strike, f"Max Pain {mp_strike:,.0f}",
                                       mpt["color"].get(), int(mpt["size"].get())))
                    level_labels.append((mp_strike, f"Max Pain {mp_strike:,.0f}",
                                         mpl["color"].get()))

        # Directional call/put wall lines (GEX view only) — quick win #2.
        if view == "gex":
            dwalls = get_directional_walls({"gex": gex, "spot": spot}, spot)
            cw, pw = dwalls.get("call_wall"), dwalls.get("put_wall")
            if cw is not None and lo <= cw <= hi:
                cwl = self._stl["Call Wall Line"]
                ax.axhline(y=cw, color=cwl["color"].get(),
                           linestyle=cwl["linestyle"].get(),
                           linewidth=cwl["thickness"].get(), alpha=0.55, zorder=48)
                level_labels.append((cw, f"Call Wall {cw:,.0f}",
                                     cwl["color"].get()))
            if pw is not None and lo <= pw <= hi:
                pwl = self._stl["Put Wall Line"]
                ax.axhline(y=pw, color=pwl["color"].get(),
                           linestyle=pwl["linestyle"].get(),
                           linewidth=pwl["thickness"].get(), alpha=0.55, zorder=48)
                level_labels.append((pw, f"Put Wall {pw:,.0f}",
                                     pwl["color"].get()))

        # De-overlap labels
        y_range = max(strikes) - min(strikes)
        min_gap = y_range * 0.025 if y_range > 0 else 1.0
        ref_labels.sort(key=lambda r: r[0])
        nudged = [r[0] for r in ref_labels]
        for i in range(1, len(nudged)):
            if nudged[i] - nudged[i - 1] < min_gap:
                nudged[i] = nudged[i - 1] + min_gap

        # Spot / Proj Flip / EM lines stay unlabelled (Spot is in the status
        # row; EM lines read as ±1σ). The KEY LEVELS (Call Wall, Put Wall,
        # Flip, Max Pain, …) are labelled directly on the chart — unobtrusive
        # text anchored to the left edge, just above each line — replacing the
        # retired top headline strip.
        _ = (ref_labels, nudged)
        if level_labels:
            lvl_sz = int(self._stl["Level Label Text"]["size"].get())
            level_labels.sort(key=lambda r: r[0])
            y_span = (max(strikes) - min(strikes)) if len(strikes) > 1 else 1.0
            gap = y_span * 0.02 if y_span > 0 else 1.0
            lab_ys = [r[0] for r in level_labels]
            for i in range(1, len(lab_ys)):
                if lab_ys[i] - lab_ys[i - 1] < gap:
                    lab_ys[i] = lab_ys[i - 1] + gap
            for (y_orig, text, color), y in zip(level_labels, lab_ys):
                ax.text(0.015, y, text, transform=ax.get_yaxis_transform(),
                        va="bottom", ha="left", fontsize=lvl_sz, color=color,
                        alpha=0.92, zorder=60, clip_on=True,
                        path_effects=text_glow)

        # Comparison dots
        if self._show_open_var.get() and self._engine.market_open:
            self._draw_comparison_dots(ax, strikes, self._engine.market_open, display, grouping, GOLD)
        if self._show_prev_var.get() and self._engine.previous:
            self._draw_comparison_dots(ax, strikes, self._engine.previous, display, grouping, GRAY)

        # Heatmap panel (right).
        self._draw_heatmap(self._ax_heat, self._symbol_var.get(), view, spot)

        # Styling / titles
        for spine in ax.spines.values():
            spine.set_color(FG_DIM)
            spine.set_linewidth(0.5)
        dte = self._engine._last_dte
        dte_str = "0-DTE" if dte == 0 else f"{dte}-DTE"
        ttl = self._stl["Title"]
        ttl_sz = int(ttl["size"].get())
        ttl_clr = ttl["color"].get()
        al = self._stl["Axis Labels"]
        al_sz = int(al["size"].get())
        al_clr = al["color"].get()
        sym = self._symbol_var.get()
        if view == "dex":
            ax.set_title(f"Delta Exposure (DEX) \u2014 {sym} {dte_str}",
                         color=ttl_clr, fontsize=ttl_sz, fontfamily=FONT,
                         fontweight="semibold", pad=10)
            ax.set_xlabel("Delta Exposure ($)", color=al_clr, fontsize=al_sz)
        elif view == "vanna":
            ax.set_title(f"Vanna Exposure (VEX) \u2014 {sym} {dte_str}",
                         color=ttl_clr, fontsize=ttl_sz, fontfamily=FONT,
                         fontweight="semibold", pad=10)
            ax.set_xlabel("Vanna Exposure ($)", color=al_clr, fontsize=al_sz)
        elif is_charm:
            ax.set_title(f"Charm Pressure (ChEX) \u2014 {sym} {dte_str}",
                         color=ttl_clr, fontsize=ttl_sz, fontfamily=FONT,
                         fontweight="semibold", pad=10)
            ax.set_xlabel("Charm Exposure ($)", color=al_clr, fontsize=al_sz)
        else:
            ax.set_title(f"Gamma Exposure (GEX) \u2014 {sym} {dte_str}",
                         color=ttl_clr, fontsize=ttl_sz, fontfamily=FONT,
                         fontweight="semibold", pad=10)
            ax.set_xlabel("GEX ($)", color=al_clr, fontsize=al_sz)

        theme.apply_matplotlib(self._fig, [self._ax_bars, self._ax_heat])
        self._canvas.draw_idle()

        try:
            self._update_collector_status()
        except Exception:
            pass  # never let status-label bugs crash the main redraw

    def _draw_premium_bar(self, ax, y, width, height, color, alpha=0.92):
        """Draw an embossed cylindrical bar with highlight, body, and drop shadow."""
        if width == 0:
            return
        x0 = min(0, width)
        x1 = max(0, width)
        bar_w = x1 - x0
        if bar_w == 0:
            return

        rounding = min(height * 0.45, bar_w * 0.04)
        h2 = height / 2

        # 1. Drop shadow
        shadow = FancyBboxPatch(
            (x0 - bar_w * 0.005, y - h2 * 0.85 - height * 0.08),
            bar_w * 1.01, height * 0.9,
            boxstyle=f"round,pad=0,rounding_size={rounding}",
            facecolor="#000000", edgecolor="none", alpha=0.35, zorder=2,
        )
        ax.add_patch(shadow)

        # 2. Darker body
        r, g, b, _ = to_rgba(color)
        dark_color = (r * 0.55, g * 0.55, b * 0.55)
        body = FancyBboxPatch(
            (x0, y - h2), bar_w, height,
            boxstyle=f"round,pad=0,rounding_size={rounding}",
            facecolor=dark_color, edgecolor="none", alpha=alpha, zorder=3,
        )
        ax.add_patch(body)

        # 3. Cylindrical gradient
        n_rows = 64
        grad = np.zeros((n_rows, 1, 4))
        for i in range(n_rows):
            t = i / (n_rows - 1)
            brightness = 1.0 - 2.8 * (t - 0.45) ** 2
            brightness = max(0.3, min(1.0, brightness))
            grad[i, 0] = [r * brightness, g * brightness, b * brightness, alpha]
        cmap_v = LinearSegmentedColormap.from_list(
            "cyl", [to_rgba(c) for c in grad[:, 0, :3]], N=n_rows)
        vert_grad = np.linspace(0, 1, n_rows).reshape(-1, 1)
        extent = [x0, x1, y - h2, y + h2]
        clip_box = FancyBboxPatch(
            (x0, y - h2), bar_w, height,
            boxstyle=f"round,pad=0,rounding_size={rounding}",
            facecolor="none", edgecolor="none", zorder=4,
        )
        ax.add_patch(clip_box)
        ax.imshow(vert_grad, aspect="auto", cmap=cmap_v, extent=extent,
                  clip_path=clip_box, clip_on=True, zorder=4,
                  interpolation="bicubic", origin="lower")

        # 4. Top highlight
        hi_y = y + h2 * 0.3
        hi_h = height * 0.25
        hi_round = min(hi_h * 0.45, bar_w * 0.04)
        highlight = FancyBboxPatch(
            (x0 + bar_w * 0.01, hi_y), bar_w * 0.98, hi_h,
            boxstyle=f"round,pad=0,rounding_size={hi_round}",
            facecolor="#ffffff", edgecolor="none", alpha=0.18, zorder=5,
        )
        ax.add_patch(highlight)

        # 5. Tip cap
        tip_x = x1 if width >= 0 else x0
        cap_w = bar_w * 0.03
        cap = FancyBboxPatch(
            (tip_x - cap_w if width >= 0 else tip_x, y - h2 * 0.6),
            cap_w, height * 0.6,
            boxstyle=f"round,pad=0,rounding_size={min(cap_w, height * 0.3)}",
            facecolor=color, edgecolor="none", alpha=0.6, zorder=6,
        )
        ax.add_patch(cap)

    def _draw_history_overlay(self, ax, strikes, history, display, grouping):
        """Draw faint ghost bars for historical snapshots.

        Samples up to 6 evenly-spaced snapshots from `history` (oldest -> newest)
        and draws a thin marker at each strike's historical value, with alpha
        fading from 0.15 (oldest) to 0.6 (newest-1). The most recent snapshot
        IS the live bar already, so we skip it. Y coordinates are real strike
        prices now — the chart's Y axis uses price units directly.
        """
        n = len(history)
        if n < 2:
            return
        sample_count = min(6, n - 1)
        if sample_count == 1:
            indices = [0]
        else:
            step = (n - 1) / sample_count
            indices = [int(i * step) for i in range(sample_count)]

        strike_set = set(strikes)
        for rank, idx in enumerate(indices):
            alpha = 0.15 + (0.45 * rank / max(1, sample_count - 1))
            snap_gex_raw = history[idx].get("gex", {})
            snap_grouped = GammaEngine.group_gex(snap_gex_raw, grouping)
            for s in strike_set:
                if s not in snap_grouped:
                    continue
                val = snap_grouped[s][display]
                if val == 0:
                    continue
                ax.plot([0, val], [s, s], color=GRAY, alpha=alpha,
                        linewidth=1.5, solid_capstyle="butt", zorder=1)

    def _eod_flip_projection(self, hist):
        """Linear-extrapolate today's flip series to 15:15 CT close.

        Returns the projected flip price, or None when there isn't enough
        history, the latest snapshot already sits past close, or the fit
        is degenerate. Used both by the main chart (price-scale marker)
        and the EOD panel (dotted projection line + verbose narrative).
        """
        if not hist or len(hist) < 2:
            return None
        def _tod(dt):
            return dt.hour * 3600 + dt.minute * 60 + dt.second
        xs, ys = [], []
        for h in hist:
            flip = h.get("flip")
            ts = h.get("ts")
            if flip is None or ts is None:
                continue
            try:
                xs.append(_tod(ts))
                ys.append(float(flip))
            except (AttributeError, TypeError, ValueError):
                continue
        if len(xs) < 2:
            return None
        close_secs = 15 * 3600 + 15 * 60
        if xs[-1] >= close_secs:
            return None
        window = min(12, len(xs))
        return extrapolate_linear(xs[-window:], ys[-window:], close_secs)

    def _compute_projected_flip(self, data, spot):
        """Thin wrapper over the module-level ``compute_projected_flip``.

        Kept as a method so existing chart-rendering callsites work unchanged.
        """
        return compute_projected_flip(data, spot)

    def _draw_heatmap(self, ax_heat, symbol, view, current_spot):
        """Render the intraday strike × time heatmap on ax_heat.

        Layout:
            - Historical cells (left side): pcolormesh at alpha=1.0 from
              gex_history.snapshots for today.
            - "Now" vertical marker.
            - Forward-projection cells (right side): pcolormesh at alpha=0.7
              computed from self._engine._last_chain + bs_* greeks at future T.
            - White price line traces historical spot values.
            - No forward projection on the price line.

        Falls back to a placeholder when the DB has no rows today or last_chain
        is missing.
        """
        import numpy as np
        from matplotlib.colors import TwoSlopeNorm, LinearSegmentedColormap
        from matplotlib.dates import DateFormatter, MinuteLocator
        from datetime import datetime, timedelta

        ax_heat.set_facecolor(BG_MAIN)

        # Dark-centered diverging colormap: zero maps to BG_MAIN (dark navy)
        # instead of white, so empty/near-zero cells blend with the rest of
        # the dashboard background. Endpoints use brighter coral-red and
        # sky-blue (Tier-1 boost ~65-68% luminance vs ~40-50% prior) so
        # high-magnitude cells pop against the dark dashboard.
        heat_cmap = LinearSegmentedColormap.from_list(
            "gex_heat_dark",
            [
                self._stl["Heatmap Negative"]["color"].get(),
                self._stl["Heatmap Midpoint"]["color"].get(),
                self._stl["Heatmap Positive"]["color"].get(),
            ],
            N=256,
        )

        # Historical data.
        rows = []
        if self._db is not None:
            try:
                from gex_history_db import load_today_with_grid
                rows = load_today_with_grid(self._db, symbol, view)
            except Exception as e:
                log.warning("Heatmap load failed for %s/%s: %s", symbol, view, e)

        if not rows:
            ax_heat.text(0.5, 0.5, "Waiting for first snapshot…",
                         ha="center", va="center",
                         color=FG_DIM, fontsize=12, transform=ax_heat.transAxes)
            ax_heat.tick_params(colors=FG_DIM, labelsize=8)
            return

        display = self._display_var.get() if hasattr(self, "_display_var") else "net"
        strikes, times, hist_matrix = build_historical_matrix(rows, current_spot, display)

        if not strikes:
            ax_heat.text(0.5, 0.5, "No strikes within ±5% of spot",
                         ha="center", va="center",
                         color=FG_DIM, fontsize=12, transform=ax_heat.transAxes)
            return

        # Forward projection cells.
        fwd_strikes = []
        fwd_times = []
        fwd_matrix = None
        last_fetch_ts = times[-1] if times else None
        cache_key = (symbol, view)

        if self._engine._last_chain and last_fetch_ts is not None:
            cached = self._fwd_cache.get(cache_key)
            # Cache key is (symbol, view), but the strike list also depends
            # on live spot (±5% window) which can shift between redraws even
            # when the chain hasn't been re-fetched. Invalidate on strike
            # count change to keep fwd_matrix shape in sync with y_edges.
            cache_valid = (
                cached is not None
                and cached[0] == last_fetch_ts
                and len(cached[1]) == len(strikes)
            )
            if cache_valid:
                _, fwd_strikes, fwd_times, fwd_matrix = cached
            else:
                fwd_strikes, fwd_times, fwd_matrix = self._build_forward_band(
                    strikes, view, current_spot,
                )
                self._fwd_cache[cache_key] = (last_fetch_ts, fwd_strikes, fwd_times, fwd_matrix)

        # Combined min/max for normalization.
        all_vals = [hist_matrix]
        if fwd_matrix is not None and fwd_matrix.size > 0:
            all_vals.append(fwd_matrix)
        combined = np.concatenate([m.ravel() for m in all_vals])
        finite = combined[np.isfinite(combined)]
        if finite.size == 0:
            ax_heat.text(0.5, 0.5, "No data in window", ha="center", va="center",
                         color=FG_DIM, fontsize=12, transform=ax_heat.transAxes)
            return
        vmin = float(finite.min())
        vmax = float(finite.max())
        # TwoSlopeNorm requires vmin < 0 < vmax. Clamp defensively.
        if vmin >= 0:
            vmin = -abs(vmax) * 0.01 - 1e-9
        if vmax <= 0:
            vmax = abs(vmin) * 0.01 + 1e-9
        norm = TwoSlopeNorm(vmin=vmin, vcenter=0.0, vmax=vmax)

        # X-coords (timestamps as matplotlib datetimes).
        hist_xs = [datetime.fromtimestamp(t, TZ) for t in times]
        y = np.array(strikes)

        # Mesh cell edges: add half-interval margins so each snapshot occupies a visible cell.
        half_dt = timedelta(minutes=2.5)  # 5-min polls
        def _compute_x_edges(xs):
            if len(xs) == 1:
                return [xs[0] - half_dt, xs[0] + half_dt]
            edges = [xs[0] - half_dt]
            for i in range(len(xs) - 1):
                edges.append(xs[i] + (xs[i + 1] - xs[i]) / 2)
            edges.append(xs[-1] + half_dt)
            return edges

        x_edges_hist = _compute_x_edges(hist_xs)
        if len(strikes) > 1:
            y_edges = [strikes[0] - (strikes[1] - strikes[0]) / 2]
            for i in range(len(strikes) - 1):
                y_edges.append((strikes[i] + strikes[i + 1]) / 2)
            y_edges.append(strikes[-1] + (strikes[-1] - strikes[-2]) / 2)
        else:
            y_edges = [strikes[0] - 1, strikes[0] + 1]

        ax_heat.pcolormesh(
            x_edges_hist, y_edges, hist_matrix,
            cmap=heat_cmap, norm=norm, alpha=1.0, shading="flat",
        )

        # Forward band.
        if fwd_matrix is not None and fwd_matrix.size > 0 and fwd_times:
            fwd_xs = [datetime.fromtimestamp(t, TZ) for t in fwd_times]
            x_edges_fwd = _compute_x_edges(fwd_xs)
            ax_heat.pcolormesh(
                x_edges_fwd, y_edges, fwd_matrix,
                cmap=heat_cmap, norm=norm, alpha=0.7, shading="flat",
            )
            # "Now" vertical marker at the boundary.
            now_x = datetime.fromtimestamp(last_fetch_ts, TZ) + half_dt
            ax_heat.axvline(now_x, color=FG_DIM, linewidth=0.8, alpha=0.6, linestyle="--")

        # Price line (historical only).
        spots = [row[1] for row in rows]
        ax_heat.plot(hist_xs, spots, color=WHITE, linewidth=1.2, alpha=0.9, zorder=5)

        # Axis formatting.
        ax_heat.xaxis.set_major_locator(MinuteLocator(byminute=[0, 30]))
        ax_heat.xaxis.set_major_formatter(DateFormatter("%H:%M", tz=TZ))
        ax_heat.tick_params(axis="x", colors=FG_DIM, labelsize=8, rotation=0)
        ax_heat.tick_params(axis="y", colors=FG_PRIMARY, labelsize=8)
        ax_heat.set_facecolor(BG_MAIN)

        # Clamp x-range to market hours (08:30 - 15:00 CT).
        today_open = datetime.now(TZ).replace(hour=8, minute=30, second=0, microsecond=0)
        today_close = datetime.now(TZ).replace(hour=15, minute=0, second=0, microsecond=0)
        ax_heat.set_xlim(today_open, today_close)

        # Key-level labels on the right edge.
        # Latest live snapshot per view - freshest for labels.
        live_data = {
            "gex": self._engine.current,
            "charm": self._charm_data,
            "dex": self._dex_data,
            "vanna": self._vanna_data,
        }.get(view)
        if live_data:
            self._draw_heatmap_key_levels(
                ax_heat, symbol, view, live_data, current_spot, strikes,
            )

    def _draw_heatmap_key_levels(self, ax_heat, symbol, view, data, current_spot, strikes):
        """Overlay horizontal lines + right-edge labels for key strikes on the heatmap.

        Label set depends on view; labels outside the Y-range (+/-5%) are silently
        skipped; labels on the same strike are deduplicated (concatenated).
        """
        if not strikes:
            return

        y_lo, y_hi = min(strikes), max(strikes)

        # Dedup by strike: first label wins, same-strike additions are concatenated.
        labels_by_strike = {}

        def _add(strike, label, color, linestyle="-"):
            if strike is None or not (y_lo <= strike <= y_hi):
                return
            if strike in labels_by_strike:
                prev_label, prev_color, prev_style = labels_by_strike[strike]
                labels_by_strike[strike] = (f"{prev_label} / {label}", prev_color, prev_style)
            else:
                labels_by_strike[strike] = (label, color, linestyle)

        summary = data if isinstance(data, dict) else {}
        pos = summary.get("top_pos_strike")
        neg = summary.get("top_neg_strike")
        flip = summary.get("flip")

        if view == "gex":
            _add(pos, "Call Wall", self._clr_gex_pos.get())
            _add(neg, "Put Wall", self._clr_neg.get())
            _add(flip, "Gamma Flip", WHITE)
            key_gamma = find_key_gamma_strike(data.get("gex") or {}, current_spot)
            _add(key_gamma, "Key \u0393 Strike", self._clr_gex_pos.get())
        elif view == "charm":
            _add(pos, "Max Charm+", self._clr_charm_pos.get())
            _add(neg, "Max Charm\u2212", self._clr_neg.get())
            _add(flip, "Charm Flip", WHITE)
        elif view == "dex":
            _add(pos, "Max \u0394+", self._clr_dex_pos.get())
            _add(neg, "Max \u0394\u2212", self._clr_neg.get())
            _add(flip, "\u0394 Flip", WHITE)
            proj_flip = self._compute_projected_flip(data, current_spot)
            _add(proj_flip, "Proj \u0394 Flip 15:00", self._trading["dex_proj_flip"], linestyle="--")
        elif view == "vanna":
            _add(pos, "Max Vanna+", self._clr_vanna_pos.get())
            _add(neg, "Max Vanna\u2212", self._clr_neg.get())
            _add(flip, "Vanna Flip", WHITE)

        # Last Close for all views.
        last_close = self._fetch_last_close(symbol)
        _add(last_close, "Last Close", FG_DIM, linestyle="--")

        # Spot on the heatmap edge — used to overflow into the heatmap from
        # the bar panel; keep it here where it's always readable.
        if current_spot is not None:
            spot_clr = self._stl["Spot Text"]["color"].get()
            _add(current_spot, f"Spot {current_spot:,.1f}", spot_clr)

        # Expected-move +/-1s labels (heatmap edge).
        if self._show_em_var.get() and self._last_em and current_spot is not None:
            em_clr = self._stl["EM Text"]["color"].get()
            _add(current_spot + self._last_em,
                 f"+1s {current_spot + self._last_em:,.1f}", em_clr)
            _add(current_spot - self._last_em,
                 f"-1s {current_spot - self._last_em:,.1f}", em_clr)

        xmin, xmax = ax_heat.get_xlim()

        # Always draw the horizontal lines at their true strike.
        for strike, (label, color, linestyle) in labels_by_strike.items():
            ax_heat.axhline(y=strike, color=color, linewidth=0.7, alpha=0.6,
                            linestyle=linestyle, zorder=4)

        # Cluster labels whose strikes are within ~0.15% of spot (or a small
        # absolute fraction of the visible y-range) so their right-edge text
        # doesn't overlap. Within a cluster, merge labels into one line at
        # the cluster's mean strike. Across clusters, nudge text vertically
        # using offset_points so neighbouring clusters never collide.
        if not labels_by_strike:
            return

        y_span = max(y_hi - y_lo, 1.0)
        # Tolerance: ~1.2% of the visible window — roughly one fontsize-8 line height.
        tol = y_span * 0.012

        sorted_items = sorted(labels_by_strike.items(), key=lambda kv: kv[0])
        clusters = []  # list of [strike_list, [(label, color, style), ...]]
        for strike, payload in sorted_items:
            if clusters and abs(strike - clusters[-1][0][-1]) <= tol:
                clusters[-1][0].append(strike)
                clusters[-1][1].append(payload)
            else:
                clusters.append([[strike], [payload]])

        # Min vertical pixel separation between successive clusters' anchor text.
        from matplotlib import transforms
        line_pad_pts = 11  # ~fontsize 8 + a couple pts
        last_disp_y = None
        renderer = ax_heat.figure.canvas.get_renderer() if hasattr(
            ax_heat.figure.canvas, "get_renderer") else None

        for strike_list, payloads in clusters:
            anchor = sum(strike_list) / len(strike_list)
            # Merge labels at this cluster — first color/style wins for the line,
            # but each label keeps its own color via separate text() calls stacked.
            # Simplest: concatenate with " / " in cluster's first color.
            merged_label = " / ".join(p[0] for p in payloads)
            color = payloads[0][1]

            # Convert anchor strike to display pixels, push down if too close
            # to the previous cluster, then convert back to data coords.
            disp_xy = ax_heat.transData.transform((xmax, anchor))
            if last_disp_y is not None and disp_xy[1] - last_disp_y < line_pad_pts:
                disp_xy = (disp_xy[0], last_disp_y + line_pad_pts)
            last_disp_y = disp_xy[1]
            data_xy = ax_heat.transData.inverted().transform(disp_xy)

            ax_heat.text(xmax, data_xy[1], f"  {merged_label}",
                         color=color, fontsize=8, va="center", ha="left",
                         clip_on=False, zorder=4)

    def _build_forward_band(self, strikes, view, current_spot):
        """Compute per-strike forward-projected exposure at each 5-min slot from
        next_boundary(now) through 15:00 CT.

        Returns (fwd_strikes, fwd_times, matrix). fwd_strikes == the input
        strikes list for alignment with the historical matrix. matrix shape
        is (len(strikes), len(slots)). Empty matrix when no future slots exist.
        """
        import numpy as np
        from datetime import datetime, timedelta

        # Reuse the collector's boundary function via module import (keeps logic in one place).
        try:
            from gex_collector import next_boundary, POLL_INTERVAL_MIN
        except ImportError:
            return strikes, [], np.zeros((len(strikes), 0))

        now = datetime.now(TZ)
        close = now.replace(hour=CLOSE_HOUR_CT, minute=CLOSE_MIN_CT, second=0, microsecond=0)
        if now >= close:
            return strikes, [], np.zeros((len(strikes), 0))

        slots = []
        cursor = next_boundary(now)
        while cursor <= close:
            slots.append(cursor)
            cursor += timedelta(minutes=POLL_INTERVAL_MIN)

        if not slots:
            return strikes, [], np.zeros((len(strikes), 0))

        matrix = np.full((len(strikes), len(slots)), np.nan)
        for col_idx, slot_time in enumerate(slots):
            hours_to_close = (close - slot_time).total_seconds() / 3600.0
            # MVP: treat the forward slots as if nearest expiry is today's close.
            # For multi-DTE chains we'd need per-expiry handling.
            T_future = max(hours_to_close / (365 * 24), 1e-6)
            per_strike = self._engine.project_exposure_forward(view, T_future)
            for row_idx, strike in enumerate(strikes):
                if strike in per_strike:
                    matrix[row_idx, col_idx] = per_strike[strike]

        fwd_times = [int(s.timestamp()) for s in slots]
        return strikes, fwd_times, matrix

    def _draw_comparison_dots(self, ax, strikes, snapshot, display, grouping, color):
        """Overlay small dots for a comparison snapshot (y = strike price)."""
        comp_gex = GammaEngine.group_gex(snapshot["gex"], grouping)
        for s in strikes:
            if s in comp_gex:
                val = comp_gex[s][display]
                if val != 0:
                    ax.plot(val, s, "o", color=color, markersize=4, alpha=0.7)

    @staticmethod
    def _fmt_gex(x, _pos):
        """Format GEX value as K/M/B."""
        ax_val = abs(x)
        if ax_val >= 1e9:
            return f"{x / 1e9:.1f}B"
        if ax_val >= 1e6:
            return f"{x / 1e6:.1f}M"
        if ax_val >= 1e3:
            return f"{x / 1e3:.0f}K"
        return f"{x:.0f}"

    # ── Data Fetch (background thread) ──

    def _do_fetch(self):
        """Fetch option chain once, compute GEX and EM from it.

        Called from the worker thread.  Tkinter variable reads use simple
        string gets which are safe under the GIL.  All UI mutations and
        shared-state writes are dispatched to the main thread via ``after``.
        """
        symbol = self._symbol_var.get()
        today = datetime.now(TZ).date()
        use_volume = (self._formula_var.get() == "volume")

        try:
            # SPX has daily expirations; VIX only Wed/Tue.
            # Widen window to 7 days so the nearest VIX expiration is included.
            # _find_nearest_exp_key picks the closest one.
            to_date = today + timedelta(days=7)
            kwargs = {"contract_type": self._client.Options.ContractType.ALL,
                      "from_date": today, "to_date": to_date}
            with self._client_lock:
                r = self._client.get_option_chain(symbol, **kwargs)
            chain = r.json() if r.status_code == 200 else None
        except Exception as e:
            log.error("GEX fetch failed for %s: %s", symbol, e)
            chain = None

        if not chain:
            self.after(0, lambda: self._status_lbl.configure(
                text=f"Fetch failed for {symbol}"))
            return

        # Single chain, four computations — engine is only touched here
        result = self._engine.calc_from_chain(chain, use_volume=use_volume)
        charm_result = self._engine.calc_charm_from_chain(chain, use_volume=use_volume)
        dex_result = self._engine.calc_dex_from_chain(chain, use_volume=use_volume)
        vanna_result = self._engine.calc_vanna_from_chain(chain, use_volume=use_volume)
        last_em = self._engine.calc_expected_move_from_chain(chain)

        spot = chain.get("underlyingPrice", 0)
        sc = result["strike_count"] if result else 0
        dte = self._engine._last_dte

        # Dealer Pinch state — the IV/RV price-history fetch belongs on this
        # worker thread (never on the UI thread). Stashed for the status panel.
        self._last_pinch_state = self._compute_pinch_state(
            chain=chain, spot=spot, dte=dte, expected_move=last_em,
            gex_result=result)
        dte_label = "0-DTE" if dte == 0 else f"{dte}-DTE"
        formula_label = "Vol-Weighted" if use_volume else "Standard (OI)"

        def _update_ui():
            # Drop stale results: if user switched symbol during the fetch,
            # this result belongs to the wrong buffer — skip the append.
            if self._symbol_var.get() != symbol:
                return
            self._last_em = last_em
            # Update prev/open trackers BEFORE overwriting current.
            # Reset open trackers on day rollover (mirrors engine._today_str logic).
            today_str = datetime.now(TZ).strftime("%Y-%m-%d")
            if getattr(self, "_open_today_str", None) != today_str:
                self._open_today_str = today_str
                self._open_charm_data = None
                self._open_dex_data = None
                self._open_vanna_data = None
            self._prev_charm_data = self._charm_data
            self._prev_dex_data = self._dex_data
            self._prev_vanna_data = self._vanna_data
            if self._open_charm_data is None and charm_result is not None:
                self._open_charm_data = charm_result
            if self._open_dex_data is None and dex_result is not None:
                self._open_dex_data = dex_result
            if self._open_vanna_data is None and vanna_result is not None:
                self._open_vanna_data = vanna_result
            self._charm_data = charm_result
            self._dex_data = dex_result
            self._vanna_data = vanna_result
            self._status_lbl.configure(
                text=f"{symbol}  |  {spot:,.1f}  |  {dte_label}  |  {sc} strikes  |  {formula_label}")
            self._redraw()
            # Grow the term-view slider as new snapshots arrive (live-follow).
            # Wrapped so any slider issue cannot break the main refresh path.
            try:
                if self._view_var.get() == "term":
                    self._refresh_term_slider()
            except Exception as e:
                log.debug("term slider refresh skipped: %s", e)

        self.after(0, _update_ui)

    @staticmethod
    def _fetch_symbol_analysis_impl(client, symbol, use_volume=False, grouping=1):
        """Fetch chain + compute analysis blocks for any symbol.

        Returns dict {'gex': ..., 'charm': ..., 'dex': ...} where each value is
        the output of build_analysis_dict, or None if fetch fails.
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

    def _fetch_symbol_analysis(self, symbol):
        use_volume = (self._formula_var.get() == "volume")
        grouping = self._grouping_var.get()
        return self._fetch_symbol_analysis_impl(
            self._client, symbol, use_volume=use_volume, grouping=grouping,
        )

    # ── Worker Thread ──

    def _start_worker(self):
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self._collector_thread = threading.Thread(
            target=self._collector_loop, daemon=True)
        self._collector_thread.start()
        self._tick_countdown()

    def _collector_loop(self):
        """Daemon thread: own RW DB connection + the embedded GEX collector.
        Guarded by the shared lock file so a standalone collector won't also
        write. Interrupted by self._stop_event on window close."""
        import gex_collector as gc
        import gex_history_db as _hdb
        import time as _time
        # Durable file logging so an in-tool poll failure / crash traceback
        # lands in logs/gex_collector.log instead of being lost to the console.
        try:
            gc.ensure_file_logging()
        except Exception:
            pass
        try:
            # Wait for the lock instead of giving up after one try. This lets a
            # RESTART recover: if a previous instance was killed without
            # releasing the lock, its orphaned-but-fresh lock blocks us only
            # until it ages out (LOCK_TTL_SEC), after which we take over. A
            # genuinely live standalone collector keeps the lock fresh and we
            # stay idle ("external") until it stops. Interruptible by close.
            self._collector_external = True
            acquired = gc.wait_for_lock(
                gc.LOCK_PATH, source="gamma_tool",
                owner=self._collector_owner,
                now_fn=lambda: int(_time.time()),
                interrupted=self._stop_event.wait,
                check_interval=30)
            if not acquired:
                log.info("In-tool collector stopping before lock acquired "
                         "(window closed or live external collector).")
                return
            self._collector_external = False
            log.info("In-tool GEX collector acquired lock; collecting.")
            conn = None
            try:
                conn = _hdb.connect()
                _hdb.init_schema(conn)
                _hdb.purge_old(conn)
                _poll = gc.make_heartbeat_poll(
                    gc.LOCK_PATH, source="gamma_tool",
                    owner=self._collector_owner, client_lock=self._client_lock)
                gc.run_collector_loop(
                    self._client, GammaEngine(), conn,
                    stop_event=self._stop_event, poll=_poll)
            finally:
                if conn is not None:
                    conn.close()
                gc.release_lock(gc.LOCK_PATH, owner=self._collector_owner)
        except Exception:
            log.exception("In-tool GEX collector crashed")

    def _worker_loop(self):
        """Background loop: fetch immediately, then every REFRESH_INTERVAL seconds.

        All fetches run on this single worker thread.  ``_trigger_refresh``
        wakes the worker early via ``_refresh_event`` instead of spawning a
        second thread, which prevents concurrent fetch / snapshot corruption.
        """
        while not self._stop_event.is_set():
            self._do_fetch()
            self._countdown = self.REFRESH_INTERVAL
            # Wait, but wake early if refresh requested or stop signalled
            self._refresh_event.wait(self.REFRESH_INTERVAL)
            self._refresh_event.clear()

    def _tick_countdown(self):
        """Update countdown label every second."""
        if self._stop_event.is_set():
            return
        self._countdown = max(0, self._countdown - 1)
        mins, secs = divmod(self._countdown, 60)
        self._countdown_lbl.configure(text=f"Next refresh: {mins}:{secs:02d}")
        self.after(1000, self._tick_countdown)

    # ── Symbol defaults ──

    # Default strike grouping per symbol
    _SYMBOL_GROUPING = {"$VIX": 0.5}
    _DEFAULT_GROUPING = 1

    def _on_bar_hover(self, event):
        """Show/hide tooltip based on cursor position over left-panel bars.

        Three branches:
          1. Outside _ax_bars — hide if visible
          2. No cache (empty-data redraw) — hide if visible
          3. Inside _ax_bars with cache — find nearest strike within
             bar_height/2 of event.ydata; if found, populate and show;
             if cursor is in a gap, hide.
        """
        annot = self._hover_annotation
        if annot is None:
            return  # no redraw has created it yet (first frame)

        # Branch 1: cursor outside the bars axis (heatmap, title, margins).
        if event.inaxes is not self._ax_bars:
            if annot.get_visible():
                annot.set_visible(False)
                self._canvas.draw_idle()
            return

        # Branch 2: no cached strikes (placeholder/empty-data redraw).
        if not self._hover_strikes or event.ydata is None:
            if annot.get_visible():
                annot.set_visible(False)
                self._canvas.draw_idle()
            return

        # Branch 3: hit-test. Find nearest strike to event.ydata.
        y = event.ydata
        nearest = min(self._hover_strikes, key=lambda s: abs(s - y))
        tolerance = self._hover_bar_height / 2.0
        if abs(nearest - y) > tolerance:
            # Cursor in a gap between bars.
            if annot.get_visible():
                annot.set_visible(False)
                self._canvas.draw_idle()
            return

        # Hit. Build tooltip text.
        # Note: rstrip("0").rstrip(".") is safe here because this project's
        # strikes are always >= ~$18 (VIX floor); it would over-strip
        # hypothetical single-digit strikes like 10.0 -> "1".
        cell = self._hover_grid.get(nearest, {})
        strike_label = f"{nearest:,.1f}".rstrip("0").rstrip(".")
        text = (
            f"{strike_label}\n"
            f"Net:  {_fmt_dollar_magnitude(cell.get('net', 0.0))}\n"
            f"Call: {_fmt_dollar_magnitude(cell.get('call', 0.0))}\n"
            f"Put:  {_fmt_dollar_magnitude(cell.get('put', 0.0))}"
        )

        # Adaptive offset — flip tooltip to the left of the cursor when the
        # cursor is on the left half of the axes (near negative values) so
        # the tooltip doesn't go past the axes' left edge.
        if event.xdata is not None and event.xdata < 0:
            annot.set_position((-8, 0))
            annot.set_horizontalalignment("right")
        else:
            annot.set_position((8, 0))
            annot.set_horizontalalignment("left")

        annot.set_text(text)
        annot.xy = (event.xdata if event.xdata is not None else 0, nearest)
        annot.set_visible(True)
        self._canvas.draw_idle()

    def _on_symbol_change(self):
        """Update default grouping for the selected symbol, then refresh.

        History is per-symbol in the SQLite store, so no buffer to clear —
        switching symbols just re-queries the DB on next redraw.
        """
        sym = self._symbol_var.get()
        default_grp = self._SYMBOL_GROUPING.get(sym, self._DEFAULT_GROUPING)
        self._grouping_var.set(default_grp)
        self._refresh_term_button_state()
        self._trigger_refresh()

    def _on_formula_change(self):
        """Refresh after formula toggle.

        The collector only stores OI-based snapshots; if the user selects the
        volume formula, ``_load_history_dicts`` returns [] so history hides.
        """
        self._trigger_refresh()

    def _update_pressure_panel(self):
        """Refresh the 0-DTE delta-pressure panel based on view and _dex_data.

        Panel is shown only in the DEX and Charm views (hidden in GEX,
        Vanna, and Term). In DEX and Charm views, shows three
        $-magnitudes (now / projected close / hedge pressure) — Charm view
        shares the same projection because charm IS what produces the
        projected-close delta (delta_proj = delta + charm × dt). Falls back
        to a greyed "No 0-DTE" label when the chain has no same-day expiry.
        """
        view = self._view_var.get() if hasattr(self, "_view_var") else "gex"
        if view not in ("dex", "charm"):
            # GEX, Vanna, Term: no pressure/drift panel. (Vanna's drift data now
            # lives in the Explain popup.)
            self._pressure_frame.pack_forget()
            return

        # DEX/Charm path — existing behavior unchanged
        self._pressure_frame.pack(side="right", anchor="e")

        dex = self._dex_data
        now_val = dex.get("net_delta_0dte") if dex else None
        proj_val = dex.get("projected_net_delta_close") if dex else None
        hedge_val = dex.get("hedge_pressure") if dex else None

        if now_val is None:
            self._pressure_label_now.configure(
                text="No 0-DTE contracts", fg=FG_DIM,
            )
            self._pressure_label_proj.configure(text="")
            self._pressure_label_hedge.configure(text="")
            return

        # Projected-close label uses CLOSE_HOUR_CT / CLOSE_MIN_CT constants
        # from Task 3.
        self._pressure_label_now.configure(
            text=f"0-DTE \u0394 now:      {_fmt_dollar_magnitude(now_val)}",
            fg=FG_PRIMARY,
        )
        self._pressure_label_proj.configure(
            text=f"Projected {CLOSE_HOUR_CT:02d}:{CLOSE_MIN_CT:02d}: "
                 f"{_fmt_dollar_magnitude(proj_val)}",
            fg=FG_PRIMARY,
        )
        if hedge_val is None:
            direction = ""
            color = FG_PRIMARY
        elif hedge_val > 0:
            direction = " (buy)"
            color = "#3bd671"  # green
        elif hedge_val < 0:
            direction = " (sell)"
            color = "#e06c75"  # red
        else:
            direction = ""
            color = FG_PRIMARY
        self._pressure_label_hedge.configure(
            text=f"Hedge pressure:  {_fmt_dollar_magnitude(hedge_val)}{direction}",
            fg=color,
        )

    def _show_explain(self):
        """Open/refresh the plain-English Explain popup for the active view.

        Gathers the same in-memory snapshots the status strip uses
        (mirrors ``_update_collector_status`` ~5599) and hands them to the
        pure ``build_explain_text`` builder. All optional-data and
        sentiment/bridge access is guarded so a hiccup can never crash the
        popup.
        """
        view = self._view_var.get()
        gex_summary = (GammaEngine.snapshot_summary(self._engine.current, "gex")
                       if self._engine.current else None)
        charm_summary = (GammaEngine.snapshot_summary(self._charm_data, "charm")
                         if self._charm_data else None)
        dex_summary = (GammaEngine.snapshot_summary(self._dex_data, "dex")
                       if self._dex_data else None)
        spot = (self._engine.current.get("spot")
                if self._engine.current else None)
        vix_now, vix_open = _load_vix_today(self._db)
        vix_delta = (vix_now - vix_open
                     if vix_now is not None and vix_open is not None else None)

        # Build the drift panel whenever vanna data exists (not just on the
        # vanna tab) — the combined Explain page renders every view's section.
        drift_panel = None
        if self._vanna_data:
            try:
                charm_flip = charm_summary.get("flip") if charm_summary else None
                now = datetime.now(TZ)
                hours_to_close = max(
                    0.0,
                    (CLOSE_HOUR_CT - now.hour) + (CLOSE_MIN_CT - now.minute) / 60.0,
                )
                drift_panel = format_drift_pressure_panel(
                    vanna_data=self._vanna_data, charm_data=self._charm_data,
                    vix_now=vix_now, vix_open=vix_open,
                    spot=spot or 0.0, dte=self._engine._last_dte or 0,
                    expected_move=(spot * 0.005) if spot else 1.0,
                    hours_to_close=hours_to_close,
                    top5_oi=0, charm_flip=charm_flip,
                )
            except Exception:
                drift_panel = None

        try:
            from regime_filter import evaluate_regime
            sentiment = evaluate_regime()
        except Exception:
            sentiment = {"active": False}

        # Term view: derive near/far walls from the snapshot currently shown
        # in the term heatmap (set by _load_term_snapshot_at). None when not
        # on the term view or no SPXW snapshot is loaded.
        term_data = None
        if view == "term":
            try:
                term_data = _term_walls_from_rows(
                    getattr(self, "_term_last_rows", None))
            except Exception:
                term_data = None

        # Max pain / pin risk / magnet from the retained chain (quick win #1).
        max_pain_ctx = None
        try:
            chain = getattr(self._engine, "_last_chain", None)
            if chain is not None:
                mp = calc_max_pain_from_chain(chain)
                if mp is not None:
                    grid = (GammaEngine.group_gex(self._engine.current["gex"],
                                                  self._grouping_var.get())
                            if self._engine.current else {})
                    kg = find_key_gamma_strike(grid, spot or 0.0)
                    max_pain_ctx = {
                        "max_pain": mp["max_pain"],
                        "pin_risk": pin_risk(spot, mp["max_pain"], self._last_em),
                        "magnet": zero_dte_magnet(spot, mp["max_pain"], kg),
                    }
        except Exception:
            max_pain_ctx = None

        # Directional walls (quick win #2): GEX basis from the live grid,
        # OI basis from the retained chain.
        walls_ctx = None
        try:
            grid = (GammaEngine.group_gex(self._engine.current["gex"],
                                          self._grouping_var.get())
                    if self._engine.current else {})
            chain = getattr(self._engine, "_last_chain", None)
            walls_ctx = {
                "gex": get_directional_walls({"gex": grid, "spot": spot}, spot or 0.0),
                "oi": get_oi_walls(chain, spot or 0.0) if chain is not None
                else {"call_wall": None, "put_wall": None},
            }
        except Exception:
            walls_ctx = None

        ctx = {
            "symbol": self._symbol_var.get(), "spot": spot,
            "dte": self._engine._last_dte or 0,
            "vix_now": vix_now, "vix_delta": vix_delta,
            "gex_summary": gex_summary, "charm_summary": charm_summary,
            "dex_summary": dex_summary, "drift_panel": drift_panel,
            "sentiment": sentiment, "term_data": term_data,
            "max_pain": max_pain_ctx, "walls": walls_ctx,
            "pc_ratios": (calc_pc_ratios(getattr(self._engine, "_last_chain", None))
                          if getattr(self._engine, "_last_chain", None) is not None
                          else None),
            "oi_concentration": (
                calc_oi_concentration(getattr(self._engine, "_last_chain", None))
                if getattr(self._engine, "_last_chain", None) is not None else None),
            "hedge_shares": (
                dealer_hedge_shares(gex_summary.get("net_total"), spot)
                if gex_summary else None),
            "gamma_acceleration": (
                (calc_gamma_acceleration(getattr(self._engine, "_last_chain", None))
                 or {}).get("ratio")
                if getattr(self._engine, "_last_chain", None) is not None else None),
        }
        # One combined page covering every view + the Dealer Pinch section,
        # regardless of the active tab.
        text = build_explain_html_text(ctx)
        self._render_explain_popup(text)

    def _render_explain_popup(self, text):
        """Render the combined Explain page (all views + Dealer Pinch) to a
        styled HTML file and open it.

        Matches the Key-Levels page (dark theme, headers, colors, Google-search
        hyperlinks). Falls back to a minimal Tk text popup if HTML render/write
        fails, so the Explain button always shows something.
        """
        import webbrowser
        from pathlib import Path
        try:
            import html_render
            out_path = Path(__file__).parent / "data" / "explain.html"
            out_path.parent.mkdir(exist_ok=True)
            out_path.write_text(
                html_render.render_explain_html(
                    text, pinch_state=getattr(self, "_last_pinch_state", None),
                    symbol=self._symbol_var.get()),
                encoding="utf-8")
            webbrowser.open(out_path.as_uri())
        except Exception:
            log.exception("Explain HTML render failed; falling back to Tk popup")
            self._render_explain_popup_tk("explain", text)

    def _render_explain_popup_tk(self, view, text):
        """Fallback: scrollable Tk Text popup (used only if HTML render fails)."""
        title = {"gex": "GEX", "charm": "Charm", "dex": "DEX",
                 "vanna": "Vanna", "term": "Term"}.get(view, view).upper()
        win = getattr(self, "_explain_win", None)
        if win is None or not win.winfo_exists():
            win = tk.Toplevel(self)
            win.configure(bg=BG_PANEL)
            win.geometry("540x560")
            self._explain_win = win
            txt = tk.Text(win, wrap="word", bg=BG_INPUT, fg=FG_PRIMARY,
                          font=(FONT, 10), relief="flat", padx=12, pady=10,
                          borderwidth=0)
            sb = tk.Scrollbar(win, command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            txt.pack(side="top", fill="both", expand=True)
            btnf = tk.Frame(win, bg=BG_PANEL)
            btnf.pack(side="bottom", fill="x")
            tk.Button(btnf, text="Copy", command=lambda: (
                self.clipboard_clear(),
                self.clipboard_append(self._explain_txt.get("1.0", "end-1c"))),
                bg=BG_INPUT, fg=FG_PRIMARY, relief="flat", cursor="hand2",
                font=(FONT, 9)).pack(side="left", padx=6, pady=4)
            tk.Button(btnf, text="Close", command=win.destroy,
                bg=BG_INPUT, fg=FG_PRIMARY, relief="flat", cursor="hand2",
                font=(FONT, 9)).pack(side="right", padx=6, pady=4)
            self._explain_txt = txt
        win.title(f"Explain \u2014 {title} view")
        self._explain_txt.configure(state="normal")
        self._explain_txt.delete("1.0", "end")
        self._explain_txt.insert("1.0", text)
        self._explain_txt.configure(state="disabled")
        win.deiconify()
        win.lift()

    def _update_collector_status(self):
        """Refresh the collector health status label based on DB + current time."""
        view = self._view_var.get() if hasattr(self, "_view_var") else "gex"
        symbol = self._symbol_var.get() if hasattr(self, "_symbol_var") else "$SPX"

        if getattr(self, "_collector_external", False):
            self._status_label.configure(
                text="Collector: external", foreground="gray")
        else:
            age, last_ts = (None, None)
            has_data = False
            if self._db is not None:
                try:
                    age, last_ts = _history_db.last_snapshot_age(self._db, symbol, view)
                    has_data = last_ts is not None
                except sqlite3.OperationalError:
                    pass

            text, color = classify_collector_status(
                age_seconds=age,
                now_ct=datetime.now(STATUS_TZ),
                has_data=has_data,
                last_ts=last_ts,
            )
            self._status_label.configure(text=text, foreground=color)

        # Key-levels headline (view-aware). Built from current in-memory
        # snapshots; safe to call even when state is partially populated —
        # _drift_headline_text returns "" if the required data is missing.
        gex_summary = (GammaEngine.snapshot_summary(self._engine.current)
                       if self._engine.current else None)
        charm_summary = (GammaEngine.snapshot_summary(self._charm_data)
                         if self._charm_data else None)
        drift_panel_dict = None
        if view == "vanna" and self._vanna_data:
            vix_now, vix_open = _load_vix_today(self._db)
            charm_flip = charm_summary.get("flip") if charm_summary else None
            spot = (self._engine.current.get("spot")
                    if self._engine.current else 0.0) or 0.0
            now = datetime.now(TZ)
            hours_to_close = max(
                0.0,
                (CLOSE_HOUR_CT - now.hour) + (CLOSE_MIN_CT - now.minute) / 60.0,
            )
            try:
                drift_panel_dict = format_drift_pressure_panel(
                    vanna_data=self._vanna_data, charm_data=self._charm_data,
                    vix_now=vix_now, vix_open=vix_open,
                    spot=spot, dte=self._engine._last_dte or 0,
                    expected_move=(spot * 0.005) if spot else 1.0,
                    hours_to_close=hours_to_close,
                    top5_oi=0, charm_flip=charm_flip,
                )
            except Exception:
                drift_panel_dict = None
        # Dealer Pinch flag (rendered from the worker-computed state; no fetch
        # on this UI-thread path). Fill forced-hedge direction from the drift
        # pair-state when it's available (vanna view).
        try:
            state = getattr(self, "_last_pinch_state", None)
            if state is not None and drift_panel_dict:
                st = drift_panel_dict.get("pair_state", "")
                state["forced_hedge_dir"] = (
                    "up" if st == "AGREE_UP" else
                    "down" if st == "AGREE_DOWN" else
                    state.get("forced_hedge_dir"))
            ptext, pcolor = pinch_flag_text(state)
            if hasattr(self, "_pinch_lbl"):
                self._pinch_lbl.configure(text=ptext, foreground=pcolor or FG_DIM)
        except Exception:
            pass

        headline = _drift_headline_text(
            view, gex_summary, charm_summary, self._dex_data, drift_panel_dict,
        )
        if hasattr(self, "_headline_label"):
            self._headline_label.configure(text=headline)

    def _fetch_last_close(self, symbol):
        """Return yesterday's (or most recent trading day's) close, cached per session.

        One API call per symbol per session. On failure, returns None and avoids
        retry via self._last_close_attempted. Subsequent calls for the same
        symbol during the session hit cache without re-raising.
        """
        if symbol in self._last_close_cache:
            return self._last_close_cache[symbol]
        if symbol in self._last_close_attempted:
            return None
        self._last_close_attempted.add(symbol)
        try:
            from scanner_engine import fetch_price_history
            hist = fetch_price_history(self._client, symbol)
            candles = (hist or {}).get("candles") or []
            if not candles:
                self._last_close_cache[symbol] = None
                return None
            # Most recent completed trading day = last candle in the list.
            last = candles[-1]
            close = float(last.get("close") or 0)
            if close <= 0:
                self._last_close_cache[symbol] = None
                return None
            self._last_close_cache[symbol] = close
            return close
        except Exception as e:
            log.warning("fetch_last_close failed for %s: %s", symbol, e)
            self._last_close_cache[symbol] = None
            return None

    def _load_history_dicts(self, view="gex") -> list[dict]:
        """Load today's snapshots from SQLite as snapshot_summary-shaped dicts.

        Accepts either a view string ('gex', 'charm', 'dex') or a legacy bool
        (True → 'charm', False → 'gex') for back-compat.

        Returns empty list if DB is unavailable, the user chose a formula the
        collector doesn't store (volume), or the query fails.
        """
        # Back-compat: older callers pass is_charm=True/False.
        if view is True:
            view = "charm"
        elif view is False:
            view = "gex"
        if self._db is None:
            return []
        # Collector stores only use_volume=False snapshots. If the user has
        # toggled volume-based formula, history is meaningless — hide it.
        try:
            if str(self._formula_var.get()).lower().startswith("vol"):
                return []
        except Exception:
            pass
        symbol = self._symbol_var.get()
        try:
            rows = _history_db.load_today_with_grid(self._db, symbol, view)
        except sqlite3.OperationalError:
            return []
        out = []
        for r in rows:
            ts_raw = r[0]
            try:
                ts_dt = datetime.fromtimestamp(int(ts_raw), TZ)
            except (TypeError, ValueError, OSError):
                continue
            out.append({
                "ts": ts_dt,
                "spot": r[1],
                "flip": r[2],
                "top_pos_strike": r[3],
                "top_neg_strike": r[4],
                "net_total": r[5],
                "gex": r[6],
            })
        return out

    def _trigger_refresh(self):
        """Manual refresh: wake the worker thread to fetch immediately."""
        self._status_lbl.configure(text="Refreshing...")
        self._countdown = 0
        self._refresh_event.set()  # wake worker

    # ── AI Analysis ──

    def _analyze(self, auto=False, slot_tag=None):
        """Slot-routed bundled SPX/SPY/QQQ analysis. Writes two prompt files
        per slot to data/. No PNGs."""
        if slot_tag is None:
            slot_tag = "manual"

        # In-flight guard — prevents overlapping runs if a manual click arrives
        # while a previous (auto or manual) run is still fetching, or vice-versa.
        if getattr(self, "_analyze_inflight", False):
            if auto:
                log.warning("Auto-analyze %s arrived while a run is in flight; skipping", slot_tag)
            return

        spx_engine_data = self._engine.current
        spx_charm = self._charm_data
        spx_dex = self._dex_data
        spx_vanna = self._vanna_data
        if spx_engine_data is None and spx_charm is None and spx_dex is None and spx_vanna is None:
            if not auto:
                self._status_lbl.configure(text="No SPX data to analyze")
            else:
                log.warning("Auto-analyze %s: SPX data missing; skipping", slot_tag)
            return

        # Read Tk vars on the main thread BEFORE dispatching the worker.
        # Tkinter is not thread-safe — touching StringVar/DoubleVar from a
        # daemon thread can raise "main thread is not in main loop".
        use_volume = (self._formula_var.get() == "volume")
        grouping = self._grouping_var.get()
        client = self._client

        self._analyze_inflight = True
        self._analyze_btn.configure(text="\U0001f916 Bundling...", state="disabled")

        spx_blocks = {
            "gex":   self._build_analysis_data(spx_engine_data, view="gex")   if spx_engine_data else None,
            "charm": self._build_analysis_data(spx_charm, view="charm")       if spx_charm       else None,
            "dex":   self._build_analysis_data(spx_dex, view="dex")           if spx_dex         else None,
            "vanna": self._build_analysis_data(spx_vanna, view="vanna")       if spx_vanna       else None,
        }

        def _worker():
            try:
                spy = GammaWindow._fetch_symbol_analysis_impl(
                    client, "SPY", use_volume=use_volume, grouping=grouping)
            except Exception:
                log.exception("SPY fetch failed in worker")
                spy = None
            try:
                qqq = GammaWindow._fetch_symbol_analysis_impl(
                    client, "QQQ", use_volume=use_volume, grouping=grouping)
            except Exception:
                log.exception("QQQ fetch failed in worker")
                qqq = None
            try:
                internals = _fetch_market_internals(client)
            except Exception:
                log.exception("Market internals fetch failed in worker")
                internals = {}
            self.after(0, lambda: self._finalize_analyze(
                auto, slot_tag, spx_blocks, spy, qqq, internals))

        threading.Thread(target=_worker, daemon=True).start()

    def _finalize_analyze(self, auto, slot_tag, spx_blocks, spy_blocks,
                          qqq_blocks, internals):
        """Main-thread continuation after SPY/QQQ fetches return.

        Builds the detail + summary prompts via the bundled builders, writes
        slot-tagged files under data/, optionally copies/opens for manual
        runs, and updates the status bar. Always re-enables the button.
        """
        try:
            premarket = (slot_tag == "0820")

            # Gather SPX intraday-evolution history (intraday slots only).
            spx_history = None
            if not premarket:
                spx_history = [
                    ("GEX",   self._load_history_dicts("gex")),
                    ("Charm", self._load_history_dicts("charm")),
                    ("DEX",   self._load_history_dicts("dex")),
                    ("Vanna", self._load_history_dicts("vanna")),
                ]

            img_dir = Path(__file__).parent / "data"
            img_dir.mkdir(exist_ok=True)

            # Write JSON sidecar (best-effort — don't kill the whole finalize)
            try:
                write_slot_data_json(img_dir, slot_tag, spx_blocks, spy_blocks,
                                     qqq_blocks, internals)
            except Exception:
                log.exception("Failed to write slot JSON sidecar")

            # For 1500, gather earlier same-day JSONs and build the retrospective.
            path_block = None
            if slot_tag == "1500":
                jsons = {}
                for s in _RETROSPECTIVE_SLOTS:
                    j = read_today_slot_data(img_dir, s)
                    if j:
                        jsons[s] = j

                def _current_spot(blocks):
                    if blocks is None:
                        return None
                    for view in ("gex", "charm", "dex", "vanna"):
                        v = blocks.get(view)
                        if v and v.get("spot") is not None:
                            return v["spot"]
                    return None

                current_spots = {
                    "SPX": _current_spot(spx_blocks),
                    "SPY": _current_spot(spy_blocks),
                    "QQQ": _current_spot(qqq_blocks),
                }
                path_block = build_todays_path_block(jsons, current_spots)

            prompt = build_combined_prompt_bundled(
                spx_blocks, spy_blocks, qqq_blocks,
                premarket=premarket, spx_history=spx_history,
                internals=internals, slot_tag=slot_tag,
                todays_path_block=path_block,
            )
            summary = build_summary_prompt_bundled(
                spx_blocks, spy_blocks, qqq_blocks,
                premarket=premarket, internals=internals,
            )

            detail_name, summary_name = slot_filenames(slot_tag)
            (img_dir / detail_name).write_text(prompt, encoding="utf-8")
            (img_dir / summary_name).write_text(summary, encoding="utf-8")

            if not auto:
                self.clipboard_clear()
                self.clipboard_append(prompt)
                os.startfile(str(img_dir))

            n_ok = sum(1 for b in (spx_blocks, spy_blocks, qqq_blocks) if b)
            if auto:
                self._status_lbl.configure(
                    text=f"Auto {slot_tag.upper()}: {n_ok}/3 symbols, 2 prompts")
            else:
                self._status_lbl.configure(
                    text=f"Manual: {n_ok}/3 symbols · detail copied · folder opened")
            log.info("Slot %s bundle written: SPX=%s SPY=%s QQQ=%s",
                     slot_tag, bool(spx_blocks), bool(spy_blocks), bool(qqq_blocks))
        except Exception as e:
            log.exception("Finalize analyze failed: %s", e)
            try:
                self._status_lbl.configure(text=f"Analyze error: {str(e)[:30]}")
            except Exception:
                pass
        finally:
            self._analyze_inflight = False
            # Re-enable after a grace period, guarded against the window being
            # destroyed during the 5s wait.
            def _reenable():
                try:
                    if self.winfo_exists():
                        self._analyze_btn.configure(
                            text="\U0001f916 Analyze", state="normal")
                except Exception:
                    pass
            self.after(5000, _reenable)

    def _schedule_next_auto_analyze(self):
        """Compute delay to next scheduled auto-analyze and arm self.after().

        Weekday-only (Mon-Fri). If all of today's trigger times are in the
        past, rolls forward through weekend to next Monday's first slot.
        """
        from datetime import timedelta
        now = datetime.now(TZ)

        # Search up to 4 days forward (worst case: Fri evening → Mon morning).
        for day_offset in range(5):
            candidate_dt = now + timedelta(days=day_offset)
            if candidate_dt.weekday() >= 5:
                continue  # Skip Sat/Sun
            candidate_date = candidate_dt.date()
            for (h, m) in self._auto_analyze_times:
                target = datetime(
                    candidate_date.year, candidate_date.month, candidate_date.day,
                    h, m, 0, tzinfo=TZ,
                )
                if target > now:
                    delay_ms = int((target - now).total_seconds() * 1000)
                    self._auto_analyze_timer_id = self.after(
                        delay_ms, self._on_auto_analyze_fire,
                    )
                    log.info("Next auto-analyze scheduled for %s (%.1f min away)",
                             target.strftime("%a %H:%M CT"), delay_ms / 60000)
                    return
        # Safety fallback — should never hit given the 5-day window.
        log.warning("No auto-analyze slot found within 5-day window; not scheduling")

    def _on_auto_analyze_fire(self):
        """Timer-fired auto-analyze. Identifies the slot tag for the current
        fire time and passes it to _analyze. Never propagates exceptions —
        scheduler must survive any single-run failure and queue the next slot.
        """
        self._auto_analyze_timer_id = None
        try:
            now = datetime.now(TZ)
            tag = slot_tag_for_time(now.hour, now.minute)
            if tag is None:
                # Allow up to 90 seconds of drift in either direction.
                for (h, m), candidate in _FIRE_TIME_TO_SLOT.items():
                    target = datetime(now.year, now.month, now.day, h, m, tzinfo=TZ)
                    if abs((now - target).total_seconds()) <= 90:
                        tag = candidate
                        break
            if tag is None:
                log.warning("Auto-analyze fired but no slot tag matched %s", now)
            else:
                self._analyze(auto=True, slot_tag=tag)
        except Exception:
            log.exception("Auto-analyze fire failed; continuing scheduler")
        finally:
            self._schedule_next_auto_analyze()

    def _build_analysis_data(self, data, view="gex", is_charm=None):
        """Extract structured analysis data from a GEX/Charm/DEX snapshot."""
        # Back-compat: older callers pass is_charm=True/False.
        if is_charm is True:
            view = "charm"
        elif is_charm is False and view == "gex":
            view = "gex"
        spot = data["spot"]
        gex_raw = data["gex"]
        grouping = self._grouping_var.get()
        gex = GammaEngine.group_gex(gex_raw, grouping)
        symbol = self._symbol_var.get()
        dte = self._engine._last_dte

        now = datetime.now(TZ)
        close_hour, close_min = 15, 15
        hours_left = max(0, (close_hour - now.hour) + (close_min - now.minute) / 60.0)

        # Top 20 positive/negative strikes + tail aggregate so the LLM sees the
        # full distribution shape from structured data alone (no chart attached).
        top_pos, top_neg, tail = top_strikes_with_tail(gex, n=20)

        # Per-top-strike intraday context: change vs prior snapshot and value
        # at market open. Sources differ by view:
        #   GEX  → engine.previous / engine.market_open
        #   Charm/DEX → window-tracked _prev_*_data / _open_*_data
        if view == "charm":
            prev_data = self._prev_charm_data
            open_data = self._open_charm_data
        elif view == "dex":
            prev_data = self._prev_dex_data
            open_data = self._open_dex_data
        elif view == "vanna":
            prev_data = self._prev_vanna_data
            open_data = self._open_vanna_data
        else:
            prev_data = self._engine.previous
            open_data = self._engine.market_open

        prev_grid = (
            GammaEngine.group_gex(prev_data["gex"], grouping)
            if prev_data and prev_data.get("gex") else None
        )
        open_grid = (
            GammaEngine.group_gex(open_data["gex"], grouping)
            if open_data and open_data.get("gex") else None
        )
        top_strikes = [item["strike"] for item in top_pos] + \
                      [item["strike"] for item in top_neg]
        delta_change = delta_change_for_strikes(top_strikes, gex, prev_grid)
        value_at_open = value_at_open_for_strikes(top_strikes, open_grid)

        # 0-DTE pressure panel (DEX only). Numerical projection of intraday
        # delta state: current 0-DTE delta, projected close delta, hedge
        # pressure direction, and the projected EOD flip strike.
        pressure_panel = format_pressure_panel(data, spot) if view == "dex" else None

        # Flip point: where net crosses zero near spot
        flip_point = self._calc_flip_point(gex, spot)

        # Net by zone
        zones = {"above_0_2pct": 0, "below_0_2pct": 0, "below_2_5pct": 0}
        for s, vals in gex.items():
            net = vals["net"]
            if s > spot and s <= spot * 1.02:
                zones["above_0_2pct"] += net
            elif s < spot and s >= spot * 0.98:
                zones["below_0_2pct"] += net
            elif s < spot * 0.98 and s >= spot * 0.95:
                zones["below_2_5pct"] += net

        # ATM breakdown (5 strikes nearest spot)
        near_strikes = sorted(gex.keys(), key=lambda s: abs(s - spot))[:5]
        atm_breakdown = []
        for s in sorted(near_strikes):
            d = gex[s]
            atm_breakdown.append({
                "strike": s, "call": d["call"], "put": d["put"], "net": d["net"]
            })

        view_label = {"gex": "GEX", "charm": "Charm", "dex": "DEX", "vanna": "Vanna"}.get(view, "GEX")
        return {
            "view": view_label,
            "symbol": symbol,
            "spot": spot,
            "dte": dte,
            "expected_move": self._last_em,
            "em_upper": round(spot + self._last_em, 2) if self._last_em else None,
            "em_lower": round(spot - self._last_em, 2) if self._last_em else None,
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
        }

    @staticmethod
    def _calc_flip_point(gex, spot):
        """Find the strike near spot where net GEX/Charm crosses from positive to negative."""
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

    # ── Cleanup ──

    def _on_close(self):
        self._stop_event.set()
        self._refresh_event.set()          # wake worker so it exits promptly
        # Join the collector thread briefly so its lock is released before the
        # process may exit.
        if getattr(self, "_collector_thread", None) is not None:
            # If the join times out mid-poll the lock may not be released here, but the
            # lock TTL (gex_collector.LOCK_TTL_SEC) backstops it — the next collector reclaims a stale lock.
            self._collector_thread.join(timeout=2.0)
        # Cancel pending auto-analyze timer so it doesn't fire on a dead widget.
        if self._auto_analyze_timer_id is not None:
            try:
                self.after_cancel(self._auto_analyze_timer_id)
            except Exception:
                pass
            self._auto_analyze_timer_id = None
        if getattr(self, "_db", None) is not None:
            try:
                self._db.close()
            except Exception:
                pass
        import matplotlib.pyplot as plt
        plt.close(self._fig)
        self.destroy()
