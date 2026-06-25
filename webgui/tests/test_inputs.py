from pages.options.inputs import should_load


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
