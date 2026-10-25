"""The ONE position the Calculator and the Simulator share (2026-09-12).

Replaces the Copy-to-Simulator / Copy-to-Calculator buttons: whichever page was
edited last is what the other one opens with.
"""
from pages.options import shared_position as SP


def _leg(**kw):
    base = {"option_type": "put", "side": "short", "strike": 570.0,
            "expiry": "2026-09-19", "qty": 1, "premium": 2.5}
    base.update(kw)
    return base


def setup_function(_):
    SP.reset()


def test_nothing_is_shared_until_a_page_publishes():
    assert SP.current() is None


def test_publish_then_current_round_trips_the_position():
    SP.publish("tsla", "PCS", [_leg(), _leg(side="long", strike=565.0, premium=2.0)],
               "2026-09-19")
    pos = SP.current()
    assert pos["symbol"] == "TSLA" and pos["strategy"] == "PCS"
    assert pos["expiry"] == "2026-09-19"
    assert [l["strike"] for l in pos["legs"]] == [570.0, 565.0]
    assert pos["legs"][0]["premium"] == 2.5           # typed prices travel too


def test_current_is_a_copy_so_a_page_cannot_mutate_the_other_pages_view():
    SP.publish("SPY", "PCS", [_leg()], None)
    SP.current()["legs"][0]["strike"] = 1.0
    assert SP.current()["legs"][0]["strike"] == 570.0


def test_publish_strips_widget_refs_and_private_flags():
    SP.publish("SPY", "PCS", [dict(_leg(), _strike_widget=object(), _manual_premium=True)], None)
    assert set(SP.current()["legs"][0]) == {"option_type", "side", "strike", "expiry",
                                            "qty", "premium"}


def test_a_blank_symbol_is_not_a_position():
    SP.publish("  ", "PCS", [_leg()], None)
    assert SP.current() is None


def test_split_stock_legs_keeps_order_within_each_group():
    stock = {"option_type": "stock", "side": "long", "strike": None, "expiry": None,
             "qty": 1, "premium": 100.0}
    call = _leg(option_type="call", strike=105.0)
    options, shares = SP.split_stock_legs([stock, call, dict(stock, qty=2)])
    assert options == [call]
    assert [s["qty"] for s in shares] == [1, 2]


# --- the Expected Move page joins the share (2026-10-24) --------------------
#
# It edits less of a position than the other two pages - a symbol, an expiry,
# at most one strike - so it goes through ``merge_view``: what it does not name
# (quantities, prices, a calendar's far leg, the shares) must survive its edits.

def _ic():
    return [_leg(strike=570.0, premium=2.5, qty=3),
            _leg(side="long", strike=565.0, premium=1.5, qty=3),
            _leg(option_type="call", strike=600.0, premium=2.0, qty=3),
            _leg(option_type="call", side="long", strike=605.0, premium=1.0, qty=3)]


def test_view_legs_are_the_option_strikes_only():
    stock = {"option_type": "stock", "side": "long", "strike": None,
             "expiry": None, "qty": 1, "premium": 100.0}
    SP.publish("SPY", "COVERED_CALL", [stock, _leg(option_type="call", strike=600.0)],
               "2026-09-19")
    assert SP.view_legs(SP.current()) == [
        {"strike": 600.0, "option_type": "call", "side": "short"}]
    assert SP.view_legs(None) == []


def test_drawing_the_shared_position_unchanged_keeps_it_whole():
    SP.publish("SPY", "IC", _ic(), "2026-09-19")
    before = SP.current()
    SP.publish_view("SPY", "2026-09-19", SP.view_legs(before))
    assert SP.current() == before, "qty 3 and the typed prices must survive"


def test_a_draw_with_no_strike_is_not_a_request_to_delete_the_legs():
    SP.publish("SPY", "IC", _ic(), "2026-09-19")
    before = SP.current()
    SP.publish_view("SPY", "2026-09-19", [])
    SP.publish_view("SPY", None)              # a reload of the same symbol
    assert SP.current() == before


def test_a_new_expiry_moves_the_legs_and_drops_their_prices():
    SP.publish("SPY", "IC", _ic(), "2026-09-19")
    SP.publish_view("SPY", "2026-10-17", [])
    pos = SP.current()
    assert pos["expiry"] == "2026-10-17" and pos["strategy"] == "IC"
    assert {l["expiry"] for l in pos["legs"]} == {"2026-10-17"}
    assert {l["premium"] for l in pos["legs"]} == {None}, "the old expiry's prices"
    assert [l["qty"] for l in pos["legs"]] == [3, 3, 3, 3]
    assert [l["strike"] for l in pos["legs"]] == [570.0, 565.0, 600.0, 605.0]


def test_a_new_expiry_leaves_a_calendars_far_leg_and_the_shares_alone():
    stock = {"option_type": "stock", "side": "long", "strike": None,
             "expiry": None, "qty": 1, "premium": 100.0}
    far = _leg(option_type="call", side="long", strike=600.0, expiry="2026-12-18")
    near = _leg(option_type="call", strike=600.0)
    SP.publish("SPY", "CALENDAR_CALL", [stock, near, far], "2026-09-19")
    SP.publish_view("SPY", "2026-10-17", [])
    legs = SP.current()["legs"]
    assert legs[0] == dict(stock), "a share leg is never touched"
    assert legs[1]["expiry"] == "2026-10-17"
    assert legs[2]["expiry"] == "2026-12-18" and legs[2]["premium"] == 2.5


def test_a_new_symbol_keeps_the_strategy_and_drops_the_strikes():
    SP.publish("SPY", "IC", _ic(), "2026-09-19")
    SP.publish_view("qqq", None)
    assert SP.current() == {"symbol": "QQQ", "strategy": "IC", "legs": [],
                            "expiry": None}


def test_a_picked_strike_is_a_one_leg_position_priced_by_the_calculator():
    SP.publish("SPY", "IC", _ic(), "2026-09-19")
    SP.publish_view("SPY", "2026-09-19",
                    [{"strike": 560.0, "option_type": "put", "side": "short"}])
    assert SP.current() == {
        "symbol": "SPY", "strategy": "NAKED_PUT", "expiry": "2026-09-19",
        "legs": [{"option_type": "put", "side": "short", "strike": 560.0,
                  "expiry": "2026-09-19", "qty": 1, "premium": None}]}


def test_a_hand_off_with_nothing_shared_publishes_its_legs():
    SP.publish_view("SPY", "2026-09-19",
                    [{"strike": 570.0, "option_type": "put", "side": "short"},
                     {"strike": 565.0, "option_type": "put", "side": "long"}])
    pos = SP.current()
    assert pos["symbol"] == "SPY" and pos["strategy"] == "PCS"
    assert [l["strike"] for l in pos["legs"]] == [570.0, 565.0]


def test_a_debit_spread_keeps_its_name_when_its_strikes_move():
    """Strikes never count toward a template's shape, so a put debit spread
    and a put credit spread look alike to ``template_for``: the prior name is
    kept while it still fits."""
    SP.publish("SPY", "VERT_PUT_DEBIT",
               [_leg(side="long", strike=570.0), _leg(strike=565.0)], "2026-09-19")
    SP.publish_view("SPY", "2026-09-19",
                    [{"strike": 575.0, "option_type": "put", "side": "long"},
                     {"strike": 570.0, "option_type": "put", "side": "short"}])
    assert SP.current()["strategy"] == "VERT_PUT_DEBIT"


def test_the_dollar_sign_does_not_make_a_new_symbol():
    """The Expected Move page spells $SPX as SPX; the Calculator keeps the $."""
    SP.publish("$SPX", "IC", _ic(), "2026-09-19")
    before = SP.current()
    SP.publish_view("SPX", None)
    assert SP.current() == before


def test_a_blank_symbol_publishes_nothing():
    SP.publish("SPY", "IC", _ic(), "2026-09-19")
    before = SP.current()
    SP.publish_view("  ", "2026-09-19")
    assert SP.current() == before
    assert SP.merge_view(before, "", None) is None
