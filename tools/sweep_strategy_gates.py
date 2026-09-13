#!/usr/bin/env python
"""Reproduce the Black-Scholes sweep behind the Strategy Finder's gate profiles.

`options-scanner/strategy_scoring.py:_TYPE_PROFILE` maps each structure the
Strategy Finder added on 2026-09-13 (straddles/strangles, butterflies/condors,
calendars/diagonals, and the three share structures) to a gate profile, and its
comments -- plus the Scoring table in
`docs/plans/2026-09-13-strategy-finder-all-structures-design.md` -- quote what
each structure measures against that profile's bars: the short straddle and the
covered call failing, the iron butterfly judged as the long butterfly it is by
put-call parity. This script is that measurement, so the claims are re-runnable
rather than remembered (the repo has twice been burned by a comment asserting a
number nobody could re-run -- see `tools/sweep_naked_capeff.py`).

PURE: a synthetic Black-Scholes chain in memory (analytic delta, gamma, theta
and vega, so the vega-sign fit and liquidity scores behave as on a real chain),
then the REAL `strategy_scanner` builders and the REAL
`strategy_scoring.score_strategy`. No Schwab call, no SQLite, no live DB, no
network. Not a test -- deliberately outside pytest's `test_*` collection;
`tools/tests/test_sweep_strategy_gates.py` pins that it still reaches every
structure and still reproduces the two cuts.

    python tools/sweep_strategy_gates.py
    python tools/sweep_strategy_gates.py --step 5
    python tools/sweep_strategy_gates.py --iv 0.20 --days 7,14,30,45

For each front DTE in `--days` the chain lists two expiries, front and
front + 28 (so the calendar/diagonal builders find a back month), strikes
spot +/- 40% on a `--step` ladder. All four builders run with the Finder's
default short-delta bands (put -0.20..-0.10, call 0.10..0.20) over a 0 to
front + 30 DTE window, and each candidate is scored against a NEUTRAL view
(`direction neutral, conviction 0.1, vol_regime mid`) with
`em_1sd = spot * iv * sqrt(front / 365)`.

Columns:
    type      the normalized signal type (the `_TYPE_PROFILE` key)
    dte       the candidate's FRONT-leg DTE (a share leg never expires)
    legs      L/S, "2x" when a leg is not one contract, strike, C/P; `L SH`
              is a 100-share lot. Expiries are not printed -- a calendar's
              or diagonal's second leg is the back month
    net       +debit / -credit, per-contract dollars
    maxP      max profit, per contract, net of round-trip commission
              ("unb" = unbounded)
    maxL      max loss, per contract, commission included
    R:R       max_profit / max_loss ("-" when undefined)
    PoP       probability of profit, percent
    profile   `strategy_scoring.gate_profile` -- which GATE_BARS row applies
    reward    `strategy_scoring._reward_metric(sig, profile)`: R:R for most
              profiles, ANNUALISED capital efficiency (per year) for NAKED,
              "inf" for an unbounded long's auto-pass, "-" = unjudgeable (fails)
    score     composite_score (capped at GATE_FAIL_CAP on a gate failure)
    grade     Strong / Good / Marginal / Weak, with the failing gates for Weak

⚠ THE FIGURES MOVE WITH `--step`, WING WIDTH AND IV. Wings sit at the listed
symmetric distance nearest half the expected move, so the ladder decides the
wing; a condor's R:R and PoP in particular are ladder-dependent. Quote every
number from this script WITH its parameters (spot, iv, step, front DTE) -- a
bare figure is exactly the stale-comment failure this script exists to end.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from repo_paths import OPTIONS_SCANNER  # noqa: E402

sys.path.insert(0, str(OPTIONS_SCANNER))
import options_calculator as oc      # noqa: E402
import strategy_scanner as ss        # noqa: E402
import strategy_scoring as sc        # noqa: E402

BUILDERS = ("build_straddles_strangles", "build_butterflies_condors",
            "build_calendars", "build_stock_structures")
# The builders that take the caller's short-delta bands (the other two do not).
BANDED = ("build_straddles_strangles", "build_stock_structures")
PUT_BAND, CALL_BAND = (-0.20, -0.10), (0.10, 0.20)   # the Finder's defaults
NEUTRAL_VIEW = {"direction": "neutral", "conviction": 0.1, "vol_regime": "mid"}
BACK_OFFSET = 28
SPAN = 0.40


def chain(spot, iv, days, step):
    """A Schwab-shaped chain: one expiration per entry in `days`, strikes
    spot +/- SPAN on a `step` ladder. Marks are Black-Scholes at
    `RISK_FREE_RATE` floored at 0.01, bid/ask +/-2% of mark; greeks analytic.
    Liquidity is generous on purpose -- this sweep is about the reward and PoP
    bars, and a liquidity failure would mask them.
    """
    r = oc.RISK_FREE_RATE
    out = {"underlyingPrice": spot, "callExpDateMap": {}, "putExpDateMap": {}}
    n = int(spot * SPAN / step)
    for d in days:
        key = f"{(dt.date.today() + dt.timedelta(days=d)).isoformat()}:{d}"
        T = max(d, 0.5) / 365.0
        for kind, m in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
            side = {}
            for i in range(-n, n + 1):
                K = round(spot + i * step, 2)
                if K <= 0:
                    continue
                mark = round(max(oc.bs_price(spot, K, T, r, iv, kind), 0.01), 2)
                side[f"{K}"] = [{
                    "delta": round(oc.bs_delta(spot, K, T, r, iv, kind), 4),
                    "mark": mark,
                    "bid": round(mark * 0.98, 2), "ask": round(mark * 1.02, 2),
                    "theta": round(float(oc.bs_theta(spot, K, T, r, iv, kind)), 4),
                    "vega": round(float(oc.bs_vega(spot, K, T, r, iv, kind)), 4),
                    "gamma": round(float(oc.bs_gamma(spot, K, T, r, iv, kind)), 5),
                    "volatility": iv * 100,
                    "totalVolume": 500, "openInterest": 2000}]
            out[m][key] = side
    return out


def _legs_text(legs):
    parts = []
    for l in legs:
        side = "L" if l["side"] == "long" else "S"
        qty = int(l.get("qty", 1) or 1)
        q = f"{qty}x" if qty != 1 else ""
        if l.get("kind") == oc.STOCK_KIND:
            parts.append(f"{side} {q}SH")
        else:
            k = l["strike"]
            ks = f"{k:g}"
            parts.append(f"{side}{q}{ks}{'C' if l['kind'] == 'call' else 'P'}")
    return " ".join(parts)


def rows(spot, iv, front_days, step):
    """One scored row per candidate the four builders emit at this front DTE."""
    c = chain(spot, iv, (front_days, front_days + BACK_OFFSET), step)
    em_1sd = spot * iv * math.sqrt(front_days / 365.0)
    for name in BUILDERS:
        kw = {"put_band": PUT_BAND, "call_band": CALL_BAND} if name in BANDED else {}
        for sig in getattr(ss, name)(c, "SWEEP", spot, iv, 0, front_days + 30, **kw):
            profile = sc.gate_profile(sig)
            # The PRODUCTION expression, called rather than restated.
            reward = sc._reward_metric(dict(sig), profile)
            scored = sc.score_strategy(dict(sig), NEUTRAL_VIEW, iv, em_1sd)
            net = (sig["net_debit"] if sig.get("net_debit") is not None
                   else -(sig.get("net_credit") or 0.0))
            yield {
                "front": front_days, "type": sig["type"], "dte": sig["dte"],
                "legs": _legs_text(sig["legs"]), "net": net,
                "max_profit": sig.get("max_profit"), "max_loss": sig.get("max_loss"),
                "rr": sig.get("rr"), "pop": sig.get("pop_pct"),
                "profile": profile, "reward": reward,
                "score": scored["composite_score"], "grade": scored["grade"],
                "grade_reason": scored["grade_reason"],
            }


def _num(v, fmt):
    if v is None:
        return "-"
    if isinstance(v, float) and math.isinf(v):
        return "inf"
    return format(v, fmt)


def _fmt(all_rows, spot, iv, step):
    out = [f"Strategy Finder gate sweep -- spot {spot:g}, iv {iv:g}, step {step:g}, "
           f"back = front + {BACK_OFFSET}, bands put {PUT_BAND} call {CALL_BAND}",
           ""]
    head = (f"{'type':<16}{'dte':>4}  {'legs':<30}{'net':>9}{'maxP':>9}{'maxL':>10}"
            f"{'R:R':>7}{'PoP':>6}  {'profile':<8}{'reward':>7}{'score':>7}  grade")
    front = None
    for r in all_rows:
        if r["front"] != front:
            front = r["front"]
            out += [f"-- front {front} DTE (em_1sd {spot * iv * math.sqrt(front / 365):.2f})",
                    head]
        grade = r["grade"] if r["grade"] != "Weak" else f"Weak ({r['grade_reason']})"
        maxp = "unb" if r["max_profit"] is None else _num(r["max_profit"], ".2f")
        out.append(f"{r['type']:<16}{r['dte']:>4}  {r['legs']:<30}{r['net']:>9.2f}"
                   f"{maxp:>9}{_num(r['max_loss'], '.2f'):>10}"
                   f"{_num(r['rr'], '.2f'):>7}{_num(r['pop'], '.1f'):>6}  "
                   f"{r['profile']:<8}{_num(r['reward'], '.2f'):>7}"
                   f"{r['score']:>7.1f}  {grade}")
    out += ["", "bars (min): " + "; ".join(
        f"{p} " + " ".join(f"{k} {v}" for k, v in b["min"].items())
        for p, b in sc.GATE_BARS.items())]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spot", type=float, default=100.0)
    ap.add_argument("--iv", type=float, default=0.28)
    ap.add_argument("--days", default="14,30,45",
                    help="comma-separated FRONT DTEs (default 14,30,45)")
    ap.add_argument("--step", type=float, default=2.5, help="strike ladder step")
    a = ap.parse_args(argv)
    fronts = [int(x) for x in a.days.split(",") if x.strip()]
    all_rows = [r for d in fronts for r in rows(a.spot, a.iv, d, a.step)]
    print(_fmt(all_rows, a.spot, a.iv, a.step))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
