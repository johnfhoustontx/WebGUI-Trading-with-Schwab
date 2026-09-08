"""The public live entrypoint — ``webgui/live_main.py``.

⚠ ORDERING TRAP. ``Client.page_routes`` is a NiceGUI GLOBAL. main.py and
live_main.py both register ``/desk`` and ``/sentiment``, so in one pytest
process the registry holds both sets and an absolute assertion would be
meaningless. Every route test here therefore imports ``main`` FIRST, so the
registry always holds both, and attributes each registration to the module that
DEFINED it -- see ``_live_builders``, which records why a before/after diff of
that registry is the wrong instrument here.

⚠ THESE TESTS MUTATE PROCESS-WIDE STATE, AND IMPORTING THE SUBJECT IS WHAT DOES
IT. ``live_main``'s module body calls ``bus_client.set_read_only(True)``,
``bus_client.set_url(...)`` and ``app_settings.freeze(...)`` — that IS the
entrypoint, and driving the tests from the entrypoint rather than from the
primitives is the whole point (CLAUDE.md's ``signal_band`` incident is the
standing example of a consumer-side assertion that passed for weeks against a
payload the producer never wrote). None of it can be undone by unimporting, so
these live in their own file behind a module-scoped teardown. Measured with that
teardown removed: 11 tests fail across four unrelated modules
(``test_settings``, ``test_options_simulator``, ``test_options_calculator_apply``,
``test_sentiment_bullbear``) -- the frozen store swallows their writes and the
read-only bus refuses their commands.

The ONE leak the teardown cannot reverse is the fourteen routes this import
adds to the shared ``nicegui.app`` — see ``test_auth_covers_every_route.py``,
which excludes them for exactly that reason.
"""
import ast
import inspect
import pathlib
import subprocess
import sys

import pytest

import app_settings
import bus_client

_LIVE_MAIN = pathlib.Path(__file__).resolve().parents[1] / "live_main.py"

# Control surfaces that must not exist in the public process. Mirrors (a subset
# of) tests/test_live_screens.FORBIDDEN — asserted again HERE because that file
# guards the table while this one guards what was actually registered.
FORBIDDEN_ROUTES = ("/terminate", "/settings", "/status", "/driver", "/manuals",
                    "/options/paper", "/options/captured", "/eod", "/logout")


@pytest.fixture(autouse=True, scope="module")
def _restore_process_state():
    """Undo everything importing the entrypoint installs, for the rest of the run."""
    yield
    import shell
    bus_client.set_read_only(False)
    bus_client.set_url(None)
    bus_client.reset()
    app_settings.unfreeze()
    app_settings.reset_cache()
    shell.unpublish()


def _live_builders():
    """``{route: page function}`` for the routes ``live_main`` registered.

    ⚠ NOT a before/after diff of the registry, for two measured reasons.
    ``Client.page_routes`` is ``{function: route}``, so a diff of route VALUES
    collapses ``/desk`` and ``/sentiment`` into the entries ``main`` already
    holds and reports them MISSING — that form agrees with the published table
    only when live_main has failed to register those two. And any diff at all is
    one-shot: ``import live_main`` runs the module body once, so the second test
    to ask sees no new registrations and an empty answer.

    Attribution by ``__module__`` is exact, order-independent, and says the thing
    the tests actually mean: this function was defined by the public entrypoint.
    ``main`` is imported first regardless, so the registry always holds both sets
    and no test can pass merely because the app's own routes were absent."""
    import main            # noqa: F401 -- imported FIRST, deliberately: see docstring
    import live_main       # noqa: F401 -- importing installs the refusals
    from nicegui import Client
    return {route: fn for fn, route in Client.page_routes.items()
            if getattr(fn, "__module__", "") == "live_main"}


def _live_routes():
    return set(_live_builders())


# --- the route set ----------------------------------------------------------

def test_it_registers_every_published_screen_and_nothing_else():
    import live_screens
    new = _live_routes()
    expected = {s.route for s in live_screens.SCREENS}
    assert new == expected, f"unexpected: {new - expected}; missing: {expected - new}"


def test_it_registers_no_control_surface():
    """Non-vacuity for the test above: name the routes that would be a disaster.

    The set-equality assertion already covers this, but it fails as a diff of
    two sets and the reader has to work out which member matters. This one names
    the answer."""
    new = _live_routes()
    for route in FORBIDDEN_ROUTES:
        assert route not in new, f"the public process registered {route}"


# --- the guard that keeps /terminate off the public origin ------------------

def test_live_main_does_not_import_main():
    """THE GUARD.

    Source-level, because a behavioural test would pass in a process where main
    happened to be imported already — which is exactly the case in this suite."""
    tree = ast.parse(_LIVE_MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name == "main" for a in node.names), \
                "live_main imports main — that publishes /terminate and /settings"
        if isinstance(node, ast.ImportFrom):
            assert node.module != "main", \
                "live_main imports from main — that publishes /terminate and /settings"


def test_live_main_calls_none_of_the_sync_helpers():
    """``main``'s three ``sync_*`` helpers are cross-process WRITERS, not readers.

    Each reads ``app_settings.get(...)`` and then ``bus_client.request(...)``s the
    value to a Tier-2 service. Against a FROZEN store they would read the pinned
    default and re-assert it to the shared services — ``ticker_enabled`` defaults
    True, so the public process would re-enable the ~20-minute PAID Claude
    verdict the owner may have deliberately switched off, and
    ``captured_autoclose_enabled`` would re-arm auto-close on the paper book.

    Source-level and by NAME PREFIX, so a copy grown here "for symmetry" is
    caught as surely as a call. ``set_read_only`` would in fact refuse them; the
    refusal is the backstop, not the design."""
    tree = ast.parse(_LIVE_MAIN.read_text(encoding="utf-8"))
    called = [n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
              for n in ast.walk(tree)
              if isinstance(n, ast.Call)
              and isinstance(n.func, (ast.Attribute, ast.Name))]
    offenders = [name for name in called if name.startswith("sync_")]
    defined = [n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name.startswith("sync_")]
    assert offenders == [] and defined == [], (
        f"live_main must not call or define a sync_* helper: "
        f"called={offenders} defined={defined}")


def test_a_real_public_process_holds_no_route_of_the_apps():
    """THE PRODUCTION PROPERTY, in a process that looks like production.

    The two guards above are proxies: one says this file does not import
    ``main``, the other (``test_shell_seam``) says no page does. Neither can see
    a THIRD path -- a helper this module reaches that imports the app, today or
    later. In this suite that is unobservable, because every test process has
    already imported ``main`` for its own reasons.

    So run the entrypoint alone, the way the systemd unit does, and ask the
    interpreter. It enumerates the whole ASGI app, not just the page registry,
    so a raw ``@app.get`` reached transitively is caught as surely as a page. A
    pass means the fourteen routes really are all that exist on the public
    origin; a failure means ``/terminate`` is on the internet.
    """
    probe = (
        "import importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('live_main', sys.argv[1]);"
        "m = importlib.util.module_from_spec(spec); sys.modules['live_main'] = m;"
        "spec.loader.exec_module(m);"
        "from nicegui import app;"
        "print('main' in sys.modules);"
        "print(sorted(p for r in app.routes if (p := getattr(r, 'path', None))))"
    )
    out = subprocess.run([sys.executable, "-c", probe, str(_LIVE_MAIN)],
                         capture_output=True, text=True, timeout=180,
                         cwd=str(_LIVE_MAIN.parent.parent))
    assert out.returncode == 0, f"the public entrypoint did not import: {out.stderr}"
    imported_main, routes = out.stdout.strip().splitlines()[-2:]
    assert imported_main == "False", (
        "importing live_main pulled main in transitively -- every @_page route, "
        "/terminate included, is now registered in the public process")

    import live_screens
    published = {s.route for s in live_screens.SCREENS}
    # NiceGUI's own machinery (``/_nicegui/<ver>/...``, the websocket mount) is
    # the framework, not this app's surface. Everything else must be published.
    served = {p for p in ast.literal_eval(routes) if not p.startswith("/_nicegui")}
    assert served == published, (
        f"the public process serves {sorted(served - published)} beyond the "
        f"published screens; missing {sorted(published - served)}")


# --- the read-only layers, driven from the ENTRYPOINT -----------------------

def test_it_puts_the_bus_in_read_only_mode_and_freezes_settings():
    """⚠ Driven by IMPORTING the entrypoint, not by calling the primitives.

    A consumer-side assertion never driven from the producer proves nothing."""
    import live_main       # noqa: F401 -- importing installs the refusals

    assert bus_client.is_read_only()
    assert app_settings.is_frozen()
    with pytest.raises(PermissionError):
        bus_client.request("options", {"type": "gamma_analyze"})


def test_it_declares_itself_the_public_origin_to_the_shell():
    """The layer a PAGE can act on.

    ``bus_client``'s refusal makes an enqueue impossible; it cannot make a
    button not be drawn, and a drawn one costs a full traceback in journald per
    anonymous click (``ui_guard.guard`` re-raises anything that is not the
    deleted-slot error). ``shell.publish()`` is what lets a page decline.

    ⚠ Driven from the ENTRYPOINT. A test calling ``shell.publish()`` itself
    would prove the pages read the flag and nothing about whether the live
    process ever sets it — the same consumer-side hole the ``signal_band``
    incident is the standing example of."""
    import live_main       # noqa: F401 -- importing installs the refusals
    import shell

    assert shell.is_public() is True
    assert shell.may_enqueue() is False


def test_the_frozen_settings_carry_every_screen_pin():
    import live_main       # noqa: F401
    import live_screens
    assert live_screens.SETTINGS_PINS, "no pins — this test would be vacuous"
    for key, value in live_screens.SETTINGS_PINS.items():
        assert app_settings.get(key) == value


def test_a_frozen_store_refuses_a_write():
    """The pins have to be pins, not merely initial values: every published page
    still runs the REAL page module, and a page that writes a setting would
    otherwise persist a public visitor's choice into the owner's settings.json."""
    import live_main       # noqa: F401
    app_settings.set("macro_skin", "A")
    assert app_settings.get("macro_skin") == "B", \
        "app_settings.set() wrote through the freeze"


# --- the per-route binding, and what a visitor cannot reach -----------------

def test_each_route_renders_its_OWN_screen(monkeypatch):
    """THE LOOP-VARIABLE TRAP.

    A closure over the ``for`` variable makes all fourteen routes render the
    LAST screen — a bug that reads as "the site works" right up until you click
    a second tile, because the first one you open is usually the Desk and the
    last screen is a real page that renders fine. Every builder is driven here
    and the screen it asks for is recorded."""
    import live_main
    import live_screens

    seen = []
    monkeypatch.setattr(live_main, "_render", lambda s: seen.append(s))
    builders = _live_builders()
    for route in sorted(builders):
        builders[route]()

    assert [s.route for s in seen] == sorted(builders), \
        "a route rendered a screen that is not its own"
    assert len({id(s) for s in seen}) == len(live_screens.SCREENS), \
        "two routes share one Screen object — the loop variable leaked"


def test_no_published_route_takes_a_request_parameter():
    """⚠ A page-function parameter is a QUERY PARAMETER a stranger can set.

    Measured, not assumed: NiceGUI hands the page function's signature to
    FastAPI, so ``def _page(_s=screen)`` — the obvious per-iteration binding —
    makes ``_s`` settable, and ``GET /desk?_s=anything`` replaced the Screen with
    the string ``'anything'``. On this origin that reaches ``_render``, whose
    first act is ``importlib.import_module(f"pages.{screen.module}")``.

    The binding is done by ``_register``'s own parameter instead, which leaves
    the signature empty and nothing to inject."""
    for route, fn in sorted(_live_builders().items()):
        params = inspect.signature(fn).parameters
        assert not params, (
            f"{route} takes {list(params)} — a public query parameter. Bind the "
            "screen through _register's argument, not the page signature.")


# --- what a published page renders inside ------------------------------------

def test_the_public_render_injects_the_page_level_css(monkeypatch):
    """A published screen renders the REAL page module, so it needs the CSS that
    module's own widgets depend on -- sticky Deep Slate table headers
    (/opportunity, /flow) and the ``.compact-subtabs`` pill row (/net-premium's
    group picker). Both used to be injected only by ``main._layout``, which this
    process may never import.

    Driven through ``_render`` rather than asserted as a substring of the file:
    a constant imported and never injected reads identically in the source."""
    import types

    import live_main
    import live_screens
    import shell

    class _Col:
        def classes(self, *a, **k):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    seen: list = []
    fake_ui = types.SimpleNamespace(
        add_css=seen.append,
        add_head_html=lambda _h: None,
        colors=lambda **_k: None,
        column=_Col)
    monkeypatch.setattr(live_main, "ui", fake_ui)
    monkeypatch.setattr(live_main, "importlib", types.SimpleNamespace(
        import_module=lambda _n: types.SimpleNamespace(render=lambda **_k: None)))

    board = next(s for s in live_screens.SCREENS if s.slug == "opportunity")
    live_main._render(board)

    assert shell.TABLE_CSS in seen,         "the published tables render without their sticky headers"
    assert shell.SUBTAB_CSS in seen,         "the published subtab rows render as stock Quasar tabs"


# --- the entrypoint's own shape ---------------------------------------------

def test_it_binds_loopback_only():
    """Caddy terminates TLS for LIVE_HOST and is the only thing that should ever
    talk to this port — the same rule the private app follows, and for the same
    reason. ⚠ Never widen this to a wildcard bind.

    Read off the ``ui.run`` CALL rather than as a substring of the file: the
    first draft of this test grepped for the wildcard and failed on the comment
    warning against it, which is a test that cannot distinguish the rule from a
    note about the rule."""
    tree = ast.parse(_LIVE_MAIN.read_text(encoding="utf-8"))
    hosts = [kw.value.value
             for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "run"
             for kw in node.keywords
             if kw.arg == "host" and isinstance(kw.value, ast.Constant)]
    assert hosts == ["127.0.0.1"], f"ui.run binds {hosts}, not loopback only"


def test_it_serves_the_live_port_not_the_app_port():
    """A copy-paste of ``NICEGUI_PORT`` would put the public process on the
    private app's port, where it would silently fail to bind (the app already
    holds it) and the OLD server would keep serving — the documented
    failed-bind-is-silent trap."""
    import repo_paths
    src = _LIVE_MAIN.read_text(encoding="utf-8")
    assert "NICEGUI_LIVE_PORT" in src
    assert "NICEGUI_PORT" not in src.replace("NICEGUI_LIVE_PORT", "")
    assert repo_paths.NICEGUI_LIVE_PORT != repo_paths.NICEGUI_PORT


# --- what a published PAGE must not do --------------------------------------
# ⚠ Driven by importing the entrypoint, so the real ``PUBLIC_PINS`` are what
# switch the feature off. A hand-set ``voice_enabled=False`` would pass just as
# happily against a table that had never pinned it.

def _desk_elements():
    """``render()``'s newly built elements, in build order."""
    from nicegui import ui
    from pages import desk

    before = set(ui.context.client.elements)
    desk.render()
    return [e for key, e in ui.context.client.elements.items() if key not in before]


def _unlock_buttons(elements):
    from pages import desk
    return [e for e in elements
            if str(getattr(e, "text", "")) == desk.VOICE_UNLOCK_LABEL]


def test_the_published_desk_builds_no_voice_unlock_button():
    """FINDING 1. ``voice_enabled`` is pinned False because ``webgui/voice.py``
    synthesizes through ``edge_tts`` — a network call to a Microsoft endpoint —
    and writes an mp3 this process does not even mount ``/voice`` to serve.

    The pin covered ``speak_phrases`` and ``_prewarm_clips`` and MISSED the
    third caller: ``_unlock_voice``, reachable from a browser console in two
    messages (``emitEvent('desk_voice_blocked')`` reveals the button, then a
    click). A control that cannot work must not be drawn."""
    import live_main       # noqa: F401 -- importing installs the pins
    assert _unlock_buttons(_desk_elements()) == []


def test_the_unlock_handler_refuses_even_if_the_button_is_reached(monkeypatch):
    """The other half: the handler refuses underneath.

    Not drawing the button is unreachable-by-construction only for as long as
    nothing else reveals it, and ``ui.on(VOICE_BLOCKED_EVENT, ...)`` is
    registered on the client LAYOUT — which is visible, so NiceGUI's
    hidden-element event gate does not apply. So the handler is captured from a
    render where voice is ON, and then driven with the origin's own pins.

    ``app_settings.load`` is monkeypatched rather than the store unfrozen: the
    freeze is installed by ``import live_main``, and a module already in
    ``sys.modules`` does not reinstall it, so an unfreeze here would disarm
    every other test in this file depending on the order they ran in."""
    import asyncio

    import app_settings
    import live_screens
    from pages import desk

    calls = []
    monkeypatch.setattr(desk._voice, "ensure",
                        lambda *a, **k: calls.append(a) or None)
    monkeypatch.setattr(desk._voice, "prewarm", lambda *a, **k: None)

    monkeypatch.setattr(app_settings, "load", lambda: {"voice_enabled": True})
    buttons = _unlock_buttons(_desk_elements())
    assert len(buttons) == 1, "voice on: the unlock button must still be built"
    # ⚠ NOT ``listener.handler``. ``Button.on_click`` registers a one-argument
    # lambda that hands the real callback to NiceGUI's ``handle_event``, which
    # SCHEDULES a coroutine on the running loop and returns None — so calling
    # the listener in a test runs nothing at all and every assertion after it
    # is vacuous. Measured: with the guard deleted, the version of this test
    # that drove ``listener.handler`` still passed. The page's own callback is
    # the first free variable of that lambda.
    wrapper = next(listener.handler
                   for listener in buttons[0]._event_listeners.values()
                   if listener.type == "click")
    names = wrapper.__code__.co_freevars
    handler = wrapper.__closure__[names.index("callback")].cell_contents
    assert handler.__name__ == "_unlock_voice", handler

    async def _no_thread(fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr(desk.run, "io_bound", _no_thread)
    # Now the origin's own pin, exactly as ``live_main`` applies it.
    monkeypatch.setattr(app_settings, "load",
                        lambda: dict(live_screens.SETTINGS_PINS))
    asyncio.run(handler())
    assert calls == [], "the unlock handler synthesized with voice pinned off"

    # Non-vacuity: the very same call DOES synthesize once voice is on again.
    monkeypatch.setattr(app_settings, "load", lambda: {"voice_enabled": True})
    asyncio.run(handler())
    assert calls, "the test never reached the synthesizer at all"
