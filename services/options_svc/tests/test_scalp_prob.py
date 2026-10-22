"""The live chance of target (scalp_prob).

Models are handed in as the stored bodies ``scalp_store.latest_prob_models`` returns,
with weights chosen so the expected probability is known before the code runs. The
store is an empty ``tmp_path`` one, so the trend readings are gaps (as for a symbol
with no bars), which the chance must treat as no push.
"""
import datetime as dt
import math

import pytest

from services import _degrade
from services.options_svc import scalp_prob, scalp_sim, scalp_store

DAY = dt.date(2026, 10, 20)
ENTRY_TS = 1_792_500_000


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z))


def body(weights, intercepts=None, days=None, beats=True, share=None):
    return {"weights": dict(weights),
            "intercepts": {"SPY": 0.0} if intercepts is None else dict(intercepts),
            "scaling": {n: 1.0 for n in weights if n != "flow"},
            "days": dict(days if days is not None else {n: 100 for n in weights}),
            "data_share": dict(share if share is not None else {n: 0.9 for n in weights}),
            "n_days": 100, "rows": 400, "priors": {}, "prior_sd": {},
            "walk_forward": {"beats_usual": beats}}


def models(kind="morning", long_with=None, long_against=None, version=7):
    long_with = long_with or body({"price:gap_pct": 0.8})
    long_against = long_against or body({"price:gap_pct": -0.8})
    out = {}
    for role in scalp_sim.ROLES:
        out[(kind, role)] = {"long_with": long_with,
                             "long_against": long_against}.get(role, long_with)
    return {"version": version, "fitted_at": "2026-10-21T20:00:00-05:00", "models": out}


def entry(direction=1, gap=1.0, kind="morning"):
    return {"direction": direction, "inputs": {"gap_pct": gap}, "entry_ts": ENTRY_TS,
            "entry_spot": 500.0, "vol_at_entry": 0.2,
            "slot": "08:36" if kind == "morning" else "10:15",
            "roles": {"long_with": {"win_points": 2.0}}}


def call(tmp_path, kind="morning", e=None, m=None, snapshot=None, symbol="SPY"):
    return scalp_prob.chance(kind, symbol, e if e is not None else entry(kind=kind),
                             snapshot, DAY, db_path=tmp_path / "s.db",
                             models=m if m is not None else models(kind), trend_cache={})


def test_no_models_gives_none_not_a_number(tmp_path):
    out = call(tmp_path, m={})
    assert out["version"] is None and out["fitted_at"] is None
    assert out["by_role"] == {r: None for r in scalp_sim.ROLES}
    assert out["pushes"] == []


def test_no_models_in_the_store_gives_none(tmp_path):
    out = scalp_prob.chance("morning", "SPY", entry(), None, DAY,
                            db_path=tmp_path / "empty.db")
    assert out["version"] is None
    assert set(out["by_role"].values()) == {None}


def test_the_probability_is_the_models_own(tmp_path):
    out = call(tmp_path)
    assert out["version"] == 7 and out["fitted_at"].startswith("2026-10-21")
    assert out["by_role"]["long_with"] == pytest.approx(sigmoid(0.8 * 1.0))
    assert out["beats_usual"] == {r: True for r in scalp_sim.ROLES}


def test_no_source_history_still_finite(tmp_path):
    # Every source factor is a gap (snapshot None), every trend reading too.
    w = {"price:gap_pct": 0.5, "room": 1.0, "breadth_room": 1.0, "flip_side": 1.0,
         "netprem": 1.0, "flow": 1.0, "sentiment": 1.0, "slope_5": 1.0, "rsi_15": 1.0}
    out = call(tmp_path, m=models(long_with=body(w), long_against=body(w)))
    for role, value in out["by_role"].items():
        assert value is not None and 0.0 < value < 1.0, role
    assert out["by_role"]["long_with"] == pytest.approx(sigmoid(0.5))


def test_flat_tape_has_no_chance(tmp_path):
    for flat in (None, 0):
        out = call(tmp_path, e=entry(direction=flat))
        assert set(out["by_role"].values()) == {None}
        assert out["pushes"] == []
        assert out["version"] is None


def test_an_unknown_kind_has_no_chance(tmp_path):
    out = call(tmp_path, kind="evening", m=models("morning"))
    assert set(out["by_role"].values()) == {None}


def test_a_role_fitted_on_no_rows_has_no_chance(tmp_path):
    empty = body({}, intercepts={})
    m = models(long_against=empty)
    out = call(tmp_path, m=m)
    assert out["by_role"]["long_against"] is None
    assert out["by_role"]["long_with"] is not None


def test_pushes_name_assumed_factors(tmp_path):
    # "Assumed" is the weight's data share under [prob] assumed_share (0.5), not
    # its day count: room was read on 40 days and is still mostly its prior.
    w = {"price:gap_pct": 0.8, "slope_5": 0.5, "room": 0.0}
    days = {"price:gap_pct": 180, "slope_5": 3, "room": 40}
    share = {"price:gap_pct": 0.95, "slope_5": 0.02, "room": 0.12}
    e = entry(gap=1.0)
    out = call(tmp_path, e=e, m=models(long_with=body(w, days=days, share=share)))
    by = {p["factor"]: p for p in out["pushes"]}
    assert set(by) == set(w)
    assert by["slope_5"]["assumed"] is True and by["slope_5"]["days"] == 3
    assert by["room"]["assumed"] is True and by["room"]["days"] == 40
    assert by["price:gap_pct"]["assumed"] is False and by["price:gap_pct"]["days"] == 180
    assert by["slope_5"]["reading"] is None          # a gap reads as None
    assert by["slope_5"]["points"] == 0.0
    assert by["price:gap_pct"]["reading"] == pytest.approx(1.0)
    p = sigmoid(0.8)
    assert by["price:gap_pct"]["points"] == pytest.approx(p * (1 - p) * 0.8 * 100)
    magnitudes = [abs(x["points"]) for x in out["pushes"]]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_a_factor_the_model_never_read_is_assumed_with_zero_days(tmp_path):
    out = call(tmp_path, m=models(long_with=body({"price:gap_pct": 0.8}, days={},
                                                 share={"price:gap_pct": 0.0})))
    assert out["pushes"][0]["days"] == 0 and out["pushes"][0]["assumed"] is True


def test_the_share_line_is_the_config_and_half_is_learned(tmp_path, monkeypatch):
    w = {"price:gap_pct": 0.8, "room": 0.1}
    share = {"price:gap_pct": 0.5, "room": 0.7}
    m = models(long_with=body(w, share=share))
    by = {p["factor"]: p["assumed"] for p in call(tmp_path, m=m)["pushes"]}
    assert by == {"price:gap_pct": False, "room": False}     # at the line is learned
    monkeypatch.setattr(scalp_prob.scalp_config, "prob_assumed_share", lambda: 0.8)
    by = {p["factor"]: p["assumed"] for p in call(tmp_path, m=m)["pushes"]}
    assert by == {"price:gap_pct": True, "room": True}


def test_a_model_stored_without_data_share_reads_every_factor_assumed(tmp_path):
    # A body fitted before the share was kept (or a junk share) is never shown as
    # learned, however many days it was read on.
    w = {"price:gap_pct": 0.8, "slope_5": 0.5}
    old = body(w, days={"price:gap_pct": 180, "slope_5": 180})
    old.pop("data_share")
    out = call(tmp_path, m=models(long_with=old))
    assert [p["assumed"] for p in out["pushes"]] == [True, True]
    assert out["by_role"]["long_with"] == pytest.approx(sigmoid(0.8))
    junk = body(w, share={"price:gap_pct": float("nan"), "slope_5": "x"})
    out = call(tmp_path, m=models(long_with=junk))
    assert [p["assumed"] for p in out["pushes"]] == [True, True]


def test_against_and_with_move_opposite_ways(tmp_path):
    out = call(tmp_path, e=entry(gap=2.0))
    assert out["by_role"]["long_with"] > 0.5 > out["by_role"]["long_against"]
    assert out["by_role"]["long_against"] == pytest.approx(sigmoid(-0.8 * 2.0))


def test_a_down_call_signs_the_factor_by_its_direction(tmp_path):
    up = call(tmp_path, e=entry(direction=1, gap=1.0))["by_role"]["long_with"]
    down = call(tmp_path, e=entry(direction=-1, gap=1.0))["by_role"]["long_with"]
    assert up > 0.5 > down


def test_an_intraday_call_reads_the_intraday_models(tmp_path):
    out = call(tmp_path, kind="intraday", e=entry(kind="intraday"))
    assert out["by_role"]["long_with"] == pytest.approx(sigmoid(0.8))
    # the morning models are not what an intraday call reads
    none = call(tmp_path, kind="intraday", e=entry(kind="intraday"), m=models("morning"))
    assert set(none["by_role"].values()) == {None}


def test_never_raises_and_counts_a_degrade(tmp_path):
    before = _degrade.counts().get("options.scalp_prob", 0)
    out = scalp_prob.chance("morning", "SPY", {"direction": 1, "inputs": 5,
                                               "entry_ts": "x", "roles": 3},
                            None, DAY, db_path=tmp_path / "s.db",
                            models={"version": "bad", "models": 4})
    assert set(out["by_role"].values()) == {None}
    assert out["version"] is None
    assert _degrade.counts().get("options.scalp_prob", 0) == before + 1


def test_reads_the_store_when_no_models_are_handed_in(tmp_path):
    db = tmp_path / "s.db"
    stored = models()
    scalp_store.save_prob_models(3, stored["fitted_at"], stored["models"], db_path=db)
    out = scalp_prob.chance("morning", "SPY", entry(), None, DAY, db_path=db,
                            trend_cache={})
    assert out["version"] == 3
    assert out["by_role"]["long_with"] == pytest.approx(sigmoid(0.8))


def test_the_room_factor_is_priced_as_the_fit_prices_it(monkeypatch):
    # The factors' win points are the fit's own pricing (scalp_prob_fit._win_points),
    # never the role's chain-priced points: the room means the same live as in training.
    seen = {}

    def fake_win_points(*args):
        seen["fit"] = args
        return 3.5

    real = scalp_prob.scalp_prob_features.factors

    def spy(kind, symbol, slot, d, inputs, trend, snapshot, win_points, spot):
        seen["factors"] = win_points
        return real(kind, symbol, slot, d, inputs, trend, snapshot, win_points, spot)

    monkeypatch.setattr(scalp_prob.scalp_prob_fit, "_win_points", fake_win_points)
    monkeypatch.setattr(scalp_prob.scalp_prob_features, "factors", spy)
    e = entry()
    e["roles"]["long_with"]["win_points"] = 2.0     # the chain's figure: not used
    scalp_prob.chance("morning", "SPY", e, None, DAY, models=models(), trend_cache={})
    assert seen["fit"] == ("SPY", DAY, 1, 500.0, ENTRY_TS, 0.2)
    assert seen["factors"] == 3.5


def test_an_entry_with_no_roles_still_gets_its_chance():
    e = entry()
    e.pop("roles")
    out = scalp_prob.chance("morning", "SPY", e, None, DAY, models=models(),
                            trend_cache={})
    assert out["by_role"]["long_with"] == pytest.approx(sigmoid(0.8))
