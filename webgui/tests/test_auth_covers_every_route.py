"""Every route this app serves is either documented-open or refuses a stranger.

THIS IS THE ONE TEST IN THE FEATURE THAT CATCHES A MISTAKE NOBODY HAS MADE YET.
Every other auth test pins a decision someone already took; this one fails when
route #44 is added and its author never thought about authentication at all. A
per-route test cannot do that, because the new route has no test. Same shape,
and the same reason, as ``test_no_inline_style.py``.

**It drives the REAL ``main.app``.** Not a stand-in FastAPI app with three
hand-written endpoints -- ``test_auth_middleware.py`` already does that, and it
is the right shape for asserting the gate's *logic*. What it cannot see is
whether the gate is MOUNTED on the app that ships. This repo has paid twice for
a consumer-side guard that passed while the producer never emitted the shape
being tested (``signal_band``, and the ADX characterization test), so the
enumeration and the requests both come from the object ``ui.run`` serves.

**The fixture configures real credentials, and that is load-bearing.** The gate
is default-deny: with no credentials file it refuses EVERYTHING, so a run
against an unconfigured checkout would go green while proving nothing at all --
every route refused because the app is broken, not because it is gated. The
fixture therefore writes a real credentials file, one test asserts the gate can
see it, and another drives a gated route WITH a valid session and demands the
handler's own answer. Between them, a refusal below can only mean the gate.
"""
import pytest
from starlette.testclient import TestClient

import auth
import auth_middleware
import auth_store
import login_page
import main
import wall

PASSWORD = "hunter2"
SECRET = "JBSWY3DPEHPK3PXP" * 2      # 32 base32 chars, well over the 16 floor
KEY = "k" * 43
EPOCH = 1


# The whole open list, and it is deliberately tiny. ``/favicon.ico`` is in the
# gate's ``OPEN_PATHS`` but is NOT a route this app registers (a browser asks
# for it beside the login page and NiceGUI answers it from its own bundle), so
# it cannot appear in the enumeration below and is named separately.
#
# ⚠ Adding an entry here is a security decision, not test maintenance. Anything
# listed is reachable by anyone on the internet who knows the hostname. If a new
# route needs to be open, the argument belongs in ``auth_middleware.OPEN_PATHS``
# next to the two that are already there -- this constant only mirrors it.
DOCUMENTED_OPEN = frozenset({"/login"})

UNROUTED_OPEN_PATHS = frozenset({"/favicon.ico"})

# A refusal is 303 to the login form, and ONLY that -- deliberately narrower
# than "some 3xx". Measured while writing this: a bare GET of the Starlette
# mount ``/_nicegui_ws`` answers 307 (the mount's own redirect to
# ``/_nicegui_ws/``), so a check that accepted any redirect passed that route
# without the gate having said anything at all. Two paths were green for that
# reason before this was tightened.
REFUSAL_STATUS = 303
LOGIN_LOCATION_PREFIX = "/login?next="


def _refusal(response) -> bool:
    return (response.status_code == REFUSAL_STATUS
            and response.headers.get("location", "").startswith(
                LOGIN_LOCATION_PREFIX))


def _all_routes():
    """Every concrete path registered on the real app.

    Parameterised paths (``/static/{path:path}``) are skipped: there is no one
    URL to request, and the prefix they serve is covered by the gate's
    ``WALL_PREFIXES`` tests in ``test_auth_middleware.py``. Mounts are NOT
    skipped -- ``/_nicegui_ws`` is a concrete path and an ungated websocket
    mount is exactly the thing this file exists to notice.
    """
    return {p for r in main.app.routes
            if (p := getattr(r, "path", None)) and "{" not in p}


@pytest.fixture
def configured_credentials(tmp_path, monkeypatch):
    """A real credentials file the gate's DEFAULT providers will find.

    ``auth_store.DEFAULT_PATH`` is patched rather than a key being injected into
    the gate, because Task 10 mounts ``AuthGate`` with its default providers and
    a test that injected its own would stop exercising the wiring that ships.
    """
    creds = auth_store.Credentials(
        password_hash=auth.hash_password(PASSWORD),
        totp_secret=SECRET,
        session_secret=KEY,
        epoch=EPOCH,
        last_totp_counter=0,
    )
    path = tmp_path / "webgui_auth.json"
    auth_store.save(creds, path)
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)
    return creds


@pytest.fixture
def client_unauthenticated(configured_credentials):
    """A stranger: through the edge, with no cookies of any kind.

    Two things make it a stranger rather than the kiosk, and both matter.
    ``TestClient``'s default peer is the literal string ``"testclient"``, which
    is not loopback -- and the ``X-Edge`` header says the request came through
    Caddy, which the kiosk's never does. Either alone closes the wall
    exemption; both together mean a pass here can only be a real one.

    ``raise_server_exceptions=False`` so a handler that blows up is recorded as
    a 500 -- an offence, since 500 is not a refusal -- instead of aborting the
    sweep at whichever route happened to be first alphabetically.
    """
    return TestClient(main.app, base_url="https://testserver",
                      headers={auth_middleware.EDGE_HEADER: "1"},
                      raise_server_exceptions=False)


@pytest.fixture
def client_signed_in(configured_credentials):
    client = TestClient(main.app, base_url="https://testserver",
                        headers={auth_middleware.EDGE_HEADER: "1"})
    client.cookies.set(auth_middleware.SESSION_COOKIE,
                       auth.mint_token(KEY, kind=auth.KIND_SESSION,
                                       epoch=EPOCH))
    return client


# --- the guard itself -------------------------------------------------------

def test_every_route_is_documented_open_or_refuses_an_unauthenticated_caller(
        client_unauthenticated):
    """THE GUARD. Add a route without an auth decision and this names it.

    Every offender is collected rather than asserted one at a time, so the
    failure lists the whole set instead of whichever path sorted first -- when
    this goes red it is usually one new route, and reading the name is the
    entire value of the test.
    """
    offenders = []
    for path in sorted(_all_routes()):
        if path in DOCUMENTED_OPEN:
            continue
        r = client_unauthenticated.get(path, follow_redirects=False)
        if not _refusal(r):
            offenders.append(
                f"{path} -> {r.status_code} {r.headers.get('location', '')}".strip())
    assert not offenders, (
        "these routes answered an unauthenticated caller instead of refusing "
        "-- either gate them, or add them to DOCUMENTED_OPEN and to "
        f"auth_middleware.OPEN_PATHS with the reason: {offenders}")


def test_a_refusal_is_a_redirect_to_the_login_form(client_unauthenticated):
    """The refusal has to be USABLE, not merely a non-200.

    A gate that answered 403 would satisfy the guard above and leave the owner
    staring at an error page with no way to sign in.
    """
    r = client_unauthenticated.get("/desk", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=%2Fdesk"


def test_the_documented_open_list_is_what_the_gate_actually_opens():
    """This file's list mirrors the gate's; it must not drift into fiction.

    A path documented open here but absent from ``OPEN_PATHS`` would be
    SKIPPED by the guard above while the gate refused it -- a hole in the
    coverage that looks like coverage.
    """
    assert DOCUMENTED_OPEN <= set(auth_middleware.OPEN_PATHS)


def test_the_gate_opens_nothing_this_app_does_not_serve():
    """The other direction: every open path is a route, or a named exception.

    An ``OPEN_PATHS`` entry matching no route is either a typo (so the page it
    meant to open is gated and unreachable) or a leftover from a route that has
    since been renamed -- and a stale open path is a hole waiting for someone to
    register that name again.
    """
    assert set(auth_middleware.OPEN_PATHS) - _all_routes() == UNROUTED_OPEN_PATHS


# --- the two tests that stop the guard passing for the wrong reason ---------

def test_the_fixture_really_configures_the_gate(configured_credentials):
    """Without this, an unconfigured checkout makes the guard vacuously green.

    The gate is default-deny: with no credentials file every route refuses,
    which is the correct behaviour and a worthless test result -- the guard
    would be green on an app whose gate had no idea who anyone is. So assert
    the providers can see a usable key before believing any refusal below.
    """
    assert auth_middleware.default_session_key() == KEY
    assert auth_middleware.default_epoch() == EPOCH


def test_a_valid_session_reaches_a_gated_route(client_signed_in):
    """THE DISCRIMINATING ONE -- the guard alone cannot tell gated from broken.

    ``/manuals/file`` is chosen because it is a raw route with a cheap, certain
    answer of its own: an unknown manual name is its own 404. A 404 here means
    the request reached the handler, so the 303s above are the gate refusing
    and not the app failing.
    """
    r = client_signed_in.get("/manuals/file?name=__no_such_manual__",
                             follow_redirects=False)
    assert r.status_code == 404
    assert "Unknown manual" in r.text


def test_the_login_page_is_reachable_and_is_a_plain_form(client_unauthenticated):
    """The one open route has to actually work, or the gate locks everyone out.

    It also has to stay a plain HTML form: a NiceGUI login would drag
    ``/_nicegui_ws/`` into the open list and stop the websocket being a real
    boundary.
    """
    r = client_unauthenticated.get("/login")
    assert r.status_code == 200
    assert "<form" in r.text and 'method="post"' in r.text
    assert "_nicegui" not in r.text


# --- the wall mirror --------------------------------------------------------

def test_wall_paths_mirror_what_the_wall_actually_frames():
    """``WALL_PATHS`` is a hand-copied mirror of ``wall.PAGES``, and nothing
    else notices when the two drift.

    Both directions are failures, and neither is loud on its own. Rotate a new
    dashboard into the wall and forget this set, and that panel renders a
    redirect to the login form -- on a public YouTube broadcast, discovered by
    viewers. Drop a page from the wall and leave it here, and a route stays
    reachable unauthenticated from the box for no reason anyone remembers.
    """
    framed = {wall.PAGE_ROUTE} | {p["path"] for p in wall.PAGES}
    assert set(auth_middleware.WALL_PATHS) == framed


def test_every_framed_wall_page_is_a_route_the_app_serves():
    """A wall panel pointed at a path nothing registers is a 404 on camera."""
    assert {p["path"] for p in wall.PAGES} <= _all_routes()


# --- the third token kind must not be a session ----------------------------

def test_a_form_token_from_the_live_login_page_is_not_a_session(
        client_unauthenticated, configured_credentials):
    """End-to-end version of ``test_auth_middleware``'s unit-level check.

    ``GET /login`` mints a signed token and hands it to an ANONYMOUS visitor. If
    the gate ever accepted that kind, fetching the login page would be the
    login. Driving it through the real route is what makes this discriminating:
    the token is the one the app actually issues, not one the test minted.
    """
    client_unauthenticated.get("/login")
    token = login_page.issue_form_token()
    assert token, "GET /login must issue a form token"
    client_unauthenticated.cookies.set(auth_middleware.SESSION_COOKIE, token)
    r = client_unauthenticated.get("/desk", follow_redirects=False)
    assert r.status_code == 303
