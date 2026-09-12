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
