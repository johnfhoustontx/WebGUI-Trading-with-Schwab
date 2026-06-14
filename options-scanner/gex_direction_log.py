"""
gex_direction_log.py - Forward instrumentation for the GEX direction edge
Version: 1.0.0
Last Updated: 2026-06-13

PURPOSE
-------
The 0-DTE backtest (backtest_0dte.py) showed direction selection has a large
ceiling (perfect direction ~4x the trend-filter expectancy) BUT that the trend
filter itself is noise (it performs the same as its own inverse). The open
question is whether the GEX / gamma regime predicts the late-day direction
better than a coin flip. We cannot answer that today -- only ~11 days of GEX
history exist. This module INSTRUMENTS the question so a real sample
accumulates going forward.

For each session it records, at the strategy's ~13:00 CT entry time:
  * raw GEX regime features (net GEX, gamma center-of-mass, call/put walls, spot)
  * the realized entry->close direction (the truth label)
It logs raw features rather than one baked-in rule, so several candidate GEX
direction rules can be scored later (in `analyze`) without re-collecting.

Reads the existing gex_history.db `gex_term_snapshots` table (per-strike 0-DTE
GEX with underlying price, written every 5 min by the collector). Writes a
small, separate, idempotent log DB so it never touches the live collector data.

USAGE
-----
  python gex_direction_log.py backfill      # log every complete session in history
  python gex_direction_log.py log 2026-06-12  # log one date (daily forward use)
  python gex_direction_log.py analyze       # score candidate GEX rules vs truth
  python gex_direction_log.py               # backfill then analyze

To accumulate automatically, schedule `backfill` (idempotent) once daily after
the close (~15:30 CT). It only adds newly-complete sessions.

Version 1.0.0 Changes:
- Initial implementation
"""

import sys
import math
import sqlite3
import pathlib
import datetime
import logging

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import gex_history_db

log = logging.getLogger("gex_direction_log")

#############################################
# CONFIG
#############################################

SYMBOL = "SPX"                 # the only symbol with multi-day term history
ENTRY_HOUR_CT = 13.0           # ~2h before the 15:00 CT close (the entry window)
LOG_DB_PATH = pathlib.Path(__file__).parent / "data" / "gex_direction_log.db"

LOG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS gex_dir_log (
    symbol          TEXT NOT NULL,
    date            TEXT NOT NULL,
    entry_ts        TEXT,
    open_spot       REAL,
    entry_spot      REAL,
    close_ts        TEXT,
    close_spot      REAL,
    net_gex         REAL,   -- sum of 0DTE net gamma exposure ($)
    gex_com         REAL,   -- |gex|-weighted strike (gamma center of mass)
    top_pos_strike  REAL,   -- largest positive net-GEX strike (call wall)
    top_neg_strike  REAL,   -- most negative net-GEX strike (put wall)
    realized_up     INTEGER,-- 1 if close_spot >= entry_spot
    PRIMARY KEY (symbol, date)
);
"""


#############################################
# DB
#############################################

def _connect_log():
    LOG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LOG_DB_PATH))
    conn.executescript(LOG_SCHEMA_SQL)
    return conn


def _logged_dates(conn):
    return {r[0] for r in conn.execute(
        "SELECT date FROM gex_dir_log WHERE symbol = ?", (SYMBOL,))}


#############################################
# FEATURE EXTRACTION
#############################################

def _ts_hour_ct(ts_iso):
    """Local hour-of-day (float) from an ISO timestamp like
    '2026-06-11T13:05:00-05:00'."""
    dt = datetime.datetime.fromisoformat(ts_iso)
    return dt.hour + dt.minute / 60.0


def pick_entry_ts(timestamps, entry_hour=ENTRY_HOUR_CT):
    """Choose the timestamp closest to the entry hour (CT)."""
    return min(timestamps, key=lambda t: abs(_ts_hour_ct(t) - entry_hour))


def regime_features(rows, on_date):
    """Compute GEX features from a term snapshot (list of per-strike dicts).

    Uses the 0-DTE expiration (expiration_date == on_date) if present, else the
    nearest available expiration. Returns a dict or None if unusable.
    """
    zerodte = [r for r in rows if r["expiration_date"] == on_date]
    if not zerodte:
        exps = sorted({r["expiration_date"] for r in rows})
        if not exps:
            return None
        zerodte = [r for r in rows if r["expiration_date"] == exps[0]]

    spot = next((r["underlying_price"] for r in zerodte
                 if r["underlying_price"]), None)
    if spot is None:
        return None

    net_gex = sum(r["net_gex_usd"] for r in zerodte)
    abs_sum = sum(abs(r["net_gex_usd"]) for r in zerodte)
    gex_com = (sum(r["strike"] * abs(r["net_gex_usd"]) for r in zerodte) / abs_sum
               if abs_sum else spot)
    top_pos = max(zerodte, key=lambda r: r["net_gex_usd"])["strike"]
    top_neg = min(zerodte, key=lambda r: r["net_gex_usd"])["strike"]
    return {
        "entry_spot": spot, "net_gex": net_gex, "gex_com": gex_com,
        "top_pos_strike": top_pos, "top_neg_strike": top_neg,
    }


#############################################
# LOGGING
#############################################

def log_date(conn, hist, date_str, *, force=False):
    """Record one session. Returns True if a row was written."""
    if not force and date_str in _logged_dates(conn):
        return False
    timestamps = gex_history_db.list_term_timestamps_for_date(hist, date_str, SYMBOL)
    if len(timestamps) < 2:
        log.info("skip %s: only %d snapshots", date_str, len(timestamps))
        return False

    entry_ts = pick_entry_ts(timestamps)
    close_ts = timestamps[-1]
    open_ts = timestamps[0]

    feats = regime_features(
        gex_history_db.load_term_snapshot(hist, entry_ts, SYMBOL), date_str)
    if feats is None:
        log.info("skip %s: no usable entry snapshot", date_str)
        return False

    def _spot(ts):
        rows = gex_history_db.load_term_snapshot(hist, ts, SYMBOL)
        return next((r["underlying_price"] for r in rows if r["underlying_price"]),
                    None)
    close_spot = _spot(close_ts)
    open_spot = _spot(open_ts)
    if close_spot is None:
        return False

    conn.execute(
        "INSERT OR REPLACE INTO gex_dir_log (symbol, date, entry_ts, open_spot, "
        "entry_spot, close_ts, close_spot, net_gex, gex_com, top_pos_strike, "
        "top_neg_strike, realized_up) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (SYMBOL, date_str, entry_ts, open_spot, feats["entry_spot"], close_ts,
         close_spot, feats["net_gex"], feats["gex_com"], feats["top_pos_strike"],
         feats["top_neg_strike"], 1 if close_spot >= feats["entry_spot"] else 0),
    )
    conn.commit()
    return True


def backfill(conn, hist):
    """Log every session present in the GEX history that isn't already logged."""
    dates = [r[0] for r in hist.execute(
        "SELECT DISTINCT substr(timestamp_ct,1,10) d FROM gex_term_snapshots "
        "WHERE symbol = ? ORDER BY d", (SYMBOL,))]
    added = sum(log_date(conn, hist, d) for d in dates)
    log.info("backfill: %d sessions in history, %d newly logged", len(dates), added)
    return added


#############################################
# CANDIDATE DIRECTION RULES + ANALYSIS
#############################################

def _rule_predictions(row):
    """Map raw features to each candidate rule's predicted direction (1=up).

    All rules are testable hypotheses about how GEX positioning leads price into
    the close; `analyze` scores them against the realized label.
    """
    entry, com = row["entry_spot"], row["gex_com"]
    pos_wall, net, open_s = row["top_pos_strike"], row["net_gex"], row["open_spot"]
    preds = {
        # gamma magnet: price drawn toward the gamma center of mass
        "magnet_com": 1 if entry < com else 0,
        # call-wall magnet: price drawn toward the largest positive-GEX strike
        "magnet_wall": 1 if entry < pos_wall else 0,
    }
    # regime-conditional: positive gamma -> mean-revert to COM; negative gamma
    # -> momentum continuation of the morning move (needs open_spot).
    if open_s:
        if net >= 0:
            preds["regime_cond"] = 1 if entry < com else 0
        else:
            preds["regime_cond"] = 1 if entry > open_s else 0
    return preds


def analyze(conn):
    rows = [dict(zip([c[0] for c in cur.description], r))
            for cur in [conn.execute("SELECT * FROM gex_dir_log WHERE symbol=? "
                                     "ORDER BY date", (SYMBOL,))]
            for r in cur.fetchall()]
    n = len(rows)
    print(f"\nGEX direction instrumentation - {n} sessions logged "
          f"({rows[0]['date']} -> {rows[-1]['date']})" if n else
          "\nNo sessions logged yet.")
    if n == 0:
        return
    up_rate = sum(r["realized_up"] for r in rows) / n
    majority = max(up_rate, 1 - up_rate)
    print(f"  realized up-days: {up_rate*100:.0f}%   "
          f"(always-pick-majority baseline = {majority*100:.0f}%)")

    tallies = {}
    for r in rows:
        for rule, pred in _rule_predictions(r).items():
            t = tallies.setdefault(rule, [0, 0])
            t[0] += 1 if pred == r["realized_up"] else 0
            t[1] += 1
    print(f"\n  {'rule':<14}{'accuracy':>10}{'hits':>8}{'vs coin':>10}")
    for rule, (hits, tot) in sorted(tallies.items()):
        acc = hits / tot
        print(f"  {rule:<14}{acc*100:>9.1f}%{hits:>5}/{tot:<3}{(acc-0.5)*100:>+8.1f}pt")

    print("\n  NOTE: with a sample this small these numbers are NOISE -- a"
          "\n  single rule needs ~150+ sessions before ~5pt edge clears"
          "\n  significance. This is instrumentation; let it accumulate, then"
          "\n  re-run `analyze` in a few months.")


#############################################
# CLI
#############################################

def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    conn = _connect_log()
    hist = gex_history_db.connect(read_only=True)
    cmd = argv[1] if len(argv) > 1 else "all"
    if cmd == "backfill":
        backfill(conn, hist)
    elif cmd == "log" and len(argv) > 2:
        print("logged" if log_date(conn, hist, argv[2], force=True) else "skipped")
    elif cmd == "analyze":
        analyze(conn)
    else:
        backfill(conn, hist)
        analyze(conn)


if __name__ == "__main__":
    main(sys.argv)
