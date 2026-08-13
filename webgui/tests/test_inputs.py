from pages.options.inputs import bind_symbol_load, select_all_on_focus, should_load


def test_select_all_on_focus_uppercases_and_chains():
    """Every Symbol field is shown in caps (tickers are all-caps) and returns the
    input for chaining."""
    from nicegui import ui
    with ui.card():
        inp = select_all_on_focus(ui.input("Symbol", value="spy"))
    assert "uppercase" in inp._classes
    assert inp.__class__.__name__ == "Input"       # returned the same element


def test_bind_symbol_load_returns_input_and_does_not_fire_on_bind():
    """Binding must not itself trigger a load (it seeds the dedup from the initial
    value), and it returns the input for chaining."""
    from nicegui import ui
    fired = []
    with ui.card():
        inp = ui.input("Symbol", value="SPY")
        out = bind_symbol_load(inp, lambda: fired.append(1))
    assert out is inp
    assert fired == []                              # seeded 'SPY' → no load on bind


def test_should_load_true_for_new_symbol():
    assert should_load("AAPL", None) is True
    assert should_load("MSFT", "AAPL") is True


def test_should_load_false_when_unchanged_or_empty():
    assert should_load("AAPL", "AAPL") is False   # same as already loaded
    assert should_load("", "AAPL") is False        # empty
    assert should_load(None, "AAPL") is False      # None


def test_build_loading_overlay_handle_starts_hidden():
    from nicegui import ui
    from pages.options.overlay import build_loading_overlay
    with ui.card():
        ov = build_loading_overlay("Loading…")
    assert hasattr(ov, "show") and hasattr(ov, "hide")
    assert ov.element.visible is False     # starts hidden
    ov.show("Loading AAPL…")
    assert ov.element.visible is True
    ov.hide()
    assert ov.element.visible is False
