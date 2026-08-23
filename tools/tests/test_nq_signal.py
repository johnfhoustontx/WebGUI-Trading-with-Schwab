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

from tools import nq_instruments as ni  # noqa: E402
from tools import nq_signal as ns  # noqa: E402

from datetime import date, datetime  # noqa: E402


#############################################
# CONVERSION
#############################################

def test_cash_scale_is_exactly_one_for_ndx():
    # $NDX needs no conversion, and must not depend on live quotes to say so.
    assert ns.cash_scale("$NDX", None, None) == 1.0
    assert ns.cash_scale("$NDX", 23000.0, 560.0) == 1.0


def test_cash_scale_is_the_live_ratio_for_qqq():
    assert ns.cash_scale("QQQ", 23000.0, 560.0) == pytest.approx(23000.0 / 560.0)


@pytest.mark.parametrize("ndx,src", [(None, 560.0), (23000.0, None),
                                     (23000.0, 0.0), (23000.0, -1.0)])
def test_cash_scale_degrades_to_none(ndx, src):
    assert ns.cash_scale("QQQ", ndx, src) is None


def test_qqq_strike_converts_to_future_points():
    # The design's worked example: QQQ 560 at NDX 23000 with basis +120 -> NQ 23120.
    scale = ns.cash_scale("QQQ", 23000.0, 560.0)
    assert ns.to_future(560.0, scale, 120.0) == pytest.approx(23120.0)


@pytest.mark.parametrize("level,scale,basis", [
    (None, 41.0, 120.0),
    (560.0, None, 120.0),
    (560.0, 41.0, None),
])
def test_to_future_returns_none_rather_than_a_wrong_number(level, scale, basis):
    # A missing input must paint "—", never a plausible-looking bad level.
    assert ns.to_future(level, scale, basis) is None


def test_to_index_converts_a_strike_to_cash_equivalent_points():
    # $NDX: scale 1.0, so a strike is already in index points.
    assert ns.to_index(27190.0, 1.0) == pytest.approx(27190.0)
    # QQQ: a 560 strike at NDX/QQQ ~41 is ~23,000 index points.
    assert ns.to_index(560.0, 23000.0 / 560.0) == pytest.approx(23000.0)


@pytest.mark.parametrize("level,scale", [(None, 1.0), (560.0, None), (None, None)])
def test_to_index_returns_none_rather_than_a_wrong_number(level, scale):
    assert ns.to_index(level, scale) is None


def test_to_future_is_to_index_plus_basis():
    """The two frames differ by an additive basis and nothing else — which is
    why distances (ATR, stop size, wall proximity) are frame-invariant and only
    LEVELS need shifting for display.
    """
    assert ns.to_future(560.0, 41.0, 120.0) == pytest.approx(ns.to_index(560.0, 41.0) + 120.0)


def test_shift_verdict_levels_moves_only_the_price_fields():
    v = {"action": "SHORT", "reason": "x", "entry": 23000.0,
         "stop": 23050.0, "target": 22900.0}
    out = ns.shift_verdict_levels(v, 120.0)
    assert out["entry"] == pytest.approx(23120.0)
    assert out["stop"] == pytest.approx(23170.0)
    assert out["target"] == pytest.approx(23020.0)
    assert out["action"] == "SHORT" and out["reason"] == "x"
    assert v["entry"] == 23000.0, "must not mutate the verdict it is given"


@pytest.mark.parametrize("basis", [None, 0.0])
def test_shift_verdict_levels_is_a_noop_without_a_usable_basis(basis):
    v = {"action": "SHORT", "entry": 23000.0, "stop": 23050.0, "target": None}
    out = ns.shift_verdict_levels(v, basis)
    assert out["entry"] == 23000.0 and out["stop"] == 23050.0
    assert out["target"] is None


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
    # Guards the cases below against a silently wrong fixture date. Asks the
    # shared calendar rather than a local HOLIDAYS literal (deleted 2026-08-20).
    assert _WED.weekday() < 5
    assert ns.is_trading_day(_WED)


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
    assert ns.stop_points(150.0, ni.NQ) == pytest.approx(30.0)


def test_stop_clamps_at_the_floor_and_ceiling():
    assert ns.stop_points(10.0, ni.NQ) == ni.NQ.min_stop     # 2.0 raw -> floor
    assert ns.stop_points(300.0, ni.NQ) == ni.NQ.max_stop    # 60.0 raw -> ceiling


@pytest.mark.parametrize("atr", [None, 0.0, -5.0])
def test_stop_falls_back_to_the_floor_on_a_missing_range(atr):
    # Early session / no snapshots yet: never return 0 or a negative stop.
    assert ns.stop_points(atr, ni.NQ) == ni.NQ.min_stop


#############################################
# VERDICT
#############################################

SPOT = 23000.0
# near() band at this spot = 23000 * 0.0015 = 34.5 points.
AT_CALL = {"call_wall": 23020.0, "put_wall": 22800.0, "pin": 22900.0}
AT_PUT = {"call_wall": 23200.0, "put_wall": 22980.0, "pin": 23100.0}
MID = {"call_wall": 23200.0, "put_wall": 22800.0, "pin": 23000.0}


def test_positive_gamma_at_the_call_wall_shorts_with_the_stop_above_the_wall():
    v = ns.build_verdict("positive", "pin", SPOT, AT_CALL, 150.0, ni.NQ)
    assert v["action"] == "SHORT"
    assert v["stop"] > AT_CALL["call_wall"]
    assert v["target"] == AT_CALL["pin"]
    assert v["entry"] == SPOT


def test_positive_gamma_at_the_put_wall_longs_with_the_stop_below_the_wall():
    v = ns.build_verdict("positive", "pin", SPOT, AT_PUT, 150.0, ni.NQ)
    assert v["action"] == "LONG"
    assert v["stop"] < AT_PUT["put_wall"]
    assert v["target"] == AT_PUT["pin"]


def test_positive_gamma_mid_range_waits():
    v = ns.build_verdict("positive", "pin", SPOT, MID, 150.0, ni.NQ)
    assert v["action"] == "WAIT"
    assert v["entry"] is None


def test_negative_gamma_inside_the_walls_waits():
    v = ns.build_verdict("negative", "pin", SPOT, MID, 150.0, ni.NQ)
    assert v["action"] == "WAIT"


def test_negative_gamma_broken_above_goes_long_with_no_fixed_target():
    lv = {"call_wall": 22900.0, "put_wall": 22700.0, "pin": 22800.0}
    v = ns.build_verdict("negative", "pin", SPOT, lv, 150.0, ni.NQ)
    assert v["action"] == "LONG"
    assert v["target"] is None      # continuation: trail, do not cap


def test_negative_gamma_broken_below_goes_short_with_no_fixed_target():
    lv = {"call_wall": 23300.0, "put_wall": 23100.0, "pin": 23200.0}
    v = ns.build_verdict("negative", "pin", SPOT, lv, 150.0, ni.NQ)
    assert v["action"] == "SHORT"
    assert v["target"] is None


def test_flip_zone_always_stands_down():
    v = ns.build_verdict("flip_zone", "pin", SPOT, AT_CALL, 150.0, ni.NQ)
    assert v["action"] == "STAND DOWN"
    assert v["entry"] is None


def test_unknown_regime_stands_down():
    v = ns.build_verdict("unknown", "pin", SPOT, AT_CALL, 150.0, ni.NQ)
    assert v["action"] == "STAND DOWN"


@pytest.mark.parametrize("phase", ["premarket", "opening", "flatten", "closed"])
def test_outside_the_window_the_phase_note_wins_over_unknown_regime(phase):
    """When the cash-freshness guard forces the regime to unknown outside RTH,
    the user should still get the specific "Pre-open, no signals before 08:30"
    note rather than a generic "no gamma map" — there IS a map, it is just
    yesterday's. Both mean don't trade; one explains why.
    """
    v = ns.build_verdict("unknown", phase, SPOT, AT_CALL, 150.0, ni.NQ)
    assert v["action"] == "WAIT"
    assert v["reason"] == ns.PHASE_NOTE[phase]


@pytest.mark.parametrize("phase", ["premarket", "opening", "flatten", "closed"])
def test_outside_the_trading_window_always_waits(phase):
    # Even sitting exactly on a wall with a textbook setup.
    v = ns.build_verdict("positive", phase, SPOT, AT_CALL, 150.0, ni.NQ)
    assert v["action"] == "WAIT"
    assert v["entry"] is None


@pytest.mark.parametrize("levels", [
    {}, {"call_wall": None, "put_wall": None, "pin": None},
    {"call_wall": 23020.0}, {"put_wall": 22980.0},
])
@pytest.mark.parametrize("regime", ["positive", "negative"])
def test_missing_walls_never_raise(regime, levels):
    v = ns.build_verdict(regime, "pin", SPOT, levels, 150.0, ni.NQ)
    assert v["action"] in ("LONG", "SHORT", "WAIT", "STAND DOWN")


def test_verdict_carries_no_colour():
    # Presentation belongs to the HUD; a colour here would drag tkinter into
    # the pure module's contract.
    assert "color" not in ns.build_verdict("positive", "pin", SPOT, AT_CALL, 150.0, ni.NQ)


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
    v = ns.build_verdict("positive", "pin", SPOT, lv, 150.0, ni.NQ)
    assert v["action"] == "SHORT", "fixture must stay inside the proximity band"
    sp = ns.stop_points(150.0, ni.NQ)
    band = SPOT * ns.WALL_PROXIMITY_PCT
    assert abs(v["entry"] - v["stop"]) <= band + sp


#############################################
# FLIP SELECTION — which zero crossing is the regime boundary
#############################################

def test_prefers_the_recomputed_flip():
    assert ns.pick_flip(27564.5, 28013.81) == 27564.5


def test_falls_back_to_the_stored_flip():
    """A grid with no crossing in the band yields no computed flip; the stored
    column keeps the HUD producing a regime rather than going blank.
    """
    assert ns.pick_flip(None, 28013.81) == 28013.81


def test_both_missing_is_none():
    assert ns.pick_flip(None, None) is None


def test_zero_is_not_treated_as_missing():
    # `computed or stored` would silently discard a legitimate 0.0.
    assert ns.pick_flip(0.0, 28013.81) == 0.0


#############################################
# CASH FRESHNESS — the regime is anchored to a cash index that stops ticking
#############################################

def test_cash_is_live_during_rth_with_a_fresh_snapshot():
    for phase in ("opening", "morning", "pin", "afternoon", "flatten"):
        assert ns.cash_stale_reason(phase, 40.0, 150) is None


@pytest.mark.parametrize("phase", ["premarket", "closed"])
def test_cash_is_stale_when_the_index_is_not_trading(phase):
    """$NDX cash does not tick outside 08:30-15:00 CT. Because basis is measured
    as NQ - NDX, a frozen cash print makes the regime distance collapse to
    (yesterday's close - flip) with the live NQ price cancelling out entirely —
    so the HUD would show a confident band computed from stale data.
    """
    reason = ns.cash_stale_reason(phase, 40.0, 150)
    assert reason and "closed" in reason.lower()


def test_cash_is_stale_when_the_gamma_map_has_gone_stale():
    reason = ns.cash_stale_reason("pin", 900.0, 150)
    assert reason and "900" in reason


def test_premarket_collection_window_is_caught():
    """08:00-08:30 CT is the gap that a staleness check on snapshot age ALONE
    would miss: the collector is running (snapshot fresh) but cash has not
    opened, so the regime is still anchored to yesterday's close.
    """
    assert ns.cash_stale_reason("premarket", 5.0, 150) is not None


def test_cash_staleness_tolerates_a_missing_age():
    assert ns.cash_stale_reason("pin", None, 150) is None


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
    v = ns.build_verdict("positive", "pin", SPOT, lv, 150.0, ni.NQ)
    assert not (v["action"] == "SHORT" and v["target"] > v["entry"])


def test_long_is_not_taken_when_the_pin_sits_below_the_entry():
    lv = {"call_wall": 24000.0, "put_wall": 22980.0, "pin": 22850.0}
    v = ns.build_verdict("positive", "pin", SPOT, lv, 150.0, ni.NQ)
    assert not (v["action"] == "LONG" and v["target"] < v["entry"])


def test_short_stop_is_above_the_entry_even_when_spot_overshoots_the_wall():
    """Spot can sit ABOVE the call wall and still be "at" it (the band is ~35
    points at 23,000). With a small ATR the wall-derived stop then lands BELOW
    the entry — a short that is already stopped out the moment it is taken.
    """
    lv = {"call_wall": 22980.0, "put_wall": 22000.0, "pin": 22900.0}
    v = ns.build_verdict("positive", "pin", SPOT, lv, 10.0, ni.NQ)   # -> MIN stop
    if v["action"] == "SHORT":
        assert v["stop"] > v["entry"]


def test_long_stop_is_below_the_entry_even_when_spot_undershoots_the_wall():
    lv = {"call_wall": 24000.0, "put_wall": 23020.0, "pin": 23100.0}
    v = ns.build_verdict("positive", "pin", SPOT, lv, 10.0, ni.NQ)
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
        v = ns.build_verdict(regime, "pin", SPOT, lv, atr, ni.NQ)
        if v["action"] == "SHORT":
            assert v["stop"] > v["entry"]
            if v["target"] is not None:
                assert v["target"] < v["entry"]
        elif v["action"] == "LONG":
            assert v["stop"] < v["entry"]
            if v["target"] is not None:
                assert v["target"] > v["entry"]


#############################################
# FRAME INVARIANCE — deciding in cash terms must change nothing observable
#############################################

@pytest.mark.parametrize("basis", [-250.0, -1.0, 0.0, 1.0, 120.0, 362.0, 950.0])
@pytest.mark.parametrize("offset", [-400.0, -34.0, 0.0, 34.0, 400.0])
def test_deciding_in_cash_terms_matches_deciding_in_futures_terms(basis, offset):
    """The whole justification for computing the regime and verdict in CASH
    terms and shifting only for display.

    Because basis is measured as (futures - cash), the two frames differ by a
    constant: every level moves by +basis and so does spot. Distances are
    therefore identical, and the verdict must be too — the futures price
    genuinely carries no information the cash price does not.

    If this test ever fails, the reframe stopped being a pure refactor.
    """
    cash_spot = 23000.0
    cash_levels = {"call_wall": 23000.0 + offset, "put_wall": 22800.0,
                   "pin": 22900.0}

    fut_spot = cash_spot + basis
    fut_levels = {k: v + basis for k, v in cash_levels.items()}

    cash_regime, cash_dist = classify_in(cash_spot, 22950.0)
    fut_regime, fut_dist = classify_in(fut_spot, 22950.0 + basis)
    assert cash_regime == fut_regime
    assert cash_dist == pytest.approx(fut_dist)

    cash_v = ns.build_verdict(cash_regime, "pin", cash_spot, cash_levels, 150.0, ni.NQ)
    fut_v = ns.build_verdict(fut_regime, "pin", fut_spot, fut_levels, 150.0, ni.NQ)

    assert cash_v["action"] == fut_v["action"]
    # Shifting the cash verdict for display must reproduce the futures verdict.
    shifted = ns.shift_verdict_levels(cash_v, basis)
    for field in ("entry", "stop", "target"):
        if fut_v[field] is None:
            assert shifted[field] is None
        else:
            assert shifted[field] == pytest.approx(fut_v[field])


def classify_in(spot, flip):
    return ns.classify_regime(spot, flip)


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
    v = ns.build_verdict("negative", "pin", spot, lv, 150.0, ni.NQ)
    if spot > call_wall:
        assert v["action"] != "SHORT", "shorting a break ABOVE the call wall is a fade"
    if spot < put_wall:
        assert v["action"] != "LONG", "longing a break BELOW the put wall is a fade"


#############################################
# INSTRUMENT-AGNOSTICISM (ES alongside NQ)
#############################################

def test_cash_scale_recognises_spx_as_a_cash_index():
    """The "$" prefix rule has to hold for every cash index, not just $NDX."""
    assert ns.cash_scale("$SPX", None, None) == 1.0
    assert ns.cash_scale("$SPX", 6900.0, 690.0) == 1.0


def test_spy_strike_scales_up_to_spx():
    assert ns.cash_scale("SPY", 6900.0, 690.0) == pytest.approx(10.0)
    scale = ns.cash_scale("SPY", 6900.0, 690.0)
    assert ns.to_future(690.0, scale, 25.0) == pytest.approx(6925.0)


def test_the_same_atr_sizes_differently_per_instrument():
    """The whole point of putting the clamps on the spec. A shared band would
    give ES a floor stop worth ~4x the intended risk."""
    assert ns.stop_points(10.0, ni.ES) == ni.ES.min_stop
    assert ns.stop_points(10.0, ni.NQ) == ni.NQ.min_stop
    assert ns.stop_points(10.0, ni.ES) != ns.stop_points(10.0, ni.NQ)


def test_es_stop_clamps_to_the_es_ceiling_not_nqs():
    """A big ES session range must not be allowed to run to NQ's 45-point cap —
    that is $2,250 of risk on a $50/pt contract."""
    huge = 10_000.0
    assert ns.stop_points(huge, ni.ES) == ni.ES.max_stop
    assert ns.stop_points(huge, ni.ES) < ni.NQ.max_stop


def test_stop_points_requires_a_spec():
    """Defaulting would silently apply NQ sizing to ES. Fail loudly instead."""
    with pytest.raises(TypeError):
        ns.stop_points(150.0)


def test_build_verdict_requires_a_spec():
    with pytest.raises(TypeError):
        ns.build_verdict("positive", "pin", 6900.0, {"call_wall": 6900.0}, 50.0)


def test_es_verdict_uses_the_es_stop_band():
    """End to end: an ES setup at the call wall must produce an ES-sized stop."""
    spot = 6900.0
    lv = {"call_wall": spot, "put_wall": 6800.0, "pin": 6850.0}
    v = ns.build_verdict("positive", "pin", spot, lv, 10.0, ni.ES)
    assert v["action"] == "SHORT"
    assert abs(v["stop"] - v["entry"]) == pytest.approx(ni.ES.min_stop)


# The SAME structural setup, expressed at NQ scale and at ES scale. Every
# threshold in the module is a PERCENTAGE of spot, so the verdict must not
# depend on the absolute price level.
_SCALE = 4.0   # roughly NDX / SPX


@pytest.mark.parametrize("offset_pct,expected", [
    (0.0, "flip_zone"),      # exactly on the flip
    (0.001, "flip_zone"),    # inside the +/-0.30% band
    (-0.002, "flip_zone"),
    (0.01, "positive"),      # clear of the band, above
    (-0.01, "negative"),     # clear of the band, below
])
def test_regime_is_scale_free(offset_pct, expected):
    nq_spot = 28000.0
    es_spot = nq_spot / _SCALE
    nq_regime, _ = ns.classify_regime(nq_spot, nq_spot / (1 + offset_pct))
    es_regime, _ = ns.classify_regime(es_spot, es_spot / (1 + offset_pct))
    assert nq_regime == expected
    assert es_regime == nq_regime, "regime must not depend on the price level"


def test_wall_proximity_is_scale_free():
    """`near` is also percentage-based, so "at the wall" must mean the same
    thing on a 6,900 index as on a 28,000 one."""
    nq_spot = 28000.0
    es_spot = nq_spot / _SCALE
    inside = 0.001    # within the 0.15% band
    outside = 0.005   # clearly outside it
    for pct, want in ((inside, True), (outside, False)):
        assert ns.near(nq_spot, nq_spot * (1 + pct), nq_spot) is want
        assert ns.near(es_spot, es_spot * (1 + pct), es_spot) is want


#############################################
# REWARD:RISK GATE — refuse fades that do not pay for their own stop
#############################################

def test_reward_risk_is_the_plain_ratio():
    assert ns.reward_risk(100.0, 90.0, 130.0) == pytest.approx(3.0)
    assert ns.reward_risk(100.0, 110.0, 70.0) == pytest.approx(3.0)  # short


@pytest.mark.parametrize("entry,stop,target", [
    (None, 90.0, 130.0), (100.0, None, 130.0), (100.0, 90.0, None),
    (100.0, 100.0, 130.0),        # zero risk -> no ratio, not infinity
])
def test_reward_risk_returns_none_rather_than_a_misleading_number(entry, stop, target):
    assert ns.reward_risk(entry, stop, target) is None


def _fade(pin, spot=SPOT, call=23020.0, put=22800.0, atr=150.0, spec=None):
    lv = {"call_wall": call, "put_wall": put, "pin": pin}
    return ns.build_verdict("positive", "pin", spot, lv, atr, spec or ni.NQ)


def test_a_fade_whose_pin_sits_on_top_of_spot_is_refused():
    """The measured failure mode: at the call wall with the pin ~18 points
    below spot against a ~60-point stop. That is a 0.31:1 trade, and 85% of a
    live session's NQ signals looked like this.
    """
    v = _fade(pin=SPOT - 18.0)
    assert v["action"] == "WAIT"
    assert v["entry"] is None and v["stop"] is None and v["target"] is None


def test_the_refusal_names_the_actual_ratio():
    """"Waiting" with no number reads as the HUD being coy. Seeing 0.31:1 makes
    it obvious that the setup exists but is not worth the risk."""
    v = _fade(pin=SPOT - 18.0)
    assert "%.2f:1" % v["rr"] in v["reason"]
    assert "1.5:1" in v["reason"]          # the minimum it was measured against


def test_a_fade_with_a_distant_pin_is_still_taken():
    v = _fade(pin=SPOT - 400.0)
    assert v["action"] == "SHORT"
    assert v["rr"] >= ns.MIN_REWARD_RISK


def test_the_gate_boundary_is_inclusive():
    """A setup exactly at the minimum is taken; a hair below is not. Pinning the
    boundary stops a future refactor silently turning >= into >."""
    v = _fade(pin=SPOT - 18.0)
    sp = ns.stop_points(150.0, ni.NQ)
    risk = abs(SPOT - ns._stop_above(SPOT, 23020.0, sp))
    at = _fade(pin=SPOT - risk * ns.MIN_REWARD_RISK)
    below = _fade(pin=SPOT - risk * (ns.MIN_REWARD_RISK - 0.01))
    assert at["action"] == "SHORT", "exactly at the minimum must be allowed"
    assert below["action"] == "WAIT"
    assert v["action"] == "WAIT"


def test_the_long_side_is_gated_too():
    lv = {"call_wall": 23200.0, "put_wall": 22980.0, "pin": SPOT + 18.0}
    v = ns.build_verdict("positive", "pin", SPOT, lv, 150.0, ni.NQ)
    assert v["action"] == "WAIT"
    assert "put wall" in v["reason"]

    lv["pin"] = SPOT + 400.0
    assert ns.build_verdict("positive", "pin", SPOT, lv, 150.0, ni.NQ)["action"] == "LONG"


def test_every_verdict_reports_its_reward_risk():
    """The HUD, the state export and the log all read it off the verdict rather
    than recomputing, so it must be present on taken AND refused setups."""
    taken = _fade(pin=SPOT - 400.0)
    refused = _fade(pin=SPOT - 18.0)
    assert taken["rr"] > 0 and refused["rr"] > 0
    assert taken["rr"] > refused["rr"]


def test_continuation_trades_are_not_gated():
    """A negative-gamma break is trailed, so it has no target and therefore no
    ratio. Treating "no ratio" as a failed gate would silently delete the entire
    continuation half of the strategy.
    """
    lv = {"call_wall": 23020.0, "put_wall": 22800.0, "pin": 23000.0}
    v = ns.build_verdict("negative", "pin", 23100.0, lv, 150.0, ni.NQ)
    assert v["action"] == "LONG"
    assert v["target"] is None
    assert v["rr"] is None


def test_the_gate_is_scale_free():
    """R:R is a ratio, so the same geometry must be judged identically on a
    7,400 index and a 28,000 one — unlike the stop clamps, which are in points
    and live on the Instrument spec."""
    for pin_frac, expected in ((0.0008, "WAIT"), (0.02, "SHORT")):
        nq = _fade(pin=SPOT * (1 - pin_frac), spot=SPOT,
                   call=SPOT * 1.0009, put=SPOT * 0.99, spec=ni.NQ)
        es_spot = SPOT / 4.0
        es = _fade(pin=es_spot * (1 - pin_frac), spot=es_spot,
                   call=es_spot * 1.0009, put=es_spot * 0.99,
                   atr=150.0 / 4.0, spec=ni.ES)
        assert nq["action"] == expected
        assert es["action"] == nq["action"], (
            "same geometry, different index level -> same decision")


def test_the_gate_only_ever_removes_trades():
    """It must never turn a WAIT into a trade, or flip a side. Swept across the
    whole wall range so the property holds everywhere, not at one point."""
    for i in range(60):
        spot = 22700.0 + 10.0 * i
        lv = {"call_wall": 23100.0, "put_wall": 22900.0, "pin": 23000.0}
        v = ns.build_verdict("positive", "pin", spot, lv, 150.0, ni.NQ)
        if v["action"] in ("LONG", "SHORT"):
            assert v["rr"] >= ns.MIN_REWARD_RISK
            # A taken fade always points at the pin.
            assert v["target"] == 23000.0


def test_minimum_is_a_ratio_not_a_point_count():
    """If this ever became instrument-specific it would belong on the spec.
    It is a pure ratio, so it correctly lives in the signal module."""
    assert isinstance(ns.MIN_REWARD_RISK, float)
    assert 1.0 <= ns.MIN_REWARD_RISK <= 3.0
    assert not hasattr(ni.NQ, "min_reward_risk")


# ── holidays come from the shared calendar, not a yearly-edit literal ──────

def test_holidays_are_not_a_hardcoded_2026_2027_literal():
    """`tools/` carried its own 2026-2027 frozenset, justified by a comment
    claiming an import would drag `compute`/`handlers` in. That is false for
    `shared/market_calendar`, which is deliberately import-light (measured: no
    pandas/numpy/redis/fastapi) and derives holidays ALGORITHMICALLY — so the
    literal was both unnecessary and, being a fixed two-year set, silently wrong
    from 2028 onward (2026-08-20)."""
    import datetime as _d
    # dates past the old literal's horizon must still be recognised
    assert not ns.is_trading_day(_d.date(2028, 1, 17))    # MLK 2028
    assert not ns.is_trading_day(_d.date(2029, 11, 22))   # Thanksgiving 2029
    assert not ns.is_trading_day(_d.date(2030, 12, 25))   # Christmas 2030


def test_holiday_gate_still_recognises_the_dates_the_literal_covered():
    import datetime as _d
    for d in (_d.date(2026, 1, 1), _d.date(2026, 7, 3), _d.date(2026, 11, 26),
              _d.date(2027, 5, 31), _d.date(2027, 12, 24)):
        assert not ns.is_trading_day(d), d


def test_holiday_gate_passes_a_plain_weekday_and_blocks_weekends():
    import datetime as _d
    assert ns.is_trading_day(_d.date(2026, 7, 29))        # a plain Wednesday
    assert not ns.is_trading_day(_d.date(2026, 7, 25))    # Saturday
    assert not ns.is_trading_day(_d.date(2026, 7, 26))    # Sunday
