"""Tests for the scanner-local commission helper (C1)."""
import commissions as cm


def test_round_trip_equity_two_leg_spread():
    # 2 legs x 1 qty x $0.65 x 2 (open+close) = $2.60
    assert cm.round_trip_commission(2, "SPY", 1) == 2.6


def test_round_trip_iron_condor_four_legs():
    # 4 legs x 1 x $0.65 x 2 = $5.20 (the finding's IC example)
    assert cm.round_trip_commission(4, "SPY", 1) == 5.2


def test_round_trip_accepts_leg_list():
    legs = [{"kind": "put"}, {"kind": "put"}, {"kind": "call"}, {"kind": "call"}]
    assert cm.round_trip_commission(legs, "IWM", 1) == 5.2


def test_round_trip_scales_with_qty():
    assert cm.round_trip_commission(2, "SPY", 3) == round(2 * 3 * 0.65 * 2, 4)


def test_round_trip_index_symbol():
    # $SPX is an index root; rate = index + index_exchange_fee (0.65 + 0 by toml)
    assert cm.is_index_symbol("$SPX")
    assert cm.round_trip_commission(4, "$SPX", 1) == 5.2


def test_round_trip_zero_on_bad_input():
    assert cm.round_trip_commission(0, "SPY", 1) == 0.0
    assert cm.round_trip_commission(2, "SPY", 0) == 0.0
    assert cm.round_trip_commission(None, "SPY", 1) == 0.0
