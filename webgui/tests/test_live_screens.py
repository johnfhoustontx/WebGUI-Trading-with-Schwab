"""The published screen table. Pure data, so the route guard, the capture
script and the static grid all read ONE source."""
import ast
import importlib
import inspect
import pathlib

import pytest

FORBIDDEN = {"/terminate", "/settings", "/status", "/driver", "/manuals",
             "/options/paper", "/options/captured", "/options/portfolio",
             "/options/shares", "/eod", "/trade"}

_LIVE_SCREENS = pathlib.Path(__file__).resolve().parents[1] / "live_screens.py"


def test_there_are_exactly_fourteen_screens():
    import live_screens
    assert len(live_screens.SCREENS) == 14


def test_no_screen_publishes_a_control_surface():
    """THE GUARD THAT MATTERS. Every route here is served unauthenticated on a
    public origin, so a route added carelessly is a control surface on the
    internet -- /terminate stops the whole stack."""
    import live_screens
    routes = {s.route for s in live_screens.SCREENS}
    assert routes & FORBIDDEN == set(), f"published a control surface: {routes & FORBIDDEN}"


def test_routes_and_slugs_are_unique():
    """A duplicate slug would make two screens overwrite one capture file."""
    import live_screens
    routes = [s.route for s in live_screens.SCREENS]
    slugs = [s.slug for s in live_screens.SCREENS]
    assert len(set(routes)) == len(routes)
    assert len(set(slugs)) == len(slugs)


def test_every_route_starts_with_a_slash_and_every_slug_is_url_safe():
    import re
    import live_screens
    for s in live_screens.SCREENS:
        assert s.route.startswith("/")
        assert re.fullmatch(r"[a-z0-9-]+", s.slug), f"{s.slug} is not URL-safe"


def test_the_pins_name_real_settings_keys():
    """A typo'd pin key would silently do nothing and the screen would publish
    the wrong default."""
    import app_settings
    import live_screens
    for s in live_screens.SCREENS:
        for key in s.settings:
            assert key in app_settings.DEFAULTS, f"{s.slug} pins unknown key {key}"


def test_the_net_prem_pin_matches_the_requested_screen():
    """SPY, QQQ and BIG10 in Dollars. BIG10 is a SYMBOL inside the `indices`
    group in config/symbols.toml, not a group of its own."""
    import live_screens
    np = next(s for s in live_screens.SCREENS if s.slug == "net-premium")
    assert np.settings["gamma_netprem_group"] == "indices"
    assert np.settings["gamma_netprem_symbols"] == ["SPY", "QQQ", "BIG10"]
    assert np.settings["gamma_netprem_mode"] == "dollars"


def test_no_two_screens_pin_the_same_key_to_different_values():
    """SETTINGS_PINS is process-wide -- app_settings is a module singleton, not
    per-request state. Two screens disagreeing about a key would silently
    last-wins, and one of them would publish the wrong view."""
    import collections
    import live_screens
    seen = collections.defaultdict(set)
    for s in live_screens.SCREENS:
        for k, v in s.settings.items():
            seen[k].add(repr(v))
    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    assert clashes == {}, f"screens disagree on {clashes}"


def test_settings_pins_is_the_union_of_every_screen_s_pins():
    """Non-vacuity for the clash test above: the union is what the entrypoint
    freezes, so a pin that never reaches it is a screen publishing a default."""
    import live_screens
    assert live_screens.SETTINGS_PINS == {
        k: v for s in live_screens.SCREENS for k, v in s.settings.items()}
    assert live_screens.SETTINGS_PINS, "no pins at all - the freeze would be vacuous"


def test_every_module_resolves_and_accepts_its_kwargs():
    """A typo'd module path is a 500 on a PUBLIC page, and nothing else in this
    plan catches it -- the route registration only imports lazily at request
    time. Signature-checked rather than called: ``render()`` mounts widgets and
    needs a NiceGUI client."""
    import live_screens
    for s in live_screens.SCREENS:
        mod = importlib.import_module(f"pages.{s.module}")
        render = getattr(mod, "render", None)
        assert callable(render), f"pages.{s.module} has no render()"
        try:
            inspect.signature(render).bind(**s.kwargs)
        except TypeError as exc:            # pragma: no cover - the failure text
            pytest.fail(f"{s.slug}: pages.{s.module}.render{inspect.signature(render)} "
                        f"cannot take {s.kwargs}: {exc}")


def test_live_screens_imports_nothing_from_nicegui():
    """PURE DATA is the whole point: the capture script and the static-site
    build read this table, and neither should drag in the UI stack. Read as
    SOURCE, in the shape of ``test_shell_seam.test_no_page_imports_main``, so an
    import inside a function is caught too."""
    banned = {"nicegui", "pages", "main", "shell"}
    tree = ast.parse(_LIVE_SCREENS.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names
                    if a.name.split(".")[0] in banned]
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in banned:
            bad.append(node.module)
    assert bad == [], f"live_screens.py must stay pure data; it imports {bad}"
