"""The Calculator's generic summary must not print a finite MAX RISK for a
position whose risk is not finite, or a small one for a loss that keeps growing
below the grid (audit AC-05).

``calc_summary_generic`` reads max loss off a price grid from 0.5x to 1.5x spot.
For anything short more calls than it is long, the loss has no upper bound, so
the grid's edge is not a risk figure; and for anything net short puts the loss
keeps growing all the way to zero, well below 0.5x spot.

Expected figures are worked from the expiry payoff by hand. No leg carries an
``expiry``, so every leg is valued at intrinsic.
"""
import pytest

import options_calculator as oc


def _leg(kind, side, strike, premium, qty=1):
    return {"option_type": kind, "side": side, "strike": strike,
            "premium": premium, "qty": qty}


def _summary(legs, spot=100.0):
    return oc.calc_summary_generic(legs, spot, iv=0.25, T=30 / 365)


# ── unbounded above ──────────────────────────────────────────────────────────

def test_short_straddle_has_unlimited_risk():
    # The audit's example: this read a finite dollar figure, the loss at 1.5x spot.
    s = _summary([_leg("call", "short", 100, 3.0), _leg("put", "short", 100, 3.0)])
    assert s["max_loss"] == oc.UNLIMITED


def test_naked_short_call_has_unlimited_risk():
    assert _summary([_leg("call", "short", 105, 1.5)])["max_loss"] == oc.UNLIMITED


def test_short_strangle_has_unlimited_risk():
    s = _summary([_leg("call", "short", 110, 1.0), _leg("put", "short", 90, 1.0)])
    assert s["max_loss"] == oc.UNLIMITED


def test_call_ratio_spread_short_more_calls_than_long_has_unlimited_risk():
    s = _summary([_leg("call", "long", 100, 4.0), _leg("call", "short", 110, 1.5, qty=2)])
    assert s["max_loss"] == oc.UNLIMITED


def test_unlimited_risk_reports_no_return_on_risk():
    s = _summary([_leg("call", "short", 105, 1.5)])
    assert s["return_on_risk"] == 0.0


def test_a_call_credit_spread_is_still_capped_at_its_width():
    # short 105 / long 110 for a 1.00 credit: (5.00 - 1.00) x 100.
    s = _summary([_leg("call", "short", 105, 1.5), _leg("call", "long", 110, 0.5)])
    assert s["max_loss"] == pytest.approx(400.0)


def test_a_long_call_risks_only_its_premium():
    assert _summary([_leg("call", "long", 100, 3.0)])["max_loss"] == pytest.approx(300.0)


def test_a_covered_call_is_bounded_because_the_shares_cover_the_call():
    # Long 100 shares at 100.00, short the 105 call for 2.00: the stock can go
    # to zero, so the worst case is the 100.00 paid less the 2.00 collected.
    legs = [{"option_type": oc.STOCK_KIND, "side": "long", "strike": None,
             "premium": 100.0, "qty": 1},
            _leg("call", "short", 105, 2.0)]
    assert _summary(legs)["max_loss"] == pytest.approx(9800.0)


def test_shares_cover_only_as_many_calls_as_there_are_lots():
    legs = [{"option_type": oc.STOCK_KIND, "side": "long", "strike": None,
             "premium": 100.0, "qty": 1},
            _leg("call", "short", 105, 2.0, qty=2)]
    assert _summary(legs)["max_loss"] == oc.UNLIMITED


# ── bounded, but below the old grid ──────────────────────────────────────────

def test_a_short_put_risks_its_strike_less_the_premium():
    # Assigned at 100 with the stock at zero: (100.00 - 2.00) x 100. The old
    # grid stopped at 50, where the loss is only 4,800.
    assert _summary([_leg("put", "short", 100, 2.0)])["max_loss"] == pytest.approx(9800.0)


def test_a_far_out_of_the_money_short_put_does_not_read_as_riskless():
    # A 40 put on a 100 stock: the old grid (50 to 150) never reached the strike
    # and reported a max loss of zero.
    s = _summary([_leg("put", "short", 40, 0.10)])
    assert s["max_loss"] == pytest.approx(3990.0)


def test_a_put_ratio_spread_short_more_puts_than_long_is_scanned_to_zero():
    # Long one 100 put at 4.00, short two 90 puts at 1.50: a 1.00 debit. At zero
    # the long put is worth 100 and the two shorts cost 180: -80 - 1 = -81.
    s = _summary([_leg("put", "long", 100, 4.0), _leg("put", "short", 90, 1.5, qty=2)])
    assert s["max_loss"] == pytest.approx(8100.0)


def test_a_put_credit_spread_is_still_capped_at_its_width():
    s = _summary([_leg("put", "short", 95, 1.5), _leg("put", "long", 90, 0.5)])
    assert s["max_loss"] == pytest.approx(400.0)


def test_an_iron_condor_is_still_capped_at_its_wider_wing():
    s = _summary([_leg("put", "long", 85, 0.3), _leg("put", "short", 90, 1.0),
                  _leg("call", "short", 110, 1.0), _leg("call", "long", 120, 0.2)])
    # credit 1.50; the call wing is 10 wide.
    assert s["max_loss"] == pytest.approx(850.0)


def test_breakevens_of_a_put_credit_spread_are_unchanged_by_the_wider_scan():
    s = _summary([_leg("put", "short", 95, 1.5), _leg("put", "long", 90, 0.5)])
    assert s["breakevens"] == [pytest.approx(94.0, abs=0.01)]
