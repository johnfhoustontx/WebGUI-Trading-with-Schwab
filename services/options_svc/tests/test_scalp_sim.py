"""The Opening Scalp's trade model (scalp_sim): one 0DTE trade along hand-built bars.

Every expected figure is computed here from ``options_calculator.bs_price`` and
``expiry_time_to_years`` directly, never from ``scalp_sim.value``, so the tests
hold the module to the pricer and not to itself. No network, no store."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import repo_paths
from services.options_svc import scalp_sim as sim
from services.options_svc.scalp_store import Bar
from shared import scalp_config

oc = sim._calc()
_CT = ZoneInfo("America/Chicago")
DAY = dt.date(2026, 10, 19)
ENTRY = dt.datetime(2026, 10, 19, 8, 35, tzinfo=_CT)
SPOT0 = 571.4
SIGMA = 0.20


def _ts(h, m):
    return int(dt.datetime(2026, 10, 19, h, m, tzinfo=_CT).timestamp())


def _bar(h, m, o, hi, lo, c):
    return Bar(_ts(h, m), o, hi, lo, c, 1000.0)


def _flat(h, m, px):
    return _bar(h, m, px, px, px, px)


def _bs(spot, strike, kind, when):
    t = oc.expiry_time_to_years(when, DAY)
    return oc.bs_price(spot, strike, t, oc.RISK_FREE_RATE, SIGMA, kind)


def _at(h, m):
    return dt.datetime(2026, 10, 19, h, m, tzinfo=_CT)


def _flat_path(px, n=12, start=(8, 35)):
    """``n`` flat 5-minute bars from ``start``."""
    base = dt.datetime(2026, 10, 19, *start, tzinfo=_CT)
    out = []
    for i in range(n):
        t = base + dt.timedelta(minutes=5 * i)
        out.append(_flat(t.hour, t.minute, px))
    return out


# ── strikes and structures ──────────────────────────────────────────────────────

def test_strikes_pcs_short_at_or_below_spot():
    assert sim.strikes_for("pcs", 571.4, 1, 1, 0) == (571.0, 570.0)


def test_strikes_ccs_short_at_or_above_spot():
    assert sim.strikes_for("ccs", 571.4, 1, 1, 0) == (572.0, 573.0)


def test_strikes_long_options_and_otm_steps():
    assert sim.strikes_for("long_call", 571.4, 1, 1, 0) == (572.0, None)
    assert sim.strikes_for("long_put", 571.4, 1, 1, 0) == (571.0, None)
    assert sim.strikes_for("long_call", 571.4, 1, 1, 2) == (574.0, None)
    assert sim.strikes_for("long_put", 571.4, 1, 1, 2) == (569.0, None)
    assert sim.strikes_for("pcs", 5712.0, 5, 5, 1) == (5705.0, 5700.0)
    assert sim.strikes_for("ccs", 5712.0, 5, 5, 1) == (5720.0, 5725.0)


def test_strikes_spot_on_a_strike_uses_that_strike():
    assert sim.strikes_for("long_call", 571.0, 1, 1, 0) == (571.0, None)
    assert sim.strikes_for("long_put", 571.0, 1, 1, 0) == (571.0, None)
    assert sim.strikes_for("pcs", 571.0, 1, 1, 0) == (571.0, 570.0)
    assert sim.strikes_for("ccs", 571.0, 1, 1, 0) == (571.0, 572.0)


def test_structure_mirrors_on_down_morning():
    assert [sim.structure_for(r, 1) for r in sim.ROLES] == [
        "long_call", "long_put", "pcs", "ccs"]
    assert [sim.structure_for(r, -1) for r in sim.ROLES] == [
        "long_put", "long_call", "ccs", "pcs"]
    with pytest.raises(ValueError):
        sim.structure_for("bogus", 1)
    with pytest.raises(ValueError):
        sim.structure_for("long_with", 0)


# ── value ───────────────────────────────────────────────────────────────────────

def test_value_matches_the_pricer():
    when = _at(8, 45)
    assert sim.value("long_call", 572.0, (572.0, None), when, DAY, SIGMA) == \
        pytest.approx(_bs(572.0, 572.0, "call", when))
    pcs = _bs(572.0, 571.0, "put", when) - _bs(572.0, 570.0, "put", when)
    assert sim.value("pcs", 572.0, (571.0, 570.0), when, DAY, SIGMA) == pytest.approx(pcs)
    assert pcs > 0
    ccs = _bs(572.0, 572.0, "call", when) - _bs(572.0, 573.0, "call", when)
    assert sim.value("ccs", 572.0, (572.0, 573.0), when, DAY, SIGMA) == pytest.approx(ccs)
    # A naive datetime means Central: the same instant gives the same value.
    assert sim.value("long_call", 572.0, (572.0, None), when.replace(tzinfo=None),
                     DAY, SIGMA) == pytest.approx(_bs(572.0, 572.0, "call", when))


# ── long options ────────────────────────────────────────────────────────────────

def _call():
    return sim.strikes_for("long_call", SPOT0, 1, 1, 0)


def test_long_call_win_first():
    entry_val = _bs(SPOT0, 572.0, "call", ENTRY)
    path = [_flat(8, 35, SPOT0), _bar(8, 40, SPOT0, SPOT0 + 1.6, SPOT0, SPOT0 + 1.0),
            _flat(8, 45, SPOT0 - 5)]
    # The second bar's high prices to a win at its END (08:45); precondition.
    assert _bs(SPOT0 + 1.6, 572.0, "call", _at(8, 45)) >= entry_val * 1.5
    assert _bs(SPOT0, 572.0, "call", _at(8, 45)) > entry_val * 0.5
    out = sim.simulate("long_call", SPOT0, _call(), ENTRY, DAY, SIGMA, path, 5)
    assert out.result == "win"
    assert out.minutes == 10          # 08:35 -> END of the 08:40 bar
    assert out.entry_value == pytest.approx(entry_val)
    assert out.end_pnl_pct == pytest.approx(50.0)
    assert out.both_in_bar is False


def test_long_call_loss_first():
    entry_val = _bs(SPOT0, 572.0, "call", ENTRY)
    path = [_flat(8, 35, SPOT0), _bar(8, 40, SPOT0, SPOT0, SPOT0 - 2.0, SPOT0 - 1.5),
            _flat(8, 45, SPOT0 + 5)]
    assert _bs(SPOT0 - 2.0, 572.0, "call", _at(8, 45)) <= entry_val * 0.5
    out = sim.simulate("long_call", SPOT0, _call(), ENTRY, DAY, SIGMA, path, 5)
    assert out.result == "loss"
    assert out.minutes == 10
    assert out.end_pnl_pct == pytest.approx(-50.0)
    assert out.both_in_bar is False


def test_both_levels_in_one_bar_is_loss():
    entry_val = _bs(SPOT0, 572.0, "call", ENTRY)
    path = [_bar(8, 35, SPOT0, SPOT0 + 3.0, SPOT0 - 3.0, SPOT0)]
    assert _bs(SPOT0 + 3.0, 572.0, "call", _at(8, 40)) >= entry_val * 1.5
    assert _bs(SPOT0 - 3.0, 572.0, "call", _at(8, 40)) <= entry_val * 0.5
    out = sim.simulate("long_call", SPOT0, _call(), ENTRY, DAY, SIGMA, path, 5)
    assert out.result == "loss"
    assert out.both_in_bar is True
    assert out.minutes == 5


def test_long_put_wins_on_the_low_and_loses_on_the_high():
    strikes = sim.strikes_for("long_put", SPOT0, 1, 1, 0)       # 571
    entry_val = _bs(SPOT0, 571.0, "put", ENTRY)
    down = [_bar(8, 35, SPOT0, SPOT0, SPOT0 - 1.6, SPOT0 - 1.0)]
    assert _bs(SPOT0 - 1.6, 571.0, "put", _at(8, 40)) >= entry_val * 1.5
    assert sim.simulate("long_put", SPOT0, strikes, ENTRY, DAY, SIGMA, down, 5).result == "win"
    up = [_bar(8, 35, SPOT0, SPOT0 + 1.5, SPOT0, SPOT0 + 1.0)]
    assert _bs(SPOT0 + 1.5, 571.0, "put", _at(8, 40)) <= entry_val * 0.5
    assert sim.simulate("long_put", SPOT0, strikes, ENTRY, DAY, SIGMA, up, 5).result == "loss"


def test_timeout_after_60_minutes():
    entry_val = _bs(SPOT0, 572.0, "call", ENTRY)
    path = _flat_path(SPOT0)                                      # 08:35 .. 09:30 starts
    # Bars at and after 09:35 are outside the window and must be ignored.
    path += [_bar(9, 35, SPOT0, SPOT0 + 9.0, SPOT0 - 9.0, SPOT0),
             _flat(9, 40, SPOT0 + 9.0)]
    v_end = _bs(SPOT0, 572.0, "call", _at(9, 35))
    assert entry_val * 0.5 < v_end < entry_val * 1.5              # precondition
    out = sim.simulate("long_call", SPOT0, _call(), ENTRY, DAY, SIGMA, path, 5)
    assert out.result == "timeout"
    assert out.minutes == 60
    assert out.end_pnl_pct == pytest.approx((v_end - entry_val) / entry_val * 100.0)
    assert out.end_pnl_pct < 0                                    # decay only
    assert out.both_in_bar is False


def test_bars_before_the_entry_are_not_in_the_path():
    early = [_bar(8, 30, SPOT0, SPOT0 + 9.0, SPOT0 - 9.0, SPOT0)]
    out = sim.simulate("long_call", SPOT0, _call(), ENTRY, DAY, SIGMA,
                       early + _flat_path(SPOT0, n=2), 5)
    assert out.result == "timeout"
    assert out.minutes == 10


def test_tiny_premium_skips():
    strikes = (600.0, None)                                       # far out of the money
    assert _bs(SPOT0, 600.0, "call", ENTRY) < scalp_config.min_premium()
    out = sim.simulate("long_call", SPOT0, strikes, ENTRY, DAY, SIGMA,
                       _flat_path(SPOT0), 5)
    assert out.result == "skip"
    assert out.minutes is None and out.end_pnl_pct is None
    assert out.entry_value == pytest.approx(_bs(SPOT0, 600.0, "call", ENTRY))


def test_an_empty_path_is_a_skip():
    out = sim.simulate("long_call", SPOT0, _call(), ENTRY, DAY, SIGMA, [], 5)
    assert out.result == "skip"
    assert out.minutes is None and out.end_pnl_pct is None
    assert out.entry_value == pytest.approx(_bs(SPOT0, 572.0, "call", ENTRY))


def test_the_target_percent_is_read_from_config(monkeypatch):
    entry_val = _bs(SPOT0, 572.0, "call", ENTRY)
    path = [_bar(8, 35, SPOT0, SPOT0 + 0.5, SPOT0, SPOT0 + 0.3)]
    v_hi = _bs(SPOT0 + 0.5, 572.0, "call", _at(8, 40))
    assert entry_val * 1.2 <= v_hi < entry_val * 1.5              # wins at 20, not at 50
    strikes = _call()
    assert sim.simulate("long_call", SPOT0, strikes, ENTRY, DAY, SIGMA, path, 5).result == "timeout"
    monkeypatch.setattr(scalp_config, "target_pct", lambda: 20.0)
    out = sim.simulate("long_call", SPOT0, strikes, ENTRY, DAY, SIGMA, path, 5)
    assert out.result == "win"
    assert out.end_pnl_pct == pytest.approx(20.0)


# ── credit spreads ──────────────────────────────────────────────────────────────

def _pcs_credit(when=ENTRY, spot=SPOT0):
    return _bs(spot, 571.0, "put", when) - _bs(spot, 570.0, "put", when)


def test_pcs_win_at_half_credit():
    credit = _pcs_credit()
    strikes = sim.strikes_for("pcs", SPOT0, 1, 1, 0)
    path = [_bar(8, 35, SPOT0, SPOT0 + 2.5, SPOT0, SPOT0 + 1.0)]
    assert _pcs_credit(_at(8, 40), SPOT0 + 2.5) <= credit * 0.5
    assert _pcs_credit(_at(8, 40), SPOT0) > credit * 0.5
    out = sim.simulate("pcs", SPOT0, strikes, ENTRY, DAY, SIGMA, path, 5)
    assert out.result == "win"
    assert out.entry_value == pytest.approx(credit)
    assert out.minutes == 5
    assert out.end_pnl_pct == pytest.approx(50.0)


def test_pcs_stop_at_one_and_half_credit():
    credit = _pcs_credit()
    strikes = sim.strikes_for("pcs", SPOT0, 1, 1, 0)
    path = [_bar(8, 35, SPOT0, SPOT0, SPOT0 - 2.0, SPOT0 - 0.5)]
    assert _pcs_credit(_at(8, 40), SPOT0 - 2.0) >= credit * 1.5
    assert _pcs_credit(_at(8, 40), SPOT0) < credit * 1.5
    out = sim.simulate("pcs", SPOT0, strikes, ENTRY, DAY, SIGMA, path, 5)
    assert out.result == "loss"
    assert out.end_pnl_pct == pytest.approx(-50.0)
    assert out.both_in_bar is False


def test_ccs_wins_on_the_low_and_loses_on_the_high():
    strikes = sim.strikes_for("ccs", SPOT0, 1, 1, 0)              # 572 / 573

    def cost(spot, when):
        return _bs(spot, 572.0, "call", when) - _bs(spot, 573.0, "call", when)

    credit = cost(SPOT0, ENTRY)
    down = [_bar(8, 35, SPOT0, SPOT0, SPOT0 - 2.5, SPOT0 - 1.0)]
    assert cost(SPOT0 - 2.5, _at(8, 40)) <= credit * 0.5
    assert sim.simulate("ccs", SPOT0, strikes, ENTRY, DAY, SIGMA, down, 5).result == "win"
    up = [_bar(8, 35, SPOT0, SPOT0 + 1.5, SPOT0, SPOT0 + 1.0)]
    assert cost(SPOT0 + 1.5, _at(8, 40)) >= credit * 1.5
    assert sim.simulate("ccs", SPOT0, strikes, ENTRY, DAY, SIGMA, up, 5).result == "loss"


def test_credit_timeout_marks_the_cost_to_close_at_the_last_close():
    credit = _pcs_credit()
    strikes = sim.strikes_for("pcs", SPOT0, 1, 1, 0)
    out = sim.simulate("pcs", SPOT0, strikes, ENTRY, DAY, SIGMA, _flat_path(SPOT0), 5)
    cost = _pcs_credit(_at(9, 35))
    assert credit * 0.5 < cost < credit * 1.5                     # precondition
    assert out.result == "timeout"
    assert out.minutes == 60
    assert out.end_pnl_pct == pytest.approx((credit - cost) / credit * 100.0)


def test_credit_both_levels_in_one_bar_is_loss():
    strikes = sim.strikes_for("pcs", SPOT0, 1, 1, 0)
    path = [_bar(8, 35, SPOT0, SPOT0 + 2.0, SPOT0 - 2.0, SPOT0)]
    out = sim.simulate("pcs", SPOT0, strikes, ENTRY, DAY, SIGMA, path, 5)
    assert out.result == "loss" and out.both_in_bar is True


# ── level_points ────────────────────────────────────────────────────────────────

def test_level_points_signs():
    win, loss = sim.level_points("long_call", SPOT0, _call(), ENTRY, DAY, SIGMA)
    assert win is not None and loss is not None
    assert win > 0 > loss
    entry_val = _bs(SPOT0, 572.0, "call", ENTRY)
    assert _bs(SPOT0 + win, 572.0, "call", ENTRY) == pytest.approx(entry_val * 1.5, rel=1e-6)
    assert _bs(SPOT0 + loss, 572.0, "call", ENTRY) == pytest.approx(entry_val * 0.5, rel=1e-6)


def test_level_points_for_a_put_and_a_credit_spread():
    win, loss = sim.level_points("long_put", SPOT0, (571.0, None), ENTRY, DAY, SIGMA)
    assert win < 0 < loss
    credit = _pcs_credit()
    win, loss = sim.level_points("pcs", SPOT0, (571.0, 570.0), ENTRY, DAY, SIGMA)
    assert win > 0 > loss
    assert _pcs_credit(ENTRY, SPOT0 + win) == pytest.approx(credit * 0.5, rel=1e-6)
    assert _pcs_credit(ENTRY, SPOT0 + loss) == pytest.approx(credit * 1.5, rel=1e-6)


def test_level_points_none_where_the_level_is_out_of_reach():
    # A $1-wide spread whose credit is above two thirds of the width: 1.5x the
    # credit is more than the spread can ever cost, so no price reaches the stop.
    strikes = (575.0, 574.0)
    assert 1.5 * _bs(SPOT0, 575.0, "put", ENTRY) - 1.5 * _bs(SPOT0, 574.0, "put", ENTRY) > 1.0
    win, loss = sim.level_points("pcs", SPOT0, strikes, ENTRY, DAY, SIGMA)
    assert loss is None
    assert win is not None and win > 0


# ── short sessions ──────────────────────────────────────────────────────────────

def _session(last_h, last_m, first=(8, 30)):
    bars = []
    t = dt.datetime(2026, 10, 19, *first, tzinfo=_CT)
    end = dt.datetime(2026, 10, 19, last_h, last_m, tzinfo=_CT)
    while t <= end:
        bars.append(_flat(t.hour, t.minute, 5700.0))
        t += dt.timedelta(minutes=5)
    return bars


def test_short_session_detected():
    assert sim.short_session(_session(11, 55)) is True
    assert sim.short_session(_session(12, 55)) is True
    assert sim.short_session(_session(14, 50)) is True


def test_full_session_is_not_short():
    assert sim.short_session(_session(14, 55)) is False
    one_minute = [_flat(14, 59, 5700.0)]
    assert sim.short_session(_session(14, 50) + one_minute) is False


def test_short_session_ignores_bars_outside_the_regular_session():
    after_hours = [_flat(15, 0, 5700.0), _flat(16, 0, 5700.0), _flat(17, 0, 5700.0)]
    pre = [_flat(7, 0, 5700.0)]
    assert sim.short_session(pre + _session(11, 55) + after_hours) is True
    assert sim.short_session(pre + _session(14, 55) + after_hours) is False


def test_short_session_with_no_regular_bars_is_true():
    assert sim.short_session([]) is True
    assert sim.short_session([_flat(7, 0, 5700.0), _flat(16, 0, 5700.0)]) is True


def test_scalp_sim_imports_nothing_from_compute():
    import ast
    src = (repo_paths.REPO_ROOT / "services" / "options_svc" / "scalp_sim.py"
           ).read_text(encoding="utf-8")
    names = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names += [f"{node.module}.{a.name}" for a in node.names]
    assert names and not [n for n in names if n.split(".")[-1] == "compute"
                          or ".compute" in n]
