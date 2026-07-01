"""
scanner_engine.py - Core scanning logic for Options Scanner
Version: 1.3.0
Last Updated: 2026-05-06

Reusable scanning engine called by both CLI scanner and GUI dashboard.

Version 1.3.0 Changes:
- Sentiment/trend regime filter (regime_filter.py) gates CCS/PCS by
  SentimentDashboard composite score + per-symbol technical trend.
  Drops the structurally-doomed side before scoring (e.g. CCS in bull
  regime), based on EOD post-mortem of CCS bleed (2026-04/05).

Version 1.2.0 Changes:
- Integrated composite scoring model (scoring.py)
- Signals now include composite_score, grade, and factor_scores
- Sorted by composite score (primary) then R:R (secondary)

Version 1.1.0 Changes:
- Integrated IV Rank, IV Percentile, and Expected Move via iv_analysis module

Version 1.0.0 Changes:
- Extracted from scanner.py for reuse
"""

import math
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

# Cap per-scan concurrency to stay well below Schwab API rate limits while
# still collapsing per-symbol round-trips from O(symbols) to ~O(1).
_SCAN_MAX_WORKERS = 4

log = logging.getLogger("scanner")

from watchlist import get_scan_symbols, get_load_error
import fill_model

TZ = ZoneInfo("America/Chicago")

HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3), date(2026, 5, 25), date(2026, 7, 3),
    date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
}

#############################################
# MARKET HOURS
#############################################

def is_trading_day(dt=None):
    if dt is None:
        dt = datetime.now(TZ)
    d = dt.date() if hasattr(dt, "date") else dt
    return d.weekday() < 5 and d not in HOLIDAYS_2026

def is_market_hours(dt=None, start_h=8, start_m=0, end_h=15, end_m=15):
    if dt is None:
        dt = datetime.now(TZ)
    if not is_trading_day(dt):
        return False
    t = dt.time()
    from datetime import time as dtime
    return dtime(start_h, start_m) <= t <= dtime(end_h, end_m)

#############################################
# DATA FETCHING
#############################################

def fetch_quotes(client, symbols):
    try:
        r = client.get_quotes(symbols)
        return r.json() if r.status_code == 200 else {}
    except Exception as e:
        log.error(f"Quote fetch: {e}")
        return {}

def fetch_option_chain(client, symbol, from_date=None, to_date=None):
    try:
        kwargs = {"contract_type": client.Options.ContractType.ALL}
        if from_date:
            kwargs["from_date"] = from_date
        if to_date:
            kwargs["to_date"] = to_date
        r = client.get_option_chain(symbol, **kwargs)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        log.error(f"Chain fetch {symbol}: {e}")
        return None

def fetch_price_history(client, symbol):
    try:
        r = client.get_price_history_every_day(symbol)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        log.error(f"History fetch {symbol}: {e}")
        return None

#############################################
# TECHNICALS
#############################################

def calc_technicals(hist):
    if not hist or "candles" not in hist or len(hist["candles"]) < 50:
        return None
    closes = [c["close"] for c in hist["candles"]]
    def sma(d, p):
        return sum(d[-p:]) / p if len(d) >= p else None
    cur = closes[-1]
    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    candles = hist["candles"]
    hi20 = max(c["high"] for c in candles[-20:])
    lo20 = min(c["low"] for c in candles[-20:])
    trs = []
    for i in range(1, min(15, len(candles))):
        c, p = candles[-i], candles[-i-1]
        trs.append(max(c["high"]-c["low"], abs(c["high"]-p["close"]), abs(c["low"]-p["close"])))
    atr = sum(trs)/len(trs) if trs else 0
    trend = "NEUTRAL"
    if s20 and s50:
        if cur > s20 > s50: trend = "BULLISH"
        elif cur < s20 < s50: trend = "BEARISH"
        elif cur > s20 and s20 < s50: trend = "RECOVERING"
        elif cur < s20 and s20 > s50: trend = "WEAKENING"
    # RSI(14)
    rsi14 = None
    if len(closes) >= 15:
        gains, losses = [], []
        for i in range(1, len(closes)):
            change = closes[i] - closes[i-1]
            gains.append(max(change, 0))
            losses.append(max(-change, 0))
        period = 14
        if len(gains) >= period:
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            # Wilder's smoothing over remaining data
            for i in range(len(gains) - period, len(gains)):
                avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi14 = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi14 = round(100 - (100 / (1 + rs)), 1)

    return {"price": round(cur,2), "sma20": round(s20,2) if s20 else None,
            "sma50": round(s50,2) if s50 else None, "sma200": round(s200,2) if s200 else None,
            "high20": round(hi20,2), "low20": round(lo20,2), "atr14": round(atr,2), "trend": trend,
            "rsi14": rsi14}

def vix_regime(vix):
    if vix < 15: return {"regime": "LOW", "label": "Low vol", "color": "#1D9E75"}
    elif vix < 20: return {"regime": "NORMAL", "label": "Normal", "color": "#378ADD"}
    elif vix < 30: return {"regime": "ELEVATED", "label": "Elevated", "color": "#EF9F27"}
    else: return {"regime": "HIGH", "label": "High vol", "color": "#E24B4A"}

def calc_vix_term_structure(vix, vix9d, vix3m):
    """Determine VIX term structure from spot and term VIX values."""
    if not vix or not vix9d or not vix3m:
        return {"structure": "UNKNOWN", "vix9d_ratio": None, "vix3m_ratio": None}

    vix9d_ratio = round(vix9d / vix, 3) if vix > 0 else None
    vix3m_ratio = round(vix3m / vix, 3) if vix > 0 else None

    if vix9d < vix < vix3m:
        structure = "CONTANGO"
    elif vix9d > vix:
        structure = "BACKWARDATION"
    else:
        structure = "MIXED"

    return {"structure": structure, "vix9d_ratio": vix9d_ratio, "vix3m_ratio": vix3m_ratio}

# --- Expected Move windows (Option-A discipline: tighten outward at short DTE) ---
# 0-DTE uses an intraday-decayed window: shorts must sit between
#   1.5x and 3.0x of the REMAINING session EM (daily_EM * sqrt(hours_left/6.5)).
# Swing uses a DTE-aware multiplier curve (linear interp between anchors)
#   applied to period_EM = daily_EM * sqrt(DTE).
ZERO_DTE_MIN_MULT = 0.618
ZERO_DTE_MAX_MULT = 3.00

# CT session boundaries for 0-DTE intraday decay.
from datetime import time as _dt_time
SESSION_OPEN  = _dt_time(8, 30)
SESSION_CLOSE = _dt_time(15, 0)
SESSION_HOURS = 6.5

# 0-DTE bucket EM-window curve for DTE 1-4 (same-day handled separately by
# zero_dte_em_window). Min mult held flat at 0.618; max anchors preserved
# from the prior shared curve so the outer bound is unchanged.
ZERO_DTE_BUCKET_EM_CURVE = [
    (1, 0.618, 2.50),
    (3, 0.618, 2.20),
    (4, 0.618, 2.10),
]

# Swing EM-window curve (DTE 5-15): (DTE, min_mult, max_mult). Linear interp between rows.
SWING_EM_CURVE = [
    (1,  1.50, 2.50),
    (3,  1.25, 2.20),
    (5,  1.00, 2.00),
    (7,  0.85, 1.85),
    (10, 0.70, 1.70),
    (15, 0.50, 1.50),
]

DELTA_SANITY_MAX = 0.40  # Reject |delta| > this as a data-glitch rail.
                         # EM windows are the primary selector now.

# Hard ceiling on the SHORT-leg delta at entry. Sits safely below the
# entry-relative DELTA_STOP hard ceiling (signal_recommender) so a position can
# never be born past its own exit. The EM window is the primary strike selector;
# this is the delta-domain rail that DELTA_SANITY_MAX (0.40, a glitch rail) is
# too loose to provide.
# 2026-06-11 quality retune: tightened 0.30 -> 0.27 to favor further-OTM shorts
# (higher PoP per trade). See docs/plans/2026-06-11-quality-first-selection-design.md.
MAX_ENTRY_SHORT_DELTA = 0.27

# Intraday directional veto: suppress the offside vertical once a symbol has
# moved more than this fraction of its daily expected move. Stops selling calls
# into a rising tape / puts into a falling one. See the 2026-06-11 design doc.
MOMENTUM_VETO = 0.6

# Index symbols whose dealer-gamma signal is reliable enough to gate on.
INDEX_SYMBOLS = frozenset({"$SPX", "SPY", "QQQ"})
# Net-GEX regime: ratio = net/gross in [-1,1]. <= this => strongly-negative
# (trend-prone) -> skip index premium. See 2026-06-12 design doc.
GEX_STRONG_NEG = -0.30
# Elevated score floor required for index premium spreads in a mildly-negative
# GEX regime (above the 58 capture floor).
NEG_GEX_MIN_SCORE = 62

# Required credit/width margin ABOVE the delta-implied breakeven (|short_delta|).
# 0.00 = enforce the zero-margin breakeven (cr/w >= |delta|, i.e. non-negative
# modeled EV) only. A live differential scan (2026-06-10) showed a positive
# margin starves the book ~90%: efficient option markets price spreads at
# cr/w ~= |delta|, so demanding edge above fair value removes nearly everything;
# the premium edge is the variance-risk-premium realized over many trades (the
# IV factors in scoring), not a per-trade static credit. The gate is retained
# (tunable) and still adds the breakeven floor to the explicit-widths path,
# which has no E[PnL] check. See docs/plans/2026-06-10-signal-quality-edge-gates.
# 2026-06-11 quality retune: raised 0.00 -> 0.02 to require a thin edge above
# fair value (cr/w >= |delta| + 0.02), trading a small volume cut for higher
# per-trade quality. See docs/plans/2026-06-11-quality-first-selection-design.md.
EDGE_MARGIN = 0.02


def _interp_em_curve(curve, dte):
    """Linear-interpolate (min_mult, max_mult) for a DTE against a curve.

    DTE outside the curve's range clamps to the nearest endpoint.
    """
    if dte <= curve[0][0]:
        return (curve[0][1], curve[0][2])
    if dte >= curve[-1][0]:
        return (curve[-1][1], curve[-1][2])
    for i in range(len(curve) - 1):
        d0, mn0, mx0 = curve[i]
        d1, mn1, mx1 = curve[i + 1]
        if d0 <= dte <= d1:
            t = (dte - d0) / (d1 - d0)
            return (mn0 + t * (mn1 - mn0), mx0 + t * (mx1 - mx0))
    return (curve[-1][1], curve[-1][2])


def zero_dte_bucket_em_window(dte):
    """Return (min_mult, max_mult) for a 0-DTE-bucket strike at DTE 1-4."""
    return _interp_em_curve(ZERO_DTE_BUCKET_EM_CURVE, dte)


# Directional pass: short strike sits 0.0x-0.618x of period EM from spot
# (or 0.0x-0.618x of remaining EM at DTE=0). The band stops just where the
# premium band starts, so directional and premium do not overlap.
DIRECTIONAL_MIN_MULT = 0.0
DIRECTIONAL_MAX_MULT = 0.618


def directional_em_window(dte):
    """Return (min_mult, max_mult) for the directional EM band.

    DTE-independent: directional is always (0.0, 0.618) applied to whatever
    period_EM the caller computes.
    """
    return (DIRECTIONAL_MIN_MULT, DIRECTIONAL_MAX_MULT)


def swing_em_window(dte):
    """Return (min_mult, max_mult) for a swing EM window at this DTE.

    Linear interpolation between SWING_EM_CURVE anchors. DTE outside the
    [1, 15] range clamps to the nearest endpoint.
    """
    if dte <= SWING_EM_CURVE[0][0]:
        return (SWING_EM_CURVE[0][1], SWING_EM_CURVE[0][2])
    if dte >= SWING_EM_CURVE[-1][0]:
        return (SWING_EM_CURVE[-1][1], SWING_EM_CURVE[-1][2])

    for i in range(len(SWING_EM_CURVE) - 1):
        d0, mn0, mx0 = SWING_EM_CURVE[i]
        d1, mn1, mx1 = SWING_EM_CURVE[i + 1]
        if d0 <= dte <= d1:
            t = (dte - d0) / (d1 - d0)
            return (mn0 + t * (mn1 - mn0), mx0 + t * (mx1 - mx0))

    # Unreachable, but defensive
    return (SWING_EM_CURVE[-1][1], SWING_EM_CURVE[-1][2])


def _hours_until_close(now_ct):
    """Hours remaining in the session, clamped to [0, SESSION_HOURS].
    Pre-open returns SESSION_HOURS (full day); at/after close returns 0.
    """
    t = now_ct.time()
    if t <= SESSION_OPEN:
        return SESSION_HOURS
    if t >= SESSION_CLOSE:
        return 0.0
    delta_secs = (
        (SESSION_CLOSE.hour - t.hour) * 3600
        + (SESSION_CLOSE.minute - t.minute) * 60
        - t.second
    )
    return max(0.0, delta_secs / 3600.0)


def zero_dte_em_window(now_ct, daily_em):
    """Return (remaining_em, min_mult, max_mult) for a 0-DTE strike check.

    Remaining EM decays with sqrt(hours_left / SESSION_HOURS):
        remaining_em = daily_em * sqrt(hours_left / 6.5)

    Pre-open uses full daily_em; at/after close yields remaining_em = 0
    (caller should reject all candidates).
    """
    if daily_em <= 0:
        return (0.0, ZERO_DTE_MIN_MULT, ZERO_DTE_MAX_MULT)
    hours_left = _hours_until_close(now_ct)
    remaining_em = daily_em * math.sqrt(hours_left / SESSION_HOURS)
    return (remaining_em, ZERO_DTE_MIN_MULT, ZERO_DTE_MAX_MULT)


# --- Liquidity gate thresholds (hard filters, pre-scoring) ---
# Contracts failing any check are rejected before scoring.
# Long legs (protective wings) use relaxed thresholds — they are further OTM
# with naturally wider spreads and lower OI. They just need to be tradeable.
LIQUIDITY_THRESHOLDS = {
    "0-DTE": {
        "min_oi": 100,           # min open interest — short leg
        "min_oi_long": 20,       # min open interest — long leg (protective wing)
        "min_volume": 50,        # min volume on short leg (long leg exempt)
        "max_spread_pct": 0.15,  # max bid-ask spread (% of mid) — short leg only
    },
    "SWING": {
        "min_oi": 50,
        "min_oi_long": 10,
        "min_volume": 10,
        "max_spread_pct": 0.25,
    },
}

# Absolute spread cents below which the percentage gate is bypassed.
# Penny-wide markets on cheap options (e.g. $0.05 mid, $0.01 ask-bid) are
# the tightest possible markets — rejecting them as "wide" because 1¢/5¢
# = 20% is wrong. Far-OTM 0-DTE option chains on SPY/QQQ are entirely
# composed of 5–25¢ marks with 1–2¢ markets; the % gate alone gives zero
# qualifying strikes most days.
MIN_ABS_SPREAD = 0.02

# --- IV Rank hard floor (pre-filter) ---
# Signals below this IV Rank are rejected — selling cheap premium is destructive.
# 2026-06-11 quality retune: raised 0-DTE 25->35, SWING 20->30 to demand richer
# premium before selling. Binds only on low-IV days (most days have IV Rank well
# above these floors). See docs/plans/2026-06-11-quality-first-selection-design.md.
MIN_IV_RANK = {
    "0-DTE": 35,
    "SWING": 30,
}

# --- Adaptive minimum credit % by VIX regime ---
MIN_CREDIT_PCT = {
    "0-DTE": {
        "LOW":      0.08,   # IV is cheap — accept thinner credits, POP is high
        "NORMAL":   0.12,
        "ELEVATED": 0.15,
        "HIGH":     0.20,   # Rich premium — demand more credit
    },
    "SWING": 0.12,          # 1-15 DTE: raised from 0.10 — shorter DTE leaves less recovery time
}

# --- Directional pass parameters ---
DIRECTIONAL_DELTA_RANGE = {
    # short delta band — closer-to-spot than premium ranges
    "PCS": (-0.55, -0.30),
    "CCS": (0.30, 0.55),
}
DIRECTIONAL_MAX_PER_SYMBOL_BUCKET = 2
DIRECTIONAL_MAX_RISK_PCT = 0.02   # 2% account risk per directional trade
DIRECTIONAL_MIN_CREDIT_PCT = 0.20  # higher floor — these strikes pay more


def get_min_credit_pct(regime, trade_type):
    """Return minimum credit-to-width ratio for the VIX regime and trade type."""
    entry = MIN_CREDIT_PCT.get(trade_type)
    if isinstance(entry, dict):
        return entry.get(regime, entry.get("NORMAL", 0.12))
    return entry if entry is not None else 0.10


def calculate_position_size(max_loss_per_contract, account_size=100000,
                            max_risk_pct=0.05, multiplier=100):
    """Calculate max contracts based on account risk limits.

    Args:
        max_loss_per_contract: Max loss per contract in dollars (e.g., 5.0 for a $5-wide spread)
        account_size: Total account value in dollars
        max_risk_pct: Maximum percentage of account to risk per trade (0.05 = 5%)
        multiplier: Options multiplier (100 for standard options)

    Returns:
        int: Maximum number of contracts (0 if max_loss is invalid)
    """
    max_risk = account_size * max_risk_pct
    loss_per = max_loss_per_contract * multiplier
    if loss_per <= 0:
        return 0
    return int(max_risk / loss_per)


def _is_options_market_open():
    """Return True if options market is open (9:30 AM - 4:00 PM ET / 8:30 AM - 3:00 PM CT)."""
    now = datetime.now(TZ)
    t = now.time()
    from datetime import time as dtime
    return is_trading_day(now) and dtime(8, 30) <= t <= dtime(15, 0)


def passes_liquidity_gate(contract, trade_type, is_short_leg=True):
    """Return True if contract meets liquidity thresholds for the trade type.

    Args:
        contract: dict with keys oi, volume, bid, ask, mark
        trade_type: "0-DTE" or "SWING"
        is_short_leg: if True, volume check is applied; if False, only OI and spread
    """
    thresholds = LIQUIDITY_THRESHOLDS.get(trade_type)
    if thresholds is None:
        return True  # unknown trade type — don't filter

    pre_market = not _is_options_market_open()

    # Open interest check — relaxed for long legs (protective wings)
    min_oi = thresholds["min_oi"] if is_short_leg else thresholds.get("min_oi_long", thresholds["min_oi"])
    if (contract.get("oi") or 0) < min_oi:
        return False

    # Volume check (short leg only — long legs exempt)
    # Skip during pre-market: no volume before options market opens
    if not pre_market and is_short_leg and (contract.get("volume") or 0) < thresholds["min_volume"]:
        return False

    # Bid-ask spread check — short leg only.
    # Long legs (protective wings) are exempt: far-OTM options have tiny
    # premiums where even a nickel spread produces huge percentages.
    # The long leg just defines max loss — mark > 0 + OI is sufficient.
    # Skip during pre-market: bid/ask may be stale or zero.
    #
    # Hybrid gate: a market is OK if EITHER it's penny-tight in absolute
    # terms (≤ MIN_ABS_SPREAD) OR its spread/mid ratio is below the
    # threshold. This admits the natural 1¢-wide quotes on cheap far-OTM
    # 0-DTE options while still catching genuinely wide markets.
    if not pre_market and is_short_leg:
        bid = contract.get("bid") or 0
        ask = contract.get("ask") or 0
        mid = (bid + ask) / 2
        if mid <= 0:
            return False
        abs_spread = ask - bid
        if abs_spread > MIN_ABS_SPREAD:
            spread_pct = abs_spread / mid
            if spread_pct > thresholds["max_spread_pct"]:
                return False

    return True


def is_strike_in_expected_move_window(strike, spot, daily_expected_move,
                                       dte, trade_type, now_ct=None,
                                       mode="PREMIUM"):
    """Return True if the SHORT strike sits in the valid EM window.

    Bucket semantics (2026-05-21 redesign):
      "0-DTE" bucket covers DTE 0..4.
      "SWING" bucket covers DTE 5..15.

    EM curve dispatch (mode="PREMIUM", default):
      DTE == 0                       -> intraday-decayed remaining EM (zero_dte_em_window)
      DTE 1-4, trade_type == "0-DTE" -> zero_dte_bucket_em_window
      DTE >= 1, trade_type == "SWING" (or other) -> swing_em_window
      All non-intraday paths apply the mult to period_EM = daily_em * sqrt(DTE).

    The intraday-decay formula only makes sense for same-session expiry where
    hours-remaining is the dominant factor. For DTE >= 1, period_EM scaled by
    sqrt(DTE) is the standard convention and matches the strikes the swing
    scanner has been producing tradeable signals at.

    Daily EM <= 0 disables the filter (accept all strikes).

    `mode` controls which band is applied:
      "PREMIUM"     (default) -> existing 0.618x-3.0x (DTE=0) / curve-driven (DTE>=1)
      "DIRECTIONAL"           -> 0.0x-0.618x of remaining_EM (DTE=0) or
                                 period_EM (DTE>=1). Same band regardless of bucket.
    """
    if daily_expected_move <= 0:
        return True

    distance = abs(strike - spot)

    # Same-day expiry: intraday-decay window
    if dte == 0:
        if now_ct is None:
            now_ct = datetime.now(TZ)
        remaining_em, premium_min, premium_max = zero_dte_em_window(
            now_ct, daily_expected_move
        )
        if remaining_em <= 0:
            return False
        if mode == "DIRECTIONAL":
            min_mult, max_mult = directional_em_window(dte)
        else:
            min_mult, max_mult = premium_min, premium_max
        return (min_mult * remaining_em) <= distance <= (max_mult * remaining_em)

    # DTE >= 1: DTE-aware multi-day window. The 0-DTE bucket (DTE 1-4) uses a
    # dedicated curve with a 0.618 minimum; the SWING bucket uses its own curve.
    period_em = daily_expected_move * math.sqrt(dte)
    if mode == "DIRECTIONAL":
        min_mult, max_mult = directional_em_window(dte)
    elif trade_type == "0-DTE":
        min_mult, max_mult = zero_dte_bucket_em_window(dte)
    else:
        min_mult, max_mult = swing_em_window(dte)
    return (min_mult * period_em) <= distance <= (max_mult * period_em)


def _prior_close(hist, now_ct):
    """Most recent daily close strictly before today's session, or None.
    During RTH the last candle is today's forming bar — skip it."""
    candles = (hist or {}).get("candles") or []
    today = now_ct.date()
    for c in reversed(candles):
        ts = c.get("datetime")
        close = c.get("close")
        if ts is None or close is None:
            continue
        cdate = datetime.fromtimestamp(ts / 1000, TZ).date()
        if cdate < today:
            return close
    return None


def intraday_move_ratio(price, prior_close, daily_em):
    """Signed intraday move as a fraction of the daily expected move, or None
    when inputs are missing/zero (caller treats None as 'no veto')."""
    if not price or not prior_close or not daily_em or daily_em <= 0:
        return None
    return (price - prior_close) / daily_em


def _apply_momentum_veto(sigs, move_ratio):
    """Drop CCS when up-extended (>= MOMENTUM_VETO), PCS when down-extended.
    None or in-band ratio passes everything through."""
    if move_ratio is None:
        return sigs
    if move_ratio >= MOMENTUM_VETO:
        return [s for s in sigs if s["type"] != "CCS"]
    if move_ratio <= -MOMENTUM_VETO:
        return [s for s in sigs if s["type"] != "PCS"]
    return sigs


def gex_regime_band(gex_data):
    """Classify net-GEX regime: 'POS' / 'NEG' / 'STRONG_NEG', or None when no
    usable gamma grid. ratio = sum(net) / sum(|net|), normalized so one
    threshold works across $SPX and SPY/QQQ."""
    grid = (gex_data or {}).get("gex") or {}
    net = sum((c.get("net") or 0) for c in grid.values())
    gross = sum(abs(c.get("net") or 0) for c in grid.values())
    if gross <= 0:
        return None
    ratio = net / gross
    if ratio >= 0:
        return "POS"
    if ratio <= GEX_STRONG_NEG:
        return "STRONG_NEG"
    return "NEG"


def passes_wall(sig, walls):
    """Beyond-wall strike placement: CCS short above the call wall (resistance),
    PCS short below the put wall (support); IC must clear both. A None wall side
    imposes no constraint (no gamma data on that side). A missing short strike
    fails (a malformed signal is never admitted)."""
    cw = (walls or {}).get("call_wall")
    pw = (walls or {}).get("put_wall")
    t = sig.get("type")
    ss = sig.get("short_strike")
    if t == "CCS":
        return cw is None or (ss is not None and ss > cw)
    if t == "PCS":
        return pw is None or (ss is not None and ss < pw)
    if t == "IC":
        call_short = sig.get("call_short")
        call_ok = cw is None or (call_short is not None and call_short > cw)
        put_ok = pw is None or (ss is not None and ss < pw)
        return call_ok and put_ok
    return True


def apply_gex_gate(sigs, gex_context):
    """Filter index premium signals by dealer-gamma regime. Per signal:
    non-index / no context / mode=='DIRECTIONAL' -> pass through. STRONG_NEG ->
    drop. NEG -> keep only if beyond-wall AND composite_score >= NEG_GEX_MIN_SCORE.
    POS -> pass through. gex_context maps symbol -> {band, walls}."""
    out = []
    for s in sigs:
        ctx = gex_context.get(s.get("symbol")) if s.get("mode") != "DIRECTIONAL" else None
        if ctx is None:
            out.append(s)
            continue
        band = ctx.get("band")
        if band == "STRONG_NEG":
            continue
        if band == "NEG":
            if not passes_wall(s, ctx.get("walls")):
                continue
            if (s.get("composite_score") or 0) < NEG_GEX_MIN_SCORE:
                continue
        out.append(s)
    return out


def check_earnings_conflict(earnings_date, expiration):
    """Check if an option expiration straddles an earnings date.
    Returns True if earnings fall between today and expiration (conflict)."""
    if not earnings_date or not expiration:
        return False
    try:
        today = datetime.now(TZ).date()
        earn_dt = datetime.strptime(str(earnings_date)[:10], "%Y-%m-%d").date()
        exp_dt = datetime.strptime(str(expiration)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    earliest = today - timedelta(days=5)
    return earliest <= earn_dt <= exp_dt


def detect_strike_increment(strikes):
    """Detect the standard strike increment from a sorted list of strikes.

    Returns the most common minimum gap, or None if fewer than 2 strikes.
    """
    if len(strikes) < 2:
        return None

    sorted_strikes = sorted(strikes)
    gaps = [round(sorted_strikes[i+1] - sorted_strikes[i], 4)
            for i in range(len(sorted_strikes) - 1)
            if sorted_strikes[i+1] - sorted_strikes[i] > 0]

    if not gaps:
        return None

    # Most common gap is the standard increment
    counter = Counter(gaps)
    return counter.most_common(1)[0][0]


#############################################
# SPREAD SCREENING
#############################################

def screen_spreads(chain, symbol, dte_min, dte_max, put_d_min, put_d_max,
                   call_d_min, call_d_max, min_cr_pct, trade_type,
                   spot=None, daily_expected_move=None, earnings_date=None,
                   widths=None, account_size=100000, max_risk_pct=0.05,
                   now_ct=None, mode="PREMIUM"):
    if not chain:
        return []
    if now_ct is None:
        now_ct = datetime.now(TZ)
    underlying = chain.get("underlyingPrice", 0)
    if underlying == 0:
        return []

    results = []

    # Reject-reason counters: initialised here so the "NO SPREADS" log path
    # at the bottom never sees an unbound name when both loops skip entirely
    # (empty exp maps, or every expiration outside [dte_min, dte_max], or
    # every contract failing earlier sanity checks). Counters accumulate
    # across both sides + all expirations — log now shows totals, not just
    # the last expiration's reasons.
    delta_pass = 0; mark_fail = 0; liq_fail_short = 0; liq_fail_long = 0
    em_fail = 0; credit_fail = 0; no_width = 0

    for side, exp_map, d_min, d_max in [
        ("PCS", chain.get("putExpDateMap", {}), put_d_min, put_d_max),
        ("CCS", chain.get("callExpDateMap", {}), call_d_min, call_d_max),
    ]:
        for exp_key, strikes_data in exp_map.items():
            parts = exp_key.split(":")
            exp_str, dte = parts[0], int(float(parts[1]))
            if not (dte_min <= dte <= dte_max):
                continue

            # Earnings avoidance (swing trades only)
            if earnings_date and trade_type == "SWING":
                if check_earnings_conflict(earnings_date, exp_str):
                    log.info(f"  [{trade_type}] Skipping {exp_str} — earnings conflict ({earnings_date})")
                    continue

            opts = {}
            for sk, contracts in strikes_data.items():
                k = float(sk)
                if contracts:
                    c = contracts[0]
                    d = c.get("delta")
                    if d is None:
                        continue
                    # Use mark if available; fall back to bid/ask midpoint
                    # or previous close for pre-market scans.
                    raw_mark = c.get("mark") or 0
                    bid = c.get("bid") or 0
                    ask = c.get("ask") or 0
                    if raw_mark <= 0 and bid > 0 and ask > 0:
                        raw_mark = round((bid + ask) / 2, 4)
                    if raw_mark <= 0:
                        raw_mark = c.get("close") or c.get("theoreticalOptionValue") or 0
                    opts[k] = {"strike": k, "delta": d, "mark": raw_mark,
                               "bid": bid, "ask": ask,
                               "theta": c.get("theta") or 0, "vega": c.get("vega") or 0,
                               "gamma": c.get("gamma") or 0, "iv": c.get("volatility") or 0,
                               "volume": c.get("totalVolume") or 0, "oi": c.get("openInterest") or 0}

            # --- Strike increment validation (applies to all strikes) ---
            pre_inc_count = len(opts)
            all_strikes_for_exp = sorted(opts.keys())
            std_increment = detect_strike_increment(all_strikes_for_exp)
            if std_increment is not None and std_increment > 0:
                valid_strikes = {}
                for sk, v in opts.items():
                    remainder = sk % std_increment
                    if remainder < 1e-9 or abs(remainder - std_increment) < 1e-9:
                        valid_strikes[sk] = v
                log.debug(f"  [{trade_type}] {side} Increment filter: inc={std_increment} "
                          f"| {pre_inc_count} -> {len(valid_strikes)} survived")
                opts = valid_strikes

            for k, short in opts.items():
                d = short["delta"]
                if not (d_min <= d <= d_max):
                    continue
                delta_pass += 1
                if short["mark"] <= 0:
                    mark_fail += 1
                    continue
                # Entry-delta ceiling (PREMIUM only): keep the short strike
                # comfortably below the delta stop so positions aren't born past
                # their own exit. The DIRECTIONAL pass intentionally sells closer
                # to spot (0.30-0.55 delta) with its own risk controls, so it is
                # exempt.
                if mode != "DIRECTIONAL" and abs(d) > MAX_ENTRY_SHORT_DELTA:
                    continue
                # --- Strike validity: only SHORT leg must be in expected move window ---
                if spot is not None and daily_expected_move is not None and daily_expected_move > 0:
                    if not is_strike_in_expected_move_window(
                            k, spot, daily_expected_move, dte, trade_type,
                            now_ct=now_ct, mode=mode):
                        em_fail += 1
                        continue
                if not passes_liquidity_gate(short, trade_type, is_short_leg=True):
                    liq_fail_short += 1
                    continue

                # Width selection: auto (widths=None) or explicit list
                if widths is None:
                    selection = select_best_width(
                        short, opts, side, std_increment or 1.0, trade_type, min_cr_pct,
                        account_size=account_size, max_risk_pct=max_risk_pct)
                    if selection is None:
                        no_width += 1
                        continue
                    w, lo, credit, ml = selection
                    # Sanity cap already enforced inside select_best_width
                    pop = ((1 + d) if side == "PCS" else (1 - d)) * 100
                    bk = (k - credit) if side == "PCS" else (k + credit)
                    long_k = lo["strike"]

                    results.append({
                        "id": f"{symbol}_{side}_{exp_str}_{k}_{long_k}",
                        "symbol": symbol, "type": side, "trade_type": trade_type,
                        "expiration": exp_str, "dte": dte,
                        "short_strike": k, "long_strike": long_k, "width": w,
                        "short_mark": round(short["mark"], 2),
                        "long_mark": round(lo["mark"], 2),
                        "credit": round(credit, 2), "max_loss": round(ml, 2),
                        "rr_pct": round(credit / ml * 100, 1),
                        "pop_pct": round(pop, 1), "short_delta": round(d, 4),
                        "net_theta": round((short["theta"] or 0) - (lo["theta"] or 0), 3),
                        "net_vega": round((short["vega"] or 0) - (lo["vega"] or 0), 3),
                        "entry_net_delta_position": round((lo["delta"] or 0) - (short["delta"] or 0), 4),
                        "entry_net_theta_position": round((lo["theta"] or 0) - (short["theta"] or 0), 3),
                        "spread_bid": round((short.get("bid") or 0) - (lo.get("ask") or 0), 2),
                        "spread_ask": round((short.get("ask") or 0) - (lo.get("bid") or 0), 2),
                        "short_iv": round(short["iv"], 1), "breakeven": round(bk, 2),
                        "volume": short["volume"], "underlying_price": underlying,
                        "bid": short["bid"], "ask": short["ask"],
                        "suggested_limit": round(short["bid"] + (short["ask"] - short["bid"]) * 0.40, 2),
                        "timestamp": datetime.now(TZ).isoformat(),
                        "mode": mode,
                    })
                else:
                    # Explicit widths list: original behavior
                    for w in widths:
                        long_k = (k - w) if side == "PCS" else (k + w)
                        if long_k not in opts:
                            continue
                        lo = opts[long_k]
                        if lo["mark"] <= 0:
                            continue
                        if not passes_liquidity_gate(lo, trade_type, is_short_leg=False):
                            liq_fail_long += 1
                            continue
                        credit = _entry_credit(short, lo, w)
                        if credit is None or credit <= 0:
                            continue
                        ml = w - credit
                        effective_min = calc_effective_min_credit(w, min_cr_pct)
                        if ml <= 0 or credit / w < effective_min:
                            credit_fail += 1
                            continue
                        # Delta-aware edge floor (same rule as select_best_width):
                        # pay above the |delta| breakeven plus a margin.
                        if credit / w < abs(d) + EDGE_MARGIN:
                            credit_fail += 1
                            continue
                        # Sanity cap: credit cannot exceed ~95% of width in
                        # practice — anything higher indicates bad market data.
                        if credit / w > 0.95:
                            continue

                        pop = ((1 + d) if side == "PCS" else (1 - d)) * 100
                        bk = (k - credit) if side == "PCS" else (k + credit)

                        results.append({
                            "id": f"{symbol}_{side}_{exp_str}_{k}_{long_k}",
                            "symbol": symbol, "type": side, "trade_type": trade_type,
                            "expiration": exp_str, "dte": dte,
                            "short_strike": k, "long_strike": long_k, "width": w,
                            "short_mark": round(short["mark"], 2),
                            "long_mark": round(lo["mark"], 2),
                            "credit": round(credit, 2), "max_loss": round(ml, 2),
                            "rr_pct": round(credit / ml * 100, 1),
                            "pop_pct": round(pop, 1), "short_delta": round(d, 4),
                            "net_theta": round((short["theta"] or 0) - (lo["theta"] or 0), 3),
                            "net_vega": round((short["vega"] or 0) - (lo["vega"] or 0), 3),
                            "entry_net_delta_position": round((lo["delta"] or 0) - (short["delta"] or 0), 4),
                            "entry_net_theta_position": round((lo["theta"] or 0) - (short["theta"] or 0), 3),
                            "spread_bid": round((short.get("bid") or 0) - (lo.get("ask") or 0), 2),
                            "spread_ask": round((short.get("ask") or 0) - (lo.get("bid") or 0), 2),
                            "short_iv": round(short["iv"], 1), "breakeven": round(bk, 2),
                            "volume": short["volume"], "underlying_price": underlying,
                            "bid": short["bid"], "ask": short["ask"],
                            "suggested_limit": round(short["bid"] + (short["ask"] - short["bid"]) * 0.40, 2),
                            "timestamp": datetime.now(TZ).isoformat(),
                            "mode": mode,
                        })

    if not results:
        log.info(f"  [{trade_type}] NO SPREADS — delta={delta_pass} mark={mark_fail} em={em_fail} "
                 f"liq_short={liq_fail_short} liq_long={liq_fail_long} credit={credit_fail} "
                 f"no_width={no_width}")
    else:
        log.info(f"  [{trade_type}] {len(results)} spreads produced")
    results.sort(key=lambda x: x["rr_pct"], reverse=True)
    return results


def build_iron_condors(spreads, max_n=3):
    pcs = [s for s in spreads if s["type"] == "PCS"][:5]
    ccs = [s for s in spreads if s["type"] == "CCS"][:5]
    ics = []
    for p in pcs:
        for c in ccs:
            if p["symbol"] == c["symbol"] and p["expiration"] == c["expiration"]:
                cr = p["credit"] + c["credit"]
                ml = max(p["width"], c["width"]) - cr
                if ml <= 0:
                    continue
                ics.append({
                    "id": f"{p['symbol']}_IC_{p['expiration']}_{p['short_strike']}_{c['short_strike']}",
                    "symbol": p["symbol"], "type": "IC", "trade_type": p["trade_type"],
                    "expiration": p["expiration"], "dte": p["dte"],
                    "short_strike": p["short_strike"], "long_strike": p["long_strike"],
                    "call_short": c["short_strike"], "call_long": c["long_strike"],
                    "short_mark": p.get("short_mark"), "long_mark": p.get("long_mark"),
                    "call_short_mark": c.get("short_mark"),
                    "call_long_mark": c.get("long_mark"),
                    # Forward short-leg liquidity (additive) so the swing scanner's
                    # per-family liquidity gate can check BOTH shorts: put side from
                    # the PCS, call side from the CCS. Other consumers ignore these.
                    "bid": p.get("bid"), "ask": p.get("ask"), "volume": p.get("volume"),
                    "call_bid": c.get("bid"), "call_ask": c.get("ask"),
                    "call_volume": c.get("volume"),
                    "width": p["width"], "credit": round(cr, 2), "max_loss": round(ml, 2),
                    "spread_bid": round(p.get("spread_bid", 0) + c.get("spread_bid", 0), 2),
                    "spread_ask": round(p.get("spread_ask", 0) + c.get("spread_ask", 0), 2),
                    "rr_pct": round(cr / ml * 100, 1),
                    "pop_pct": round(min(p["pop_pct"], c["pop_pct"]), 1),
                    "short_delta": round(p["short_delta"] + c["short_delta"], 4),
                    "net_theta": round(p["net_theta"] + c["net_theta"], 3),
                    "breakeven": f"{p['breakeven']}/{c['breakeven']}",
                    "underlying_price": p["underlying_price"],
                    "timestamp": datetime.now(TZ).isoformat(),
                    "mode": "PREMIUM",
                    "_pcs_signal": p,
                    "_ccs_signal": c,
                })
    ics.sort(key=lambda x: x["rr_pct"], reverse=True)
    return ics[:max_n]


#############################################
# FULL SCAN CYCLE
#############################################

MIN_ABS_CREDIT = 0.25  # $25 per contract minimum

# --- Profit target and contract sizing for 0-DTE ---
PROFIT_TARGET = 1000        # Dollar profit target per trade
CONTRACT_ROUND_TO = 5       # Position sizes must be multiples of 5
CONTRACT_DEFAULT = 10       # Typical position size


def calc_expected_pnl(credit, max_loss, pop_pct, contracts):
    """Expected P&L at expiration in dollars.

    E[PnL] = (credit * P(win) - max_loss * P(lose)) * contracts * 100
    """
    p = pop_pct / 100.0
    return ((credit * p) - (max_loss * (1 - p))) * contracts * 100


def calc_contracts_for_target(credit, profit_target=PROFIT_TARGET):
    """Contracts needed to hit profit target if held to expiration for full credit.

    Rounds to nearest multiple of CONTRACT_ROUND_TO (5), minimum 5.
    Returns 0 if credit is non-positive.
    """
    if credit <= 0:
        return 0
    raw = profit_target / (credit * 100)
    rounded = int(round(raw / CONTRACT_ROUND_TO) * CONTRACT_ROUND_TO)
    return max(CONTRACT_ROUND_TO, rounded)


def calc_effective_min_credit(width, min_cr_pct, min_abs_credit=None):
    """Return effective min credit as fraction of width.
    Takes the higher of the percentage threshold and the absolute floor."""
    if min_abs_credit is None:
        min_abs_credit = MIN_ABS_CREDIT
    if width <= 0:
        return min_cr_pct
    abs_as_pct = min_abs_credit / width
    return max(min_cr_pct, abs_as_pct)


# --- Dynamic spread width selection ---
# Candidate widths are strike_increment * these multipliers.
WIDTH_MULTIPLIERS = [1, 2, 3, 5, 10, 15, 20, 25, 50, 100]

# Safety cap -- widths above this ($ value) are impractical.
MAX_WIDTH_DOLLARS = 200


def _entry_credit(short, lo, width):
    """Candidate credit under the realistic fill model.

    Returns None when the net market is untradeable (gate in fill_model).
    Falls back to mark-mid credit when any leg lacks a two-sided quote
    (pre-market scans price legs off close/theoretical values).
    """
    sb = short.get("bid") or 0
    sa = short.get("ask") or 0
    lb = lo.get("bid") or 0
    la = lo.get("ask") or 0
    if min(sb, sa, lb, la) <= 0:
        return short["mark"] - lo["mark"]
    if not fill_model.is_tradeable(sb, sa, lb, la, width):
        return None
    return fill_model.realistic_vertical_fill(sb, sa, lb, la, "SELL_TO_OPEN")


def select_best_width(short, opts, side, strike_increment, trade_type,
                      min_cr_pct, account_size=100000, max_risk_pct=0.05):
    """Select the width that maximizes expected total dollar P&L.

    For each candidate width that passes liquidity and the credit/width sanity
    floor, contracts are sized to hit PROFIT_TARGET on a win
    (calc_contracts_for_target), then capped by the account risk limit
    (calculate_position_size). The width with the highest
    E[PnL_total] = (credit·PoP − max_loss·(1−PoP)) × contracts × 100 wins.

    Narrow 0-DTE spreads (small credit, large contract count) and wider swing
    spreads (large credit, small contract count) are evaluated on the same
    dollar-profit basis — no hard per-share credit floor.

    Args:
        short: opts dict entry for the short leg (has strike, delta, mark, etc.)
        opts: dict of all strikes in the chain for this expiration
        side: "PCS" or "CCS"
        strike_increment: float, the chain's standard strike increment
        trade_type: "0-DTE" or "SWING" (for liquidity gate)
        min_cr_pct: minimum credit as fraction of width (regime sanity floor)
        account_size: account size for risk-cap contract sizing
        max_risk_pct: max % of account to risk per trade (0.05 = 5%)

    Returns:
        Tuple (width, long_leg_opts, credit, max_loss) or None.
    """
    k = short["strike"]
    d = short["delta"]
    pop = (1 + d) if side == "PCS" else (1 - d)

    candidates = []
    for mult in WIDTH_MULTIPLIERS:
        w = mult * strike_increment
        if w > MAX_WIDTH_DOLLARS:
            break
        long_k = (k - w) if side == "PCS" else (k + w)
        if long_k not in opts:
            continue
        lo = opts[long_k]
        if lo["mark"] <= 0:
            continue
        if not passes_liquidity_gate(lo, trade_type, is_short_leg=False):
            continue
        credit = _entry_credit(short, lo, w)
        if credit is None or credit <= 0:
            continue
        ml = w - credit
        if ml <= 0:
            continue
        if credit / w > 0.95:
            continue  # sanity cap on absurd marks
        effective_min = calc_effective_min_credit(w, min_cr_pct)
        if credit / w < effective_min:
            continue
        # Delta-aware edge floor: pay strictly above the assignment-probability
        # breakeven (|delta|) plus a margin. (E[PnL]>0 below is the zero-margin
        # form of this; this makes the margin explicit.)
        if credit / w < abs(d) + EDGE_MARGIN:
            continue

        # Contract sizing: hit profit target on a win, bounded by risk cap.
        n_target = calc_contracts_for_target(credit)
        n_risk = calculate_position_size(ml, account_size, max_risk_pct)
        contracts = min(n_target, n_risk) if n_risk > 0 else n_target
        if contracts < CONTRACT_ROUND_TO:
            contracts = CONTRACT_ROUND_TO

        # Expected total $P&L at the sized position.
        e_pnl = (credit * pop - ml * (1 - pop)) * contracts * 100
        if e_pnl <= 0:
            continue

        candidates.append((e_pnl, w, lo, credit, ml))

    if not candidates:
        return None

    # Highest E[PnL] first; ties broken by narrower width (less capital at risk)
    candidates.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    _, w, lo, credit, ml = candidates[0]
    return (w, lo, credit, ml)


def run_full_scan(client, symbols=None, account_size=100000, max_risk_pct=0.05):
    """Execute one full scan cycle. Returns structured results dict."""
    from iv_analysis import run_iv_analysis
    from scoring import score_all_signals, score_iron_condor, calc_composite_score
    from gamma_tool import GammaEngine, get_gex_walls, calc_dex_from_chain, get_dex_walls, get_directional_walls

    from regime_filter import evaluate_regime, filter_signals

    wl_warning = None
    if symbols is None:
        symbols = get_scan_symbols()
        wl_warning = get_load_error()

    now = datetime.now(TZ)
    today = now.date()
    results = {
        "timestamp": now.isoformat(),
        "vix": 0, "vix_regime": {},
        "sentiment_regime": {},
        "symbols": {},
        "iv_data": {},
        "signals_0dte": [],
        "signals_swing": [],
        "errors": [],
        "warnings": [],
    }
    if wl_warning:
        results["warnings"].append(wl_warning)

    # Quotes
    quotes = fetch_quotes(client, symbols + ["$VIX", "$VIX9D", "$VIX3M"])
    if not quotes:
        results["errors"].append(
            "No market data — quote fetch failed (check Schwab connection / proxy).")
        return results

    prices = {}
    vix9d = 0
    vix3m = 0
    for sym, data in quotes.items():
        q = data.get("quote", data.get("reference", data))
        p = q.get("lastPrice", 0)
        if sym == "$VIX":
            results["vix"] = p
            results["vix_regime"] = vix_regime(p)
        elif sym == "$VIX9D":
            vix9d = p
        elif sym == "$VIX3M":
            vix3m = p
        else:
            prices[sym] = p

    results["vix_term_structure"] = calc_vix_term_structure(results["vix"], vix9d, vix3m)

    # Surface any requested symbols Schwab couldn't quote (e.g. a malformed
    # ticker in the watchlist) — they are skipped downstream when price is 0.
    requested = [s for s in symbols if s not in ("$VIX", "$VIX9D", "$VIX3M")]
    dropped = [s for s in requested if s not in prices]
    if dropped:
        log.info("Scan dropped %d unquotable symbol(s): %s",
                 len(dropped), ", ".join(dropped))
        results["warnings"].append(
            f"Dropped {len(dropped)} unquotable symbol(s): {', '.join(dropped)}")

    regime_label = results["vix_regime"].get("regime", "NORMAL")

    # Bucket DTE ranges (2026-05-21 redesign):
    #   "0-DTE" bucket scans 0-4 DTE — captures same-day plus the next few
    #     expirations where pro 0/1-DTE strategies actually operate. True 0-DTE
    #     far-OTM at low VIX produces $0.01-$0.03 marks with no tradeable
    #     credit; DTE 1-4 has meaningful overnight/multi-day theta to harvest.
    #   "SWING" bucket scans 5-15 DTE.
    # IV analysis still wants 20-45 DTE for ATM IV extraction.
    iv_from = today + timedelta(days=20)
    iv_to = today + timedelta(days=45)
    zerodte_from = today
    zerodte_to = today + timedelta(days=4)
    swing_from = today + timedelta(days=5)
    swing_to = today + timedelta(days=15)
    zerodte_min_dte = 0
    zerodte_max_dte = 4
    swing_min_dte = 5
    swing_max_dte = 15

    def _empty_iv_data(sym):
        return {
            "symbol": sym, "current_iv": None,
            "iv_rank": None, "iv_percentile": None,
            "iv_high_52w": None, "iv_low_52w": None,
            "hv_rank": None, "hv_percentile": None,
            "hv_high_52w": None, "hv_low_52w": None,
            "expected_moves": {"daily": None, "weekly": None, "monthly": None},
        }

    # Per-symbol I/O runs in a worker thread. The schwab-py client is sync
    # and network-bound, so threads (not asyncio) are the right fit.
    def _fetch_symbol_data(symbol):
        price = prices.get(symbol, 0)
        if price == 0:
            return symbol, None
        hist = fetch_price_history(client, symbol)
        # Swing (1-15 DTE) and IV analysis (20-45 DTE) windows are disjoint,
        # so fetch them separately. run_iv_analysis falls back to a broader
        # 0-60 DTE fetch internally if its chain comes back empty.
        chain_iv = fetch_option_chain(client, symbol, from_date=iv_from, to_date=iv_to)
        chain_swing = fetch_option_chain(client, symbol, from_date=swing_from, to_date=swing_to)
        try:
            iv_data = run_iv_analysis(
                client, symbol, price=price, hist=hist, chain=chain_iv,
            )
        except Exception as e:
            log.warning(f"  IV analysis for {symbol} failed: {e}")
            iv_data = _empty_iv_data(symbol)
        # "0-DTE" bucket now covers 0-4 DTE — fetch the whole window in one call.
        chain_0 = fetch_option_chain(client, symbol, from_date=zerodte_from, to_date=zerodte_to)
        return symbol, {
            "price": price, "hist": hist, "iv_data": iv_data,
            "chain_0": chain_0, "chain_s": chain_swing,
        }

    active = [s for s in symbols if prices.get(s, 0) > 0]
    per_sym = {}
    if active:
        with ThreadPoolExecutor(max_workers=min(_SCAN_MAX_WORKERS, len(active))) as ex:
            for symbol, data in ex.map(_fetch_symbol_data, active):
                if data is not None:
                    per_sym[symbol] = data

    # Serial processing over pre-fetched data — all CPU-bound, no I/O.
    gex_context = {}  # symbol -> {"band", "walls"} for index dealer-gamma gate
    for symbol in symbols:
        data = per_sym.get(symbol)
        if data is None:
            continue
        price = data["price"]
        tech = calc_technicals(data["hist"])
        results["symbols"][symbol] = {"price": price, "technicals": tech}

        iv_data = data["iv_data"]
        results["iv_data"][symbol] = iv_data
        log.info(f"  {symbol} IV={iv_data.get('current_iv','?')}% "
                 f"Rank={iv_data.get('iv_rank','?')} "
                 f"Pctl={iv_data.get('iv_percentile','?')}")

        daily_move = (iv_data.get("expected_moves") or {}).get("daily")
        daily_em = daily_move.get("move_dollars", 0) if isinstance(daily_move, dict) else (daily_move or 0)
        move_ratio = intraday_move_ratio(
            price, _prior_close(data["hist"], datetime.now(TZ)), daily_em)

        # 0-DTE
        chain_0 = data["chain_0"]
        if chain_0 and chain_0.get("status") != "FAILED":
            # Compute GEX + DEX walls for 0-DTE short strike proximity scoring
            gamma_engine = GammaEngine()
            gex_data = gamma_engine.calc_from_chain(chain_0)
            band = gex_regime_band(gex_data)
            if symbol in INDEX_SYMBOLS and band is not None:
                gex_context[symbol] = {
                    "band": band,
                    "walls": get_directional_walls(gex_data, price),
                }
            dex_data = calc_dex_from_chain(chain_0)
            gex_walls = get_gex_walls(gex_data, top_n=5) if gex_data else []
            dex_walls = get_dex_walls(dex_data, top_n=5) if dex_data else []

            # Delta is a sanity rail only; EM window is primary. Allow full +/- sanity range.
            pd_min_0, pd_max_0 = -DELTA_SANITY_MAX, -1e-6
            cd_min_0, cd_max_0 =  1e-6,  DELTA_SANITY_MAX
            min_cr_0 = get_min_credit_pct(regime_label, "0-DTE")
            sigs = screen_spreads(chain_0, symbol, zerodte_min_dte, zerodte_max_dte,
                                  pd_min_0, pd_max_0, cd_min_0, cd_max_0, min_cr_0, "0-DTE",
                                  spot=price, daily_expected_move=daily_em,
                                  account_size=account_size, max_risk_pct=max_risk_pct,
                                  now_ct=datetime.now(TZ))

            # Attach walls to every signal for scoring
            for s in sigs:
                s["gex_walls"] = gex_walls
                s["dex_walls"] = dex_walls

            sigs = _apply_momentum_veto(sigs, move_ratio)
            ics = build_iron_condors(sigs, 2)
            for ic in ics:
                ic["gex_walls"] = gex_walls
                ic["dex_walls"] = dex_walls

            pcs = [s for s in sigs if s["type"] == "PCS"][:3]
            ccs = [s for s in sigs if s["type"] == "CCS"][:3]
            results["signals_0dte"].extend(pcs + ccs + ics)

        # Swing
        chain_s = data["chain_s"]
        if chain_s and chain_s.get("status") != "FAILED":
            pd_min_s, pd_max_s = -DELTA_SANITY_MAX, -1e-6
            cd_min_s, cd_max_s =  1e-6,  DELTA_SANITY_MAX
            min_cr_s = get_min_credit_pct(regime_label, "SWING")
            sigs = screen_spreads(chain_s, symbol, swing_min_dte, swing_max_dte, pd_min_s, pd_max_s, cd_min_s, cd_max_s, min_cr_s, "SWING",
                                  spot=price, daily_expected_move=daily_em,
                                  account_size=account_size, max_risk_pct=max_risk_pct,
                                  now_ct=datetime.now(TZ))
            sigs = _apply_momentum_veto(sigs, move_ratio)
            ics = build_iron_condors(sigs, 2)
            pcs = [s for s in sigs if s["type"] == "PCS"][:3]
            ccs = [s for s in sigs if s["type"] == "CCS"][:3]
            results["signals_swing"].extend(pcs + ccs + ics)

    # --- Directional pass (regime-gated) ---
    # Evaluate the regime ONCE — both the directional pass and the late
    # sentiment/trend filter below share this result. A single read avoids a
    # mid-scan race where sentiment_bridge.json could be rewritten between
    # two evaluate_regime calls and silently produce inconsistent gating.
    _regime_tech_map = {
        sym: (info.get("technicals") or {})
        for sym, info in results["symbols"].items()
    }
    sentiment_regime = evaluate_regime(technicals_by_symbol=_regime_tech_map)
    results["sentiment_regime"] = sentiment_regime

    if sentiment_regime.get("active"):
        now_ct = datetime.now(TZ)
        for symbol in symbols:
            data = per_sym.get(symbol)
            if data is None:
                continue
            iv_data = results["iv_data"].get(symbol, {})
            daily_move = (iv_data.get("expected_moves") or {}).get("daily")
            daily_em = (daily_move.get("move_dollars", 0)
                        if isinstance(daily_move, dict) else (daily_move or 0))
            spot = data["price"]

            per_sym_regime = (sentiment_regime.get("per_symbol", {}).get(symbol)
                              or {"allow_ccs": sentiment_regime["allow_ccs"],
                                  "allow_pcs": sentiment_regime["allow_pcs"]})

            # BULLISH (allow_ccs=False) -> produce directional PCS
            # BEARISH (allow_pcs=False) -> produce directional CCS
            if not per_sym_regime.get("allow_ccs", True):
                directional_side = "PCS"
            elif not per_sym_regime.get("allow_pcs", True):
                directional_side = "CCS"
            else:
                continue  # neither side blocked -> no directional bet

            put_d_min, put_d_max = DIRECTIONAL_DELTA_RANGE["PCS"]
            call_d_min, call_d_max = DIRECTIONAL_DELTA_RANGE["CCS"]
            # Mask the opposite side: zero-width band, plus screen_spreads uses
            # inclusive bounds and no real contract has delta exactly == 0, so
            # this guarantees the masked side produces nothing.
            if directional_side == "PCS":
                call_d_min = call_d_max = 0.0
            else:
                put_d_min = put_d_max = 0.0

            # 0-DTE bucket
            chain_0 = data.get("chain_0")
            # Guard against pre-existing screen_spreads bug on empty chains (see follow-up task)
            if chain_0 and chain_0.get("status") != "FAILED" and (
                chain_0.get("putExpDateMap") or chain_0.get("callExpDateMap")
            ):
                dir_sigs_0 = screen_spreads(
                    chain_0, symbol, zerodte_min_dte, zerodte_max_dte,
                    put_d_min, put_d_max, call_d_min, call_d_max,
                    DIRECTIONAL_MIN_CREDIT_PCT, "0-DTE",
                    spot=spot, daily_expected_move=daily_em,
                    account_size=account_size,
                    max_risk_pct=DIRECTIONAL_MAX_RISK_PCT,
                    now_ct=now_ct, mode="DIRECTIONAL",
                )
                dir_sigs_0 = [s for s in dir_sigs_0 if s["type"] == directional_side]
                dir_sigs_0 = dir_sigs_0[:DIRECTIONAL_MAX_PER_SYMBOL_BUCKET]
                results["signals_0dte"].extend(dir_sigs_0)

            # SWING bucket
            chain_s = data.get("chain_s")
            # Guard against pre-existing screen_spreads bug on empty chains (see follow-up task)
            if chain_s and chain_s.get("status") != "FAILED" and (
                chain_s.get("putExpDateMap") or chain_s.get("callExpDateMap")
            ):
                dir_sigs_s = screen_spreads(
                    chain_s, symbol, swing_min_dte, swing_max_dte,
                    put_d_min, put_d_max, call_d_min, call_d_max,
                    DIRECTIONAL_MIN_CREDIT_PCT, "SWING",
                    spot=spot, daily_expected_move=daily_em,
                    account_size=account_size,
                    max_risk_pct=DIRECTIONAL_MAX_RISK_PCT,
                    now_ct=now_ct, mode="DIRECTIONAL",
                )
                dir_sigs_s = [s for s in dir_sigs_s if s["type"] == directional_side]
                dir_sigs_s = dir_sigs_s[:DIRECTIONAL_MAX_PER_SYMBOL_BUCKET]
                results["signals_swing"].extend(dir_sigs_s)

    # Sentiment/trend regime filter — drop signals on the structurally-doomed
    # side based on the SentimentDashboard bridge + per-symbol technical trend.
    # Reuses `sentiment_regime` evaluated once above.
    if sentiment_regime.get("active"):
        log.info(f"Sentiment regime: {sentiment_regime.get('reason')}")
        results["signals_0dte"] = filter_signals(results["signals_0dte"], sentiment_regime)
        results["signals_swing"] = filter_signals(results["signals_swing"], sentiment_regime)

    #############################################
    # COMPOSITE SCORING
    #############################################

    # Build technicals map for scoring
    tech_map = {}
    for sym, info in results["symbols"].items():
        tech_map[sym] = info.get("technicals") or {}

    # Score PCS and CCS signals (non-IC)
    spreads_0 = [s for s in results["signals_0dte"] if s["type"] != "IC"]
    spreads_s = [s for s in results["signals_swing"] if s["type"] != "IC"]

    score_all_signals(spreads_0, results["iv_data"], tech_map)
    score_all_signals(spreads_s, results["iv_data"], tech_map)

    # Score Iron Condors using leg scores + delta bonus. If a leg is also one
    # of the top-3 flat PCS/CCS signals, score_all_signals already stamped
    # composite_score onto the dict — reuse it instead of re-running the full
    # 9-factor normalization. Legs that exist only as IC components fall back
    # to the original calc_composite_score path so per-signal behavior is
    # unchanged (peer-percentile theta is not appropriate for lone legs).
    for sig_list in [results["signals_0dte"], results["signals_swing"]]:
        for s in sig_list:
            if s["type"] == "IC":
                pcs_leg = s.get("_pcs_signal")
                ccs_leg = s.get("_ccs_signal")
                iv_for_sym = results["iv_data"].get(s["symbol"], {})
                tech_for_sym = tech_map.get(s["symbol"], {})

                def _leg_score(leg):
                    if leg is None:
                        return None
                    cached = leg.get("composite_score")
                    if cached is not None:
                        return {"composite_score": cached}
                    return calc_composite_score(
                        leg, iv_data=iv_for_sym, technicals=tech_for_sym)

                pcs_sc = _leg_score(pcs_leg)
                ccs_sc = _leg_score(ccs_leg)
                ic_score = score_iron_condor(pcs_sc, ccs_sc, s.get("short_delta"))
                s["composite_score"] = ic_score["composite_score"]
                s["grade"] = ic_score["grade"]
                s["factor_scores"] = {
                    "pcs_leg": ic_score.get("pcs_score", 0),
                    "ccs_leg": ic_score.get("ccs_score", 0),
                    "delta_bonus": ic_score.get("delta_bonus", 0),
                }
                # Clean up internal refs
                s.pop("_pcs_signal", None)
                s.pop("_ccs_signal", None)

    # IV Rank hard floor — reject signals where IV is historically cheap
    for trade_type, sig_list_key in [("0-DTE", "signals_0dte"), ("SWING", "signals_swing")]:
        min_rank = MIN_IV_RANK.get(trade_type, 0)
        if min_rank > 0:
            original_count = len(results[sig_list_key])
            results[sig_list_key] = [
                s for s in results[sig_list_key]
                if (results["iv_data"].get(s["symbol"], {}).get("iv_rank") or 0) >= min_rank
            ]
            filtered = original_count - len(results[sig_list_key])
            if filtered:
                log.info(f"  [{trade_type}] IV Rank floor: {filtered} signals removed (IV Rank < {min_rank})")

    # Dealer-gamma regime gate (index premium only; directional pass exempt)
    for key in ("signals_0dte", "signals_swing"):
        before = len(results[key])
        results[key] = apply_gex_gate(results[key], gex_context)
        dropped = before - len(results[key])
        if dropped:
            log.info(f"  GEX-regime gate: {dropped} index signals removed ({key})")

    # Re-sort all signals by composite score
    results["signals_0dte"].sort(key=lambda x: (x.get("composite_score", 0), x.get("rr_pct", 0)), reverse=True)
    results["signals_swing"].sort(key=lambda x: (x.get("composite_score", 0), x.get("rr_pct", 0)), reverse=True)

    # Add position sizing to all signals
    for sig_list in [results["signals_0dte"], results["signals_swing"]]:
        for s in sig_list:
            ml = s.get("max_loss", 0)
            s["max_contracts"] = calculate_position_size(ml, account_size, max_risk_pct)

    # Inject expected P&L + profit-target sizing on 0-DTE signals only
    for s in results["signals_0dte"]:
        cr = s.get("credit", 0)
        ml = s.get("max_loss", 0)
        pop = s.get("pop_pct", 0)
        s["contracts_for_target"] = calc_contracts_for_target(cr)
        s["expected_pnl_10"] = calc_expected_pnl(cr, ml, pop, CONTRACT_DEFAULT)
        s["expected_pnl_target"] = calc_expected_pnl(cr, ml, pop, s["contracts_for_target"])
        s["max_profit_target"] = round(cr * s["contracts_for_target"] * 100, 2)

    # Persist signals to the signal-tracking DB (score >= 50 dedup)
    try:
        import signal_recorder
        # Inject per-symbol iv_rank (lives in results["iv_data"], not on signal dicts)
        for sig_list in (results["signals_0dte"], results["signals_swing"]):
            for s in sig_list:
                s["iv_rank"] = results["iv_data"].get(s["symbol"], {}).get("iv_rank") or 0
        signal_recorder.record_signals(results["signals_0dte"], "0DTE")
        signal_recorder.record_signals(results["signals_swing"], "SWING")
    except Exception as e:
        log.error(f"signal_recorder failed: {e}")

    # Daily potential-trade journal (futures-options strategy): fires once per
    # day inside the ~13:00 CT entry window, no-op otherwise. Self-gating and
    # idempotent, so it is safe under both orchestrators (CLI scanner.py and the
    # FastAPI scanner_task that calls this). Never let it break the scan.
    try:
        import daily_trade_log
        daily_trade_log.capture_if_due(now)
    except Exception as e:
        log.error(f"daily_trade_log.capture_if_due failed (non-fatal): {e}")

    return results
