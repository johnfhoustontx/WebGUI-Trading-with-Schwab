"""What the wider gamma-history crop costs, measured on a stored session.

``options_svc`` crops each view's history rows to the display window before it
publishes them. The rule kept the window around the current spot plus every
strike the session's path crossed. The Dealer Positioning spot frame needs a
full window around the session's LOW and HIGH as well
(``services/options_svc/gamma_window.crop_keep``), or its early columns are
gaps on a trending day. This prints, for one symbol's stored session, how many
strikes and how many JSON bytes each rule keeps, so the cost is known before the
service changes. Design: docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md

Read-only. Usage, from the repo root::

    .venv/bin/python tools/measure_gamma_crop.py SPX 2026-10-07
    .venv/bin/python tools/measure_gamma_crop.py SPX SPY NVDA --date 2026-09-24 --db /path/to/gex_history.db

A symbol may be typed without its ``$``. ``--db`` reads a COPY of the database
(a backup) without touching it: it is opened immutable, which is only safe for a
file nothing is writing.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
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

from services.options_svc import gamma_window as gw  # noqa: E402
from shared import gamma_heat_config as heat_cfg  # noqa: E402
from shared.market_calendar import _session_bounds  # noqa: E402


def old_keep(strikes, spot, path, n_side):
    """The crop rule before the spot frame: the window around the current spot
    plus every strike the path crossed."""
    keep = gw.window_around(strikes, spot, n_side)
    if path:
        lo, hi = min(path), max(path)
        keep |= {k for k in strikes if lo <= k <= hi}
    return keep


def _bytes(rows, keep):
    """The JSON size of the rows cropped to ``keep``, as the service publishes
    them (float keys become text)."""
    return len(json.dumps([[*r[:6], {str(k): v for k, v in r[6].items() if k in keep}]
                           for r in rows]))


def measure(rows, n_side, edge_side=None):
    """Both rules on one session's rows ``(ts, spot, …, grid)``: strikes kept,
    bytes published and the growth. ``edge_side`` is the new rule's window
    around the session's low and high (default: ``n_side``, the full window).
    None when there is nothing to measure."""
    rows = [r for r in rows or () if len(r) > 6 and isinstance(r[6], dict)
            and isinstance(r[1], (int, float))]
    if not rows:
        return None
    strikes = sorted({k for r in rows for k in r[6] if isinstance(k, (int, float))})
    path = [r[1] for r in rows]
    spot = path[-1]
    old = old_keep(strikes, spot, path, n_side)
    new = gw.crop_keep(strikes, spot, path, n_side,
                       n_side if edge_side is None else edge_side)
    old_b, new_b = _bytes(rows, old), _bytes(rows, new)
    return {"rows": len(rows), "all_strikes": len(strikes),
            "path_strikes": len([k for k in strikes if min(path) <= k <= max(path)]),
            "low": min(path), "high": max(path), "last": spot,
            "old_strikes": len(old), "new_strikes": len(new),
            "old_bytes": old_b, "new_bytes": new_b,
            "growth_pct": round(100.0 * (new_b - old_b) / old_b, 1) if old_b else 0.0}


def report(symbol, date, m):
    if m is None:
        return [f"{symbol} {date}: no rows in the display window"]
    return [f"{symbol} {date}: {m['rows']} rows, spot {m['low']:.2f} to {m['high']:.2f}"
            f" (last {m['last']:.2f}), {m['path_strikes']} strikes crossed"
            f" of {m['all_strikes']} listed",
            f"  old rule: {m['old_strikes']} strikes, {m['old_bytes'] / 1024:,.0f} KB",
            f"  new rule: {m['new_strikes']} strikes, {m['new_bytes'] / 1024:,.0f} KB"
            f"  ({m['growth_pct']:+.1f}%)"]


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
    ap.add_argument("symbols", nargs="+", help="one or more symbols; a trailing "
                    "YYYY-MM-DD is taken as the date")
    ap.add_argument("--date", help="session date, YYYY-MM-DD (default: today)")
    ap.add_argument("--db", help="read this COPY of gex_history.db, immutably")
    ap.add_argument("--view", default="gex")
    ap.add_argument("--n-side", type=int, default=heat_cfg_n_side())
    ap.add_argument("--edge-side", type=int, default=None,
                    help="strikes kept each side of the session's low and high "
                         "(default: --n-side). config: [window] spot_side")
    args = ap.parse_args(argv)
    symbols, date = list(args.symbols), args.date
    if date is None and len(symbols) > 1 and symbols[-1][:2] == "20":
        date = symbols.pop()
    day = _dt.date.fromisoformat(date) if date else _dt.date.today()
    try:
        conn = _connect(args.db)
    except sqlite3.Error as e:
        print(f"Cannot open the history database read-only: {e}", file=sys.stderr)
        return 2
    try:
        for typed in symbols:
            name, rows = load_symbol(
                lambda s: _display_rows(gh.load_date_with_grid(
                    conn, s, args.view, date=day)), typed)
            print("\n".join(report(name, day.isoformat(),
                                   measure(rows, args.n_side,
                                           args.edge_side))))
    finally:
        conn.close()
    return 0


def heat_cfg_n_side():
    """The display window's half-width in strikes: config when it has the key
    (Phase 3 of the design moves it there), else the value both tiers ship."""
    return getattr(heat_cfg, "n_side", lambda: 20)()


if __name__ == "__main__":
    raise SystemExit(main())
