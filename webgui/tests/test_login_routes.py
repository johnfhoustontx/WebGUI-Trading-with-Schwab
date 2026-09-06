"""The three wired routes: ``GET /login``, ``POST /login``, ``GET /logout``.

Driven through the REAL ``main.app`` for the same reason
``test_auth_covers_every_route.py`` is: ``login_page`` is already tested to
death as pure logic, and none of that says whether the handler reads the field
the form emits, sets the cookie the gate reads, or survives a second import.

**Everything here runs over ``https://testserver``.** The cookies are ``Secure``
unconditionally -- deliberately, because behind Caddy this app sees plain HTTP
on loopback, so a scheme-derived flag would look right in every local test and
ship Secure-less cookies to production. An httpx client will not send a Secure
cookie back over ``http://``, so the round-trip tests use https rather than
weakening the flag to suit the test client.
"""
import re

import pyotp
import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

import auth
import auth_middleware
import auth_store
import login_page
import main

PASSWORD = "hunter2"
SECRET = "JBSWY3DPEHPK3PXP" * 2
KEY = "k" * 43
EPOCH = 1

BASE = "https://testserver"
EDGE = {auth_middleware.EDGE_HEADER: "1"}

_TOKEN_RE = re.compile(
    rf'name="{login_page.FIELD_FORM_TOKEN}" value="([^"]*)"')


@pytest.fixture(autouse=True)
def _fresh_lockout():
    """The lockout counter is module state shared by every test in the process.

    Without the reset, a file that deliberately fails a few sign-ins leaks that
    into whatever runs next -- and at 50 global failures in fifteen minutes it
    would eventually lock this file out of its own fixtures.
    """
    login_page.reset_lockout()
    yield
    login_page.reset_lockout()


@pytest.fixture
def creds(tmp_path, monkeypatch):
    c = auth_store.Credentials(
        password_hash=auth.hash_password(PASSWORD),
        totp_secret=SECRET,
        session_secret=KEY,
        epoch=EPOCH,
        last_totp_counter=0,
    )
    path = tmp_path / "webgui_auth.json"
    auth_store.save(c, path)
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)
    return c


@pytest.fixture
def client(creds):
    return TestClient(main.app, base_url=BASE, headers=EDGE)


def _form_token(html: str) -> str:
    m = _TOKEN_RE.search(html)
    assert m, "the rendered form carries no form token"
    return m.group(1)


def _post(client, *, password=PASSWORD, code=None, token=None, next_path=None,
          remember=False):
    """A sign-in POST shaped exactly like the browser's, fields and all."""
    data = {
        login_page.FIELD_PASSWORD: password,
        login_page.FIELD_CODE: pyotp.TOTP(SECRET).now() if code is None else code,
        login_page.FIELD_FORM_TOKEN: (login_page.issue_form_token()
                                      if token is None else token),
    }
    if next_path is not None:
        data[login_page.FIELD_NEXT] = next_path
    if remember:
        data[login_page.FIELD_REMEMBER] = login_page.REMEMBER_ON
    return client.post(login_page.ROUTE, data=data, follow_redirects=False)


# --- GET /login --------------------------------------------------------------

def test_the_form_renders_with_a_token_and_is_not_cacheable(client):
    r = client.get(login_page.ROUTE)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert login_page.verify_form_token(_form_token(r.text), creds_of())


def creds_of():
    """The store as the routes see it -- read fresh, never captured."""
    return auth_store.load()


def test_an_offsite_next_never_reaches_the_form(client):
    """``safe_next`` is the one validator, and the form escapes what it is given.

    Both have their own unit tests; this asserts the ROUTE actually calls them.
    """
    r = client.get(f"{login_page.ROUTE}?next=//evil.example/x")
    assert f'name="{login_page.FIELD_NEXT}" value="{login_page.DEFAULT_NEXT}"' in r.text
    assert "evil.example" not in r.text


def test_the_form_renders_even_with_no_credentials_configured(
        tmp_path, monkeypatch):
    """An unconfigured app must show a login page that refuses, not a traceback.

    ``issue_form_token`` returns None here, ``render_form`` accepts None, and
    the POST then refuses. A 500 on the one public URL would be worse in every
    way -- including as a fingerprint.
    """
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", tmp_path / "absent.json")
    r = TestClient(main.app, base_url=BASE, headers=EDGE).get(login_page.ROUTE)
    assert r.status_code == 200
    assert "<form" in r.text


# --- POST /login: the cookies ------------------------------------------------

def test_cookies_are_host_only_secure_httponly_and_lax(client):
    """Host-only is the one that matters and the one most easily lost.

    A ``Domain=`` cookie is sent to neuralstrike.co and EVERY subdomain,
    forever -- so the session that arms the trading driver would travel to the
    public marketing page, alongside its YouTube and Discord embeds, on every
    page view. One attribute undoes the whole origin split.
    """
    r = _post(client, remember=True)
    assert r.status_code == 303
    raws = r.headers.get_list("set-cookie")
    assert len(raws) == 2, f"expected both cookies, got {raws}"
    for raw in raws:
        assert "domain=" not in raw.lower(), f"cookie is not host-only: {raw}"
        assert "httponly" in raw.lower(), raw
        assert "samesite=lax" in raw.lower(), raw
        assert "secure" in raw.lower(), raw


def test_the_logout_cookie_clears_carry_the_same_attributes(client):
    """A delete that does not MATCH the cookie's attributes does not delete it.

    Path in particular: a clear written without ``path=/`` leaves the original
    cookie sitting there, and "sign out" silently does nothing.
    """
    _post(client, remember=True)        # /logout is gated -- sign in first
    raws = client.get(login_page.LOGOUT_ROUTE,
                      follow_redirects=False).headers.get_list("set-cookie")
    assert len(raws) == 2
    for raw in raws:
        assert "domain=" not in raw.lower(), raw
        assert "path=/" in raw.lower(), raw
        assert "httponly" in raw.lower() and "secure" in raw.lower(), raw


# --- POST /login: the outcome ------------------------------------------------

def test_a_correct_sign_in_redirects_and_the_session_reaches_a_gated_route(client):
    r = _post(client, next_path="/options/gamma?symbol=SPY")
    assert r.status_code == 303
    assert r.headers["location"] == "/options/gamma?symbol=SPY"
    assert client.cookies.get(auth_middleware.SESSION_COOKIE)

    # The discriminating half: the cookie the route set must satisfy the gate.
    # ``/manuals/file`` is a raw route with a cheap answer of its own, so a 404
    # means the request reached the handler rather than the login redirect.
    after = client.get("/manuals/file?name=__no_such_manual__",
                       follow_redirects=False)
    assert after.status_code == 404


def test_a_wrong_password_re_renders_the_form_with_the_generic_message(client):
    r = _post(client, password="wrong")
    assert r.status_code == 200
    assert login_page.GENERIC_FAILURE in r.text
    assert not r.headers.get_list("set-cookie")


def test_the_re_rendered_form_carries_a_TOKEN_THAT_STILL_WORKS(client):
    """A stale token would make the SECOND attempt fail for the wrong reason.

    Asserting the token merely differs would be weaker and flakier (two mints in
    the same millisecond can collide); the property that matters is that the
    form handed back after a failure can actually be submitted, so this drives
    a real retry through it.
    """
    failed = _post(client, password="wrong")
    retry = _post(client, token=_form_token(failed.text))
    assert retry.status_code == 303, "the retry form's token was not usable"


def test_a_blind_post_with_no_form_token_is_refused(client):
    """Design mitigation 5, end to end. Most stuffing bots never fetch the form."""
    r = _post(client, token="")
    assert r.status_code == 200 and login_page.GENERIC_FAILURE in r.text


def test_a_malformed_body_is_a_refusal_not_a_500(client):
    """The body and its content type are attacker-controlled on a public URL.

    A parse that escaped would be a 500 on every malformed POST -- and a
    cheaper, louder thing to send us than a well-formed one.
    """
    r = client.post(login_page.ROUTE, content=b"\xff\xfe not a form",
                    headers={"content-type": "multipart/form-data; boundary=x"},
                    follow_redirects=False)
    assert r.status_code == 200 and login_page.GENERIC_FAILURE in r.text


def test_a_successful_sign_in_never_lands_on_logout(client):
    """The gate refuses a signed-out ``GET /logout`` with ``?next=/logout``.

    Honouring that would sign the user in and immediately back out, dropping
    them at the form they had just completed with no error and nothing to do
    differently. ``/logout`` is a safe site-relative path, so ``safe_next``
    cannot be the thing that catches it.
    """
    r = _post(client, next_path=login_page.LOGOUT_ROUTE)
    assert r.status_code == 303
    assert r.headers["location"] == login_page.DEFAULT_NEXT


def test_an_offsite_next_is_not_honoured_on_the_post_either(client):
    r = _post(client, next_path="https://evil.example/x")
    assert r.headers["location"] == login_page.DEFAULT_NEXT


# --- the remember-device flow, both directions ------------------------------

def test_the_remember_cookie_is_set_only_when_the_box_is_ticked(client):
    r = _post(client, remember=False)
    assert r.status_code == 303
    assert client.cookies.get(auth_middleware.REMEMBER_COOKIE) is None


def test_a_trusted_device_signs_in_with_the_password_and_no_code(client):
    """THE POINT OF THE COOKIE, and its entire power."""
    client.cookies.set(auth_middleware.REMEMBER_COOKIE,
                       login_page.mint_remember_token(KEY, epoch=EPOCH))
    r = _post(client, code="")
    assert r.status_code == 303
    assert client.cookies.get(auth_middleware.SESSION_COOKIE)


def test_a_trusted_device_still_needs_the_RIGHT_PASSWORD(client):
    """THE DISCRIMINATING ONE. The cookie waives the second factor, never the
    first -- otherwise a stolen month-old cookie plus a guessed username-less
    form is a full session."""
    client.cookies.set(auth_middleware.REMEMBER_COOKIE,
                       login_page.mint_remember_token(KEY, epoch=EPOCH))
    r = _post(client, password="wrong", code="")
    assert r.status_code == 200 and login_page.GENERIC_FAILURE in r.text
    assert client.cookies.get(auth_middleware.SESSION_COOKIE) is None


@pytest.mark.parametrize("cookie, why", [
    ("not-a-token", "forged"),
    (auth.mint_token("z" * 43, kind=auth.KIND_REMEMBER, epoch=EPOCH),
     "signed with another key"),
    (login_page.mint_remember_token(KEY, epoch=EPOCH + 1), "a bumped epoch"),
    (auth.mint_token(KEY, kind=auth.KIND_SESSION, epoch=EPOCH),
     "the wrong KIND -- a session token in the remember slot"),
])
def test_an_unusable_remember_cookie_still_demands_the_code(client, cookie, why):
    client.cookies.set(auth_middleware.REMEMBER_COOKIE, cookie)
    r = _post(client, code="")
    assert r.status_code == 200, f"{why} was accepted as a trusted device"


def test_an_expired_remember_cookie_still_demands_the_code(client):
    import time
    stale = login_page.mint_remember_token(
        KEY, epoch=EPOCH, now=time.time() - auth.REMEMBER_MAX_AGE_SEC - 60)
    client.cookies.set(auth_middleware.REMEMBER_COOKIE, stale)
    assert _post(client, code="").status_code == 200


def test_a_remember_cookie_alone_admits_nothing_at_the_gate(client):
    """End-to-end mirror of the middleware unit test, on the real app.

    Its power is skipping the TOTP prompt at the next sign-in and nothing else.
    Admitting it here would promote the weakest, longest-lived, on-disk
    credential in the system into the strongest one.
    """
    client.cookies.set(auth_middleware.REMEMBER_COOKIE,
                       login_page.mint_remember_token(KEY, epoch=EPOCH))
    r = client.get("/manuals/file?name=__no_such_manual__",
                   follow_redirects=False)
    assert r.status_code == 303


# --- GET /logout -------------------------------------------------------------

def test_logout_clears_both_cookies_and_returns_to_the_form(client):
    _post(client, remember=True)
    assert client.cookies.get(auth_middleware.SESSION_COOKIE)
    assert client.cookies.get(auth_middleware.REMEMBER_COOKIE)

    r = client.get(login_page.LOGOUT_ROUTE, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == login_page.ROUTE
    assert client.cookies.get(auth_middleware.SESSION_COOKIE) is None
    assert client.cookies.get(auth_middleware.REMEMBER_COOKIE) is None, (
        "signing out left the device trusted -- on a borrowed machine that is "
        "most of what 'sign out' was supposed to mean")


def test_logout_is_gated_like_everything_else(client):
    """Not in ``OPEN_PATHS``: clearing a cookie nobody presented is a no-op, and
    the gate already sends a signed-out visitor where this route would."""
    assert login_page.LOGOUT_ROUTE not in auth_middleware.OPEN_PATHS
    r = TestClient(main.app, base_url=BASE, headers=EDGE).get(
        login_page.LOGOUT_ROUTE, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=")


# --- the client address the lockout counts against --------------------------

def _request(*, peer, headers):
    return Request({"type": "http", "path": "/login", "method": "POST",
                    "client": peer,
                    "headers": [(k.lower().encode(), v.encode())
                                for k, v in headers.items()]})


def test_the_client_is_the_peer_when_the_request_did_not_come_through_caddy():
    assert main._client_ip(_request(peer=("100.64.0.7", 5), headers={})) == "100.64.0.7"


def test_a_forwarded_for_header_is_ignored_without_the_edge_header():
    """No ``X-Edge`` means nothing wrote that header on our behalf, so it is
    just a string the client sent."""
    r = _request(peer=("100.64.0.7", 5), headers={"x-forwarded-for": "9.9.9.9"})
    assert main._client_ip(r) == "100.64.0.7"


def test_behind_the_edge_the_client_is_the_LAST_forwarded_hop():
    """Behind Caddy every peer is 127.0.0.1, so the peer would file the whole
    internet under one address -- and the per-client penalty ramps to 900 s
    where the global one is deliberately 60 s. Any bot spraying the advertised
    hostname would lock the owner out of the UI that stops the stack.

    The LAST entry, never the first: Caddy APPENDS the address it observed, so
    the tail is the one hop we wrote. A client can lengthen the list in front of
    it and cannot change it.
    """
    r = _request(peer=("127.0.0.1", 5),
                 headers={auth_middleware.EDGE_HEADER: "1",
                          "x-forwarded-for": "1.1.1.1, 203.0.113.9"})
    assert main._client_ip(r) == "203.0.113.9"


def test_a_spoofed_forwarded_for_cannot_choose_its_own_bucket():
    """The header a client sends is the PREFIX; ours is appended after it."""
    r = _request(peer=("127.0.0.1", 5),
                 headers={auth_middleware.EDGE_HEADER: "1",
                          "x-forwarded-for": "8.8.8.8"})
    assert main._client_ip(r) == "8.8.8.8"      # single hop: ours IS the tail

    r2 = _request(peer=("127.0.0.1", 5),
                  headers={auth_middleware.EDGE_HEADER: "1",
                           "x-forwarded-for": "8.8.8.8, 203.0.113.9"})
    assert main._client_ip(r2) == "203.0.113.9"


def test_an_edge_request_with_no_forwarded_for_degrades_to_the_peer():
    """Blunt but safe: one bucket, rather than trusting a header nobody wrote."""
    r = _request(peer=("127.0.0.1", 5),
                 headers={auth_middleware.EDGE_HEADER: "1"})
    assert main._client_ip(r) == "127.0.0.1"


# --- the gate is mounted once, and mounting is idempotent -------------------

def test_the_gate_is_mounted_on_the_app_that_ships(client):
    names = [getattr(m, "cls", None).__name__ for m in main.app.user_middleware
             if getattr(m, "cls", None) is not None]
    assert names.count(auth_middleware.AuthGate.__name__) == 1


def test_installing_the_gate_again_is_a_no_op_rather_than_a_500(client):
    """⚠ THE TRAP THIS GUARDS. Pages ``import main`` lazily at request time, and
    because ``main.py`` runs as ``__main__`` in production that re-executes the
    file as a second module object -- AFTER NiceGUI has started. Starlette's
    ``add_middleware`` raises once the middleware stack is built, so an
    unguarded module-scope call would 500 every page that does the lazy import.
    Exactly the hazard the ``app.on_startup`` calls are already inside the
    ``__main__`` guard for.

    The request first is deliberate: it forces the stack to be built, which is
    the state in which the naive version raises.
    """
    client.get(login_page.ROUTE)
    assert main.install_auth_gate() is False
    names = [getattr(m, "cls", None).__name__ for m in main.app.user_middleware
             if getattr(m, "cls", None) is not None]
    assert names.count(auth_middleware.AuthGate.__name__) == 1
