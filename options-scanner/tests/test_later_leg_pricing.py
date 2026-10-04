"""How the Strategy Finder values a LATER-expiring leg at the front expiry.

Audit AC-07 (2026-10-03): a calendar's or diagonal's back leg was priced with
Black-Scholes at a ZERO dividend yield, and at the chain's per-contract
``volatility``. Two things are wrong with that, the second found by measuring
real chains while fixing the first:

* a dividend lowers the forward, so q = 0 over-prices the back call and
  under-prices the back put - by the whole dividend over a long gap;
* Schwab's per-contract ``volatility`` does not reproduce that contract's own
  mark under this app's model (measured 2026-10-03 on seven names: the ratio of
  Schwab's figure to the mark-implied one ran 0.71 to 1.08), so the model's
  value for the leg TODAY disagreed with the price the position is entered at.

The fix prices a later leg with the chain's own ``dividendYield`` and a
volatility calibrated to that leg's own mark, so the model reproduces the entry
price exactly; the chain IV is the fallback when a mark cannot be inverted.

The reference below is an independent Black-Scholes-Merton written here with
``math.erf`` - it shares no code with ``options_calculator``.
"""
import datetime as dt
import math

import pytest

import options_calculator as oc
import strategy_scanner as ssn

R = oc.RISK_FREE_RATE
NOW = dt.datetime(2026, 6, 1, 10, 0)           # naive = Central, the project rule
FRONT = dt.date(2026, 7, 1)
BACK = dt.date(2027, 6, 1)


def _N(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bsm(S, K, T, r, q, sigma, kind):
    """Reference Black-Scholes-Merton price with a continuous yield ``q``."""
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if kind == "call":
        return S * math.exp(-q * T) * _N(d1) - K * math.exp(-r * T) * _N(d2)
    return K * math.exp(-r * T) * _N(-d2) - S * math.exp(-q * T) * _N(-d1)


def _years(to_date):
    return oc.expiry_time_to_years(NOW, to_date)


# ── the pricing core takes a dividend yield ──────────────────────────────────

@pytest.mark.parametrize("q", [0.0, 0.012, 0.06])
@pytest.mark.parametrize("K", [80.0, 100.0, 125.0])
def test_bs_price_with_a_yield_matches_the_reference(q, K):
    for kind in ("call", "put"):
        assert oc.bs_price(100.0, K, 0.75, R, 0.28, kind, q=q) == pytest.approx(
            bsm(100.0, K, 0.75, R, q, 0.28, kind), abs=1e-10)


@pytest.mark.parametrize("q", [0.012, 0.06])
def test_put_call_parity_holds_with_a_yield(q):
    S, K, T = 100.0, 105.0, 1.0
    c = oc.bs_price(S, K, T, R, 0.3, "call", q=q)
    p = oc.bs_price(S, K, T, R, 0.3, "put", q=q)
    assert c - p == pytest.approx(S * math.exp(-q * T) - K * math.exp(-R * T), abs=1e-10)


def test_the_default_yield_is_zero_and_changes_nothing():
    assert oc.bs_price(100.0, 105.0, 0.5, R, 0.3, "call") == \
        oc.bs_price(100.0, 105.0, 0.5, R, 0.3, "call", q=0.0)
    assert oc.implied_vol(6.0, 100.0, 105.0, 0.5, R, "call") == \
        oc.implied_vol(6.0, 100.0, 105.0, 0.5, R, "call", q=0.0)


@pytest.mark.parametrize("kind", ["call", "put"])
def test_implied_vol_round_trips_through_a_yield(kind):
    price = bsm(100.0, 100.0, 1.0, R, 0.04, 0.27, kind)
    assert oc.implied_vol(price, 100.0, 100.0, 1.0, R, kind, q=0.04) == \
        pytest.approx(0.27, abs=1e-6)


# ── the chain's yield reaches the legs ───────────────────────────────────────

def _chain(dividend_yield, strikes=(95.0, 100.0, 105.0)):
    def side():
        return {f"{exp.isoformat()}:{dte}": {
            f"{k:.1f}": [{"delta": 0.5, "mark": 5.0, "bid": 4.9, "ask": 5.1,
                          "volatility": 25.0, "totalVolume": 10, "openInterest": 10}]
            for k in strikes} for exp, dte in ((FRONT, 30), (BACK, 365))}
    out = {"callExpDateMap": side(), "putExpDateMap": side(), "underlyingPrice": 100.0}
    if dividend_yield is not ...:
        out["dividendYield"] = dividend_yield
    return out


def test_extract_options_stamps_the_chains_yield_as_a_fraction():
    opts = ssn.extract_options(_chain(2.512), "call", 0, 400)
    for exp in opts.values():
        for leg in exp["strikes"].values():
            assert leg["div_yield"] == pytest.approx(0.02512)


@pytest.mark.parametrize("bad", [..., None, 0, -1.5, float("nan"), float("inf"), "n/a", True])
def test_an_unusable_chain_yield_is_zero(bad):
    opts = ssn.extract_options(_chain(bad), "call", 0, 400)
    leg = next(iter(next(iter(opts.values()))["strikes"].values()))
    assert leg["div_yield"] == 0.0


def test_a_leg_built_from_the_chain_carries_the_yield():
    opts = ssn.extract_options(_chain(6.163), "put", 0, 400)
    exp, data = next(iter(opts.items()))
    leg = ssn._leg_from(data["strikes"][100.0], "put", "long", exp)
    assert leg["div_yield"] == pytest.approx(0.06163)


# ── a calendar, valued ───────────────────────────────────────────────────────

def _leg(kind, side, strike, expiration, mark, iv_pct, div_yield=None):
    leg = {"kind": kind, "side": side, "strike": strike, "qty": 1,
           "expiration": expiration.isoformat(), "mark": mark, "iv": iv_pct,
           "delta": 0.5, "theta": 0.0, "vega": 0.0, "gamma": 0.0}
    if div_yield is not None:
        leg["div_yield"] = div_yield
    return leg


def _calendar(kind, *, q, true_vol, chain_iv_pct, spot=100.0, strike=100.0):
    """A long calendar whose marks are what a market with yield ``q`` and
    volatility ``true_vol`` would quote, and whose ``iv`` field is whatever the
    data feed happens to say."""
    front = bsm(spot, strike, _years(FRONT), R, q, true_vol, kind)
    back = bsm(spot, strike, _years(BACK), R, q, true_vol, kind)
    return [_leg(kind, "short", strike, FRONT, front, chain_iv_pct, q),
            _leg(kind, "long", strike, BACK, back, chain_iv_pct, q)], back - front


def _true_peak(kind, q, true_vol, debit, strike=100.0):
    """The best the calendar can do at the front expiry in that same market."""
    gap = (BACK - FRONT).days / 365.0
    best = -1e9
    for i in range(1, 4001):
        S = 200.0 * i / 4000
        short = max(S - strike, 0.0) if kind == "call" else max(strike - S, 0.0)
        best = max(best, bsm(S, strike, gap, R, q, true_vol, kind) - short - debit)
    return best * 100.0


@pytest.mark.parametrize("kind", ["call", "put"])
def test_a_calendar_on_a_dividend_payer_is_valued_at_what_the_market_implies(kind):
    """The audit's case: a 30-day / one-year calendar on a 1.2% yielder. Priced
    at q = 0 its max profit was 47% too high for calls and 28% too low for puts."""
    legs, debit = _calendar(kind, q=0.012, true_vol=0.25, chain_iv_pct=25.0)
    m = ssn.payoff_metrics(legs, 100.0, "XYZ", now=NOW)
    truth = _true_peak(kind, 0.012, 0.25, debit) - m["commission"]
    assert m["max_profit"] == pytest.approx(truth, rel=0.01)


@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("feed_ratio", [0.75, 1.08])
def test_a_feed_volatility_that_disagrees_with_the_mark_does_not_move_the_value(kind, feed_ratio):
    """Measured on real chains: the per-contract ``volatility`` ran 0.71 to 1.08
    of the figure the contract's own mark implies. The valuation follows the
    MARK - the price the position is actually entered at."""
    legs, debit = _calendar(kind, q=0.025, true_vol=0.25,
                            chain_iv_pct=25.0 * feed_ratio)
    m = ssn.payoff_metrics(legs, 100.0, "XYZ", now=NOW)
    truth = _true_peak(kind, 0.025, 0.25, debit) - m["commission"]
    assert m["max_profit"] == pytest.approx(truth, rel=0.01)


def test_the_model_reproduces_the_back_legs_mark_at_entry():
    """Valued at today's spot with no time passed, a later leg is worth its
    mark - which is what makes entry cost and exit value one model."""
    legs, _ = _calendar("call", q=0.03, true_vol=0.3, chain_iv_pct=21.0)
    back = legs[1]
    sigmas = ssn.later_leg_vols(legs, 100.0, back["expiration"], now=NOW)
    # ``front`` = the back leg's own expiry leaves no later leg to calibrate...
    assert sigmas == [None, None]
    sigmas = ssn.later_leg_vols(legs, 100.0, FRONT.isoformat(), now=NOW)
    assert sigmas[0] is None                       # the front leg is intrinsic
    assert sigmas[1] == pytest.approx(0.30, abs=1e-4)


def test_a_leg_with_no_yield_stamp_is_calibrated_at_zero_yield():
    # A leg set built outside the chain parser (the Calculator's rating path).
    front = bsm(100.0, 100.0, _years(FRONT), R, 0.0, 0.25, "call")
    back = bsm(100.0, 100.0, _years(BACK), R, 0.0, 0.25, "call")
    legs = [_leg("call", "short", 100.0, FRONT, front, 19.0),
            _leg("call", "long", 100.0, BACK, back, 19.0)]
    m = ssn.payoff_metrics(legs, 100.0, "XYZ", now=NOW)
    truth = _true_peak("call", 0.0, 0.25, back - front) - m["commission"]
    assert m["max_profit"] == pytest.approx(truth, rel=0.01)


def test_a_mark_that_cannot_be_inverted_falls_back_to_the_chain_volatility():
    # A back call marked BELOW what any volatility prices it at: there is no
    # implied volatility, so the leg is priced at the chain's figure as before.
    legs, _ = _calendar("call", q=0.0, true_vol=0.25, chain_iv_pct=25.0,
                        spot=100.0, strike=60.0)
    legs[1]["mark"] = 30.0                         # under the 40.00 intrinsic
    assert ssn.later_leg_vols(legs, 100.0, FRONT.isoformat(), now=NOW) == [None, None]
    m = ssn.payoff_metrics(legs, 100.0, "XYZ", now=NOW)
    assert math.isfinite(m["max_loss"])


def test_an_unusable_chain_volatility_still_raises_when_there_is_no_calibration():
    legs, _ = _calendar("call", q=0.0, true_vol=0.25, chain_iv_pct=-999.0,
                        spot=100.0, strike=60.0)
    legs[1]["mark"] = 30.0
    with pytest.raises(ValueError):
        ssn.payoff_metrics(legs, 100.0, "XYZ", now=NOW)


def test_the_three_valuations_agree_on_one_model():
    """``payoff_metrics``, ``payoff_curve`` and ``pop_from_payoff`` must value a
    later leg the same way, or the shape, the probability and the numbers on
    one row describe three different positions."""
    legs, debit = _calendar("call", q=0.03, true_vol=0.25, chain_iv_pct=18.0)
    curve = ssn.payoff_curve(legs, 100.0, 0.25, 30, n=41, now=NOW)
    peak = max(p[1] for p in curve)
    m = ssn.payoff_metrics(legs, 100.0, "XYZ", now=NOW)
    assert peak == pytest.approx(m["max_profit"] + m["commission"], rel=0.02)
    assert ssn.pop_from_payoff(legs, 100.0, 0.25, 30, now=NOW) > 0
