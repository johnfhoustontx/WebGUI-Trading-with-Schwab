"""The Opening Scalp study (scalp_study) on a tmp store of hand-built bars.

Every morning below is constructed so its outcome is known before the study runs:

* the 08:30 open bar sets the direction AND the alignment bucket. Nothing else is
  stored that ``scalp_inputs`` reads as an agreement input (no futures, no ETFs,
  no $VIX premarket), so the only vote is where the open bar closed in its range:
  an up bar closing at 0.8 of it agrees (alignment 1.0, ``high``), one closing at
  0.3 disagrees (alignment 0.0, ``low``), and a bar with no body has no direction;
* the twelve 5-minute bars from 08:35 are the trade's path: ``up`` jumps +1% in
  the first bar (the long call and the put spread win, the long put and the call
  spread lose), ``flat`` never moves (every role times out), ``both`` spans +1%
  and -1% inside the first bar (a 5-minute both-levels bar: every role loses);
* $VIX's 08:30 bar closes at 20, so every symbol prices at 20% (QQQ falls back to
  $VIX: no $VXN is stored);
* $SPX carries regular-session bars to 14:55, or to 11:55 for a short session.
"""
import datetime as dt
import importlib.util
import json
import pathlib
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from shared import market_calendar as mc
from shared import scalp_config
from services.options_svc import scalp_model, scalp_store, scalp_study
from services.options_svc.scalp_sim import ROLES

CT = ZoneInfo("America/Chicago")
PRICES = {"$SPX": 5000.0, "SPY": 500.0, "QQQ": 400.0}
_ROOT = pathlib.Path(__file__).resolve().parents[3]


def ts(day, h, m=0):
    return int(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=CT).timestamp())


def trading_days(n, start=dt.date(2026, 9, 1)):
    out, d = [], start
    while len(out) < n:
        if mc.is_trading_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _open_bar(p, bucket):
    if bucket == "high":
        return p, p * 1.002, p * 0.9995, p * 1.0015
    if bucket == "low":
        return p, p * 1.004, p * 0.999, p * 1.0005
    return p, p, p, p          # no body: no direction


def _path5(c, kind):
    """The twelve 5-minute path bars' (o, h, l, c) after an entry at ``c``."""
    if kind == "up":
        first = (c, c * 1.01, c, c * 1.01)
        rest = (c * 1.01,) * 4
    elif kind == "both":
        first = (c, c * 1.01, c * 0.99, c)
        rest = (c,) * 4
    else:
        first = (c, c, c, c)
        rest = (c,) * 4
    return [first] + [rest] * 11


def _path1(c, kind):
    """Sixty 1-minute bars. ``both`` goes up FIRST: +1% in the 08:35 bar, -1% in
    the 08:37 bar, so the 1-minute walk wins the long call the 5-minute bar loses."""
    bars = [(c, c, c, c)] * 60
    if kind == "both":
        bars = list(bars)
        bars[0] = (c, c * 1.01, c, c * 1.005)
        bars[2] = (c * 1.005, c * 1.005, c * 0.99, c)
    elif kind == "up":
        bars = [(c, c * 1.01, c, c * 1.01)] + [(c * 1.01,) * 4] * 59
    return bars


def seed_day(db, day, kind="up", bucket="high", spx_last=(14, 55), one_min=False,
             vix=True, hole=None, symbols=None):
    rows = []
    for sym in (symbols or PRICES):
        p = PRICES[sym]
        o, h, lo, c = _open_bar(p, bucket)
        rows.append((sym, 5, ts(day, 8, 30), o, h, lo, c, 1000.0))
        for i, bar in enumerate(_path5(c, kind)):
            if hole == sym and i == 5:
                continue
            rows.append((sym, 5, ts(day, 8, 35) + 300 * i, *bar, 1000.0))
        if sym == "$SPX":
            t, end = ts(day, 9, 35), ts(day, *spx_last)
            while t <= end:
                rows.append((sym, 5, t, c, c, c, c, 0.0))
                t += 300
        if one_min:
            for i in range(5):
                rows.append((sym, 1, ts(day, 8, 30) + 60 * i, p, h, lo,
                             c if i == 4 else p, 100.0))
            for i, bar in enumerate(_path1(c, kind)):
                rows.append((sym, 1, ts(day, 8, 35) + 60 * i, *bar, 100.0))
    if vix:
        rows.append(("$VIX", 5, ts(day, 8, 30), 20.0, 20.0, 20.0, 20.0, 0.0))
    scalp_store.upsert_bars(rows, db_path=db)


def _all(db, table, order):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
    finally:
        conn.close()


def _outcomes(db, where="1=1", args=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM outcomes WHERE {where} "
            "ORDER BY day, symbol, iv_scale, role, interval", args)]
    finally:
        conn.close()


@pytest.fixture
def db(tmp_path):
    return tmp_path / "scalp.db"


@pytest.fixture
def days():
    return trading_days(8)


# ── rebuild ──────────────────────────────────────────────────────────────────
def test_rebuild_outcomes_match_the_constructed_paths(db, days):
    seed_day(db, days[0], kind="up")
    seed_day(db, days[1], kind="flat", bucket="low")
    seed_day(db, days[2], kind="both")
    scalp_study.rebuild(db_path=db)
    one = _outcomes(db, "interval = 5 AND iv_scale = 1.0")
    by = {(r["day"], r["symbol"], r["role"]): r for r in one}
    for sym in PRICES:
        d0, d1, d2 = (d.isoformat() for d in days[:3])
        assert by[(d0, sym, "long_with")]["result"] == "win"
        assert by[(d0, sym, "long_with")]["structure"] == "long_call"
        assert by[(d0, sym, "credit_with")]["result"] == "win"
        assert by[(d0, sym, "credit_with")]["structure"] == "pcs"
        assert by[(d0, sym, "long_against")]["result"] == "loss"
        assert by[(d0, sym, "credit_against")]["result"] == "loss"
        for role in ROLES:
            assert by[(d0, sym, role)]["bucket"] == "high"
            assert by[(d1, sym, role)]["result"] == "timeout"
            assert by[(d1, sym, role)]["bucket"] == "low"
            assert by[(d2, sym, role)]["result"] == "loss"
            assert by[(d2, sym, role)]["both_in_bar"] in (0, 1)
        # The long call's 5-minute bar spanned both levels: a loss, flagged.
        assert by[(d2, sym, "long_with")]["both_in_bar"] == 1


def test_rebuild_computes_every_scale_and_the_current_one(db, days, monkeypatch):
    seed_day(db, days[0], kind="up")
    monkeypatch.setattr(scalp_config, "iv_scale", lambda: 0.9)
    scalp_study.rebuild(db_path=db)
    scales = sorted({r["iv_scale"] for r in _outcomes(db, "interval = 5")})
    assert scales == sorted(set(scalp_config.iv_scales()) | {0.9})
    # Every scale holds every symbol x role.
    for s in scales:
        assert len(_outcomes(db, "interval = 5 AND iv_scale = ?", (s,))) == 3 * 4


def test_rebuild_is_deterministic(tmp_path, days):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    for path in (a, b):
        for i, d in enumerate(days):
            seed_day(path, d, kind=("up", "flat", "both")[i % 3],
                     bucket=("high", "low")[i % 2], one_min=(i % 2 == 0))
    first = scalp_study.rebuild(db_path=a)
    snap = {t: _all(a, t, o) for t, o in (("outcomes", "day, symbol, iv_scale, role, interval"),
                                          ("mornings", "day, symbol"),
                                          ("verdicts", "symbol, role, bucket"))}
    second = scalp_study.rebuild(db_path=a)
    assert {t: _all(a, t, o) for t, o in (("outcomes", "day, symbol, iv_scale, role, interval"),
                                          ("mornings", "day, symbol"),
                                          ("verdicts", "symbol, role, bucket"))} == snap
    assert first["outcomes"] == second["outcomes"] > 0
    # And a separate store holding the same bars gives the same rows.
    scalp_study.rebuild(db_path=b)
    assert _all(b, "outcomes", "day, symbol, iv_scale, role, interval") == snap["outcomes"]
    assert _all(b, "verdicts", "symbol, role, bucket") == snap["verdicts"]


def test_rebuild_replaces_rather_than_appends(db, days):
    seed_day(db, days[0], kind="up")
    scalp_study.rebuild(db_path=db)
    n = len(_outcomes(db))
    seed_day(db, days[1], kind="up")
    scalp_study.rebuild(db_path=db)
    assert len(_outcomes(db)) == 2 * n


def test_rebuild_skips_short_sessions(db, days):
    seed_day(db, days[0], kind="up")
    seed_day(db, days[1], kind="up", spx_last=(11, 55))
    seed_day(db, days[2], kind="up")
    counts = scalp_study.rebuild(db_path=db)
    stored = {r["day"] for r in _outcomes(db)}
    assert days[1].isoformat() not in stored
    assert {days[0].isoformat(), days[2].isoformat()} <= stored
    morn = {(r[0], r[1]): json.loads(r[2]) for r in _all(db, "mornings", "day, symbol")}
    for sym in PRICES:
        assert morn[(days[1].isoformat(), sym)]["skip"] == "short_session"
        assert morn[(days[0].isoformat(), sym)]["skip"] is None
    assert counts["skipped"]["short_session"] == 3


def test_rebuild_skips_a_morning_with_no_direction_vol_or_full_path(db, days):
    seed_day(db, days[0], kind="up", bucket="none")               # no direction
    seed_day(db, days[1], kind="up", vix=False)                    # no vol
    seed_day(db, days[2], kind="up", hole="SPY")                   # SPY path hole
    counts = scalp_study.rebuild(db_path=db)
    rows = _outcomes(db)
    assert {r["day"] for r in rows} == {days[2].isoformat()}
    assert {r["symbol"] for r in rows} == {"$SPX", "QQQ"}
    morn = {(r[0], r[1]): json.loads(r[2]) for r in _all(db, "mornings", "day, symbol")}
    assert morn[(days[0].isoformat(), "SPY")]["skip"] == "no_direction"
    assert morn[(days[1].isoformat(), "SPY")]["skip"] == "no_vol"
    assert morn[(days[2].isoformat(), "SPY")]["skip"] == "data_hole"
    assert counts["skipped"] == {"no_direction": 3, "no_vol": 3, "data_hole": 1}


def test_rebuild_a_symbol_with_no_open_bar_leaves_the_others(db, days):
    seed_day(db, days[0], kind="up", symbols=["$SPX", "QQQ"])
    scalp_study.rebuild(db_path=db)
    assert {r["symbol"] for r in _outcomes(db)} == {"$SPX", "QQQ"}
    morn = {(r[0], r[1]): json.loads(r[2]) for r in _all(db, "mornings", "day, symbol")}
    assert morn[(days[0].isoformat(), "SPY")]["skip"] == "no_open_bar"


def test_rebuild_one_minute_rows_only_at_the_current_scale(db, days):
    seed_day(db, days[0], kind="both", one_min=True)
    seed_day(db, days[1], kind="both")
    scalp_study.rebuild(db_path=db)
    ones = _outcomes(db, "interval = 1")
    assert {r["iv_scale"] for r in ones} == {scalp_config.iv_scale()}
    assert {r["day"] for r in ones} == {days[0].isoformat()}
    assert len(ones) == 3 * 4
    # The same trade on 1-minute bars: up first, so the long call WINS where the
    # 5-minute both-levels bar called it a loss.
    five = {(r["symbol"], r["role"]): r for r in _outcomes(
        db, "interval = 5 AND iv_scale = 1.0 AND day = ?", (days[0].isoformat(),))}
    for r in ones:
        twin = five[(r["symbol"], r["role"])]
        assert (r["structure"], r["bucket"], r["entry_value"]) == (
            twin["structure"], twin["bucket"], twin["entry_value"])
    lw = {r["symbol"]: r for r in ones if r["role"] == "long_with"}
    for sym in PRICES:
        assert lw[sym]["result"] == "win"
        assert five[(sym, "long_with")]["result"] == "loss"


def test_rebuild_verdict_uses_5min_only(tmp_path, days):
    with1, without = tmp_path / "with1.db", tmp_path / "without.db"
    for i, d in enumerate(days):
        kind, bucket = ("both", "up", "flat")[i % 3], ("high", "low")[i % 2]
        seed_day(with1, d, kind=kind, bucket=bucket, one_min=True)
        seed_day(without, d, kind=kind, bucket=bucket, one_min=False)
    scalp_study.rebuild(db_path=with1)
    scalp_study.rebuild(db_path=without)
    ones = _outcomes(with1, "interval = 1")
    assert ones, "the fixture must produce 1-minute rows"
    # Not vacuous: the 1-minute rows disagree with the 5-minute ones somewhere,
    # so a verdict that read them would move.
    five = {(r["day"], r["symbol"], r["role"]): r["result"] for r in _outcomes(
        with1, "interval = 5 AND iv_scale = 1.0")}
    assert any(five[(r["day"], r["symbol"], r["role"])] != r["result"] for r in ones)
    assert _all(with1, "verdicts", "symbol, role, bucket") == \
        _all(without, "verdicts", "symbol, role, bucket")
    assert _all(with1, "verdicts", "symbol, role, bucket")
    for sym in PRICES:
        for b in scalp_model.BUCKETS:
            assert scalp_study.current_rates(sym, b, db_path=with1) == \
                scalp_study.current_rates(sym, b, db_path=without)


def test_rebuild_verdicts_are_the_walk_forward_of_the_5min_rows(db, days, monkeypatch):
    monkeypatch.setattr(scalp_config, "min_samples", lambda: 2)
    for i, d in enumerate(days):
        seed_day(db, d, kind=("up", "flat")[i % 2], bucket=("high", "low")[i % 2],
                 one_min=True)
    scalp_study.rebuild(db_path=db)
    stored = {(r[0], r[1], r[2]): json.loads(r[3])
              for r in _all(db, "verdicts", "symbol, role, bucket")}
    for sym in PRICES:
        for role in ROLES:
            by_scale = {}
            for s in scalp_config.iv_scales():
                rows = [{"day": r["day"], "bucket": r["bucket"],
                         "win": 1 if r["result"] == "win" else 0}
                        for r in _outcomes(db, "symbol = ? AND role = ? AND iv_scale = ? "
                                           "AND interval = 5 AND result != 'skip'",
                                           (sym, role, s))]
                by_scale[s] = scalp_model.verdict(scalp_model.walk_forward(rows), rows, 2)
            across = scalp_model.edge_across_scales(by_scale)
            for b in scalp_model.BUCKETS:
                got = stored[(sym, role, b)]
                assert got["edge"] == across[b]
                for s in scalp_config.iv_scales():
                    assert got["scales"][str(s)] == by_scale[s][b]
    # This fixture's high bucket always wins long_with and low always times out:
    # walk-forward that IS an edge once enough mornings are scored.
    assert stored[("SPY", "long_with", "high")]["edge"] is True


def test_current_rates_counts_every_stored_day_at_the_current_scale(db, days):
    kinds = [("up", "high"), ("up", "high"), ("flat", "high"), ("up", "low"),
             ("flat", "low")]
    for d, (kind, bucket) in zip(days, kinds):
        seed_day(db, d, kind=kind, bucket=bucket)
    scalp_study.rebuild(db_path=db)
    rates = scalp_study.current_rates("SPY", "high", db_path=db)
    assert set(rates) == set(ROLES)
    lw = rates["long_with"]
    assert (lw["n"], lw["n_base"]) == (3, 5)
    assert lw["p"] == pytest.approx(2 / 3)
    assert lw["p_base"] == pytest.approx(3 / 5)
    assert (lw["lo"], lw["hi"]) == pytest.approx(scalp_model.wilson(2, 3))
    assert lw["edge"] is False
    assert rates["long_against"]["p"] == 0.0
    # An empty bucket: no rate, but the base is still there.
    empty = scalp_study.current_rates("SPY", "mid", db_path=db)["long_with"]
    assert empty["n"] == 0 and empty["p"] is None and empty["lo"] is None
    assert empty["n_base"] == 5
    # No bucket (alignment unknown): the base only.
    none = scalp_study.current_rates("SPY", None, db_path=db)["long_with"]
    assert none["n"] == 0 and none["p"] is None and none["p_base"] == pytest.approx(3 / 5)


def test_current_rates_on_an_empty_store(db):
    rates = scalp_study.current_rates("SPY", "high", db_path=db)
    assert rates["long_with"] == {"p": None, "lo": None, "hi": None, "n": 0,
                                  "p_base": None, "n_base": 0, "edge": False}


# ── the report ───────────────────────────────────────────────────────────────
def _report():
    spec = importlib.util.spec_from_file_location(
        "scalp_study_report", _ROOT / "tools" / "scalp_study_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_prints_the_study_without_an_expectancy(db, days):
    for i, d in enumerate(days):
        seed_day(db, d, kind=("both", "up", "flat")[i % 3],
                 bucket=("high", "low")[i % 2], one_min=(i < 4))
    scalp_study.rebuild(db_path=db)
    text = _report().render(db)
    for sym in PRICES:
        assert f"## {sym}" in text
    assert "DIAGNOSTIC" in text
    assert "Up-only" in text and "Down-only" in text and "Pooled" in text
    assert "1-minute" in text and "both levels in one bar" in text
    assert "no bucket" in text.lower()
    for word in ("expectancy", "average p&l", "avg p&l"):
        assert word not in text.lower()


def test_report_opens_the_store_read_only(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(sqlite3.OperationalError):
        _report().render(missing)
    assert not missing.exists()
