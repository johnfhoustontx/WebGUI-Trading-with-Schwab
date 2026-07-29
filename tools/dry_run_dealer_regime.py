#!/usr/bin/env python
"""Dry-run: label each collected symbol with a dealer-flow regime from the LIVE
gex_history.db -- WITHOUT surfacing it in the webgui or changing any contract.

Reads a session's gex-view snapshots READ-ONLY, computes
matrix.dealer_regime_from_rows per symbol, and prints a table + tally. Use it to
eyeball whether the four named regimes (vanna_squeeze / gamma_cascade /
delta_wall_pin / charm_grind) fire where the charts say they should, before
deciding to wire the label onto the Matrix.

The IV-direction axis (iv_state) reads 'na' until the forward-only ``atm_iv``
column has data for the chosen session -- the STRUCTURAL axes (regime vs flip,
wall proximity, time-of-day) work from existing columns immediately, so
gamma_cascade needs atm_iv but pin/grind/neutral do not.

    python tools/dry_run_dealer_regime.py                     # live: now, today
    python tools/dry_run_dealer_regime.py --date 2026-07-21 --at 14:30
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from repo_paths import OPTIONS_SCANNER  # noqa: E402

sys.path.insert(0, str(OPTIONS_SCANNER))
import gex_history_db as db  # noqa: E402
import gex_collector as gc  # noqa: E402
from services.options_svc import matrix as mx  # noqa: E402  (pure, no I/O)

TZ = ZoneInfo("America/Chicago")


def _day_bounds(d: dt.date) -> tuple[int, int]:
    start = dt.datetime(d.year, d.month, d.day, tzinfo=TZ)
    return int(start.timestamp()), int((start + dt.timedelta(days=1)).timestamp())


def _select(conn, cols, symbol, start, end):
    return conn.execute(
        f"SELECT {cols} FROM snapshots WHERE symbol=? AND view='gex' "
        "AND ts>=? AND ts<? ORDER BY ts",
        (symbol, start, end),
    ).fetchall()


def _fmt(x) -> str:
    return "-" if x is None else f"{x:g}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="session date YYYY-MM-DD (CT); default today")
    ap.add_argument("--at", help="evaluate as-of HH:MM CT (default: now if today, "
                                 "else each symbol's last snapshot)")
    args = ap.parse_args()

    today = dt.datetime.now(TZ).date()
    d = dt.date.fromisoformat(args.date) if args.date else today
    start, end = _day_bounds(d)
    close_ts = int(dt.datetime(d.year, d.month, d.day, 15, 0, tzinfo=TZ).timestamp())

    if args.at:
        hh, mm = (int(p) for p in args.at.split(":"))
        anchor = int(dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=TZ).timestamp())
    elif d == today:
        anchor = int(dt.datetime.now(TZ).timestamp())
    else:
        anchor = None  # per-symbol: use the last snapshot ts

    try:
        conn = db.connect(read_only=True)
    except Exception as e:  # noqa: BLE001 — friendly message beats a traceback
        print(f"cannot open gex_history.db read-only ({e}). Has the collector run?")
        return

    # atm_iv is forward-only: a live DB written by the pre-migration collector has
    # no such column yet. Probe once; if absent, the IV axis reads 'na' until the
    # options service restarts on the new schema (structural axes work regardless).
    has_atm = any(row[1] == "atm_iv"
                  for row in conn.execute("PRAGMA table_info(snapshots)"))

    try:
        symbols = gc.collection_symbols()
    except Exception:
        symbols = gc.SYMBOLS

    as_of = "now" if anchor and d == today and not args.at else (args.at or "last row")
    print(f"Dealer-regime dry-run   session={d}   as-of={as_of}   "
          f"symbols={len(symbols)}\n")
    hdr = (f"{'SYM':<6}{'SPOT':>10}{'FLIP':>10}  {'TREND':<11}{'IV':<11}"
           f"{'WALL%':>7}{'MIN2CLOSE':>10}   REGIME")
    print(hdr)
    print("-" * len(hdr))

    tally: dict[str, int] = {}
    for sym in symbols:
        rows = _select(conn, "ts, spot, flip, top_pos_strike, top_neg_strike, "
                             "net_total", sym, start, end)
        if not rows:
            continue
        a = anchor if anchor is not None else rows[-1][0]
        rows = [r for r in rows if r[0] <= a]
        if not rows:
            continue
        atm = ([r for r in _select(conn, "ts, atm_iv", sym, start, end) if r[0] <= a]
               if has_atm else [])
        r = mx.dealer_regime_from_rows(rows, atm, a, close_ts)
        tally[r["regime"]] = tally.get(r["regime"], 0) + 1
        print(f"{sym:<6}{_fmt(r['spot']):>10}{_fmt(r['flip']):>10}  "
              f"{r['trend_state']:<11}{r['iv_state']:<11}"
              f"{_fmt(r['wall_dist_pct']):>7}{_fmt(r['mins_to_close']):>10}   "
              f"{r['regime']}")
    conn.close()

    print("\ntally:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "(no rows)")
    active = {k: v for k, v in tally.items() if k not in ("na", "neutral")}
    if not active:
        print("no active setups — expected off-hours, or until atm_iv fills in for "
              "this session (gamma_cascade/vanna_squeeze need the IV axis).")


if __name__ == "__main__":
    main()
