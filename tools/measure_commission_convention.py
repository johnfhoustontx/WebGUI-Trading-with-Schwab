#!/usr/bin/env python
"""What would change if commissions were netted everywhere? (gap assessment A7)

A7 proposes one commission convention: realized P&L is already NET in the paper
account and driver books but GROSS in captured signals and the ledger, and the
Market Scanner ranks on a gross ``rr_pct``. The stated rationale was that "the
calibration's R-multiples inherit the gross figure, and commissions fall hardest
on the lowest-scoring bucket".

**Measured 2026-09-11 against prod, that rationale does not hold** — so this
script exists to keep the figures re-runnable rather than remembered, the same
reason ``sweep_naked_capeff.py`` does. Quote its numbers WITH the date and the
sample size: both move as the book grows.

Two independent halves, because they have different answers:

* ``--calibration`` re-computes every closed captured signal's R gross and net
  and prints the per-bucket means. On 910 outcomes the drop was a near-uniform
  **-0.019R** (per bucket -0.017 to -0.031), it reordered NO bucket, and it
  changed no conclusion. The drop tracks SPREAD WIDTH, not score — the denominator
  is ``entry_max_loss``, so a narrow spread pays a larger fraction — which is why
  the "hardest on the lowest-scoring bucket" claim is wrong: the largest drop
  landed on the HIGHEST bucket.
* ``--ranking`` re-ranks the current scan on gross vs net ``rr_pct``. The order
  DOES change, and the bias is systematic: a $1-wide spread lost up to 3.82
  points of rr_pct against 0.70 for a wider one. Netting the ranking therefore
  pushes selection toward WIDER spreads, which costs more dollars of risk per
  contract under the same $250 cap. That is a selection-policy change and belongs
  with A6 (width sizing), not inside a consistency fix.

Read-only: it opens ``signals.db`` for reading and the Redis scan view, computes,
and prints. It writes nothing and places no order.

    python tools/measure_commission_convention.py --calibration --ranking
"""
import argparse
import pathlib
import sqlite3
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from repo_paths import OPTIONS_SCANNER  # noqa: E402

sys.path.insert(0, str(OPTIONS_SCANNER))

from shared import calibration  # noqa: E402
from services.options_svc import commission  # noqa: E402

MIN_BUCKET_N = 5     # below this a bucket mean is noise, not a reading


def _round_trip(strategy, symbol):
    """Round-trip commission for ONE contract, or 0.0 when unpriceable.

    0.0 rather than a skip: a structure the commission model does not know is a
    structure that pays nothing in this comparison, which understates the drop
    instead of hiding the row. It has never fired on real data.
    """
    try:
        return commission.round_trip_commission(strategy, symbol, 1) or 0.0
    except Exception:
        return 0.0


def _outcomes(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT s.scanner_type, s.strategy, s.symbol, s.entry_score,"
            "       s.entry_max_loss, o.realized_pnl"
            "  FROM signal_outcomes o JOIN signals s ON s.signal_id = o.signal_id"
            " WHERE o.realized_pnl IS NOT NULL AND s.entry_max_loss IS NOT NULL")]
    finally:
        conn.close()


def calibration_impact(db_path):
    """Per-bucket mean R, gross vs net. Returns the rows it printed."""
    rows = _outcomes(db_path)
    print(f"closed captured signals with an outcome: {len(rows)}")
    if not rows:
        return []
    buckets: dict = {}
    pairs = []
    for r in rows:
        fee = _round_trip(r["strategy"], r["symbol"])
        gross = calibration.r_multiple(r["realized_pnl"], r["entry_max_loss"])
        net = calibration.r_multiple(r["realized_pnl"] - fee, r["entry_max_loss"])
        if gross is None or net is None:
            continue
        pairs.append((gross, net))
        key = calibration.bucket_key(r["scanner_type"], r["entry_score"])
        buckets.setdefault(key, ([], []))
        buckets[key][0].append(gross)
        buckets[key][1].append(net)
    fees = [_round_trip(r["strategy"], r["symbol"]) for r in rows]
    print(f"round-trip commission per contract: ${min(fees):.2f}-${max(fees):.2f}")
    print()
    print(f"{'bucket':<16} {'n':>5} {'meanR gross':>12} {'meanR net':>11} {'drop':>8}")
    ordered = []
    for key in sorted(b for b in buckets if b is not None):
        gross, net = buckets[key]
        if len(gross) < MIN_BUCKET_N:
            continue
        mg, mn = st.mean(gross), st.mean(net)
        ordered.append((key, mg, mn))
        print(f"{key:<16} {len(gross):>5} {mg:>+12.3f} {mn:>+11.3f} {mn - mg:>+8.3f}")
    if pairs:
        mg = st.mean(p[0] for p in pairs)
        mn = st.mean(p[1] for p in pairs)
        print(f"{'ALL':<16} {len(pairs):>5} {mg:>+12.3f} {mn:>+11.3f} {mn - mg:>+8.3f}")
    # The conclusion that matters is whether the ORDER of the buckets moves,
    # because that is what any selection rule reads. A uniform shift does not.
    by_gross = [k for k, _g, _n in sorted(ordered, key=lambda t: -t[1])]
    by_net = [k for k, _g, _n in sorted(ordered, key=lambda t: -t[2])]
    print()
    print("bucket ORDER unchanged by netting:", by_gross == by_net)
    return ordered


def ranking_impact(bus=None):
    """Top-N membership and order on gross vs net ``rr_pct`` for the live scan."""
    from shared.bus import Bus

    bus = bus or Bus()
    env = bus.cache_get("cache:options:scan_day")
    payload = env.payload if env is not None else {}
    signals = []
    for key in ("signals_0dte", "signals_swing", "signals"):
        signals += [s for s in (payload.get(key) or []) if isinstance(s, dict)]
    rows = []
    for s in signals:
        credit, max_loss = s.get("credit"), s.get("max_loss")
        if not credit or not max_loss:
            continue
        fee = _round_trip(s.get("type"), s.get("symbol"))
        rows.append({
            "symbol": s.get("symbol"), "type": s.get("type"),
            "width": s.get("width"),
            "gross": credit / max_loss * 100,
            # The fee is per CONTRACT and credit/max_loss are per SHARE - the
            # unit trap this repo keeps paying for. Hence /100.
            "net": (credit - fee / 100.0) / max_loss * 100,
        })
    print(f"scan signals with a priceable credit: {len(rows)}")
    if not rows:
        return
    drops = [r["gross"] - r["net"] for r in rows]
    print(f"rr_pct drop: {min(drops):.2f}-{max(drops):.2f} points")
    by_gross = sorted(range(len(rows)), key=lambda i: -rows[i]["gross"])
    by_net = sorted(range(len(rows)), key=lambda i: -rows[i]["net"])
    for top in (5, 10, 20):
        k = min(top, len(rows))
        same = len(set(by_gross[:k]) & set(by_net[:k]))
        print(f"  top-{top}: {same}/{k} of the same signals, "
              f"same order = {by_gross[:k] == by_net[:k]}")
    print()
    print("  largest drops - netting penalises NARROW spreads most:")
    for i in sorted(range(len(rows)), key=lambda i: -(rows[i]["gross"] - rows[i]["net"]))[:5]:
        r = rows[i]
        print(f"    {r['symbol']:<6} {r['type']:<4} {str(r['width']):>5} wide  "
              f"{r['gross']:.2f} -> {r['net']:.2f}  ({r['gross'] - r['net']:+.2f})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--calibration", action="store_true",
                    help="per-bucket mean R, gross vs net (reads signals.db)")
    ap.add_argument("--ranking", action="store_true",
                    help="top-N reordering on the live scan (reads Redis)")
    ap.add_argument("--db", default=None, help="signals.db path override")
    args = ap.parse_args(argv)
    if not (args.calibration or args.ranking):
        ap.error("pick --calibration and/or --ranking")
    if args.calibration:
        import signal_db
        print("== calibration: does netting move an R bucket? ==")
        calibration_impact(args.db or signal_db.DEFAULT_DB_PATH)
        print()
    if args.ranking:
        print("== ranking: does netting reorder the scan? ==")
        ranking_impact()


if __name__ == "__main__":
    main()
