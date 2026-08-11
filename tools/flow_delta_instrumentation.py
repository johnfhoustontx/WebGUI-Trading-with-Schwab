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

WHAT A SINGLE POST-CLOSE SNAPSHOT CAN AND CANNOT TELL YOU
---------------------------------------------------------
``totalVolume`` is CUMULATIVE for the session, and the UOA-style dedup fires at
most once per contract per day (``handlers.run_flow_alerts`` uses the cooldown
map as a date-scoped seen-set, valid because vol/OI is monotonic). So for a rule
gated on VOLUME alone, the number of alerts today equals the number of contracts
finishing the day above the threshold, and no intraday polling is required.

That reasoning does NOT extend to any rule gated on price or greeks, and this
script's own reconciliation proves it. Premium is ``mark x volume x 100`` and
delta notional is ``|delta| x volume x 100 x spot``; mark and delta both move
all day and, for 0-DTE, collapse into the close. Measured 2026-08-10: 34 UOA
alerts genuinely fired that this script's model cannot reproduce, and ALL 34
ended the day under the $5M premium floor — median closing premium $0.45M,
32 of them 0-DTE marked at $0.01-$0.03. They were worth real money when they
alerted and worth nothing by the time we measured.

Consequences, which the reconciliation section quantifies per run:

  * the modelled baseline is approximate in BOTH directions -- it invents
    alerts that never fired (final-state contracts that only crossed late) and
    misses alerts that did (contracts that decayed below the floor);
  * every delta-notional figure here carries the same bias. Closing delta
    understates 0-DTE contracts that spent the session near the money, so the
    thresholds this script recommends are calibrated on a conservative view of
    exactly the contracts most likely to trip them.

Order-of-magnitude calibration survives this. Anything finer needs intraday
sampling -- which the live detector already does, and which is why the
reconciliation against ``cache:options:flow_alerts`` is the honest check.

USAGE
-----
Run AFTER the close (equity options 15:00 CT, index options 15:15 CT):

    .venv\\Scripts\\python.exe tools\\flow_delta_instrumentation.py
    .venv\\Scripts\\python.exe tools\\flow_delta_instrumentation.py --force

Skips with exit 0 and writes nothing on a weekend or NYSE holiday — its reports
feed threshold calibration, and a non-session distribution would drag that
calibration down (see ``is_trading_day``). ``--force`` measures anyway, for
ad-hoc exploration; do not use it for a report the calibration will consume.

Writes a markdown report + a CSV of qualifying contracts under
``options-scanner/data/flow_delta_instrumentation/<date>/``. Needs the proxy up
(dev borrows prod's on :8100).

Read-only, but no longer isolated: it READS ``cache:options:flow_alerts`` from
production's Redis db to reconcile its modelled baseline against the alerts
that actually fired (``--alerts-db N`` overrides the db). It still writes
nothing but report files, and touches no live alert path.
"""
from __future__ import annotations

import csv
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date, datetime, timedelta
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

# Schwab returns **-999.0** as a "no value" SENTINEL for delta on contracts it
# can't price (seen live 2026-08-09: 46 contracts across T/MU/IONQ/UAL). Taken at
# face value that is not a small error — one 2,088-lot contract manufactured
# $4.96B of phantom delta notional, and the sentinel rows together produced
# $18.3B, enough to rank AT&T the 4th-largest name in the universe. Any absolute
# threshold would fire on them EVERY day, on whatever illiquid contract happens
# to lack a quote. |delta| > 1 is impossible for a vanilla option, so that is the
# guard; a real detector needs this same check.
DELTA_MAX = 1.0

# ── Baseline reconciliation ────────────────────────────────────────────────
# The "baseline" column models the live UOA rule by applying it to every
# contract. That model is NOT the live detector, and the difference is large:
# on 2026-08-10 it claimed 119 UOA alerts while the channel actually carried 82.
# Reconciling against what really fired is what keeps the threshold numbers
# honest -- and it also supplies the real channel denominator, which is every
# alert type, not just UOA (that day: 92 crossover + 82 uoa + 1 gamma_flip).
FLOW_ALERTS_KEY = "cache:options:flow_alerts"
# PRODUCTION's Redis db, deliberately not repo_paths.MEMURAI_URL. Prod owns the
# always-on services that publish the channel; run from the dev checkout,
# MEMURAI_URL resolves to dev's own db (1), which holds no live alerts -- the
# reconciliation would then report "0 fired" and read as a total detector
# outage rather than "wrong database". --alerts-db overrides.
LIVE_ALERTS_DB = 0

# NYSE full-closure holidays 2026–2027, COPIED (not imported) from
# services/options_svc/scheduler.py. Importing that module would drag `compute`
# and `handlers` — the engine chain and the Redis bus — into a tool that
# deliberately touches neither; shared/notify/channels.py keeps its own copy for
# the same reason. Observed dates per NYSE (Sat->prior Fri, Sun->following Mon).
# **Update yearly**, alongside webgui/alerts.py _HOLIDAYS.
_HOLIDAYS = frozenset({
    # 2026
    _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16), _date(2026, 4, 3),
    _date(2026, 5, 25), _date(2026, 6, 19), _date(2026, 7, 3), _date(2026, 9, 7),
    _date(2026, 11, 26), _date(2026, 12, 25),
    # 2027
    _date(2027, 1, 1), _date(2027, 1, 18), _date(2027, 2, 15), _date(2027, 3, 26),
    _date(2027, 5, 31), _date(2027, 6, 18), _date(2027, 7, 5), _date(2027, 9, 6),
    _date(2027, 11, 25), _date(2027, 12, 24),
})


def is_trading_day(d) -> bool:
    """True on a weekday that is not an NYSE full-closure holiday.

    The gate matters because this script is now scheduled daily and its reports
    feed threshold calibration. Run on a holiday it would happily write a
    normal-looking session directory holding a near-empty distribution, and a
    calibration window that absorbed it would read the closure as a genuine
    quiet session. A weekend run is worse than empty: chains still return, but
    open interest has already settled to include the prior session's trades, so
    vol/OI is deflated throughout and index names can come back with OI 0 across
    the board — the exact artifact that invalidated the 2026-08-09 baseline."""
    return d.weekday() < 5 and d not in _HOLIDAYS


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
    """Flatten a chain into per-contract measurement rows.

    Returns ``(rows, n_sentinel_delta)`` — the dropped count is reported rather
    than silently swallowed (see DELTA_MAX)."""
    spot = (chain or {}).get("underlyingPrice")
    if not isinstance(spot, (int, float)) or spot <= 0:
        return [], 0
    out, dropped = [], 0
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
                    if abs(delta) > DELTA_MAX:     # -999.0 sentinel / rounding past 1
                        dropped += 1
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
    return out, dropped


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


def alert_uoa_id(r):
    """The id the live handler stamps on a UOA alert for this contract.

    Must stay byte-identical to handlers.run_flow_alerts:
    ``f"{sym}|uoa|{c['side']}|{c['strike']:g}|{c['expiry']}"`` -- the whole
    reconciliation is a set comparison on this string."""
    return f"{r['symbol']}|uoa|{r['side']}|{r['strike']:g}|{r['expiry']}"


def read_live_alerts(db=LIVE_ALERTS_DB, port=None):
    """Today's published flow-alert payload, or None.

    Returns ``(payload, note)``. ``payload`` is None whenever the channel
    cannot be read or is not today's, with ``note`` saying which -- the report
    prints that instead of a comparison, because a silent zero here is
    indistinguishable from a real outage. Never raises: reconciliation is a
    cross-check, and it must never cost us the measurement."""
    try:
        import redis
    except Exception:
        return None, "redis client not installed"
    if port is None:
        try:
            from repo_paths import MEMURAI_PORT
            port = MEMURAI_PORT
        except Exception:
            port = 6379
    try:
        r = redis.Redis(host="127.0.0.1", port=port, db=db, socket_timeout=5)
        raw = r.get(FLOW_ALERTS_KEY)
    except Exception as e:  # noqa: BLE001
        return None, f"could not reach Redis on :{port} db{db} ({e})"
    if not raw:
        return None, f"{FLOW_ALERTS_KEY} is empty in db{db}"
    try:
        import json
        env = json.loads(raw)
    except Exception:
        return None, f"{FLOW_ALERTS_KEY} did not decode as JSON"
    payload = env.get("payload", env) if isinstance(env, dict) else {}
    if not isinstance(payload, dict):
        return None, "unexpected payload shape"
    return payload, f"db{db}"


def reconcile(rows, cfg, payload, today, note=""):
    """Compare the MODELLED UOA baseline against what the channel really fired.

    Pure. ``payload`` is read_live_alerts' dict (or None). Returns a dict the
    report renders; ``ok`` False means we could not compare and ``note`` says
    why."""
    if not payload:
        return {"ok": False, "note": note or "no live alert payload"}
    if str(payload.get("date")) != str(today):
        return {"ok": False,
                "note": f"published channel is dated {payload.get('date')}, "
                        f"not {today} -- not comparable"}
    alerts = payload.get("alerts") or []
    by_type = {}
    for a in alerts:
        t = a.get("type") or "unknown"
        by_type[t] = by_type.get(t, 0) + 1

    modelled = {alert_uoa_id(r) for r in rows if passes_uoa(r, cfg)}
    actual = {a.get("id") for a in alerts if a.get("type") == "uoa" and a.get("id")}
    return {
        "ok": True, "note": note,
        "by_type": by_type, "channel_total": len(alerts),
        "modelled": len(modelled), "actual": len(actual),
        "matched": len(modelled & actual),
        "only_modelled": sorted(modelled - actual),
        "only_actual": sorted(actual - modelled),
    }


def reconciliation_section(rec):
    """Markdown for the reconciliation block. Pure."""
    L = ["\n## Baseline reconciliation — modelled vs what actually fired\n"]
    if not rec.get("ok"):
        L.append(f"> Not reconciled: {rec.get('note')}. The baseline below is a "
                 f"MODEL of the live rule, not a record of it.\n")
        return L

    total = rec["channel_total"]
    L.append(f"The live channel carried **{total} alerts** today "
             f"({', '.join(f'{k} {v}' for k, v in sorted(rec['by_type'].items()))}).\n")
    L.append("\n**That total is the denominator** for 'would a new detector drown the "
             "channel?' — not the UOA count alone.\n")

    mod, act, mat = rec["modelled"], rec["actual"], rec["matched"]
    delta = mod - act
    sign = "over" if delta > 0 else ("under" if delta < 0 else "exactly")
    L.append(f"\n| | contracts |\n|---|---:|\n")
    L.append(f"| modelled by this script | {mod} |\n")
    L.append(f"| actually fired (uoa) | {act} |\n")
    L.append(f"| matched | {mat} |\n")
    L.append(f"| modelled but never fired | {len(rec['only_modelled'])} |\n")
    L.append(f"| fired but not modelled | {len(rec['only_actual'])} |\n")
    if delta:
        L.append(f"\nThe model **{sign}counts by {abs(delta)}** "
                 f"({abs(delta) / act * 100:.0f}% of actual). "
                 f"Treat every alerts-per-day figure derived from the model as "
                 f"approximate until this closes.\n")
    else:
        L.append("\nThe model matches the channel exactly.\n")

    for label, key in (("Modelled but never fired", "only_modelled"),
                       ("Fired but not modelled", "only_actual")):
        ids = rec[key]
        if ids:
            L.append(f"\n{label} (first 10 of {len(ids)}):\n\n")
            for i in ids[:10]:
                L.append(f"- `{i}`\n")
    L.append("\nLikely sources of a gap, in order of expected size: the live "
             "detector returns only `top_n` contracts per symbol per tick; it runs "
             "only while the collector polls (08:00–15:20 CT) whereas this script "
             "sees final settled volume; and it depends on the per-tick chain "
             "stash, so a skipped symbol or a service restart drops alerts.\n")
    return L


def _fmt_money(v):
    if abs(v) >= 1e9:
        return f"${v/1e9:,.2f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:,.0f}M"
    return f"${v/1e3:,.0f}k"


def build_report(rows, cfg, symbols, failed, sentinel=0, rec=None):
    """The markdown report. Pure over the collected rows."""
    L = []
    now = datetime.now(CT)
    L.append(f"# Delta-notional flow-alert instrumentation — {now:%Y-%m-%d %H:%M} CT\n")
    L.append(f"Universe: **{len(symbols)}** symbols "
             f"({len(symbols) - len(failed)} fetched, {len(failed)} failed"
             + (f": {', '.join(failed)}" if failed else "") + ")\n")
    L.append(f"Contracts with volume + delta: **{len(rows):,}**\n")
    if sentinel:
        L.append(f"\n> Dropped **{sentinel}** contracts with an impossible "
                 f"`|delta| > 1` — Schwab's `-999.0` no-value sentinel. Left in, "
                 f"they manufacture billions in phantom exposure and would fire "
                 f"on every absolute threshold, daily. **A real detector needs "
                 f"this same guard.**\n")

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

    if rec is not None:
        L.extend(reconciliation_section(rec))

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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    force = "--force" in argv
    today = datetime.now(CT).date()
    if not is_trading_day(today) and not force:
        why = "weekend" if today.weekday() >= 5 else "market holiday"
        print(f"Skipped: {today:%Y-%m-%d} is not a trading day ({why}). "
              f"No report written — a non-session distribution would corrupt "
              f"threshold calibration. Use --force to measure anyway.")
        return 0        # a correct skip is not a failure; the wrapper logs exit=0
    symbols = flow_symbols()
    cfg = uoa_cfg()
    print(f"Flow universe: {len(symbols)} symbols; proxy {PROXY_URL}")
    client = SchwabPyProxyClient(PROXY_URL)

    rows, failed, sentinel = [], [], 0
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
        for sym, chain in zip(symbols, ex.map(lambda s: fetch_chain(client, s, today), symbols)):
            if not chain:
                failed.append(sym)
                continue
            got, bad = contracts_from(sym, chain)
            rows.extend(got)
            sentinel += bad
            print(f"  {sym:<6} {len(got):>5} contracts"
                  + (f"  ({bad} sentinel delta dropped)" if bad else ""))

    # Reconcile the modelled baseline against the channel that actually fired.
    # Best-effort by construction: a failure here degrades to a "not reconciled"
    # note in the report and never costs us the measurement we just paid ~91
    # chain fetches for.
    db = LIVE_ALERTS_DB
    if "--alerts-db" in argv:
        try:
            db = int(argv[argv.index("--alerts-db") + 1])
        except (IndexError, ValueError):
            print("  ! --alerts-db needs an integer; using default")
    payload, note = read_live_alerts(db)
    rec = reconcile(rows, cfg, payload, today, note)
    print(f"Reconciliation: {'ok' if rec.get('ok') else rec.get('note')}")

    out_dir = OPTIONS_SCANNER / "data" / "flow_delta_instrumentation" / str(today)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = build_report(rows, cfg, symbols, failed, sentinel, rec)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
