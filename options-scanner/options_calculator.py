"""
options_calculator.py - Black-Scholes Calculation Engine
Version: 1.0.0
Last Updated: 2026-04-07

Pure calculation module for option pricing, Greeks, spread summaries,
and P&L projection. No GUI dependencies.

Uses only stdlib math and datetime - no numpy, scipy, or external deps.

Version 1.0.0 Changes:
- Initial implementation
- Black-Scholes pricing and delta
- Spread summary metrics (credit, max P/L, breakevens, PoP)
- P&L grid generation for visualization
"""

import math
import logging
from datetime import datetime, date, timedelta

log = logging.getLogger("scanner")

UNLIMITED = 999999  # placeholder for unlimited risk/reward in single-leg strategies

#############################################
# NORMAL DISTRIBUTION HELPERS
#############################################

def norm_cdf(x):
    """
    Cumulative distribution function for the standard normal distribution.
    Uses stdlib math.erfc (Abramowitz & Stegun rational approximation
    under the hood). No scipy dependency.
    """
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_pdf(x):
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


#############################################
# BLACK-SCHOLES PRICING
#############################################

def _d1_d2(S, K, T, r, sigma):
    """Compute d1 and d2 for Black-Scholes formula."""
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return d1, d2


def bs_price(S, K, T, r, sigma, option_type):
    """
    Black-Scholes option price.

    Parameters:
        S          - Current spot price
        K          - Strike price
        T          - Time to expiration in years
        r          - Risk-free interest rate (annualized)
        sigma      - Implied volatility (annualized)
        option_type - 'call' or 'put'

    Returns intrinsic value when T <= 0 (at or past expiration).
    """
    option_type = option_type.lower()
    if T <= 0:
        if option_type == "call":
            return max(S - K, 0.0)
        else:
            return max(K - S, 0.0)

    d1, d2 = _d1_d2(S, K, T, r, sigma)

    if option_type == "call":
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def implied_vol(price, S, K, T, r, option_type, lo=1e-4, hi=5.0, iters=100):
    """
    Implied volatility back-solved from an option price via bisection on bs_price.

    bs_price is monotonically increasing in sigma, so bisection is robust and
    needs no derivative. ToS-style: pass an *intraday* T (e.g. hours-to-close/365)
    and the option *mark* to reproduce the broker's displayed IV at 0DTE.

    Returns None (rather than a misleading number) when the price carries no
    implied time value: non-positive price/spot/strike, T <= 0 (expired), a price
    at or below intrinsic, or a price unreachable within [lo, hi].
    """
    option_type = option_type.lower()
    if (price is None or price <= 0 or T is None or T <= 0
            or S is None or S <= 0 or K is None or K <= 0):
        return None
    intrinsic = max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
    if price <= intrinsic + 1e-9:
        return None
    # Price must be bracketed by [bs_price(lo), bs_price(hi)] for bisection.
    if bs_price(S, K, T, r, hi, option_type) < price:
        return None
    a, b = lo, hi
    for _ in range(iters):
        mid = 0.5 * (a + b)
        if bs_price(S, K, T, r, mid, option_type) < price:
            a = mid
        else:
            b = mid
    return 0.5 * (a + b)


def bs_delta(S, K, T, r, sigma, option_type):
    """
    Black-Scholes delta.

    Parameters:
        S          - Current spot price
        K          - Strike price
        T          - Time to expiration in years
        r          - Risk-free interest rate (annualized)
        sigma      - Implied volatility (annualized)
        option_type - 'call' or 'put'

    Returns 1/-1 or 0 at expiration depending on moneyness.
    """
    option_type = option_type.lower()
    if T <= 0:
        if option_type == "call":
            return 1.0 if S > K else 0.0
        else:
            return -1.0 if S < K else 0.0

    d1, _ = _d1_d2(S, K, T, r, sigma)

    if option_type == "call":
        return norm_cdf(d1)
    else:
        return norm_cdf(d1) - 1.0


def bs_charm(S, K, T, r, sigma, option_type):
    """
    Black-Scholes charm (delta decay: d(delta)/d(time)).

    Measures how delta changes as time passes. Positive charm means
    delta is increasing; negative means delta is decreasing.

    Parameters:
        S          - Current spot price
        K          - Strike price
        T          - Time to expiration in years (must be > 0)
        r          - Risk-free interest rate (annualized)
        sigma      - Implied volatility (annualized)
        option_type - 'call' or 'put'

    Returns 0.0 at or past expiration.
    """
    option_type = option_type.lower()
    if T <= 0 or sigma <= 0:
        return 0.0

    d1, d2 = _d1_d2(S, K, T, r, sigma)
    sqrt_T = math.sqrt(T)
    pdf_d1 = norm_pdf(d1)

    charm_call = -pdf_d1 * (2.0 * r * T - d2 * sigma * sqrt_T) / (2.0 * T * sigma * sqrt_T)

    if option_type == "call":
        return charm_call
    else:
        return charm_call + r * math.exp(-r * T)


def bs_vanna(S, K, T, r, sigma, option_type="call"):
    """
    Black-Scholes vanna (d(delta)/d(sigma) = d(vega)/d(S)).

    Measures how delta changes per unit change in implied volatility.
    Scaled to **per 1 vol-point** (divides analytical vanna by 100) so callers
    can multiply by raw IV deltas in percent without extra scaling.

    Same value for calls and puts. Returns 0.0 at/past expiration and for
    non-positive sigma.

    Parameters:
        S          - Current spot price
        K          - Strike price
        T          - Time to expiration in years (must be > 0)
        r          - Risk-free interest rate (annualized)
        sigma      - Implied volatility (annualized)
        option_type - 'call' or 'put' (ignored — vanna is symmetric)
    """
    if T <= 0 or sigma <= 0:
        return 0.0

    d1, d2 = _d1_d2(S, K, T, r, sigma)
    return -norm_pdf(d1) * d2 / sigma / 100.0


def bs_gamma(S, K, T, r, sigma, option_type="call"):
    """
    Black-Scholes gamma (d²V/dS²).

    Parameters:
        S          - Current spot price
        K          - Strike price
        T          - Time to expiration in years (must be > 0)
        r          - Risk-free interest rate (annualized)
        sigma      - Implied volatility (annualized)
        option_type - 'call' or 'put' (ignored — gamma is symmetric for both)

    Returns 0.0 at or past expiration and for non-positive sigma.
    """
    if T <= 0 or sigma <= 0:
        return 0.0

    d1, _ = _d1_d2(S, K, T, r, sigma)
    return norm_pdf(d1) / (S * sigma * math.sqrt(T))


#############################################
# EXTENDED GREEKS (options simulator)
#
# These accept scalar or numpy-array S/K. They mirror the
# (S, K, T, r, sigma, option_type) convention of the stdlib
# helpers above and use numpy/scipy internally so the simulator
# can evaluate a full strike vector in one call.
#############################################

def _as_array(x):
    import numpy as np
    return np.asarray(x, dtype=float)


def _is_vectorized(*args):
    """Return True if any positional arg is array-like with ndim>0."""
    import numpy as np
    for a in args:
        arr = np.asarray(a)
        if arr.ndim > 0:
            return True
    return False


def bs_price_v(S, K, T, r, sigma, option_type="call"):
    """Vectorized Black-Scholes theoretical price. Accepts scalar or array S/K."""
    import numpy as np
    from scipy.stats import norm
    option_type = option_type.lower()
    S = _as_array(S)
    K = _as_array(K)
    if T <= 0:
        intrinsic = np.maximum(S - K, 0.0) if option_type == "call" else np.maximum(K - S, 0.0)
        return intrinsic if intrinsic.ndim else float(intrinsic)
    if sigma <= 0:
        intrinsic = np.maximum(S - K, 0.0) if option_type == "call" else np.maximum(K - S, 0.0)
        return intrinsic if intrinsic.ndim else float(intrinsic)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == "call":
        out = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        out = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return out if out.ndim else float(out)


def bs_delta_v(S, K, T, r, sigma, option_type="call"):
    """Vectorized Black-Scholes delta."""
    import numpy as np
    from scipy.stats import norm
    option_type = option_type.lower()
    S = _as_array(S)
    K = _as_array(K)
    if T <= 0:
        if option_type == "call":
            out = np.where(S > K, 1.0, 0.0)
        else:
            out = np.where(S < K, -1.0, 0.0)
        return out if out.ndim else float(out)
    if sigma <= 0:
        if option_type == "call":
            out = np.where(S > K, 1.0, 0.0)
        else:
            out = np.where(S < K, -1.0, 0.0)
        return out if out.ndim else float(out)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    if option_type == "call":
        out = norm.cdf(d1)
    else:
        out = norm.cdf(d1) - 1.0
    return out if out.ndim else float(out)


def bs_gamma_v(S, K, T, r, sigma, option_type="call"):
    """Vectorized Black-Scholes gamma."""
    import numpy as np
    from scipy.stats import norm
    S = _as_array(S)
    K = _as_array(K)
    if T <= 0 or sigma <= 0:
        z = np.zeros_like(S * K)
        return z if z.ndim else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    out = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    return out if out.ndim else float(out)


def bs_vanna_v(S, K, T, r, sigma, option_type="call"):
    """Vectorized Black-Scholes vanna, per 1 vol-point. Accepts scalar or array S/K."""
    import numpy as np
    from scipy.stats import norm
    S = _as_array(S)
    K = _as_array(K)
    if T <= 0 or sigma <= 0:
        z = np.zeros_like(S * K)
        return z if z.ndim else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    out = -norm.pdf(d1) * d2 / sigma / 100.0
    return out if out.ndim else float(out)


def bs_theta(S, K, T, r, sigma, option_type="call"):
    """
    Black-Scholes theta per-day (annual theta / 365).

    Negative for long options. Returns 0 at/past expiration.
    Accepts scalar or numpy-array S/K.
    """
    import numpy as np
    from scipy.stats import norm
    option_type = option_type.lower()
    S = _as_array(S)
    K = _as_array(K)
    if T <= 0 or sigma <= 0:
        z = np.zeros_like(S * K)
        return z if z.ndim else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    term1 = -(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
    if option_type == "call":
        term2 = -r * K * np.exp(-r * T) * norm.cdf(d2)
    else:
        term2 = r * K * np.exp(-r * T) * norm.cdf(-d2)
    out = (term1 + term2) / 365.0
    return out if out.ndim else float(out)


def bs_vega(S, K, T, r, sigma, option_type="call"):
    """
    Black-Scholes vega per 1 vol point (annual vega / 100).

    Same for calls and puts. Returns 0 at/past expiration.
    Accepts scalar or numpy-array S/K.
    """
    import numpy as np
    from scipy.stats import norm
    S = _as_array(S)
    K = _as_array(K)
    if T <= 0 or sigma <= 0:
        z = np.zeros_like(S * K)
        return z if z.ndim else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    out = S * np.sqrt(T) * norm.pdf(d1) / 100.0
    return out if out.ndim else float(out)


def bs_rho(S, K, T, r, sigma, option_type="call"):
    """
    Black-Scholes rho per 1% rate move (annual rho / 100).

    Returns 0 at/past expiration. Accepts scalar or numpy-array S/K.
    """
    import numpy as np
    from scipy.stats import norm
    option_type = option_type.lower()
    S = _as_array(S)
    K = _as_array(K)
    if T <= 0 or sigma <= 0:
        z = np.zeros_like(S * K)
        return z if z.ndim else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == "call":
        out = K * T * np.exp(-r * T) * norm.cdf(d2) / 100.0
    else:
        out = -K * T * np.exp(-r * T) * norm.cdf(-d2) / 100.0
    return out if out.ndim else float(out)


def bs_greeks(S, K, T, r, sigma, option_type="call"):
    """
    Full Greek bundle in one call.

    Returns a dict with keys: price, delta, gamma, theta, vega, rho.
    Each value is a scalar float or numpy array, matching the input shape.
    Accepts scalar or numpy-array S/K.
    """
    return {
        "price": bs_price_v(S, K, T, r, sigma, option_type),
        "delta": bs_delta_v(S, K, T, r, sigma, option_type),
        "gamma": bs_gamma_v(S, K, T, r, sigma, option_type),
        "vanna": bs_vanna_v(S, K, T, r, sigma, option_type),
        "theta": bs_theta(S, K, T, r, sigma, option_type),
        "vega":  bs_vega(S, K, T, r, sigma, option_type),
        "rho":   bs_rho(S, K, T, r, sigma, option_type),
    }


#############################################
# SPREAD SUMMARY
#############################################

def calc_summary(legs, strategy, spot, r=0.045, iv=0.20, T=None):
    """
    Calculate summary metrics for a credit spread or iron condor.

    Parameters:
        legs     - List of leg dicts, each with keys:
                     strike, option_type ('call'/'put'),
                     side ('long'/'short'), premium, qty
        strategy - 'CCS' (call credit spread), 'PCS' (put credit spread),
                   or 'IC' (iron condor)
        spot     - Current underlying price
        r        - Risk-free rate (default 0.045)
        iv       - Implied volatility (default 0.20)
        T        - Time to expiration in years (default None -> 1/365)

    Returns dict with: entry_credit, max_profit, max_loss,
        breakevens, return_on_risk, pop
    """
    if T is None:
        T = 1.0 / 365.0

    strategy = strategy.upper()

    SINGLE_LEG = {"LONG_PUT", "LONG_CALL", "NAKED_PUT", "NAKED_CALL"}
    if strategy in SINGLE_LEG:
        leg = legs[0]
        qty = leg.get("qty", 1)
        prem = leg["premium"]
        prem_total = prem * qty * 100
        strike = leg["strike"]

        if strategy == "LONG_PUT":
            entry_credit = -prem_total  # debit
            max_profit = strike * qty * 100 - prem_total
            max_loss = prem_total
            breakevens = [strike - prem]
        elif strategy == "LONG_CALL":
            entry_credit = -prem_total  # debit
            max_profit = UNLIMITED
            max_loss = prem_total
            breakevens = [strike + prem]
        elif strategy == "NAKED_PUT":
            entry_credit = prem_total
            max_profit = prem_total
            max_loss = strike * qty * 100 - prem_total
            breakevens = [strike - prem]
        else:  # NAKED_CALL
            entry_credit = prem_total
            max_profit = prem_total
            max_loss = UNLIMITED
            breakevens = [strike + prem]

        if max_loss in (0, UNLIMITED) or max_profit == UNLIMITED:
            return_on_risk = 0.0
        else:
            return_on_risk = (max_profit / max_loss * 100) if max_loss > 0 else 0.0

        pop = _estimate_pop(legs, strategy, spot, r, iv, T)

        return {
            "entry_credit": round(entry_credit, 2),
            "max_profit": round(max_profit, 2) if max_profit != UNLIMITED else UNLIMITED,
            "max_loss": round(max_loss, 2) if max_loss != UNLIMITED else UNLIMITED,
            "breakevens": [round(b, 2) for b in breakevens],
            "return_on_risk": round(return_on_risk, 2),
            "pop": round(pop, 2),
        }

    # --- entry_credit: net premium received ---
    # Sum short premiums minus long premiums, scaled by qty * 100
    total_short_premium = 0.0
    total_long_premium = 0.0
    for leg in legs:
        prem_per_share = leg["premium"]
        qty = leg.get("qty", 1)
        if leg["side"] == "short":
            total_short_premium += prem_per_share * qty * 100
        else:
            total_long_premium += prem_per_share * qty * 100

    entry_credit = total_short_premium - total_long_premium

    # --- credit_per_share: for breakeven calculation ---
    # This is the net credit per share (not scaled by 100 or qty)
    short_prem_per_share = sum(
        l["premium"] for l in legs if l["side"] == "short"
    )
    long_prem_per_share = sum(
        l["premium"] for l in legs if l["side"] == "long"
    )
    credit_per_share = short_prem_per_share - long_prem_per_share

    # --- Identify strikes by role ---
    short_legs = [l for l in legs if l["side"] == "short"]
    long_legs = [l for l in legs if l["side"] == "long"]

    # Use first leg's qty for width calculation (assumes uniform qty)
    qty = legs[0].get("qty", 1) if legs else 1

    if strategy == "IC":
        # Iron Condor: has both call and put spreads
        short_calls = [l for l in short_legs if l["option_type"].lower() == "call"]
        short_puts = [l for l in short_legs if l["option_type"].lower() == "put"]
        long_calls = [l for l in long_legs if l["option_type"].lower() == "call"]
        long_puts = [l for l in long_legs if l["option_type"].lower() == "put"]

        call_width = abs(long_calls[0]["strike"] - short_calls[0]["strike"]) if long_calls and short_calls else 0
        put_width = abs(short_puts[0]["strike"] - long_puts[0]["strike"]) if long_puts and short_puts else 0

        max_profit = entry_credit
        max_loss = (max(call_width, put_width) * qty * 100) - entry_credit

        short_put_strike = short_puts[0]["strike"] if short_puts else 0
        short_call_strike = short_calls[0]["strike"] if short_calls else 0

        breakevens = [
            short_put_strike - credit_per_share,
            short_call_strike + credit_per_share,
        ]
    else:
        # CCS or PCS: single vertical spread
        if len(short_legs) > 0 and len(long_legs) > 0:
            spread_width = abs(short_legs[0]["strike"] - long_legs[0]["strike"])
        else:
            spread_width = 0

        max_profit = entry_credit
        max_loss = (spread_width * qty * 100) - entry_credit

        short_strike = short_legs[0]["strike"] if short_legs else 0

        if strategy == "CCS":
            breakevens = [short_strike + credit_per_share]
        else:  # PCS
            breakevens = [short_strike - credit_per_share]

    return_on_risk = (max_profit / max_loss * 100) if max_loss > 0 else 0.0

    pop = _estimate_pop(legs, strategy, spot, r, iv, T)

    return {
        "entry_credit": round(entry_credit, 2),
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "breakevens": [round(b, 2) for b in breakevens],
        "return_on_risk": round(return_on_risk, 2),
        "pop": round(pop, 2),
    }


def calc_summary_generic(legs, spot, r=0.045, iv=0.20, T=None):
    """Numeric summary for ANY leg set (incl. butterfly/condor/calendar).

    Evaluates net position P&L across a dense price grid at the FRONT-leg
    expiry (calendars price the back leg via BS at its remaining T) and reads
    max-profit / max-loss / breakevens off that curve. PoP = risk-neutral
    lognormal probability mass over the profitable price region.
    """
    import math
    if T is None or T <= 0:
        T = 1.0 / 365.0

    # Entry net credit (short premium - long premium), scaled.
    entry_credit = 0.0
    for leg in legs:
        q = leg.get("qty", 1)
        amt = leg["premium"] * q * 100
        entry_credit += amt if leg["side"] == "short" else -amt

    # Front (nearest) expiry sets the horizon; each leg keeps its own remaining T.
    leg_t0, front_t0 = [], None
    for leg in legs:
        t = _leg_expiry_years(leg)
        leg_t0.append(t)
        if t is not None:
            front_t0 = t if front_t0 is None else min(front_t0, t)

    def _leg_T(i):
        t = leg_t0[i]
        if t is None or front_t0 is None:
            return 0.0                 # same-expiry set -> expiration payoff
        return max(t - front_t0, 0.0)

    lo, hi = spot * 0.5, spot * 1.5
    n = 601
    xs = [lo + (hi - lo) * k / (n - 1) for k in range(n)]
    pnl = []
    for S in xs:
        val = 0.0
        for i, leg in enumerate(legs):
            q = leg.get("qty", 1)
            price = bs_price(S, leg["strike"], _leg_T(i), r, max(iv, 0.01),
                             leg["option_type"].lower())
            val += price * q * 100 * (1 if leg["side"] == "long" else -1)
        pnl.append(entry_credit + val)

    max_profit = max(pnl)
    max_loss_signed = min(pnl)
    max_loss = abs(max_loss_signed) if max_loss_signed < 0 else 0.0

    # Breakevens: linear-interpolated zero-crossings of the P&L curve.
    bes = []
    for k in range(1, n):
        a, b = pnl[k - 1], pnl[k]
        if (a <= 0 < b) or (a >= 0 > b):
            x0, x1 = xs[k - 1], xs[k]
            bes.append(round(x0 + (x1 - x0) * (0 - a) / (b - a), 2))

    ror = (max_profit / max_loss * 100) if max_loss > 0 else 0.0

    # PoP: risk-neutral lognormal mass over the profitable region (P&L > 0).
    mu = math.log(spot) + (r - 0.5 * iv * iv) * T
    sd = iv * math.sqrt(T)

    def _cdf_S(x):
        return norm_cdf((math.log(x) - mu) / sd) if x > 0 and sd > 0 else 0.0

    pop = 0.0
    prev_prof = pnl[0] > 0
    seg_start = xs[0]
    for k in range(1, n):
        prof = pnl[k] > 0
        if prof != prev_prof:
            if prev_prof:
                pop += _cdf_S(xs[k]) - _cdf_S(seg_start)
            seg_start = xs[k]
            prev_prof = prof
    if prev_prof:
        pop += _cdf_S(xs[-1]) - _cdf_S(seg_start)

    return {
        "entry_credit": round(entry_credit, 2),
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "breakevens": sorted(bes),
        "return_on_risk": round(ror, 2),
        "pop": round(max(0.0, min(100.0, pop * 100.0)), 2),
    }


#############################################
# PROBABILITY OF PROFIT
#############################################

def _estimate_pop(legs, strategy, spot, r, iv, T=1/365):
    """
    Estimate probability of profit using Black-Scholes CDF.

    Uses the BS d2 formula with the breakeven price as the "strike"
    to estimate the probability that S finishes profitable.

    Parameters:
        legs     - List of leg dicts
        strategy - 'CCS', 'PCS', or 'IC'
        spot     - Current underlying price
        r        - Risk-free rate
        iv       - Implied volatility
        T        - Time to expiration in years (defaults to 1/365 if None)

    Returns probability as a percentage (0-100).
    """
    if T is None:
        T = 1.0 / 365.0
    if T <= 0:
        T = 1.0 / 365.0

    strategy = strategy.upper()

    # Calculate breakevens (inline to avoid circular dependency)
    short_prem = sum(l["premium"] for l in legs if l["side"] == "short")
    long_prem = sum(l["premium"] for l in legs if l["side"] == "long")
    credit_per_share = short_prem - long_prem

    short_legs = [l for l in legs if l["side"] == "short"]

    sqrt_T = math.sqrt(T)

    def _calc_d2(breakeven):
        """d2 for the BS formula using breakeven as the strike."""
        d1 = (math.log(spot / breakeven) + (r + 0.5 * iv * iv) * T) / (iv * sqrt_T)
        return d1 - iv * sqrt_T

    if strategy in ("LONG_PUT", "LONG_CALL", "NAKED_PUT", "NAKED_CALL"):
        leg = legs[0]
        strike = leg["strike"]
        prem = leg["premium"]
        if strategy == "LONG_PUT":
            be = strike - prem
            d2 = _calc_d2(be)
            return norm_cdf(-d2) * 100.0
        if strategy == "LONG_CALL":
            be = strike + prem
            d2 = _calc_d2(be)
            return norm_cdf(d2) * 100.0
        if strategy == "NAKED_PUT":
            be = strike - prem
            d2 = _calc_d2(be)
            return norm_cdf(d2) * 100.0
        # NAKED_CALL
        be = strike + prem
        d2 = _calc_d2(be)
        return norm_cdf(-d2) * 100.0

    if strategy == "CCS":
        short_strike = short_legs[0]["strike"]
        be = short_strike + credit_per_share
        d2 = _calc_d2(be)
        # Profit when S < breakeven -> P(S < be) = norm_cdf(-d2)
        return norm_cdf(-d2) * 100.0

    elif strategy == "PCS":
        short_strike = short_legs[0]["strike"]
        be = short_strike - credit_per_share
        d2 = _calc_d2(be)
        # Profit when S > breakeven -> P(S > be) = norm_cdf(d2)
        return norm_cdf(d2) * 100.0

    elif strategy == "IC":
        short_puts = [l for l in short_legs if l["option_type"].lower() == "put"]
        short_calls = [l for l in short_legs if l["option_type"].lower() == "call"]

        be_lower = short_puts[0]["strike"] - credit_per_share
        be_upper = short_calls[0]["strike"] + credit_per_share

        d2_lower = _calc_d2(be_lower)
        d2_upper = _calc_d2(be_upper)

        # Under risk-neutral measure with d2 = [ln(S/K) + (r - 0.5*sig^2)*T] / (sig*sqrt(T)):
        #   P(S_T > K) = N(d2),  P(S_T < K) = N(-d2)
        # Profit when be_lower < S_T < be_upper:
        #   P(S < be_lower) = N(-d2_lower)
        #   P(S > be_upper) = N(d2_upper)
        #   PoP = 1 - P(S < be_lower) - P(S > be_upper)
        p_below = norm_cdf(-d2_lower)
        p_above = norm_cdf(d2_upper)
        pop_pct = (1.0 - p_below - p_above) * 100.0
        return max(pop_pct, 0.0)

    return 0.0


#############################################
# P&L PROJECTION
#############################################

def generate_eval_dates(start_date, expiry_date, max_columns=7):
    """
    Generate evenly-spaced evaluation dates between start and expiry.
    Expiry is always the last date.

    Parameters:
        start_date  - date or datetime for first eval date
        expiry_date - date or datetime for expiration
        max_columns - Maximum number of date columns (default 7)

    Returns list of date objects.
    """
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(expiry_date, datetime):
        expiry_date = expiry_date.date()

    total_days = (expiry_date - start_date).days
    if total_days <= 0:
        return [expiry_date]

    if max_columns <= 1:
        return [expiry_date]

    # Number of intervals = max_columns - 1
    n_intervals = max_columns - 1
    step = total_days / n_intervals

    dates = []
    for i in range(max_columns - 1):
        d = start_date + timedelta(days=round(i * step))
        if d not in dates:
            dates.append(d)

    # Ensure expiry is always last and not duplicated
    if dates and dates[-1] == expiry_date:
        pass
    else:
        dates.append(expiry_date)

    return dates


def generate_price_range(spot, pct=0.05):
    """
    Generate a price range around spot.

    Parameters:
        spot - Current underlying price
        pct  - Percentage above/below spot (default 5%)

    Returns tuple (low, high) rounded to 2 decimals.
    """
    low = round(spot * (1.0 - pct), 2)
    high = round(spot * (1.0 + pct), 2)
    return (low, high)


def _leg_expiry_years(leg, expiry_date=None):
    """Leg's CURRENT (now) time-to-expiry in years from its own ``expiry``
    (4pm-close convention, /365, never negative). None if the leg has no
    ``expiry`` (caller then falls back to the column T)."""
    import datetime as _dt
    e = leg.get("expiry")
    if not e:
        return None
    try:
        d = _dt.date.fromisoformat(str(e))
    except (TypeError, ValueError):
        return None
    close = _dt.datetime(d.year, d.month, d.day, 16)
    now = _dt.datetime.now()
    return max((close - now).total_seconds(), 0.0) / (365 * 86400)


def calc_spread_pnl(legs, spot, iv, r, eval_dates, price_range,
                    expiry_date, iv_adjustment=0.0, rows_per_side=30,
                    eval_times=None, per_leg_expiry=False):
    """
    Calculate P&L grid for a spread across prices and dates.

    Parameters:
        legs          - List of leg dicts with: strike, option_type,
                        side ('long'/'short'), premium, qty
        spot          - Current underlying price
        iv            - Base implied volatility
        r             - Risk-free rate
        eval_dates    - List of date objects for evaluation
        price_range   - Tuple (low, high) for price axis
        expiry_date   - Expiration date (date object)
        iv_adjustment - Additive IV shift (default 0.0)
        rows_per_side - Max rows above/below spot (default 30)
        eval_times    - Optional list of explicit time-to-expiry values (years),
                        one per column. When given it OVERRIDES the calendar-day
                        ``(expiry - eval_date).days`` math entirely and defines the
                        number of columns — this is how the caller injects an
                        intraday "Now" column (fractional day) and an expiration
                        column (T=0) for 0DTE. ``eval_dates`` is ignored for timing
                        when ``eval_times`` is supplied.

    Returns list of dicts: {price: float, pnl: [floats], pnl_pct: [floats]}
        Each pnl/pnl_pct list has one value per column.
    """
    if isinstance(expiry_date, datetime):
        expiry_date = expiry_date.date()

    adjusted_iv = max(iv + iv_adjustment, 0.01)

    # Total premium received at entry (per-share basis, then scaled)
    total_premium_received = 0.0
    for leg in legs:
        qty = leg.get("qty", 1)
        if leg["side"] == "short":
            total_premium_received += leg["premium"] * qty * 100
        else:
            total_premium_received -= leg["premium"] * qty * 100

    # Generate price steps with size scaled to underlying price:
    #   spot > 5000        -> $5.00
    #   1000 <= spot <= 5000 -> $2.50
    #   spot < 1000        -> $1.00
    # Capped at rows_per_side above and below spot.
    import math
    if spot > 5000:
        step = 5.00
    elif spot < 1000:
        step = 1.00
    else:
        step = 2.50

    inv = 1.0 / step  # used to snap to the step grid
    low_limit = math.floor((spot - rows_per_side * step) * inv) / inv
    high_limit = math.ceil((spot + rows_per_side * step) * inv) / inv
    # Also respect the user-supplied price_range boundaries
    low = max(price_range[0], low_limit)
    high = min(price_range[1], high_limit)
    low_r = math.floor(low * inv) / inv
    high_r = math.ceil(high * inv) / inv
    if high_r <= low_r:
        price_steps = [low_r]
    else:
        price_steps = []
        p = low_r
        while p <= high_r + 0.01:
            price_steps.append(round(p, 2))
            p += step

    # Per-column time-to-expiry (years). Explicit ``eval_times`` (intraday "Now"
    # + expiration) win; otherwise fall back to the legacy calendar-day math.
    if eval_times is not None:
        col_times = [max(float(t), 0.0) for t in eval_times]
    else:
        col_times = []
        for eval_date in eval_dates:
            eval_date_d = eval_date.date() if isinstance(eval_date, datetime) else eval_date
            col_times.append(max((expiry_date - eval_date_d).days / 365.0, 0.0))

    t0 = col_times[0] if col_times else 0.0
    leg_t0 = [(_leg_expiry_years(l) if per_leg_expiry else None) for l in legs]

    results = []

    for price in price_steps:
        pnl_list = []
        pnl_pct_list = []

        for T in col_times:
            # Calculate current position value
            position_value = 0.0
            for li, leg in enumerate(legs):
                qty = leg.get("qty", 1)
                opt_type = leg["option_type"].lower()
                strike = leg["strike"]

                base = leg_t0[li]
                t_leg = T if base is None else max(base - (t0 - T), 0.0)
                current_price = bs_price(price, strike, t_leg, r, adjusted_iv, opt_type)

                if leg["side"] == "long":
                    # Long position: we own it, positive value
                    position_value += current_price * qty * 100
                else:
                    # Short position: we owe it, negative value
                    position_value -= current_price * qty * 100

            # P&L = premium received + current position value
            # (position_value is negative for short legs we'd buy back)
            pnl = total_premium_received + position_value
            pnl = round(pnl, 2)

            # P&L percentage based on premium received
            if abs(total_premium_received) > 0:
                pnl_pct = round(pnl / abs(total_premium_received) * 100, 2)
            else:
                pnl_pct = 0.0

            pnl_list.append(pnl)
            pnl_pct_list.append(pnl_pct)

        results.append({
            "price": price,
            "pnl": pnl_list,
            "pnl_pct": pnl_pct_list,
        })

    return results
