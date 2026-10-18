"""Print the Opening Scalp study as Markdown - the verdict the operator reads.

Reads what ``scalp_study.rebuild`` last stored in ``services/options_svc/data/
scalp.db`` (the nightly ``[slots.scalp_bars]`` branch rebuilds it after the bar
top-up), opening the file READ-ONLY: this never creates, migrates or rebuilds
anything. Run on the box:

    .venv/bin/python tools/scalp_study_report.py [path/to/scalp.db]

What it prints, per symbol: the walk-forward Brier score of each role x alignment
bucket against the base rate at every volatility multiplier, with the edge flag,
and whether the edge holds at every multiplier (the only edge the page may show);
the plain rates over every stored morning; up-only and down-only rates and the
pooled-symbol rates, each labelled DIAGNOSTIC (design §5.2: never the number
shown); and the bias check of the 5-minute "both levels in one bar is a loss"
rule against the same trades on 1-minute bars.

Deliberately absent: any P&L average. A win or loss row stores the target or stop
LEVEL, not a fill, so averaging it would print a number the data cannot support.
"""
import datetime as _dt
import json
import pathlib
import sqlite3
import sys
from collections import Counter

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import repo_paths  # noqa: E402
from shared import scalp_config  # noqa: E402
from services.options_svc import scalp_model  # noqa: E402

ROLES = ("long_with", "long_against", "credit_with", "credit_against")
ROLE_WORDS = {"long_with": "Long, with the open", "long_against": "Long, against the open",
              "credit_with": "Credit, with the open",
              "credit_against": "Credit, against the open"}
RESULTS = ("win", "loss", "timeout")


def _open_read_only(path) -> sqlite3.Connection:
    uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _fixed(x, places=3) -> str:
    return "-" if x is None else f"{x:.{places}f}"


def _pct(x) -> str:
    return "-" if x is None else f"{100.0 * x:.1f}%"


def _rate_cells(results: Counter) -> list:
    """``n | win | loss | timeout | win rate | Wilson 95%`` for a tally."""
    n = sum(results[r] for r in RESULTS)
    wins = results["win"]
    if n:
        lo, hi = scalp_model.wilson(wins, n)
        rate, band = _pct(wins / n), f"{_pct(lo)} to {_pct(hi)}"
    else:
        rate, band = "-", "-"
    return [str(n), str(wins), str(results["loss"]), str(results["timeout"]), rate, band]


def _table(header, rows) -> list:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _load(conn):
    conn.row_factory = sqlite3.Row
    outcomes = [dict(r) for r in conn.execute("SELECT * FROM outcomes")]
    mornings = {}
    for r in conn.execute("SELECT day, symbol, json FROM mornings"):
        mornings[(r["day"], r["symbol"])] = json.loads(r["json"])
    verdicts = {}
    for r in conn.execute("SELECT symbol, role, bucket, json FROM verdicts"):
        verdicts[(r["symbol"], r["role"], r["bucket"])] = json.loads(r["json"])
    return outcomes, mornings, verdicts


def _symbol_section(sym, outcomes, mornings, verdicts, current) -> list:
    lines = [f"## {sym}", ""]
    mine = [m for (d, s), m in mornings.items() if s == sym]
    used = sum(1 for m in mine if m.get("skip") is None)
    skips = Counter(m["skip"] for m in mine if m.get("skip"))
    lines.append(f"Mornings stored: {len(mine)}; simulated: {used}; skipped: "
                 + (", ".join(f"{k} {v}" for k, v in sorted(skips.items())) or "none") + ".")
    lines.append("")

    # ── verdict ─────────────────────────────────────────────────────────────
    scales = sorted({float(k) for (s, _r, _b), v in verdicts.items() if s == sym
                     for k in v["scales"]})
    edge_scales = []
    for (s, _r, _b), v in verdicts.items():
        if s == sym:
            edge_scales = v.get("edge_scales", [])
            break
    lines.append("### Walk-forward verdict (5-minute bars)")
    lines.append("")
    lines.append("Each cell: Brier of the bucket's rate / Brier of the base rate, over "
                 "the same scored mornings (n), and `EDGE` when the bucket's is lower "
                 "with n at least the minimum. Lower Brier is better.")
    lines.append("")
    header = ["Role", "Bucket"] + [f"x{s:g}" for s in scales] + ["Edge at every scale"]
    rows, edges_all, edges_some = [], [], []
    for role in ROLES:
        for bucket in scalp_model.BUCKETS:
            v = verdicts.get((sym, role, bucket))
            if v is None:
                continue
            cells = []
            some = []
            for s in scales:
                c = v["scales"].get(str(s))
                if c is None:
                    cells.append("-")
                    continue
                flag = " EDGE" if c["edge"] else ""
                if c["edge"]:
                    some.append(f"x{s:g}")
                cells.append(f"{_fixed(c['brier_bucket'])} / {_fixed(c['brier_base'])} "
                             f"({c['n_scored']}){flag}")
            rows.append([ROLE_WORDS[role], bucket] + cells
                        + ["YES" if v["edge"] else "no"])
            if v["edge"]:
                edges_all.append(f"{ROLE_WORDS[role]} / {bucket}")
            elif some:
                edges_some.append(f"{ROLE_WORDS[role]} / {bucket} (at {', '.join(some)})")
    lines += _table(header, rows) if rows else ["No verdict stored for this symbol."]
    lines.append("")
    across = ", ".join(f"x{s:g}" for s in edge_scales)
    if edges_all:
        lines.append(f"**{sym}: an edge at every multiplier ({across}) for: "
                     + "; ".join(edges_all) + ".**")
    else:
        lines.append(f"**{sym}: no bucket shows an edge at every volatility multiplier "
                     f"({across}). The page would show the base rate with "
                     "\"no measured edge\".**")
    if edges_some:
        lines.append("")
        lines.append("Edges at some multipliers only (an artefact of the volatility "
                     "assumption, not shown on the page): " + "; ".join(edges_some) + ".")
    lines.append("")

    # ── plain rates at the current scale ───────────────────────────────────
    here = [o for o in outcomes if o["symbol"] == sym and o["interval"] == 5
            and o["iv_scale"] == current]
    lines.append(f"### Rates over every stored morning (x{current:g}, 5-minute bars)")
    lines.append("")
    rows = []
    for role in ROLES:
        mine = [o for o in here if o["role"] == role]
        rows.append([ROLE_WORDS[role], "all (base)"]
                    + _rate_cells(Counter(o["result"] for o in mine)))
        for bucket in scalp_model.BUCKETS:
            rows.append([ROLE_WORDS[role], bucket] + _rate_cells(
                Counter(o["result"] for o in mine if o["bucket"] == bucket)))
    lines += _table(["Role", "Bucket", "n", "win", "loss", "timeout", "win rate",
                     "Wilson 95%"], rows)
    under = sum(1 for o in here if o["result"] == "skip")
    if under:
        lines.append("")
        lines.append(f"{under} trade(s) skipped: the modelled entry premium was under "
                     "`min_premium`.")
    lines.append("")

    # ── up-only / down-only ───────────────────────────────────────────────
    lines.append(f"### DIAGNOSTIC - Up-only and Down-only mornings (x{current:g}, "
                 "5-minute bars)")
    lines.append("")
    lines.append("Never the number shown: the study pools both directions by role.")
    lines.append("")
    rows = []
    for role in ROLES:
        for label, sign in (("Up-only", 1), ("Down-only", -1)):
            tally = Counter(o["result"] for o in here if o["role"] == role
                            and (mornings.get((o["day"], sym)) or {}).get("direction") == sign)
            structures = sorted({o["structure"] for o in here if o["role"] == role
                                 and (mornings.get((o["day"], sym)) or {}).get("direction")
                                 == sign})
            rows.append([ROLE_WORDS[role], label, "/".join(structures) or "-"]
                        + _rate_cells(tally))
    lines += _table(["Role", "Mornings", "Structure", "n", "win", "loss", "timeout",
                     "win rate", "Wilson 95%"], rows)
    lines.append("")
    return lines


def _pooled_section(outcomes, current) -> list:
    lines = [f"## DIAGNOSTIC - Pooled symbols (x{current:g}, 5-minute bars)", "",
             "The three symbols trade on one tape, so a pooled n overstates the "
             "evidence about threefold. Never the number shown.", ""]
    here = [o for o in outcomes if o["interval"] == 5 and o["iv_scale"] == current]
    rows = []
    for role in ROLES:
        mine = [o for o in here if o["role"] == role]
        rows.append([ROLE_WORDS[role], "all (base)"]
                    + _rate_cells(Counter(o["result"] for o in mine)))
        for bucket in scalp_model.BUCKETS:
            rows.append([ROLE_WORDS[role], bucket] + _rate_cells(
                Counter(o["result"] for o in mine if o["bucket"] == bucket)))
    lines += _table(["Role", "Bucket", "n", "win", "loss", "timeout", "win rate",
                     "Wilson 95%"], rows)
    lines.append("")
    return lines


def _bias_section(outcomes, current) -> list:
    lines = ["## Bias check - 5-minute vs 1-minute bars", "",
             "The same trades (same morning, strikes, entry premium and bucket) walked "
             "on both bar sizes, on the days that have complete 1-minute bars, at "
             f"x{current:g}. On 5-minute bars a bar that reaches both levels in one "
             "bar is counted a loss; the 1-minute walk shows how often that was wrong.",
             ""]
    five = {(o["day"], o["symbol"], o["role"]): o for o in outcomes
            if o["interval"] == 5 and o["iv_scale"] == current}
    pairs = [(five[(o["day"], o["symbol"], o["role"])], o) for o in outcomes
             if o["interval"] == 1 and (o["day"], o["symbol"], o["role"]) in five]
    pairs = [(a, b) for a, b in pairs if a["result"] != "skip" and b["result"] != "skip"]
    if not pairs:
        lines.append("No overlapping mornings yet.")
        lines.append("")
        return lines
    days = sorted({a["day"] for a, _ in pairs})
    lines.append(f"Overlapping trades: {len(pairs)} over {len(days)} day(s) "
                 f"({days[0]} to {days[-1]}).")
    lines.append("")
    rows = []
    for sym in sorted({a["symbol"] for a, _ in pairs}):
        for role in ROLES:
            mine = [(a, b) for a, b in pairs if a["symbol"] == sym and a["role"] == role]
            if not mine:
                continue
            n = len(mine)
            w5 = sum(1 for a, _ in mine if a["result"] == "win")
            w1 = sum(1 for _, b in mine if b["result"] == "win")
            both = sum(1 for a, _ in mine if a["both_in_bar"])
            rows.append([sym, ROLE_WORDS[role], str(n), _pct(w5 / n), _pct(w1 / n),
                         f"{both} ({_pct(both / n)})"])
    lines += _table(["Symbol", "Role", "n", "5-minute win rate", "1-minute win rate",
                     "both levels in one bar (5-minute)"], rows)
    lines.append("")
    grid = Counter((a["result"], b["result"]) for a, b in pairs)
    lines.append("All overlapping trades, 5-minute result (rows) by 1-minute result "
                 "(columns):")
    lines.append("")
    lines += _table(["5-minute \\ 1-minute"] + list(RESULTS),
                    [[r5] + [str(grid[(r5, r1)]) for r1 in RESULTS] for r5 in RESULTS])
    lines.append("")
    all5 = [o for o in outcomes if o["interval"] == 5 and o["iv_scale"] == current
            and o["result"] in RESULTS]
    both_all = sum(1 for o in all5 if o["both_in_bar"])
    if all5:
        lines.append(f"Across every 5-minute trade at x{current:g}: {both_all} of "
                     f"{len(all5)} ({_pct(both_all / len(all5))}) had both levels in one "
                     "bar and were counted losses.")
        lines.append("")
    return lines


def render(db_path) -> str:
    """The whole report as Markdown. Raises ``sqlite3.OperationalError`` when the
    store is missing (it is opened read-only and never created)."""
    conn = _open_read_only(db_path)
    try:
        outcomes, mornings, verdicts = _load(conn)
    finally:
        conn.close()
    current = round(float(scalp_config.iv_scale()), 6)
    days = sorted({d for d, _s in mornings})
    symbols = list(scalp_config.symbols())
    for _d, s in mornings:
        if s not in symbols:
            symbols.append(s)
    lines = [f"# Opening Scalp study - {_dt.date.today().isoformat()}", ""]
    if not mornings:
        lines += ["No study stored: the nightly rebuild has not run on this store.", ""]
        return "\n".join(lines)
    lines += [
        f"Store: `{db_path}`. Mornings from {days[0]} to {days[-1]} ({len(days)} "
        f"trading days). Alignment cuts {scalp_config.buckets()}; verdict multipliers "
        f"{scalp_config.iv_scales()}; current x{current:g}; minimum scored mornings "
        f"{scalp_config.min_samples()}; target {scalp_config.target_pct():g}% / stop "
        f"{scalp_config.stop_pct():g}% / time stop {scalp_config.time_stop_min()} min.",
        "",
        "Win = the target level was reached first; a timeout and a loss both count 0. "
        "Every rate is a share of modelled trades (Black-Scholes at the volatility "
        "index times the multiplier), not of filled ones. P&L per trade is not "
        "reported: a win or loss row stores the level, not a fill.",
        ""]
    for sym in symbols:
        lines += _symbol_section(sym, outcomes, mornings, verdicts, current)
    lines += _pooled_section(outcomes, current)
    lines += _bias_section(outcomes, current)
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else repo_paths.SCALP_DB
    print(render(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
