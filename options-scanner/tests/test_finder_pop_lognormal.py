"""The Strategy Finder's probability of profit (audit AC-11).

It was a zero-drift NORMAL distribution of the stock price, sized
``spot * iv * sqrt(dte / 365)`` with a floor of half a day:

* a normal distribution puts mass on prices a stock cannot reach (below zero)
  and has no skew, so over a long horizon it overstates a short put. Two years
  out at 70% volatility, 20% out of the money, it read 66.0% where the lognormal
  figure is 54.8%. (Inside two months the two agree within a point.)
* a same-day structure got twelve hours of movement whatever the clock said.

It is now lognormal, centred on the forward, over the time actually left to the
row's own expiry.
"""
import datetime as dt
import math
from zoneinfo import ZoneInfo

import pytest

import options_calculator as oc
import strategy_scanner as ss


def _leg(kind, side, strike, mark, expiration="2030-01-18", **kw):
    g = {"delta": 0.5, "theta": -0.02, "vega": 0.1, "gamma": 0.01, "iv": 30.0}
    g.update(kw)
    return {"kind": kind, "side": side, "strike": strike, "expiration": expiration,
            "qty": 1, "mark": mark, **g}


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _prob_above(spot, level, vol, years, q=0.0):
    """P(S_T > level) under the lognormal the pricing model uses."""
    d2 = ((math.log(spot / level) + (oc.RISK_FREE_RATE - q - 0.5 * vol * vol) * years)
          / (vol * math.sqrt(years)))
    return 100.0 * _phi(d2)


def test_a_short_put_matches_the_lognormal_closed_form():
    spot, strike, mark, vol, years = 100.0, 90.0, 9.0, 0.40, 2.0
    pop = ss.pop_from_payoff([_leg("put", "short", strike, mark)], spot, vol,
                             dte=730, years=years)
    assert pop == pytest.approx(_prob_above(spot, strike - mark, vol, years), abs=0.3)


def test_a_long_call_matches_the_lognormal_closed_form():
    spot, strike, mark, vol, years = 100.0, 105.0, 4.0, 0.30, 0.25
    pop = ss.pop_from_payoff([_leg("call", "long", strike, mark)], spot, vol,
                             dte=91, years=years)
    assert pop == pytest.approx(_prob_above(spot, strike + mark, vol, years), abs=0.3)


def test_two_years_out_the_normal_model_overstated_the_short_put():
    """Measured: 66.0 under the old model against 54.8, at 70% volatility."""
    spot, strike, vol, years = 100.0, 80.0, 0.70, 2.0
    mark = oc.bs_price(spot, strike, years, oc.RISK_FREE_RATE, vol, "put")
    sigma = spot * vol * math.sqrt(years)
    old_normal = 100.0 * (1 - _phi(((strike - mark) - spot) / sigma))
    pop = ss.pop_from_payoff([_leg("put", "short", strike, mark)], spot, vol,
                             dte=730, years=years)
    assert old_normal == pytest.approx(66.0, abs=0.2)
    assert pop == pytest.approx(54.8, abs=0.2)


def test_inside_two_months_the_two_models_agree_within_a_point():
    """So the gates tuned on the old figure barely move where most rows are."""
    spot, strike, vol, years = 100.0, 94.0, 0.30, 35 / 365
    mark = oc.bs_price(spot, strike, years, oc.RISK_FREE_RATE, vol, "put")
    sigma = spot * vol * math.sqrt(years)
    old_normal = 100.0 * (1 - _phi(((strike - mark) - spot) / sigma))
    pop = ss.pop_from_payoff([_leg("put", "short", strike, mark)], spot, vol,
                             dte=35, years=years)
    assert abs(pop - old_normal) < 1.0


def test_no_probability_sits_below_a_price_of_zero():
    """A long put struck far below the money, two years out at high volatility:
    under the normal model a large share of the mass was below zero, all of it
    counted as profit."""
    spot, strike, mark, vol, years = 100.0, 20.0, 0.5, 0.90, 2.0
    pop = ss.pop_from_payoff([_leg("put", "long", strike, mark)], spot, vol,
                             dte=730, years=years)
    assert pop == pytest.approx(100.0 - _prob_above(spot, strike - mark, vol, years),
                                abs=0.3)


def test_a_dividend_yield_lowers_the_forward():
    spot, strike, mark, vol, years = 100.0, 90.0, 9.0, 0.40, 2.0
    plain = ss.pop_from_payoff([_leg("put", "short", strike, mark)], spot, vol,
                               dte=730, years=years)
    paying = ss.pop_from_payoff([_leg("put", "short", strike, mark, div_yield=0.04)],
                                spot, vol, dte=730, years=years)
    assert paying < plain
    assert paying == pytest.approx(
        _prob_above(spot, strike - mark, vol, years, q=0.04), abs=0.3)


# --- the time actually left ---------------------------------------------------

CT = ZoneInfo("America/Chicago")


def test_years_to_expiry_reads_the_clock_on_expiration_day():
    morning = dt.datetime(2026, 10, 9, 10, 0, tzinfo=CT)       # 5 hours to 15:00 CT
    late = dt.datetime(2026, 10, 9, 14, 30, tzinfo=CT)
    assert ss._years_to_expiry("2026-10-09", morning) == pytest.approx(5 / 8760)
    assert ss._years_to_expiry("2026-10-09", late) == pytest.approx(0.5 / 8760)


def test_years_to_expiry_is_none_for_an_unreadable_date():
    assert ss._years_to_expiry("soon", dt.datetime(2026, 10, 9, 10, 0, tzinfo=CT)) is None
    assert ss._years_to_expiry(None, dt.datetime(2026, 10, 9, 10, 0, tzinfo=CT)) is None


def test_a_same_day_short_put_is_safer_with_less_time_left():
    """It read the same at 09:00 and at 14:30: twelve hours of movement."""
    legs = [_leg("put", "short", 99.0, 0.20)]
    morning = ss.pop_from_payoff(legs, 100.0, 0.30, dte=0, years=6 / 8760)
    late = ss.pop_from_payoff(legs, 100.0, 0.30, dte=0, years=0.5 / 8760)
    assert late > morning
    assert late > 99.0


def test_an_expired_row_is_decided_by_where_the_stock_is():
    legs = [_leg("put", "short", 99.0, 0.20)]
    assert ss.pop_from_payoff(legs, 100.0, 0.30, dte=0, years=0.0) == 100.0
    assert ss.pop_from_payoff(legs, 95.0, 0.30, dte=0, years=0.0) == 0.0


def test_whole_days_still_work_when_no_clock_time_is_given():
    legs = [_leg("put", "short", 90.0, 1.0)]
    assert ss.pop_from_payoff(legs, 100.0, 0.30, dte=30) == pytest.approx(
        ss.pop_from_payoff(legs, 100.0, 0.30, dte=30, years=30 / 365), abs=1e-9)


def test_an_assembled_row_uses_its_own_expiry_and_the_clock(monkeypatch):
    """``_assemble`` passed whole days, so every same-day row took the floor."""
    seen = {}
    real = ss.pop_from_payoff

    def spy(legs, spot, atm_iv, dte, now=None, years=None):
        seen["years"] = years
        return real(legs, spot, atm_iv, dte, now=now, years=years)

    monkeypatch.setattr(ss, "pop_from_payoff", spy)
    monkeypatch.setattr(ss, "_years_to_expiry", lambda exp, now=None: 3 / 8760)
    ss._assemble("SHORT_PUT", "NAKED", "Short put", "bullish",
                 [_leg("put", "short", 99.0, 0.20)], "XYZ", 100.0, 0.30)
    assert seen["years"] == 3 / 8760
