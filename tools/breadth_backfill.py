"""Load the breadth history: Schwab's daily bars, or a TradingView CSV.

    .venv/bin/python -X utf8 tools/breadth_backfill.py --source schwab [--years 20] [--force] [--db PATH]
    .venv/bin/python -X utf8 tools/breadth_backfill.py --source csv --series tv_pct_above_50 FILE [--force] [--db PATH]
    .venv/bin/python -X utf8 tools/breadth_backfill.py --status [--db PATH]

``--source schwab`` asks the proxy for each of ``breadth_daily.HISTORY_SYMBOLS``
(the stack's ``.env`` loaded, as for any proxy call by hand) and writes every
session ``$SPX`` has a bar for. Without ``$SPX`` it writes nothing: the index's
bars are the session calendar, and without them a breadth series' weekend junk
bars would look like sessions.

``--source csv`` loads one ``tv_`` series from a ``day,value`` file a Claude
session pulled with the TradingView connector. **A TradingView CSV never enters
git**, and no ``tv_`` value may reach a cache view or a site file
(``shared/breadth_history.py`` says why).

A first load needs no ``--force``: the lock only refuses to CHANGE a row more
than two sessions old. ``--force`` is for a re-load that changes old values.
The summary prints every count, so rows a lock kept are visible.

Design: docs/plans/2026-11-27-market-cycle-design.md
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.market_svc import breadth_daily as bd  # noqa: E402
from shared import breadth_config  # noqa: E402
from shared import breadth_history as bh  # noqa: E402
from shared.numeric import parsed_finite  # noqa: E402

_INDEX = "$SPX"
_COUNT_SUFFIXES = ("_highs", "_lows")
_TV_ZERO = 0.01     # TradingView stores a zero count as 0.01


def backfill_schwab(conn, fetch, years: int, today, force=False) -> dict:
    """Fetch every history symbol and write the sessions ``$SPX`` has. Returns
    ``upsert``'s counts plus ``missing``: the symbols whose fetch came back
    empty. Raises ``RuntimeError``, writing nothing, when ``$SPX`` is empty."""
    candles, missing = {}, []
    for sym in bd.HISTORY_SYMBOLS:
        got = fetch(sym, period_type="year", period=years)
        if sym == _INDEX and not got:
            raise RuntimeError("no $SPX history: it is the session calendar, so nothing is written")
        if got:
            candles[sym] = got
        else:
            missing.append(sym)
    out = bh.upsert(conn, bd.history_rows(candles), today, force=force)
    out["missing"] = missing
    return out


def read_csv(path, series: str) -> list[tuple]:
    """``(series, day, value, "tradingview")`` rows from a ``day,value`` file.
    A blank or non-numeric value is skipped; a count's 0.01 is a zero."""
    if not series.startswith("tv_"):
        raise ValueError(f"{series!r} is not a TradingView series (they start tv_)")
    is_count = series.endswith(_COUNT_SUFFIXES)
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for rec in csv.DictReader(fh):
            value = parsed_finite(rec.get("value"))
            day = (rec.get("day") or "").strip()
            if value is None or not day:
                continue
            if is_count and value == _TV_ZERO:
                value = 0.0
            rows.append((series, day, value, "tradingview"))
    return rows


def status_lines(conn) -> list[str]:
    """One line per series: name, first day, last day, rows."""
    return [f"{s}  {first}  {last}  {n}" for s, first, last, n in bh.coverage(conn)]


def _summary(counts: dict) -> str:
    text = ", ".join(f"{k} {v}" for k, v in counts.items() if k != "missing")
    if counts.get("missing"):
        text += "; no history for " + " ".join(counts["missing"])
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Load the breadth history.")
    ap.add_argument("--source", choices=("schwab", "csv"))
    ap.add_argument("--series", help="the tv_ series a CSV holds")
    ap.add_argument("file", nargs="?", help="the CSV (day,value)")
    ap.add_argument("--years", type=int, help="years of Schwab history (default from breadth.toml)")
    ap.add_argument("--force", action="store_true", help="change rows more than two sessions old")
    ap.add_argument("--db", help="write this file instead of the app's store")
    ap.add_argument("--status", action="store_true", help="list what the store holds")
    args = ap.parse_args(argv)

    conn = bh.connect(args.db)
    try:
        if args.status:
            for line in status_lines(conn):
                print(line)
            return 0
        today = _dt.date.today()
        if args.source == "schwab":
            from services.market_svc import compute   # here, so tests build no proxy session
            years = args.years or breadth_config.load()["history_years"]
            counts = backfill_schwab(conn, compute.fetch_daily, years, today, force=args.force)
        elif args.source == "csv":
            if not args.series or not args.file:
                ap.error("--source csv needs --series NAME and a FILE")
            counts = bh.upsert(conn, read_csv(args.file, args.series), today, force=args.force)
        else:
            ap.error("give --source schwab, --source csv or --status")
        print(_summary(counts))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
