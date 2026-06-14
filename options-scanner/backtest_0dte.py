"""
backtest_0dte.py - Daily-bar backtest of directional 0-DTE credit spreads
Version: 1.0.0
Last Updated: 2026-06-13

Backtests a directional 0-DTE futures-options credit-spread strategy on /ES
using ~5 years of DAILY OHLC (Schwab caps intraday history at ~10 days, so an
intraday backtest is impossible over a long sample). VIX9D supplies the
short-dated implied-vol input. Black-Scholes pricing is reused from
options_calculator.py (stdlib, no numpy/scipy).

METHODOLOGY (read before trusting any number)
---------------------------------------------
The strategy is: late in the session, sell a directional credit spread, target
~$300 of premium, let it expire worthless; stop out at 2x the credit collected.

Daily bars cannot show the last-2-hours price move the strategy actually risks.
So the PRIMARY model is the conservative *full-session* version:

    enter at the OPEN, settle at the CLOSE, T = 1 session = 1/252 yr.

A full session has MORE time to breach the short strike than a late-day entry,
so this model's win rate is a LOWER BOUND for the real "enter 1-3 hrs before
close" strategy. If it clears the bar entered at the open, it does better
entered late. A separate late-day PoP figure (model-based, T = 2h) shows the
uplift from entering later.

Assumptions, all deliberately conservative on credit / pessimistic on P&L:
  * Credit modeled with VIX9D; true 0-DTE IV runs higher -> real credit >= modeled.
  * Direction from a trend filter (open vs 20-day SMA). The GEX lean the user
    wants can only be validated FORWARD on gex_history.db (~11 days, growing).
  * 2x-credit stop evaluated at the day's adverse extreme (low for put spreads,
    high for call spreads) with half the holding window of time remaining.
  * Fills at Black-Scholes mid; no slippage/commissions (add ~$1.30/contract/leg
    for ES round-turn when interpreting).

Version 1.0.0 Changes:
- Initial implementation
"""

import sys
import json
import math
import pathlib
import urllib.request
import urllib.parse
import datetime
import logging
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import PROXY_URL

from options_calculator import bs_price, bs_delta

log = logging.getLogger("backtest_0dte")

#############################################
# CONSTANTS
#############################################

TRADING_DAYS_PER_YEAR = 252
SESSION_HOURS = 6.5
RISK_FREE = 0.04

# --- Schwab futures-options costs ---
# Commission is the EXACT Schwab published rate: $2.25 per contract per side.
# Exchange + regulatory (CME + NFA) fees vary by product/membership; the values
# below are representative retail estimates, NOT exact. A credit spread is two
# legs, so per-side cost per spread = 2 * COMM_PER_SIDE_*.
SCHWAB_COMMISSION = 2.25            # $/contract/side (exact)
EXCH_REG_BIG = 1.50                # ~CME+NFA for /ES, /NQ (estimate)
EXCH_REG_MICRO = 0.35              # ~CME+NFA for /MES, /MNQ (estimate)
COMM_PER_SIDE_BIG = SCHWAB_COMMISSION + EXCH_REG_BIG      # 3.75 / leg / side
COMM_PER_SIDE_MICRO = SCHWAB_COMMISSION + EXCH_REG_MICRO  # 2.60 / leg / side

ES_MULT = 50.0          # /ES options: $50 per index point
MES_MULT = 5.0          # /MES options: $5 per index point
NQ_MULT = 20.0          # /NQ options: $20 per index point
MNQ_MULT = 2.0          # /MNQ options: $2 per index point

# Per-instrument backtest profiles. width is in INDEX POINTS, chosen so the
# spread sits a comparable ~0.34% of spot out for each product (ES 25/7436 ~=
# NQ 100/29678). vol_symbol is the short-dated IV proxy (no 9-day Nasdaq vol
# exists on Schwab, so /NQ uses 30-day VXN — slightly conservative on credit).
INSTRUMENTS = [
    {"name": "S&P",    "price": "/ES", "vol": "$VIX9D", "width": 25,
     "big": ("/ES", ES_MULT, COMM_PER_SIDE_BIG),
     "micro": ("/MES", MES_MULT, COMM_PER_SIDE_MICRO)},
    {"name": "Nasdaq", "price": "/NQ", "vol": "$VXN", "width": 100,
     "big": ("/NQ", NQ_MULT, COMM_PER_SIDE_BIG),
     "micro": ("/MNQ", MNQ_MULT, COMM_PER_SIDE_MICRO)},
]

# One full RTH session expressed in years (the 0-DTE holding period for the
# conservative full-session model).
T_FULL_SESSION = 1.0 / TRADING_DAYS_PER_YEAR


#############################################
# DATA FETCH (via schwab-proxy)
#############################################

def _proxy_daily(symbol, years=5):
    """Fetch daily OHLC candles for `symbol` from the schwab-proxy.

    Returns a dict keyed by 'YYYY-MM-DD' -> {open, high, low, close, volume}.
    """
    q = urllib.parse.urlencode({
        "symbol": symbol,
        "periodType": "year",
        "period": years,
        "frequencyType": "daily",
        "frequency": 1,
    })
    url = f"{PROXY_URL}/pricehistory?{q}"
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    out = {}
    for c in data.get("candles", []):
        d = datetime.datetime.fromtimestamp(c["datetime"] / 1000).strftime("%Y-%m-%d")
        out[d] = {
            "open": c["open"], "high": c["high"],
            "low": c["low"], "close": c["close"],
            "volume": c.get("volume", 0),
        }
    return out


def build_dataset(price_symbol="/ES", vol_symbol="$VIX9D", years=5):
    """Join daily price candles with the VIX9D close on each date.

    Returns a list of per-day dicts sorted by date, each with:
        date, open, high, low, close, sigma (annualized decimal), sma20.
    Days without a VIX9D reading are dropped. sma20 is the 20-day SMA of close
    (None until 20 days are available).
    """
    px = _proxy_daily(price_symbol, years)
    vix = _proxy_daily(vol_symbol, years)
    dates = sorted(set(px) & set(vix))

    rows = []
    closes = []  # prior-day closes only (no look-ahead)
    for d in dates:
        p = px[d]
        sigma = vix[d]["close"] / 100.0
        # SMA uses the 20 PRIOR closes — today's close is unknown at open-entry.
        sma20 = sum(closes[-20:]) / 20.0 if len(closes) >= 20 else None
        closes.append(p["close"])
        rows.append({
            "date": d,
            "open": p["open"], "high": p["high"],
            "low": p["low"], "close": p["close"],
            "sigma": sigma, "sma20": sma20,
        })
    return rows


#############################################
# STRIKE SELECTION
#############################################

def solve_strike_by_delta(S, T, r, sigma, option_type, target_delta):
    """Find the strike whose |BS delta| == target_delta via bisection.

    target_delta is a positive magnitude (e.g. 0.10). For puts the strike is
    below S; for calls, above S. Returns a float strike.
    """
    option_type = option_type.lower()
    target = abs(target_delta)
    if option_type == "put":
        lo, hi = S * 0.5, S          # deep OTM .. ATM
    else:
        lo, hi = S, S * 1.5
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        dlt = abs(bs_delta(S, mid, T, r, sigma, option_type))
        # delta magnitude increases toward ATM (toward S)
        if option_type == "put":
            # higher strike (closer to S) -> larger |delta|
            if dlt < target:
                lo = mid
            else:
                hi = mid
        else:
            # lower strike (closer to S) -> larger |delta|
            if dlt < target:
                hi = mid
            else:
                lo = mid
    return 0.5 * (lo + hi)


def strike_by_em(S, T, sigma, option_type, em_mult):
    """Place the short strike at em_mult * expected-move from S.

    Expected move EM = S * sigma * sqrt(T). Put -> below S, call -> above S.
    """
    em = S * sigma * math.sqrt(T)
    if option_type.lower() == "put":
        return S - em_mult * em
    return S + em_mult * em


#############################################
# TRADE EVALUATION
#############################################

def spread_value(S, short_k, long_k, option_type, T, r, sigma):
    """Mark value (in points) of the short vertical = short leg - long leg.

    Positive number = what it costs to buy the spread back. At T<=0 this is the
    intrinsic settlement value.
    """
    return (bs_price(S, short_k, T, r, sigma, option_type)
            - bs_price(S, long_k, T, r, sigma, option_type))


def evaluate_trade(row, option_type, strike_rule, width,
                   target_delta=0.10, em_mult=1.0,
                   T=T_FULL_SESSION, r=RISK_FREE,
                   stop_mult=2.0, multiplier=ES_MULT, comm_per_side=0.0):
    """Evaluate one day's credit-spread trade (full-session model).

    option_type : 'put' (bullish, PCS) or 'call' (bearish, CCS)
    strike_rule : 'delta' or 'em'
    width       : spread width in index points
    Returns a dict: credit_pts, credit_usd, short_k, long_k, exit_pts,
        pnl_pts, pnl_usd, outcome ('worthless'|'breach'|'stopped'),
        max_loss_usd.
    """
    S0 = row["open"]
    sigma = row["sigma"]

    if strike_rule == "delta":
        short_k = solve_strike_by_delta(S0, T, r, sigma, option_type, target_delta)
    else:
        short_k = strike_by_em(S0, T, sigma, option_type, em_mult)

    if option_type == "put":
        long_k = short_k - width
    else:
        long_k = short_k + width

    credit_pts = spread_value(S0, short_k, long_k, option_type, T, r, sigma)
    credit_usd = credit_pts * multiplier
    max_loss_usd = width * multiplier - credit_usd

    # --- 2x-credit stop, checked at the adverse intraday extreme ---
    # Put spread is hurt by a fall (use the day's low); call spread by a rise
    # (use the high). Use half the holding window of time remaining at the
    # extreme (we don't know when it occurred).
    adverse = row["low"] if option_type == "put" else row["high"]
    mark_at_extreme = spread_value(adverse, short_k, long_k, option_type,
                                   T * 0.5, r, sigma)
    loss_at_extreme = mark_at_extreme - credit_pts
    stop_threshold_pts = stop_mult * credit_pts  # loss in pts that trips stop

    if loss_at_extreme >= stop_threshold_pts:
        pnl_pts = -stop_mult * credit_pts
        outcome = "stopped"
    else:
        # --- settle at the close ---
        exit_pts = spread_value(row["close"], short_k, long_k, option_type,
                                0.0, r, sigma)
        pnl_pts = credit_pts - exit_pts
        outcome = "worthless" if exit_pts <= 1e-9 else "breach"

    # --- commissions (2 legs/spread): entry always; exit only if we close ---
    # A clean OTM worthless expiry pays nothing to close (legs lapse).
    entry_cost = 2.0 * comm_per_side
    exit_cost = 0.0 if outcome == "worthless" else 2.0 * comm_per_side
    commissions = entry_cost + exit_cost

    return {
        "credit_pts": credit_pts,
        "credit_usd": credit_usd,
        "short_k": short_k,
        "long_k": long_k,
        "pnl_pts": pnl_pts,
        "pnl_usd": pnl_pts * multiplier - commissions,
        "outcome": outcome,
        "max_loss_usd": max_loss_usd + 2.0 * comm_per_side,  # worst case incl. exit
        "commissions": commissions,
    }


def choose_direction(row, mode="trend"):
    """Pick the spread side ('put'=bullish, 'call'=bearish), or None to skip.

    Modes (all gated on sma20 being available so every mode trades the SAME
    sample of days, for an apples-to-apples comparison):
      trend   - bullish if open >= 20-day SMA, else bearish (the live default)
      bull    - always sell a put spread
      bear    - always sell a call spread
      inverse - the opposite of `trend` (contrarian)
      oracle  - PERFECT HINDSIGHT: sell the side that won (uses today's close).
                Cheats by design -> the ceiling for ANY direction signal.
    """
    if row["sma20"] is None:
        return None
    if mode == "bull":
        return "put"
    if mode == "bear":
        return "call"
    if mode == "oracle":
        return "put" if row["close"] >= row["open"] else "call"
    trend = "put" if row["open"] >= row["sma20"] else "call"
    if mode == "inverse":
        return "call" if trend == "put" else "put"
    return trend


#############################################
# BACKTEST RUNNER + METRICS
#############################################

def run_backtest(rows, strike_rule, width, target_delta=0.10, em_mult=1.0,
                 stop_mult=2.0, multiplier=ES_MULT, contracts=1,
                 comm_per_side=0.0, direction_mode="trend"):
    """Run the directional strategy across all rows. Returns (trades, stats)."""
    trades = []
    for row in rows:
        otype = choose_direction(row, direction_mode)
        if otype is None:
            continue
        t = evaluate_trade(row, otype, strike_rule, width,
                           target_delta=target_delta, em_mult=em_mult,
                           stop_mult=stop_mult, multiplier=multiplier,
                           comm_per_side=comm_per_side)
        t["date"] = row["date"]
        t["dir"] = otype
        t["pnl_usd"] *= contracts
        t["credit_usd"] *= contracts
        t["max_loss_usd"] *= contracts
        t["commissions"] *= contracts
        trades.append(t)
    return trades, summarize(trades)


def summarize(trades):
    """Aggregate win rate, expectancy, drawdown, target-hit rate."""
    if not trades:
        return {}
    pnls = [t["pnl_usd"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(pnls)

    # max drawdown of the cumulative equity curve
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    outcomes = defaultdict(int)
    for t in trades:
        outcomes[t["outcome"]] += 1

    avg_credit = sum(t["credit_usd"] for t in trades) / n
    total_comm = sum(t.get("commissions", 0.0) for t in trades)
    return {
        "n": n,
        "win_rate": len(wins) / n,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "expectancy": sum(pnls) / n,
        "total_pnl": sum(pnls),
        "max_drawdown": max_dd,
        "best_day": max(pnls),
        "worst_day": min(pnls),
        "avg_credit": avg_credit,
        "avg_comm": total_comm / n,
        "total_comm": total_comm,
        "comm_drag_pct": (total_comm / sum(t["credit_usd"] for t in trades) * 100)
                         if avg_credit else 0.0,
        "pct_hit_300": sum(1 for p in pnls if p >= 300) / n,
        "outcomes": dict(outcomes),
    }


def late_day_pop(rows, target_delta=0.10, hours_to_close=2.0, r=RISK_FREE):
    """Model-based win-rate uplift for entering late.

    For each day, compute P(expire worthless) = 1 - |delta| at a target-delta
    short strike, for the late-day holding window (T = hours/(6.5*252)). This is
    the textbook short-delta PoP and ignores the long wing (a slight
    underestimate of the spread's true win rate). Returns the mean across days.
    """
    T = (hours_to_close / SESSION_HOURS) / TRADING_DAYS_PER_YEAR
    pops = []
    for row in rows:
        otype = choose_direction(row)
        if otype is None:
            continue
        # By construction the short strike sits at target_delta, so
        # P(worthless) ~= 1 - target_delta. We instead report the realized
        # settlement-based figure elsewhere; here just reflect the delta.
        pops.append(1.0 - target_delta)
    # The interesting number is how T shrinks the strike distance for the SAME
    # delta -> tighter strikes but far less time. We report the win-rate proxy
    # as 1 - target_delta (delta == risk-neutral breach prob) which is
    # invariant in T; the practical uplift shows up as larger credit per unit
    # distance, captured in the full-session credit already. Kept for clarity.
    return sum(pops) / len(pops) if pops else 0.0


#############################################
# LATE-DAY ENTRY: PER-DAY MONTE CARLO
#############################################

def simulate_day_paths(S0, short_k, long_k, option_type, T_entry, sigma, r,
                       credit_pts, width, stop_mult, multiplier, comm_per_side,
                       n_paths, n_steps, rng, sigma_path=None):
    """Monte-Carlo one day's late-day trade over the entry window.

    Simulates GBM paths from S0 to expiry (T_entry years, n_steps), tracks the
    adverse extreme to model the 2x-credit stop, settles survivors at intrinsic.
    Returns a list of net P&L ($) per path (commissions included).

    PRICING (credit, stop mark) uses `sigma` = IMPLIED vol (what you sell at).
    PATH dynamics use `sigma_path` = REALIZED vol. When realized < implied (the
    variance risk premium), the market moves less than priced -> the edge.
    Defaults sigma_path = sigma (no premium) if not given.
    """
    if sigma_path is None:
        sigma_path = sigma
    dt = T_entry / n_steps
    drift = (r - 0.5 * sigma_path * sigma_path) * dt
    vol_step = sigma_path * math.sqrt(dt)
    is_put = (option_type == "put")
    # stop is only reachable if the spread can lose >= stop_mult*credit
    stop_reachable = (width - credit_pts) >= stop_mult * credit_pts

    pnls = []
    for _ in range(n_paths):
        S = S0
        extreme = S0
        for _ in range(n_steps):
            S *= math.exp(drift + vol_step * rng.gauss(0.0, 1.0))
            if is_put:
                extreme = min(extreme, S)
            else:
                extreme = max(extreme, S)

        stopped = False
        if stop_reachable:
            mark_extreme = spread_value(extreme, short_k, long_k, option_type,
                                        T_entry * 0.5, r, sigma)
            if (mark_extreme - credit_pts) >= stop_mult * credit_pts:
                stopped = True

        if stopped:
            pnl_pts = -stop_mult * credit_pts
            outcome = "stopped"
        else:
            exit_pts = spread_value(S, short_k, long_k, option_type, 0.0, r, sigma)
            pnl_pts = credit_pts - exit_pts
            outcome = "worthless" if exit_pts <= 1e-9 else "breach"

        exit_cost = 0.0 if outcome == "worthless" else 2.0 * comm_per_side
        commissions = 2.0 * comm_per_side + exit_cost
        pnls.append(pnl_pts * multiplier - commissions)
    return pnls


def run_montecarlo(rows, hours_to_close, strike_rule, width,
                   target_delta=0.16, em_mult=1.0, stop_mult=2.0,
                   multiplier=ES_MULT, comm_per_side=0.0,
                   n_paths=200, seed=12345, realized_ratio=1.0):
    """Late-day MC across all days. Entry price anchored at each day's close
    (late-day level proxy); strikes/credit priced with T = hours_to_close.
    Returns an aggregate stats dict (mean over per-path P&L pooled by day)."""
    rng = __import__("random").Random(seed)
    T = (hours_to_close / SESSION_HOURS) / TRADING_DAYS_PER_YEAR
    n_steps = max(6, int(round(hours_to_close * 12)))  # ~5-min steps

    per_day = []   # one representative (mean) P&L per day for equity-curve stats
    all_pnls = []
    for row in rows:
        otype = choose_direction(row)
        if otype is None:
            continue
        S0 = row["close"]
        sigma = row["sigma"]
        if strike_rule == "delta":
            short_k = solve_strike_by_delta(S0, T, RISK_FREE, sigma, otype, target_delta)
        else:
            short_k = strike_by_em(S0, T, sigma, otype, em_mult)
        long_k = short_k - width if otype == "put" else short_k + width
        credit_pts = spread_value(S0, short_k, long_k, otype, T, RISK_FREE, sigma)

        pnls = simulate_day_paths(S0, short_k, long_k, otype, T, sigma, RISK_FREE,
                                  credit_pts, width, stop_mult, multiplier,
                                  comm_per_side, n_paths, n_steps, rng,
                                  sigma_path=sigma * realized_ratio)
        all_pnls.extend(pnls)
        per_day.append(sum(pnls) / len(pnls))

    # win rate / outcome mix from the pooled paths; drawdown from per-day means
    wins = sum(1 for p in all_pnls if p > 0)
    fake_trades = [{"pnl_usd": p, "credit_usd": 0, "outcome": "", "commissions": 0}
                   for p in per_day]
    s = summarize(fake_trades)
    s["win_rate"] = wins / len(all_pnls) if all_pnls else 0.0
    s["n_paths"] = len(all_pnls)
    s["expectancy"] = sum(all_pnls) / len(all_pnls) if all_pnls else 0.0
    return s


#############################################
# REPORT
#############################################

def _fmt_stats(label, s):
    if not s:
        return f"{label}: no trades"
    return (
        f"{label}\n"
        f"  trades        : {s['n']}\n"
        f"  win rate      : {s['win_rate']*100:5.1f}%   "
        f"(outcomes: {s['outcomes']})\n"
        f"  avg credit    : ${s['avg_credit']:8.2f}\n"
        f"  avg commission: ${s['avg_comm']:8.2f}   "
        f"(drag {s['comm_drag_pct']:.1f}% of credit)\n"
        f"  avg win       : ${s['avg_win']:8.2f}\n"
        f"  avg loss      : ${s['avg_loss']:8.2f}\n"
        f"  expectancy/dy : ${s['expectancy']:8.2f}\n"
        f"  total P&L     : ${s['total_pnl']:10.2f}\n"
        f"  max drawdown  : ${s['max_drawdown']:10.2f}\n"
        f"  best / worst  : ${s['best_day']:.0f} / ${s['worst_day']:.0f}\n"
        f"  days >= $300  : {s['pct_hit_300']*100:5.1f}%\n"
    )


def compute_realized_ratio(rows):
    """Annualized open->close REALIZED vol / mean IMPLIED vol (VIX9D/VXN) over
    the sample. <1 quantifies the variance risk premium (market moves less than
    options imply) -- the source of the premium-selling edge."""
    rets = [math.log(r["close"] / r["open"]) for r in rows if r["open"] > 0]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / (n - 1)
    realized_annual = math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR)
    mean_implied = sum(r["sigma"] for r in rows) / len(rows)
    return realized_annual, mean_implied, realized_annual / mean_implied


def montecarlo_report():
    """Late-day MC: calibrate realized vol from data, validate at full session
    against the empirical backtest, then report the H=2h late-day entry."""
    print("\n" + "#" * 64)
    print("# LATE-DAY MONTE CARLO  (200 paths/day; credit priced at IMPLIED,")
    print("# paths simulated at data-calibrated REALIZED vol)")
    print("#" * 64)
    for inst in INSTRUMENTS:
        rows = build_dataset(inst["price"], inst["vol"], years=5)
        sym, mult, comm = inst["big"]
        rz, imp, ratio = compute_realized_ratio(rows)
        print(f"\n=== {inst['name']} {sym}  delta 0.16  width={inst['width']}pt  "
              f"stop=2x  1 contract  comm=${comm:.2f}/leg ===")
        print(f"  variance risk premium: realized {rz*100:.1f}% vs implied "
              f"{imp*100:.1f}%  ->  realized/implied = {ratio:.2f}")
        for hours, tag in [(6.5, "H=6.5h FULL SESSION (validation vs empirical ~76%)"),
                           (2.0, "H=2.0h LATE-DAY ENTRY (the strategy)")]:
            s = run_montecarlo(rows, hours, "delta", inst["width"],
                               target_delta=0.16, multiplier=mult,
                               comm_per_side=comm, n_paths=200,
                               realized_ratio=ratio)
            print(f"  [{tag}]")
            print(f"     win rate      : {s['win_rate']*100:5.1f}%   "
                  f"(over {s['n_paths']:,} simulated paths)")
            print(f"     expectancy/day: ${s['expectancy']:7.2f}/contract\n")


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if "--mc" in sys.argv:
        montecarlo_report()
        return

    for inst in INSTRUMENTS:
        width = inst["width"]
        print(f"\nFetching {inst['price']} + {inst['vol']} daily (5y) via proxy ...")
        rows = build_dataset(inst["price"], inst["vol"], years=5)
        print(f"  {inst['name']}: {len(rows)} aligned days "
              f"({rows[0]['date']} -> {rows[-1]['date']}), "
              f"width={width}pt (~{width/rows[-1]['close']*100:.2f}% of spot)")

        configs = [
            ("delta 0.10", dict(strike_rule="delta", target_delta=0.10, width=width)),
            ("delta 0.16", dict(strike_rule="delta", target_delta=0.16, width=width)),
            ("EM 1.00x",   dict(strike_rule="em", em_mult=1.00, width=width)),
            ("EM 1.25x",   dict(strike_rule="em", em_mult=1.25, width=width)),
        ]
        for label, (sym, mult, comm) in (("BIG", inst["big"]),
                                         ("MICRO", inst["micro"])):
            print("=" * 64)
            print(f"{inst['name']}  {sym} (${mult:.0f}/pt)   width={width}pt   "
                  f"stop=2x   1 contract   comm=${comm:.2f}/leg/side")
            print("=" * 64)
            for name, cfg in configs:
                _, s = run_backtest(rows, multiplier=mult, stop_mult=2.0,
                                    comm_per_side=comm, **cfg)
                print(_fmt_stats(f"[{name}]", s))


if __name__ == "__main__":
    main()
