"""Does the stored traded premium ever fall, and what does it cost to store.

``services/options_svc/traded_premium.py`` stores, per strike per poll, a
RUNNING TOTAL of traded premium (the ``tprem`` view). A running total can never
fall, so on each side (calls, puts) of each strike every fall is a bug. This
reads every symbol's stored rows for one day, counts the falls, and prints each
view's rows and bytes so what ``tprem`` adds can be set against the estimate
(about an eighth). Design:
docs/plans/2026-10-09-traded-premium-increment-design.md, "How Phase A proves
itself".

Read-only. Usage, from the repo root::

    .venv/bin/python tools/check_traded_premium.py
    .venv/bin/python tools/check_traded_premium.py --date 2026-10-12
    .venv/bin/python tools/check_traded_premium.py --date 2026-10-12 --db /path/to/gex_history.db

``--db`` reads a COPY of the database (a backup) immutably, which is only safe
for a file nothing is writing. Exit status 1 when any total fell.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sqlite3
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import gex_history_db as gh  # noqa: E402

from tools import measure_prem_remark as remark  # noqa: E402

VIEW = "tprem"


def falls(rows):
    """``(steps, fell, dollars)`` over each side of each strike, every reading
    against the one before it whatever the time between them: a running total
    must not fall across a gap either."""
    steps = fell = 0
    dollars = 0.0
    for series in remark._side_series(rows).values():
        for (_t0, before), (_t1, after) in zip(series, series[1:]):
            steps += 1
            if after < before:
                fell += 1
                dollars += before - after
    return steps, fell, dollars


def symbols_with(conn, view, day):
    start, end = gh._local_unix_range(day)
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM snapshots "
        "WHERE view = ? AND ts >= ? AND ts < ? ORDER BY symbol",
        (view, start, end))]


def sizes(conn, day):
    """``{view: (rows, grid bytes)}`` for the day."""
    start, end = gh._local_unix_range(day)
    return {view: (n, size) for view, n, size in conn.execute(
        "SELECT view, COUNT(*), COALESCE(SUM(LENGTH(gex_json)), 0) FROM snapshots "
        "WHERE ts >= ? AND ts < ? GROUP BY view", (start, end))}


def check(conn, day, view=VIEW):
    out = {"symbols": 0, "rows": 0, "steps": 0, "fell": 0, "dollars": 0.0,
           "worst": []}
    for symbol in symbols_with(conn, view, day):
        rows = gh.load_date_with_grid(conn, symbol, view, date=day)
        steps, fell, dollars = falls(rows)
        out["symbols"] += 1
        out["rows"] += len(rows)
        out["steps"] += steps
        out["fell"] += fell
        out["dollars"] += dollars
        if fell:
            out["worst"].append((dollars, symbol, fell))
    out["worst"].sort(reverse=True)
    return out


def report(day, result, by_view, view=VIEW):
    lines = [f"{view} {day}: {result['symbols']} symbols, {result['rows']:,} rows, "
             f"{result['steps']:,} side-steps compared"]
    if not result["symbols"]:
        lines.append("  nothing stored: is [collection] traded_premium on?")
    elif result["fell"]:
        lines.append(f"  FELL {result['fell']:,} times, ${result['dollars']:,.0f} in "
                     "all. A traded total must never fall: this is a bug.")
        lines += [f"    {symbol}: {fell:,} falls, ${dollars:,.0f}"
                  for dollars, symbol, fell in result["worst"][:10]]
    else:
        lines.append("  no side of any strike ever fell")
    for name in sorted(by_view):
        n, size = by_view[name]
        lines.append(f"  {name:6} {n:8,} rows {size / 1e6:8.1f} MB")
    others = sum(size for name, (_n, size) in by_view.items() if name != view)
    if view in by_view and others > 0:
        lines.append(f"  {view} adds {100.0 * by_view[view][1] / others:.1f}% "
                     "to the day's grid bytes")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", help="session date, YYYY-MM-DD (default: today)")
    ap.add_argument("--db", help="read this COPY of gex_history.db, immutably")
    ap.add_argument("--view", default=VIEW)
    args = ap.parse_args(argv)
    day = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    try:
        conn = remark._connect(args.db)
    except sqlite3.Error as e:
        print(f"Cannot open the history database read-only: {e}", file=sys.stderr)
        return 2
    try:
        result = check(conn, day, args.view)
        print("\n".join(report(day, result, sizes(conn, day), args.view)))
    finally:
        conn.close()
    return 1 if result["fell"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
