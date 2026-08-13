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
