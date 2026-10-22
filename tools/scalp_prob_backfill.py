"""Rebuild the Opening Scalp's dealer, premium and flow sources from old copies.

The fitted chance (design 2026-10-22-scalp-weighted-probability, section 4) reads,
beside the price, what the dealer walls, the net premium and the flow looked like
at each call. Prod's gamma store keeps five sessions; the backups hold many more.
This tool reads those copies, READ-ONLY, and writes the same snapshot the live
call records (``scalp_confirm.snapshot``'s shape) into the ``prob_sources`` table
of ``scalp.db``, in two steps so the slow part can run anywhere:

    # 1. on whichever box holds the copies (reads them, writes one file)
    python tools/scalp_prob_backfill.py extract --gamma E:/a/gex_history.db \\
        E:/b/gex_history.db --flow-db E:/b/gex_history.db --out history.jsonl

    # 2. on the box that owns scalp.db (merges, never shadows a live record)
    python tools/scalp_prob_backfill.py load --in history.jsonl --db <scalp.db>

History's flow is contract alerts only (no crossover or HIRO), so its counts are
a subset of the live window's.

``extract`` writes, for every session day found in a copy, every call time (the
08:36 call, entered on the 08:35 boundary, and each 15-minute slot) and every
scalp symbol, ``{"kind", "day", "slot", "symbol", "snapshot"}`` per line. A
symbol's or big-ten member's row is the newest FETCHED (not carried) ``gex`` row at
or before the entry and no older than ``[confirm] max_age_min``; otherwise that
name is ``None``. Sentiment is never extracted: its history is the live records.

Safe to re-run, and the files may be loaded in separate runs (the Windows box's
file in one, prod's in another): ``load`` merges each key with the row ALREADY
STORED in ``prob_sources`` first, then the files in the order given, so a value
already stored is never overwritten and a later file only fills its nulls. It
never writes a key the live ``intraday_readings`` already holds.
"""
import argparse
import datetime as _dt
import json
import os
import pathlib
import sqlite3
import sys
from zoneinfo import ZoneInfo

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "options-scanner")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gamma_tool  # noqa: E402
import gex_history_db as _gh  # noqa: E402

from services.options_svc import scalp_confirm as _confirm  # noqa: E402
from services.options_svc import scalp_store  # noqa: E402
from shared import scalp_config  # noqa: E402
from shared import symbols as _symbols  # noqa: E402
from shared.numeric import finite as _finite  # noqa: E402

_CT = ZoneInfo("America/Chicago")
_MORNING_KIND, _INTRADAY_KIND = "morning", "intraday"
_SOURCE_NAMES = ("matrix", "netprem", "flow", "sentiment", "flow_sides")


# --- opening a copy ----------------------------------------------------------------

def open_readonly(path) -> sqlite3.Connection:
    """A connection to a store that can neither write nor be written under: the
    copy is opened ``mode=ro`` and ``immutable=1`` (no journal, no lock, no WAL
    read), so a backup on a read-only volume opens and is never touched. The URI
    is built from the resolved path, so ``E:/a b/x.db`` and a POSIX path both
    work."""
    resolved = pathlib.Path(os.fspath(path)).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"no such store: {path}")
    return sqlite3.connect(f"{resolved.as_uri()}?mode=ro&immutable=1", uri=True)


# --- the call times ----------------------------------------------------------------

def call_times(day) -> list:
    """``[(kind, slot, entry_ts)]`` for ``day``: the 08:36 call (``"morning"``) and
    every 15-minute slot (``"intraday"``), each with the epoch second of the
    5-minute boundary it enters on."""
    times = [(_MORNING_KIND, scalp_config.CALL_SLOT,
              scalp_config.intraday_entry_ts(day, scalp_config.CALL_SLOT))]
    for slot in scalp_config.intraday_slots(day):
        times.append((_INTRADAY_KIND, slot, scalp_config.intraday_entry_ts(day, slot)))
    return times


def _day_start(day) -> int:
    return int(_dt.datetime(day.year, day.month, day.day, tzinfo=_CT).timestamp())


def session_days(conn, names) -> list:
    """The Central dates with a ``gex`` row for any of ``names``, ascending. A seek
    per name per day on the primary key (symbol, view, ts), never a scan of the
    table."""
    days = set()
    for name in names:
        ts = conn.execute("SELECT MIN(ts) FROM snapshots WHERE symbol = ? AND view = 'gex'",
                          (name,)).fetchone()[0]
        while ts is not None:
            day = _dt.datetime.fromtimestamp(ts, tz=_CT).date()
            days.add(day)
            ts = conn.execute(
                "SELECT MIN(ts) FROM snapshots WHERE symbol = ? AND view = 'gex' AND ts >= ?",
                (name, _day_start(day + _dt.timedelta(days=1)))).fetchone()[0]
    return sorted(days)


# --- the gamma read ----------------------------------------------------------------

class _Gamma:
    """One copy's reader: the newest fetched ``gex`` row of a name inside an entry's
    window, as the live matrix and net-premium entries."""

    def __init__(self, conn):
        self.conn = conn
        have = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
        self.fetched = _gh.fetched_only_clause(conn)
        self.prem = ("call_prem, put_prem" if {"call_prem", "put_prem"} <= have
                     else "NULL, NULL")
        self.bad_grids = 0

    def row(self, name, entry_ts, max_age_sec):
        return self.conn.execute(
            f"SELECT ts, spot, flip, net_total, gex_json, {self.prem} FROM snapshots "
            f"WHERE symbol = ? AND view = 'gex' AND ts BETWEEN ? AND ? AND {self.fetched} "
            "ORDER BY ts DESC LIMIT 1",
            (name, entry_ts - max_age_sec, entry_ts)).fetchone()

    def walls(self, raw, spot):
        """``(call_wall, put_wall)`` over the decoded grid, split by SPOT (the call
        wall strictly above, the put wall strictly below), exactly as the live
        matrix does. A grid that will not decode, or no usable spot, is none."""
        spot = _finite(spot)
        if raw is None or spot is None or spot <= 0:
            return None, None
        try:
            grid = _gh._decode_grid(raw)
            found = gamma_tool.get_directional_walls({"gex": grid}, spot)
        except Exception as exc:
            self.bad_grids += 1
            print(f"warning: a stored grid would not decode ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
            return None, None
        walls = [w for w in (found.get("put_wall"), found.get("call_wall")) if w is not None]
        return (next((w for w in walls if w > spot), None),
                next((w for w in walls if w < spot), None))

    def read(self, name, entry_ts, max_age_sec):
        """``(matrix_entry, netprem_entry)``, each ``None`` with no usable row."""
        got = self.row(name, entry_ts, max_age_sec)
        if got is None:
            return None, None
        ts, spot, flip, net_total, raw, call_prem, put_prem = got
        call_wall, put_wall = self.walls(raw, spot)
        net = _finite(net_total)
        matrix = {"spot": _finite(spot), "call_wall": call_wall, "put_wall": put_wall,
                  "flip": _finite(flip), "net_gex": net,
                  "walls_untrusted": net is None or net == 0}
        call, put = _finite(call_prem), _finite(put_prem)
        netprem = None if call is None or put is None else {
            "call": call, "put": put, "ts": int(ts)}
        return matrix, netprem


# --- the flow read -----------------------------------------------------------------

def flow_alerts(conn, day) -> list:
    """The flagged contracts of one session as ``(alert, estimate)`` pairs in the
    shape ``scalp_confirm.flow_sense`` reads: the alert's ``type``, ``side``,
    ``symbol`` and ``ts`` (its fire time), and the bought / sold tally AT ALERT TIME
    (``at_bought`` / ``at_sold``) as the estimate. The ``poll_*`` columns are
    running tallies overwritten all session, so a backup holds the end-of-day
    figure: reading them would decide a pre-entry alert's direction from volume
    traded after the entry. They are never read here. An alert whose ``at_*``
    tally is NULL (a first write before the columns existed) has no estimate and
    is not directional, as a live alert with no estimate is. A store with no such
    table is no flow.

    History holds contract alerts only (``uoa``, ``big_delta``): crossover and
    HIRO alerts are not stored, so these counts are a subset of the live
    window's."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(flow_contract_days)")}
    if not cols:
        return []
    tally = "at_bought, at_sold" if {"at_bought", "at_sold"} <= cols else "NULL, NULL"
    rows = conn.execute(
        f"SELECT alert_id, symbol, side, alert_type, fired_ts, {tally} "
        "FROM flow_contract_days WHERE session_date = ? AND fired_ts IS NOT NULL "
        "ORDER BY fired_ts, alert_id", (day.isoformat(),)).fetchall()
    out = []
    for aid, sym, side, typ, ts, bought, sold in rows:
        estimate = None if bought is None or sold is None else {
            "bought": bought, "sold": sold}
        out.append(({"id": aid, "symbol": sym, "side": side, "type": typ, "ts": ts},
                    estimate))
    return out


def flow_block(alerts, symbol, entry_ts, day) -> dict:
    """The ``flow`` entry of a snapshot: the whole view up to the entry and the
    windowed, scoped reading the call saw (``scalp_confirm._flow_scope``'s fields,
    counted here from the stored alerts). ``None`` for a session with no flow
    rows at all: before the store existed, flow is a gap, not a quiet window."""
    if not alerts:
        return None
    window_min = scalp_config.flow_window_min()
    start = entry_ts - window_min * 60
    me = _confirm._norm(symbol)
    members = {_confirm._norm(m) for m in _symbols.big10()}
    whole, scoped = {}, {}
    n_whole = n_scoped = bull = bear = 0
    for alert, estimate in alerts:
        if alert["ts"] > entry_ts:
            continue
        sense = _confirm.flow_sense(alert, estimate)
        sym = _confirm._norm(alert["symbol"])
        in_scope = start <= alert["ts"] and (sym == me or sym in members)
        for table in (whole, scoped) if in_scope else (whole,):
            row = table.setdefault(str(alert["type"]), {"n": 0, "bull": 0, "bear": 0})
            row["n"] += 1
            row["bull"] += sense == 1
            row["bear"] += sense == -1
        n_whole += 1
        if in_scope:
            n_scoped += 1
            bull += sense == 1
            bear += sense == -1
    return {"date": day.isoformat(), "n": n_whole, "sides_view": "ok", "by_type": whole,
            "window": {"symbol": symbol, "entry_ts": int(entry_ts), "state": "ok",
                       "window_min": window_min, "n_alerts": n_scoped, "bull": bull,
                       "bear": bear, "net": bull - bear,
                       "margin": scalp_config.flow_margin(), "by_type": scoped,
                       "truncated": False, "sides_view": "ok"}}


# --- the snapshot ------------------------------------------------------------------

def sources_block(snapshot) -> dict:
    """Where each source of a backfilled snapshot came from: ``backfill`` when it
    holds a value, ``absent`` when it does not. A backfilled row has no age."""
    present = {
        "matrix": any(v is not None for v in (snapshot.get("matrix") or {}).values()),
        "netprem": any(v is not None for v in (snapshot.get("netprem") or {}).values()),
        "flow": snapshot.get("flow") is not None,
        "sentiment": snapshot.get("sentiment") is not None,
    }
    present["flow_sides"] = present["flow"]
    return {name: {"state": "backfill" if present[name] else "absent", "age_min": None}
            for name in _SOURCE_NAMES}


def _has_content(snapshot) -> bool:
    return any(entry["state"] == "backfill" for entry in snapshot["sources"].values())


# --- extract -----------------------------------------------------------------------

def extract_copy(gamma_conn, flow_conn=None):
    """Yield ``{"kind", "day", "slot", "symbol", "snapshot"}`` for one gamma copy.
    A call time with no source at all is not yielded. ``flow_conn`` (any store
    with ``flow_contract_days``) adds the flow window."""
    reader = _Gamma(gamma_conn)
    max_age = scalp_config.confirm_max_age_min() * 60
    scalp_symbols = list(scalp_config.symbols())
    names = list(dict.fromkeys(scalp_symbols + list(_symbols.big10())))
    for day in session_days(gamma_conn, names):
        alerts = flow_alerts(flow_conn, day) if flow_conn is not None else []
        for kind, slot, entry_ts in call_times(day):
            matrix, netprem = {}, {}
            for name in names:
                matrix[name], netprem[name] = reader.read(name, entry_ts, max_age)
            for symbol in scalp_symbols:
                snapshot = {"sources": {}, "matrix": dict(matrix), "netprem": dict(netprem),
                            "sentiment": None,
                            "flow": flow_block(alerts, symbol, entry_ts, day)}
                snapshot["sources"] = sources_block(snapshot)
                if _has_content(snapshot):
                    yield {"kind": kind, "day": day.isoformat(), "slot": slot,
                           "symbol": symbol, "snapshot": snapshot}


def extract(gamma_paths, flow_db, out_path) -> dict:
    """Write every copy's records to ``out_path`` (one JSON object per line) and
    return ``{"copies", "records", "days"}``."""
    flow_conn = open_readonly(flow_db) if flow_db else None
    records, days = 0, set()
    try:
        with open(out_path, "w", encoding="utf-8") as out:
            for path in gamma_paths:
                conn = open_readonly(path)
                try:
                    for record in extract_copy(conn, flow_conn):
                        out.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
                        records += 1
                        days.add(record["day"])
                finally:
                    conn.close()
    finally:
        if flow_conn is not None:
            flow_conn.close()
    return {"copies": len(gamma_paths), "records": records, "days": len(days)}


# --- load --------------------------------------------------------------------------

def merge_snapshots(earlier, later) -> dict:
    """``later`` fills the nulls of ``earlier``; a non-null value is never
    overwritten. Matrix and net-premium entries merge per name, the flow block as
    a whole. ``sources`` is recomputed from the result."""
    merged = {"matrix": dict(earlier.get("matrix") or {}),
              "netprem": dict(earlier.get("netprem") or {}),
              "sentiment": earlier.get("sentiment"), "flow": earlier.get("flow")}
    for block in ("matrix", "netprem"):
        for name, value in (later.get(block) or {}).items():
            if merged[block].get(name) is None:
                merged[block][name] = value
    for block in ("sentiment", "flow"):
        if merged[block] is None:
            merged[block] = later.get(block)
    merged["sources"] = sources_block(merged)
    return merged


def _read_records(path):
    """``(records, bad_lines)`` of one JSONL file: a line that is not a record
    with the five fields and a snapshot dict is counted, not read."""
    good, bad = [], 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                key = (rec["kind"], rec["day"], rec["slot"], rec["symbol"])
                if not all(isinstance(part, str) for part in key) \
                        or not isinstance(rec["snapshot"], dict):
                    raise ValueError("not a record")
            except (ValueError, KeyError, TypeError):
                bad += 1
                continue
            good.append((key, rec["snapshot"]))
    return good, bad


def load(in_paths, db_path) -> dict:
    """Merge, per ``(kind, day, slot, symbol)``, the row already stored in
    ``prob_sources`` (if any) and then the files in the order given (each fills
    only the nulls of what came before, so a stored non-null value is never
    overwritten), and save the result with ``save_prob_sources``, leaving out any
    ``(day, slot, symbol)`` the live ``intraday_readings`` already records.
    Returns the counts (``merged_stored``: keys that already had a stored row)."""
    merged, bad = {}, 0
    for path in in_paths:
        records, bad_here = _read_records(path)
        bad += bad_here
        for key, snapshot in records:
            merged[key] = merge_snapshots(merged[key], snapshot) if key in merged \
                else merge_snapshots({}, snapshot)
    stored, merged_stored = {}, 0
    for kind in sorted({key[0] for key in merged}):
        stored[kind] = scalp_store.prob_sources_all(kind, db_path)
    for key in merged:
        kind, day, slot, symbol = key
        before = stored[kind].get((day, slot, symbol))
        if isinstance(before, dict):
            merged[key] = merge_snapshots(before, merged[key])
            merged_stored += 1
    live = scalp_store.readings_all(db_path)
    rows = [(kind, day, slot, symbol, snapshot)
            for (kind, day, slot, symbol), snapshot in sorted(merged.items())
            if (day, slot, symbol) not in live]
    written = scalp_store.save_prob_sources(rows, db_path)
    return {"files": len(in_paths), "keys": len(merged), "written": written,
            "merged_stored": merged_stored, "skipped_live": len(merged) - len(rows),
            "bad_lines": bad}


# --- command line ------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild the Opening Scalp chance's dealer, premium and flow "
                    "sources from gamma-history copies.")
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract", help="read gamma copies (read-only) into a JSONL file")
    ex.add_argument("--gamma", nargs="+", required=True, metavar="PATH",
                    help="gamma-history copies (gex_history.db)")
    ex.add_argument("--flow-db", metavar="PATH",
                    help="a store holding flow_contract_days (often one of the copies)")
    ex.add_argument("--out", required=True, metavar="FILE.jsonl")
    ld = sub.add_parser("load", help="merge JSONL files into scalp.db's prob_sources")
    ld.add_argument("--in", dest="inputs", nargs="+", required=True, metavar="FILE")
    ld.add_argument("--db", required=True, metavar="PATH", help="scalp.db")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "extract":
            done = extract(args.gamma, args.flow_db, args.out)
        else:
            done = load(args.inputs, args.db)
    except (OSError, sqlite3.Error) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(", ".join(f"{k} {v}" for k, v in done.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
