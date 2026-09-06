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

It also carries the measurement behind `strategy_scoring.MIN_ANNUALISE_DTE`
(Task 2.7 -- `--floors`), the floor on the annualisation DIVISOR that caps the
short-end amplification annualising introduced. Both halves run off the same
scorers, so a bar tune and a floor tune are argued from one script.

PURE: a synthetic Black-Scholes chain in memory, then the real
`strategy_scanner.build_directional` + `strategy_scoring.score_strategy`. No
Schwab call, no SQLite, no live DB, no network. Not a test -- deliberately
outside pytest's `test_*` collection. `--floors` additionally imports the
options-scanner test module to reuse its `fake_client` fixture; that fixture is
a pure in-memory stub too (it monkeypatches the regime read away), so the
no-network / no-DB property holds.

    python tools/sweep_naked_capeff.py
    python tools/sweep_naked_capeff.py --spot 100 --iv 0.28 --max-dte 60 --rows
    python tools/sweep_naked_capeff.py --floors
    python tools/sweep_naked_capeff.py --floors --floor-candidates 3,5,7,10,14

Read the bar half as: annualised capeff is separated by CAPITAL BASIS (a short
call is capitalised at the 20%-of-spot margin proxy, a short put at its true
stock-to-zero max loss), not by horizon -- so raising the bar to discourage
short-dated naked shorts cuts on the wrong axis.

Read the floor half as: the floor is the ONLY lever that acts on the horizon
axis, and it acts on the divisor alone -- `dte <= 0` still returns None, since a
zero or unknown horizon is unjudgeable rather than merely short.
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
                # The PRODUCTION expression, called rather than restated -- so
                # the `MIN_ANNUALISE_DTE` floor cannot be present in the gate and
                # absent from the sweep that argues for it.
                "annualised": sc._reward_metric(dict(sig), "NAKED"),
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


#############################################
# Task 2.7 -- the MIN_ANNUALISE_DTE floor
#############################################

FLOOR_CANDIDATES = (3, 5, 7, 10, 14)
FLOOR_DTES = (1, 3, 7, 14, 21, 35, 45, 60)


def _floored(per_trade, dte, floor):
    """The production expression, isolated: 365 / max(dte, floor)."""
    return per_trade * (365.0 / max(dte, floor))


def floor_capeff(rows, candidates=FLOOR_CANDIDATES, dtes=FLOOR_DTES):
    """{stype: {floor: {dte: annualised capeff}}} over the Black-Scholes sweep.

    `floor == 0` is the UNFLOORED reference row -- what shipped in Task 2.6 --
    since `max(dte, 0)` is `dte` for every dte the metric accepts.
    """
    per_trade = {(r["type"], r["dte"]): r["per_trade"] for r in rows}
    out = {}
    for stype in NAKED:
        out[stype] = {}
        for floor in (0, *candidates):
            out[stype][floor] = {
                d: _floored(per_trade[(stype, d)], d, floor)
                for d in dtes if (stype, d) in per_trade}
    return out


def _fake_client_rows(floor, uncut=False):
    """Drive the repo's OWN `fake_client` fixture through the production cut.

    This is the measurement that matters: the Black-Scholes table above says
    what the metric returns, but only the end-to-end scan says which rows a user
    actually sees, because the cut compares a COMPOSITE the reward gate merely
    caps.

    Reuses `options-scanner/tests/test_scanner_engine.py::fake_client` rather
    than restating it -- a second copy of a fixture is a second thing to drift.
    `__wrapped__` is the undecorated generator/function pytest wraps.

    ⚠ `run_full_scan` calls `signal_recorder.record_signals`, which defaults to
    the LIVE `data/signals.db`. The suite is protected by the repo-root
    sqlite3 guard plus the conftest redirect; a script run outside pytest has
    neither, so the recorder is stubbed to a no-op here. Do not remove that stub
    -- writing synthetic SPY/QQQ rows into the production store is the exact
    2026-07-16 incident the root CLAUDE.md documents.
    """
    import pytest                                    # noqa: PLC0415  (tool-only dep)

    sys.path.insert(0, str(OPTIONS_SCANNER / "tests"))
    import test_scanner_engine as tse                # noqa: E402, PLC0415
    import scanner_engine                            # noqa: PLC0415
    import signal_recorder                           # noqa: PLC0415

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(signal_recorder, "record_signals",
                   lambda *a, **k: None)             # never touch the live DB
        mp.setattr(sc, "MIN_ANNUALISE_DTE", floor)
        if uncut:
            mp.setattr(scanner_engine, "SINGLE_LEG_MIN_SCORE", 0.0)
            mp.setattr(scanner_engine, "SINGLE_LEG_EXCLUDED_GRADES", ())
        client = tse.fake_client.__wrapped__(mp)
        sigs = scanner_engine.run_full_scan(client, symbols=["SPY", "QQQ"])
        return [{"symbol": s["symbol"], "type": s["type"], "dte": s["dte"],
                 "max_profit": s.get("max_profit"), "capital": s.get("capital"),
                 "composite": s.get("composite_score"), "grade": s.get("grade")}
                for s in sigs["signals_directional"]]


def scan_windows():
    """The production DTE windows, READ FROM THE SOURCE, not restated here.

    The floor is only defensible if something other than taste bounds it, and
    the repo already states the boundary: `scanner_engine.run_full_scan` scans a
    0-DTE window and a SWING window, and `options_svc`'s `_SWING_DEFAULTS` opens
    the Strategy Finder at the same swing floor. A floor at or below
    `swing_min` rescales ONLY horizons the 0-DTE window owns; above it, the
    floor starts discounting genuine swing candidates, which are not the problem
    this task exists to fix.

    They are local names inside `run_full_scan`, so they are scraped rather than
    imported -- and scraped rather than copied, so moving the window in
    `scanner_engine` moves this argument with it instead of silently stranding
    it. Returns None if the scrape stops matching, so a stale number can never
    be printed as a live one.
    """
    import re                                         # noqa: PLC0415

    src = (OPTIONS_SCANNER / "scanner_engine.py").read_text(encoding="utf-8",
                                                            errors="replace")
    found = {}
    for name in ("zerodte_min_dte", "zerodte_max_dte",
                 "swing_min_dte", "swing_max_dte"):
        m = re.search(rf"^\s*{name}\s*=\s*(\d+)\s*$", src, re.M)
        if m is None:
            return None
        found[name] = int(m.group(1))
    return found


def _fmt_floors(rows, candidates=FLOOR_CANDIDATES, dtes=FLOOR_DTES):
    bar = sc.GATE_BARS["NAKED"]["min"]["capeff"]
    table = floor_capeff(rows, candidates, dtes)
    out = ["NAKED annualisation-horizon floor -- MIN_ANNUALISE_DTE candidates",
           f"metric: (max_profit / capital) x 365 / max(dte, F);  min bar "
           f"{bar} /yr;  shipping MIN_ANNUALISE_DTE = {sc.MIN_ANNUALISE_DTE}",
           "F=0 is the UNFLOORED Task 2.6 reference. '*' clears the min bar.",
           ""]

    for stype in NAKED:
        out.append(f"(a) {stype} annualised capeff /yr by DTE")
        out.append("   F  " + "".join(f"{d:>10}" for d in dtes))
        for floor, by_dte in table[stype].items():
            cells = "".join(
                f"{by_dte[d]:>9.2f}" + ("*" if by_dte[d] >= bar else " ")
                for d in dtes if d in by_dte)
            out.append(f"{floor:>4}  " + cells)
        out.append("")

    out.append("(b) 1-DTE amplification, the thing the floor caps, and the "
               "per-trade")
    out.append("    return a 1-DTE naked short must earn to clear the bar")
    for floor in (0, *candidates):
        mult = 365.0 / max(1, floor)
        out.append(f"   F={floor:<3} multiplier {mult:>6.1f}x   "
                   f"needs {bar / mult * 100:>6.3f}% per trade")
    out.append("")

    win = scan_windows()
    out.append("(c) which production DTE windows each candidate reaches into")
    if win is None:
        out.append("    !! could not read the windows out of scanner_engine.py "
                   "-- the scrape stopped matching; fix it rather than trusting "
                   "a remembered number")
    else:
        out.append(f"    scanner_engine windows: 0-DTE "
                   f"{win['zerodte_min_dte']}-{win['zerodte_max_dte']}, SWING "
                   f"{win['swing_min_dte']}-{win['swing_max_dte']}")
        for floor in candidates:
            rescaled = list(range(1, floor))
            into = [d for d in rescaled if d >= win["swing_min_dte"]]
            verdict = ("0-DTE window only" if not into
                       else f"reaches SWING at dte {into}")
            out.append(f"   F={floor:<3} rescales dte {rescaled} -- {verdict}")
    out.append("")

    out.append("(d) end-to-end: the repo's own `fake_client` fixture through the")
    out.append("    PRODUCTION cut (scanner_engine.run_full_scan, SINGLE_LEG_*")
    out.append("    bars in force) -- signals_directional, per candidate")
    baseline = None
    for floor in (0, *candidates):
        emitted = _fake_client_rows(floor)
        key = [(r["symbol"], r["type"], r["dte"], r["composite"]) for r in emitted]
        label = "F=0 (unfloored)" if floor == 0 else f"F={floor}"
        if baseline is None:
            baseline = key
            delta = ""
        else:
            delta = "  (identical to F=0)" if key == baseline else "  CHANGED vs F=0"
        if not emitted:
            out.append(f"   {label:<16} -- nothing emitted{delta}")
            continue
        out.append(f"   {label:<16} {len(emitted)} row(s){delta}")
        for r in sorted(emitted, key=lambda r: -(r["composite"] or 0)):
            out.append(f"      {r['symbol']:<4} {r['type']:<11} dte={r['dte']:<3} "
                       f"comp={r['composite']:>5.1f}  {r['grade']}")
    out.append("")

    out.append("(e) why (d) barely moves: the fixture's naked shorts, uncut, and")
    out.append("    the floor each would need before its reward gate fails")
    for r in sorted(_fake_client_rows(0, uncut=True),
                    key=lambda r: (r["symbol"], r["type"], r["dte"])):
        if r["type"] not in NAKED or not r["capital"]:
            continue
        pt = r["max_profit"] / r["capital"]
        # floored capeff < bar  <=>  F > per_trade * 365 / bar
        need = pt * 365.0 / bar
        out.append(f"      {r['symbol']:<4} {r['type']:<11} dte={r['dte']:<3} "
                   f"per-trade {pt * 100:>6.2f}%   cut only once F > {need:>6.1f}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spot", type=float, default=100.0)
    ap.add_argument("--iv", type=float, default=0.28)
    ap.add_argument("--max-dte", type=int, default=60)
    ap.add_argument("--rows", action="store_true",
                    help="print every swept row, not just the summary")
    ap.add_argument("--floors", action="store_true",
                    help="sweep MIN_ANNUALISE_DTE candidates instead of the bars")
    ap.add_argument("--floor-candidates", default=None,
                    help="comma-separated floors to try (default 3,5,7,10,14)")
    a = ap.parse_args(argv)
    rows = sweep(a.spot, a.iv, a.max_dte)
    if a.floors:
        cands = (tuple(int(x) for x in a.floor_candidates.split(","))
                 if a.floor_candidates else FLOOR_CANDIDATES)
        print(_fmt_floors(rows, cands))
    else:
        print(_fmt(rows, a.rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
