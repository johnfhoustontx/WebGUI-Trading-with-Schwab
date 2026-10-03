"""Carry a chain forward: same contracts, live price, greeks moved by the
Black-Scholes change.

Every date here is derived from TODAY (Central). The last tests run the real
``GammaEngine``, whose "today" is the wall clock, so a pinned expiration would
turn into a past one and the file would rot.
"""
import copy
import datetime as dt
import math
from zoneinfo import ZoneInfo

import pytest

import chain_carry as cc
from options_calculator import RISK_FREE_RATE, bs_delta, bs_gamma

CT = ZoneInfo("America/Chicago")
TODAY = dt.datetime.now(CT).date()
NOW = dt.datetime.combine(TODAY, dt.time(10, 0), tzinfo=CT)
NEAR_DATE = (TODAY + dt.timedelta(days=4)).isoformat()
FAR_DATE = (TODAY + dt.timedelta(days=11)).isoformat()
NEAR, FAR = f"{NEAR_DATE}:4", f"{FAR_DATE}:11"
ZERO = f"{TODAY.isoformat()}:0"                 # an expiration that is TODAY
IV = 30.0


def _c(put_call, strike, gamma, delta, iv=IV):
    return {"putCall": put_call, "strikePrice": strike, "gamma": gamma,
            "delta": delta, "volatility": iv, "openInterest": 500,
            "totalVolume": 40, "mark": 1.25}


def _chain(spot=100.0, exps=(NEAR, FAR)):
    def side(pc, sign):
        return {exp: {"95.0": [_c(pc, 95.0, 0.030, sign * 0.70)],
                      "100.0": [_c(pc, 100.0, 0.060, sign * 0.50)],
                      "105.0": [_c(pc, 105.0, 0.030, sign * 0.30)]}
                for exp in exps}
    return {"symbol": "AAPL", "underlyingPrice": spot,
            "callExpDateMap": side("CALL", 1), "putExpDateMap": side("PUT", -1)}


def _numbers(chain):
    """Every gamma and delta in a chain, with where it sits."""
    for map_key in ("callExpDateMap", "putExpDateMap"):
        for exp, strikes in chain[map_key].items():
            for strike, contracts in strikes.items():
                for c in contracts:
                    for field in ("gamma", "delta"):
                        yield (map_key, exp, strike, field), c.get(field)


#############################################
# THE PLAN'S SPECIFICATION
#############################################

def test_the_price_becomes_the_live_price():
    assert cc.carry_chain(_chain(), 101.5, age_sec=120, now=NOW)["underlyingPrice"] == 101.5


def test_the_source_chain_is_not_mutated():
    src = _chain()
    before = copy.deepcopy(src)
    cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert src == before


def test_gamma_moves_by_the_black_scholes_ratio():
    out = cc.carry_chain(_chain(), 102.0, age_sec=120, now=NOW)
    t1 = cc.years_to_expiry(NOW, NEAR_DATE)
    t0 = t1 + 120 / (365 * 24 * 3600)
    want = 0.060 * (bs_gamma(102.0, 100.0, t1, RISK_FREE_RATE, 0.30, "call")
                    / bs_gamma(100.0, 100.0, t0, RISK_FREE_RATE, 0.30, "call"))
    got = out["callExpDateMap"][NEAR]["100.0"][0]["gamma"]
    assert got == pytest.approx(want, rel=1e-9)


def test_delta_moves_by_the_black_scholes_difference():
    out = cc.carry_chain(_chain(), 102.0, age_sec=120, now=NOW)
    t1 = cc.years_to_expiry(NOW, NEAR_DATE)
    t0 = t1 + 120 / (365 * 24 * 3600)
    want = -0.50 + (bs_delta(102.0, 100.0, t1, RISK_FREE_RATE, 0.30, "put")
                    - bs_delta(100.0, 100.0, t0, RISK_FREE_RATE, 0.30, "put"))
    got = out["putExpDateMap"][NEAR]["100.0"][0]["delta"]
    assert got == pytest.approx(want, rel=1e-9)


def test_an_unmoved_price_and_no_age_changes_nothing():
    out = cc.carry_chain(_chain(), 100.0, age_sec=0, now=NOW)
    assert out["callExpDateMap"][NEAR] == _chain()["callExpDateMap"][NEAR]


def test_only_the_nearest_expiration_is_touched():
    out = cc.carry_chain(_chain(), 104.0, age_sec=120, now=NOW)
    assert out["callExpDateMap"][FAR] == _chain()["callExpDateMap"][FAR]
    assert out["callExpDateMap"][NEAR] != _chain()["callExpDateMap"][NEAR]


def test_volume_premium_inputs_and_open_interest_are_never_touched():
    out = cc.carry_chain(_chain(), 104.0, age_sec=120, now=NOW)
    c = out["callExpDateMap"][NEAR]["100.0"][0]
    assert (c["totalVolume"], c["openInterest"], c["mark"], c["volatility"]) == (40, 500, 1.25, IV)


def test_deltas_stay_inside_their_side():
    out = cc.carry_chain(_chain(), 140.0, age_sec=120, now=NOW)
    for strikes in out["callExpDateMap"][NEAR].values():
        assert 0.0 <= strikes[0]["delta"] <= 1.0
    for strikes in out["putExpDateMap"][NEAR].values():
        assert -1.0 <= strikes[0]["delta"] <= 0.0


def test_a_contract_without_usable_volatility_keeps_schwabs_greeks():
    src = _chain()
    src["callExpDateMap"][NEAR]["100.0"][0]["volatility"] = -999.0   # Schwab's sentinel
    src["putExpDateMap"][NEAR]["100.0"][0]["volatility"] = None
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["callExpDateMap"][NEAR]["100.0"][0]["gamma"] == 0.060
    assert out["putExpDateMap"][NEAR]["100.0"][0]["delta"] == -0.50


def test_a_contract_with_no_gamma_stays_at_zero_not_nan():
    src = _chain()
    src["callExpDateMap"][NEAR]["105.0"][0]["gamma"] = 0
    src["callExpDateMap"][NEAR]["95.0"][0]["gamma"] = None
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["callExpDateMap"][NEAR]["105.0"][0]["gamma"] == 0
    assert out["callExpDateMap"][NEAR]["95.0"][0]["gamma"] is None


@pytest.mark.parametrize("bad", [None, 0, -5.0, float("nan"), float("inf"), True, "101"])
def test_an_unusable_live_price_returns_the_chain_unchanged(bad):
    src = _chain()
    assert cc.carry_chain(src, bad, age_sec=120, now=NOW) is src


def test_a_chain_with_no_usable_price_of_its_own_is_returned_unchanged():
    src = _chain(spot=0)
    assert cc.carry_chain(src, 101.0, age_sec=120, now=NOW) is src


def test_the_engine_sees_the_move():
    import gamma_tool as gt
    engine = gt.GammaEngine()
    base, *_ = engine.calc_all_from_chain(_chain(), use_volume=False)
    moved, *_ = gt.GammaEngine().calc_all_from_chain(
        cc.carry_chain(_chain(), 104.0, age_sec=120, now=NOW), use_volume=False)
    assert moved["spot"] == 104.0
    assert moved["gex"][100.0]["call"] != base["gex"][100.0]["call"]


#############################################
# THE EXPIRATION ADJUSTED IS THE ONE THE ENGINE READS
#############################################

def test_a_zero_dte_expiration_is_the_one_adjusted_and_the_engine_prices_it():
    """With an expiration TODAY in the chain the engine reads that one, not the
    one four days out. The carry must land on the same key, or the engine would
    price Schwab's stale gamma at the live spot."""
    import gamma_tool as gt
    src = _chain(exps=(NEAR, ZERO, FAR))
    out = cc.carry_chain(src, 101.0, age_sec=120, now=NOW)

    assert out["callExpDateMap"][NEAR] == src["callExpDateMap"][NEAR]
    assert out["callExpDateMap"][FAR] == src["callExpDateMap"][FAR]
    carried = out["callExpDateMap"][ZERO]["100.0"][0]["gamma"]
    assert carried != 0.060

    engine = gt.GammaEngine()
    moved, *_ = engine.calc_all_from_chain(out, use_volume=False)
    assert engine._last_dte == 0
    # The engine's GEX for a strike is gamma x open interest x 100 x spot^2 x 1%.
    assert moved["gex"][100.0]["call"] == pytest.approx(
        carried * 500 * 100 * 101.0 * 101.0 * 0.01, rel=1e-12)
    stale = 0.060 * 500 * 100 * 101.0 * 101.0 * 0.01
    assert moved["gex"][100.0]["call"] != pytest.approx(stale, rel=1e-6)


def test_the_put_side_picks_its_own_nearest_expiration():
    src = _chain()
    del src["putExpDateMap"][NEAR]                 # puts only list the far one
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["putExpDateMap"][FAR] != src["putExpDateMap"][FAR]
    assert out["callExpDateMap"][FAR] == src["callExpDateMap"][FAR]


def test_today_is_the_central_date_whatever_zone_now_carries():
    """10:00 Central is already tomorrow in Tokyo. The engine's "today" is
    Central, so the expiration matched by date must be Central's too."""
    tokyo = NOW.astimezone(ZoneInfo("Asia/Tokyo"))
    assert tokyo.date() == TODAY + dt.timedelta(days=1)
    tomorrow = f"{(TODAY + dt.timedelta(days=1)).isoformat()}:9"   # a misleading DTE
    src = _chain(exps=(tomorrow, f"{TODAY.isoformat()}:5"))
    out = cc.carry_chain(src, 103.0, age_sec=120, now=tokyo)
    today_key = f"{TODAY.isoformat()}:5"
    assert out["callExpDateMap"][tomorrow] == src["callExpDateMap"][tomorrow]
    assert out["callExpDateMap"][today_key] != src["callExpDateMap"][today_key]
    assert out["underlyingPrice"] == 103.0


def test_an_expiration_the_engine_skips_is_skipped_here_too():
    """The engine never reads an expiration whose days-to-expiry is negative,
    even though its date sorts first. Same rule, same answer."""
    import gamma_tool as gt
    stale_key = f"{(TODAY - dt.timedelta(days=1)).isoformat()}:-1"
    src = _chain(exps=(stale_key, NEAR))
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["callExpDateMap"][stale_key] == src["callExpDateMap"][stale_key]
    carried = out["callExpDateMap"][NEAR]["100.0"][0]["gamma"]
    assert carried != 0.060
    moved, *_ = gt.GammaEngine().calc_all_from_chain(out, use_volume=False)
    assert moved["gex"][100.0]["call"] == pytest.approx(
        carried * 500 * 100 * 103.0 * 103.0 * 0.01, rel=1e-12)


def test_a_naive_now_is_read_as_central():
    naive = cc.carry_chain(_chain(), 102.0, age_sec=120, now=NOW.replace(tzinfo=None))
    aware = cc.carry_chain(_chain(), 102.0, age_sec=120, now=NOW)
    assert naive == aware


#############################################
# TINY TIMES, FAR STRIKES, HUGE MOVES: NEVER A NONSENSE NUMBER
#############################################

def _assert_sane(src, out, exp):
    before = dict(_numbers(src))
    for where, value in _numbers(out):
        if value is None:
            continue
        assert isinstance(value, (int, float)) and math.isfinite(value), where
        if where[-1] == "gamma":
            assert 0.0 <= value <= abs(before[where]) * cc.MAX_GAMMA_RATIO + 1e-15, where
        elif where[0] == "callExpDateMap":
            assert 0.0 <= value <= 1.0, where
        else:
            assert -1.0 <= value <= 0.0, where
    assert math.isfinite(out["underlyingPrice"]) and out["underlyingPrice"] > 0


@pytest.mark.parametrize("clock", [dt.time(8, 30), dt.time(14, 30), dt.time(14, 57),
                                   dt.time(14, 59, 50), dt.time(15, 0), dt.time(15, 19)])
@pytest.mark.parametrize("live", [0.01, 50.0, 99.0, 100.0, 100.4, 101.0, 105.0,
                                  300.0, 1e9])
@pytest.mark.parametrize("age", [0, 5, 120, 210, 86400])
def test_expiration_day_never_yields_a_non_finite_or_absurd_number(clock, live, age):
    now = dt.datetime.combine(TODAY, clock, tzinfo=CT)
    src = _chain(exps=(ZERO, FAR))
    for m, sign in (("callExpDateMap", 1), ("putExpDateMap", -1)):
        pc = "CALL" if sign == 1 else "PUT"
        src[m][ZERO]["150.0"] = [_c(pc, 150.0, 1e-9, sign * 0.001)]     # deep out
        src[m][ZERO]["5.0"] = [_c(pc, 5.0, 1e-9, sign * 0.999)]         # deep in
        src[m][ZERO]["100.5"] = [_c(pc, 100.5, 0.2, sign * 0.4, iv=4.0)]  # low vol
    out = cc.carry_chain(src, live, age_sec=age, now=now)
    _assert_sane(src, out, ZERO)


def test_a_gamma_jump_is_capped():
    """Thirty minutes to the close a strike 1% away has almost no Black-Scholes
    gamma, so a move onto it is a ratio in the thousands. Schwab's own value need
    not be that small, and the product would be an absurd GEX wall."""
    now = dt.datetime.combine(TODAY, dt.time(14, 30), tzinfo=CT)
    src = _chain(exps=(ZERO,))
    src["callExpDateMap"][ZERO]["101.0"] = [_c("CALL", 101.0, 0.004, 0.02)]
    t1 = cc.years_to_expiry(now, TODAY.isoformat())
    t0 = t1 + 120 / (365 * 24 * 3600)
    raw = (bs_gamma(101.0, 101.0, t1, RISK_FREE_RATE, 0.30, "call")
           / bs_gamma(100.0, 101.0, t0, RISK_FREE_RATE, 0.30, "call"))
    assert raw > 100 * cc.MAX_GAMMA_RATIO          # the case really is extreme
    out = cc.carry_chain(src, 101.0, age_sec=120, now=now)
    got = out["callExpDateMap"][ZERO]["101.0"][0]["gamma"]
    assert got == pytest.approx(0.004 * cc.MAX_GAMMA_RATIO, rel=1e-12)


def test_a_strike_too_far_to_model_keeps_schwabs_gamma():
    """Both Black-Scholes gammas underflow to zero. 0/0 must not reach the chain."""
    now = dt.datetime.combine(TODAY, dt.time(14, 57), tzinfo=CT)
    src = _chain(exps=(ZERO,))
    src["callExpDateMap"][ZERO]["150.0"] = [_c("CALL", 150.0, 0.0007, 0.001)]
    out = cc.carry_chain(src, 100.5, age_sec=120, now=now)
    assert out["callExpDateMap"][ZERO]["150.0"][0]["gamma"] == 0.0007


@pytest.mark.parametrize("clock", [dt.time(15, 0), dt.time(15, 10), dt.time(15, 19)])
def test_after_settlement_schwabs_greeks_stand_and_the_price_still_moves(clock):
    """Nothing is left to model once the expiration has settled. A floor under
    the time would zero every strike but the nearest on each carried minute and
    step back to Schwab's values on each fetched one."""
    now = dt.datetime.combine(TODAY, clock, tzinfo=CT)
    assert cc.years_to_expiry(now, TODAY.isoformat()) == 0.0
    src = _chain(exps=(ZERO, FAR))
    out = cc.carry_chain(src, 100.2, age_sec=180, now=now)
    assert out["underlyingPrice"] == 100.2
    assert out["callExpDateMap"] == src["callExpDateMap"]
    assert out["putExpDateMap"] == src["putExpDateMap"]


def test_seconds_before_settlement_the_numbers_are_still_sane():
    now = dt.datetime.combine(TODAY, dt.time(14, 59, 59), tzinfo=CT)
    assert 0 < cc.years_to_expiry(now, TODAY.isoformat()) < 1e-7
    src = _chain(exps=(ZERO,))
    out = cc.carry_chain(src, 100.0, age_sec=210, now=now)
    _assert_sane(src, out, ZERO)
    # At the money, time decay alone is a ratio of sqrt(211) -- capped.
    assert out["callExpDateMap"][ZERO]["100.0"][0]["gamma"] == pytest.approx(
        0.060 * cc.MAX_GAMMA_RATIO)


def test_a_model_gamma_too_small_to_trust_keeps_schwabs_gamma():
    """Black-Scholes puts the fetch-price gamma under one part in a trillion
    while Schwab's own is material. The ratio (here a rise) would be applied to
    a number the model does not explain."""
    now = dt.datetime.combine(TODAY, dt.time(14, 30), tzinfo=CT)
    t1 = cc.years_to_expiry(now, TODAY.isoformat())
    t0 = t1 + 120 / (365 * 24 * 3600)
    g0 = bs_gamma(100.0, 101.85, t0, RISK_FREE_RATE, 0.30, "call")
    g1 = bs_gamma(100.3, 101.85, t1, RISK_FREE_RATE, 0.30, "call")
    assert 0 < g0 < cc._TINY_GAMMA and g1 > g0     # the case is what it claims
    src = _chain(exps=(ZERO,))
    src["callExpDateMap"][ZERO]["101.85"] = [_c("CALL", 101.85, 0.004, 0.02)]
    out = cc.carry_chain(src, 100.3, age_sec=120, now=now)
    assert out["callExpDateMap"][ZERO]["101.85"][0]["gamma"] == 0.004


@pytest.mark.parametrize("iv", [5e-324, 1e-321, 1e-300])
def test_a_volatility_too_small_to_price_keeps_schwabs_greeks(iv):
    """5e-324 / 100 is 0.0, and 1e-321 survives the division but sigma x sqrt(T)
    rounds to zero: both are a division by zero inside Black-Scholes."""
    src = _chain()
    src["callExpDateMap"][NEAR]["100.0"][0]["volatility"] = iv
    src["callExpDateMap"][NEAR]["105.0"][0]["volatility"] = iv
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    c = out["callExpDateMap"][NEAR]["100.0"][0]
    assert (c["gamma"], c["delta"]) == (0.060, 0.50)
    c = out["callExpDateMap"][NEAR]["105.0"][0]
    assert c["gamma"] == 0.030 and math.isfinite(c["delta"])
    assert out["callExpDateMap"][NEAR]["95.0"][0]["gamma"] != 0.030   # neighbours still move


@pytest.mark.parametrize("iv", [0, -999.0, float("nan"), float("inf"), "30", True])
def test_any_unusable_volatility_keeps_schwabs_greeks(iv):
    src = _chain()
    src["callExpDateMap"][NEAR]["100.0"][0]["volatility"] = iv
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    c = out["callExpDateMap"][NEAR]["100.0"][0]
    assert (c["gamma"], c["delta"]) == (0.060, 0.50)


def test_schwabs_not_computed_sentinels_are_not_scaled():
    """Schwab writes -999 for a greek it could not compute. Scaling it, or
    clamping it into a delta, would turn a visible sentinel into a plausible
    number."""
    src = _chain()
    src["callExpDateMap"][NEAR]["100.0"][0].update(gamma=-999.0, delta=-999.0)
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    c = out["callExpDateMap"][NEAR]["100.0"][0]
    assert (c["gamma"], c["delta"]) == (-999.0, -999.0)


#############################################
# EVERY GUARD, ONE AT A TIME
#############################################

@pytest.mark.parametrize("bad", [None, [], "chain", 7])
def test_something_that_is_not_a_chain_is_returned_as_it_is(bad):
    assert cc.carry_chain(bad, 101.0, age_sec=120, now=NOW) is bad


@pytest.mark.parametrize("bad", [None, -1.0, float("nan"), float("inf"), True, "100"])
def test_any_unusable_chain_price_returns_the_chain_unchanged(bad):
    src = _chain(spot=bad)
    assert cc.carry_chain(src, 101.0, age_sec=120, now=NOW) is src


@pytest.mark.parametrize("bad", [None, -30, float("nan"), float("inf"), True, "120"])
def test_an_unusable_age_is_no_age(bad):
    assert (cc.carry_chain(_chain(), 102.0, age_sec=bad, now=NOW)
            == cc.carry_chain(_chain(), 102.0, age_sec=0, now=NOW))


def test_the_age_matters():
    aged = cc.carry_chain(_chain(), 102.0, age_sec=210, now=NOW)
    fresh = cc.carry_chain(_chain(), 102.0, age_sec=0, now=NOW)
    assert (aged["callExpDateMap"][NEAR]["100.0"][0]["gamma"]
            != fresh["callExpDateMap"][NEAR]["100.0"][0]["gamma"])


def test_a_missing_or_empty_side_still_moves_the_price_and_the_other_side():
    src = _chain()
    src["putExpDateMap"] = {}
    del src["callExpDateMap"]
    src["callExpDateMap"] = None
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["underlyingPrice"] == 103.0
    assert out["putExpDateMap"] == {} and out["callExpDateMap"] is None

    src = _chain()
    src["putExpDateMap"] = {}
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["callExpDateMap"][NEAR] != src["callExpDateMap"][NEAR]

    src = _chain()
    src["putExpDateMap"] = [NEAR]                  # not a mapping at all
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["putExpDateMap"] == [NEAR]
    assert out["callExpDateMap"][NEAR] != src["callExpDateMap"][NEAR]


@pytest.mark.parametrize("key", ["garbage", "not-a-date:3", "2026-13-45:2"])
def test_an_expiration_key_that_cannot_be_read_leaves_that_side_alone(key):
    src = _chain(exps=(key,))
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["underlyingPrice"] == 103.0
    assert out["callExpDateMap"] == src["callExpDateMap"]
    assert out["putExpDateMap"] == src["putExpDateMap"]


def test_odd_shapes_inside_an_expiration_pass_through():
    src = _chain()
    src["callExpDateMap"][NEAR]["100.0"] = [None, "x", _c("CALL", 100.0, 0.060, 0.50)]
    src["callExpDateMap"][NEAR]["95.0"] = "not a list"
    src["putExpDateMap"][NEAR] = "not strikes"
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["callExpDateMap"][NEAR]["100.0"][:2] == [None, "x"]
    assert out["callExpDateMap"][NEAR]["100.0"][2]["gamma"] != 0.060
    assert out["callExpDateMap"][NEAR]["95.0"] == "not a list"
    assert out["putExpDateMap"][NEAR] == "not strikes"


@pytest.mark.parametrize("bad", [None, 0, -100.0, float("nan"), True, "100"])
def test_a_contract_without_a_usable_strike_keeps_schwabs_greeks(bad):
    src = _chain()
    src["callExpDateMap"][NEAR]["100.0"][0]["strikePrice"] = bad
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    c = out["callExpDateMap"][NEAR]["100.0"][0]
    assert (c["gamma"], c["delta"]) == (0.060, 0.50)


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "0.5", True])
def test_a_delta_that_is_not_a_number_is_left_alone(bad):
    src = _chain()
    src["callExpDateMap"][NEAR]["100.0"][0]["delta"] = bad
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    got = out["callExpDateMap"][NEAR]["100.0"][0]
    assert got["delta"] is bad or (isinstance(bad, float) and math.isnan(bad)
                                   and math.isnan(got["delta"]))
    assert got["gamma"] != 0.060                   # the gamma still moved


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "0.06", True])
def test_a_gamma_that_is_not_a_number_is_left_alone(bad):
    src = _chain()
    src["callExpDateMap"][NEAR]["100.0"][0]["gamma"] = bad
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    got = out["callExpDateMap"][NEAR]["100.0"][0]
    assert got["gamma"] is bad or (isinstance(bad, float) and math.isnan(bad)
                                   and math.isnan(got["gamma"]))
    assert got["delta"] != 0.50                    # the delta still moved


def test_call_and_put_deltas_move_together():
    out = cc.carry_chain(_chain(), 103.0, age_sec=120, now=NOW)
    call = out["callExpDateMap"][NEAR]["100.0"][0]["delta"]
    put = out["putExpDateMap"][NEAR]["100.0"][0]["delta"]
    assert call > 0.50 and put > -0.50             # both rise with the price
    assert call - put == pytest.approx(1.0, abs=1e-9)   # they started 1.0 apart


#############################################
# HOW OFTEN THE GAMMA CAP BINDS (tools/measure_chain_carry.py reports it)
#############################################

def _cap_case():
    """Thirty minutes to the close, the price walks onto the 101 strike."""
    now = dt.datetime.combine(TODAY, dt.time(14, 30), tzinfo=CT)
    src = _chain(exps=(ZERO, FAR))
    src["callExpDateMap"][ZERO]["101.0"] = [_c("CALL", 101.0, 0.004, 0.02)]
    return src, now


def test_an_ordinary_move_caps_nothing():
    assert cc.capped_gammas(_chain(), 102.0, age_sec=120, now=NOW) == 0
    assert cc.capped_gammas(_chain(), 100.0, age_sec=0, now=NOW) == 0


def test_a_capped_contract_is_counted_once():
    src, now = _cap_case()
    assert cc.capped_gammas(src, 101.0, age_sec=120, now=now) == 1


def test_both_sides_are_counted():
    src, now = _cap_case()
    src["putExpDateMap"][ZERO]["101.0"] = [_c("PUT", 101.0, 0.004, -0.98)]
    assert cc.capped_gammas(src, 101.0, age_sec=120, now=now) == 2


@pytest.mark.parametrize("clock", [dt.time(9, 0), dt.time(14, 30), dt.time(14, 57),
                                   dt.time(14, 59, 59), dt.time(15, 5)])
@pytest.mark.parametrize("live", [50.0, 99.5, 100.0, 100.5, 101.0, 105.0, 300.0])
@pytest.mark.parametrize("age", [0, 60, 120, 210])
def test_the_count_is_the_number_of_gammas_the_carry_wrote_at_the_cap(clock, live, age):
    """Counted by the same pass that carries, so the two cannot disagree."""
    now = dt.datetime.combine(TODAY, clock, tzinfo=CT)
    src, _ = _cap_case()
    src["putExpDateMap"][ZERO]["100.5"] = [_c("PUT", 100.5, 0.2, -0.6, iv=4.0)]
    out = cc.carry_chain(src, live, age_sec=age, now=now)
    before = dict(_numbers(src))
    at_cap = sum(1 for where, value in _numbers(out)
                 if where[-1] == "gamma" and where[1] == ZERO
                 and value == before[where] * cc.MAX_GAMMA_RATIO)
    assert cc.capped_gammas(src, live, age_sec=age, now=now) == at_cap


def test_a_ratio_exactly_at_the_cap_is_not_counted(monkeypatch):
    """The cap BINDS only when it changed the number."""
    src, now = _cap_case()
    calls = iter([1.0, 10.0] * 100)
    monkeypatch.setattr(cc, "bs_gamma", lambda *a, **k: next(calls))   # g1/g0 == 10
    assert cc.capped_gammas(src, 101.0, age_sec=120, now=now) == 0
    calls = iter([1.0, 10.000001] * 100)
    assert cc.capped_gammas(src, 101.0, age_sec=120, now=now) > 0


def test_a_gamma_that_was_not_scaled_is_not_counted():
    """Schwab's sentinel, a zero and a missing gamma are left alone, so the cap
    did not bind on them whatever the model's ratio."""
    for unscaled in (-999.0, 0, None):
        src, now = _cap_case()
        src["callExpDateMap"][ZERO]["101.0"][0]["gamma"] = unscaled
        assert cc.capped_gammas(src, 101.0, age_sec=120, now=now) == 0


def test_a_capped_gamma_too_large_to_write_is_neither_written_nor_counted():
    """Ten times a number near the top of the float range is infinity. Nothing
    is written, so the cap did not bind on anything."""
    src, now = _cap_case()
    src["callExpDateMap"][ZERO]["101.0"][0]["gamma"] = 1e308
    out = cc.carry_chain(src, 101.0, age_sec=120, now=now)
    assert out["callExpDateMap"][ZERO]["101.0"][0]["gamma"] == 1e308
    assert cc.capped_gammas(src, 101.0, age_sec=120, now=now) == 0


@pytest.mark.parametrize("chain, live", [
    (None, 101.0), ([], 101.0), ("chain", 101.0),
    ("src", None), ("src", 0), ("src", float("nan")), ("src", "101"),
    ("zero-price", 101.0)])
def test_a_carry_that_does_nothing_caps_nothing(chain, live):
    src, now = _cap_case()
    if chain == "src":
        chain = src
    elif chain == "zero-price":
        chain = dict(src, underlyingPrice=0)
    assert cc.capped_gammas(chain, live, age_sec=120, now=now) == 0


def test_a_settled_expiration_caps_nothing():
    src, _ = _cap_case()
    after = dt.datetime.combine(TODAY, dt.time(15, 5), tzinfo=CT)
    assert cc.capped_gammas(src, 101.0, age_sec=120, now=after) == 0


def test_only_the_expiration_that_is_carried_is_counted():
    """The far expiration is never touched, so nothing in it can be capped."""
    src, now = _cap_case()
    src["callExpDateMap"][FAR]["101.0"] = [_c("CALL", 101.0, 0.004, 0.02)]
    assert cc.capped_gammas(src, 101.0, age_sec=120, now=now) == 1


def test_counting_does_not_change_the_carry_or_the_source():
    src, now = _cap_case()
    before = copy.deepcopy(src)
    first = cc.carry_chain(src, 101.0, age_sec=120, now=now)
    cc.capped_gammas(src, 101.0, age_sec=120, now=now)
    assert src == before
    assert cc.carry_chain(src, 101.0, age_sec=120, now=now) == first


#############################################
# A SCHWAB DELTA OF ZERO IS "MISSING" TO THE ENGINE
#############################################
# GammaEngine.calc_all_from_chain reads ``delta is None or delta == 0`` as "no
# delta" and substitutes its own Black-Scholes delta at the live price. A carry
# that writes ``0 + (d1 - d0)`` hands it a small non-zero number instead, which
# it then USES: measured on an at-the-money call, the strike's DEX fell 14 times
# on an up-move and not on a down-move.

def _engine_at(monkeypatch, now):
    """The real engine with its wall clock pinned, so two runs price the same T."""
    import gamma_tool as gt

    class _Pinned(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    monkeypatch.setattr(gt, "datetime", _Pinned)
    return gt


def _dex(gt, chain):
    _gex, _charm, dex, _vanna = gt.GammaEngine().calc_all_from_chain(
        chain, use_volume=False)
    return dex["gex"]


@pytest.mark.parametrize("live", [103.0, 97.0])               # an up-move, a down-move
@pytest.mark.parametrize("side, pc", [("callExpDateMap", "call"),
                                      ("putExpDateMap", "put")])
def test_a_zero_delta_keeps_the_engines_own_fallback(monkeypatch, live, side, pc):
    gt = _engine_at(monkeypatch, NOW)
    src = _chain()
    src[side][NEAR]["100.0"][0]["delta"] = 0
    carried = cc.carry_chain(src, live, age_sec=120, now=NOW)

    assert carried[side][NEAR]["100.0"][0]["delta"] == 0      # left for the engine
    assert carried[side][NEAR]["100.0"][0]["gamma"] != 0.060  # the gamma still moved

    # The same chain, never carried, simply read at the live price.
    never_carried = dict(src, underlyingPrice=live)
    got = _dex(gt, carried)[100.0][pc]
    want = _dex(gt, never_carried)[100.0][pc]
    assert got == want
    assert want != 0                                           # the fallback did apply


@pytest.mark.parametrize("zero", [0, 0.0, -0.0])
def test_every_spelling_of_zero_is_left_alone(zero):
    src = _chain()
    src["callExpDateMap"][NEAR]["100.0"][0]["delta"] = zero
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    assert out["callExpDateMap"][NEAR]["100.0"][0]["delta"] == 0


def test_a_tiny_real_delta_is_still_moved():
    """Only an exact zero is "missing". A far wing's 0.001 is a reading."""
    src = _chain()
    src["callExpDateMap"][NEAR]["105.0"][0]["delta"] = 0.001
    out = cc.carry_chain(src, 104.0, age_sec=120, now=NOW)
    assert out["callExpDateMap"][NEAR]["105.0"][0]["delta"] > 0.001


#############################################
# THE STRIKE: THE CONTRACT'S OWN, ELSE THE MAP KEY THE ENGINE USES
#############################################

def test_a_contract_with_no_strike_field_is_carried_at_its_map_key():
    src = _chain()
    for m in ("callExpDateMap", "putExpDateMap"):
        for contracts in src[m][NEAR].values():
            del contracts[0]["strikePrice"]
    out = cc.carry_chain(src, 102.0, age_sec=120, now=NOW)
    assert out == _strip_strikes(cc.carry_chain(_chain(), 102.0, age_sec=120, now=NOW))
    assert out["callExpDateMap"][NEAR]["100.0"][0]["gamma"] != 0.060
    assert "strikePrice" not in out["callExpDateMap"][NEAR]["100.0"][0]


def _strip_strikes(chain):
    out = copy.deepcopy(chain)
    for m in ("callExpDateMap", "putExpDateMap"):
        for contracts in out[m][NEAR].values():
            contracts[0].pop("strikePrice", None)
    return out


@pytest.mark.parametrize("key", ["abc", "", "nan", "inf", "0", "-100.0"])
def test_no_strike_field_and_a_map_key_that_is_not_a_strike_keeps_schwabs_greeks(key):
    src = _chain()
    contract = _c("CALL", 100.0, 0.060, 0.50)
    del contract["strikePrice"]
    src["callExpDateMap"][NEAR] = {key: [contract]}
    out = cc.carry_chain(src, 103.0, age_sec=120, now=NOW)
    c = out["callExpDateMap"][NEAR][key][0]
    assert (c["gamma"], c["delta"]) == (0.060, 0.50)
    assert out["underlyingPrice"] == 103.0


def test_the_contracts_own_strike_wins_over_the_map_key():
    src = _chain()
    src["callExpDateMap"][NEAR] = {"999.0": [_c("CALL", 100.0, 0.060, 0.50)]}
    out = cc.carry_chain(src, 102.0, age_sec=120, now=NOW)
    want = cc.carry_chain(_chain(), 102.0, age_sec=120, now=NOW)
    assert (out["callExpDateMap"][NEAR]["999.0"][0]["gamma"]
            == want["callExpDateMap"][NEAR]["100.0"][0]["gamma"])


def test_a_capped_contract_with_no_strike_field_is_counted():
    src, now = _cap_case()
    del src["callExpDateMap"][ZERO]["101.0"][0]["strikePrice"]
    assert cc.capped_gammas(src, 101.0, age_sec=120, now=now) == 1


#############################################
# THE CAP IS A SETTING (config/marketdata.toml [collection] max_gamma_ratio)
#############################################

@pytest.mark.parametrize("ratio", [1.0, 3.0, 25, 1000.0])
def test_the_cap_is_whatever_it_is_given(ratio):
    src, now = _cap_case()                         # the raw ratio is in the thousands
    out = cc.carry_chain(src, 101.0, age_sec=120, now=now, max_ratio=ratio)
    assert out["callExpDateMap"][ZERO]["101.0"][0]["gamma"] == pytest.approx(
        0.004 * ratio, rel=1e-12)
    assert cc.capped_gammas(src, 101.0, age_sec=120, now=now, max_ratio=ratio) == 1


def test_a_cap_above_the_raw_ratio_binds_on_nothing():
    src, now = _cap_case()
    assert cc.capped_gammas(src, 101.0, age_sec=120, now=now, max_ratio=1e9) == 0
    out = cc.carry_chain(src, 101.0, age_sec=120, now=now, max_ratio=1e9)
    assert out["callExpDateMap"][ZERO]["101.0"][0]["gamma"] > 0.004 * 100


@pytest.mark.parametrize("unusable", [None, 0, 0.5, -3, float("nan"), float("inf"),
                                      "10", True])
def test_an_unusable_cap_is_the_built_in_one(unusable):
    """Below 1 a "cap" would shrink every gamma that should have stood still."""
    src, now = _cap_case()
    assert (cc.carry_chain(src, 101.0, age_sec=120, now=now, max_ratio=unusable)
            == cc.carry_chain(src, 101.0, age_sec=120, now=now))
    assert cc.capped_gammas(src, 101.0, age_sec=120, now=now, max_ratio=unusable) == 1


def test_the_cap_given_does_not_touch_a_move_under_it():
    """Four days out, a 2% move changes no gamma by as much as five times."""
    assert cc.capped_gammas(_chain(), 102.0, age_sec=120, now=NOW, max_ratio=5.0) == 0
    assert (cc.carry_chain(_chain(), 102.0, age_sec=120, now=NOW, max_ratio=5.0)
            == cc.carry_chain(_chain(), 102.0, age_sec=120, now=NOW))
