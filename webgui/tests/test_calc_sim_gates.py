"""The private Calculator and Simulator gate every command they can send.

Neither page is published yet, so ``test_live_commands`` (which enumerates
``live_screens.SCREENS``) does not walk them. The public screens will name these
two modules and pass ``public=True``, which hands off to ``calc_live`` /
``sim_live`` before anything is built; the ``_may_enqueue`` gate is the belt
beside those braces, exactly as ``pages/options/rescue.py`` does it. This test
runs the SAME ``_guarded`` check ``test_live_commands`` runs, over these two
files by name, so the day they are published they already pass it.
"""
import ast
import pathlib

import pytest

from test_live_commands import (_enclosing_functions, _enqueue_calls,
                                _first_statement, _guarded)

_OPTIONS = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options"
_PAGES = {"calculator": ("calc_live", "sim_live"),
          "simulator": ("sim_live", "calc_live")}


def _tree(name):
    return ast.parse((_OPTIONS / f"{name}.py").read_text(encoding="utf-8"))


def _render(tree):
    return next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "render")


@pytest.mark.parametrize("name", sorted(_PAGES))
def test_the_walk_finds_command_sites(name):
    """Non-vacuity: each page really does send commands."""
    tree = _tree(name)
    literal = (_OPTIONS / f"{name}.py").read_text(encoding="utf-8").count(
        "bus_client.request(")
    assert literal >= 3
    assert len(_enqueue_calls(tree)) >= literal


@pytest.mark.parametrize("name", sorted(_PAGES))
def test_every_command_the_page_can_send_is_gated_on_the_origin(name):
    for fn_name, fn in sorted(_enclosing_functions(_tree(name)).items()):
        assert fn_name != "render", (
            f"pages/options/{name}.py sends a command directly from render(); "
            "move it into a small gated inner function")
        assert _guarded(fn), (
            f"pages/options/{name}.py::{fn_name}() (line {fn.lineno}) puts a "
            "command on a cmd: stream without first checking _may_enqueue")


@pytest.mark.parametrize("name", sorted(_PAGES))
def test_render_takes_public_and_hands_off_before_building_anything(name):
    """``render(public=True)`` must import its live module and return as the
    FIRST statement after the docstring, so the owner's page (its bus reads,
    its shared single-user stores) never builds on the public origin. The
    import sits inside the branch, so nothing else ever imports the module."""
    own, other = _PAGES[name]
    tree = _tree(name)
    fn = _render(tree)
    params = [a.arg for a in fn.args.args]
    assert params == ["public"]
    assert [ast.unparse(d) for d in fn.args.defaults] == ["False"]
    stmt = _first_statement(fn)
    assert isinstance(stmt, ast.If) and not stmt.orelse
    assert ast.unparse(stmt.test) == "public"
    assert [ast.unparse(s) for s in stmt.body] == [
        f"from . import {own}", f"return {own}.render()"]
    # Nowhere else in the module is either live module imported.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node is not stmt.body[0]:
            names = {a.name for a in node.names}
            assert own not in names and other not in names
            assert (node.module or "").split(".")[-1] not in (own, other)


@pytest.mark.parametrize("name", sorted(_PAGES))
def test_the_gate_is_read_once_from_shell_after_the_hand_off(name):
    fn = _render(_tree(name))
    src = [ast.unparse(s) for s in fn.body]
    assert "import shell as _shell_gate" in src
    assert "_may_enqueue = _shell_gate.may_enqueue()" in src
    # The hand-off comes first; the gate is read only on the private path.
    hand_off = fn.body.index(_first_statement(fn))
    assert src.index("import shell as _shell_gate") > hand_off
