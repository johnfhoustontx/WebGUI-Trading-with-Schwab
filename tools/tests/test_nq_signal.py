"""Tests for the NQ HUD's pure signal logic (no Redis, no SQLite, no tkinter).

Run: ``python -m pytest tools/tests -q`` from the repo root, or
``cd tools && ..\\.venv\\Scripts\\python -m pytest tests``. Both work — the
sys.path insert below makes the import cwd-independent, matching the other
tools/ tests.

These pin CURRENT behaviour. The side-correctness fixes land in a later task
with their own failing-tests-first, so nothing here encodes a known defect as
if it were intended.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import nq_signal as ns  # noqa: E402

from datetime import date, datetime  # noqa: E402


#############################################
# CONVERSION
#############################################

def test_ndx_scale_is_exactly_one_for_ndx():
    # $NDX needs no conversion, and must not depend on live quotes to say so.
    assert ns.ndx_scale("$NDX", None, None) == 1.0
    assert ns.ndx_scale("$NDX", 23000.0, 560.0) == 1.0


def test_ndx_scale_is_the_live_ratio_for_qqq():
    assert ns.ndx_scale("QQQ", 23000.0, 560.0) == pytest.approx(23000.0 / 560.0)


@pytest.mark.parametrize("ndx,src", [(None, 560.0), (23000.0, None),
                                     (23000.0, 0.0), (23000.0, -1.0)])
def test_ndx_scale_degrades_to_none(ndx, src):
    assert ns.ndx_scale("QQQ", ndx, src) is None


def test_qqq_strike_converts_to_nq_points():
    # The design's worked example: QQQ 560 at NDX 23000 with basis +120 -> NQ 23120.
    scale = ns.ndx_scale("QQQ", 23000.0, 560.0)
    assert ns.to_nq(560.0, scale, 120.0) == pytest.approx(23120.0)


@pytest.mark.parametrize("level,scale,basis", [
    (None, 41.0, 120.0),
    (560.0, None, 120.0),
    (560.0, 41.0, None),
])
def test_to_nq_returns_none_rather_than_a_wrong_number(level, scale, basis):
    # A missing input must paint "—", never a plausible-looking bad level.
    assert ns.to_nq(level, scale, basis) is None


#############################################
# REGIME
#############################################

def test_regime_positive_and_negative():
    assert ns.classify_regime(1100.0, 1000.0)[0] == "positive"
    assert ns.classify_regime(900.0, 1000.0)[0] == "negative"


def test_regime_flip_zone_boundary_is_inclusive_at_exactly_030_pct():
    # band = spot * 0.003; at spot 1000 that is 3.0 points.
    assert ns.classify_regime(1000.0, 997.0)[0] == "flip_zone"   # dist == +band
    assert ns.classify_regime(1000.0, 1003.0)[0] == "flip_zone"  # dist == -band
    # A hair outside on either side leaves the zone.
    assert ns.classify_regime(1000.0, 996.9)[0] == "positive"
    assert ns.classify_regime(1000.0, 1003.1)[0] == "negative"


def test_regime_reports_signed_distance():
    assert ns.classify_regime(1100.0, 1000.0)[1] == pytest.approx(100.0)
    assert ns.classify_regime(900.0, 1000.0)[1] == pytest.approx(-100.0)


@pytest.mark.parametrize("spot,flip", [(None, 1000.0), (1000.0, None), (None, None)])
def test_regime_unknown_when_an_input_is_missing(spot, flip):
    assert ns.classify_regime(spot, flip) == ("unknown", None)


#############################################
# SESSION GATING
#############################################

_WED = date(2026, 7, 29)   # a plain non-holiday weekday


def test_the_session_fixture_date_is_a_weekday_and_not_a_holiday():
    # Guards the cases below against a silently wrong fixture date.
    assert _WED.weekday() < 5
    assert _WED not in ns.HOLIDAYS


@pytest.mark.parametrize("hh,mm,expected", [
    (7, 0, "premarket"),
    (8, 35, "opening"),
    (9, 30, "morning"),
    (12, 0, "pin"),
    (14, 10, "afternoon"),
    (14, 56, "flatten"),
    (15, 30, "closed"),
])
def test_session_phase_across_the_day(hh, mm, expected):
    assert ns.session_phase(datetime(2026, 7, 29, hh, mm)) == expected


def test_weekend_is_closed():
    sat = datetime(2026, 8, 1, 12, 0)
    assert sat.weekday() == 5
    assert ns.session_phase(sat) == "closed"


def test_observed_july_fourth_is_closed():
    # 2026-07-03 is the observed July 4th holiday and a Friday — i.e. it would
    # pass a weekday-only check, which is the point of the holiday set.
    assert datetime(2026, 7, 3).weekday() < 5
    assert ns.session_phase(datetime(2026, 7, 3, 12, 0)) == "closed"


@pytest.mark.parametrize("phase,ok", [
    ("morning", True), ("pin", True), ("afternoon", True),
    ("premarket", False), ("opening", False), ("flatten", False), ("closed", False),
])
def test_tradeable_only_inside_the_window(phase, ok):
    assert ns.tradeable(phase) is ok


def test_every_phase_has_a_note():
    # PHASE_NOTE is the user-facing explanation; a missing key would paint blank.
    for phase in ("closed", "premarket", "opening", "morning", "pin",
                  "afternoon", "flatten"):
        assert ns.PHASE_NOTE.get(phase)


#############################################
# STOPS
#############################################

def test_stop_from_a_150_point_session_range():
    assert ns.stop_points(150.0) == pytest.approx(30.0)


def test_stop_clamps_at_the_floor_and_ceiling():
    assert ns.stop_points(10.0) == ns.MIN_STOP_POINTS     # 2.0 raw -> floor
    assert ns.stop_points(300.0) == ns.MAX_STOP_POINTS    # 60.0 raw -> ceiling


@pytest.mark.parametrize("atr", [None, 0.0, -5.0])
def test_stop_falls_back_to_the_floor_on_a_missing_range(atr):
    # Early session / no snapshots yet: never return 0 or a negative stop.
    assert ns.stop_points(atr) == ns.MIN_STOP_POINTS


#############################################
# VERDICT
#############################################

SPOT = 23000.0
# near() band at this spot = 23000 * 0.0015 = 34.5 points.
AT_CALL = {"call_wall": 23020.0, "put_wall": 22800.0, "pin": 22900.0}
AT_PUT = {"call_wall": 23200.0, "put_wall": 22980.0, "pin": 23100.0}
MID = {"call_wall": 23200.0, "put_wall": 22800.0, "pin": 23000.0}


def test_positive_gamma_at_the_call_wall_shorts_with_the_stop_above_the_wall():
    v = ns.build_verdict("positive", "pin", SPOT, AT_CALL, 150.0)
    assert v["action"] == "SHORT"
    assert v["stop"] > AT_CALL["call_wall"]
    assert v["target"] == AT_CALL["pin"]
    assert v["entry"] == SPOT


def test_positive_gamma_at_the_put_wall_longs_with_the_stop_below_the_wall():
    v = ns.build_verdict("positive", "pin", SPOT, AT_PUT, 150.0)
    assert v["action"] == "LONG"
    assert v["stop"] < AT_PUT["put_wall"]
    assert v["target"] == AT_PUT["pin"]


def test_positive_gamma_mid_range_waits():
    v = ns.build_verdict("positive", "pin", SPOT, MID, 150.0)
    assert v["action"] == "WAIT"
    assert v["entry"] is None


def test_negative_gamma_inside_the_walls_waits():
    v = ns.build_verdict("negative", "pin", SPOT, MID, 150.0)
    assert v["action"] == "WAIT"


def test_negative_gamma_broken_above_goes_long_with_no_fixed_target():
    lv = {"call_wall": 22900.0, "put_wall": 22700.0, "pin": 22800.0}
    v = ns.build_verdict("negative", "pin", SPOT, lv, 150.0)
    assert v["action"] == "LONG"
    assert v["target"] is None      # continuation: trail, do not cap


def test_negative_gamma_broken_below_goes_short_with_no_fixed_target():
    lv = {"call_wall": 23300.0, "put_wall": 23100.0, "pin": 23200.0}
    v = ns.build_verdict("negative", "pin", SPOT, lv, 150.0)
    assert v["action"] == "SHORT"
    assert v["target"] is None


def test_flip_zone_always_stands_down():
    v = ns.build_verdict("flip_zone", "pin", SPOT, AT_CALL, 150.0)
    assert v["action"] == "STAND DOWN"
    assert v["entry"] is None


def test_unknown_regime_stands_down():
    v = ns.build_verdict("unknown", "pin", SPOT, AT_CALL, 150.0)
    assert v["action"] == "STAND DOWN"


@pytest.mark.parametrize("phase", ["premarket", "opening", "flatten", "closed"])
def test_outside_the_trading_window_always_waits(phase):
    # Even sitting exactly on a wall with a textbook setup.
    v = ns.build_verdict("positive", phase, SPOT, AT_CALL, 150.0)
    assert v["action"] == "WAIT"
    assert v["entry"] is None


@pytest.mark.parametrize("levels", [
    {}, {"call_wall": None, "put_wall": None, "pin": None},
    {"call_wall": 23020.0}, {"put_wall": 22980.0},
])
@pytest.mark.parametrize("regime", ["positive", "negative"])
def test_missing_walls_never_raise(regime, levels):
    v = ns.build_verdict(regime, "pin", SPOT, levels, 150.0)
    assert v["action"] in ("LONG", "SHORT", "WAIT", "STAND DOWN")


def test_verdict_carries_no_colour():
    # Presentation belongs to the HUD; a colour here would drag tkinter into
    # the pure module's contract.
    assert "color" not in ns.build_verdict("positive", "pin", SPOT, AT_CALL, 150.0)


#############################################
# RISK DISTANCE
#############################################

@pytest.mark.parametrize("offset", [-34.0, -20.0, -5.0, 0.0, 5.0, 20.0, 34.0])
def test_risk_distance_stays_within_the_proximity_band_plus_the_stop(offset):
    """The proximity band is ~35 NQ points at 23,000, so testing stop_points in
    isolation does not bound the real risk: the entry can sit anywhere in that
    band while the stop is measured from the WALL. Assert the upper bound.
    """
    wall = SPOT + offset          # spot sits `-offset` from the wall, inside the band
    lv = {"call_wall": wall, "put_wall": 22000.0, "pin": 22900.0}
    v = ns.build_verdict("positive", "pin", SPOT, lv, 150.0)
    assert v["action"] == "SHORT", "fixture must stay inside the proximity band"
    sp = ns.stop_points(150.0)
    band = SPOT * ns.WALL_PROXIMITY_PCT
    assert abs(v["entry"] - v["stop"]) <= band + sp


#############################################
# LEVEL SIDEDNESS — stop and target must bracket the entry correctly
#############################################

def test_short_is_not_taken_when_the_pin_sits_above_the_entry():
    """A fade needs a mean-reversion target on the profitable side.

    The pin is the max-|net| strike anywhere in the grid — nothing constrains it
    to lie between spot and the direction of the trade. A SHORT whose target is
    ABOVE its entry is not a trade.
    """
    lv = {"call_wall": 23020.0, "put_wall": 22000.0, "pin": 23150.0}
    v = ns.build_verdict("positive", "pin", SPOT, lv, 150.0)
    assert not (v["action"] == "SHORT" and v["target"] > v["entry"])


def test_long_is_not_taken_when_the_pin_sits_below_the_entry():
    lv = {"call_wall": 24000.0, "put_wall": 22980.0, "pin": 22850.0}
    v = ns.build_verdict("positive", "pin", SPOT, lv, 150.0)
    assert not (v["action"] == "LONG" and v["target"] < v["entry"])


def test_short_stop_is_above_the_entry_even_when_spot_overshoots_the_wall():
    """Spot can sit ABOVE the call wall and still be "at" it (the band is ~35
    points at 23,000). With a small ATR the wall-derived stop then lands BELOW
    the entry — a short that is already stopped out the moment it is taken.
    """
    lv = {"call_wall": 22980.0, "put_wall": 22000.0, "pin": 22900.0}
    v = ns.build_verdict("positive", "pin", SPOT, lv, 10.0)   # -> MIN stop
    if v["action"] == "SHORT":
        assert v["stop"] > v["entry"]


def test_long_stop_is_below_the_entry_even_when_spot_undershoots_the_wall():
    lv = {"call_wall": 24000.0, "put_wall": 23020.0, "pin": 23100.0}
    v = ns.build_verdict("positive", "pin", SPOT, lv, 10.0)
    if v["action"] == "LONG":
        assert v["stop"] < v["entry"]


@pytest.mark.parametrize("regime", ["positive", "negative"])
@pytest.mark.parametrize("wall_offset", [-300.0, -34.0, -5.0, 0.0, 5.0, 34.0, 300.0])
@pytest.mark.parametrize("pin_offset", [-300.0, -100.0, -1.0, 1.0, 100.0, 300.0])
@pytest.mark.parametrize("atr", [10.0, 150.0, 400.0])
def test_any_signal_brackets_its_entry_correctly(regime, wall_offset, pin_offset, atr):
    """Sweep the whole space: whatever comes out, a SHORT's stop is above and
    its target below the entry, and a LONG's are the other way round.

    Both regimes, and wall offsets that reach beyond the proximity band so the
    negative-gamma break branches are exercised too.
    """
    for side in ("call", "put"):
        lv = {"call_wall": 24000.0, "put_wall": 22000.0, "pin": SPOT + pin_offset}
        lv[f"{side}_wall"] = SPOT + wall_offset
        v = ns.build_verdict(regime, "pin", SPOT, lv, atr)
        if v["action"] == "SHORT":
            assert v["stop"] > v["entry"]
            if v["target"] is not None:
                assert v["target"] < v["entry"]
        elif v["action"] == "LONG":
            assert v["stop"] < v["entry"]
            if v["target"] is not None:
                assert v["target"] > v["entry"]


#############################################
# THE INVARIANT — never fade a short-gamma tape
#############################################

@pytest.mark.parametrize("spot", [22700.0 + 10.0 * i for i in range(61)])
def test_negative_gamma_never_fades_anywhere_across_the_wall_range(spot):
    """Parametrised, not spot-checked: for EVERY spot across (and beyond) the
    walls, a negative-gamma verdict must never take the mean-reverting side.

    Fading a short-gamma tape is the one mistake that compounds — dealer hedging
    pushes the move further, so the loser grows instead of reverting.
    """
    call_wall, put_wall = 23100.0, 22900.0
    lv = {"call_wall": call_wall, "put_wall": put_wall, "pin": 23000.0}
    v = ns.build_verdict("negative", "pin", spot, lv, 150.0)
    if spot > call_wall:
        assert v["action"] != "SHORT", "shorting a break ABOVE the call wall is a fade"
    if spot < put_wall:
        assert v["action"] != "LONG", "longing a break BELOW the put wall is a fade"
