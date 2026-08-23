"""Nightly: fill in what actually happened after each recommendation.

Reads the recommendation journal for readings whose 20-day horizon has matured,
fetches each symbol's history through the proxy, and writes the realized forward
returns back — RAW, BETA-ADJUSTED, and the market's own move (see
``services/trade_svc/labeler`` for why all three).

**A standalone script driven by a Windows scheduled task, deliberately — not a
service scheduler job.** Dev runs with ``schedulers: False``, so a job written
inside a service would sit inert until promotion, and this store pays only in
calendar time: every night it does not run is a night of readings that can never
be labelled, because a model's historical output is not recoverable after the
fact.

Usage:
    .venv\\Scripts\\python tools\\label_journal.py [--dry-run] [--limit N]

Schedule (run once, from an elevated prompt):
    schtasks /Create /TN "TradeJournalLabeler" /TR ^
      "\\"D:\\WebGUI Trading with Schwab\\.venv\\Scripts\\python.exe\\" ^
       \\"D:\\WebGUI Trading with Schwab\\tools\\label_journal.py\\"" ^
      /SC DAILY /ST 18:30
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd                                              # noqa: E402

from services.trade_svc import labeler as L                      # noqa: E402
from services.trade_svc import rec_journal                       # noqa: E402

# `research.labels` owns the trailing-beta definition. Importing it keeps ONE
# definition of beta in the repo: the label the fit would use and the label the
# monitor scores against cannot drift apart.
from repo_paths import TRADE_ANALYZER                            # noqa: E402
if str(TRADE_ANALYZER) not in sys.path:
    sys.path.insert(0, str(TRADE_ANALYZER))

YEARS = 2          # enough for a trailing beta plus the longest horizon


def _history(symbol, years=YEARS):
    """Daily closes for ``symbol`` via the proxy, or None."""
    from services.trade_svc import compute as C
    df = C._price_history(symbol, "year", years, "daily", 1)
    if df is None or getattr(df, "empty", True):
        return None
    return pd.Series(df["close"].to_numpy(dtype="float64"),
                     index=pd.to_datetime(df["datetime"]))


def _under_pytest():
    import os
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def run(dry_run=False, limit=None, log=print, db_path=None):
    """Label every matured reading. Returns how many rows were written.

    ⚠ ``db_path`` is resolved at CALL time, and with none given the run is
    SKIPPED under pytest. Both guards exist because of the same near-miss:
    ``rec_journal.init_db(db_path=DEFAULT_DB_PATH)`` binds its default at
    definition, so monkeypatching the module attribute does nothing and this
    function happily opened the REAL journal from inside a test. The bus is
    fakeredis; SQLite is not, and this repo has a documented incident where a
    suite wrote into live data. Tests pass their own path, which bypasses the
    guard."""
    from research import labels as RL

    if db_path is None and _under_pytest():
        log("skipped: no db_path under pytest")
        return 0

    conn = rec_journal.init_db(db_path or rec_journal.DEFAULT_DB_PATH)
    try:
        due = rec_journal.unlabeled(conn, before_date=L.due_before())
        if limit:
            due = due[:int(limit)]
        if not due:
            log("nothing due for labelling")
            return 0

        spy = _history("SPY")
        if spy is None:
            log("could not fetch SPY — aborting rather than labelling without a "
                "market reference (every label is relative to it)")
            return 0

        cache, written = {}, 0
        for row in due:
            sym = row["symbol"]
            if sym not in cache:
                cache[sym] = _history(sym)
            close = cache[sym]
            if close is None:
                log(f"  {sym} {row['reading_date']}: no history — leaving unlabelled")
                continue
            beta_series = RL.rolling_beta(close, spy)
            i = L._start_index(close, row["reading_date"])
            beta = None
            if i is not None and i < len(beta_series):
                b = beta_series.iloc[i]
                beta = float(b) if b == b else None
            fields = L.labels_for(close, spy, row["reading_date"], beta=beta)
            if all(v is None for k, v in fields.items() if k != "beta"):
                log(f"  {sym} {row['reading_date']}: nothing matured — skipping")
                continue
            if dry_run:
                log(f"  [dry] {sym} {row['reading_date']}: "
                    f"20d={fields['fwd_20d']} ba={fields['fwd_20d_ba']} "
                    f"beta={fields['beta']}")
            else:
                rec_journal.apply_label(conn, sym, row["reading_date"], **fields)
            written += 1
        log(f"labelled {written} of {len(due)} due readings"
            + (" (dry run)" if dry_run else ""))
        return written
    finally:
        rec_journal.close_db(conn)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written, change nothing")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
