"""Paper-engine realized P&L is net of lifecycle option commission.

A managed close (BUY_TO_CLOSE) pays commission on the open AND the close
(round-trip); an OTM expiration pays only the opening commission. This makes the
driver's performance scorecard (and the manual paper account) reflect net-of-fees
realized P&L. See the 2026-07-01 calc-accuracy remediation.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # options-scanner

import paper_engine as pe


def _vert():
    return {"symbol": "SPY", "short_strike": 100, "long_strike": 98, "call_short": None}


def _ic():
    return {"symbol": "SPY", "short_strike": 100, "long_strike": 98,
            "call_short": 110, "call_long": 112}


def _csp():
    """A cash-secured short put — ONE leg, the only single-leg structure here."""
    return {"symbol": "SPY", "strategy": "SHORT_PUT", "short_strike": 100,
            "long_strike": None, "call_short": None, "call_long": None}


def test_managed_close_debits_round_trip_commission():
    # vertical qty 1: round-trip = 2 legs x 1 x $0.65 x 2 = $2.60
    assert pe.net_realized_pnl(50.0, _vert(), 1, expired=False) == 47.40


def test_expiration_debits_opening_commission_only():
    # vertical qty 1: opening-only = half the round-trip = $1.30
    assert pe.net_realized_pnl(50.0, _vert(), 1, expired=True) == 48.70


def test_iron_condor_four_legs_qty2():
    # IC qty 2: round-trip = 4 legs x 2 x $0.65 x 2 = $10.40 ($2.60 open + $2.60 close, x2)
    assert pe.net_realized_pnl(300.0, _ic(), 2, expired=False) == 289.60
    # expiration opening-only = half the round-trip = $5.20
    assert pe.net_realized_pnl(300.0, _ic(), 2, expired=True) == 294.80


def test_loss_becomes_more_negative_by_commission():
    assert pe.net_realized_pnl(-100.0, _vert(), 1, expired=False) == -102.60


def test_single_leg_short_put_is_billed_as_one_leg():
    # A cash-secured put has ONE leg. Inferring the count from `call_short`
    # alone billed it as a vertical, doubling its commission.
    assert pe._position_legs(_csp()) == 1


def test_assigned_short_put_pays_the_opening_commission_on_one_leg():
    # CSP qty 1: round-trip = 1 leg x 1 x $0.65 x 2 = $1.30; an assignment pays
    # only the opening half = $0.65 (Schwab charges nothing for assignment).
    assert pe.net_realized_pnl(200.0, _csp(), 1, expired=True) == 199.35


def test_managed_close_of_a_short_put_pays_a_one_leg_round_trip():
    assert pe.net_realized_pnl(50.0, _csp(), 1, expired=False) == 48.70


def test_naked_put_spelling_counts_the_same_single_leg():
    # NAKED_PUT is the Calculator/rescue spelling of the same structure.
    pos = dict(_csp(), strategy="NAKED_PUT")
    assert pe._position_legs(pos) == 1


def test_leg_count_by_structure():
    assert pe._position_legs(_vert()) == 2
    assert pe._position_legs(_ic()) == 4
