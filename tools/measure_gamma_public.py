#!/usr/bin/env python
"""Measure what a HOT public Gamma symbol would cost per minute (Phase 0).

The public-Gamma roadmap (docs/plans/2026-09-21-public-gamma-any-symbol-roadmap.md)
adds visitor-picked symbols to the 1-minute GEX branch while a visitor watches
them. The cap on that hot set hangs on three numbers nobody has, and this
measures all three:

* **per symbol** -- the real ``compute.gamma_snapshot`` on a freshly fetched
  chain, timed COLD (the first build, which loads the session's whole history)
  and WARM (a second build on the same chain, which is what every later minute
  costs once the history memo holds the symbol); the Schwab calls it made
  beyond the base chain (Term's wider fetches, ``_term_chain``); and the bytes a
  publish would write (the slim main payload plus the four history keys).
* **the branch today** (``--watch``) -- how long the live 1-minute ``gex``
  branch takes now: from the journal's ``Polling GEX history`` line to the
  moment the branch's LAST published key is stamped
  (``cache:options:gamma_pub:QQQ:ts``). The branch already overruns its minute
  a few times a day (``still running; skipping this slot``), so the headroom is
  the number that sets the cap.
* **no Term** -- the public page has no Term view (owner, 2026-09-21), so by
  default the builds run with ``compute._term_chain`` stubbed out: no wider
  chain fetch, and an empty Term grid. ``--with-term`` measures the old cost.
* **the cap** -- the largest hot set whose warm cost, added to today's p95
  branch time, still fits the budget (``--budget``, default 50 s of the 60).

⚠ It calls ``compute`` DIRECTLY, never ``handlers``: ``gamma_snapshot`` with an
explicit chain reads SQLite read-only and writes no cache key, no command stream
and no database row. It does not touch the service's consume-once chain stash
either -- that lives in the options_svc process, not this one. The one Redis
access is ``--watch``'s GET of a ``:ts`` side key.

⚠ It DOES spend Schwab calls (one chain per symbol plus up to three Term
widenings) through the shared proxy, and it competes with the live branch for
the box's CPU. Run it during the session, off the quarter hours. Read the
journal for ``still running`` afterwards; a skip at the run's minutes is part of
the finding, not noise.

    python tools/measure_gamma_public.py --watch 10 NVDA AAPL TSLA IWM XLU
    python tools/measure_gamma_public.py --json out.json NVDA

On the box, from the checkout: ``set -a; . ./.env; set +a`` first (``--watch``
reads Redis with ``MEMURAI_PASSWORD``).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import statistics
import subprocess
import sys
import time
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HISTORY_VIEWS = ("GEX", "Charm", "DEX", "Vanna")

# Unpublished names across the cadences that matter: daily expirations (IWM),
# the heavy single names, and a sector ETF with sparser expirations (Term widens).
DEFAULT_SYMBOLS = ("NVDA", "AAPL", "TSLA", "IWM", "XLU")

# The branch's last write today: refresh_gamma_current publishes $SPX, SPY, QQQ
# in that order (PUBLISHED_GAMMA_VIEWS order), so QQQ's stamp is its end.
END_KEY = "cache:options:gamma_pub:QQQ"
POLL_MARK = "Polling GEX history (options_svc)"
UNIT = "trading-prod-options_svc.service"


# ── pure ─────────────────────────────────────────────────────────────────────

def endpoint(url) -> str:
    """The proxy path a request hit, without its query (``/chains``)."""
    return urllib.parse.urlsplit(str(url)).path or "?"


def expirations(chain) -> int:
    """Distinct expirations in a chain's call and put maps."""
    seen = set()
    for side in ("callExpDateMap", "putExpDateMap"):
        for key in (chain or {}).get(side) or {}:
            seen.add(str(key).split(":")[0])
    return len(seen)


def payload_sizes(snap) -> dict:
    """What ONE publish of ``snap`` writes, split the way ``_publish_gamma``
    splits it: history rows out into four keys, the slim rest into the main key.
    Works on a copy -- the caller's snapshot keeps its rows."""
    snap = json.loads(json.dumps(snap, default=str))
    hist = {}
    for view in HISTORY_VIEWS:
        entry = (snap.get("views") or {}).get(view)
        rows = entry.pop("history", None) if isinstance(entry, dict) else None
        rows = rows or []
        hist[view] = {"rows": len(rows),
                      "bytes": len(json.dumps({"symbol": snap.get("symbol"),
                                               "view": view, "rows": rows}))}
    main = len(json.dumps(snap))
    return {"main_bytes": main, "history": hist,
            "total_bytes": main + sum(h["bytes"] for h in hist.values())}


def summarize(symbol, *, fetch_s, fetch_calls, cold_s, cold_calls, warm_s,
              exp_count, sizes, error=None) -> dict:
    """One symbol's row. ``cold_calls`` are the calls the snapshot made BEYOND
    the base chain -- Term's widenings; a warm build makes them again, since
    the Term chain is not memoized."""
    return {"symbol": symbol, "error": error,
            "fetch_s": round(fetch_s, 2), "fetch_calls": fetch_calls,
            "cold_s": round(cold_s, 2), "warm_s": round(warm_s, 2),
            "term_calls": cold_calls, "expirations_7d": exp_count,
            **(sizes or {"main_bytes": 0, "history": {}, "total_bytes": 0})}


def pair_ticks(starts, ends, max_s=180.0) -> list:
    """Each branch start paired with the first end stamp after it, in seconds.

    ``starts`` and ``ends`` are sorted epoch seconds. A start with no end inside
    ``max_s`` (a branch still running when the watch stopped, or one whose end
    stamp was skipped) is dropped rather than guessed."""
    out, j = [], 0
    for s in starts:
        while j < len(ends) and ends[j] <= s:
            j += 1
        if j < len(ends) and ends[j] - s <= max_s:
            out.append(round(ends[j] - s, 1))
    return out


def p95(values):
    """The 95th percentile by nearest rank (no interpolation), or None."""
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(0.95 * len(v))) - 1))]


def hot_cap(branch_p95, warm_mean, budget_s) -> int | None:
    """Largest N with ``branch_p95 + N * warm_mean <= budget_s``, or None
    when either input is missing. 0 means there is no headroom at all."""
    if branch_p95 is None or not warm_mean:
        return None
    return max(0, int((budget_s - branch_p95) // warm_mean))


def totals(rows, ticks, budget_s) -> dict:
    ok = [r for r in rows if not r["error"]]
    warm = statistics.mean(r["warm_s"] for r in ok) if ok else None
    bp95 = p95(ticks)
    return {
        "symbols": len(rows),
        "errors": [r["symbol"] for r in rows if r["error"]],
        "warm_mean_s": round(warm, 2) if warm else None,
        "cold_mean_s": round(statistics.mean(r["cold_s"] for r in ok), 2) if ok else None,
        "term_calls_per_min": round(statistics.mean(r["term_calls"] for r in ok), 2) if ok else None,
        "bytes_per_symbol_min": int(statistics.mean(r["total_bytes"] for r in ok)) if ok else None,
        "branch_ticks": len(ticks),
        "branch_median_s": statistics.median(ticks) if ticks else None,
        "branch_p95_s": bp95,
        "branch_max_s": max(ticks) if ticks else None,
        "budget_s": budget_s,
        "hot_cap": hot_cap(bp95, warm, budget_s),
    }


# ── impure ───────────────────────────────────────────────────────────────────

class CallCounter:
    """Count this process's HTTP requests by endpoint while installed."""

    def __init__(self):
        self.calls = collections.Counter()
        self._orig = None

    def __enter__(self):
        import requests
        self._orig = requests.Session.request
        counter, orig = self.calls, self._orig

        def _counted(session, method, url, *a, **kw):
            counter[endpoint(url)] += 1
            return orig(session, method, url, *a, **kw)

        requests.Session.request = _counted
        return self

    def __exit__(self, *exc):
        import requests
        requests.Session.request = self._orig
        return False

    @property
    def total(self):
        return sum(self.calls.values())


class NoTerm:
    """Build without Term while installed: ``_term_chain`` returns nothing, so
    ``gamma_snapshot`` makes no wider fetch and its guarded Term compute
    degrades to ``{}`` -- the hot-symbol path the public page will run."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self._orig = None

    def __enter__(self):
        if self.enabled:
            from services.options_svc import compute
            self._orig = compute._term_chain
            compute._term_chain = lambda symbol, base_chain, *a, **kw: None
        return self

    def __exit__(self, *exc):
        if self._orig is not None:
            from services.options_svc import compute
            compute._term_chain = self._orig
        return False


def measure(symbol, with_term=False) -> dict:
    with NoTerm(enabled=not with_term):
        return _measure(symbol)


def _measure(symbol) -> dict:
    from services.options_svc import compute
    zero = dict(fetch_s=0.0, fetch_calls=0, cold_s=0.0, cold_calls=0,
                warm_s=0.0, exp_count=0, sizes=None)
    try:
        with CallCounter() as cc:
            t0 = time.perf_counter()
            chain = compute._gamma_fetch_chain(symbol)
            fetch_s = time.perf_counter() - t0
        if not chain:
            return summarize(symbol, **{**zero, "fetch_s": fetch_s,
                                        "fetch_calls": cc.total}, error="no chain")
        with CallCounter() as cold:
            t0 = time.perf_counter()
            snap = compute.gamma_snapshot(symbol, chain=chain)
            cold_s = time.perf_counter() - t0
        if not snap:
            return summarize(symbol, **{**zero, "fetch_s": fetch_s,
                                        "fetch_calls": cc.total, "cold_s": cold_s},
                             error="no snapshot")
        t0 = time.perf_counter()
        compute.gamma_snapshot(symbol, chain=chain)
        warm_s = time.perf_counter() - t0
        return summarize(symbol, fetch_s=fetch_s, fetch_calls=cc.total,
                         cold_s=cold_s, cold_calls=cold.total, warm_s=warm_s,
                         exp_count=expirations(chain), sizes=payload_sizes(snap))
    except Exception as exc:  # noqa: BLE001 - recorded, the pass goes on
        return summarize(symbol, **zero, error=type(exc).__name__)


def _ts_epoch(raw):
    if raw is None:
        return None
    raw = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        return dt.datetime.fromisoformat(raw).timestamp()
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return None


def watch(minutes) -> list:
    """Sample the live branch for ``minutes``: end stamps from Redis each
    second, start marks from the journal afterwards. Returns durations."""
    from shared.bus import Bus
    r = Bus()._r
    ends, last = [], None
    t_start = time.time()
    stop = t_start + minutes * 60
    while time.time() < stop:
        ts = _ts_epoch(r.get(f"{END_KEY}:ts"))
        if ts is not None and ts != last:
            if last is not None:
                ends.append(ts)
            last = ts
        time.sleep(1)
    since = dt.datetime.fromtimestamp(t_start - 5).strftime("%Y-%m-%d %H:%M:%S")
    out = subprocess.run(
        ["journalctl", "--user", "-u", UNIT, "--since", since, "--no-pager",
         "-o", "short-unix"], capture_output=True, text=True).stdout
    starts = sorted(float(line.split()[0]) for line in out.splitlines()
                    if POLL_MARK in line)
    skips = sum(1 for line in out.splitlines() if "branch 'gex' still running" in line)
    print(f"watch: {len(starts)} branch starts, {len(ends)} end stamps, "
          f"{skips} gex skips", flush=True)
    return pair_ticks(starts, sorted(ends))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    ap.add_argument("--watch", type=int, default=0, metavar="MIN",
                    help="also time the live gex branch for MIN minutes first")
    ap.add_argument("--budget", type=float, default=50.0,
                    help="seconds of the minute the branch may use (default 50)")
    ap.add_argument("--with-term", action="store_true",
                    help="build Term too (the page will not; default off)")
    ap.add_argument("--json", help="also write the full measurement here")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    started = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    ticks = watch(args.watch) if args.watch else []
    if ticks:
        print(f"branch today: n={len(ticks)} median={statistics.median(ticks)}s "
              f"p95={p95(ticks)}s max={max(ticks)}s", flush=True)
    rows = []
    for sym in args.symbols:
        r = measure(sym, with_term=args.with_term)
        rows.append(r)
        print(f"{sym:>6}  fetch {r['fetch_s']:5.1f}s  cold {r['cold_s']:5.1f}s  "
              f"warm {r['warm_s']:5.1f}s  term_calls={r['term_calls']}  "
              f"exp7d={r['expirations_7d']}  {r['total_bytes'] // 1024} KB"
              + (f"  ERROR {r['error']}" if r["error"] else ""), flush=True)
    tot = totals(rows, ticks, args.budget)
    print("TOTAL  " + "  ".join(f"{k}={v}" for k, v in tot.items()), flush=True)
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(
            {"started": started, "symbols": rows, "branch_ticks": ticks,
             "totals": tot}, indent=2, default=str), encoding="utf-8")
    return 1 if tot["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
