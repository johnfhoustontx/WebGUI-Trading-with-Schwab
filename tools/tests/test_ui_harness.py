"""The harness must import without starting a server (tests import it)."""
import importlib.util
import pathlib

import pytest


def _harness():
    path = pathlib.Path(__file__).resolve().parents[1] / "ui_harness.py"
    spec = importlib.util.spec_from_file_location("ui_harness", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)            # __name__ != "__main__": no ui.run
    return mod


def test_harness_imports_without_starting_a_server():
    assert callable(_harness().main)


def test_render_kwargs_default_to_nothing():
    """A page with no arguments is the common case and must stay argument-free."""
    assert _harness().parse_args(["settings"]).render_kwargs == {}


def test_render_kwargs_reach_the_page_that_needs_them():
    """``symbol.render(symbol=...)`` and ``sentiment_momentum.render(level=...)``
    take their subject as an argument, not from the cache - without this the
    harness can only ever draw their empty state."""
    args = _harness().parse_args(
        ["symbol", "--kwargs", '{"symbol": "SPY"}'])
    assert args.render_kwargs == {"symbol": "SPY"}


def test_a_kwargs_value_that_is_not_an_object_is_refused_at_the_command_line():
    """A bare string or list would fail later as ``render(**"SPY")`` - deep in a
    page build, where the traceback says nothing about the command line."""
    h = _harness()
    for bad in ('"SPY"', "[1,2]", "null", "not json"):
        with pytest.raises(SystemExit):
            h.parse_args(["symbol", "--kwargs", bad])
