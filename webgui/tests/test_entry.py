"""Pure trade-entry rules shared by the Calculator and the Simulator."""
import pytest

from pages.options import entry as E

LADDER = [560.0, 565.0, 570.0, 575.0]


def test_step_strike_moves_one_real_strike_and_stops_at_the_ends():
    assert E.step_strike(LADDER, 570.0, +1) == 575.0
    assert E.step_strike(LADDER, 570.0, -1) == 565.0
    assert E.step_strike(LADDER, 575.0, +1) == 575.0
    assert E.step_strike(LADDER, 560.0, -1) == 560.0


def test_step_strike_snaps_an_off_ladder_value_first():
    assert E.step_strike(LADDER, 571.0, 0) == 570.0
    assert E.step_strike(LADDER, 573.0, +1) == 575.0     # snaps to 575, capped
    assert E.step_strike(LADDER, 566.0, -1) == 560.0     # snaps to 565, one down


def test_step_strike_total():
    assert E.step_strike([], 570.0, +1) is None
    assert E.step_strike(None, 570.0, +1) is None
    assert E.step_strike(LADDER, None, +1) == 560.0
    assert E.step_strike([575.0, "x", 560.0, 560.0], 560.0, +1) == 575.0


def test_leg_from_pick_bid_sells_ask_buys_at_the_given_price():
    sell = E.leg_from_pick("bid", "put", 565.0, "2026-09-19", price=2.07)
    assert sell == {"option_type": "put", "side": "short", "strike": 565.0,
                    "expiry": "2026-09-19", "qty": 1, "premium": 2.07}
    buy = E.leg_from_pick("ask", "call", 575, "2026-09-19", price=None)
    assert buy["side"] == "long" and buy["strike"] == 575.0 and buy["premium"] is None


def test_leg_from_pick_rejects_what_is_not_a_click_target():
    with pytest.raises(ValueError):
        E.leg_from_pick("mark", "put", 565.0, "2026-09-19", price=1.0)
    with pytest.raises(ValueError):
        E.leg_from_pick("bid", "stock", 565.0, "2026-09-19", price=1.0)


def test_refill_only_for_identity_fields_and_never_a_manual_price():
    assert E.should_refill("strike", manual=False) is True
    assert E.should_refill("expiry", manual=False) is True
    assert E.should_refill("option_type", manual=False) is True
    assert E.should_refill("strike", manual=True) is False
    assert E.should_refill("qty", manual=False) is False
    assert E.should_refill("side", manual=False) is False
    assert E.should_refill("premium", manual=False) is False


def test_debounce_fires_once_after_the_last_poke():
    d = E.Debounce(0.3)
    assert d.ready(0.0) is False
    d.poke(0.0)
    d.poke(0.2)
    assert d.pending is True
    assert d.ready(0.4) is False     # 0.2 + 0.3 = 0.5
    assert d.ready(0.5) is True
    assert d.pending is False
    assert d.ready(0.9) is False     # consumed


def test_debounce_cancel_drops_a_pending_fire():
    d = E.Debounce(0.3)
    d.poke(0.0)
    d.cancel()
    assert d.ready(10.0) is False


def test_expiry_label_is_a_short_month_day_and_passes_junk_through():
    assert E.expiry_label("2026-09-14") == "Sep 14"
    assert E.expiry_label("2026-10-03") == "Oct 3"
    assert E.expiry_label("junk") == "junk"
    assert E.expiry_label(None) == ""


def test_expiry_options_map_iso_values_to_short_labels():
    assert E.expiry_options(["2026-09-14", "2026-09-18"]) == {
        "2026-09-14": "Sep 14", "2026-09-18": "Sep 18"}


# ── a grid click moves the matching leg (2026-09-12) ────────────────────────

def _L(otype, side, strike, **kw):
    return dict({"option_type": otype, "side": side, "strike": strike,
                 "expiry": "2026-09-19", "qty": 1, "premium": None}, **kw)


def test_pick_target_is_the_leg_with_the_same_side_and_type():
    pcs = [_L("put", "short", 570.0), _L("put", "long", 565.0)]
    assert E.pick_target(pcs, _L("put", "short", 555.0)) == 0
    assert E.pick_target(pcs, _L("put", "long", 575.0)) == 1


def test_pick_target_is_none_when_nothing_matches_so_the_page_adds_a_leg():
    pcs = [_L("put", "short", 570.0), _L("put", "long", 565.0)]
    assert E.pick_target(pcs, _L("call", "short", 580.0)) is None
    assert E.pick_target([], _L("call", "short", 580.0)) is None


def test_pick_target_prefers_the_nearest_strike_among_several_matches():
    ladder = [_L("call", "long", 560.0), _L("call", "short", 570.0),
              _L("call", "long", 580.0)]
    assert E.pick_target(ladder, _L("call", "long", 578.0)) == 2
    assert E.pick_target(ladder, _L("call", "long", 561.0)) == 0


def test_pick_target_never_moves_a_share_leg_and_is_total_on_junk():
    legs = [{"option_type": "stock", "side": "long", "strike": None, "expiry": None,
             "qty": 1, "premium": 100.0}, "junk", None]
    assert E.pick_target(legs, _L("call", "long", 100.0)) is None
    assert E.pick_target(None, _L("call", "long", 100.0)) is None
    assert E.pick_target([_L("call", "long", None)], _L("call", "long", 100.0)) == 0


def test_price_sources_are_bid_mark_ask_with_mark_the_default():
    assert list(E.PRICE_SOURCES) == ["bid", "mark", "ask"]
    assert E.PRICE_SOURCES == {"bid": "Bid", "mark": "Mark", "ask": "Ask"}
    assert E.DEFAULT_PRICE_SOURCE == "mark"
    assert E.price_source(None) == "mark" and E.price_source("ask") == "ask"
    assert E.price_source("last") == "mark"
