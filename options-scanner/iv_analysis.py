"""
iv_analysis.py - Vol Regime, Expected Move, and ATM IV Calculations
Version: 1.0.1
Last Updated: 2026-04-19

Computes implied volatility analytics from Schwab option chain and price
history data. Used by the scanner engine and displayed in the dashboard.

IMPORTANT — naming note:
    The fields `iv_rank` / `iv_percentile` in this module are **proxies**
    that place current ATM IV within the 52-week HISTORICAL-volatility
    (HV-30) distribution, not a pure IV-vs-IV-history rank. A true IV
    rank requires a persisted daily IV time series, which this project
    does not yet capture. Treat these as "volatility regime" indicators:
    - High value: current IV is rich relative to recent realized vol
    - Low value:  current IV is cheap relative to recent realized vol
    The same numbers are also returned under `hv_rank` / `hv_percentile`
    keys, which are the honest labels.

Definitions:
    Current IV:     ATM 30-day option implied volatility from Schwab chain
    Vol Rank:       Where current IV sits between 52w-HV low and 52w-HV high
    Vol Percentile: % of the past 252 HV-30 days below current IV
    Expected Move:  Price * IV * sqrt(DTE / 365) — the +/- 1 standard deviation move

Version 1.0.1 Changes:
- Relabel IV Rank / IV Percentile to Vol Rank / Vol Percentile, since the
  underlying comparison is current-IV vs HV-distribution, not IV-vs-IV.
- Added `hv_rank` / `hv_percentile` alias keys in the return dict for
  future code that wants the unambiguous name. Legacy `iv_*` keys retained.
"""

import math
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("scanner")
TZ = ZoneInfo("America/Chicago")

#############################################
# ATM IV FROM OPTION CHAIN
#############################################

def extract_atm_iv(chain_data):
    """Extract the at-the-money implied volatility from a Schwab option chain.

    Uses the nearest monthly expiration (20-45 DTE preferred) and picks the
    strike closest to the underlying price. Averages the put and call IV at
    that strike for a balanced ATM reading.
    """
    if not chain_data:
        return None

    underlying = chain_data.get("underlyingPrice", 0)
    if underlying == 0:
        return None

    call_map = chain_data.get("callExpDateMap", {})
    put_map = chain_data.get("putExpDateMap", {})

    # Find best expiration (closest to 30 DTE)
    best_exp = None
    best_dte_dist = 999
    for exp_key in call_map.keys():
        parts = exp_key.split(":")
        if len(parts) < 2:
            continue
        dte = int(float(parts[1]))
        dist = abs(dte - 30)
        if 7 <= dte <= 60 and dist < best_dte_dist:
            best_dte_dist = dist
            best_exp = exp_key

    if best_exp is None:
        # Fallback: use any expiration
        for exp_key in call_map.keys():
            parts = exp_key.split(":")
            dte = int(float(parts[1]))
            if dte > 0 and abs(dte - 30) < best_dte_dist:
                best_dte_dist = abs(dte - 30)
                best_exp = exp_key

    if best_exp is None:
        return None

    # Find ATM strike
    call_strikes = call_map.get(best_exp, {})
    put_strikes = put_map.get(best_exp, {})

    best_strike = None
    best_dist = float("inf")
    for sk in call_strikes.keys():
        k = float(sk)
        d = abs(k - underlying)
        if d < best_dist:
            best_dist = d
            best_strike = sk

    if best_strike is None:
        return None

    # Get IV from both call and put at ATM strike
    ivs = []
    if best_strike in call_strikes:
        contracts = call_strikes[best_strike]
        if contracts:
            iv = contracts[0].get("volatility")
            if iv and iv > 0:
                ivs.append(iv)

    if best_strike in put_strikes:
        contracts = put_strikes[best_strike]
        if contracts:
            iv = contracts[0].get("volatility")
            if iv and iv > 0:
                ivs.append(iv)

    if not ivs:
        return None

    atm_iv = sum(ivs) / len(ivs)
    return round(atm_iv, 2)

#############################################
# HISTORICAL VOLATILITY (ROLLING HV-30)
#############################################

def calc_historical_vol_series(candles, window=30):
    """Compute a rolling annualized historical volatility series.

    Uses close-to-close log returns with a 30-day lookback window.
    Returns a list of (date_ms, hv_annualized) tuples.
    """
    if not candles or len(candles) < window + 1:
        return []

    # Compute daily log returns
    log_returns = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1]["close"]
        curr_close = candles[i]["close"]
        if prev_close > 0 and curr_close > 0:
            lr = math.log(curr_close / prev_close)
            log_returns.append((candles[i].get("datetime", 0), lr))
        else:
            log_returns.append((candles[i].get("datetime", 0), 0))

    # Rolling window standard deviation, annualized
    hv_series = []
    for i in range(window - 1, len(log_returns)):
        window_returns = [lr for _, lr in log_returns[i - window + 1:i + 1]]
        n = len(window_returns)
        if n < 2:
            continue
        mean = sum(window_returns) / n
        variance = sum((r - mean) ** 2 for r in window_returns) / (n - 1)
        std = math.sqrt(variance)
        hv_ann = std * math.sqrt(252) * 100  # annualized, as percentage
        dt = log_returns[i][0]
        hv_series.append((dt, round(hv_ann, 2)))

    return hv_series

#############################################
# IV RANK & PERCENTILE
#############################################

def calc_iv_rank_percentile(current_iv, hv_series, lookback_days=252):
    """Rank current ATM IV against the 52-week HV-30 distribution.

    This is a "Vol Rank" / "Vol Percentile" proxy — NOT a pure IV rank.
    A true IV rank requires a persisted daily IV time series (not yet
    available); until then, we compare current IV against the 52-week
    distribution of realized volatility (HV-30) as a regime indicator.

    Vol Rank:       (current_iv - 52w_HV_low) / (52w_HV_high - 52w_HV_low) * 100
                    0 = IV at or below 52w HV low, 100 = IV at or above 52w HV high.
    Vol Percentile: % of the past `lookback_days` HV-30 days below current IV.
                    High value = current IV is rich vs recent realized vol.

    Args:
        current_iv: Current ATM IV as a percentage (e.g., 25.5 for 25.5%)
        hv_series: List of (timestamp, hv_value) from calc_historical_vol_series
        lookback_days: Number of trading days to look back (default 252 = 1 year)

    Returns:
        dict with both legacy (`iv_*`) and honest (`hv_*`) aliases for the
        same underlying numbers:
            iv_rank, iv_percentile, iv_high_52w, iv_low_52w  (legacy)
            hv_rank, hv_percentile, hv_high_52w, hv_low_52w  (honest)
    """
    none_result = {
        "iv_rank": None, "iv_percentile": None,
        "iv_high_52w": None, "iv_low_52w": None,
        "hv_rank": None, "hv_percentile": None,
        "hv_high_52w": None, "hv_low_52w": None,
    }
    if not hv_series or current_iv is None or current_iv <= 0:
        return dict(none_result)

    # Use the most recent lookback_days entries
    recent = hv_series[-lookback_days:] if len(hv_series) > lookback_days else hv_series
    hv_values = [v for _, v in recent]

    if not hv_values:
        return dict(none_result)

    high_52w = max(hv_values)
    low_52w = min(hv_values)

    if high_52w == low_52w:
        vol_rank = 50.0  # No range, default to middle
    else:
        vol_rank = (current_iv - low_52w) / (high_52w - low_52w) * 100
        vol_rank = max(0, min(100, vol_rank))

    days_below = sum(1 for v in hv_values if v < current_iv)
    vol_percentile = days_below / len(hv_values) * 100

    rank_r = round(vol_rank, 1)
    pctl_r = round(vol_percentile, 1)
    hi_r = round(high_52w, 1)
    lo_r = round(low_52w, 1)
    return {
        # Legacy names — retained for back-compat. Misleading; prefer hv_*.
        "iv_rank": rank_r,
        "iv_percentile": pctl_r,
        "iv_high_52w": hi_r,
        "iv_low_52w": lo_r,
        # Honest names — current IV ranked within HV-30 distribution.
        "hv_rank": rank_r,
        "hv_percentile": pctl_r,
        "hv_high_52w": hi_r,
        "hv_low_52w": lo_r,
    }

#############################################
# EXPECTED MOVE
#############################################

def calc_expected_move(price, iv_pct, dte):
    """Calculate the expected move (1 standard deviation) for a given DTE.

    Expected Move = Price * (IV / 100) * sqrt(DTE / 365)

    Args:
        price: Current underlying price
        iv_pct: Annualized implied volatility as percentage (e.g., 25.5)
        dte: Days to expiration

    Returns:
        dict with move_dollars, move_pct, upper_1sd, lower_1sd,
        upper_2sd, lower_2sd
    """
    if not price or not iv_pct or iv_pct <= 0 or dte < 0:
        return None

    iv = iv_pct / 100.0
    em_1sd = price * iv * math.sqrt(max(dte, 0.25) / 365.0)
    em_2sd = em_1sd * 2.0
    em_pct = em_1sd / price * 100.0

    return {
        "dte": dte,
        "move_dollars": round(em_1sd, 2),
        "move_pct": round(em_pct, 2),
        "upper_1sd": round(price + em_1sd, 2),
        "lower_1sd": round(price - em_1sd, 2),
        "upper_2sd": round(price + em_2sd, 2),
        "lower_2sd": round(price - em_2sd, 2),
    }


def expiry_atm_iv(chain_data, exp_str, underlying):
    """ATM implied vol for ONE specific expiration, as a percentage.

    ``extract_atm_iv`` deliberately reads the ~30-DTE expiration to get a stable
    symbol-level IV. That is the right input for IV rank / IV-HV, but it is the
    WRONG input for an expected move at any other horizon: scaling a 30-day IV
    by sqrt(dte) assumes a flat term structure. Measured live on 2026-08-07,
    SPY's 3-DTE expiry priced 8.1 vol against IV30 10.3 — the sqrt-scaled EM
    overstated that expiration's own move by 27%. Event days invert it.

    Averages the call and put IV at the strike nearest ``underlying`` (the same
    balanced ATM reading ``extract_atm_iv`` takes). Returns None when the
    expiration is absent or carries no usable IV, so callers fall back rather
    than silently scoring off a fabricated number.
    """
    if not chain_data or not underlying or underlying <= 0 or not exp_str:
        return None

    ivs = []
    for map_key in ("callExpDateMap", "putExpDateMap"):
        exp_map = (chain_data.get(map_key) or {})
        strikes = None
        for key, val in exp_map.items():
            if key.split(":")[0] == exp_str:
                strikes = val
                break
        if not strikes:
            continue
        try:
            best = min(strikes.keys(), key=lambda sk: abs(float(sk) - underlying))
        except (TypeError, ValueError):
            continue
        contracts = strikes.get(best) or []
        if not contracts:
            continue
        iv = contracts[0].get("volatility")
        if iv and iv > 0:
            ivs.append(iv)

    if not ivs:
        return None
    return round(sum(ivs) / len(ivs), 2)


def expiry_daily_em(price, iv_pct):
    """One-DAY expected move computed at a specific expiration's own IV.

    Deliberately returns a DAILY move, not that expiration's period move: every
    consumer downstream (the sqrt(dte) period scaling, the 0-DTE hours-to-close
    decay, all the EM-multiplier curves) already scales a daily EM. Substituting
    this for the IV30-derived daily EM therefore changes ONLY the vol input and
    leaves every one of those formulas untouched.
    """
    em = calc_expected_move(price, iv_pct, 1)
    return em["move_dollars"] if em else None


def calc_expected_moves(price, iv_pct):
    """Calculate expected moves for standard timeframes.

    Returns dict with 0-DTE (1 day), 1-week, and 30-day expected moves.
    """
    if not price or not iv_pct or iv_pct <= 0:
        return {"daily": None, "weekly": None, "monthly": None}

    return {
        "daily": calc_expected_move(price, iv_pct, 1),
        "weekly": calc_expected_move(price, iv_pct, 7),
        "monthly": calc_expected_move(price, iv_pct, 30),
    }

#############################################
# FULL IV ANALYSIS FOR ONE SYMBOL
#############################################

def run_iv_analysis(client, symbol, price=None, hist=None, chain=None):
    """Run complete IV analysis for a symbol.

    Fetches option chain and 1-year price history, computes:
    - Current ATM IV
    - Vol Rank (current IV vs 52-week HV-30 range) — exposed as iv_rank & hv_rank
    - Vol Percentile (current IV within 52-week HV-30 distribution)
    - Expected moves (daily, weekly, monthly)

    Args:
        client: Schwab client
        symbol: ticker
        price: current underlying (optional; pulled from chain if None)
        hist: pre-fetched price history from fetch_price_history (optional).
              If provided, the internal fetch is skipped — useful when the
              caller has already fetched it concurrently with other work.
        chain: pre-fetched option chain covering ~20-45 DTE (optional).
              If provided and non-empty, the internal chain fetch is skipped.
              The caller can reuse this chain for other purposes (e.g. the
              swing scanner) to collapse two Schwab API calls into one.

    Returns a dict with all metrics.
    """
    from scanner_engine import fetch_option_chain, fetch_price_history

    result = {
        "symbol": symbol,
        "current_iv": None,
        "iv_rank": None,
        "iv_percentile": None,
        "iv_high_52w": None,
        "iv_low_52w": None,
        "expected_moves": {"daily": None, "weekly": None, "monthly": None},
        "hv_current": None,
    }

    # Get option chain for ATM IV (target ~30 DTE). Caller may pre-fetch.
    today = datetime.now(TZ).date()
    if chain is None or not chain.get("callExpDateMap"):
        from_d = today + timedelta(days=20)
        to_d = today + timedelta(days=45)
        chain = fetch_option_chain(client, symbol, from_date=from_d, to_date=to_d)

    # Fallback: broader range if narrow range returned nothing
    if not chain or not chain.get("callExpDateMap"):
        chain = fetch_option_chain(client, symbol, from_date=today, to_date=today + timedelta(days=60))

    atm_iv = extract_atm_iv(chain)
    result["current_iv"] = atm_iv

    if price is None and chain:
        price = chain.get("underlyingPrice", 0)

    # Price history for historical vol. Caller may pre-fetch to parallelize.
    if hist is None:
        hist = fetch_price_history(client, symbol)
    if hist and "candles" in hist:
        hv_series = calc_historical_vol_series(hist["candles"], window=30)
        if atm_iv and hv_series:
            rank_data = calc_iv_rank_percentile(atm_iv, hv_series)
            result.update(rank_data)

            # Expose current HV for IV/HV ratio calculation
            if hv_series:
                result["hv_current"] = hv_series[-1][1]

    # Expected moves
    if atm_iv and price and price > 0:
        result["expected_moves"] = calc_expected_moves(price, atm_iv)

    return result
