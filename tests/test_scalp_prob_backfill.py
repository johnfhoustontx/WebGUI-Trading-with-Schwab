"""The history loader for the Opening Scalp's weighted chance (tools/scalp_prob_backfill).

Every store here is a small SQLite file built in ``tmp_path`` with the real schema;
nothing opens a live store. ``extract`` rebuilds ``scalp_confirm.snapshot``-shaped
sources from a gamma-history copy, ``load`` merges them into ``prob_sources``.
"""
import datetime as dt
import json
import pathlib
import sqlite3
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "options-scanner")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gex_history_db as gh  # noqa: E402

from services.options_svc import scalp_store  # noqa: E402
from shared import scalp_config  # noqa: E402
from tools import scalp_prob_backfill as bf  # noqa: E402

DAY = dt.date(2026, 10, 14)
DAY_ISO = DAY.isoformat()
ENTRY = scalp_config.intraday_entry_ts(DAY, "08:36")          # 08:35 CT
SLOT = scalp_config.intraday_slots(DAY)[0]                    # 08:51
SLOT_ENTRY = scalp_config.intraday_entry_ts(DAY, SLOT)        # 08:50 CT

# Strikes 95 / 105 carry the walls the tests expect; spot is 100.
GRID = {90.0: {"call": 0.0, "put": -1.0, "net": -1.0},
        95.0: {"call": 0.0, "put": -9.0, "net": -9.0},
        105.0: {"call": 8.0, "put": 0.0, "net": 8.0},
        110.0: {"call": 2.0, "put": 0.0, "net": 2.0}}
OTHER_GRID = {90.0: {"call": 0.0, "put": -9.0, "net": -9.0},
              95.0: {"call": 0.0, "put": -1.0, "net": -1.0},
              105.0: {"call": 1.0, "put": 0.0, "net": 1.0},
              110.0: {"call": 9.0, "put": 0.0, "net": 9.0}}


def make_gamma(path, rows, drop_carried_column=False, flow=()):
    """A gamma-history copy with ``rows`` of ``(symbol, ts, grid, **summary)``."""
    conn = sqlite3.connect(str(path))
    gh.init_schema(conn)
    gh.init_flow_day_schema(conn)
    for symbol, ts, grid, summary in rows:
        gh.insert_snapshot(conn, symbol, "gex",
                           {"ts": ts, "spot": 100.0, "flip": 99.0, "net_total": 5.0,
                            "call_prem": 30.0, "put_prem": 10.0, **summary},
                           grid, 0)
    if flow:
        gh.upsert_flow_contract_days(conn, list(flow))
    conn.commit()
    if drop_carried_column:
        conn.execute("ALTER TABLE snapshots DROP COLUMN carried_age_sec")
        conn.commit()
    conn.close()
    return str(path)


def flow_row(alert_id, symbol, side, fired_ts, bought, sold, alert_type="uoa", day=DAY_ISO):
    return {"session_date": day, "alert_id": alert_id, "symbol": symbol, "osi": "X " + alert_id,
            "side": side, "strike": 100.0, "expiry": day, "alert_type": alert_type,
            "fired_ts": fired_ts, "oi_prev": 1.0, "volume": bought + sold,
            "poll_bought": bought, "poll_sold": sold, "poll_unlabelled": 0.0}


def run_extract(tmp_path, gamma, flow_db=None):
    out = tmp_path / "out.jsonl"
    bf.extract([str(p) for p in (gamma if isinstance(gamma, list) else [gamma])],
               flow_db, str(out))
    return [json.loads(line) for line in out.read_text().splitlines() if line.strip()]


def pick(records, slot, symbol, kind=None):
    hits = [r for r in records if r["slot"] == slot and r["symbol"] == symbol
            and (kind is None or r["kind"] == kind)]
    assert len(hits) <= 1
    return hits[0] if hits else None


# --- extract ------------------------------------------------------------------

def test_extract_reads_walls_flip_net_gex_and_premium(tmp_path):
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 60, GRID, {})])
    rec = pick(run_extract(tmp_path, g), "08:36", "$SPX")
    assert rec["kind"] == "morning" and rec["day"] == DAY_ISO
    spx = rec["snapshot"]["matrix"]["$SPX"]
    assert spx == {"spot": 100.0, "call_wall": 105.0, "put_wall": 95.0, "flip": 99.0,
                   "net_gex": 5.0, "walls_untrusted": False}
    assert rec["snapshot"]["netprem"]["$SPX"] == {"call": 30.0, "put": 10.0, "ts": ENTRY - 60}
    # a name with no row is None, the big ten included
    assert rec["snapshot"]["matrix"]["SPY"] is None
    assert rec["snapshot"]["matrix"]["NVDA"] is None
    assert rec["snapshot"]["sentiment"] is None and rec["snapshot"]["flow"] is None
    assert rec["snapshot"]["sources"]["matrix"]["state"] == "backfill"


def test_extract_covers_the_intraday_slots_as_intraday_kind(tmp_path):
    g = make_gamma(tmp_path / "g.db", [("SPY", SLOT_ENTRY - 120, GRID, {})])
    records = run_extract(tmp_path, g)
    rec = pick(records, SLOT, "QQQ")
    assert rec is not None and rec["kind"] == "intraday"
    assert rec["snapshot"]["matrix"]["SPY"]["call_wall"] == 105.0
    # every call time of every scalp symbol is keyed, on the one session day
    assert {r["day"] for r in records} == {DAY_ISO}


def test_extract_big_ten_members_are_in_every_symbols_matrix(tmp_path):
    g = make_gamma(tmp_path / "g.db", [("NVDA", ENTRY - 60, OTHER_GRID, {}),
                                       ("$SPX", ENTRY - 60, GRID, {})])
    rec = pick(run_extract(tmp_path, g), "08:36", "QQQ")
    assert rec["snapshot"]["matrix"]["NVDA"]["put_wall"] == 90.0
    assert rec["snapshot"]["matrix"]["NVDA"]["call_wall"] == 110.0


def test_extract_ignores_a_row_after_entry(tmp_path):
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 60, GRID, {}),
                                       ("$SPX", ENTRY + 60, OTHER_GRID, {"flip": 50.0})])
    spx = pick(run_extract(tmp_path, g), "08:36", "$SPX")["snapshot"]["matrix"]["$SPX"]
    assert (spx["call_wall"], spx["put_wall"], spx["flip"]) == (105.0, 95.0, 99.0)


def test_extract_uses_the_newest_row_at_or_before_entry(tmp_path):
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 120, GRID, {}),
                                       ("$SPX", ENTRY, OTHER_GRID, {})])
    spx = pick(run_extract(tmp_path, g), "08:36", "$SPX")["snapshot"]["matrix"]["$SPX"]
    assert (spx["call_wall"], spx["put_wall"]) == (110.0, 90.0)


def test_extract_a_row_older_than_the_confirm_age_is_no_reading(tmp_path):
    old = ENTRY - scalp_config.confirm_max_age_min() * 60 - 60
    g = make_gamma(tmp_path / "g.db", [("$SPX", old, GRID, {})])
    snap = pick(run_extract(tmp_path, g), "08:36", "$SPX")
    # nothing usable at 08:35: the call time carries no row at all
    assert snap is None or snap["snapshot"]["matrix"]["$SPX"] is None


def test_extract_a_row_exactly_at_the_age_limit_is_used(tmp_path):
    edge = ENTRY - scalp_config.confirm_max_age_min() * 60
    g = make_gamma(tmp_path / "g.db", [("$SPX", edge, GRID, {})])
    spx = pick(run_extract(tmp_path, g), "08:36", "$SPX")["snapshot"]["matrix"]["$SPX"]
    assert spx["call_wall"] == 105.0


def test_extract_marks_zero_net_gex_untrusted(tmp_path):
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 60, GRID, {"net_total": 0.0})])
    spx = pick(run_extract(tmp_path, g), "08:36", "$SPX")["snapshot"]["matrix"]["$SPX"]
    assert spx["walls_untrusted"] is True and spx["net_gex"] == 0.0


def test_extract_marks_missing_net_gex_untrusted(tmp_path):
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 60, GRID, {"net_total": None})])
    spx = pick(run_extract(tmp_path, g), "08:36", "$SPX")["snapshot"]["matrix"]["$SPX"]
    assert spx["walls_untrusted"] is True and spx["net_gex"] is None


def test_extract_skips_carried_rows(tmp_path):
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 120, GRID, {}),
                                       ("$SPX", ENTRY - 60, OTHER_GRID,
                                        {"carried_age_sec": 90.0})])
    spx = pick(run_extract(tmp_path, g), "08:36", "$SPX")["snapshot"]["matrix"]["$SPX"]
    # the newer row is carried (a model, not an observation): the older fetched one wins
    assert (spx["call_wall"], spx["put_wall"]) == (105.0, 95.0)


def test_extract_reads_a_copy_that_predates_the_carried_column(tmp_path):
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 60, GRID, {})],
                   drop_carried_column=True)
    spx = pick(run_extract(tmp_path, g), "08:36", "$SPX")["snapshot"]["matrix"]["$SPX"]
    assert spx["call_wall"] == 105.0


def test_extract_splits_walls_by_spot_not_by_position(tmp_path):
    only_above = {105.0: {"call": 8.0, "put": 0.0, "net": 8.0},
                  110.0: {"call": 2.0, "put": 0.0, "net": 2.0}}
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 60, only_above, {})])
    spx = pick(run_extract(tmp_path, g), "08:36", "$SPX")["snapshot"]["matrix"]["$SPX"]
    assert spx["call_wall"] == 105.0 and spx["put_wall"] is None


def test_extract_a_session_day_per_distinct_central_date(tmp_path):
    next_day = DAY + dt.timedelta(days=1)
    ts2 = scalp_config.intraday_entry_ts(next_day, "08:36") - 60
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 60, GRID, {}),
                                       ("$SPX", ts2, OTHER_GRID, {})])
    records = run_extract(tmp_path, g)
    assert {r["day"] for r in records} == {DAY_ISO, next_day.isoformat()}
    second = pick([r for r in records if r["day"] == next_day.isoformat()], "08:36", "$SPX")
    assert second["snapshot"]["matrix"]["$SPX"]["call_wall"] == 110.0


def test_extract_reads_a_path_with_spaces(tmp_path):
    folder = tmp_path / "a folder with spaces"
    folder.mkdir()
    g = make_gamma(folder / "gex history.db", [("$SPX", ENTRY - 60, GRID, {})])
    assert pick(run_extract(tmp_path, g), "08:36", "$SPX") is not None


def test_extract_opens_the_copy_read_only(tmp_path):
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 60, GRID, {})])
    conn = bf.open_readonly(g)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM snapshots")
    finally:
        conn.close()


def test_extract_refuses_a_missing_copy(tmp_path):
    with pytest.raises(FileNotFoundError):
        bf.open_readonly(str(tmp_path / "nope.db"))


# --- extract: flow -------------------------------------------------------------

def test_extract_flow_window_counts_the_directional_alerts(tmp_path):
    window = scalp_config.flow_window_min() * 60
    flow = [flow_row("a1", "SPY", "call", ENTRY - 300, 90.0, 10.0),        # call bought: bull
            flow_row("a2", "NVDA", "put", ENTRY - 200, 90.0, 10.0),        # put bought: bear
            flow_row("a3", "XYZ", "call", ENTRY - 200, 90.0, 10.0),        # not ours
            flow_row("a4", "SPY", "call", ENTRY - window - 60, 90.0, 10.0),  # too old
            flow_row("a5", "SPY", "call", ENTRY + 60, 90.0, 10.0),         # after entry
            flow_row("a6", "SPY", "call", ENTRY - 100, 55.0, 45.0),        # no majority
            flow_row("a7", "SPY", "call", ENTRY - 100, 90.0, 10.0, day="2026-10-13")]
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 60, GRID, {})], flow=flow)
    rec = pick(run_extract(tmp_path, g, flow_db=g), "08:36", "SPY")
    win = rec["snapshot"]["flow"]["window"]
    assert (win["n_alerts"], win["bull"], win["bear"], win["net"]) == (3, 1, 1, 0)
    assert win["symbol"] == "SPY" and win["entry_ts"] == ENTRY
    assert win["window_min"] == scalp_config.flow_window_min()
    assert win["margin"] == scalp_config.flow_margin()
    assert win["by_type"] == {"uoa": {"n": 3, "bull": 1, "bear": 1}}
    assert rec["snapshot"]["flow"]["date"] == DAY_ISO
    assert rec["snapshot"]["sources"]["flow"]["state"] == "backfill"


def test_extract_flow_is_a_gap_without_the_flow_db_or_a_flow_day(tmp_path):
    flow = [flow_row("a1", "SPY", "call", ENTRY - 300, 90.0, 10.0, day="2026-10-13")]
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 60, GRID, {})], flow=flow)
    assert pick(run_extract(tmp_path, g), "08:36", "SPY")["snapshot"]["flow"] is None
    assert pick(run_extract(tmp_path, g, flow_db=g), "08:36", "SPY")["snapshot"]["flow"] is None


def test_extract_flow_alone_makes_a_record_when_gamma_has_no_row(tmp_path):
    flow = [flow_row("a1", "SPY", "call", ENTRY - 300, 90.0, 10.0)]
    g = make_gamma(tmp_path / "g.db", [("$SPX", ENTRY - 3600, GRID, {})], flow=flow)
    rec = pick(run_extract(tmp_path, g, flow_db=g), "08:36", "SPY")
    assert rec["snapshot"]["matrix"]["SPY"] is None
    assert rec["snapshot"]["flow"]["window"]["bull"] == 1


# --- load ----------------------------------------------------------------------

def snap(matrix=None, netprem=None, flow=None):
    return {"sources": {}, "matrix": matrix or {}, "netprem": netprem or {},
            "sentiment": None, "flow": flow}


def write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return str(path)


def rec(snapshot, slot="08:36", symbol="SPY", day=DAY_ISO, kind="morning"):
    return {"kind": kind, "day": day, "slot": slot, "symbol": symbol, "snapshot": snapshot}


def entry(call_wall):
    return {"spot": 100.0, "call_wall": call_wall, "put_wall": 95.0, "flip": 99.0,
            "net_gex": 5.0, "walls_untrusted": False}


def test_load_writes_prob_sources_with_the_kind(tmp_path):
    db = str(tmp_path / "scalp.db")
    f = write_jsonl(tmp_path / "a.jsonl", [
        rec(snap({"SPY": entry(105.0)})),
        rec(snap({"SPY": entry(106.0)}), slot="08:51", kind="intraday")])
    assert bf.load([f], db) == {"files": 1, "keys": 2, "written": 2, "skipped_live": 0,
                                "bad_lines": 0}
    assert scalp_store.prob_sources_all("morning", db)[(DAY_ISO, "08:36", "SPY")][
        "matrix"]["SPY"]["call_wall"] == 105.0
    assert (DAY_ISO, "08:51", "SPY") in scalp_store.prob_sources_all("intraday", db)


def test_load_merges_without_overwriting(tmp_path):
    db = str(tmp_path / "scalp.db")
    first = write_jsonl(tmp_path / "a.jsonl", [rec(snap(
        {"SPY": entry(105.0), "QQQ": None},
        {"SPY": {"call": 1.0, "put": 2.0, "ts": 5}}))])
    second = write_jsonl(tmp_path / "b.jsonl", [rec(snap(
        {"SPY": entry(999.0), "QQQ": entry(300.0)},
        {"SPY": {"call": 8.0, "put": 8.0, "ts": 9}, "QQQ": {"call": 3.0, "put": 4.0, "ts": 9}},
        {"date": DAY_ISO, "window": {"bull": 2, "bear": 0}}))])
    bf.load([first, second], db)
    got = scalp_store.prob_sources_all("morning", db)[(DAY_ISO, "08:36", "SPY")]
    assert got["matrix"]["SPY"]["call_wall"] == 105.0           # the first non-null stays
    assert got["matrix"]["QQQ"]["call_wall"] == 300.0           # a null is filled
    assert got["netprem"]["SPY"]["call"] == 1.0
    assert got["netprem"]["QQQ"]["put"] == 4.0
    assert got["flow"]["window"]["bull"] == 2                   # a null flow is filled
    assert got["sources"]["flow"]["state"] == "backfill"


def test_load_never_shadows_a_live_reading(tmp_path):
    db = str(tmp_path / "scalp.db")
    scalp_store.save_reading(DAY_ISO, "08:51", "SPY", {"live": True}, db)
    f =write_jsonl(tmp_path / "a.jsonl", [
        rec(snap({"SPY": entry(105.0)}), slot="08:51", kind="intraday"),
        rec(snap({"SPY": entry(105.0)}), slot="09:06", kind="intraday")])
    result = bf.load([f], db)
    assert result["skipped_live"] == 1 and result["written"] == 1
    stored = scalp_store.prob_sources_all("intraday", db)
    assert (DAY_ISO, "08:51", "SPY") not in stored
    assert (DAY_ISO, "09:06", "SPY") in stored


def test_load_skips_a_malformed_line_and_counts_it(tmp_path):
    db = str(tmp_path / "scalp.db")
    f = tmp_path / "a.jsonl"
    f.write_text(json.dumps(rec(snap({"SPY": entry(105.0)}))) + "\nnot json\n"
                 + json.dumps({"kind": "morning"}) + "\n")
    result = bf.load([str(f)], db)
    assert result["written"] == 1 and result["bad_lines"] == 2


def test_load_is_idempotent(tmp_path):
    db = str(tmp_path / "scalp.db")
    f = write_jsonl(tmp_path / "a.jsonl", [rec(snap({"SPY": entry(105.0)}))])
    bf.load([f], db)
    bf.load([f], db)
    assert len(scalp_store.prob_sources_all("morning", db)) == 1


# --- end to end and the command line --------------------------------------------

def test_extract_then_load_round_trips_into_the_features(tmp_path):
    from services.options_svc import scalp_prob_features as feats
    g = make_gamma(tmp_path / "g.db", [("SPY", ENTRY - 60, GRID, {})])
    out = tmp_path / "out.jsonl"
    bf.extract([g], None, str(out))
    db = str(tmp_path / "scalp.db")
    bf.load([str(out)], db)
    stored = scalp_store.prob_sources_all("morning", db)[(DAY_ISO, "08:36", "SPY")]
    got = feats.source_factors(stored, "SPY", 1, 2.5, 100.0)
    assert got["room"] == pytest.approx(5.0 / 2.5)
    assert got["netprem"] == pytest.approx(0.5)
    assert got["flip_side"] == 1


def test_cli_extract_and_load(tmp_path, capsys):
    g = make_gamma(tmp_path / "g.db", [("SPY", ENTRY - 60, GRID, {})])
    out, db = tmp_path / "o.jsonl", tmp_path / "scalp.db"
    assert bf.main(["extract", "--gamma", g, "--out", str(out)]) == 0
    assert bf.main(["load", "--in", str(out), "--db", str(db)]) == 0
    assert "written" in capsys.readouterr().out
    assert scalp_store.prob_sources_all("morning", str(db))
