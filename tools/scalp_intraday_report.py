"""Print the intraday scalp study as Markdown - the verdict the operator reads.

Reads what ``scalp_intraday_study.rebuild`` last stored in ``services/options_svc/
data/scalp.db`` (the nightly ``[slots.scalp_bars]`` branch rebuilds it after the
08:36 study), opening the file READ-ONLY: this never creates, migrates or rebuilds
anything. Run on the box:

    .venv/bin/python tools/scalp_intraday_report.py [path/to/scalp.db]

What it prints:

* the headline, "k of N cells pass at every scale": a cell is one (symbol, time
  block, role, alignment bucket), N counts the cells with any scored day, and a
  cell passes when its walk-forward Brier beats the base rate's by at least
  ``[intraday] min_brier_margin``, over at least ``min_samples`` DAYS, at every
  multiplier in ``iv_scales``. That gate is read back from the stored verdicts
  (each records the margin, the minimum and the scales it was judged under), never
  from a config that may have changed since the rebuild. Beside it, how many
  would pass with no edge at all: the same verdicts with each cell's bucket
  sequence ROTATED against its outcomes (the buckets' clustering kept, their link
  to the outcomes cut) - the mean over a fixed set of rotations, the most in any
  one, and how many rotations reach k. A rough estimate of chance (the cells are
  strongly correlated), not a test;
* for each passing cell, its smallest Brier margin, its bucket's share of the
  block's rows, and its rows per slot beside the block's, so a bucket that only
  tells the time of day is visible;
* the base rate of every role in every block (the number the page shows when
  there is no edge), with its Wilson interval taken over DAYS;
* per symbol, every block x role x bucket's Brier against the base at each
  multiplier with its count of scored days, the edge flag, and a plain "no edge"
  line when none passes;
* up-only and down-only rates, labelled DIAGNOSTIC (never the number shown: the
  study pools both directions by role).

The day is the unit throughout (design 2026-10-20-scalp-intraday-calls §4): rows
of one day share a tape, so every ``n`` here is distinct days and every interval
is computed on days. Deliberately absent: any P&L average - a win or loss row
stores the level, not a fill, and the intraday table keeps no P&L at all.
"""
import datetime as _dt
import json
import pathlib
import sqlite3
import sys
from collections import Counter, defaultdict

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import repo_paths  # noqa: E402
from shared import scalp_config  # noqa: E402
from services.options_svc import scalp_model, scalp_sim, scalp_study  # noqa: E402

#: The roles, the label rule and each role's structure after an UP move are the
#: study's own (a down move mirrors the structures): never a copy here.
ROLES = scalp_sim.ROLES
_UP_STRUCTURE = scalp_sim._UP
_label = scalp_study._label
ROLE_WORDS = {"long_with": "Long, with the move", "long_against": "Long, against the move",
              "credit_with": "Credit, with the move",
              "credit_against": "Credit, against the move"}
BLOCK_ORDER = ("morning", "midday", "afternoon")
#: Rotations averaged for the chance estimate (evenly spaced offsets: no RNG).
ROTATIONS = 20


def _open_read_only(path) -> sqlite3.Connection:
    uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _fixed(x, places=3) -> str:
    return "-" if x is None else f"{x:.{places}f}"


def _pct(x) -> str:
    return "-" if x is None else f"{100.0 * x:.1f}%"


def _table(header, rows) -> list:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _load(conn):
    conn.row_factory = sqlite3.Row
    outcomes = [dict(r) for r in conn.execute("SELECT * FROM intraday_outcomes")]
    verdicts = {}
    for r in conn.execute("SELECT symbol, block, role, bucket, json FROM intraday_verdicts"):
        verdicts[(r["symbol"], r["block"], r["role"], r["bucket"])] = json.loads(r["json"])
    return outcomes, verdicts


def _blocks(verdicts, outcomes) -> list:
    seen = {k[1] for k in verdicts} | {o["block"] for o in outcomes}
    return [b for b in BLOCK_ORDER if b in seen] + sorted(seen - set(BLOCK_ORDER))


def _rate_cells(rows) -> list:
    """``rows | days | win rate | Wilson 95% (days)`` for labelled outcome rows."""
    labelled = [o for o in rows if _label(o["result"]) is not None]
    n = len(labelled)
    days = len({o["day"] for o in labelled})
    if not n:
        return ["0", "0", "-", "-"]
    wins = sum(_label(o["result"]) for o in labelled)
    lo, hi = scalp_model.wilson_days(wins, n, days)
    return [str(n), str(days), _pct(wins / n), f"{_pct(lo)} to {_pct(hi)}"]


def _scored(v) -> bool:
    return any(c.get("n_scored", 0) > 0 for c in v["scales"].values())


def _gate(verdicts):
    """``(min_samples, min_margin, study_scales)`` the stored verdicts were judged
    under: read back from them, so a config changed since the rebuild cannot
    make the header, k or the chance estimate disagree with the stored edges.
    Config's values only for a store whose verdicts do not say (or disagree)."""
    def one(key, fallback):
        seen = {json.dumps(v.get(key)) for v in verdicts.values()} - {"null"}
        return json.loads(seen.pop()) if len(seen) == 1 else fallback
    min_n = one("min_samples", scalp_config.min_samples())
    margin = one("min_margin", scalp_config.min_brier_margin())
    scales = one("edge_scales", list(scalp_config.iv_scales()))
    return int(min_n), float(margin), [round(float(s), 6) for s in scales]


# ── chance ───────────────────────────────────────────────────────────────────
def _chance(outcomes, cells, study_scales, min_n, margin) -> list:
    """Per rotation, the count of ``cells`` passing at every study scale once
    each (symbol, block, role)'s bucket sequence is rotated against its outcomes
    (``ROTATIONS`` counts)."""
    groups = defaultdict(list)
    for o in outcomes:
        if _label(o["result"]) is not None:
            groups[(o["symbol"], o["block"], o["role"])].append(o)
    wanted = defaultdict(set)
    for sym, block, role, bucket in cells:
        wanted[(sym, block, role)].add(bucket)
    counts = []
    for i in range(ROTATIONS):
        total = 0
        for key, buckets in wanted.items():
            rows = groups.get(key, [])
            slots = sorted({(o["day"], o["slot"]) for o in rows})
            if len(slots) < 2:
                continue
            bucket_at = {}
            for o in rows:
                bucket_at[(o["day"], o["slot"])] = o["bucket"]
            seq = [bucket_at[s] for s in slots]
            shift = 1 + (len(slots) - 1) * (i + 1) // (ROTATIONS + 1)
            rotated = dict(zip(slots, seq[shift:] + seq[:shift]))
            by_scale = {}
            for scale in study_scales:
                stream = sorted(
                    ({"day": o["day"], "slot": o["slot"],
                      "bucket": rotated[(o["day"], o["slot"])], "win": _label(o["result"])}
                     for o in rows if round(float(o["iv_scale"]), 6) == scale),
                    key=lambda r: (r["day"], r["slot"]))
                by_scale[scale] = scalp_model.verdict_by_day(
                    scalp_model.walk_forward(stream), stream, min_n, min_margin=margin)
            across = scalp_model.edge_across_scales(by_scale)
            total += sum(1 for b in buckets if across.get(b))
        counts.append(total)
    return counts


def _slot_table(cell_slots: Counter, block_slots: Counter) -> list:
    """Rows per slot for one passing cell beside its role's whole block: a bucket
    that only proxies the time of day shows up as a lopsided cell column."""
    cell_n, block_n = sum(cell_slots.values()), sum(block_slots.values())
    rows = [[slot, str(cell_slots[slot]),
             _pct(cell_slots[slot] / cell_n if cell_n else None),
             str(block_slots[slot]),
             _pct(block_slots[slot] / block_n if block_n else None)]
            for slot in sorted(block_slots)]
    return ["  " + line for line in _table(
        ["Slot", "cell rows", "cell share", "block rows", "block share"], rows)]


# ── sections ─────────────────────────────────────────────────────────────────
def _summary(outcomes, verdicts, gate) -> list:
    min_n, margin, study_scales = gate
    cells = {k: v for k, v in verdicts.items() if _scored(v)}
    passed = sorted(k for k, v in cells.items() if v["edge"])
    across = ", ".join(f"x{s:g}" for s in study_scales)
    lines = ["## Summary", "",
             f"**{len(passed)} of {len(cells)} cells pass at every scale** ({across}). "
             f"A cell is one symbol x time block x role x alignment bucket; "
             f"{len(verdicts)} are defined and {len(cells)} have any scored day.", ""]
    if cells:
        counts = _chance(outcomes, list(cells), study_scales, min_n, margin)
        mean = sum(counts) / len(counts)
        hits = sum(1 for c in counts if c >= len(passed))
        lines += [f"With no edge anywhere, about {mean:.1f} of {len(cells)} would pass "
                  f"(at most {max(counts)} in any one rotation; {hits} of {len(counts)} "
                  f"rotations pass at least {len(passed)}): the same verdicts with each "
                  "cell's bucket sequence rotated against its outcomes. A rough "
                  "estimate of chance, not a significance test: the cells are strongly "
                  "correlated (three symbols on one tape, shared buckets), so the count "
                  "of passes swings far more than independent cells would.", ""]
    if passed:
        lines += ["Passing cells, each with its SMALLEST Brier margin over the base "
                  "across the multipliers, the share of the role's rows in the block "
                  "that its bucket holds, and its rows per slot beside the block's. A "
                  "bucket holding nearly every row IS nearly the base rate, and a "
                  "bucket whose rows sit in a few slots may only be telling the time "
                  "of day:", ""]
        current = round(float(scalp_config.iv_scale()), 6)
        role_rows, cell_rows = Counter(), Counter()
        role_slots, cell_slots = defaultdict(Counter), defaultdict(Counter)
        for o in outcomes:
            if _label(o["result"]) is not None and round(float(o["iv_scale"]), 6) == current:
                role_key = (o["symbol"], o["block"], o["role"])
                cell_key = role_key + (o["bucket"],)
                role_rows[role_key] += 1
                cell_rows[cell_key] += 1
                role_slots[role_key][o["slot"]] += 1
                cell_slots[cell_key][o["slot"]] += 1
        for s, b, r, k in passed:
            v = verdicts[(s, b, r, k)]
            gap = min(c["brier_base"] - c["brier_bucket"]
                      for sc, c in v["scales"].items()
                      if round(float(sc), 6) in study_scales)
            total = role_rows[(s, b, r)]
            share = cell_rows[(s, b, r, k)] / total if total else None
            lines.append(f"- {s} {b} / {ROLE_WORDS[r]} / {k}: smallest Brier margin "
                         f"{gap:.5f}; the bucket holds {_pct(share)} of the role's "
                         f"labelled rows in the block (x{current:g}).")
            lines.append("")
            lines += _slot_table(cell_slots[(s, b, r, k)], role_slots[(s, b, r)])
            lines.append("")
    else:
        lines.append("No cell passes at every scale: every call would show its base "
                     "rate with \"no measured edge\".")
    lines.append("")
    return lines


def _base_section(outcomes, symbols, blocks, current) -> list:
    lines = [f"## Base rates by block (x{current:g})", "",
             "Every labelled row of the role in the block: what the page shows when "
             "no bucket has an edge. The interval is Wilson with n = days.", ""]
    here = [o for o in outcomes if round(float(o["iv_scale"]), 6) == current]
    rows = []
    for sym in symbols:
        for block in blocks:
            for role in ROLES:
                mine = [o for o in here if o["symbol"] == sym and o["block"] == block
                        and o["role"] == role]
                rows.append([sym, block, ROLE_WORDS[role]] + _rate_cells(mine))
    lines += _table(["Symbol", "Block", "Role", "rows", "days", "win rate",
                     "Wilson 95% (days)"], rows)
    skips = sum(1 for o in here if o["result"] == "skip")
    if skips:
        lines += ["", f"{skips} trade(s) at x{current:g} skipped: the modelled entry "
                  "premium was under `min_premium` (most of them late in the day)."]
    lines.append("")
    return lines


def _symbol_section(sym, outcomes, verdicts, blocks, scales, current) -> list:
    lines = [f"## {sym}", "", "### Walk-forward verdict (5-minute bars, the day as the unit)",
             "", "Each cell: Brier of the bucket's rate / Brier of the base rate over "
             "the same scored rows, (n) the DAYS those rows came from, and `EDGE` when "
             "the bucket's is lower by at least the margin with n at least the minimum. "
             "Lower Brier is better.",
             ""]
    header = ["Block", "Role", "Bucket"] + [f"x{s:g}" for s in scales] \
        + ["Edge at every scale"]
    rows, edges = [], []
    for block in blocks:
        for role in ROLES:
            for bucket in scalp_model.BUCKETS:
                v = verdicts.get((sym, block, role, bucket))
                if v is None:
                    continue
                cells = []
                for s in scales:
                    c = v["scales"].get(str(s))
                    if c is None:
                        cells.append("-")
                        continue
                    flag = " EDGE" if c["edge"] else ""
                    cells.append(f"{_fixed(c['brier_bucket'])} / "
                                 f"{_fixed(c['brier_base'])} ({c['n_scored']} days){flag}")
                rows.append([block, ROLE_WORDS[role], bucket] + cells
                            + ["YES" if v["edge"] else "no"])
                if v["edge"]:
                    edges.append(f"{block} / {ROLE_WORDS[role]} / {bucket}")
    lines += _table(header, rows) if rows else ["No verdict stored for this symbol."]
    lines.append("")
    if edges:
        lines.append(f"**{sym}: an edge at every multiplier for: " + "; ".join(edges)
                     + ".**")
    else:
        lines.append(f"**{sym}: no edge. No block, role and bucket beats the base rate "
                     "at every volatility multiplier; its calls would show the base rate "
                     "with \"no measured edge\".**")
    lines.append("")

    # ── up-only / down-only ───────────────────────────────────────────────
    here = [o for o in outcomes if o["symbol"] == sym
            and round(float(o["iv_scale"]), 6) == current]
    lines += [f"### DIAGNOSTIC - Up-only and Down-only slots (x{current:g})", "",
              "Never the number shown: the study pools both directions by role.", ""]
    rows = []
    for block in blocks:
        for role in ROLES:
            for label, up in (("Up-only", True), ("Down-only", False)):
                mine = [o for o in here if o["block"] == block and o["role"] == role
                        and (o["structure"] == _UP_STRUCTURE[role]) == up]
                structures = "/".join(sorted({o["structure"] for o in mine})) or "-"
                rows.append([block, ROLE_WORDS[role], label, structures]
                            + _rate_cells(mine))
    lines += _table(["Block", "Role", "Slots", "Structure", "rows", "days", "win rate",
                     "Wilson 95% (days)"], rows)
    lines.append("")
    return lines


def render(db_path) -> str:
    """The whole report as Markdown. Raises ``sqlite3.OperationalError`` when the
    store is missing (it is opened read-only and never created)."""
    conn = _open_read_only(db_path)
    try:
        outcomes, verdicts = _load(conn)
    finally:
        conn.close()
    current = round(float(scalp_config.iv_scale()), 6)
    lines = [f"# Intraday scalp study - {_dt.date.today().isoformat()}", ""]
    if not verdicts:
        lines += ["No study stored: the nightly rebuild has not run on this store.", ""]
        return "\n".join(lines)
    days = sorted({o["day"] for o in outcomes})
    symbols = list(scalp_config.symbols())
    for k in verdicts:
        if k[0] not in symbols:
            symbols.append(k[0])
    blocks = _blocks(verdicts, outcomes)
    gate = _gate(verdicts)
    min_n, margin, study_scales = gate
    scales = sorted({float(s) for v in verdicts.values() for s in v["scales"]})
    span = f"from {days[0]} to {days[-1]} ({len(days)} trading days)" if days else "none"
    lines += [
        f"Store: `{db_path}`. Days with a simulated slot: {span}. Slots "
        f"{scalp_config.intraday_slots(None)[0]} to {scalp_config.intraday_slots(None)[-1]} "
        f"CT every 15 minutes in blocks {', '.join(blocks)}. Alignment cuts "
        f"{scalp_config.buckets()}; verdict multipliers "
        f"{', '.join(f'x{x:g}' for x in study_scales)}; "
        f"current x{current:g}; minimum scored DAYS {min_n}; an edge "
        f"needs a Brier margin {margin:g} below the base at every multiplier; target "
        f"{scalp_config.target_pct():g}% / stop {scalp_config.stop_pct():g}% / time stop "
        f"{scalp_config.time_stop_min()} min.",
        "",
        "Win = the target level was reached first; a timeout and a loss both count 0. "
        "Every rate is a share of modelled trades (Black-Scholes at the volatility "
        "index times the multiplier), not of filled ones. The day is the unit: up to "
        "21 slots a day share one tape, so every n below counts days, and a row is "
        "predicted only from earlier days.",
        ""]
    lines += _summary(outcomes, verdicts, gate)
    lines += _base_section(outcomes, symbols, blocks, current)
    for sym in symbols:
        lines += _symbol_section(sym, outcomes, verdicts, blocks, scales, current)
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else repo_paths.SCALP_DB
    print(render(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
