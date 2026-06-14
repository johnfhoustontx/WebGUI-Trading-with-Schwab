"""Tests for the cross-page signal handoff (Send to Calculator / Paper trade)."""
from pages.options import handoff


def test_pending_calculator_set_and_take():
    handoff.set_pending_calculator({"symbol": "MU", "type": "PCS"})
    sig = handoff.take_pending_calculator()
    assert sig == {"symbol": "MU", "type": "PCS"}
    # consumed once
    assert handoff.take_pending_calculator() is None


def test_take_pending_calculator_empty():
    handoff.take_pending_calculator()  # clear any
    assert handoff.take_pending_calculator() is None
