"""The leg editor's compact ``layout="table"`` — the entry panel's leg list.

One row per leg: side and type are one-click toggles, the strike is a dropdown
of the REAL ladder (‹ › step it), and the price re-fills from the chain at the
row's Bid / Mark / Ask when the leg becomes a different contract — unless it was
typed. A chain-grid click moves the matching leg (``place_pick``). Driven through
the real widgets' event listeners.
"""
import re

import pytest
from nicegui import ui

from pages.options import leg_editor as LE

_STRIKES = [560.0, 565.0, 570.0, 575.0]
_PUT_STRIKES = [10.0, 11.0, 12.0]
_EXPS = ["2026-09-19", "2026-09-26"]
_MARKS = {("put", 565.0, _EXPS[0]): 2.0, ("put", 570.0, _EXPS[0]): 2.5,
          ("put", 575.0, _EXPS[0]): 3.1, ("call", 570.0, _EXPS[0]): 4.0,
          ("put", 11.0, _EXPS[0]): 0.7, ("put", 565.0, _EXPS[1]): 2.2}


def _leg(**kw):
    base = {"option_type": "put", "side": "short", "strike": 570.0,
            "expiry": _EXPS[0], "qty": 1, "premium": 2.5}
    base.update(kw)
    return base


def _price_for(leg, source="mark"):
    # the bid a dime under the mark and the ask a dime over it
    mark = _MARKS.get((leg.get("option_type"), leg.get("strike"), leg.get("expiry")))
    if mark is None:
        return None
    return round(mark + {"bid": -0.1, "ask": 0.1}.get(source, 0.0), 2)


def _table(legs, **kw):
    kw.setdefault("strikes_for", lambda exp, otype: list(_STRIKES))
    kw.setdefault("expiries_for", lambda: list(_EXPS))
    kw.setdefault("show_premium", True)
    kw.setdefault("price_for", _price_for)
    with ui.card() as container:
        ed = LE.build_leg_editor(container, layout="table", **kw)
        ed.set_legs(legs)
    return ed, container


def _all(container, cls):
    return [e for e in container.descendants() if cls in e._classes]


def _hook(container, cls, i=0):
    return _all(container, cls)[i]


def _fire(el, event):
    # snapshot: a handler may re-render, deleting elements mid-iteration
    for listener in list(el._event_listeners.values()):
        if listener.type == event:
            listener.handler(None)


def test_table_layout_is_accepted_and_numbers_its_rows():
    _, container = _table([_leg(), _leg(strike=565.0)])
    assert len(_all(container, "leg-trow")) == 2
    nums = [e.text for e in _all(container, "leg-num")]
    assert nums == ["1", "2"]


def test_table_side_button_flips_long_and_short():
    ed, container = _table([_leg()])
    assert _hook(container, "leg-side").text == "SELL"
    _fire(_hook(container, "leg-side"), "click")
    assert ed.get_legs()[0]["side"] == "long"
    assert _hook(container, "leg-side").text == "BUY"
    assert ed.is_dirty()


def test_table_type_button_cycles_call_and_put_only_by_default():
    ed, container = _table([_leg()])
    _fire(_hook(container, "leg-type"), "click")
    assert ed.get_legs()[0]["option_type"] == "call"
    _fire(_hook(container, "leg-type"), "click")
    assert ed.get_legs()[0]["option_type"] == "put"


def test_table_type_button_reaches_stock_only_when_the_mount_allows_it():
    ed, container = _table([_leg(option_type="call")], allow_stock=True)
    _fire(_hook(container, "leg-type"), "click")       # call -> put
    _fire(_hook(container, "leg-type"), "click")       # put -> stock
    leg = ed.get_legs()[0]
    assert leg["option_type"] == "stock"
    assert leg["strike"] is None and leg["expiry"] is None


def test_table_strike_buttons_step_one_real_strike():
    ed, container = _table([_leg()])
    _fire(_hook(container, "leg-strike-up"), "click")
    assert ed.get_legs()[0]["strike"] == 575.0
    _fire(_hook(container, "leg-strike-dn"), "click")
    _fire(_hook(container, "leg-strike-dn"), "click")
    assert ed.get_legs()[0]["strike"] == 565.0


def test_table_strike_change_refills_the_price_from_the_chain():
    ed, container = _table([_leg()])
    _fire(_hook(container, "leg-strike-dn"), "click")
    assert ed.get_legs()[0]["premium"] == 2.0


def test_table_refill_keeps_the_old_price_when_the_chain_has_none():
    # never a zeroed price: "no mark" is not "$0.00"
    ed, container = _table([_leg(strike=565.0, premium=2.0)],
                           price_for=lambda leg, source: None)
    _fire(_hook(container, "leg-strike-up"), "click")
    assert ed.get_legs()[0]["premium"] == 2.0


def test_table_typed_price_survives_a_strike_change():
    ed, container = _table([_leg()])
    _hook(container, "leg-price").value = 9.99
    _fire(_hook(container, "leg-strike-dn"), "click")
    assert ed.get_legs()[0]["strike"] == 565.0
    assert ed.get_legs()[0]["premium"] == 9.99


def test_table_price_reset_shows_only_when_manual_and_refills():
    ed, container = _table([_leg()])
    assert not _hook(container, "leg-price-reset").visible
    _hook(container, "leg-price").value = 9.99
    assert _hook(container, "leg-price-reset").visible
    _fire(_hook(container, "leg-price-reset"), "click")
    assert ed.get_legs()[0]["premium"] == 2.5
    assert not _hook(container, "leg-price-reset").visible


def test_table_type_change_resnaps_the_strike_to_the_new_ladder_and_refills():
    ed, container = _table(
        [_leg(option_type="call", premium=4.0)],
        strikes_for=lambda exp, otype: list(_PUT_STRIKES if otype == "put" else _STRIKES),
        spot_getter=lambda: 11.2)
    _fire(_hook(container, "leg-type"), "click")          # call -> put
    leg = ed.get_legs()[0]
    assert leg["strike"] == 11.0
    assert leg["premium"] == 0.7


def test_table_expiry_change_refills():
    ed, container = _table([_leg(strike=565.0, premium=1.0)])
    _hook(container, "leg-expiry").value = _EXPS[1]
    assert ed.get_legs()[0]["expiry"] == _EXPS[1]
    assert ed.get_legs()[0]["premium"] == 2.2


def test_table_qty_edit_writes_the_leg_without_refilling():
    ed, container = _table([_leg(premium=9.0)])
    _hook(container, "leg-qty").value = 3
    assert ed.get_legs()[0]["qty"] == 3
    assert ed.get_legs()[0]["premium"] == 9.0


def test_table_share_leg_disables_strike_and_expiry():
    _, container = _table([{"option_type": "stock", "side": "long", "strike": None,
                            "expiry": None, "qty": 1, "premium": 100.0}],
                          allow_stock=True)
    assert not _hook(container, "leg-strike").enabled
    assert not _hook(container, "leg-strike-up").enabled
    assert not _hook(container, "leg-strike-dn").enabled
    assert not _hook(container, "leg-expiry").enabled


def test_table_remove_respects_min_legs():
    ed, container = _table([_leg(), _leg(strike=565.0)], min_legs=1)
    _fire(_hook(container, "leg-remove"), "click")
    assert len(ed.get_legs()) == 1
    assert not _hook(container, "leg-remove").enabled


def test_table_omits_price_and_delta_columns_when_not_supplied():
    _, container = _table([_leg()], show_premium=False)
    assert not _all(container, "leg-price")
    assert not _all(container, "leg-delta")
    _, with_delta = _table([_leg()], delta_for=lambda leg: -0.31)
    assert _hook(with_delta, "leg-delta").text == "-0.31"


def test_table_header_and_row_share_one_track_list():
    for kw in ({}, {"show_premium": False}, {"delta_for": lambda leg: 0.1},
               {"show_premium": False, "delta_for": lambda leg: 0.1}):
        _, container = _table([_leg()], **kw)
        head = [c for c in _hook(container, "leg-thead")._classes if c.startswith("grid-cols-")]
        row = [c for c in _hook(container, "leg-trow")._classes if c.startswith("grid-cols-")]
        assert head and head == row, kw


def test_table_grid_tracks_are_four_static_spaceless_strings():
    assert len(set(LE._TABLE_GRIDS.values())) == 4
    for src in LE._TABLE_GRIDS.values():
        for arb in re.findall(r"\[[^\]]*\]", src):
            assert " " not in arb, src


def test_add_leg_keeps_other_legs_typed_prices():
    ed, container = _table([_leg()])
    _hook(container, "leg-price").value = 9.99
    ed.add_leg({"option_type": "call", "side": "long", "strike": 575.0,
                "expiry": _EXPS[0], "qty": 1, "premium": 1.0})
    assert len(ed.get_legs()) == 2 and ed.is_dirty()
    _fire(_hook(container, "leg-strike-dn"), "click")     # first leg: 570 -> 565
    assert ed.get_legs()[0]["premium"] == 9.99


def test_add_leg_fires_on_change():
    hits = []
    ed, _ = _table([_leg()], on_change=lambda: hits.append(1))
    ed.add_leg(_leg(strike=565.0))
    assert hits == [1]


def test_refill_prices_fills_every_leg_but_a_typed_one():
    ed, container = _table([_leg(premium=None), _leg(strike=565.0, premium=None)])
    _hook(container, "leg-price", 1).value = 7.0
    ed.refill_prices()
    assert [l["premium"] for l in ed.get_legs()] == [2.5, 7.0]


def test_normalize_strips_the_manual_flag():
    assert "_manual_premium" not in LE.normalize_legs([_leg(_manual_premium=True)])[0]


def test_build_leg_editor_still_rejects_an_unknown_layout():
    with ui.card() as container:
        with pytest.raises(ValueError):
            LE.build_leg_editor(container, layout="tabel", strikes_for=lambda e, t: [],
                                expiries_for=lambda: [], show_premium=True)


def test_refill_prices_a_share_leg_only_while_it_has_no_price():
    # A share leg's price is what the shares cost; unset means "spot now", and a
    # basis the user already has is never overwritten (fill_stock_premiums' rule).
    stock = {"option_type": "stock", "side": "long", "strike": None,
             "expiry": None, "qty": 1, "premium": None}
    ed, _ = _table([stock, dict(stock, premium=88.0)], allow_stock=True,
                   price_for=lambda leg, source: 101.5 if leg["option_type"] == "stock" else None)
    ed.refill_prices()
    assert [l["premium"] for l in ed.get_legs()] == [101.5, 88.0]


def test_apply_template_can_lay_legs_on_a_chosen_near_expiry():
    ed, _ = _table([], spot_getter=lambda: 570.0)
    ed.apply_template("PCS", near=_EXPS[1])
    assert {l["expiry"] for l in ed.get_legs()} == {_EXPS[1]}
    ed.apply_template("PCS", near="2031-01-01")          # unlisted: the nearest
    assert {l["expiry"] for l in ed.get_legs()} == {_EXPS[0]}


def test_table_expiry_select_shows_short_labels_over_iso_values():
    _, container = _table([_leg()])
    sel = _hook(container, "leg-expiry")
    assert sel.options == {_EXPS[0]: "Sep 19", _EXPS[1]: "Sep 26"}
    assert sel.value == _EXPS[0]


def test_a_page_with_no_price_column_clears_a_moved_legs_price():
    # The Simulator shows no price and has no chain price source. A price that
    # arrived with the leg (from the Calculator) belongs to the OLD contract, so
    # changing strike, expiry or type must drop it — the Calculator then prices
    # the new contract instead of showing the old one's mark.
    ed, container = _table([_leg(premium=2.5), _leg(strike=565.0, premium=2.0)],
                           show_premium=False, price_for=None)
    _fire(_hook(container, "leg-strike-up"), "click")
    assert ed.get_legs()[0]["premium"] is None
    assert ed.get_legs()[1]["premium"] == 2.0            # untouched leg keeps its price
    _fire(_hook(container, "leg-side", 1), "click")      # a side flip is the same contract
    assert ed.get_legs()[1]["premium"] == 2.0


# ── the strike dropdown (2026-09-12) ────────────────────────────────────────

def test_table_strike_is_a_dropdown_of_the_real_ladder():
    _, container = _table([_leg()])
    sel = _hook(container, "leg-strike")
    assert isinstance(sel, ui.select)
    assert sel.options == {560.0: "560", 565.0: "565", 570.0: "570", 575.0: "575"}
    assert sel.value == 570.0


def test_table_choosing_a_strike_moves_the_leg_and_refills_its_price():
    ed, container = _table([_leg()])
    _hook(container, "leg-strike").value = 575.0
    assert ed.get_legs()[0]["strike"] == 575.0
    assert ed.get_legs()[0]["premium"] == 3.1
    assert ed.is_dirty()


def test_table_a_cleared_strike_filter_does_not_unstrike_the_leg():
    ed, container = _table([_leg()])
    _hook(container, "leg-strike").value = None
    assert ed.get_legs()[0]["strike"] == 570.0


# ── the price-source dropdown (2026-09-12) ──────────────────────────────────

def test_table_price_source_dropdown_offers_bid_mark_ask_and_starts_at_the_mark():
    _, container = _table([_leg()])
    sel = _hook(container, "leg-price-source")
    assert sel.options == {"bid": "Bid", "mark": "Mark", "ask": "Ask"}
    assert sel.value == "mark"


def test_table_choosing_a_source_reprices_the_leg_from_that_side():
    ed, container = _table([_leg()])
    _hook(container, "leg-price-source").value = "bid"
    assert ed.get_legs()[0]["premium"] == 2.4
    _hook(container, "leg-price-source").value = "ask"
    assert ed.get_legs()[0]["premium"] == 2.6


def test_table_the_chosen_source_prices_the_next_strike_too():
    ed, container = _table([_leg()])
    _hook(container, "leg-price-source").value = "ask"
    _fire(_hook(container, "leg-strike-dn"), "click")       # 570 -> 565
    assert ed.get_legs()[0]["premium"] == 2.1
    assert _hook(container, "leg-price-source").value == "ask"


def test_table_choosing_a_source_replaces_a_typed_price():
    ed, container = _table([_leg()])
    _hook(container, "leg-price").value = 9.99
    _hook(container, "leg-price-source").value = "bid"
    assert ed.get_legs()[0]["premium"] == 2.4
    assert not _hook(container, "leg-price-reset").visible


def test_table_source_survives_a_type_flip_and_an_expiry_change():
    ed, container = _table([_leg(strike=565.0)])
    _hook(container, "leg-price-source").value = "ask"
    ed.apply_expiry(_EXPS[1])
    ed.refill_prices()                                    # what the page does next
    assert ed.get_legs()[0]["premium"] == 2.3             # 2.2 mark + a dime
    assert _hook(container, "leg-price-source").value == "ask"


def test_table_share_leg_hides_the_price_source():
    _, container = _table([{"option_type": "stock", "side": "long", "strike": None,
                            "expiry": None, "qty": 1, "premium": 100.0}],
                          allow_stock=True)
    assert not _hook(container, "leg-price-source").visible


def test_normalize_strips_the_price_source():
    assert "_price_source" not in LE.normalize_legs([_leg(_price_source="bid")])[0]


# ── a grid click moves the matching leg (2026-09-12) ────────────────────────

def _pick(side, otype, strike, expiry=_EXPS[0]):
    return {"option_type": otype, "side": side, "strike": strike, "expiry": expiry,
            "qty": 1, "premium": None}


def test_place_pick_moves_the_leg_on_the_same_side_and_type():
    ed, container = _table([_leg(), _leg(side="long", strike=565.0, premium=2.0)])
    assert ed.place_pick(_pick("short", "put", 575.0)) == 0
    legs = ed.get_legs()
    assert len(legs) == 2
    assert (legs[0]["side"], legs[0]["strike"], legs[0]["premium"]) == ("short", 575.0, 3.1)
    assert legs[1]["strike"] == 565.0                     # the other leg untouched
    assert ed.is_dirty()


def test_place_pick_adds_a_leg_only_when_nothing_matches():
    ed, _ = _table([_leg()])
    assert ed.place_pick(_pick("long", "call", 570.0)) == 1
    legs = ed.get_legs()
    assert len(legs) == 2 and legs[1]["option_type"] == "call"
    assert legs[1]["premium"] == 4.0


def test_place_pick_keeps_the_rows_quantity_and_price_source_but_not_a_typed_price():
    ed, container = _table([_leg(qty=3)])
    _hook(container, "leg-price-source").value = "bid"
    _hook(container, "leg-price").value = 9.99
    ed.place_pick(_pick("short", "put", 565.0, _EXPS[1]))
    leg = ed.get_legs()[0]
    assert (leg["qty"], leg["expiry"], leg["strike"]) == (3, _EXPS[1], 565.0)
    assert leg["premium"] == 2.1                          # 2.2 mark - a dime: the bid
    assert not _hook(container, "leg-price-reset").visible


def test_place_pick_with_no_reading_leaves_the_moved_leg_unpriced():
    # the old contract's price must not ride onto the new one
    ed, _ = _table([_leg()])
    ed.place_pick(_pick("short", "put", 560.0))
    assert ed.get_legs()[0]["premium"] is None


def test_place_pick_fires_on_change_once():
    hits = []
    ed, _ = _table([_leg()], on_change=lambda: hits.append(1))
    ed.place_pick(_pick("short", "put", 565.0))
    assert hits == [1]


def test_place_pick_on_a_page_with_no_price_column_moves_the_leg_unpriced():
    ed, _ = _table([_leg(premium=2.5)], show_premium=False, price_for=None)
    ed.place_pick(_pick("short", "put", 575.0))
    assert ed.get_legs() == [dict(_leg(), strike=575.0, premium=None)]
