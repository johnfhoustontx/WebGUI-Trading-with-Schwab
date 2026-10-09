"""How much of a change in stored premium is re-marking rather than trading.

The collector stores, per strike per minute, the day's traded premium as
``Σ mark × totalVolume × 100`` (``flow_skew.premium_by_strike``, the ``prem``
view). That is a day-CUMULATIVE figure priced at the CURRENT mark, so it is not
monotonic: when a contract's mark falls, the value of the volume it already
traded falls with it. A "change over 30 minutes" of it therefore holds new
trades AND the re-marking of earlier ones.

A true traded total can never fall. So on each SIDE (calls, puts) of each strike
every fall in the stored figure is re-marking, and the falls measured against
the rises bound how much of a Change view of premium would be an artifact. This
prints that for one symbol's stored session, minute to minute and over the
window the page would use. Design:
docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md, section 7.

Read-only. Usage, from the repo root::

    .venv/bin/python tools/measure_prem_remark.py SPX SPY NVDA --date 2026-10-07
    .venv/bin/python tools/measure_prem_remark.py SPX --date 2026-09-21 --db /path/to/gex_history.db

A symbol may be typed without its ``$``. ``--db`` reads a COPY of the database
(a backup) immutably, which is only safe for a file nothing is writing.

⚠ The bound is one-sided. A fall proves re-marking; a rise may hide some (marks
rising on old volume look like new premium). So this can show a Change view is
unsafe; it cannot fully prove one is clean.
"""
from __future__ import annotations

import argparse
import bisect
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

from shared import gamma_heat_config as heat_cfg  # noqa: E402
from shared.market_calendar import _session_bounds  # noqa: E402

# The gate: a Change view of premium ships only if, on every symbol measured,
# the dollars that fell over the window are under this share of those that rose.
MAX_FALLEN_PCT = 10.0

SIDES = ("call", "put")


def _side_series(rows):
    """``{(strike, side): [(ts, value), …]}``, each strike's readings in time
    order. A minute the strike is missing from is simply not in its list."""
    out = {}
    for row in rows:
        ts, grid = row[0], row[6]
        if not isinstance(grid, dict):
            continue
        for strike, cell in grid.items():
            if not isinstance(cell, dict):
                continue
            for side in SIDES:
                value = cell.get(side)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out.setdefault((strike, side), []).append((ts, float(value)))
    return out


def _tally(diffs):
    risen = sum(d for d in diffs if d > 0)
    fallen = -sum(d for d in diffs if d < 0)
    fell = sum(1 for d in diffs if d < 0)
    return risen, fallen, (100.0 * fell / len(diffs) if diffs else 0.0)


def measure(rows, window_min):
    """Rises and falls of each side of each strike: minute to minute, and over
    ``window_min`` (what a Change view would draw). None with nothing to compare.

    Minute to minute compares CONSECUTIVE rows only, so a strike missing from a
    minute contributes nothing on either side of the gap."""
    rows = [r for r in rows or () if len(r) > 6]
    if len(rows) < 2:
        return None
    index = {r[0]: i for i, r in enumerate(rows)}
    step, window = [], []
    for series in _side_series(rows).values():
        times = [t for t, _ in series]
        for (t0, v0), (t1, v1) in zip(series, series[1:]):
            if index[t1] - index[t0] == 1:
                step.append(v1 - v0)
        for ts, value in series:
            # The latest reading at least the window old, as the page's delta does.
            at = bisect.bisect_right(times, ts - window_min * 60)
            if at:
                window.append(value - series[at - 1][1])
    risen, fallen, fell_pct = _tally(step)
    w_risen, w_fallen, w_fell_pct = _tally(window)
    return {"rows": len(rows), "cells": len(step),
            "risen": risen, "fallen": fallen,
            "fallen_pct_of_risen": round(100.0 * fallen / risen, 1) if risen else 0.0,
            "cells_fell_pct": round(fell_pct, 1),
            "window_cells": len(window),
            "window_risen": w_risen, "window_fallen": w_fallen,
            "window_fallen_pct_of_risen":
                round(100.0 * w_fallen / w_risen, 1) if w_risen else 0.0,
            "window_cells_fell_pct": round(w_fell_pct, 1)}


def _money(v):
    return f"${v / 1e6:,.1f}M"


def report(symbol, date, m, window_min):
    if m is None:
        return [f"{symbol} {date}: fewer than two premium rows in the display window"]
    worst = max(m["fallen_pct_of_risen"], m["window_fallen_pct_of_risen"])
    verdict = (f"under the {MAX_FALLEN_PCT:.0f}% gate" if worst < MAX_FALLEN_PCT
               else f"OVER the {MAX_FALLEN_PCT:.0f}% gate")
    return [f"{symbol} {date}: {m['rows']} rows",
            f"  minute to minute: rose {_money(m['risen'])}, fell {_money(m['fallen'])}"
            f" = {m['fallen_pct_of_risen']:.1f}% of the rises;"
            f" {m['cells_fell_pct']:.1f}% of {m['cells']:,} side-steps fell",
            f"  over {window_min} min:     rose {_money(m['window_risen'])},"
            f" fell {_money(m['window_fallen'])}"
            f" = {m['window_fallen_pct_of_risen']:.1f}% of the rises;"
            f" {m['window_cells_fell_pct']:.1f}% of {m['window_cells']:,} comparisons fell",
            f"  {verdict}"]


def load_symbol(load, symbol):
    """``(symbol as stored, rows)``. An index is stored with a ``$``; typing it
    bare is allowed, because a ``$`` does not survive a remote shell."""
    rows = load(symbol)
    if not rows and not symbol.startswith("$"):
        with_sign = load("$" + symbol)
        if with_sign:
            return "$" + symbol, with_sign
    return symbol, rows


def _display_rows(rows):
    """The rows the heatmap shows: the regular session only."""
    start, end = _session_bounds("regular")
    return [r for r in rows
            if start <= _dt.datetime.fromtimestamp(r[0]).time() <= end]


def _connect(db):
    if db is None:
        return gh.connect(read_only=True)
    uri = f"file:{pathlib.Path(db).as_posix()}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True, isolation_level=None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--date", help="session date, YYYY-MM-DD (default: today)")
    ap.add_argument("--db", help="read this COPY of gex_history.db, immutably")
    ap.add_argument("--window-min", type=int, default=heat_cfg.change_window_min())
    args = ap.parse_args(argv)
    day = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    try:
        conn = _connect(args.db)
    except sqlite3.Error as e:
        print(f"Cannot open the history database read-only: {e}", file=sys.stderr)
        return 2
    try:
        for typed in args.symbols:
            name, rows = load_symbol(
                lambda s: _display_rows(gh.load_date_with_grid(
                    conn, s, "prem", date=day)), typed)
            print("\n".join(report(name, day.isoformat(),
                                   measure(rows, args.window_min),
                                   args.window_min)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
