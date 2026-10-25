"""Check that the gamma regime read from a backfilled matrix matches the live one.

The Opening Scalp's net-gamma regime study (design 2026-10-25-scalp-gamma-regime-study)
decides on the regime RECORDED live with each 15-minute call. Its precondition is that
the regime read from a stored gamma matrix, the way history is rebuilt, has the same
sign as the recorded one on the test-window sessions both cover. Two steps:

    # 1. over a gamma copy holding test-window sessions (writes one file)
    python tools/scalp_prob_backfill.py extract --gamma \\
        E:/TradingBackups/prod_2026-10-23_2001/options-scanner/gex_history.db \\
        --out history.jsonl

    # 2. compare it with the live record (reads the store; writes agree.json)
    python tools/scalp_regime_agree.py --in history.jsonl --db scalp.db --out agree.json

The ``--db`` store is opened read-only (``?mode=ro``, safe while the service writes it)
and only ``intraday_readings`` is read: this reads gamma and the live record, never an
outcome. ``agree.json`` is what ``tools/scalp_regime_report.py --agreement`` takes.

A slot is compared when it is an ``intraday`` record on or after the declared start day,
its slot has a block (the 08:36 record has none), and both regimes are readable. Exit
status is 0 whatever the result (a failed check is a finding), 2 when an input file or
the store cannot be opened.
"""
import argparse
import json
import pathlib
import sqlite3
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import scalp_prob_backfill as bf  # noqa: E402
from services.options_svc import scalp_regime_study  # noqa: E402
from shared import scalp_config  # noqa: E402

_INTRADAY = "intraday"


def _open_read_only(path) -> sqlite3.Connection:
    """The live store, opened so it can neither be written nor created. Not
    ``immutable=1`` (``bf.open_readonly``): that is only safe on a copy nobody
    writes, and the live store is being written while this runs."""
    uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def compare(records, readings) -> dict:
    """Compare ``records`` (as ``bf._read_records`` returns them) with ``readings``
    (``scalp_regime_study.load_readings``'s mapping). See the module docstring."""
    start = scalp_config.regime_start_day().isoformat()
    early = scalp_config.early_close_days()
    copies = {}                    # (day, slot, symbol) -> the non-gap backfill regimes
    for (kind, day, slot, symbol), snapshot in records:
        if kind != _INTRADAY or day < start or scalp_regime_study._is_early_close(day, early):
            continue
        if scalp_config.intraday_block(slot) is None:
            continue
        regime = scalp_regime_study.regime_of(snapshot, symbol)
        if regime is not None:
            copies.setdefault((day, slot, symbol), set()).add(regime)
    slots = agreeing = 0
    days, disagreeing = set(), []
    for (day, slot, symbol), found in copies.items():
        body = readings.get((day, slot, symbol))
        live = scalp_regime_study.regime_of(
            body.get("snapshot") if isinstance(body, dict) else None, symbol)
        if live is None:
            continue
        slots += 1
        days.add(day)
        if found == {live}:
            agreeing += 1
        else:
            # Copies that disagree with each other are a disagreement, whatever their order.
            backfill = next(iter(found)) if len(found) == 1 else "conflicting"
            disagreeing.append([day, slot, symbol, live, backfill])
    share = agreeing / slots if slots else None
    passed = (share is not None
              and share >= scalp_config.regime_min_sign_agreement()
              and len(days) >= scalp_config.regime_agree_min_sessions())
    return {"sessions": len(days), "slots": slots, "agreeing": agreeing, "share": share,
            "days": sorted(days),
            "disagreeing": sorted(disagreeing, key=lambda r: (r[0], r[1], r[2])),
            "passed": passed}


def _plural(n, word) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def summary(result, bad_lines=0) -> str:
    """One paragraph: what was compared, the share agreeing, the thresholds, the verdict."""
    share = "no share (no slot could be compared)" if result["share"] is None \
        else f"{100.0 * result['share']:.2f}% of them agree"
    text = (f"Compared {_plural(result['sessions'], 'session')} and "
            f"{_plural(result['slots'], 'slot')}: {share}. The check needs at least "
            f"{100.0 * scalp_config.regime_min_sign_agreement():.2f}% agreeing over at "
            f"least {_plural(scalp_config.regime_agree_min_sessions(), 'session')}; "
            f"it {'passed' if result['passed'] else 'did not pass'}.")
    if bad_lines:
        text += f" {_plural(bad_lines, 'unreadable line')} in the input files {'was' if bad_lines == 1 else 'were'} skipped."
    return text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scalp_regime_agree.py")
    parser.add_argument("--in", dest="inputs", nargs="+", required=True, metavar="FILE")
    parser.add_argument("--db", required=True, metavar="PATH")
    parser.add_argument("--out", required=True, metavar="FILE.json")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    records, bad = [], 0
    for path in args.inputs:
        try:
            good, bad_here = bf._read_records(path)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"cannot read {path}: {exc}", file=sys.stderr)
            return 2
        records += good
        bad += bad_here
    try:
        conn = _open_read_only(args.db)
        try:
            readings = scalp_regime_study.load_readings(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"cannot open {args.db} read-only: {exc}", file=sys.stderr)
        return 2
    result = compare(records, readings)
    try:
        pathlib.Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"cannot write {args.out}: {exc}", file=sys.stderr)
        return 2
    print(summary(result, bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
