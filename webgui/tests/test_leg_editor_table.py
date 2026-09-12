"""The leg editor's compact ``layout="table"`` — the entry panel's leg list.

One row per leg: side and type are one-click toggles, the strike is typed or
stepped along the REAL ladder (never a long dropdown), and the price re-fills
from the chain when the leg becomes a different contract — unless it was typed.
Driven through the real widgets' event listeners, the way test_leg_editor.py
drives the card layout.
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


def _price_for(leg):
    return _MARKS.get((leg.get("option_type"), leg.get("strike"), leg.get("expiry")))


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


def test_table_strike_input_snaps_typed_text_on_enter_and_blur():
    ed, container = _table([_leg()])
    box = _hook(container, "leg-strike")
    box.value = "574"
    _fire(box, "keydown.enter")
    assert ed.get_legs()[0]["strike"] == 575.0
    box = _hook(container, "leg-strike")
    box.value = "566"
    _fire(box, "blur")
    assert ed.get_legs()[0]["strike"] == 565.0


def test_table_strike_junk_text_restores_the_strike():
    ed, container = _table([_leg()])
    box = _hook(container, "leg-strike")
    box.value = "abc"
    _fire(box, "keydown.enter")
    assert ed.get_legs()[0]["strike"] == 570.0
    assert _hook(container, "leg-strike").value == "570"


def test_table_strike_arrow_keys_step():
    ed, container = _table([_leg()])
    _fire(_hook(container, "leg-strike"), "keydown.up")
    assert ed.get_legs()[0]["strike"] == 575.0
    _fire(_hook(container, "leg-strike"), "keydown.down")
    assert ed.get_legs()[0]["strike"] == 570.0


def test_table_strike_change_refills_the_price_from_the_chain():
    ed, container = _table([_leg()])
    _fire(_hook(container, "leg-strike-dn"), "click")
    assert ed.get_legs()[0]["premium"] == 2.0


def test_table_refill_keeps_the_old_price_when_the_chain_has_none():
    # never a zeroed price: "no mark" is not "$0.00"
    ed, container = _table([_leg(strike=565.0, premium=2.0)],
                           price_for=lambda leg: None)
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
