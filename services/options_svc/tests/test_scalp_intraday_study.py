"""The intraday study (scalp_intraday_study) on a tmp store of hand-built bars.

Each fixture day writes $SPX (5000), SPY (500) and QQQ (400) 5-minute bars from
08:30 to 14:55 CT and a flat $VIX of 20 (every symbol prices at 20%: QQQ falls
back to $VIX, no $VXN is stored). The big ten is monkeypatched EMPTY, so breadth is
absent; no futures or sector funds are stored, so the inputs that vote are the
move since the open and the side of VWAP ($SPX reads SPY's).

* ``walk`` days move each bar by a seeded random step shared by the three symbols
  (one tape), so directions, buckets and outcomes vary from slot to slot;
* ``trend`` days rise 0.1% every bar: every slot has an UP direction;
* ``spx_last`` cuts $SPX's bars short (an early close), and ``hole`` drops one
  bar of one symbol.
"""
import datetime as dt
import importlib.util
import json
import pathlib
import random
import re
import sqlite3
from collections import defaultdict
from zoneinfo import ZoneInfo

import pytest

from shared import market_calendar as mc
from shared import scalp_config
from shared import symbols as _symbols
from services.options_svc import (
    scalp_intraday_inputs, scalp_intraday_study, scalp_model, scalp_sim, scalp_store,
    scalp_study)
from services.options_svc.scalp_fetch import ct

CT = ZoneInfo("America/Chicago")
PRICES = {"$SPX": 5000.0, "SPY": 500.0, "QQQ": 400.0}
_ROOT = pathlib.Path(__file__).resolve().parents[3]
_COLUMNS = ("day, slot, symbol, iv_scale, role, structure, block, bucket, result, "
            "minutes, entry_value, both_in_bar")


def ts(day, h, m=0):
    return int(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=CT).timestamp())


def trading_days(n, start=dt.date(2026, 9, 1)):
    out, d = [], start
    while len(out) < n:
        if mc.is_trading_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _returns(kind, seed):
    if kind == "trend":
        return [0.001] * 78
    rng = random.Random(seed)
    return [rng.gauss(0.0, 0.0015) for _ in range(78)]


def seed_day(db, day, kind="walk", seed=0, spx_last=(14, 55), vix=True, hole=None):
    """Bars 08:30 .. 14:55 (78 of them) for the three symbols and $VIX.
    ``hole`` is ``(symbol, hour, minute)`` of one bar left out."""
    rets = _returns(kind, seed)
    rows = []
    for sym, p0 in PRICES.items():
        price = p0
        for i, r in enumerate(rets):
            start = ts(day, 8, 30) + 300 * i
            o, c = price, price * (1 + r)
            h, lo = max(o, c) * 1.0004, min(o, c) * 0.9996
            price = c
            if sym == "$SPX" and start > ts(day, *spx_last):
                continue
            if hole is not None and hole[0] == sym and start == ts(day, hole[1], hole[2]):
                continue
            rows.append((sym, 5, start, o, h, lo, c, 0.0 if sym == "$SPX" else 1000.0))
    if vix:
        rows += [("$VIX", 5, ts(day, 8, 30) + 300 * i, 20.0, 20.0, 20.0, 20.0, 0.0)
                 for i in range(78)]
    scalp_store.upsert_bars(rows, db_path=db)


def _all(db, table, order):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
    finally:
        conn.close()


def _outcome_tuples(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT {_COLUMNS} FROM intraday_outcomes "
                            "ORDER BY day, slot, symbol, iv_scale, role").fetchall()
    finally:
        conn.close()


def _outcomes(db, where="1=1", args=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM intraday_outcomes WHERE {where} "
            "ORDER BY day, slot, symbol, iv_scale, role", args)]
    finally:
        conn.close()


def _verdicts(db):
    return {(r[0], r[1], r[2], r[3]): json.loads(r[4])
            for r in _all(db, "intraday_verdicts", "symbol, block, role, bucket")}


@pytest.fixture(autouse=True)
def _no_big_ten(monkeypatch):
    monkeypatch.setattr(_symbols, "big10", lambda: ())


@pytest.fixture
def db(tmp_path):
    return tmp_path / "scalp.db"


@pytest.fixture
def days():
    return trading_days(8)


@pytest.fixture
def walked(db, days):
    for i, d in enumerate(days):
        seed_day(db, d, seed=i)
    return db


# ── the rows ─────────────────────────────────────────────────────────────────
def test_rebuild_rows_are_the_simulated_trade_from_the_slot_entry(db, days):
    seed_day(db, days[0], kind="trend")
    counts = scalp_intraday_study.rebuild(db_path=db)
    rows = _outcomes(db, "iv_scale = 1.0")
    assert counts["outcomes"] == len(_outcomes(db)) > 0
    by = {(r["slot"], r["symbol"], r["role"]): r for r in rows}
    step = dict.fromkeys(scalp_config.intraday_slots(days[0]))
    assert {r["slot"] for r in rows} == set(step)
    for slot in ("08:51", "10:06", "13:51"):
        r = scalp_intraday_inputs.reading("SPY", days[0], slot, db_path=db)
        assert r.direction == 1
        entry = ct(r.entry_ts)
        path = scalp_store.bars("SPY", 5, r.entry_ts, r.entry_ts + 3600, db_path=db)
        bucket = scalp_model.bucket_of(scalp_intraday_inputs.alignment(r),
                                       scalp_config.buckets())
        for role in scalp_sim.ROLES:
            structure = scalp_sim.structure_for(role, 1)
            strikes = scalp_sim.strikes_for(structure, r.entry_spot,
                                            scalp_config.strike_step("SPY"),
                                            scalp_config.width("SPY"),
                                            scalp_config.otm_steps())
            want = scalp_sim.simulate(structure, r.entry_spot, strikes, entry, days[0],
                                      r.vol_at_entry, path, 5)
            got = by[(slot, "SPY", role)]
            assert got["structure"] == structure
            assert got["block"] == scalp_config.intraday_block(slot)
            assert got["bucket"] == bucket
            assert (got["result"], got["minutes"], got["both_in_bar"]) == (
                want.result, want.minutes, int(want.both_in_bar))
            assert got["entry_value"] == pytest.approx(want.entry_value)
    assert by[("08:51", "SPY", "long_with")]["block"] == "morning"
    assert by[("10:06", "SPY", "long_with")]["block"] == "midday"
    assert by[("13:51", "SPY", "long_with")]["block"] == "afternoon"


def test_rebuild_computes_every_scale_and_the_current_one(db, days, monkeypatch):
    seed_day(db, days[0], kind="trend")
    monkeypatch.setattr(scalp_config, "iv_scale", lambda: 0.9)
    scalp_intraday_study.rebuild(db_path=db)
    scales = sorted({r["iv_scale"] for r in _outcomes(db)})
    assert scales == sorted(set(scalp_config.iv_scales()) | {0.9})
    n = len(scalp_config.intraday_slots(days[0])) * 3 * 4
    for s in scales:
        assert len(_outcomes(db, "iv_scale = ?", (s,))) == n


def test_rebuild_skips_short_sessions(db, days):
    seed_day(db, days[0], seed=1)
    seed_day(db, days[1], seed=2, spx_last=(11, 55))   # $SPX only: SPY/QQQ are full
    seed_day(db, days[2], seed=3)
    counts = scalp_intraday_study.rebuild(db_path=db)
    stored = {r["day"] for r in _outcomes(db)}
    assert days[1].isoformat() not in stored
    assert {days[0].isoformat(), days[2].isoformat()} <= stored
    slots = len(scalp_config.intraday_slots(days[1]))
    assert counts["skipped"]["short_session"] == slots * 3


def test_rebuild_skips_no_vol_and_a_path_with_a_hole(db, days):
    seed_day(db, days[0], kind="trend", vix=False)
    seed_day(db, days[1], kind="trend", hole=("SPY", 9, 0))
    counts = scalp_intraday_study.rebuild(db_path=db)
    rows = _outcomes(db)
    assert days[0].isoformat() not in {r["day"] for r in rows}
    assert counts["skipped"]["no_vol"] == len(scalp_config.intraday_slots(days[0])) * 3
    d1 = days[1].isoformat()
    got = {(r["slot"], r["symbol"]) for r in rows if r["day"] == d1}
    # 08:51 enters at 08:50: the 09:00 bar is inside its hour-long path.
    assert ("08:51", "SPY") not in got
    assert ("08:51", "QQQ") in got and ("08:51", "$SPX") in got
    # 09:06 enters at 09:05 and its last 15 minutes need the 09:00 bar: no direction.
    assert ("09:06", "SPY") not in got
    # 09:21 enters at 09:20, past the hole on both sides.
    assert ("09:21", "SPY") in got
    assert counts["skipped"]["data_hole"] == 1
    assert counts["skipped"]["no_direction"] >= 1


def test_rebuild_stores_a_simulated_skip_and_never_labels_it(db, days, monkeypatch):
    seed_day(db, days[0], kind="trend")
    seed_day(db, days[1], kind="trend")
    monkeypatch.setattr(scalp_config, "min_premium", lambda: 1e9)
    scalp_intraday_study.rebuild(db_path=db)
    rows = _outcomes(db)
    assert rows and {r["result"] for r in rows} == {"skip"}
    for v in _verdicts(db).values():
        assert all(c["n_scored"] == 0 for c in v["scales"].values())
    rates = scalp_intraday_study.current_rates("SPY", "morning", "high", db_path=db)
    assert rates["long_with"]["n_base_days"] == 0


def test_rebuild_replaces_rather_than_appends(db, days):
    seed_day(db, days[0], kind="trend")
    scalp_intraday_study.rebuild(db_path=db)
    n = len(_outcomes(db))
    seed_day(db, days[1], kind="trend")
    scalp_intraday_study.rebuild(db_path=db)
    assert len(_outcomes(db)) == 2 * n


def test_rebuild_is_deterministic(tmp_path, days):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    for path in (a, b):
        for i, d in enumerate(days[:4]):
            seed_day(path, d, seed=10 + i)
    first = scalp_intraday_study.rebuild(db_path=a)
    snap = (_outcome_tuples(a), _all(a, "intraday_verdicts", "symbol, block, role, bucket"))
    second = scalp_intraday_study.rebuild(db_path=a)
    assert (_outcome_tuples(a),
            _all(a, "intraday_verdicts", "symbol, block, role, bucket")) == snap
    assert first["outcomes"] == second["outcomes"] > 0
    scalp_intraday_study.rebuild(db_path=b)
    assert _outcome_tuples(b) == snap[0]
    assert _all(b, "intraday_verdicts", "symbol, block, role, bucket") == snap[1]


def test_rebuild_leaves_the_0836_tables_untouched(walked, days):
    db = walked
    scalp_study.rebuild(db_path=db)
    d0 = days[0].isoformat()
    scalp_store.save_call(d0, "SPY", {"x": 1}, db_path=db)
    scalp_store.save_reading(d0, "08:51", "SPY", {"y": 2}, db_path=db)
    scalp_store.save_intraday_call(d0, "08:51", "SPY", {"z": 3}, db_path=db)
    tables = {"bars": "symbol, interval, ts", "outcomes": "day, symbol, iv_scale, role, interval",
              "mornings": "day, symbol", "verdicts": "symbol, role, bucket",
              "calls": "day, symbol", "grades": "day, symbol, role",
              "iv_calibration": "day, symbol", "intraday_readings": "day, slot, symbol",
              "intraday_calls": "day, slot, symbol",
              "intraday_chain_slices": "day, slot, symbol",
              "intraday_grades": "day, slot, symbol, role"}
    before = {t: _all(db, t, o) for t, o in tables.items()}
    assert before["outcomes"] and before["verdicts"] and before["calls"]
    scalp_intraday_study.rebuild(db_path=db)
    assert _outcomes(db)
    assert {t: _all(db, t, o) for t, o in tables.items()} == before


# ── the verdicts: walk-forward, the day as the unit ─────────────────────────
def test_rebuild_rows_of_one_day_never_see_each_other(walked, days):
    """Flip every outcome on day D: no prediction on day D or earlier may move -
    in particular not a later slot's on day D itself."""
    scalp_intraday_study.rebuild(db_path=walked)
    rows = _outcome_tuples(walked)
    target = days[4].isoformat()
    flip = {"win": "loss", "loss": "win", "timeout": "win"}
    flipped = [r[:8] + (flip.get(r[8], r[8]),) + r[9:] if r[0] == target else r
               for r in rows]
    base = scalp_intraday_study.scored_streams(rows)
    again = scalp_intraday_study.scored_streams(flipped)
    assert set(base) == set(again)
    same_day_slots = moved_later = 0
    for key, (stream, scored) in base.items():
        stream2, scored2 = again[key]
        assert [(r["day"], r["slot"]) for r in stream] == \
            [(r["day"], r["slot"]) for r in stream2]
        for r, s, s2 in zip(stream, scored, scored2):
            # The calendar date the row belongs to, whatever key the stream uses.
            date = r["day"][:10]
            if date <= target:
                assert s == s2, (key, r)
                if date == target:
                    same_day_slots += 1
            elif s != s2:
                moved_later += 1
    # Not vacuous: day D has several slots in one stream, and the flip DOES move
    # the predictions after it.
    assert same_day_slots > len(base)
    assert moved_later > 0


def test_streams_are_one_per_symbol_block_role_scale_sorted_by_day_then_slot(walked):
    scalp_intraday_study.rebuild(db_path=walked)
    rows = _outcome_tuples(walked)
    assert rows
    streams = scalp_intraday_study.scored_streams(rows)
    for (sym, block, role, scale), (stream, _scored) in streams.items():
        keys = [(r["day"], r["slot"]) for r in stream]
        assert keys == sorted(keys)
        # The walk-forward key is the DAY, an ISO date (a slot is never its own "day").
        assert all(dt.date.fromisoformat(r["day"]).isoformat() == r["day"]
                   for r in stream)
        mine = [r for r in rows if (r[2], r[6], r[4], r[3]) == (sym, block, role, scale)
                and r[8] != "skip"]
        assert len(stream) == len(mine)


def test_rebuild_verdicts_are_the_walk_forward_by_day_of_the_stored_rows(
        walked, monkeypatch):
    monkeypatch.setattr(scalp_config, "min_samples", lambda: 2)
    scalp_intraday_study.rebuild(db_path=walked)
    stored = _verdicts(walked)
    rows = _outcomes(walked)
    groups = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["day"], r["slot"])):
        if r["result"] == "skip":
            continue
        groups[(r["symbol"], r["block"], r["role"], r["iv_scale"])].append(
            {"day": r["day"], "bucket": r["bucket"],
             "win": 1 if r["result"] == "win" else 0})
    blocks = sorted({r["block"] for r in rows})
    assert blocks == ["afternoon", "midday", "morning"]
    assert len(stored) == 3 * 3 * 4 * 3
    for sym in PRICES:
        for block in blocks:
            for role in scalp_sim.ROLES:
                by_scale = {}
                for s in scalp_config.iv_scales():
                    g = groups[(sym, block, role, s)]
                    by_scale[s] = scalp_model.verdict_by_day(
                        scalp_model.walk_forward(g), g, 2,
                        min_margin=scalp_config.min_brier_margin())
                across = scalp_model.edge_across_scales(by_scale)
                for b in scalp_model.BUCKETS:
                    got = stored[(sym, block, role, b)]
                    assert got["edge"] == across[b]
                    for s in scalp_config.iv_scales():
                        assert got["scales"][str(s)] == by_scale[s][b]
    # Some cell was scored over several days, so the comparison is not of empties.
    assert any(c["n_scored"] >= 2 for v in stored.values() for c in v["scales"].values())


def _gains(v):
    """The cell's smallest Brier gain over the base across the study scales."""
    cells = [v["scales"][str(s)] for s in scalp_config.iv_scales()]
    if any(c["brier_bucket"] is None for c in cells):
        return None
    return min(c["brier_base"] - c["brier_bucket"] for c in cells)


def test_an_intraday_edge_needs_the_margin_at_every_scale(walked, monkeypatch):
    monkeypatch.setattr(scalp_config, "min_samples", lambda: 1)
    monkeypatch.setattr(scalp_config, "min_brier_margin", lambda: 0.0)
    scalp_intraday_study.rebuild(db_path=walked)
    plain = _verdicts(walked)
    gains = sorted({_gains(v) for v in plain.values() if v["edge"]})
    assert len(gains) >= 2, "the fixture must give edges of different sizes"
    # A margin between the smallest and the largest passing gain.
    margin = (gains[0] + gains[-1]) / 2
    monkeypatch.setattr(scalp_config, "min_brier_margin", lambda: margin)
    scalp_intraday_study.rebuild(db_path=walked)
    strict = _verdicts(walked)
    refused = kept = 0
    for key, v in plain.items():
        g = _gains(v)
        # The numbers do not move; only the edge does.
        assert strict[key]["scales"] == {
            s: {**c, "edge": strict[key]["scales"][s]["edge"]} for s, c in v["scales"].items()}
        if v["edge"] and g < margin:
            assert strict[key]["edge"] is False  # a positive gain under the margin
            refused += 1
        elif v["edge"]:
            assert strict[key]["edge"] is True  # over the margin at every scale
            kept += 1
        else:
            assert strict[key]["edge"] is False
        assert strict[key]["min_margin"] == margin
    assert refused and kept


# ── current rates ────────────────────────────────────────────────────────────
def test_current_rates_counts_days_and_rows(walked):
    scalp_intraday_study.rebuild(db_path=walked)
    rows = [r for r in _outcomes(walked, "symbol = 'SPY' AND block = 'morning' "
                                 "AND iv_scale = 1.0") if r["result"] != "skip"]
    stored = _verdicts(walked)
    for bucket in scalp_model.BUCKETS:
        rates = scalp_intraday_study.current_rates("SPY", "morning", bucket,
                                                   db_path=walked)
        assert set(rates) == set(scalp_sim.ROLES)
        for role in scalp_sim.ROLES:
            mine = [r for r in rows if r["role"] == role]
            cell = [r for r in mine if r["bucket"] == bucket]
            wins = sum(r["result"] == "win" for r in cell)
            got = rates[role]
            assert got["n_days"] == len({r["day"] for r in cell})
            assert got["n_base_days"] == len({r["day"] for r in mine})
            assert got["p_base"] == pytest.approx(
                sum(r["result"] == "win" for r in mine) / len(mine))
            if cell:
                assert got["p"] == pytest.approx(wins / len(cell))
                assert (got["lo"], got["hi"]) == pytest.approx(
                    scalp_model.wilson_days(wins, len(cell), got["n_days"]))
            else:
                assert got["p"] is None and got["lo"] is None
            assert got["edge"] == stored[("SPY", "morning", role, bucket)]["edge"]
    none = scalp_intraday_study.current_rates("SPY", "morning", None, db_path=walked)
    assert none["long_with"]["p"] is None and none["long_with"]["n_days"] == 0
    assert none["long_with"]["p_base"] is not None


def test_current_rates_on_an_empty_store(db):
    rates = scalp_intraday_study.current_rates("SPY", "morning", "high", db_path=db)
    assert rates["long_with"] == {"p": None, "lo": None, "hi": None, "n_days": 0,
                                  "p_base": None, "n_base_days": 0, "edge": False}


# ── the report ───────────────────────────────────────────────────────────────
def _report():
    spec = importlib.util.spec_from_file_location(
        "scalp_intraday_report", _ROOT / "tools" / "scalp_intraday_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_says_k_of_cells(walked, monkeypatch):
    monkeypatch.setattr(scalp_config, "min_samples", lambda: 2)
    scalp_intraday_study.rebuild(db_path=walked)
    text = _report().render(walked)
    m = re.search(r"(\d+) of (\d+) cells pass at every scale", text)
    assert m, text[:2000]
    stored = _verdicts(walked)
    scored = [v for v in stored.values()
              if any(c["n_scored"] > 0 for c in v["scales"].values())]
    assert int(m.group(2)) == len(scored) > 0
    assert int(m.group(1)) == sum(1 for v in scored if v["edge"])
    assert "would pass" in text  # the chance estimate sits beside k
    header = text.split("## Summary", 1)[0]
    assert f"Brier margin {scalp_config.min_brier_margin():g}" in header
    if int(m.group(1)):
        assert "smallest Brier margin" in text
    for sym in PRICES:
        assert f"## {sym}" in text
    for block in ("morning", "midday", "afternoon"):
        assert block in text
    assert "DIAGNOSTIC" in text and "Up-only" in text and "Down-only" in text
    assert "days" in text.lower()
    for word in ("expectancy", "average p&l", "avg p&l", "p&l per"):
        assert word not in text.lower()


def test_report_says_no_edge_for_a_symbol_with_none(db, days):
    for i, d in enumerate(days[:3]):
        seed_day(db, d, seed=20 + i)
    scalp_intraday_study.rebuild(db_path=db)   # min_samples 20 > 3 days: no edge
    text = _report().render(db)
    assert re.search(r"0 of \d+ cells pass at every scale", text)
    for sym in PRICES:
        assert f"{sym}: no edge" in text


def test_report_opens_the_store_read_only(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(sqlite3.OperationalError):
        _report().render(missing)
    assert not missing.exists()
