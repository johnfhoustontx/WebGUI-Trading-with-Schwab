"""The three largest page ``render()`` functions may shrink and may not grow.

Audit CQ-04 (2026-10-03): ``gamma.render`` was 1,484 lines with 62 nested
functions, ``desk.render`` 1,057 and ``calculator.render`` 1,027 - each doubled
or tripled over the summer. A function that size is one scope: every nested
painter can read and write every other's state, and none can be tested without
building the whole page.

The work is to move builders out to module level, a panel at a time. These
tests hold what has been moved and stop the rest growing while that happens.
LOWER a ceiling when you move code out; never raise one. New page behaviour
goes in a module-level function (or a sibling module) that ``render`` calls.
"""
import ast
import importlib
import inspect

import pytest

# module -> (most lines, most nested functions) for its ``render``.
CEILINGS = {
    "pages.options.gamma": (1484, 62),
    "pages.desk": (790, 22),
    "pages.options.calculator": (1027, 40),
}


def _shape(module_name):
    render = importlib.import_module(module_name).render
    src = inspect.getsource(render)
    fn = ast.parse(src.lstrip()).body[0]
    nested = [n for n in ast.walk(fn)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn]
    return len(src.splitlines()), len(nested)


@pytest.mark.parametrize("module_name", sorted(CEILINGS))
def test_render_does_not_grow(module_name):
    lines, nested = _shape(module_name)
    max_lines, max_nested = CEILINGS[module_name]
    assert lines <= max_lines, (
        f"{module_name}.render is {lines} lines, over its ceiling of {max_lines}. "
        "Build the new part in a module-level function that render calls.")
    assert nested <= max_nested, (
        f"{module_name}.render holds {nested} nested functions, over {max_nested}.")


@pytest.mark.parametrize("module_name", sorted(CEILINGS))
def test_a_ceiling_stays_close_to_its_function(module_name):
    """A ceiling far above the function is no ceiling. Lower it after a move."""
    lines, nested = _shape(module_name)
    max_lines, max_nested = CEILINGS[module_name]
    assert max_lines - lines <= 60, f"lower {module_name}'s line ceiling to ~{lines}"
    assert max_nested - nested <= 3, f"lower {module_name}'s nested ceiling to {nested}"


# The Desk's row builders and click-throughs, moved out of render 2026-10-04.
DESK_BUILDERS = ("_dealer_row", "_board_row", "_flow_row", "_position_row",
                 "_news_row", "_bullbear_chip", "_bullbear_breadth",
                 "_open_gamma", "_open_news", "_open_map", "_open_position")


@pytest.mark.parametrize("name", DESK_BUILDERS)
def test_the_desks_row_builders_are_module_level(name):
    from pages import desk
    assert callable(getattr(desk, name, None)), f"desk.{name} is not module-level"


def test_a_desk_row_builder_takes_its_glow_as_an_argument():
    """They read no page state: the glow class is computed by the painter that
    owns the state and handed in, so a builder can be called on its own."""
    from pages import desk
    for name in ("_board_row", "_flow_row", "_position_row"):
        assert list(inspect.signature(getattr(desk, name)).parameters) == ["row", "glow"]
        assert "state[" not in inspect.getsource(getattr(desk, name))
