"""The range forecast (``range_forecast``): rows, point-in-time inputs and the
out-of-sample scoring. Synthetic 5-minute bars go into a ``tmp_path`` store.
Design: docs/plans/2026-11-14-range-forecast-design.md.
"""
import datetime as dt
import math

import numpy as np
import pytest

from services.options_svc import range_forecast as rf
from services.options_svc import scalp_store
from shared import market_calendar as mc
from shared import model_health_config


def trading_days(n, start=dt.date(2026, 3, 2)):
    out, d = [], start
    while len(out) < n:
        if mc.is_trading_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def day_bars(symbol, day, closes, volume=1000.0, spread=0.001):
    """One complete regular session of 5-minute bars from ``closes``."""
    start = int(mc.regular_open_on(day).timestamp())
    rows, prev = [], closes[0]
    for i, c in enumerate(closes):
        hi, lo = max(prev, c) * (1 + spread), min(prev, c) * (1 - spread)
        rows.append((symbol, 5, start + i * 300, prev, hi, lo, c, volume))
        prev = c
    return rows


def n_bars(day):
    return int((mc.regular_close_on(day) - mc.regular_open_on(day)).total_seconds() // 300)


def seed(db, days, *, symbol="SPY", vix="$VIX", sigma=None, vix_level=None, seed_=0,
         skip=None):
    """Random-walk sessions whose bar volatility is ``sigma(i)`` and whose
    volatility index reads ``vix_level(i)`` all day."""
    rng = np.random.default_rng(seed_)
    sigma = sigma or (lambda i: 0.001)
    vix_level = vix_level or (lambda i: 20.0)
    rows = []
    price = 100.0
    for i, day in enumerate(days):
        n = n_bars(day)
        closes = list(price * np.exp(np.cumsum(rng.normal(0, sigma(i), n))))
        price = closes[-1]
        bars = day_bars(symbol, day, closes)
        if skip and day in skip:
            bars = bars[:10] + bars[11:]
        rows += bars
        rows += day_bars(vix, day, [vix_level(i)] * n, volume=0.0, spread=0.0)
    scalp_store.upsert_bars(rows, db_path=db)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "scalp.db"
    scalp_store.connect(path).close()
    model_health_config.reset_cache()
    return path


def build_rows(db, horizons=(30, 60, 0), **kw):
    s, v = rf.sessions("SPY", db), rf.sessions("$VIX", db)
    return rf.rows(s, v, s, list(horizons), first=525, last=870, step=15,
                   floor=1e-8, volume_sessions=20, **kw)


def test_incomplete_and_non_trading_sessions_are_left_out(db):
    days = trading_days(8)
    seed(db, days, skip={days[3]})
    got = rf.sessions("SPY", db)
    assert days[3].isoformat() not in got and len(got) == 7
    assert all(len(s.bars) == n_bars(dt.date.fromisoformat(d)) for d, s in got.items())


def test_the_first_five_sessions_give_no_rows(db):
    days = trading_days(7)
    seed(db, days)
    assert {r.day for r in build_rows(db)} == {days[5].isoformat(), days[6].isoformat()}


def test_target_is_the_range_still_to_come(db):
    days = trading_days(6)
    seed(db, days)
    r = next(r for r in build_rows(db, horizons=(30,)) if r.day == days[5].isoformat()
             and r.t_min == 600)                       # 10:00 CT
    s = rf.sessions("SPY", db)[days[5].isoformat()]
    k = (600 - 510) // 5
    ahead = s.bars[k:k + 6]
    want = (max(b.high for b in ahead) - min(b.low for b in ahead)) / s.bars[k - 1].close
    assert r.y == pytest.approx(math.log(want))


def test_no_input_reads_a_bar_after_the_forecast_time(db, tmp_path):
    days = trading_days(7)
    seed(db, days, seed_=3)
    before = {(r.t_min, r.horizon): r.inputs for r in build_rows(db)
              if r.day == days[6].isoformat()}
    # Rewrite every bar of the last day from 11:00 on; inputs before 11:00 must not move.
    last = days[6]
    cut = mc.regular_open_on(last).timestamp() + (660 - 510) * 60
    later = [b for b in scalp_store.bars("SPY", 5, cut, 2 ** 40, db_path=db)]
    scalp_store.upsert_bars([("SPY", 5, b.ts, b.open * 3, b.high * 5, b.low, b.close * 3,
                              b.volume * 9) for b in later], db_path=db)
    after = {(r.t_min, r.horizon): r.inputs for r in build_rows(db)
             if r.day == days[6].isoformat()}
    for key, inputs in before.items():
        if key[0] <= 660:
            assert after[key] == inputs, key


def test_a_horizon_past_the_close_is_left_out(db):
    days = trading_days(6)
    seed(db, days)
    late = [r for r in build_rows(db) if r.t_min == 870]       # 14:30 CT
    assert {r.horizon for r in late} == {30, 0}


def test_a_flat_stretch_is_floored_not_minus_infinity(db):
    days = trading_days(6)
    seed(db, days, sigma=lambda i: 0.0)
    out = build_rows(db, horizons=(30,))
    assert out and all(math.isfinite(v) for r in out for v in r.inputs.values())
    assert all(math.isfinite(r.y) for r in out)


def test_volume_comes_from_the_volume_symbol(db):
    days = trading_days(6)
    seed(db, days)
    s, v = rf.sessions("SPY", db), rf.sessions("$VIX", db)
    # The index carries no volume; read SPY's and the reading is its usual level.
    out = rf.rows(v, v, s, [30], first=600, last=600, step=15, floor=1e-8, volume_sessions=20)
    assert out and out[-1].inputs["volume"] == pytest.approx(0.0)


def test_a_day_is_never_fitted_on_itself(db):
    days = trading_days(14)
    seed(db, days, seed_=5)
    base = build_rows(db, horizons=(30,))
    target = days[-1].isoformat()
    planted = [rf.Row(r.day, r.t_min, r.horizon, r.y + (50.0 if r.day == target else 0.0),
                      r.inputs) for r in base]
    a = rf.walk_forward(base, min_train_days=5, lam=1.0)
    b = rf.walk_forward(planted, min_train_days=5, lam=1.0)
    idx = [i for i, r in enumerate(a["rows"]) if r.day == target]
    assert idx and all(a["forecast"][i] == b["forecast"][i] for i in idx)


def test_the_forecast_beats_the_yardstick_when_volatility_follows_the_index(db):
    days = trading_days(70)
    rng = np.random.default_rng(9)
    level = list(rng.uniform(10, 40, size=len(days)))
    seed(db, days, vix_level=lambda i: level[i], sigma=lambda i: level[i] / 20000, seed_=11)
    out = rf.score(build_rows(db, horizons=(60,)), min_train_days=20, lam=1.0, n_boot=200)
    assert out["days"] == 70 - 5 - 20
    assert out["diff_yardstick"]["hi"] < 0
    assert 0.6 < out["coverage"] < 0.95


def test_too_little_history_scores_nothing(db):
    days = trading_days(10)
    seed(db, days)
    out = rf.score(build_rows(db, horizons=(30,)), min_train_days=40, lam=1.0, n_boot=50)
    assert out["days"] == 0 and out["diff_yardstick"] is None and out["loss"] is None


def test_evaluate_lists_each_symbol_and_horizon(db, monkeypatch):
    days = trading_days(12)
    seed(db, days)
    monkeypatch.setattr(model_health_config, "range_symbols", lambda: {"SPY": "$VIX"})
    monkeypatch.setattr(model_health_config, "range_min_train_days", lambda: 3)
    monkeypatch.setattr(model_health_config, "bootstrap_draws", lambda: 100)
    out = rf.evaluate(db)
    assert [(e["symbol"], e["horizon"]) for e in out] == [("SPY", 30), ("SPY", 60), ("SPY", 0)]
    assert out[0]["inputs"] and set(out[0]["inputs"][0][1]) == set(rf.DRIFT_INPUTS)
