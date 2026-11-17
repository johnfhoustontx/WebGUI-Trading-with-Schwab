"""Replay take-profit and profit-trail rules over the stored captured marks.

The measurement behind config/trade_mgmt.toml [protect]
(docs/plans/2026-11-15-profit-trail-design.md). It opens a copy of prod's
``signals.db`` READ-ONLY and immutable, so point it at a backup, never at a live
store:

    python tools/replay_profit_trail.py --db E:/TradingBackups/<dir>/options-scanner/data/signals.db
    python tools/replay_profit_trail.py --db ... --slippage 0.25 --hourly --type 0DTE

The population: closed PCS/CCS/IC captures (not tracked rows) with a recorded
exit value; hand closes are left out, and so are time stops within 15 minutes of
capture, which closed on their first mark by construction.

⚠ A rule can only exit EARLIER than the real close: the marks end there. When no
rule exit fires before the end of a series, the real outcome stands. So a rule
that would hold longer than the app did cannot be measured here.

Every replayed exit pays the round-trip commission ($0.65 a leg each way) and
``--slippage`` of the credit; the real outcome pays the commission only, its
slippage being already in the recorded exit mark. Intervals are a bootstrap over
capture days (trades on one day are not independent). Not advice: a measurement
of the app's paper book.
"""
import argparse
import collections
import datetime as dt
import pathlib
import random
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from shared import structures as _structures                 # noqa: E402

LEGS = {"PCS": 2, "CCS": 2, "IC": 4}
# The scanner's tracked structures share the table. The strategy and legs_json
# filters below happen to leave them out today; this clause is the one rule that
# says so (tests/test_tracked_rows_stay_hidden.py).
_NOT_TRACKED = " AND " + _structures.not_tracked_sql("s.")
OPEN, CLOSE = ("08:30", "08:45"), ("14:45", "15:00")


def load(db, scanner_type=None, hourly=False):
    c = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    trades = []
    for r in c.execute(
            "SELECT s.signal_id, s.scanner_type, s.strategy, s.entry_credit, "
            "s.first_seen_ts, o.close_ts, o.exit_value, o.exit_reason "
            "FROM signals s JOIN signal_outcomes o USING(signal_id) "
            "WHERE s.strategy IN ('PCS','CCS','IC') AND s.legs_json IS NULL "
            "AND o.exit_value IS NOT NULL" + _NOT_TRACKED):
        if (r["exit_reason"] or "").upper().startswith("MANUAL"):
            continue
        if scanner_type and r["scanner_type"] != scanner_type:
            continue
        try:
            held = (dt.datetime.fromisoformat(r["close_ts"])
                    - dt.datetime.fromisoformat(r["first_seen_ts"])).total_seconds() / 60
        except (TypeError, ValueError):
            held = 1e9
        if r["exit_reason"] == "TIME_STOP" and held < 15:
            continue
        marks = [m for m in c.execute(
            "SELECT mark_ts, unrealized_pnl, current_value FROM signal_marks "
            "WHERE signal_id = ? ORDER BY mark_ts", (r["signal_id"],))
            if m["unrealized_pnl"] is not None and m["current_value"] is not None]
        if hourly:      # the Account and the Ledger mark at the top of 09:00-14:00
            seen, kept = set(), []
            for m in marks:
                hm, key = m["mark_ts"][11:16], (m["mark_ts"][:10], m["mark_ts"][11:13])
                if "09:00" <= hm < "14:06" and hm[3:5] < "06" and key not in seen:
                    seen.add(key)
                    kept.append(m)
            marks = kept
        if marks and r["entry_credit"]:
            trades.append((r, marks))
    return trades


def rule(tp, start, giveback, slip, quiet=(OPEN, CLOSE)):
    """Take the profit at ``tp`` of the credit (None = no target) and trail from
    ``start`` with ``giveback`` (None = no trail); the trail waits out ``quiet``."""
    def run(r, marks):
        cr = r["entry_credit"] * 100
        best = None
        for m in marks:
            pnl = m["unrealized_pnl"]
            if tp is not None and pnl >= tp * cr:
                return _at(r, m["current_value"], slip)
            best = pnl if best is None else max(best, pnl)
            if start is None or best < start * cr:
                continue
            hm = m["mark_ts"][11:16]
            if any(a <= hm < b for a, b in quiet):
                continue
            if pnl <= max(0.0, best - giveback * cr):
                return _at(r, m["current_value"], slip)
        return None
    return run


def _comm(r):
    return 0.65 * LEGS[r["strategy"]] * 2


def _real(r):
    return (r["entry_credit"] - r["exit_value"]) * 100 - _comm(r)


def _at(r, value, slip):
    return (r["entry_credit"] - value) * 100 - _comm(r) - slip * r["entry_credit"] * 100


def score(trades, rules, seed=7, draws=1000):
    base = {r["signal_id"]: _real(r) for r, _ in trades}
    days = collections.defaultdict(list)
    for r, _ in trades:
        days[r["first_seen_ts"][:10]].append(r["signal_id"])
    day_list = list(days)
    rows = []
    for name, fn in rules.items():
        got = {}
        for r, marks in trades:
            x = fn(r, marks)
            got[r["signal_id"]] = base[r["signal_id"]] if x is None else x
        rng, boots = random.Random(seed), []
        for _ in range(draws):
            ids = [i for d in (rng.choice(day_list) for _ in day_list) for i in days[d]]
            boots.append(sum(got[i] - base[i] for i in ids) / len(ids))
        boots.sort()
        n = len(got)
        rows.append((sum(got.values()) / n, name,
                     sum(1 for v in got.values() if v > 0) / n,
                     boots[int(0.025 * draws)], boots[int(0.975 * draws)]))
    return base, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="a COPY of prod's signals.db")
    ap.add_argument("--slippage", type=float, default=0.10,
                    help="fraction of the credit lost on every replayed exit")
    ap.add_argument("--hourly", action="store_true",
                    help="keep one mark an hour, 09:00-14:00, as the Account marks")
    ap.add_argument("--type", dest="scanner_type", help="0DTE, SWING or INCOME")
    a = ap.parse_args()
    trades = load(a.db, a.scanner_type, a.hourly)
    rules = {"take profit at +50%": rule(0.5, None, None, a.slippage)}
    for start, give in ((0.20, 0.10), (0.25, 0.10), (0.30, 0.15)):
        rules[f"+50% and trail {start:.0%}/{give:.0%}"] = rule(0.5, start, give, a.slippage)
        rules[f"trail {start:.0%}/{give:.0%}, no target"] = rule(None, start, give, a.slippage)
    base, rows = score(trades, rules)
    print(f"{len(trades)} trades, slippage {a.slippage:.0%}, "
          f"{'hourly' if a.hourly else '5-minute'} marks; "
          f"real outcome {sum(base.values()) / len(base):+.2f} a trade")
    for avg, name, win, lo, hi in sorted(rows, reverse=True):
        print(f"  {name:32s} {avg:+8.2f} a trade  win {win:.0%}  "
              f"vs real [{lo:+.2f}, {hi:+.2f}]")


if __name__ == "__main__":
    main()
