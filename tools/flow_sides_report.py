"""Do the two bought/sold sources agree? A read-only report for one session.

WHY THIS EXISTS
---------------
A flow alert's bought / sold estimate comes from two sources: the one-minute
chain poll (the contract's whole day) and a level-one stream that starts when
the alert fires. On the first live session (2026-10-05) the two leaned the same
way on 34 of 66 contracts -- a coin flip -- but that was not a fair test: one
covered the whole day and the other only what came after the alert.

``flow_contract_days`` now keeps the poll's tally AT the alert, so this report
compares like with like: poll-since-the-alert against stream-since-the-alert,
over the same window, for every flagged contract both sources measured. If they
agree, the once-a-minute label is a usable sample of the finer one. If they do
not, the estimate on a busy contract is noise and the screens should say less.
Design: docs/plans/2026-10-04-flow-alert-sides-design.md.

USAGE
-----
    .venv/bin/python tools/flow_sides_report.py                    # the newest stored session
    .venv/bin/python tools/flow_sides_report.py --date 2026-10-06
    .venv/bin/python tools/flow_sides_report.py --min 2000         # busier contracts only

Reads ``options-scanner/gex_history.db`` read-only and nothing else (no proxy,
no Redis, no secrets). Prints to stdout; writes nothing. A session stays
available for ``[followup].keep_sessions`` days.
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import sqlite3
import statistics
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import gex_history_db as gh  # noqa: E402

from shared.numeric import finite as _finite  # noqa: E402

KEYS = ("bought", "sold", "unlabelled")

# The fewest contracts BOTH sources must have seen since the alert for a row to
# be compared: below this a lean is one or two trades. An analysis parameter,
# not an alert setting; override per run with --min.
MIN_SINCE = 500


def _tally(row, prefix):
    """``{bought, sold, unlabelled}`` from the row's ``<prefix>_*`` columns, or
    None when any is missing or not a number."""
    vals = [_finite(row.get(f"{prefix}_{k}")) for k in KEYS]
    return None if any(v is None for v in vals) else dict(zip(KEYS, vals))


def since_alert(row):
    """The poll's tally since the alert: the running tally minus the tally at
    the alert. None when the row has no at-alert tally (flagged before the
    columns existed, or before the contract was ever booked)."""
    poll, at = _tally(row, "poll"), _tally(row, "at")
    if poll is None or at is None:
        return None
    return {k: max(poll[k] - at[k], 0.0) for k in KEYS}


def lean(tally):
    """Bought minus sold as a share of the WHOLE tally, so unlabelled volume
    dilutes it. None for an empty tally."""
    total = sum(tally.values())
    return None if total <= 0 else (tally["bought"] - tally["sold"]) / total


def _correlation(xs, ys):
    """Pearson's r, or None with fewer than three points or no spread."""
    if len(xs) < 3:
        return None
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def compare(rows, min_since=MIN_SINCE) -> dict:
    """Compare poll-since-the-alert with stream-since-the-alert, row by row.

    A row is compared only when it has an at-alert tally, a stream tally, and
    at least ``min_since`` contracts since the alert on BOTH. Every other row
    is counted under the reason it was left out. Every summary figure is None
    when there is nothing to compute it from."""
    skipped: collections.Counter = collections.Counter()
    out_rows = []
    for row in rows:
        poll = since_alert(row)
        stream = _tally(row, "stream")
        if poll is None:
            skipped["no at-alert tally"] += 1
            continue
        if stream is None:
            skipped["not streamed"] += 1
            continue
        pt, st = sum(poll.values()), sum(stream.values())
        if pt < min_since or st < min_since:
            skipped["under the minimum since the alert"] += 1
            continue
        out_rows.append({
            "alert_id": row.get("alert_id"), "poll_total": pt, "stream_total": st,
            "coverage": st / pt, "poll_lean": lean(poll), "stream_lean": lean(stream),
            "poll_unlabelled": poll["unlabelled"] / pt,
            "stream_unlabelled": stream["unlabelled"] / st})
    gaps = [abs(r["poll_lean"] - r["stream_lean"]) for r in out_rows]
    same = sum(1 for r in out_rows if r["poll_lean"] * r["stream_lean"] > 0)
    none = sum(1 for r in out_rows if r["poll_lean"] == 0 or r["stream_lean"] == 0)
    return {
        "flagged": len(rows), "compared": len(out_rows), "skipped": dict(skipped),
        "rows": out_rows,
        "same_way": same, "no_lean": none, "opposite": len(out_rows) - same - none,
        "median_gap": statistics.median(gaps) if gaps else None,
        "median_coverage": (statistics.median(r["coverage"] for r in out_rows)
                            if out_rows else None),
        "correlation": _correlation([r["poll_lean"] for r in out_rows],
                                    [r["stream_lean"] for r in out_rows]),
    }


def _pts(x):
    return f"{100 * x:+.1f}"


def render(date, result, min_since) -> str:
    lines = [f"Bought/sold: poll against stream, since the alert — {date}", ""]
    lines.append(f"compared {result['compared']} of {result['flagged']} flagged contracts "
                 f"(at least {min_since:,} contracts since the alert on both sources)")
    for reason, n in sorted(result["skipped"].items()):
        lines.append(f"  left out, {reason}: {n}")
    if not result["compared"]:
        lines += ["", "Nothing to compare."]
        return "\n".join(lines)
    decided = result["same_way"] + result["opposite"]
    lines += [
        "",
        f"stream volume as a share of poll volume: median {100 * result['median_coverage']:.0f}%",
        f"lean the same way: {result['same_way']} of {decided}"
        + (f" ({100 * result['same_way'] / decided:.0f}%)" if decided else "")
        + f"; opposite: {result['opposite']}; no lean on one side: {result['no_lean']}",
        f"median gap between the two leans: {100 * result['median_gap']:.1f} points",
        "correlation of the two leans: "
        + ("not enough rows" if result["correlation"] is None
           else f"{result['correlation']:+.2f}"),
        "",
        "lean = bought minus sold, in points of the whole tally (unlabelled included)",
        "",
        f"{'alert':<46}{'since alert':>12}{'poll':>8}{'stream':>8}{'gap':>7}",
    ]
    for r in sorted(result["rows"],
                    key=lambda r: abs(r["poll_lean"] - r["stream_lean"]), reverse=True):
        lines.append(
            f"{str(r['alert_id'])[:45]:<46}{r['poll_total']:>12,.0f}"
            f"{_pts(r['poll_lean']):>8}{_pts(r['stream_lean']):>8}"
            f"{100 * abs(r['poll_lean'] - r['stream_lean']):>7.1f}")
    return "\n".join(lines)


def _connect(path):
    return sqlite3.connect(f"file:{pathlib.Path(path).as_posix()}?mode=ro", uri=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Compare the poll and stream bought/sold estimates since each alert.")
    p.add_argument("--date", default=None,
                   help="session date YYYY-MM-DD (default: the newest stored session)")
    p.add_argument("--min", type=int, default=MIN_SINCE, dest="min_since",
                   help=f"fewest contracts since the alert on both sources (default {MIN_SINCE})")
    p.add_argument("--db", default=None, help="path to gex_history.db (default: the app's)")
    args = p.parse_args(argv)
    conn = _connect(args.db or gh.DB_PATH)
    try:
        date = args.date or gh.latest_flow_session_before(conn, "9999-12-31")
        if not date:
            print("No flagged contracts are stored.")
            return 0
        rows = gh.load_flow_contract_days(conn, date)
    except sqlite3.OperationalError as e:
        # Read-only, so it cannot create or migrate anything: the options
        # service does that the first time it runs with this code.
        print(f"The store is not ready for this report ({e}). The options "
              "service creates and migrates the table when it next runs.")
        return 0
    finally:
        conn.close()
    print(render(date, compare(rows, args.min_since), args.min_since))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
