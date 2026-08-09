"""Measure how a delta-notional flow alert would fire, before building one.

WHY THIS EXISTS
---------------
The three live flow-alert detectors all measure DOLLARS, not EXPOSURE: the
crossover compares call vs put premium, unusual activity gates on
``mark x volume x 100 >= premium_floor`` ($5M), and the gamma flip is a price
level. None measures how much DIRECTIONAL RISK changed hands.

A one-off measurement on a real SPY chain (2026-08-09) found 42 contracts
carrying >= $100M of delta notional that fell BELOW the $5M premium floor —
$12.71B of exposure the current detector cannot see, all of it cheap OTM
contracts traded in size. This script generalizes that measurement across the
whole collected universe so a threshold can be chosen from evidence.

The real risk is not that a delta detector wouldn't fire. It is that it fires
200x/day on SPY and drowns the flow-alert channel that currently works. That is
what this measures.

WHY A SINGLE POST-CLOSE SNAPSHOT IS SUFFICIENT
----------------------------------------------
``totalVolume`` is CUMULATIVE for the session, and the UOA-style dedup fires at
most once per contract per day (``handlers.run_flow_alerts`` uses the cooldown
map as a date-scoped seen-set, valid because vol/OI is monotonic). So the number
of alerts a threshold would have produced today EQUALS the number of contracts
finishing the day above it — no intraday polling required.

One bias to keep in mind when reading the output: delta is the CLOSING delta,
not the delta at the moment the contract crossed the threshold. For calibrating
an order-of-magnitude threshold that is acceptable; for anything finer it is not.

USAGE
-----
Run AFTER the close (equity options 15:00 CT, index options 15:15 CT):

    .venv\\Scripts\\python.exe tools\\flow_delta_instrumentation.py

Writes a markdown report + a CSV of qualifying contracts under
``options-scanner/data/flow_delta_instrumentation/<date>/``. Needs the proxy up
(dev borrows prod's on :8100). Read-only: fetches chains, writes report files,
touches no cache key, no DB, and no live alert path.
"""
from __future__ import annotations

import csv
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER, PROXY_URL, SCHWAB_PROXY  # noqa: E402

for _p in (str(SCHWAB_PROXY), str(OPTIONS_SCANNER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proxy_client import SchwabPyProxyClient  # noqa: E402

CT = ZoneInfo("America/Chicago")

# Mirror the LIVE detector's gates so the baseline column is a true baseline.
# (config/flow_alerts.toml [uoa] — read at runtime rather than hardcoded.)
CHAIN_FORWARD_DAYS = 7      # gex_collector.poll_once fetches today -> today+7d
FETCH_WORKERS = 6           # gex_collector.POLL_FETCH_WORKERS

# Candidate ABSOLUTE thresholds ($ of delta notional).
ABS_THRESHOLDS = [50e6, 100e6, 250e6, 500e6, 1e9]
# Candidate RELATIVE thresholds (share of the symbol's OWN gross delta notional
# that day). Tests the "a flat floor biases toward mega-caps" hypothesis — the
# same flaw the flat $5M premium floor has today.
REL_THRESHOLDS = [0.02, 0.05, 0.10, 0.20]
# Deep-ITM contracts (delta ~ 1) are usually stock replacement, assignment or
# roll mechanics rather than directional bets. Measured both ways.
DELTA_BAND = (0.05, 0.85)


def flow_symbols():
    """The live flow-alert universe: collected symbols minus $VIX."""
    import gex_collector as gc
    return [s for s in gc.collection_symbols() if s != "$VIX"]


def uoa_cfg():
    from services.options_svc import flow_alerts
    return flow_alerts.load_thresholds()


def fetch_chain(client, symbol, today):
    """One symbol's chain over the same window the collector uses. None on failure."""
    try:
        r = client.get_option_chain(
            symbol,
            contract_type=client.Options.ContractType.ALL,
            from_date=today,
            to_date=today + timedelta(days=CHAIN_FORWARD_DAYS),
        )
        return r.json() if getattr(r, "status_code", 500) == 200 else None
    except Exception as e:  # noqa: BLE001 — one bad symbol can't kill the run
        print(f"  ! {symbol}: {e}")
        return None


def contracts_from(symbol, chain):
    """Flatten a chain into per-contract measurement rows."""
    spot = (chain or {}).get("underlyingPrice")
    if not isinstance(spot, (int, float)) or spot <= 0:
        return []
    out = []
    for mapkey, side in (("callExpDateMap", "call"), ("putExpDateMap", "put")):
        for ek, strike_map in ((chain.get(mapkey) or {})).items():
            exp = str(ek).split(":")[0]
            try:
                dte = int(str(ek).split(":")[1])
            except (IndexError, ValueError):
                dte = None
            for ks, cs in (strike_map or {}).items():
                try:
                    strike = float(ks)
                except (TypeError, ValueError):
                    continue
                for c in cs or []:
                    vol = c.get("totalVolume") or 0
                    delta = c.get("delta")
                    mark = c.get("mark") or 0
                    oi = c.get("openInterest") or 0
                    if not vol or not isinstance(delta, (int, float)):
                        continue
                    out.append({
                        "symbol": symbol, "side": side, "strike": strike,
                        "expiry": exp, "dte": dte, "spot": spot,
                        "volume": int(vol), "oi": int(oi), "delta": float(delta),
                        "mark": float(mark),
                        "premium": mark * vol * 100,
                        "delta_notional": abs(delta) * vol * 100 * spot,
                        "signed_delta_notional": delta * vol * 100 * spot,
                    })
    return out


def passes_uoa(r, cfg):
    """The CURRENT detector's rule — the baseline to compare against."""
    u = (cfg or {}).get("uoa", {})
    if r["oi"] <= 0 or r["volume"] < u.get("vol_floor", 500):
        return False
    if r["volume"] / r["oi"] < u.get("k", 3.0):
        return False
    return r["premium"] >= u.get("premium_floor", 5_000_000)


def in_band(r):
    lo, hi = DELTA_BAND
    return lo <= abs(r["delta"]) <= hi


def _fmt_money(v):
    if abs(v) >= 1e9:
        return f"${v/1e9:,.2f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:,.0f}M"
    return f"${v/1e3:,.0f}k"


def build_report(rows, cfg, symbols, failed):
    """The markdown report. Pure over the collected rows."""
    L = []
    now = datetime.now(CT)
    L.append(f"# Delta-notional flow-alert instrumentation — {now:%Y-%m-%d %H:%M} CT\n")
    L.append(f"Universe: **{len(symbols)}** symbols "
             f"({len(symbols) - len(failed)} fetched, {len(failed)} failed"
             + (f": {', '.join(failed)}" if failed else "") + ")\n")
    L.append(f"Contracts with volume + delta: **{len(rows):,}**\n")

    base = [r for r in rows if passes_uoa(r, cfg)]
    u = cfg.get("uoa", {})
    L.append("\n## Baseline — what fires today\n")
    L.append(f"Current UOA rule (vol >= {u.get('vol_floor')}, vol/OI >= {u.get('k')}, "
             f"premium >= {_fmt_money(u.get('premium_floor', 5e6))}): "
             f"**{len(base)} alerts** across **{len({r['symbol'] for r in base})}** symbols.\n")
    if base:
        by_sym = {}
        for r in base:
            by_sym[r["symbol"]] = by_sym.get(r["symbol"], 0) + 1
        top = sorted(by_sym.items(), key=lambda kv: -kv[1])[:10]
        L.append("\nMost alerts: " + ", ".join(f"{s} {n}" for s, n in top) + "\n")

    # Per-symbol gross delta notional -> the relative thresholds.
    gross = {}
    for r in rows:
        gross[r["symbol"]] = gross.get(r["symbol"], 0.0) + r["delta_notional"]

    L.append("\n## Candidate ABSOLUTE thresholds\n")
    L.append("`new` = would alert on delta but does NOT already alert on premium — "
             "the contracts a delta detector actually adds.\n")
    L.append("\n| threshold | alerts | new | symbols | in delta band | top symbol |")
    L.append("|---|---:|---:|---:|---:|---|")
    for t in ABS_THRESHOLDS:
        q = [r for r in rows if r["delta_notional"] >= t]
        new = [r for r in q if not passes_uoa(r, cfg)]
        banded = [r for r in q if in_band(r)]
        by_sym = {}
        for r in q:
            by_sym[r["symbol"]] = by_sym.get(r["symbol"], 0) + 1
        top = max(by_sym.items(), key=lambda kv: kv[1]) if by_sym else ("—", 0)
        L.append(f"| {_fmt_money(t)} | {len(q)} | {len(new)} | {len(by_sym)} | "
                 f"{len(banded)} | {top[0]} ({top[1]}) |")

    L.append("\n## Candidate RELATIVE thresholds (share of the symbol's own gross)\n")
    L.append("Does a relative trigger spread alerts across the universe instead of "
             "concentrating them in the index/mega-cap names?\n")
    L.append("\n| threshold | alerts | new | symbols | top symbol |")
    L.append("|---|---:|---:|---:|---|")
    for t in REL_THRESHOLDS:
        q = [r for r in rows
             if gross.get(r["symbol"], 0) > 0
             and r["delta_notional"] / gross[r["symbol"]] >= t]
        new = [r for r in q if not passes_uoa(r, cfg)]
        by_sym = {}
        for r in q:
            by_sym[r["symbol"]] = by_sym.get(r["symbol"], 0) + 1
        top = max(by_sym.items(), key=lambda kv: kv[1]) if by_sym else ("—", 0)
        L.append(f"| {t:.0%} | {len(q)} | {len(new)} | {len(by_sym)} | "
                 f"{top[0]} ({top[1]}) |")

    L.append("\n## Per-symbol totals\n")
    L.append("\n| symbol | contracts | gross delta notional | net signed | net/gross | "
             "premium | UOA alerts |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    per = {}
    for r in rows:
        d = per.setdefault(r["symbol"], {"n": 0, "gross": 0.0, "net": 0.0, "prem": 0.0, "uoa": 0})
        d["n"] += 1
        d["gross"] += r["delta_notional"]
        d["net"] += r["signed_delta_notional"]
        d["prem"] += r["premium"]
        if passes_uoa(r, cfg):
            d["uoa"] += 1
    for sym, d in sorted(per.items(), key=lambda kv: -kv[1]["gross"]):
        ratio = (d["net"] / d["gross"]) if d["gross"] else 0.0
        L.append(f"| {sym} | {d['n']:,} | {_fmt_money(d['gross'])} | "
                 f"{_fmt_money(d['net'])} | {ratio:+.1%} | {_fmt_money(d['prem'])} | "
                 f"{d['uoa']} |")

    L.append("\n## Reading this\n")
    L.append("- **`new` is the whole point.** A threshold whose alerts are already "
             "caught by the premium floor adds nothing but duplicates.\n")
    L.append("- **Watch the `top symbol` column.** If one name owns most alerts at "
             "every absolute threshold, a flat floor repeats the bias it was meant "
             "to fix, and the relative table is the better trigger.\n")
    L.append("- **Delta is the CLOSING delta**, not the delta when the contract "
             "crossed. Fine for order-of-magnitude calibration, not for more.\n")
    L.append("- **Volume is unsigned** — no tape. These are contracts where exposure "
             "changed hands, not evidence of anyone's direction.\n")
    return "\n".join(L)


def main():
    today = datetime.now(CT).date()
    symbols = flow_symbols()
    cfg = uoa_cfg()
    print(f"Flow universe: {len(symbols)} symbols; proxy {PROXY_URL}")
    client = SchwabPyProxyClient(PROXY_URL)

    rows, failed = [], []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
        for sym, chain in zip(symbols, ex.map(lambda s: fetch_chain(client, s, today), symbols)):
            if not chain:
                failed.append(sym)
                continue
            got = contracts_from(sym, chain)
            rows.extend(got)
            print(f"  {sym:<6} {len(got):>5} contracts")

    out_dir = OPTIONS_SCANNER / "data" / "flow_delta_instrumentation" / str(today)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = build_report(rows, cfg, symbols, failed)
    (out_dir / "report.md").write_text(report, encoding="utf-8")

    # Raw rows for any threshold question the report didn't anticipate.
    if rows:
        with open(out_dir / "contracts.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    print(f"\nWrote {out_dir / 'report.md'}")
    print(f"Wrote {out_dir / 'contracts.csv'}  ({len(rows):,} rows)")
    print("\n" + report.split("## Candidate RELATIVE")[0])


if __name__ == "__main__":
    main()
