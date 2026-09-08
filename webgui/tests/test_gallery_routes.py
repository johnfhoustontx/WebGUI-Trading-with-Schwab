"""Every route in ``tools/gallery_screens.py`` is one this app really serves.

⚠ WHY THIS TEST IS NOT IN ``tools/tests/`` WITH THE REST OF THAT TABLE.
It needs ``import main``, which needs ``webgui/`` on ``sys.path`` -- and
``tools/tests/test_capture_live_shots.py`` records, at length, why that
directory is not put there from a tools test: ``webgui/`` holds top-level
modules named ``main``, ``proxy``, ``auth`` and ``wall``, and the root suites run
as ONE pytest session (``tests deploy tools/tests shared/tests``), so the entry
would outlive this file and can shadow a same-named module elsewhere. That test
solves its own version of the problem by loading ``live_screens.py`` by path,
which works only because that file imports nothing. ``main.py`` imports NiceGUI
and every page module, so there is no by-path trick available; the import has to
happen where it is already routine. Hence: the table's shape is pinned beside
the table, and the half that needs the app is pinned inside the app.

The failure this catches is silent by construction. A mis-mapped route captures
and publishes a real, correct-looking screenshot of the WRONG page -- nothing
raises, nothing 404s if the route merely belongs to a different screen. It has
already happened once in this design: "Where the Market Stands" was assumed to be
a variant of ``/sentiment`` and is in fact ``/sentiment/bullbear``.
"""
from tools import gallery_screens as g


def _main_page_routes():
    """The routes ``main`` itself registered.

    Attribution by ``__module__`` rather than the whole registry: NiceGUI's
    ``Client.page_routes`` is a process GLOBAL that ``webgui/live_main.py``
    writes into as well, and whether that module has been imported depends on
    which other test ran first. Without the filter, a route served ONLY by the
    public origin would pass here on some orderings and fail on others.
    """
    import main  # noqa: F401 -- importing registers the @_page routes
    from nicegui import Client
    return {route for fn, route in Client.page_routes.items()
            if getattr(fn, "__module__", "") == "main"}


def test_every_route_is_one_the_app_actually_registers():
    registered = _main_page_routes()
    for s in g.SCREENS:
        for shot in s.shots:
            path = shot.route.split("?")[0]
            assert path in registered, f"{s.title}: {path} is not a registered route"


def test_a_query_string_is_only_ever_a_pin_the_page_can_take():
    """``?view=`` on gamma is INTENDED, not yet built -- but it must be buildable.

    A query the page cannot honour is the same silent failure as a wrong route,
    one step later: the capture navigates, the parameter is ignored, and the
    default view is published under someone else's caption. Checked against the
    ``@_page`` function's own signature, which is where a pin becomes real --
    ``/sentiment/momentum?level=`` already passes on that alone.
    """
    import inspect
    from urllib.parse import parse_qs, urlsplit
    from nicegui import Client

    import main  # noqa: F401 -- registers the @_page routes
    from pages.options import gamma

    builders = {route: fn for fn, route in Client.page_routes.items()
                if getattr(fn, "__module__", "") == "main"}
    for s in g.SCREENS:
        for shot in s.shots:
            query = urlsplit(shot.route).query
            if not query:
                continue
            path = shot.route.split("?")[0]
            accepted = set(inspect.signature(builders[path]).parameters)
            if path == "/options/gamma":
                # ``gamma.render()`` takes ``view`` already; wiring the route
                # parameter through ``@_page`` is a later task. Until then the
                # render target is what can honour the pin. This clause becomes
                # redundant the moment that lands -- the union above absorbs it.
                accepted |= set(inspect.signature(gamma.render).parameters)
            assert set(parse_qs(query)) <= accepted, \
                f"{s.title}: {shot.route} names a pin {path} cannot take"


def test_a_subtab_shot_names_a_subtab_that_exists():
    """The Simulator's three shots differ ONLY by subtab, and it takes no param.

    ``simulator.render()`` has no arguments -- the active tab lives in page
    state, so the capture has to click. That makes the ``subtab`` string the
    only thing separating three otherwise identical rows, and a typo in it would
    hand the capture tool a label it can never find, or find nothing wrong with
    and capture the default Replay tab three times.
    """
    import pathlib
    subtabbed = [(s, sh) for s in g.SCREENS for sh in s.shots if sh.subtab]
    assert subtabbed, "the Simulator shots lost their subtab labels"
    # Pinned so the hardcoded source file below cannot quietly become the wrong
    # one: a subtab shot on some OTHER page would be checked against the
    # Simulator's tabs and pass or fail for no reason connected to it.
    for s, sh in subtabbed:
        assert sh.route == "/options/simulator", \
            f"{s.title}: {sh.route} carries a subtab this test cannot check"
    source = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options" / "simulator.py"
    text = source.read_text(encoding="utf-8")
    for _s, sh in subtabbed:
        assert f'ui.tab("{sh.subtab}")' in text, f"{sh.subtab!r} is not a Simulator subtab"
