from pages.options import leg_editor as LE


def test_normalize_keeps_only_normalized_keys():
    legs = [{"option_type": "call", "side": "long", "strike": 100.0,
             "expiry": "2026-07-17", "qty": 1, "premium": 2.5,
             "_strike_widget": object(), "junk": 1}]
    out = LE.normalize_legs(legs)
    assert set(out[0]) == {"option_type", "side", "strike", "expiry", "qty", "premium"}
    assert out[0]["qty"] == 1


def test_normalize_coerces_qty_and_defaults():
    out = LE.normalize_legs([{"option_type": "put", "side": "short",
                              "strike": 95, "expiry": "2026-07-17"}])
    assert out[0]["qty"] == 1            # default
    assert out[0]["premium"] is None     # default


def test_normalize_drops_premium_when_requested():
    legs = [{"option_type": "put", "side": "short", "strike": 95,
             "expiry": "2026-07-17", "qty": 2, "premium": 1.1}]
    out = LE.normalize_legs(legs, keep_premium=False)
    assert out[0]["premium"] is None
    assert out[0]["qty"] == 2


def test_coerce_strike_snaps_to_nearest_in_options():
    assert LE.coerce_strike(736, [735, 736, 737]) == 736         # exact stays
    assert LE.coerce_strike(737.5, [735, 736, 737, 738]) in (737, 738)  # nearest (tie)
    assert LE.coerce_strike(725, [735, 736, 737]) == 735         # below range -> nearest
    assert LE.coerce_strike(999, [735, 736, 737]) == 737         # above range -> nearest
    assert LE.coerce_strike(737.5, []) is None                   # no options -> None
    assert LE.coerce_strike(None, [735]) is None                 # None stays None


def test_coerce_choice_prefers_value_else_first():
    assert LE.coerce_choice("b", ["a", "b", "c"]) == "b"         # present stays
    assert LE.coerce_choice("z", ["a", "b"]) == "a"              # absent -> first available
    assert LE.coerce_choice(None, ["a"]) == "a"                  # None -> first
    assert LE.coerce_choice("a", []) is None                     # no options -> None


def test_leg_editor_coerces_out_of_options_values_without_raising():
    """Regression: NiceGUI ``ui.select`` raises ``ValueError: Invalid value`` when
    a leg's strike/expiry isn't in the select's options. A leg built off the
    cross-expiry strike union, or copied in from the Simulator, can carry a strike
    (e.g. 737.5) or expiry absent from the chosen expiry's chain — the editor must
    coerce it, not crash ``_render``."""
    from nicegui import ui

    strikes = [735, 736, 737, 738]
    with ui.card() as container:
        ed = LE.build_leg_editor(
            container,
            strikes_for=lambda exp, otype: strikes,
            expiries_for=lambda: ["2026-06-23", "2026-06-26"],
            show_premium=True)
        ed.set_legs([
            {"option_type": "call", "side": "long", "strike": 737.5,   # not in strikes
             "expiry": "2026-06-23", "qty": 1, "premium": None},
            {"option_type": "call", "side": "short", "strike": 999,    # far above
             "expiry": "2099-12-31", "qty": 1, "premium": None},       # expiry absent
        ])  # must NOT raise

    legs = ed.get_legs()
    assert legs[0]["strike"] in strikes          # 737.5 snapped into the options
    assert legs[1]["strike"] in strikes          # 999 snapped to nearest (738)
    assert legs[1]["expiry"] == "2026-06-23"     # absent expiry -> first available


def test_set_legs_expiry_sets_every_leg():
    legs = [{"option_type": "call", "side": "long", "strike": 100,
             "expiry": "2026-07-17", "qty": 1, "premium": 2.5},
            {"option_type": "put", "side": "short", "strike": 95,
             "expiry": "2026-08-21", "qty": 2, "premium": 1.0}]
    out = LE.set_legs_expiry(legs, "2026-09-18")
    assert [l["expiry"] for l in out] == ["2026-09-18", "2026-09-18"]
    assert out[0]["strike"] == 100 and out[1]["qty"] == 2   # other fields preserved


def test_apply_expiry_propagates_to_all_legs():
    from nicegui import ui
    with ui.card() as container:
        ed = LE.build_leg_editor(
            container,
            strikes_for=lambda exp, otype: [735, 736, 737],
            expiries_for=lambda: ["2026-06-23", "2026-06-26"],
            show_premium=True)
        ed.set_legs([
            {"option_type": "call", "side": "long", "strike": 736,
             "expiry": "2026-06-23", "qty": 1, "premium": None},
            {"option_type": "put", "side": "short", "strike": 735,
             "expiry": "2026-06-26", "qty": 1, "premium": None}])
        ed.apply_expiry("2026-06-26")     # propagate to ALL legs (literal, per design)
    legs = ed.get_legs()
    assert all(l["expiry"] == "2026-06-26" for l in legs)


# -- the leg palette ----------------------------------------------------------
# The table is the SHARED geometry; the palette enters as an argument so the
# Calculator can pass its near-black CALC_* tokens while the Simulator keeps the
# app-wide dark navy. (The two-line card layout these tests first covered was
# removed 2026-09-12; its surviving behaviour is pinned on the table below.)
import re

from nicegui import ui


def test_default_tokens_cover_every_key_the_table_renders():
    # A missing token key would raise mid-render, on a page that looks fine in
    # every test that never mounts it.
    for key in ("frame", "eyebrow", "num", "delta", "remove", "remove_off", "add",
                "reset", "toggle", "side_long", "side_short", "step", "manual"):
        assert key in LE.DEFAULT_LEG_TOKENS
        assert isinstance(LE.DEFAULT_LEG_TOKENS[key], str)


def test_the_card_layout_is_gone():
    assert LE._LAYOUTS == ("row", "table")
    assert not hasattr(LE, "DEFAULT_CARD_TOKENS") and not hasattr(LE, "card_tokens")


def test_leg_tokens_merge_over_the_defaults():
    merged = LE.leg_tokens({"side_long": "text-[#123456]"})
    assert merged["side_long"] == "text-[#123456]"
    assert merged["frame"] == LE.DEFAULT_LEG_TOKENS["frame"]   # untouched


def test_leg_tokens_ignores_unknown_keys():
    assert "bogus" not in LE.leg_tokens({"bogus": "x"})


def test_leg_tokens_ignores_blank_and_non_string_overrides():
    """A page that computes a token and gets "" back must not blank the row."""
    merged = LE.leg_tokens({"frame": "  ", "eyebrow": None, "num": 7})
    assert merged["frame"] == LE.DEFAULT_LEG_TOKENS["frame"]
    assert merged["eyebrow"] == LE.DEFAULT_LEG_TOKENS["eyebrow"]
    assert merged["num"] == LE.DEFAULT_LEG_TOKENS["num"]


def test_leg_tokens_defaults_survive_a_mutated_return():
    """The merge returns a copy - a caller stashing and editing it must not
    poison every later row."""
    LE.leg_tokens()["frame"] = "wrecked"
    assert LE.DEFAULT_LEG_TOKENS["frame"] != "wrecked"


def test_leg_tokens_degrades_on_a_non_mapping():
    assert LE.leg_tokens(["side_long"]) == LE.DEFAULT_LEG_TOKENS
    assert LE.leg_tokens("frame") == LE.DEFAULT_LEG_TOKENS


def test_can_remove_respects_the_min_legs_floor():
    assert LE.can_remove(3, min_legs=2) is True
    assert LE.can_remove(2, min_legs=2) is False
    assert LE.can_remove(1, min_legs=1) is False
    assert LE.can_remove(2, min_legs=0) is True


def test_can_remove_treats_a_missing_floor_as_no_floor():
    assert LE.can_remove(1, min_legs=None) is True
    assert LE.can_remove(0, min_legs=None) is False


def test_delta_text_formats_signed_two_places():
    assert LE.delta_text(-0.31) == "-0.31"
    assert LE.delta_text(0.44) == "+0.44"
    assert LE.delta_text(0.0) == "+0.00"


def test_delta_text_renders_an_em_dash_for_no_reading():
    assert LE.delta_text(None) == "—"
    assert LE.delta_text("junk") == "—"


def test_delta_text_rejects_booleans():
    """``isinstance(True, int)`` would otherwise print '+1.00' for a flag."""
    assert LE.delta_text(True) == "—"
    assert LE.delta_text(False) == "—"


def test_delta_text_rejects_non_finite_readings():
    """A NaN formats as '+nan' and an infinity as '+inf' - a confident-looking
    string where there is no reading, which is the whole failure mode this
    helper exists to prevent (see the CLAUDE.md _clamp(nan) section)."""
    assert LE.delta_text(float("nan")) == "—"
    assert LE.delta_text(float("inf")) == "—"
    assert LE.delta_text(float("-inf")) == "—"


# -- table rendering ----------------------------------------------------------

_STRIKES = [735, 736, 737, 738]
_EXPS = ["2026-06-23", "2026-06-26"]


def _leg(**kw):
    base = {"option_type": "call", "side": "long", "strike": 736,
            "expiry": _EXPS[0], "qty": 1, "premium": None}
    base.update(kw)
    return base


def _table(legs, **kw):
    """Mount a table-layout editor over ``legs``; returns (handle, container)."""
    kw.setdefault("strikes_for", lambda exp, otype: list(_STRIKES))
    kw.setdefault("expiries_for", lambda: list(_EXPS))
    kw.setdefault("show_premium", True)
    with ui.card() as container:
        ed = LE.build_leg_editor(container, layout="table", **kw)
        ed.set_legs(legs)
    return ed, container


def _labels(container):
    return [e.text for e in container.descendants() if isinstance(e, ui.label)]


def _rows(container):
    return [e for e in container.descendants() if "leg-trow" in e._classes]


def _buttons(container):
    return [e for e in container.descendants() if isinstance(e, ui.button)]


def _fire_click(el):
    # snapshot: the handler re-renders, which deletes elements and mutates the
    # listener registry mid-iteration
    for listener in list(el._event_listeners.values()):
        if listener.type == "click":
            listener.handler(None)


def test_table_layout_coerces_out_of_options_values_like_the_row_layout():
    """The table renderer runs the SAME coercion pass the row renderer does -
    ``ui.select`` raises ValueError on a value absent from its options, and a leg
    copied in from the other page routinely carries one."""
    ed, _ = _table([
        _leg(strike=737.5),                            # not one of the strikes
        _leg(side="short", strike=999, expiry="2099-12-31"),   # expiry absent too
    ])   # must NOT raise
    legs = ed.get_legs()
    assert legs[0]["strike"] in _STRIKES
    assert legs[1]["strike"] in _STRIKES
    assert legs[1]["expiry"] == _EXPS[0]           # coerced value written back


def test_table_layout_shows_the_delta_the_page_supplies():
    """The Calculator composes ``position_delta(extract_delta(...), side)`` in its
    own closure and hands the editor one number per leg."""
    seen = []

    def delta_for(leg):
        seen.append(dict(leg))
        return -0.31 if leg["side"] == "long" else 0.44

    _, container = _table([_leg(option_type="put"),
                           _leg(option_type="put", side="short", strike=735)],
                          delta_for=delta_for)
    txt = _labels(container)
    assert "-0.31" in txt and "+0.44" in txt
    assert seen and seen[0]["option_type"] == "put"    # the whole leg is handed over
    assert seen[0]["strike"] == 736                    # coerced, not raw


def test_table_layout_renders_an_em_dash_when_the_source_has_no_reading():
    """A LIVE source with a hole for this leg (index chains read hollow outside
    regular hours) keeps the cell and blanks it; an ABSENT source drops it."""
    _, container = _table([_leg()], delta_for=lambda leg: None)
    assert "—" in _labels(container)
    assert "DELTA" in _labels(container)
    _, none = _table([_leg()])
    assert "DELTA" not in _labels(none) and "—" not in _labels(none)


def _remove_buttons(container):
    return [e for e in _buttons(container) if "leg-remove" in e._classes]


def test_table_remove_is_live_above_the_floor_and_locked_at_it():
    tk = LE.leg_tokens()
    _, above = _table([_leg(), _leg()], min_legs=1)
    _, at = _table([_leg()], min_legs=1)
    live = _remove_buttons(above)
    assert len(live) == 2 and all(b.enabled for b in live)
    assert tk["remove"].split()[0] in live[0]._classes
    locked = _remove_buttons(at)
    assert len(locked) == 1 and not locked[0].enabled
    assert tk["remove_off"].split()[0] in locked[0]._classes


def test_table_remove_floor_defaults_to_one_leg():
    _, at = _table([_leg()])
    assert not _remove_buttons(at)[0].enabled


def test_table_remove_honours_a_higher_floor():
    _, at = _table([_leg(), _leg()], min_legs=2)
    assert all(not b.enabled for b in _remove_buttons(at))


def test_table_footer_offers_reset_only_when_a_handler_is_given():
    _, without = _table([_leg()])
    _, with_reset = _table([_leg()], on_reset=lambda: None)
    assert "ADD LEG" in [b.text for b in _buttons(without)]
    assert "RESET TO TEMPLATE" not in [b.text for b in _buttons(without)]
    assert "RESET TO TEMPLATE" in [b.text for b in _buttons(with_reset)]


def test_table_reset_button_calls_the_handler():
    hits = []
    _, container = _table([_leg()], on_reset=lambda: hits.append(1))
    btn = [b for b in _buttons(container) if b.text == "RESET TO TEMPLATE"][0]
    _fire_click(btn)
    assert hits == [1]


def test_table_add_leg_button_appends_a_leg():
    ed, container = _table([_leg()])
    btn = [b for b in _buttons(container) if b.text == "ADD LEG"][0]
    _fire_click(btn)
    assert len(ed.get_legs()) == 2


def test_table_layout_renders_with_no_legs():
    ed, container = _table([])
    assert not _rows(container)
    assert "ADD LEG" in [b.text for b in _buttons(container)]
    assert ed.get_legs() == []


def test_table_layout_ignores_the_row_header():
    """``header`` belongs to the row layout; the table carries its own."""
    _, container = _table([_leg()], header=True)
    assert not [e for e in container.descendants() if "leg-head" in e._classes]


def test_row_layout_is_still_the_default_and_builds_no_table():
    with ui.card() as container:
        ed = LE.build_leg_editor(container,
                                 strikes_for=lambda exp, otype: list(_STRIKES),
                                 expiries_for=lambda: list(_EXPS),
                                 show_premium=True, header=True)
        ed.set_legs([_leg()])
    assert not _rows(container)
    assert [e for e in container.descendants() if "leg-row" in e._classes]
    assert [e for e in container.descendants() if "leg-head" in e._classes]


def test_leg_classes_are_spaceless_tailwind_arbitraries():
    """A Tailwind arbitrary value cannot contain a space - one silently
    generates no rule at all."""
    for src in list(LE.DEFAULT_LEG_TOKENS.values()) + list(LE._TABLE_GRIDS.values()):
        for arb in re.findall(r"\[[^\]]*\]", src):
            assert " " not in arb, src


def test_build_leg_editor_rejects_an_unknown_layout():
    """Both layouts are valid renders of the same state, so a typo would put the
    WRONG screen on the page with nothing anywhere reporting a failure. The
    removed ``card`` is now exactly such a typo."""
    import pytest
    for bad in ("crd", "card"):
        with ui.card() as container:
            with pytest.raises(ValueError, match=bad):
                LE.build_leg_editor(container, layout=bad,
                                    strikes_for=lambda exp, otype: list(_STRIKES),
                                    expiries_for=lambda: list(_EXPS), show_premium=True)


# -- the _strike_widget registration ------------------------------------------
# Its absence degrades SILENTLY: the strike select simply keeps the previous
# ladder, and the next edit hands ``ui.select`` a value outside its options -
# the ValueError this whole widget exists to prevent. Pinned per layout,
# behaviourally, rather than by asserting that an assignment exists.

_LADDERS = {"call": [735, 736, 737], "put": [10, 11, 12]}


def _ladder_editor(layout):
    with ui.card() as container:
        ed = LE.build_leg_editor(
            container, layout=layout,
            strikes_for=lambda exp, otype: list(_LADDERS.get(otype or "call", [])),
            expiries_for=lambda: list(_EXPS), show_premium=True)
        ed.set_legs([_leg()])
    return ed, container


def _select_over(container, values):
    return [e for e in container.descendants()
            if isinstance(e, ui.select) and e._values == values][0]


def _strike_select(container):
    return [e for e in container.descendants()
            if isinstance(e, ui.select) and "leg-strike" in e._classes][0]


def test_row_strike_ladder_resyncs_when_the_type_flips():
    """Row mode shares the hoisted registration - and rescue.py mounts it."""
    ed, container = _ladder_editor("row")
    _select_over(container, ["call", "put"]).value = "put"
    assert _strike_select(container)._values == _LADDERS["put"]
    assert ed.get_legs()[0]["strike"] in _LADDERS["put"]


def test_table_strike_dropdown_follows_the_ladder_when_the_type_flips():
    ed, container = _ladder_editor("table")
    _fire_click([b for b in _buttons(container) if "leg-type" in b._classes][0])
    assert _strike_select(container)._values == _LADDERS["put"]
    assert ed.get_legs()[0]["strike"] in _LADDERS["put"]


def test_table_strike_dropdown_follows_the_ladder_when_the_expiry_flips():
    per_expiry = {_EXPS[0]: [735, 736, 737], _EXPS[1]: [10, 11, 12]}
    with ui.card() as container:
        ed = LE.build_leg_editor(
            container, layout="table",
            strikes_for=lambda exp, otype: list(per_expiry.get(exp, [])),
            expiries_for=lambda: list(_EXPS), show_premium=True)
        ed.set_legs([_leg()])
    [e for e in container.descendants() if "leg-expiry" in e._classes][0].value = _EXPS[1]
    assert _strike_select(container)._values == per_expiry[_EXPS[1]]
    assert ed.get_legs()[0]["strike"] in per_expiry[_EXPS[1]]


# The table's track widths, written down HERE rather than read back out of
# ``_TABLE_GRIDS`` - a test that looks the answer up in the table it is checking
# follows a mistake in that table instead of catching it.
_BASE = ["16px", "40px", "36px", "minmax(0,1.15fr)", "minmax(0,1.25fr)", "40px"]
_PRICE = ["50px", "minmax(0,0.9fr)"]


def _tracks(container):
    head = [e for e in container.descendants() if "leg-thead" in e._classes][0]
    grid = [c for c in head._classes if c.startswith("grid-cols-[")][0]
    return grid[len("grid-cols-["):-1].split("_")


def test_table_tracks_match_the_cells_it_renders():
    """The grid template IS the alignment contract: one track per caption, in
    order, so a wrong list slides a value under the wrong caption."""
    d = lambda leg: 0.5
    for prem, delta in ((True, True), (True, False), (False, True), (False, False)):
        kw = {"show_premium": prem}
        if delta:
            kw["delta_for"] = d
        _, container = _table([_leg()], **kw)
        want = _BASE + (_PRICE if prem else []) + (["38px"] if delta else []) + ["22px"]
        assert _tracks(container) == want, (prem, delta)
        head = [e for e in container.descendants() if "leg-thead" in e._classes][0]
        assert len([e for e in head.descendants() if isinstance(e, ui.label)]) == len(want)
