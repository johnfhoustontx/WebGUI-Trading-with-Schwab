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


def test_a_scanner_backspread_reaches_the_calculator_as_its_own_template():
    """Without a template of that shape the Calculator's picker keeps whatever
    it last showed and marks legs nobody edited as "edited"."""
    from pages.options import sim_view
    sig = {"symbol": "$SPX", "type": "CALL_BACKSPREAD",
           "legs": [{"kind": "call", "side": "short", "strike": 100.0,
                     "expiration": "2026-11-06", "qty": 1, "mark": 2.1},
                    {"kind": "call", "side": "long", "strike": 103.0,
                     "expiration": "2026-11-06", "qty": 2, "mark": 0.85}]}
    payload = handoff._signal_legs_payload(sig)
    assert payload["symbol"] == "SPX"
    assert [(l["side"], l["qty"], l["strike"]) for l in payload["legs"]] == [
        ("short", 1, 100.0), ("long", 2, 103.0)]
    assert sim_view.template_for(payload["legs"]) == "CALL_BACKSPREAD"
    assert sim_view.matches_template("CALL_BACKSPREAD", payload["legs"])
    # The same shape at ten times the size is still a backspread ...
    big = [dict(l, qty=l["qty"] * 10) for l in payload["legs"]]
    assert sim_view.template_for(big) == "CALL_BACKSPREAD"
    # ... and it is not mistaken for either two-leg call vertical.
    assert not sim_view.matches_template("CCS", payload["legs"])
    assert not sim_view.matches_template("VERT_CALL_DEBIT", payload["legs"])
    put = [dict(l, option_type="put") for l in payload["legs"]]
    assert sim_view.template_for(put) == "PUT_BACKSPREAD"


def test_a_plain_vertical_is_not_read_as_a_backspread():
    from pages.options import sim_view
    ccs = [{"option_type": "call", "side": "short", "strike": 100, "expiry": "2026-11-06",
            "qty": 1}, {"option_type": "call", "side": "long", "strike": 105,
                        "expiry": "2026-11-06", "qty": 1}]
    assert not sim_view.matches_template("CALL_BACKSPREAD", ccs)
    assert sim_view.template_for(ccs) == "CCS"
