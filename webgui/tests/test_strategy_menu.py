from nicegui import ui

from pages.options import strategy_menu as SM


def test_strategy_menu_value_and_change_interface():
    """The cascading menu is a drop-in for the old ui.select: ``.value`` reads the
    current code, assigning ``.value`` fires the change handlers, and the button
    label tracks the selection."""
    fired = []
    with ui.card():
        sm = SM.build_strategy_menu(value="PCS", classes="w-48")
        sm.on_value_change(lambda e: fired.append(e.value))
        assert sm.value == "PCS"
        assert sm.button.text == "Credit spread — put"
        sm.value = "CCS"          # assigning .value fires handlers (like a select)
    assert sm.value == "CCS"
    assert sm.button.text == "Credit spread — call"
    assert fired == ["CCS"]


def test_strategy_menu_builds_for_every_family():
    with ui.card():
        for code in ("BUTTERFLY_CALL", "IRON_BUTTERFLY", "CALENDAR_PUT", "DIAGONAL_CALL"):
            sm = SM.build_strategy_menu(value=code)
            assert sm.value == code


def test_boxed_strategy_button_carries_hook_and_token():
    """The boxed Strategy trigger keeps the ``strategy-menu-btn`` scope hook AND
    carries the ``STRATEGY_BTN`` Tailwind token (so its box style survives once the
    page stops injecting DASHBOARD_CSS)."""
    from pages.options import theme
    with ui.card():
        sm = SM.build_strategy_menu(value="PCS", boxed=True)
    classes = sm.button.classes
    assert "strategy-menu-btn" in classes               # scope hook retained
    for tok in theme.STRATEGY_BTN.split():
        assert tok in classes                            # token applied


def _menus(root):
    """Every ui.menu mounted under ``root`` — the top-level popup AND the nested
    family submenus, all of which Quasar teleports to <body>."""
    from nicegui import ui
    found = []
    for slot in root.slots.values():
        for child in slot.children:
            if isinstance(child, ui.menu):
                found.append(child)
            found.extend(_menus(child))
    return found


def test_default_menu_and_button_classes_are_unchanged():
    """The two new overrides must be invisible until asked for: the Simulator,
    Rescue and today's Calculator all rely on these exact defaults."""
    from pages.options import theme
    with ui.card():
        boxed = SM.build_strategy_menu(value="PCS", boxed=True)
        plain = SM.build_strategy_menu(value="PCS")
    assert len(_menus(boxed.button)) > 1, "expected the popup and its submenus"
    for menu in _menus(boxed.button):
        assert "strat-menu-navy" in menu.classes
    for menu in _menus(plain.button):
        assert menu.classes == [], "the un-boxed popup carries no page class"
    for tok in theme.STRATEGY_BTN.split():
        assert tok in boxed.button.classes
    assert plain.button.classes == ["w-full", "strategy-menu-btn"]


def test_menu_class_override_reaches_every_popup():
    """A page with a palette of its own needs its own popup skin, and the popups
    are teleported to <body> — so the class has to be put on them here. A CSS
    rule for a class nothing carries is unreachable, and no CSS-string test can
    see that.

    ⚠ The Calculator was the one caller and stopped being one on 2026-09-20,
    when ``[calc]`` retired; the override stays a parameter of a SHARED widget,
    so it is exercised here with an arbitrary class rather than a live one."""
    with ui.card():
        sm = SM.build_strategy_menu(value="PCS", boxed=True,
                                    menu_class="strat-menu-elsewhere")
    menus = _menus(sm.button)
    assert menus
    for menu in menus:
        assert "strat-menu-elsewhere" in menu.classes
        assert "strat-menu-navy" not in menu.classes


#: An arbitrary trigger skin, not a live token: ``theme.CALC_STRATEGY_BTN`` was
#: the only caller and went with the ``[calc]`` surface vocabulary. What is
#: under test is that an override REPLACES the navy default rather than layering
#: over it — a ``bg-``/``border-``/``text-`` pair would otherwise tie on
#: specificity and let stylesheet order decide (the DESK_NEON_CSS trap).
_OTHER_BTN = "bg-[#001122] border border-[#003344] text-[#005566] rounded-[2px]"


def test_btn_class_override_replaces_the_navy_token():
    """``boxed=True`` otherwise paints the navy STRATEGY_BTN token straight onto
    the trigger, so a page whose own CSS sets no competing background would get
    a navy trigger on its own ground."""
    from pages.options import theme
    with ui.card():
        sm = SM.build_strategy_menu(value="PCS", boxed=True,
                                    btn_class=_OTHER_BTN)
    classes = sm.button.classes
    assert "strategy-menu-btn" in classes                 # scope hook retained
    for tok in _OTHER_BTN.split():
        assert tok in classes
    for tok in theme.STRATEGY_BTN.split():
        if tok not in _OTHER_BTN.split():
            assert tok not in classes, f"navy token {tok} leaked through"


def _texts(root):
    """Every label text mounted under ``root``."""
    stack, out = [root], []
    while stack:
        el = stack.pop()
        text = getattr(el, "text", None)
        if isinstance(text, str):
            out.append(text)
        for slot in getattr(el, "slots", {}).values():
            stack.extend(slot.children)
    return out


def test_the_strategy_caption_is_rendered_by_default():
    """The third override, and the third to default to today's behaviour: the
    caption stays unless a page says otherwise.

    ⚠ Its original reason - "the Simulator and Rescue have no other label for
    this control" - was measured false on 2026-09-20: both pass
    ``caption=False`` today (through the entry panel and through
    ``kit.field("Strategy")`` respectively). The DEFAULT is still what this
    test pins, because flipping it would silently strip the label from the
    next caller that wants one."""
    with ui.card() as root:
        SM.build_strategy_menu(value="PCS")
    assert "Strategy" in _texts(root)
    with ui.card() as boxed_root:
        SM.build_strategy_menu(value="PCS", boxed=True)
    assert "Strategy" in _texts(boxed_root)


def test_caption_false_drops_the_word_and_nothing_else():
    """The Calculator's ① STRATEGY frame chip already says it; a caption above
    the picker says it twice, one line apart."""
    with ui.card() as root:
        sm = SM.build_strategy_menu(value="PCS", boxed=True, caption=False)
    assert "Strategy" not in _texts(root)
    # the trigger itself is untouched — it still labels the current selection
    assert sm.button.text == "Credit spread — put"
    assert "strategy-menu-btn" in sm.button.classes


def test_the_class_default_matches_the_factory_default():
    """``build_strategy_menu`` passes ``caption`` through explicitly, so the
    class's own default is only reachable by constructing ``StrategyMenu``
    directly — which is public. Flipping it there would strip the caption for
    that caller alone, and every factory-routed test would stay green."""
    with ui.card() as root:
        SM.StrategyMenu(value="PCS")
    assert "Strategy" in _texts(root)


# ── the kit migration (Phase 2, Task 2) ──────────────────────────────────────

def test_the_strategy_caption_wears_the_apps_field_label():
    """It is a label above a field, and the standard says those look one way
    everywhere.

    ⚠ Measured while doing it: NO live mount draws this label. The entry panel
    passes ``caption=False`` for both the Calculator and the Simulator, and
    rescue.py mounts the picker inside ``kit.field("Strategy")`` and passes it
    too. So this is the default a FUTURE caller gets, and pinning it to the
    app's ``EYEBROW`` is what stops that caller arriving with a second spelling
    of a field label - it was ``text-xs opacity-60``, which is neither."""
    from pages.options import theme
    with ui.card() as root:
        SM.build_strategy_menu(value="PCS")
    caps = [e for e in root.descendants() if getattr(e, "text", None) == "Strategy"]
    assert len(caps) == 1
    assert caps[0].classes == theme.EYEBROW.split()


def test_the_two_triggers_stay_raw_buttons_on_purpose():
    """MUST NOT CHANGE - passes on both sides, and is here so the guard's
    ``{"button": 2}`` is a decision rather than an omission. This is a cascading
    VALUE PICKER standing in for ``ui.select``: the kit has no such field, and
    its four button kinds carry neither a current-value label nor a menu anchor.
    Both spellings survive because ``boxed`` decides whether the Quasar outline
    (which forces a transparent background page CSS cannot beat) is dropped."""
    with ui.card():
        boxed = SM.build_strategy_menu(value="PCS", boxed=True)
        plain = SM.build_strategy_menu(value="PCS")
    for sm in (boxed, plain):
        assert isinstance(sm.button, ui.button)
        assert sm.button._props.get("icon-right") == "arrow_drop_down"
        assert sm.button.text == "Credit spread — put"
    assert plain.button._props.get("outline") is True
    assert boxed.button._props.get("outline") is None
