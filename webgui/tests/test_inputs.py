from pages.options.inputs import should_load


def test_should_load_true_for_new_symbol():
    assert should_load("AAPL", None) is True
    assert should_load("MSFT", "AAPL") is True


def test_should_load_false_when_unchanged_or_empty():
    assert should_load("AAPL", "AAPL") is False   # same as already loaded
    assert should_load("", "AAPL") is False        # empty
    assert should_load(None, "AAPL") is False      # None
