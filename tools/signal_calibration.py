"""Does the composite score predict? -- the calibration report for signals.db.

EV = p*b - (1-p) is already the scanner's width-selection objective and its
rejection gate (`scanner_engine.select_best_width`, and the `credit/w < |delta| +
EDGE_MARGIN` floor above it, which is the same inequality in closed form). But
every `p` in that calculation is the RISK-NEUTRAL probability, extracted from the
option's own price -- so the EV it produces is ~0 by construction and can only
say "not obviously mispriced against me", never "this is a good trade".

The one independent source of `p` in this repo is what actually happened.
`signals` has stored `entry_grade` and `entry_score` since the recorder was
written and `signal_outcomes` has stored `realized_pnl` beside it, and nothing
has ever joined the two. This does.

Everything is measured in **R** -- realized P&L over the dollars that were at
risk -- so a 2-wide spread and a 10-wide spread are comparable, and so that the
formula's `b` and the report's EV are in the same units.

Read-only. It opens the database with `mode=ro` and never writes.

Usage:
    .venv\\Scripts\\python tools\\signal_calibration.py
    .venv\\Scripts\\python tools\\signal_calibration.py --by entry_score --min-n 20
    .venv\\Scripts\\python tools\\signal_calibration.py --db "D:\\WebGUI Trading Prod\\options-scanner\\data\\signals.db"
"""
import argparse
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import OPTIONS_SCANNER  # noqa: E402
# The bucket arithmetic lives in shared/ because options_svc needs it too --
# see the module docstring there. This file owns the SQL and the rendering.
from shared.calibration import (  # noqa: E402,F401  (re-exported for callers)
    bucket_stats, breakeven_win_rate, calibrate, priced_win_rate, r_multiple,
    score_bin, split_calibrate)

DEFAULT_DB = OPTIONS_SCANNER / "data" / "signals.db"

# The columns the report reads. `signal_id` is deliberately absent: this is an
# aggregate over buckets, and a per-trade id in the row dict invites someone to
# print one.
_SELECT = """
SELECT s.entry_grade, s.entry_score, s.entry_credit, s.entry_max_loss,
       s.entry_short_delta, s.entry_iv_rank, s.width, s.dte_at_entry,
       s.scanner_type, s.strategy, s.symbol, s.first_seen_date,
       o.realized_pnl, o.exit_reason, o.close_date
FROM signals s JOIN signal_outcomes o USING (signal_id)
"""


GROUP_KEYS = ("entry_grade", "entry_score", "scanner_type", "strategy", "symbol",
              "exit_reason", "dte_at_entry")


# ── rendering ────────────────────────────────────────────────────────────────

_COLS = [("bucket", 12, "<"), ("n", 5, ">"), ("days", 5, ">"), ("win%", 7, ">"),
         ("priced%", 8, ">"), ("edge", 7, ">"), ("b", 6, ">"), ("need%", 7, ">"),
         ("EV(R)", 8, ">"), ("t", 6, ">"), ("tDay", 6, ">"), ("totR", 8, ">")]


def _cell(v, kind):
    if v is None:
        return "--"
    if kind == "pct":
        return f"{v * 100:.1f}"
    if kind == "pp":
        return f"{v:+.1f}"
    return f"{v:+.3f}" if kind == "signed" else f"{v:.2f}"


_EMPTY_REPORT = "no closed signals to calibrate (an empty join)\n"

_LEGEND = [
    "win%    realized win rate           priced%  what the entry delta implied (1-|delta|)",
    "edge    win% - priced%, in points   b        realized avg win / avg loss, in R",
    "need%   breakeven win rate for b    EV(R)    mean R per trade -- the bottom line",
    "totR    R summed over the bucket    days     distinct entry days the bucket spans",
    "",
    "READ tDay, NOT t. One scan emits many correlated signals, so t treats a",
    "dozen bets on one tape as a dozen independent ones. tDay clusters by entry",
    "day. |tDay| < 2 is noise.",
]


def _table_lines(buckets):
    """Header, rule and one row per bucket -- no title and no legend."""
    head = "  ".join(f"{h:{a}{w}}" for h, w, a in _COLS)
    lines = [head, "-" * len(head)]
    for b in buckets:
        vals = [str(b["bucket"])[:12], str(b["n"]), str(b["days"]),
                _cell(b["realized_p"], "pct"), _cell(b["priced_p"], "pct"),
                _cell(b["edge_pp"], "pp"), _cell(b["b"], "plain"),
                _cell(b["breakeven_p"], "pct"), _cell(b["ev_r"], "signed"),
                _cell(b["t_stat"], "plain"), _cell(b["t_day"], "plain"),
                _cell(b["total_r"], "signed")]
        lines.append("  ".join(f"{v:{a}{w}}" for v, (_, w, a) in zip(vals, _COLS)))
    return lines


def _scratch_note(buckets):
    n = sum(b["scratches"] for b in buckets)
    return [f"note: {n} scratch trade(s) at exactly $0 -- neither win nor loss."] if n else []


def format_table(buckets, key="entry_grade"):
    """The report as plain text. Column meanings are spelled out underneath."""
    if not buckets:
        return _EMPTY_REPORT
    lines = [f"by {key}"] + _table_lines(buckets) + [""] + _LEGEND
    return "\n".join(lines + _scratch_note(buckets)) + "\n"


def format_split(sections, key="entry_score", split_by="scanner_type"):
    """One titled table per split value, with the legend printed once at the end."""
    if not sections:
        return _EMPTY_REPORT
    lines = []
    for name, buckets in sections:
        lines += [f"by {key}, within {split_by} = {name}"] + _table_lines(buckets) + [""]
    every = [b for _, buckets in sections for b in buckets]
    return "\n".join(lines + _LEGEND + _scratch_note(every)) + "\n"


# ── I/O ──────────────────────────────────────────────────────────────────────

def load_rows(db_path=DEFAULT_DB, where=None, params=()):
    """Every closed signal joined to its outcome, as dicts. Read-only."""
    uri = f"file:{pathlib.Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        sql = _SELECT + (f" WHERE {where}" if where else "")
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DEFAULT_DB), help="signals.db to read")
    ap.add_argument("--by", default="entry_grade", choices=GROUP_KEYS,
                    help="bucket key (default: entry_grade)")
    ap.add_argument("--split", default=None, choices=GROUP_KEYS, metavar="KEY",
                    help="report the --by breakdown separately within each value "
                         "of KEY (e.g. --split scanner_type for 0DTE vs swing)")
    ap.add_argument("--min-n", type=int, default=1,
                    help="drop buckets thinner than this (applied WITHIN a split)")
    ap.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                    help="only outcomes closed on or after this date")
    ap.add_argument("--exclude-reason", default=None, metavar="REASON",
                    help="drop an exit_reason (e.g. MANUAL_CLOSE)")
    a = ap.parse_args(argv)

    clauses, params = [], []
    if a.since:
        clauses.append("o.close_date >= ?")
        params.append(a.since)
    if a.exclude_reason:
        clauses.append("o.exit_reason <> ?")
        params.append(a.exclude_reason)

    try:
        rows = load_rows(a.db, " AND ".join(clauses) or None, tuple(params))
    except sqlite3.Error as e:
        print(f"cannot read {a.db}: {e}", file=sys.stderr)
        return 2

    if a.split:
        print(format_split(split_calibrate(rows, a.by, a.split, a.min_n),
                           a.by, a.split))
    else:
        print(format_table(calibrate(rows, a.by, a.min_n), a.by))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
