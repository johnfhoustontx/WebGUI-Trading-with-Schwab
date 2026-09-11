"""Pure Simulator readouts (``pages.options.sim_view``) — no browser, no bus."""
import math

import pytest

from pages.options import sim_view as sv

EXP = "2026-10-23"


def _leg(otype, side, strike, qty=1, expiry=EXP):
    return {"option_type": otype, "side": side, "strike": strike,
            "expiry": expiry, "qty": qty, "premium": None}


PCS_10 = [_leg("put", "short", 330.0, 10), _leg("put", "long", 325.0, 10)]


# -- Task 1: the expiration payoff ---------------------------------------------

def test_put_credit_spread_facts_are_exact():
    """Entry is minus the service's baseline (a short spread's value is negative:
    the credit received). Loss is width x 100 x qty minus the credit; the single
    breakeven sits where the corner line crosses zero."""
    f = sv.payoff_facts(PCS_10, baseline=-1000.0)
    assert f["entry"] == 1000.0
    assert f["max_profit"] == 1000.0
    assert f["max_loss"] == 4000.0
    assert f["breakevens"] == [329.0]
    assert f["reason"] is None


def test_long_call_profit_is_unlimited_and_breakeven_is_beyond_the_strike():
    f = sv.payoff_facts([_leg("call", "long", 100.0)], baseline=500.0)
    assert f["entry"] == -500.0                 # a debit paid
    assert f["max_profit"] == "unlimited"
    assert f["max_loss"] == 500.0
    assert f["breakevens"] == [105.0]


def test_naked_call_loss_is_unlimited():
    f = sv.payoff_facts([_leg("call", "short", 110.0)], baseline=-200.0)
    assert f["max_loss"] == "unlimited"
    assert f["max_profit"] == 200.0
    assert f["breakevens"] == [112.0]


def test_iron_condor_risks_one_side_and_has_two_breakevens():
    legs = [_leg("put", "short", 95.0), _leg("put", "long", 90.0),
            _leg("call", "short", 105.0), _leg("call", "long", 110.0)]
    f = sv.payoff_facts(legs, baseline=-150.0)
    assert f["max_profit"] == 150.0
    assert f["max_loss"] == 350.0               # 5-wide minus the credit, ONE side
    assert f["breakevens"] == [93.5, 106.5]


def test_mixed_expiries_state_the_entry_but_refuse_the_expiry_figures():
    """A single-date payoff would settle a live back leg at intrinsic; the tiles
    say why they are blank instead of printing that number."""
    legs = [_leg("put", "short", 330.0, 10, "2026-10-23"),
            _leg("put", "long", 325.0, 10, "2026-09-09")]
    f = sv.payoff_facts(legs, baseline=-1000.0)
    assert f["entry"] == 1000.0
    assert f["max_profit"] is None and f["max_loss"] is None
    assert f["breakevens"] is None
    assert f["reason"] == "mixed_expiry"


@pytest.mark.parametrize("baseline", [None, float("nan"), float("inf"), True, "x"])
def test_an_unusable_baseline_is_no_entry_never_a_zero(baseline):
    f = sv.payoff_facts(PCS_10, baseline=baseline)
    assert f["entry"] is None
    assert f["max_profit"] is None and f["max_loss"] is None
    assert f["reason"] == "unpriced"


def test_a_leg_without_a_strike_is_incomplete():
    legs = [_leg("put", "short", None), _leg("put", "long", 325.0)]
    f = sv.payoff_facts(legs, baseline=-100.0)
    assert f["reason"] == "incomplete"
    assert f["max_loss"] is None


def test_no_legs_is_incomplete():
    assert sv.payoff_facts([], baseline=0.0)["reason"] == "incomplete"


# -- Task 2: the six tiles -------------------------------------------------------

def _result(baseline=-1000.0, delta=0.5, theta=0.3, units="position", spot=354.0):
    base = {"theo_price": baseline, "delta": delta, "gamma": 0.01,
            "theta": theta, "vega": -2.0, "rho": 0.1}
    return {"spot": spot, "whatif_baseline": baseline,
            "ivshock": {"base": base, "shock": dict(base), "units": units}}


def _by_key(tiles):
    return {t["key"]: t for t in tiles}


def test_tiles_are_always_six_in_a_fixed_order():
    keys = [t["key"] for t in sv.position_tiles(PCS_10, _result())]
    assert keys == ["entry", "max_profit", "max_loss", "breakeven", "delta", "theta"]
    keys_empty = [t["key"] for t in sv.position_tiles([], None)]
    assert keys_empty == keys


def test_tiles_for_a_credit_spread():
    t = _by_key(sv.position_tiles(PCS_10, _result(delta=145.0, theta=42.0)))
    assert t["entry"]["label"] == "Entry credit"
    assert t["entry"]["value"] == "$1,000"
    assert t["max_profit"]["value"] == "$1,000"
    assert t["max_profit"]["tone"] == "pos"
    assert t["max_loss"]["value"] == "$4,000"
    assert t["max_loss"]["tone"] == "neg"
    assert t["breakeven"]["label"] == "Breakeven"
    assert t["breakeven"]["value"] == "329.00"
    assert t["breakeven"]["sub"] == "7.1% below spot"
    assert t["delta"]["value"] == "+145"
    assert t["delta"]["sub"] == "moves like 145 shares long"
    assert t["theta"]["value"] == "+$42"
    assert t["theta"]["tone"] == "pos"


def test_a_debit_is_labelled_as_one():
    t = _by_key(sv.position_tiles([_leg("call", "long", 100.0)], _result(baseline=500.0)))
    assert t["entry"]["label"] == "Entry debit"
    assert t["entry"]["value"] == "$500"
    assert t["max_profit"]["value"] == "Unlimited"


def test_two_breakevens_are_joined_in_words():
    legs = [_leg("put", "short", 95.0), _leg("put", "long", 90.0),
            _leg("call", "short", 105.0), _leg("call", "long", 110.0)]
    t = _by_key(sv.position_tiles(legs, _result(baseline=-150.0, spot=100.0)))
    assert t["breakeven"]["label"] == "Breakevens"
    assert t["breakeven"]["value"] == "93.50 and 106.50"


def test_mixed_expiry_tiles_say_why_they_are_blank():
    legs = [_leg("put", "short", 330.0, 10, "2026-10-23"),
            _leg("put", "long", 325.0, 10, "2026-09-09")]
    t = _by_key(sv.position_tiles(legs, _result()))
    assert t["max_loss"]["value"] == sv.NO_READING
    assert "different dates" in t["max_loss"]["sub"]


def test_no_result_is_six_em_dashes():
    for tile in sv.position_tiles(PCS_10, None):
        assert tile["value"] == sv.NO_READING


def test_a_legacy_per_share_payload_is_scaled_to_the_position():
    """A cache written before the service stated its units carries per-share
    Greeks; the tile must not print 1.45 under a shares-equivalent label."""
    t = _by_key(sv.position_tiles(PCS_10, _result(delta=1.45, theta=0.42, units=None)))
    assert t["delta"]["value"] == "+145"
    assert t["theta"]["value"] == "+$42"


def test_a_nan_greek_is_an_em_dash():
    t = _by_key(sv.position_tiles(PCS_10, _result(delta=float("nan"))))
    assert t["delta"]["value"] == sv.NO_READING


# -- Task 3: slider readouts and the Days range ----------------------------------

import datetime as dt
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
NY = ZoneInfo("America/New_York")


def test_fractional_dte_counts_to_the_new_york_close():
    now = dt.datetime(2026, 9, 11, 11, 0, tzinfo=NY)
    assert sv.fractional_dte("2026-09-11", now) == pytest.approx(5 / 24)
    assert sv.fractional_dte("2026-09-12", now) == pytest.approx(1 + 5 / 24)


def test_fractional_dte_reads_a_naive_clock_as_central():
    """The project convention: a tz-naive datetime is CENTRAL wall-clock time.
    10:00 naive = 11:00 ET, five hours to the close."""
    assert sv.fractional_dte("2026-09-11", dt.datetime(2026, 9, 11, 10, 0)) == \
        pytest.approx(5 / 24)


def test_fractional_dte_floors_at_zero_and_refuses_junk():
    now = dt.datetime(2026, 9, 11, 17, 0, tzinfo=NY)
    assert sv.fractional_dte("2026-09-11", now) == 0.0
    assert sv.fractional_dte("not-a-date", now) is None
    assert sv.fractional_dte(None, now) is None


def test_days_range_for_a_zero_dte_position_steps_in_quarter_days():
    now = dt.datetime(2026, 9, 11, 11, 0, tzinfo=NY)
    r = sv.days_range([_leg("put", "short", 100.0, expiry="2026-09-11")], now)
    assert r["step"] == 0.25
    assert r["max"] == 0.25                      # reaches the close, not 30 days
    assert r["snaps"] == [("Now", 0.0), ("Expiry", 0.25)]


def test_days_range_for_mixed_expiries_names_the_first_one():
    now = dt.datetime(2026, 9, 11, 11, 0, tzinfo=NY)
    legs = [_leg("put", "short", 330.0, expiry="2026-09-28"),     # ~17.2 days
            _leg("put", "long", 325.0, expiry="2026-10-23")]      # ~42.2 days
    r = sv.days_range(legs, now)
    assert r["step"] == 1
    assert r["max"] == 43
    assert r["snaps"] == [("Now", 0.0), ("Halfway", 9.0), ("First expiry", 18.0)]


def test_days_range_with_no_expiry_falls_back_to_a_month():
    r = sv.days_range([], dt.datetime(2026, 9, 11, 11, 0, tzinfo=NY))
    assert r == {"max": 30, "step": 1, "snaps": [("Now", 0.0)]}


@pytest.mark.parametrize("days,text", [
    (0, "0 days"), (0.25, "6 hours"), (1 / 24, "1 hour"), (1, "1 day"),
    (5, "5 days"), (1.25, "1.25 days")])
def test_days_text(days, text):
    assert sv.days_text(days) == text


def test_curve_pnl_at_interpolates_and_refuses_outside_the_sweep():
    pairs = [[100.0, -200.0], [110.0, 300.0], [120.0, 300.0]]
    assert sv.curve_pnl_at(pairs, 105.0) == pytest.approx(50.0)
    assert sv.curve_pnl_at(pairs, 110.0) == pytest.approx(300.0)
    assert sv.curve_pnl_at(pairs, 99.0) is None
    assert sv.curve_pnl_at([], 105.0) is None
    assert sv.curve_pnl_at(pairs, None) is None


def test_whatif_readout_states_price_date_and_result():
    now = dt.datetime(2026, 9, 11, 11, 0, tzinfo=CT)
    pairs = [[100.0, -200.0], [110.0, 300.0]]
    text, tone = sv.whatif_readout(pairs, 105.0, 17, now)
    assert text == "At 105.00 on Sep 28: profit $50"
    assert tone == "pos"
    text, tone = sv.whatif_readout(pairs, 101.0, 0, now)
    assert text == "At 101.00 today: loss $150"
    assert tone == "neg"
    text, _ = sv.whatif_readout(pairs, 101.0, 0.25, now)
    assert text.startswith("At 101.00 in 6 hours:")


def test_whatif_readout_with_no_curve_is_blank():
    assert sv.whatif_readout([], 101.0, 0, dt.datetime(2026, 9, 11, tzinfo=CT)) == \
        ("", "neutral")
