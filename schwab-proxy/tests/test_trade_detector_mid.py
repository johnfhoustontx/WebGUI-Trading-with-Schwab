"""trade_detector._mid through shared.quotes (2026-11-28).

A 0 x 0 quote used to read as a mid of 0.00 - a debit leg "worth nothing" that
could fire a false target - and a bid with no offer as half the bid.
"""
import pytest

import trade_detector as td

NAN, INF = float("nan"), float("inf")


def test_a_normal_quote():
    assert td._mid({"bid": 1.0, "ask": 1.1}) == pytest.approx(1.05)


def test_a_zero_bid_under_an_offer_is_still_a_market():
    assert td._mid({"bid": 0.0, "ask": 0.05}) == pytest.approx(0.025)


@pytest.mark.parametrize("leg", [
    {"bid": 0.0, "ask": 0.0},
    {"bid": 1.0, "ask": 0.0},
    {"bid": 1.2, "ask": 1.0},
    {"bid": NAN, "ask": 1.1},
    {"bid": 1.0, "ask": INF},
    {"bid": -0.05, "ask": 1.1},
])
def test_no_market_is_none(leg):
    assert td._mid(leg) is None
