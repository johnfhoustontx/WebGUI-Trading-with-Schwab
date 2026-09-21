#!/usr/bin/env python
"""Measure what a PUBLIC Strategy Finder scan would cost, per symbol (Phase 0).

The public-Finder roadmap (docs/plans/2026-09-21-public-strategy-finder-roadmap.md)
proposes one pinned scan per published symbol on a schedule, and the list size
and slot times hang on numbers nobody has for that pin: the 2026-09-14 figures
(13-40 s a scan) were for the WHOLE chain. This runs the real
``compute.swing_scan`` over a symbol list with the proposed pin and reports,
per symbol:

* wall time;
* HTTP calls to the proxy, by endpoint -- counted by wrapping
  ``requests.Session.request`` in this process, so they are exactly the calls
  this scan made, not the proxy's shared counter;
* rows, and how many carry an UNBOUNDED loss (decision D4) and how many are a
  naked short with a bounded but stock-sized loss (``SHORT_PUT``), which D4 has
  to decide separately;
* the published payload's JSON size;
* the top rows, so the ranking can be eyeballed.

⚠ It calls ``compute.swing_scan`` DIRECTLY, never ``handlers.swing_scan``, and
that is the whole safety argument: compute is proxy-only and writes nothing, so
this touches no cache key and no command stream. The one cross-service read --
the committed market state that tilts family ranking -- is a Redis GET and is
skipped with ``--no-market-state``.

⚠ It DOES spend Schwab calls, through the same proxy the stack uses and under
its shared 5 req/s. Run it during the session, off the quarter hours (the
autoscan owns :00/:15/:30/:45), and read the options_svc journal for
``still running`` afterwards: a GEX skip at the run's minutes is a finding.

    python tools/measure_finder_public.py SPY QQQ '$SPX' NVDA
    python tools/measure_finder_public.py --dte-max 45 --json out.json SPY

On the box, from the checkout: ``set -a; . ./.env; set +a`` first (the market
state read needs ``MEMURAI_PASSWORD``), or pass ``--no-market-state``.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The proposed public pin (roadmap §2). The deltas and credit floor are the
# private page's own defaults; the DTE cap is the pin's one real choice.
PIN = {
    "dte_min": 0,
    "dte_max": 90,
    "put_d_min": -0.20,
    "put_d_max": -0.10,
    "call_d_min": 0.10,
    "call_d_max": 0.20,
    "min_cr_fraction": 0.10,
}

DEFAULT_SYMBOLS = ("SPY", "QQQ", "$SPX", "IWM", "NVDA", "AAPL",
                   "MSFT", "AMD", "TSLA", "META")


# ── pure: what a result says ─────────────────────────────────────────────────

def loss_unbounded(sig) -> bool:
    """Whether a row's LOSS is unbounded (decision D4).

    The engine's explicit flag first; a row carrying only the legacy
    ``unbounded`` is partitioned by ``max_profit``, the rule
    ``webgui/pages/options/finder_view._loss_unbounded`` uses."""
    if sig.get("unbounded_loss"):
        return True
    return bool(sig.get("unbounded")) and sig.get("max_profit") is not None


def is_naked_short_put(sig) -> bool:
    """A cash-secured / naked short put: bounded loss, but the whole strike."""
    return str(sig.get("type") or "").upper() in ("SHORT_PUT", "NAKED_PUT")


def endpoint(url) -> str:
    """The proxy path a request hit, without its query (``/chains``)."""
    return urllib.parse.urlsplit(str(url)).path or "?"


def summarize(symbol, result, wall_s, calls, top=5) -> dict:
    """One symbol's measurement out of a ``compute.swing_scan`` result."""
    signals = list(result.get("signals") or [])
    by_group = collections.Counter(str(s.get("group") or "?") for s in signals)
    ranked = sorted(signals, key=lambda s: -(s.get("composite_score") or 0))
    return {
        "symbol": symbol,
        "wall_s": round(wall_s, 2),
        "http_calls": sum(calls.values()),
        "calls_by_endpoint": dict(sorted(calls.items())),
        "rows": len(signals),
        "rows_unbounded_loss": sum(1 for s in signals if loss_unbounded(s)),
        "rows_naked_short_put": sum(1 for s in signals if is_naked_short_put(s)),
        "rows_by_group": dict(sorted(by_group.items())),
        "filtered_out": result.get("filtered_out") or 0,
        "vol_filtered": result.get("vol_filtered") or 0,
        "not_shown": result.get("not_shown") or 0,
        "expiration_count": result.get("expiration_count"),
        "expirations_scanned": result.get("expirations_scanned"),
        "expiries_failed": result.get("expiries_failed"),
        "needs_choice": bool(result.get("needs_choice")),
        "error": result.get("error"),
        "payload_bytes": len(json.dumps(signals, default=str)),
        "top": [{"type": s.get("type"), "expiration": s.get("expiration"),
                 "dte": s.get("dte"), "score": s.get("composite_score"),
                 "unbounded_loss": loss_unbounded(s)} for s in ranked[:top]],
    }


def totals(rows) -> dict:
    """The pass as a whole: what a scheduled slot over these symbols costs."""
    return {
        "symbols": len(rows),
        "wall_s": round(sum(r["wall_s"] for r in rows), 1),
        "http_calls": sum(r["http_calls"] for r in rows),
        "rows": sum(r["rows"] for r in rows),
        "rows_unbounded_loss": sum(r["rows_unbounded_loss"] for r in rows),
        "rows_naked_short_put": sum(r["rows_naked_short_put"] for r in rows),
        "payload_bytes": sum(r["payload_bytes"] for r in rows),
        "errors": [r["symbol"] for r in rows if r["error"]],
    }


# ── impure: run it ───────────────────────────────────────────────────────────

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


def _market_state(enabled):
    if not enabled:
        return None
    try:
        from services.options_svc import handlers
        from shared.bus import Bus
        return handlers._market_state(Bus())
    except Exception as exc:  # noqa: BLE001 - a missing tilt is not a failure
        print(f"(market state unavailable: {type(exc).__name__}; no tilt)",
              file=sys.stderr)
        return None


def measure(symbol, pin, market_state):
    from services.options_svc import compute
    try:
        _status, earnings_date = compute.scan_earnings(symbol)
    except Exception:  # noqa: BLE001 - the handler degrades the same way
        earnings_date = None
    with CallCounter() as cc:
        t0 = time.perf_counter()
        try:
            result = compute.swing_scan(
                symbol=symbol, **pin, families=None, market_state=market_state,
                earnings_date=earnings_date, every_expiry=True,
                earnings_mode="flag",
                per_type_limit=compute.FINDER_PER_TYPE_LIMIT,
                expiry_choice=None, ask_if_large=False)
        except Exception as exc:  # noqa: BLE001 - recorded, the pass goes on
            result = {"signals": [], "error": type(exc).__name__}
        wall = time.perf_counter() - t0
    return summarize(symbol, result, wall, cc.calls)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    ap.add_argument("--dte-min", type=int, default=PIN["dte_min"])
    ap.add_argument("--dte-max", type=int, default=PIN["dte_max"])
    ap.add_argument("--no-market-state", action="store_true")
    ap.add_argument("--json", help="also write the full measurement here")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    pin = {**PIN, "dte_min": args.dte_min, "dte_max": args.dte_max}
    state = _market_state(not args.no_market_state)
    started = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    rows = []
    for sym in args.symbols:
        r = measure(sym, pin, state)
        rows.append(r)
        print(f"{sym:>6}  {r['wall_s']:6.1f}s  calls={r['http_calls']:3d}  "
              f"rows={r['rows']:3d}  unbounded={r['rows_unbounded_loss']:3d}  "
              f"shortput={r['rows_naked_short_put']:3d}  "
              f"exp={r['expirations_scanned'] or r['expiration_count']}  "
              f"{r['payload_bytes'] // 1024} KB"
              + (f"  ERROR {r['error']}" if r["error"] else ""), flush=True)
    tot = totals(rows)
    print(f"TOTAL  {tot['wall_s']}s  calls={tot['http_calls']}  rows={tot['rows']}  "
          f"unbounded={tot['rows_unbounded_loss']}  "
          f"shortput={tot['rows_naked_short_put']}  "
          f"{tot['payload_bytes'] // 1024} KB")
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(
            {"started": started, "pin": pin, "market_state": state,
             "symbols": rows, "totals": tot}, indent=2, default=str),
            encoding="utf-8")
    return 1 if tot["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
