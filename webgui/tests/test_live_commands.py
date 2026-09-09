"""No PUBLISHED page may draw a control that puts a command on a bus stream.

⚠ ENUMERATED FROM ``live_screens.SCREENS``, never from a list of today's page
names. A test naming the six sentiment screens would pass forever while a
seventh was published, which is exactly how this got missed the first time:
``pages/options/gamma.py`` was gated at length and documented as the standard,
and the six pages beside it kept their live Refresh buttons.

The three costs of a reachable command control on the public origin, in order:

1. It contradicts the standard the gamma work set — a button that cannot work
   must not be drawn.
2. ``pages/ui_guard.guard`` RE-RAISES anything that is not the deleted-slot
   ``RuntimeError``, so every click writes a full traceback. ``live_main``
   never calls ``logging_setup``, so it lands in journald: a free
   unauthenticated log-amplification vector.
3. ``bus_client.request``'s refusal is the only thing between a visitor and a
   ``sentiment refresh`` — eleven sector chains plus histories per click. It
   defaults OFF; ``live_main`` turns it on.

Two independent halves, and neither covers the other's spelling — the same
pairing ``tests/test_options_gamma.py`` documents:

* the SOURCE walk below, over ``<anything>.request(...)`` attribute calls;
* the import-form ban, since ``from bus_client import request`` makes the call
  a bare ``ast.Name`` no attribute walk can see.
"""
import ast
import importlib
import pathlib

import pytest

import live_screens

_PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"


def _module_path(dotted: str) -> pathlib.Path:
    return _PAGES.joinpath(*dotted.split(".")).with_suffix(".py")


def published_modules() -> dict[str, pathlib.Path]:
    """``{dotted module: path}`` for every page ``live_screens`` publishes.

    Deduped — four screens share ``options.gamma`` — and derived, so a
    fifteenth screen is covered the day it is added."""
    return {s.module: _module_path(s.module) for s in live_screens.SCREENS}


def _trees():
    return {name: ast.parse(path.read_text(encoding="utf-8"))
            for name, path in published_modules().items()}


def _parents(tree):
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _enqueue_calls(tree):
    """Every ``<anything>.request(...)`` call node.

    Deliberately NOT anchored on the receiver being literally ``bus_client``:
    ``import bus_client as _bc`` / ``_bc.request(...)`` is one keystroke past a
    security enumeration. The receiver carries no information this needs; the
    method name does. (Same reasoning, same shape, as the gamma walk.)"""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "request"]


def _enclosing_functions(tree):
    """``{function name: FunctionDef}`` for each command site's INNERMOST
    enclosing function, so ``render`` itself is never the answer."""
    up = _parents(tree)
    out = {}
    for call in _enqueue_calls(tree):
        node = up.get(call)
        while node is not None and not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node = up.get(node)
        assert node is not None, f"command at line {call.lineno} sits in no function"
        out[node.name] = node
    return out


def _first_statement(fn):
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]                       # skip the docstring
    return body[0] if body else None


def _guarded(fn) -> bool:
    """True when ``fn`` opens with ``if not _may_enqueue: return``.

    A first-statement early return is a TOTAL proof: it covers the button, a
    timer, a hand-off path and any closure that reaches the function, which no
    "is this control built?" check can do on its own."""
    stmt = _first_statement(fn)
    return (isinstance(stmt, ast.If) and not stmt.orelse
            and isinstance(stmt.test, ast.UnaryOp)
            and isinstance(stmt.test.op, ast.Not)
            and isinstance(stmt.test.operand, ast.Name)
            and stmt.test.operand.id == "_may_enqueue"
            and isinstance(stmt.body[-1], ast.Return))


# --- the enumeration --------------------------------------------------------

def test_every_published_module_resolves_to_a_file_on_disk():
    """The walk's own smoke test: a wrong path would make every assertion
    below vacuously true, since ``ast.parse`` would never see the code."""
    modules = published_modules()
    assert modules, "no published modules — the enumeration would be vacuous"
    for name, path in modules.items():
        assert path.is_file(), f"pages.{name} does not resolve to {path}"


def test_the_walk_finds_the_command_sites_that_are_there():
    """Non-vacuity for the gate test: at least one published page really does
    send a command, so a walk that silently matched nothing would fail here."""
    found = {name: sorted(_enclosing_functions(tree))
             for name, tree in _trees().items()}
    assert any(found.values()), "no command site found in any published page"
    for name, path in published_modules().items():
        literal = path.read_text(encoding="utf-8").count("bus_client.request(")
        assert len(_enqueue_calls(ast.parse(path.read_text(encoding="utf-8")))) \
            >= literal


def test_every_command_a_published_page_can_send_is_gated_on_the_origin():
    """THE TEST. Enumerated from the published table, never from page names."""
    for name, tree in sorted(_trees().items()):
        for fn_name, fn in sorted(_enclosing_functions(tree).items()):
            assert _guarded(fn), (
                f"pages/{name.replace('.', '/')}.py::{fn_name}() (line "
                f"{fn.lineno}) puts a command on a cmd: stream without first "
                "checking _may_enqueue. That page is published unauthenticated "
                "on live.neuralstrike.co.")


def test_a_published_page_reaches_the_bus_only_through_the_module():
    """The half a method-name walk cannot do.

    ``from bus_client import request as _req`` makes the call a bare name — an
    ``ast.Name``, indistinguishable from any other one-word call — so no
    enumeration over ``.request`` attributes can see it. The fix is to make the
    spelling unavailable rather than to try to recognise it."""
    bad = {}
    for name, tree in _trees().items():
        lines = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom)
                 and (n.module or "").split(".")[-1] == "bus_client"]
        if lines:
            bad[name] = lines
    assert bad == {}, (
        f"{bad} does `from bus_client import ...`. That binds a bus function to "
        "a bare name, which the enumeration above walks `.request` attributes "
        "and so cannot see. Use `import bus_client` and call "
        "`bus_client.request(...)`.")


# --- and the controls that would reach one ----------------------------------

@pytest.fixture
def published():
    """Render as the PUBLIC origin, then put the process back.

    Published WITH the real route map, exactly as ``live_main`` does — a
    routeless publish is a state that process never has, and a page rendered
    under it could navigate nowhere for a reason this test does not mean."""
    import shell
    shell.publish(live_screens.PUBLIC_ROUTES)
    yield
    shell.unpublish()


# The six screens whose page draws a Refresh button on the private app. Named
# only as the SUBJECT of the behavioural check below — the guarantee is the
# source enumeration above, which needs no list at all.
_REFRESH_SCREENS = ("sentiment", "bullbear", "sectors", "rotation", "rrg",
                    "momentum")


def _button_texts(screen):
    from nicegui import ui
    module = importlib.import_module(f"pages.{screen.module}")
    before = set(ui.context.client.elements)
    module.render(**screen.kwargs)
    return [str(e.text) for key, e in ui.context.client.elements.items()
            if key not in before and type(e).__name__ == "Button"]


def _screen(slug):
    return next(s for s in live_screens.SCREENS if s.slug == slug)


@pytest.mark.parametrize("slug", _REFRESH_SCREENS)
def test_the_private_render_still_draws_its_refresh_button(slug):
    """⚠ THE ONE THAT PROTECTS THE APP. Four of these screens are cached,
    manual-refresh-only — ``/sentiment/rotation`` and ``/sentiment/rrg``
    explicitly so — and a Refresh that vanished from the private app would
    strand them on whatever the service last published."""
    assert "Refresh" in _button_texts(_screen(slug))


@pytest.mark.parametrize("slug", _REFRESH_SCREENS)
def test_the_published_render_draws_no_refresh_button(slug, published):
    assert "Refresh" not in _button_texts(_screen(slug))


def test_the_published_render_keeps_the_controls_that_are_not_commands(published):
    """Not a blunt "hide every button": Expand all / Collapse are pure page
    state and are the only way to read the industries under a sector."""
    texts = _button_texts(_screen("sectors"))
    assert "Expand all" in texts and "Collapse" in texts
