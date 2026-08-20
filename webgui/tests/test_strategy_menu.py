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
    """The Calculator's near-black page needs its own popup skin, and the popups
    are teleported to <body> — so the class has to be put on them here. A CSS
    rule for a class nothing carries is unreachable, and no CSS-string test can
    see that."""
    with ui.card():
        sm = SM.build_strategy_menu(value="PCS", boxed=True,
                                    menu_class="strat-menu-calc")
    menus = _menus(sm.button)
    assert menus
    for menu in menus:
        assert "strat-menu-calc" in menu.classes
        assert "strat-menu-navy" not in menu.classes


def test_btn_class_override_replaces_the_navy_token():
    """``boxed=True`` otherwise paints the navy STRATEGY_BTN token straight onto
    the trigger, and build_calc_css sets no competing background — so without
    this the Calculator's trigger renders navy on a near-black page."""
    from pages.options import theme
    with ui.card():
        sm = SM.build_strategy_menu(value="PCS", boxed=True,
                                    btn_class=theme.CALC_STRATEGY_BTN)
    classes = sm.button.classes
    assert "strategy-menu-btn" in classes                 # scope hook retained
    for tok in theme.CALC_STRATEGY_BTN.split():
        assert tok in classes
    for tok in theme.STRATEGY_BTN.split():
        if tok not in theme.CALC_STRATEGY_BTN.split():
            assert tok not in classes, f"navy token {tok} leaked through"
