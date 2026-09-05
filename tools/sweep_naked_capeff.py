#!/usr/bin/env python
"""Reproduce the Black-Scholes sweep behind the NAKED gate bars.

`options-scanner/strategy_scoring.py:GATE_BARS["NAKED"]` carries five
load-bearing numbers in its comment -- the annualised capital-efficiency ranges
for SHORT_CALL and SHORT_PUT, the ratio between them, the 1-DTE composite, and
the naked-short composite ceiling. They are the argument for leaving the
0.10/0.20 bars unchanged when the metric was annualised (Task 2.6). This repo
has twice been burned by a comment asserting a measurement nobody could re-run
(the ADX characterization test, the C7 "single-source r" claim), so the sweep
lives here rather than in a session transcript.

PURE: a synthetic Black-Scholes chain in memory, then the real
`strategy_scanner.build_directional` + `strategy_scoring.score_strategy`. No
Schwab call, no SQLite, no live DB, no network. Not a test -- deliberately
outside pytest's `test_*` collection.

    python tools/sweep_naked_capeff.py
    python tools/sweep_naked_capeff.py --spot 100 --iv 0.28 --max-dte 60 --rows

Read the output as: annualised capeff is separated by CAPITAL BASIS (a short
call is capitalised at the 20%-of-spot margin proxy, a short put at its true
stock-to-zero max loss), not by horizon -- so raising the bar to discourage
short-dated naked shorts cuts on the wrong axis.
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

NAKED = ("SHORT_CALL", "SHORT_PUT")


def _chain(spot, iv, dte, width=0.5, span=0.35):
    """A synthetic Schwab-shaped chain: one expiration, a fine strike ladder.

    Marks are Black-Scholes mid at `RISK_FREE_RATE`; deltas are the analytic
    ones, so `nearest_by_delta` lands on the same 0.28-delta short the scanner
    would pick off a real chain. Liquidity is set generously on purpose -- this
    sweep is about the REWARD bar, and a liquidity failure would mask it.
    """
    exp = (dt.date.today() + dt.timedelta(days=dte)).isoformat()
    T = max(dte, 0.5) / 365.0
    lo, hi = spot * (1 - span), spot * (1 + span)
    strikes = [round(lo + i * width, 2)
               for i in range(int((hi - lo) / width) + 1)]

    def side(kind):
        out = {}
        for k in strikes:
            price = oc.bs_price(spot, k, T, oc.RISK_FREE_RATE, iv, kind)
            delta = oc.bs_delta(spot, k, T, oc.RISK_FREE_RATE, iv, kind)
            if price is None or delta is None or price <= 0.01:
                continue
            mark = round(price, 2)
            out[f"{k}"] = [{"delta": round(delta, 4), "mark": mark,
                            "bid": round(mark - 0.02, 2), "ask": round(mark + 0.02, 2),
                            "theta": 0.0, "vega": 0.0, "gamma": 0.0,
                            "volatility": iv * 100,
                            "totalVolume": 5000, "openInterest": 12000}]
        return out

    return {"underlyingPrice": spot,
            "callExpDateMap": {f"{exp}:{dte}": side("call")},
            "putExpDateMap": {f"{exp}:{dte}": side("put")}}


def sweep(spot=100.0, iv=0.28, max_dte=60):
    """One scored naked short per (type, dte). Returns a list of row dicts."""
    view = sc.infer_market_view({}, {})
    daily_em = spot * iv / math.sqrt(365.0)      # iv_analysis.expiry_daily_em
    rows = []
    for dte in range(1, max_dte + 1):
        chain = _chain(spot, iv, dte)
        sigs = ss.build_directional(chain, "SWEEP", spot, iv, dte, dte)
        # scanner_engine scores each DTE window against ITS OWN em_1sd.
        em_1sd = daily_em * math.sqrt(max(dte, 1))
        for sig in sigs:
            if sig["type"] not in NAKED:
                continue
            mp, cap = sig["max_profit"], sig["capital"]
            scored = sc.score_strategy(dict(sig), view, iv, em_1sd)
            rows.append({
                "type": sig["type"], "dte": dte, "strike": sig["legs"][0]["strike"],
                "credit": mp, "capital": cap,
                "per_trade": mp / cap,
                "annualised": (mp / cap) * (365.0 / dte),
                "pop": sig["pop_pct"],
                "composite": scored["composite_score"], "grade": scored["grade"],
            })
    return rows


def _fmt(rows, show_rows):
    bars = sc.GATE_BARS["NAKED"]
    out = ["NAKED annualised capital efficiency -- Black-Scholes sweep",
           f"bars: min capeff {bars['min']['capeff']} /yr, "
           f"excellent {bars['excellent']['capeff']} /yr", ""]
    if show_rows:
        out.append(f"{'type':<11}{'dte':>4}{'strike':>8}{'credit':>9}"
                   f"{'capital':>10}{'/trade':>9}{'/yr':>8}{'pop':>7}"
                   f"{'comp':>7}  grade")
        for r in rows:
            out.append(f"{r['type']:<11}{r['dte']:>4}{r['strike']:>8.1f}"
                       f"{r['credit']:>9.2f}{r['capital']:>10.2f}"
                       f"{r['per_trade']:>9.4f}{r['annualised']:>8.2f}"
                       f"{r['pop']:>7.1f}{r['composite']:>7.1f}  {r['grade']}")
        out.append("")

    spans = {}
    for t in NAKED:
        ann = [r["annualised"] for r in rows if r["type"] == t]
        if ann:
            spans[t] = (min(ann), max(ann))
            out.append(f"{t:<11} annualised capeff {min(ann):.2f} - {max(ann):.2f} /yr")
    if len(spans) == 2:
        c, p = spans["SHORT_CALL"], spans["SHORT_PUT"]
        out.append(f"{'':<11} SHORT_CALL / SHORT_PUT ratio "
                   f"{c[0] / p[0]:.1f}x (low) {c[1] / p[1]:.1f}x (high) "
                   "-- the capital basis, not the horizon")

    one = [r for r in rows if r["dte"] == 1]
    if one:
        out.append("")
        out.append(f"1-DTE composite: "
                   + ", ".join(f"{r['type']} {r['composite']:.1f} ({r['grade']})"
                               for r in one)
                   + "  -- vs the 50.0 publish floor both callers apply")
    if rows:
        top = max(rows, key=lambda r: r["composite"])
        out.append(f"naked composite ceiling over the sweep: {top['composite']:.1f} "
                   f"({top['type']} @ {top['dte']} DTE) -- "
                   f"STRONG_MIN is {sc.STRONG_MIN}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spot", type=float, default=100.0)
    ap.add_argument("--iv", type=float, default=0.28)
    ap.add_argument("--max-dte", type=int, default=60)
    ap.add_argument("--rows", action="store_true",
                    help="print every swept row, not just the summary")
    a = ap.parse_args(argv)
    print(_fmt(sweep(a.spot, a.iv, a.max_dte), a.rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
