"""The fit of the Opening Scalp's weighted chance (scalp_prob_fit).

The fit tests build ``Row``s directly (no store): a factor drawn at random, the
label drawn from a known logistic model, so the weight the fit should find is
known before it runs. The store tests (``training_rows``, ``refit``) seed a
``tmp_path`` store with hand-built 5-minute bars, rebuild the two studies on it
and read the outcomes back, as ``scalp_intraday_study``'s own tests do.
"""
import datetime as dt
import json
import math
import random
import sqlite3
from collections import defaultdict
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.options_svc import (
    scalp_intraday_study, scalp_prob_fit, scalp_store, scalp_study)
from services.options_svc.scalp_prob_fit import Row
from shared import market_calendar as mc
from shared import scalp_config
from shared import symbols as _symbols

CT = ZoneInfo("America/Chicago")
PRICES = {"$SPX": 5000.0, "SPY": 500.0, "QQQ": 400.0}
SLOTS = scalp_config.intraday_slots(None)


def trading_days(n, start=dt.date(2026, 2, 2)):
    out, d = [], start
    while len(out) < n:
        if mc.is_trading_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z))


def priors():
    return scalp_config.prob_priors()


def prior_sd():
    return scalp_config.prob_prior_sd()


def row(day, factors, win, symbol="SPY", role="long_with", kind="morning",
        slot="08:36", weight=1.0):
    return Row(kind=kind, day=day.isoformat() if isinstance(day, dt.date) else day,
               slot=slot, symbol=symbol, role=role, win=win, weight=weight,
               factors=dict(factors))


def planted(days, weight, seed, per_day=1, kind="morning", role="long_with",
            name="slope_5", symbol="SPY"):
    """Rows whose ``name`` is U(-1, 1) and whose label is drawn from
    sigmoid(weight * x); ``per_day`` independent rows a day, each weighing
    1 / per_day."""
    rng = random.Random(seed)
    out = []
    for d in days:
        for i in range(per_day):
            x = rng.uniform(-1.0, 1.0)
            win = 1 if rng.random() < sigmoid(weight * x) else 0
            slot = SLOTS[i % len(SLOTS)] if kind == "intraday" else "08:36"
            out.append(row(d, {name: x}, win, symbol=symbol, role=role, kind=kind,
                           slot=slot, weight=1.0 / per_day))
    return out


# ── the fit ──────────────────────────────────────────────────────────────────
def test_fit_recovers_a_planted_weight():
    # 200 days x 1 symbol, 21 independent slots a day (each 1/21 of its day, so
    # the evidence is still 200 days'). U(-1, 1) has an interquartile range of
    # about 1, so a scaled unit is about one raw unit and the weight is ~1.2.
    rows = planted(trading_days(200), 1.2, seed=11, per_day=21, kind="intraday")
    model = scalp_prob_fit.fit(rows, "long_with", "intraday", priors(), prior_sd())
    assert model.weights["slope_5"] == pytest.approx(1.2, abs=0.25)
    assert model.n_days == 200
    assert model.days["slope_5"] == 200
    assert set(model.intercepts) == {"SPY"}


def test_fit_recovers_a_planted_weight_across_seeds():
    # The tolerance is not a lucky seed: twenty seeds all land inside it.
    days = trading_days(200)
    for seed in range(20):
        rows = planted(days, 1.2, seed=seed, per_day=21, kind="intraday")
        model = scalp_prob_fit.fit(rows, "long_with", "intraday", priors(), prior_sd())
        assert abs(model.weights["slope_5"] - 1.2) < 0.25, seed


def test_tight_prior_holds_a_thin_source():
    # 200 days of slope_5; netprem read on five of them only, planted at 2.0.
    days = trading_days(200)
    rng = random.Random(5)
    thin = {days[i]: v for i, v in zip((20, 60, 100, 140, 180),
                                       (-0.8, -0.4, 0.4, 0.6, 0.8))}
    rows = []
    for d in days:
        x = rng.uniform(-1.0, 1.0)
        n = thin.get(d)
        z = 1.2 * x + (2.0 * n if n is not None else 0.0)
        win = 1 if rng.random() < sigmoid(z) else 0
        rows.append(row(d, {"slope_5": x, "netprem": n}, win))
    model = scalp_prob_fit.fit(rows, "long_with", "morning", priors(), prior_sd())
    assert model.weights["netprem"] == pytest.approx(0.10, abs=0.05)
    assert model.days["netprem"] == 5
    assert model.days["slope_5"] == 200


def test_against_role_prior_is_negated():
    # No row carries room: its weight is its prior, negated for a trade against
    # the move, and the data's factor is still fitted.
    rows = planted(trading_days(60), 1.0, seed=3, role="long_against")
    model = scalp_prob_fit.fit(rows, "long_against", "morning", priors(), prior_sd())
    assert model.weights["room"] == pytest.approx(-0.15, abs=1e-9)
    assert model.days["room"] == 0
    with_move = scalp_prob_fit.fit(
        [row(r.day, r.factors, r.win, role="long_with") for r in rows],
        "long_with", "morning", priors(), prior_sd())
    assert with_move.weights["room"] == pytest.approx(0.15, abs=1e-9)


def test_fit_only_reads_its_own_role_and_kind():
    rows = planted(trading_days(60), 1.0, seed=4)
    other = [row(r.day, r.factors, 1 - r.win, role="credit_with") for r in rows]
    alone = scalp_prob_fit.fit(rows, "long_with", "morning", priors(), prior_sd())
    mixed = scalp_prob_fit.fit(rows + other, "long_with", "morning", priors(), prior_sd())
    assert mixed.weights == pytest.approx(alone.weights)
    assert mixed.intercepts == pytest.approx(alone.intercepts)


def test_each_symbol_day_counts_once():
    # Day X's row has no slope_5 reading (a gap), so repeating it 21 times leaves
    # the scaling as it was; what differs is only how much day X weighs.
    days = trading_days(61)
    base = planted(days[:60], 1.0, seed=9)
    special = {"slope_5": None, "block:midday": 1.0, "block:afternoon": 0.0}

    def model_with(copies, weight):
        rows = base + [row(days[60], special, 1, weight=weight) for _ in range(copies)]
        return scalp_prob_fit.fit(rows, "long_with", "morning", priors(), prior_sd())

    one = model_with(1, 1.0)
    split = model_with(21, 1.0 / 21)
    counted = model_with(21, 1.0)           # the day counted 21 times
    for name in one.weights:
        assert split.weights[name] == pytest.approx(one.weights[name], abs=1e-6)
    assert split.intercepts["SPY"] == pytest.approx(one.intercepts["SPY"], abs=1e-6)
    assert split.n_days == one.n_days == 61
    # ...and the test can tell: a day counted 21 times moves the fit.
    assert abs(counted.weights["block:midday"] - one.weights["block:midday"]) > 0.1


def test_fit_with_no_rows_is_empty():
    model = scalp_prob_fit.fit([], "long_with", "morning", priors(), prior_sd())
    assert model.weights == {} and model.intercepts == {} and model.n_days == 0


def test_predict_is_the_intercept_plus_each_push():
    rows = planted(trading_days(80), 1.5, seed=2)
    model = scalp_prob_fit.fit(rows, "long_with", "morning", priors(), prior_sd())
    p, pushes = scalp_prob_fit.predict(model, "SPY", {"slope_5": 0.5, "room": None})
    scale = model.scaling["slope_5"]
    assert pushes["slope_5"] == pytest.approx(model.weights["slope_5"] * 0.5 / scale)
    assert pushes["room"] == 0.0                     # a gap pushes nothing
    z = model.intercepts["SPY"] + sum(pushes.values())
    assert p == pytest.approx(sigmoid(z))
    # An unknown symbol has the prior intercept, 0.
    p2, _ = scalp_prob_fit.predict(model, "IWM", {"slope_5": 0.5})
    assert p2 == pytest.approx(sigmoid(sum(pushes.values())))


# ── the walk-forward check ───────────────────────────────────────────────────
def _week(day):
    return dt.date.fromisoformat(day).isocalendar()[:2]


def test_walk_forward_never_scores_a_fitted_week(monkeypatch):
    rows = planted(trading_days(60), 2.0, seed=1) + planted(
        trading_days(60), 2.0, seed=8, symbol="QQQ")
    weeks = sorted({_week(r.day) for r in rows})
    scored = weeks[scalp_config.prob_min_train_weeks():]
    real_fit = scalp_prob_fit.fit
    seen = []

    def spy(train, *args, **kwargs):
        seen.append({r.day for r in train})
        return real_fit(train, *args, **kwargs)

    monkeypatch.setattr(scalp_prob_fit, "fit", spy)
    out = scalp_prob_fit.walk_forward(rows, "long_with", "morning", priors(), prior_sd())
    assert out["weeks"] == len(scored) == len(seen) > 0
    for week, train_days in zip(scored, seen):
        first = min(r.day for r in rows if _week(r.day) == week)
        assert max(train_days) < first
        assert all(_week(d) < week for d in train_days)


def test_walk_forward_needs_min_train_weeks():
    rows = planted(trading_days(24), 2.0, seed=1)          # five weeks
    assert len({_week(r.day) for r in rows}) == 5
    out = scalp_prob_fit.walk_forward(rows, "long_with", "morning", priors(), prior_sd())
    assert out["weeks"] == 0
    assert out["beats_usual"] is False
    assert out["brier"] is None and out["brier_usual"] is None


def test_walk_forward_beats_the_usual_rate_on_a_real_signal():
    rows = planted(trading_days(100), 3.0, seed=6)
    out = scalp_prob_fit.walk_forward(rows, "long_with", "morning", priors(), prior_sd())
    weeks = len({_week(r.day) for r in rows})
    assert out["weeks"] == weeks - scalp_config.prob_min_train_weeks() > 0
    assert out["brier"] < out["brier_usual"]
    assert out["beats_usual"] is True
    assert len(out["calibration"]) == 10
    assert sum(c[4] for c in out["calibration"]) > 0
    for lo, hi, mean_p, rate, n in out["calibration"]:
        if n:
            assert lo <= mean_p <= hi and 0.0 <= rate <= 1.0


def test_walk_forward_usual_rate_is_the_earlier_days_share(monkeypatch):
    # A factor with no data: the fit's chance is its intercept alone. The usual
    # rate is the share of wins on EARLIER days; the first day has none and is
    # left out of both sums.
    days = trading_days(50)
    rows = [row(d, {}, 1 if i % 2 else 0) for i, d in enumerate(days)]
    n_weeks = len({_week(r.day) for r in rows})
    monkeypatch.setattr(scalp_config, "prob_min_train_weeks", lambda: n_weeks - 1)
    out = scalp_prob_fit.walk_forward(rows, "long_with", "morning", priors(), prior_sd())
    assert out["weeks"] == 1
    last = [r for r in rows if _week(r.day) == _week(rows[-1].day)]
    want = 0.0
    for r in last:
        earlier = [x.win for x in rows if x.day < r.day]
        usual = sum(earlier) / len(earlier)
        want += (usual - r.win) ** 2
    assert out["brier_usual"] == pytest.approx(want / len(last))


def test_walk_forward_usual_rate_never_sees_its_own_day(monkeypatch):
    # Two slots of one block a day: the first always wins, the second always
    # loses. Over earlier days the share is exactly one half, so the usual
    # rate's Brier is 0.25; a rate that let the second slot see the first (same
    # day) would read above a half for it.
    days = trading_days(50)
    n_weeks = len({_week(d.isoformat()) for d in days})
    monkeypatch.setattr(scalp_config, "prob_min_train_weeks", lambda: n_weeks - 1)
    rows = []
    for d in days:
        rows.append(row(d, {}, 1, kind="intraday", slot="08:51", weight=0.5))
        rows.append(row(d, {}, 0, kind="intraday", slot="09:06", weight=0.5))
    out = scalp_prob_fit.walk_forward(rows, "long_with", "intraday", priors(), prior_sd())
    assert out["weeks"] == 1
    assert out["brier_usual"] == pytest.approx(0.25)


def test_walk_forward_usual_rate_is_per_block_for_intraday(monkeypatch):
    # Morning slots always win, afternoon slots always lose: the usual rate of a
    # block knows it, so the usual rate's Brier is 0 once each block has history.
    days = trading_days(50)
    n_weeks = len({_week(d.isoformat()) for d in days})
    monkeypatch.setattr(scalp_config, "prob_min_train_weeks", lambda: n_weeks - 1)
    rows = []
    for d in days:
        for slot in ("08:51", "13:51"):
            win = 1 if scalp_config.intraday_block(slot) == "morning" else 0
            rows.append(row(d, {}, win, kind="intraday", slot=slot, weight=0.5))
    out = scalp_prob_fit.walk_forward(rows, "long_with", "intraday", priors(), prior_sd())
    assert out["weeks"] == 1
    assert out["brier_usual"] == pytest.approx(0.0)


# ── the store: training rows and the refit ───────────────────────────────────
def ts(day, h, m=0):
    return int(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=CT).timestamp())


def seed_day(db, day, seed=0, spx_last=(14, 55)):
    """Bars 08:30 .. 14:55 for the three symbols and a flat $VIX of 20; a seeded
    walk with an upward drift so every morning has a direction."""
    rng = random.Random(seed)
    rets = [0.002] + [rng.gauss(0.0003, 0.0015) for _ in range(77)]
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
            rows.append((sym, 5, start, o, h, lo, c, 0.0 if sym == "$SPX" else 1000.0))
    rows += [("$VIX", 5, ts(day, 8, 30) + 300 * i, 20.0, 20.0, 20.0, 20.0, 0.0)
             for i in range(78)]
    scalp_store.upsert_bars(rows, db_path=db)


@pytest.fixture(autouse=True)
def _no_big_ten(monkeypatch):
    monkeypatch.setattr(_symbols, "big10", lambda: ())


@pytest.fixture
def db(tmp_path):
    return tmp_path / "scalp.db"


def _studied(db, days, short=()):
    for i, d in enumerate(days):
        seed_day(db, d, seed=i, spx_last=(11, 55) if d in short else (14, 55))
    scalp_study.rebuild(db_path=db)
    scalp_intraday_study.rebuild(db_path=db)


def _copy_outcomes(db, src_day, dst_day):
    """Stale outcome rows for ``dst_day``: ``src_day``'s, re-dated (as a table
    built before a config change would hold)."""
    conn = sqlite3.connect(db)
    try:
        with conn:
            for table in ("outcomes", "intraday_outcomes"):
                cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
                got = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE day = ?",
                                   (src_day.isoformat(),)).fetchall()
                at = cols.index("day")
                conn.executemany(
                    f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
                    f"VALUES ({', '.join('?' * len(cols))})",
                    [tuple(dst_day.isoformat() if i == at else v for i, v in enumerate(r))
                     for r in got])
    finally:
        conn.close()


def test_training_rows_skip_what_the_study_skips(db):
    days = trading_days(3, start=dt.date(2026, 9, 1))
    _studied(db, days, short={days[1]})
    _copy_outcomes(db, days[0], days[1])            # a stale table's rows
    for kind in ("morning", "intraday"):
        rows = scalp_prob_fit.training_rows(kind, db_path=db)
        got = {r.day for r in rows}
        assert days[1].isoformat() not in got, kind
        assert {days[0].isoformat(), days[2].isoformat()} <= got, kind


def test_training_rows_skip_slots_an_early_close_cuts(db, monkeypatch):
    days = trading_days(2, start=dt.date(2026, 9, 1))
    _studied(db, days)
    monkeypatch.setattr(scalp_config, "early_close_days", lambda: {days[0]})
    monkeypatch.setattr(scalp_config, "early_close_time", lambda: "12:00")
    rows = scalp_prob_fit.training_rows("intraday", db_path=db)
    half = {r.slot for r in rows if r.day == days[0].isoformat()}
    full = {r.slot for r in rows if r.day == days[1].isoformat()}
    assert half and max(half) <= "10:51"
    assert max(full) > "10:51"


def test_training_rows_are_the_labelled_outcomes_at_the_current_scale(db):
    days = trading_days(2, start=dt.date(2026, 9, 1))
    _studied(db, days)
    scale = scalp_study._scale_key(scalp_config.iv_scale())
    conn = sqlite3.connect(db)
    try:
        morning = conn.execute(
            "SELECT day, symbol, role, result FROM outcomes WHERE interval = 5 "
            "AND iv_scale = ? AND result != 'skip'", (scale,)).fetchall()
        intraday = conn.execute(
            "SELECT day, slot, symbol, role, result FROM intraday_outcomes "
            "WHERE iv_scale = ? AND result != 'skip'", (scale,)).fetchall()
    finally:
        conn.close()
    assert morning and intraday
    m_rows = scalp_prob_fit.training_rows("morning", db_path=db)
    assert sorted((r.day, r.symbol, r.role, r.win) for r in m_rows) == sorted(
        (d, s, ro, 1 if res == "win" else 0) for d, s, ro, res in morning)
    assert {r.slot for r in m_rows} == {"08:36"}
    i_rows = scalp_prob_fit.training_rows("intraday", db_path=db)
    assert sorted((r.day, r.slot, r.symbol, r.role, r.win) for r in i_rows) == sorted(
        (d, sl, s, ro, 1 if res == "win" else 0) for d, sl, s, ro, res in intraday)
    # Each symbol-day weighs one, per trade.
    total = defaultdict(float)
    for r in i_rows + m_rows:
        total[(r.kind, r.day, r.symbol, r.role)] += r.weight
    assert all(v == pytest.approx(1.0) for v in total.values())
    # The trend factors are read, the block indicators are set, and with no
    # source recorded every source factor is a gap.
    one = next(r for r in i_rows if r.slot == "13:51")
    assert one.factors["block:afternoon"] == 1.0
    assert any(one.factors.get(k) is not None for k in ("slope_5", "rsi_5"))
    assert all(one.factors[k] is None for k in
               ("room", "breadth_room", "flip_side", "netprem", "flow", "sentiment"))


def test_training_rows_read_the_recorded_snapshot_first(db):
    days = trading_days(2, start=dt.date(2026, 9, 1))
    _studied(db, days)
    d0, d1 = days[0].isoformat(), days[1].isoformat()

    def snap(call, put):
        return {"matrix": {}, "netprem": {"SPY": {"call": call, "put": put, "ts": 0}},
                "sentiment": None, "flow": None}

    # Day 0: a recorded reading AND a loaded source row: the reading wins.
    scalp_store.save_reading(d0, "08:36", "SPY", {"snapshot": snap(3.0, 1.0)}, db_path=db)
    # Day 1: only the loaded source row.
    scalp_store.save_prob_sources([("morning", d0, "08:36", "SPY", snap(1.0, 3.0)),
                                   ("morning", d1, "08:36", "SPY", snap(1.0, 3.0))],
                                  db_path=db)
    rows = scalp_prob_fit.training_rows("morning", db_path=db)
    by_day = {}
    for r in rows:
        if r.symbol == "SPY":
            by_day[r.day] = r.factors["netprem"]
    m0 = scalp_prob_fit.scalp_inputs.morning("SPY", days[0], 5, db_path=db)
    m1 = scalp_prob_fit.scalp_inputs.morning("SPY", days[1], 5, db_path=db)
    assert by_day[d0] == pytest.approx(0.5 * m0.direction)
    assert by_day[d1] == pytest.approx(-0.5 * m1.direction)
    assert all(r.factors["netprem"] is None for r in rows if r.symbol == "QQQ")


def test_training_rows_win_points_feed_the_room(db):
    # A wall far beyond the target in the call's direction: room / win points is
    # positive and capped, which needs the row's win points.
    days = trading_days(1, start=dt.date(2026, 9, 1))
    _studied(db, days)
    m = scalp_prob_fit.scalp_inputs.morning("SPY", days[0], 5, db_path=db)
    wall = m.entry_spot * (1 + 0.05 * m.direction)
    view = {"spot": m.entry_spot, "call_wall": wall if m.direction > 0 else None,
            "put_wall": wall if m.direction < 0 else None, "flip": None,
            "net_gex": 1.0e9, "walls_untrusted": False}
    scalp_store.save_reading(days[0].isoformat(), "08:36", "SPY",
                             {"snapshot": {"matrix": {"SPY": view}, "netprem": {},
                                           "sentiment": None, "flow": None}},
                             db_path=db)
    rows = [r for r in scalp_prob_fit.training_rows("morning", db_path=db)
            if r.symbol == "SPY"]
    assert rows and all(r.factors["room"] == pytest.approx(
        scalp_config.prob_room_cap()) for r in rows)


def test_refit_stores_eight_models_under_one_version_and_keeps_old(db):
    days = trading_days(3, start=dt.date(2026, 9, 1))
    _studied(db, days)
    now = dt.datetime(2026, 10, 24, 8, 0)
    first = scalp_prob_fit.refit(db_path=db, now=now)
    second = scalp_prob_fit.refit(db_path=db, now=now + dt.timedelta(days=7))
    assert (first["version"], second["version"]) == (1, 2)
    assert first["rows"]["morning"] > 0 and first["rows"]["intraday"] > 0
    assert first["seconds"] >= 0
    conn = sqlite3.connect(db)
    try:
        stored = conn.execute(
            "SELECT version, kind, role, fitted_at, json FROM prob_models").fetchall()
    finally:
        conn.close()
    keys = {(v, k, r) for v, k, r, _f, _j in stored}
    roles = ("long_with", "long_against", "credit_with", "credit_against")
    assert keys == {(v, k, r) for v in (1, 2) for k in ("morning", "intraday")
                    for r in roles}
    assert {f for v, _k, _r, f, _j in stored if v == 1} == {"2026-10-24T08:00:00-05:00"}
    for _v, kind, role, _f, body in stored:
        body = json.loads(body)
        assert set(body) == {"weights", "intercepts", "scaling", "days", "n_days",
                             "rows", "priors", "prior_sd", "walk_forward"}
        assert 1 <= body["n_days"] <= len(days) and body["rows"] > 0
        sign = -1 if role.endswith("against") else 1
        assert body["priors"]["room"] == pytest.approx(sign * 0.15)
        assert body["walk_forward"]["weeks"] == 0
        assert body["walk_forward"]["beats_usual"] is False
        assert set(body["intercepts"]) == set(PRICES)
    latest = scalp_store.latest_prob_models(db_path=db)
    assert latest["version"] == 2 and len(latest["models"]) == 8


def test_refit_with_no_outcomes_stores_empty_models(db):
    out = scalp_prob_fit.refit(db_path=db, now=dt.datetime(2026, 10, 24, 8, 0))
    assert out["version"] == 1
    assert out["rows"] == {"morning": 0, "intraday": 0}
    latest = scalp_store.latest_prob_models(db_path=db)
    assert len(latest["models"]) == 8
    for body in latest["models"].values():
        assert body["weights"] == {} and body["n_days"] == 0
        assert body["walk_forward"]["brier"] is None


def test_refit_failure_stores_nothing_and_counts_a_degrade(db, monkeypatch):
    def boom(kind, db_path=None):
        raise RuntimeError("broken store")

    monkeypatch.setattr(scalp_prob_fit, "training_rows", boom)
    called = []
    monkeypatch.setattr(_degrade, "degraded",
                        lambda area, **kw: called.append(area))
    out = scalp_prob_fit.refit(db_path=db, now=dt.datetime(2026, 10, 24, 8, 0))
    assert out["version"] is None
    assert called == ["options.scalp_prob"]
    assert scalp_store.latest_prob_models(db_path=db) == {}
