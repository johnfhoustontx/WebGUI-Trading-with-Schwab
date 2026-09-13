# webgui/tests/test_handoff_legs.py
from pages.options import handoff


def test_calculator_legs_stash_is_one_shot_and_separate_from_signal():
    legs_payload = {"symbol": "QQQ", "legs": []}
    handoff.set_pending_calculator_legs(legs_payload)
    # the scanner-signal calculator stash is a DIFFERENT slot and stays empty
    assert handoff.take_pending_calculator() is None
    assert handoff.take_pending_calculator_legs() == legs_payload
    assert handoff.take_pending_calculator_legs() is None


# ── Strategy Finder structures reach the Calculator intact ──────────────────
# The Finder now emits calendars (two expirations) and share structures (a
# ``kind: "stock"`` leg with no strike and no expiry, qty in 100-share lots).


def test_calendar_reaches_the_calculator_with_both_expiries():
    sig = {"symbol": "SPY", "legs": [
        {"kind": "call", "side": "short", "strike": 500.0, "expiration": "2026-10-16",
         "qty": 1, "mark": 3.0},
        {"kind": "call", "side": "long", "strike": 500.0, "expiration": "2026-11-13",
         "qty": 1, "mark": 6.0}]}
    legs = handoff._signal_legs_payload(sig)["legs"]
    assert [l["expiry"] for l in legs] == ["2026-10-16", "2026-11-13"]


def test_covered_call_reaches_the_calculator_as_a_stock_leg():
    from pages.options import strategies
    sig = {"symbol": "SPY", "legs": [
        {"kind": "stock", "side": "long", "strike": None, "expiration": None,
         "qty": 1, "mark": 500.0},
        {"kind": "call", "side": "short", "strike": 510.0, "expiration": "2026-10-16",
         "qty": 1, "mark": 3.0}]}
    stock = handoff._signal_legs_payload(sig)["legs"][0]
    assert stock["option_type"] == strategies.STOCK
    assert stock["strike"] is None and stock["expiry"] is None


def test_expected_move_drops_the_share_leg():
    sig = {"symbol": "SPY", "expiration": "2026-10-16", "legs": [
        {"kind": "stock", "side": "long", "strike": None},
        {"kind": "call", "side": "short", "strike": 510.0}]}
    assert [l["strike"] for l in handoff.signal_to_em_payload(sig)["legs"]] == [510.0]


def test_butterfly_body_reaches_the_calculator_with_qty_two():
    sig = {"symbol": "SPY", "legs": [
        {"kind": "call", "side": "long", "strike": 495.0, "expiration": "2026-10-16",
         "qty": 1, "mark": 7.0},
        {"kind": "call", "side": "short", "strike": 500.0, "expiration": "2026-10-16",
         "qty": 2, "mark": 4.0},
        {"kind": "call", "side": "long", "strike": 505.0, "expiration": "2026-10-16",
         "qty": 1, "mark": 2.0}]}
    legs = handoff._signal_legs_payload(sig)["legs"]
    assert [l["qty"] for l in legs] == [1, 2, 1]
    assert legs[1]["side"] == "short" and legs[1]["strike"] == 500.0
