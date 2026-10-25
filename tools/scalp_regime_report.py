"""Print the Opening Scalp's net-gamma regime study as Markdown.

The study asks whether the 15-minute calls' long trades reach their target more often
when the dealers' net gamma was negative than when it was positive, judged by the
verdict declared in advance (design 2026-10-25-scalp-gamma-regime-study). This reads
``services/options_svc/data/scalp.db`` OPENED READ-ONLY: it never creates, migrates or
writes anything. Run on the box:

    .venv/bin/python tools/scalp_regime_report.py [scalp.db] [--agreement agree.json]

What it prints:

* before the declared number of usable sessions has been graded, ONLY the count of
  usable sessions - no effect, no p-value, no pass or fail (design section 3.4);
* after it, the first that many sessions, the precondition (the other tool's check
  that the recorded regime agrees with the regime read from the stored matrix,
  given as ``--agreement`` JSON ``{"share": ..., "sessions": ...}``; a missing or
  unreadable file is "not checked", and no block can pass unchecked), and for each
  block in the declared order its verdict, the effect at every volatility
  multiplier, the permutation p-value, the pair counts and the figures reported
  but never decided on.

The regime is read from ``intraday_readings`` only; ``prob_sources`` is never read.
"""
import argparse
import datetime as _dt
import json
import pathlib
import sqlite3
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import repo_paths  # noqa: E402
from shared import scalp_config  # noqa: E402
from services.options_svc import scalp_regime_study  # noqa: E402

FAIL_NOTE = ("A fail does not show there is no effect, only that it is not as large "
             "as it first looked.")


def _open_read_only(path) -> sqlite3.Connection:
    uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _pp(x) -> str:
    return "-" if x is None else f"{x:+.2f} pp"


def _share(x) -> str:
    return "-" if x is None else f"{100.0 * x:.2f}%"


def _p(p) -> str:
    """A p-value of exactly 0 means no relabeling reached the effect: it is printed
    as below the smallest value ``n`` relabelings can tell apart."""
    if p is None:
        return "-"
    if p == 0:
        return f"< {1.0 / scalp_config.regime_permutations():.4f}"
    return f"{p:.4f}"


def _table(header, rows) -> list:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _agreement(path):
    """The agreement JSON, or ``None`` (not checked) when there is no usable file."""
    if path is None:
        return None
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _precondition_line(pre) -> str:
    if not pre["checked"]:
        return "Precondition not checked: no block can pass."
    share = "-" if pre["share"] is None else f"{pre['share']:.2%}"
    if pre["passed"]:
        return (f"Precondition: passed ({share} of slots agree over "
                f"{pre['sessions']} sessions).")
    return f"Precondition failed ({share} of slots agree): no block can pass."


def _block_lines(block) -> list:
    name = block["block"]
    if not block["tested"]:
        reasons = "; ".join(r.removeprefix("not tested: ") for r in block["reasons"])
        return [f"**{name}: not tested** ({reasons})", ""]
    if block["passed"]:
        lines = [f"**{name}: PASS**", ""]
    else:
        lines = [f"**{name}: FAIL** ({'; '.join(block['reasons'])})", "", FAIL_NOTE, ""]
    rep = block["reported"]
    lines += _table(["Multiplier", "Negative minus positive gamma"],
                    [[f"x{scale}", _pp(value)] for scale, value in block["effect_pp"].items()])
    lines += ["", f"p-value: {_p(block['p'])}. Pairs (day and symbol): "
              f"{block['negative_pairs']} negative gamma, {block['positive_pairs']} positive.",
              "", "Reported, never decided", ""]
    rows = [[f"{sym} alone", _pp(v)] for sym, v in rep["per_symbol"].items()]
    rows += [["Credit trades", _pp(rep["credit_pp"])],
             ["Long, with the move", _pp(rep["long_with_pp"])],
             ["Long, against the move", _pp(rep["long_against_pp"])],
             ["Timeouts, negative gamma", _share(rep["timeout_share"]["negative"])],
             ["Timeouts, positive gamma", _share(rep["timeout_share"]["positive"])]]
    lines += _table(["Figure", "Value"], rows)
    lines.append("")
    return lines


def render(db_path, agreement_path=None, today=None) -> str:
    """The whole report as Markdown. Raises ``sqlite3.OperationalError`` when the
    store is missing (it is opened read-only and never created)."""
    today = _dt.date.today() if today is None else today
    conn = _open_read_only(db_path)
    try:
        readings = scalp_regime_study.load_readings(conn)
        outcomes = scalp_regime_study.load_outcomes(conn)
    finally:
        conn.close()
    result = scalp_regime_study.evaluate(
        scalp_regime_study.regimes(readings), outcomes, _agreement(agreement_path))
    lines = [f"# Gamma regime study - {today.isoformat()}", ""]
    if result["status"] == "collecting":
        needed = result["needed"]
        lines += [f"Collecting: {result['sessions']} of {needed} usable sessions since "
                  f"{result['start_day']}. First usable session: "
                  f"{result['first'] or 'none yet'}.",
                  "",
                  f"Nothing else is shown until the {needed}th usable session is graded "
                  "(design §3.4).", ""]
        return "\n".join(lines)
    sessions = result["sessions"]
    lines += [f"Sessions: {sessions[0]} to {sessions[-1]} ({len(sessions)}).", "",
              _precondition_line(result["precondition"]), ""]
    for block in result["blocks"]:
        lines += _block_lines(block)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scalp_regime_report.py")
    parser.add_argument("db", nargs="?", default=repo_paths.SCALP_DB)
    parser.add_argument("--agreement", default=None, metavar="FILE")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    print(render(args.db, agreement_path=args.agreement))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
