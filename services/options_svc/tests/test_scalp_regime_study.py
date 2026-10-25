"""The Opening Scalp's net-gamma regime study: the gap rule, the cells and the
usable sessions (design 2026-10-25-scalp-gamma-regime-study, section 3.1)."""
import random
from datetime import date, timedelta

import pytest

from services.options_svc import scalp_regime_study as rs
from shared import scalp_config


def snap(symbol="$SPX", net_gex=-1e9, state="ok", age=1.0, untrusted=False):
    return {"matrix": {symbol: {"spot": 100.0, "flip": 99.0, "call_wall": 105.0,
                                "put_wall": 95.0, "net_gex": net_gex,
                                "walls_untrusted": untrusted}},
            "sources": {"matrix": {"state": state, "age_min": age}}}


def out(day, slot, symbol="$SPX", role="long_with", result="win", scale=1.0):
    return {"day": day, "slot": slot, "symbol": symbol, "iv_scale": scale,
            "role": role, "block": scalp_config.intraday_block(slot), "result": result}


def test_regime_of_signs():
    assert rs.regime_of(snap(net_gex=-5e8), "$SPX") == rs.NEGATIVE
    assert rs.regime_of(snap(net_gex=5e8), "$SPX") == rs.POSITIVE


def test_regime_of_gaps():
    assert rs.regime_of(snap(net_gex=0.0), "$SPX") is None
    assert rs.regime_of(snap(net_gex=None), "$SPX") is None
    assert rs.regime_of(snap(net_gex=float("nan")), "$SPX") is None
    assert rs.regime_of(snap(untrusted=True), "$SPX") is None
    assert rs.regime_of(snap(state="stale"), "$SPX") is None
    assert rs.regime_of(snap(state="absent"), "$SPX") is None
    assert rs.regime_of(snap(age=6.0), "$SPX") is None          # > max_age_min 5
    assert rs.regime_of(None, "$SPX") is None
    assert rs.regime_of(snap(symbol="SPY"), "$SPX") is None     # no row for it


def test_regime_of_reads_a_backfilled_snapshot():
    assert rs.regime_of(snap(state="backfill", age=None), "$SPX") == rs.NEGATIVE


def test_regime_of_matches_symbol_without_dollar():
    assert rs.regime_of(snap(symbol="SPX"), "$SPX") == rs.NEGATIVE


def test_regimes_maps_each_reading_by_its_symbol():
    readings = {("2026-10-21", "12:36", "$SPX"): {"snapshot": snap(net_gex=-5e8)},
                ("2026-10-21", "13:06", "$SPX"): {"snapshot": snap(net_gex=5e8)},
                ("2026-10-21", "12:36", "SPY"): {"snapshot": snap(net_gex=5e8)},
                ("2026-10-21", "13:36", "$SPX"): {}}
    assert rs.regimes(readings) == {("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE,
                                    ("2026-10-21", "13:06", "$SPX"): rs.POSITIVE,
                                    ("2026-10-21", "12:36", "SPY"): None,
                                    ("2026-10-21", "13:36", "$SPX"): None}


def test_cells_count_labelled_long_rows_of_the_block():
    rows = [out("2026-10-21", "12:36", result="win"),
            out("2026-10-21", "12:36", role="long_against", result="timeout"),
            out("2026-10-21", "12:36", role="credit_with", result="win"),
            out("2026-10-21", "12:36", result="skip"),
            out("2026-10-21", "12:36", result="win", scale=1.25),
            out("2026-10-21", "10:06", result="win")]
    got = rs.cells(rows, 1.0, "afternoon", rs.LONG_ROLES)
    assert got == {("2026-10-21", "$SPX", "12:36"): [1, 2, 1]}


def test_usable_sessions_start_on_the_declared_day():
    regime = {("2026-10-20", "12:36", "$SPX"): rs.NEGATIVE,
              ("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE}
    rows = [out("2026-10-20", "12:36"), out("2026-10-21", "12:36")]
    assert rs.usable_sessions(regime, rows) == ["2026-10-21"]


def test_usable_sessions_skip_early_close_and_gaps():
    regime = {("2026-11-27", "12:36", "$SPX"): rs.NEGATIVE,
              ("2026-10-22", "12:36", "$SPX"): None}
    rows = [out("2026-11-27", "12:36"), out("2026-10-22", "12:36")]
    assert rs.usable_sessions(regime, rows) == []


def test_usable_sessions_ignore_the_0836_record():
    regime = {("2026-10-21", "08:36", "$SPX"): rs.NEGATIVE}
    assert rs.usable_sessions(regime, [out("2026-10-21", "08:36")]) == []


def test_usable_session_needs_regime_and_outcome_on_the_same_call():
    regime = {("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE}
    apart = [out("2026-10-21", "13:06", "$SPX")]
    assert rs.usable_sessions(regime, apart) == []
    other_symbol = [out("2026-10-21", "12:36", "SPY")]
    assert rs.usable_sessions(regime, other_symbol) == []
    together = apart + [out("2026-10-21", "12:36", "$SPX")]
    assert rs.usable_sessions(regime, together) == ["2026-10-21"]


def test_day_without_afternoon_outcomes_is_not_usable():
    regime = {("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE}
    assert rs.usable_sessions(regime, [out("2026-10-21", "10:06")]) == []


# ── the effect, the permutation and the declared verdict ─────────────────────

_WORLD_SYMBOLS = ("$SPX", "SPY", "QQQ")
_WORLD_SLOTS = ("12:36", "13:06")
_AGREE = {"share": 1.0, "sessions": 5}


def world(n_days, neg_win, pos_win):
    """``(regime_map, outcomes)`` for ``n_days`` sessions from 2026-10-21: three
    symbols, two afternoon slots, regimes alternating by day and symbol, and a
    long outcome for both long roles at every multiplier."""
    rng = random.Random(1)
    regime, rows = {}, []
    day, i = date(2026, 10, 21), 0
    while i < n_days:
        if day.weekday() < 5 and day not in (date(2026, 11, 26), date(2026, 11, 27)):
            for j, sym in enumerate(_WORLD_SYMBOLS):
                reg = rs.NEGATIVE if (i + j) % 2 == 0 else rs.POSITIVE
                win = neg_win if reg == rs.NEGATIVE else pos_win
                for slot in _WORLD_SLOTS:
                    regime[(day.isoformat(), slot, sym)] = reg
                    for scale in scalp_config.iv_scales():
                        for role in rs.LONG_ROLES:
                            result = "win" if rng.random() < win else "timeout"
                            rows.append(out(day.isoformat(), slot, sym, role, result, scale))
            i += 1
        day += timedelta(days=1)
    return regime, rows


@pytest.fixture(autouse=True)
def _few_permutations(monkeypatch):
    monkeypatch.setattr(scalp_config, "regime_permutations", lambda: 300)


def test_effect_is_negative_share_minus_positive_share():
    regime = {("d1", "12:36", "$SPX"): rs.NEGATIVE, ("d1", "13:06", "$SPX"): rs.POSITIVE,
              ("d2", "12:36", "$SPX"): rs.NEGATIVE}
    cell_map = {("d1", "$SPX", "12:36"): [2, 2, 0], ("d1", "$SPX", "13:06"): [0, 2, 2],
                ("d2", "$SPX", "12:36"): [1, 2, 1]}
    assert rs.effect(cell_map, regime) == pytest.approx(75.0)   # mean(1.0, 0.5) - 0.0


def test_effect_undefined_when_one_side_is_empty():
    regime = {("d1", "12:36", "$SPX"): rs.NEGATIVE}
    assert rs.effect({("d1", "$SPX", "12:36"): [1, 2, 0]}, regime) is None


def test_permutation_is_deterministic_and_bounded():
    regime, rows = world(12, 0.7, 0.3)
    sessions = sorted({d for d, _, _ in regime})
    cm = rs.cells(rows, 1.0, "afternoon", rs.LONG_ROLES)
    obs = rs.effect(cm, regime)
    a = rs.permutation_p(cm, regime, sessions, obs, 200, 5)
    assert a == rs.permutation_p(cm, regime, sessions, obs, 200, 5)
    assert 0.0 <= a <= 1.0


def test_collecting_shows_nothing_but_the_count():
    regime, rows = world(39, 0.9, 0.1)
    got = rs.evaluate(regime, rows, _AGREE)
    assert got == {"status": "collecting", "sessions": 39, "needed": 40,
                   "first": "2026-10-21", "start_day": "2026-10-21"}


def test_a_strong_afternoon_effect_passes_and_midday_is_tested_next():
    regime, rows = world(40, 0.8, 0.2)
    got = rs.evaluate(regime, rows, _AGREE)
    first, second = got["blocks"][0], got["blocks"][1]
    assert first["block"] == "afternoon" and first["passed"] and first["reasons"] == []
    assert second["block"] == "midday" and second["tested"]
    assert second["reasons"] == ["effect undefined"]       # the world has no midday rows
    assert got["blocks"][2]["reasons"] == ["not tested: midday did not pass"]


def test_no_effect_fails_and_stops_the_order():
    regime, rows = world(40, 0.0, 0.0)          # every long times out: effect 0, p 1.0
    blocks = rs.evaluate(regime, rows, _AGREE)["blocks"]
    assert blocks[0]["reasons"] == ["p above alpha", "effect below the floor",
                                   "not above 0 at every multiplier"]
    assert blocks[1]["reasons"] == ["not tested: afternoon did not pass"]


def test_small_effect_fails_the_floor(monkeypatch):
    monkeypatch.setattr(scalp_config, "regime_min_effect_pp", lambda: 99.0)
    regime, rows = world(40, 0.8, 0.2)
    assert "effect below the floor" in rs.evaluate(regime, rows, _AGREE)["blocks"][0]["reasons"]


def test_precondition_missing_or_failed_blocks_a_pass():
    regime, rows = world(40, 0.8, 0.2)
    assert "precondition not checked" in rs.evaluate(regime, rows, None)["blocks"][0]["reasons"]
    low = rs.evaluate(regime, rows, {"share": 0.9, "sessions": 5})
    assert "precondition failed" in low["blocks"][0]["reasons"]


def test_a_multiplier_at_or_below_zero_fails():
    regime, rows = world(40, 0.8, 0.2)
    rows = [dict(r, result="timeout") if r["iv_scale"] == 1.5 else r for r in rows]
    assert "not above 0 at every multiplier" in rs.evaluate(regime, rows, _AGREE)["blocks"][0]["reasons"]


def test_sessions_after_the_minimum_are_ignored():
    regime, rows = world(40, 0.8, 0.2)
    base = rs.evaluate(regime, rows, _AGREE)
    more_regime, more_rows = world(45, 0.8, 0.2)
    later = {k: v for k, v in more_regime.items() if k[0] > base["sessions"][-1]}
    flipped = [dict(r, result="win") for r in more_rows if r["day"] > base["sessions"][-1]]
    again = rs.evaluate({**regime, **later}, rows + flipped, _AGREE)
    assert again == base
