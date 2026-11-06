"""Compare the shadow trade ideas with the hourly posts, from a trades.db.

    python tools/idea_shadow_compare.py --db E:/TradingBackups/<dir>/options-scanner/data/trades.db

Prints, for the closed rows of each source (``trade_idea`` = the hourly posts,
``idea_shadow`` = the quality-triggered picks booked in shadow): how many, the win
rate, the mean R (realized P&L over the row's max loss) and the P&L in dollars, and
the same split by grade. Design: docs/plans/2026-11-06-trade-idea-quality-shadow-design.md.

The store is opened READ-ONLY and immutable, so it is safe on a backup copy. Run it
on a backup or on a copy, never on the live file while the service writes it. The
rule goes live only if the shadow picks beat the hourly posts' mean R over 15 to 20
sessions; read the counts before the means.
"""
import argparse
import sqlite3
import statistics
import sys

SOURCES = ("trade_idea", "idea_shadow")
LABELS = {"trade_idea": "Hourly posts", "idea_shadow": "Shadow picks"}


def r_multiple(row):
    """Realized P&L over max loss, or None when either is missing or the loss is 0."""
    pnl, loss = row.get("realized_pnl"), row.get("max_loss_total")
    if pnl is None or not loss:
        return None
    return pnl / abs(loss)


def summarize(rows):
    """``{"n", "win", "mean_r", "pnl", "days"}`` over the CLOSED rows that have an R,
    or None when there are none. ``days`` counts the distinct entry dates."""
    closed = [r for r in rows if (r.get("status") or "").upper() != "OPEN"]
    rs = [(r, x) for r in closed for x in [r_multiple(r)] if x is not None]
    if not rs:
        return None
    vals = [x for _, x in rs]
    return {"n": len(vals), "win": sum(1 for x in vals if x > 0) / len(vals),
            "mean_r": statistics.mean(vals),
            "pnl": sum(r.get("realized_pnl") or 0.0 for r, _ in rs),
            "days": len({str(r.get("idea_posted") or r.get("entry_time") or "")[:10]
                         for r, _ in rs})}


def line(label, s):
    if s is None:
        return f"{label:28} no closed trades"
    return (f"{label:28} n={s['n']:4}  days={s['days']:3}  win={s['win']:4.0%}  "
            f"mean R={s['mean_r']:+.3f}  P&L=${s['pnl']:,.0f}")


def report(rows):
    """The printed comparison, as lines."""
    out = []
    for src in SOURCES:
        mine = [r for r in rows if r.get("source") == src]
        out.append(line(LABELS[src], summarize(mine)))
        for grade in sorted({r.get("idea_grade") for r in mine if r.get("idea_grade")}):
            out.append(line(f"  grade {grade}",
                            summarize([r for r in mine if r.get("idea_grade") == grade])))
    return out


def load(db_path):
    uri = f"file:{db_path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        marks = ",".join("?" for _ in SOURCES)
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM trades WHERE source IN ({marks})", SOURCES)]
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="path to a trades.db (opened read-only)")
    args = ap.parse_args(argv)
    for text in report(load(args.db)):
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
