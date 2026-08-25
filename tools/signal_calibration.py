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
import math
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import OPTIONS_SCANNER  # noqa: E402

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

CONTRACT_MULTIPLIER = 100.0

GROUP_KEYS = ("entry_grade", "entry_score", "scanner_type", "strategy", "symbol",
              "exit_reason", "dte_at_entry")


# ── the primitives ───────────────────────────────────────────────────────────

def _finite(v):
    """A real finite number, or None.

    Rejects bool (``float(True)`` is 1.0) and NaN. The NaN case is not
    hypothetical here: a NaN survives every ``>`` comparison as False, so it
    would be silently booked as a loss rather than excluded -- which is how the
    five NaN incidents documented in CLAUDE.md all began.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def r_multiple(realized_pnl, entry_max_loss):
    """Realized P&L as a multiple of the dollars risked, or None.

    ``entry_max_loss`` is stored PER SHARE and ``realized_pnl`` in dollars for
    one contract, so the risk is ``entry_max_loss * 100``. This is the only
    normalization that makes a $144 spread and a $1,000 spread comparable.
    """
    pnl, ml = _finite(realized_pnl), _finite(entry_max_loss)
    if pnl is None or ml is None or ml <= 0:
        return None
    return pnl / (ml * CONTRACT_MULTIPLIER)


def breakeven_win_rate(entry_credit, entry_max_loss):
    """The win rate this trade's own price demanded: p* = 1/(1+b).

    With b = credit/max_loss that reduces to ``max_loss / (credit + max_loss)``
    -- i.e. ``max_loss/width``, or ``1 - credit/width``. Below this, the trade
    loses money no matter how good it feels.
    """
    c, ml = _finite(entry_credit), _finite(entry_max_loss)
    if c is None or ml is None or ml <= 0 or (c + ml) <= 0:
        return None
    return ml / (c + ml)


def priced_win_rate(entry_short_delta, strategy=None):
    """The win rate the ENTRY DELTA implied: 1 - |delta|. None when unknowable.

    ⚠ An iron condor is refused. `signal_db` stores a single
    ``entry_short_delta``, and `scanner_engine` writes the SUM of the two shorts
    into it -- which for a symmetric condor is ~0, so ``1 - |d|`` would report a
    confident 100%. The two-sided probability needs both legs
    (``p_pop + c_pop - 100``) and that is not in this table, so the honest answer
    is that we do not know.
    """
    if (strategy or "").strip().upper() == "IC":
        return None
    d = _finite(entry_short_delta)
    if d is None:
        return None
    return 1.0 - abs(d)


def score_bin(score, width=5):
    """A continuous entry_score as a fixed-width bucket label, e.g. ``60-65``."""
    s = _finite(score)
    if s is None or width <= 0:
        return "?"
    lo = int(math.floor(s / width) * width)
    return f"{lo}-{lo + width}"


# ── the statistics ───────────────────────────────────────────────────────────

_EMPTY = {"n": 0, "wins": 0, "losses": 0, "scratches": 0, "realized_p": None,
          "avg_win_r": None, "avg_loss_r": None, "b": None, "ev_r": None,
          "ev_units": None, "breakeven_p": None, "priced_p": None,
          "priced_breakeven_p": None, "edge_pp": None, "t_stat": None,
          "t_day": None, "days": 0, "total_r": None}


def _t_stat(values):
    """t-stat of the mean against zero, or None when it is not defined."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    se = math.sqrt(var / n)
    return (mean / se) if se > 0 else None


def bucket_stats(rows):
    """Every number the report needs for one bucket. Never raises.

    ``ev_r`` is the arithmetic mean R -- the truth, computed without any model.
    ``ev_units`` is the same thing through ``p*b - (1-p)``, stated in units of
    the average loss. They are pinned to each other by test, because a report
    that shows both and lets them drift is worse than one that shows neither.

    ⚠ With a scratch (exactly $0) present the second term is the LOSS FRACTION,
    not ``1-p``. A scratch is neither a win nor a loss, so ``p + loss_frac < 1``
    and the textbook form would overstate the drag. When there are no scratches
    the two are identical.
    """
    rs, priced, priced_be = [], [], []
    by_day = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        rv = r_multiple(r.get("realized_pnl"), r.get("entry_max_loss"))
        if rv is None:
            continue                      # unusable row: excluded, not a loss
        rs.append(rv)
        day = r.get("first_seen_date")
        if day:                           # an undated row joins no day cluster
            by_day.setdefault(str(day), []).append(rv)
        p = priced_win_rate(r.get("entry_short_delta"), r.get("strategy"))
        if p is not None:
            priced.append(p)
        be = breakeven_win_rate(r.get("entry_credit"), r.get("entry_max_loss"))
        if be is not None:
            priced_be.append(be)

    n = len(rs)
    if not n:
        return dict(_EMPTY)

    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    mean_r = sum(rs) / n
    p_hat = len(wins) / n
    loss_frac = len(losses) / n
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None

    b = (avg_win / abs(avg_loss)) if (avg_win is not None and avg_loss) else None
    # p*b - loss_frac, which IS p*b - (1-p) whenever nothing scratched.
    ev_units = (p_hat * b - loss_frac) if b is not None else None
    breakeven_p = (1.0 / (1.0 + b)) if b else None

    priced_p = (sum(priced) / len(priced)) if priced else None
    edge_pp = ((p_hat - priced_p) * 100.0) if priced_p is not None else None

    # Two t-stats on purpose. The naive one treats every signal as an
    # independent bet, which it plainly is not -- one scan emits a dozen at once
    # and they share a tape. t_day is computed over PER-DAY mean R, so a day that
    # fired twenty correlated signals counts once. Read t_day.
    t_stat = _t_stat(rs)
    t_day = _t_stat([sum(v) / len(v) for v in by_day.values()])

    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "scratches": n - len(wins) - len(losses),
        "days": len(by_day),
        "realized_p": p_hat,
        "avg_win_r": avg_win, "avg_loss_r": avg_loss, "b": b,
        "ev_r": mean_r, "ev_units": ev_units,
        "total_r": sum(rs),
        "breakeven_p": breakeven_p,
        "priced_p": priced_p,
        "priced_breakeven_p": (sum(priced_be) / len(priced_be)) if priced_be else None,
        "edge_pp": edge_pp,
        "t_stat": t_stat, "t_day": t_day,
    }


def calibrate(rows, key="entry_grade", min_n=1):
    """Group the joined rows and report each bucket. Sorted by bucket name.

    Name order is deliberate rather than "best first": the whole point is to read
    DOWN a monotone axis (grade, or a score bin) and see whether the numbers move
    with it. Sorting by EV would hide exactly the pattern being looked for.
    """
    groups = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if r_multiple(r.get("realized_pnl"), r.get("entry_max_loss")) is None:
            continue
        groups.setdefault(_bucket_of(r, key), []).append(r)

    out = []
    for name, rs in groups.items():
        s = bucket_stats(rs)
        if s["n"] < min_n:
            continue
        out.append({"bucket": name, **s})
    return sorted(out, key=lambda b: str(b["bucket"]))


def split_calibrate(rows, key="entry_score", split_by="scanner_type", min_n=1):
    """``[(split_value, buckets), ...]`` -- the ``calibrate`` breakdown computed
    SEPARATELY within each value of ``split_by``.

    0-DTE and swing are different games: a 0-DTE spread has hours of gamma risk
    and no room to recover, a 14-DTE one has both. Pooling them reports the mean
    of two populations that need not share a score gate at all.

    ⚠ ``min_n`` applies WITHIN a split, which is the whole point -- six trades
    that clear a floor pooled may be three and three once split, and neither side
    is then worth reporting. A split value left with no surviving bucket is
    dropped rather than printed empty.
    """
    groups = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        groups.setdefault(_bucket_of(r, split_by), []).append(r)

    out = []
    for name in sorted(groups, key=str):
        buckets = calibrate(groups[name], key, min_n)
        if buckets:
            out.append((name, buckets))
    return out


def _bucket_of(row, key):
    if key == "entry_score":
        return score_bin(row.get("entry_score"))
    v = row.get(key)
    return "?" if v is None or v == "" else str(v)


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
