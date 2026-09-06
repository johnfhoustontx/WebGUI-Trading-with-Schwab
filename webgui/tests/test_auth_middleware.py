"""The gate, driven through a real ASGI app rather than by calling helpers.

A consumer-side guard proves nothing until a test drives it the way production
does -- this repo has paid for that lesson more than once (see CLAUDE.md on the
signal_band and ADX incidents). So every test here goes through ``TestClient``:
a real scope, real headers, a real cookie jar, and a real peer address.

THE PEER ADDRESS IS SET EXPLICITLY, and that is the single most important line
of setup in the file. ``TestClient``'s default peer is the literal string
``("testclient", 50000)``, which is NOT loopback -- so a fixture that leaves it
alone would exercise the "remote client" branch while reading as though it
tested the kiosk. Every fixture below names the peer it means.
"""
import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

import auth
import auth_middleware
import auth_store

KEY = "k" * 43
LOOPBACK_PEER = ("127.0.0.1", 51234)
REMOTE_PEER = ("203.0.113.9", 51234)

WS_PATH = "/_nicegui_ws/socket.io/"


def _app(**gate_kwargs):
    app = FastAPI()

    @app.get("/desk")
    def desk():
        return PlainTextResponse("desk")

    @app.get("/wall")
    def wall():
        return PlainTextResponse("wall")

    @app.get("/login")
    def login():
        return PlainTextResponse("login")

    # A real websocket endpoint, so "the socket was closed" can only mean the
    # GATE closed it. Without one, a middleware that passed the scope straight
    # through would still fail to connect -- and the test would pass for the
    # wrong reason, which is the failure mode this file exists to avoid.
    @app.websocket(WS_PATH)
    async def socket(ws: WebSocket):
        await ws.accept()
        await ws.send_text("open")
        await ws.close()

    app.add_middleware(auth_middleware.AuthGate, **gate_kwargs)
    return app


def _client(app, peer=LOOPBACK_PEER):
    return TestClient(app, base_url="http://testserver", client=peer)


@pytest.fixture
def client():
    """The kiosk's view: a loopback peer, exactly like Chrome on the box."""
    return _client(_app(session_key=lambda: KEY, epoch=lambda: 1))


@pytest.fixture
def remote_client():
    """A peer that is not loopback -- what an outside client would present if
    the bind were ever widened, or a port forwarded."""
    return _client(_app(session_key=lambda: KEY, epoch=lambda: 1),
                   peer=REMOTE_PEER)


@pytest.fixture
def client_no_creds(monkeypatch):
    """No ``session_key``/``epoch`` overrides: the gate uses its own providers,
    which read ``auth_store``. Here the store says nothing is configured."""
    monkeypatch.setattr(auth_store, "load", lambda path=None: None)
    return _client(_app())


@pytest.fixture
def client_corrupt_creds(monkeypatch):
    def _boom(path=None):
        raise auth_store.CredentialsError("truncated file")

    monkeypatch.setattr(auth_store, "load", _boom)
    return _client(_app())


def _edge(extra=None):
    h = {auth_middleware.EDGE_HEADER: "1"}
    h.update(extra or {})
    return h


def _session_cookie(client, *, epoch=1, key=KEY):
    client.cookies.set(auth_middleware.SESSION_COOKIE,
                       auth.mint_token(key, kind=auth.KIND_SESSION, epoch=epoch))


# --- the ordinary path ------------------------------------------------------

def test_an_unauthenticated_request_through_the_edge_is_redirected(client):
    r = client.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=")


def test_the_login_page_itself_is_open(client):
    assert client.get("/login", headers=_edge()).status_code == 200


def test_a_valid_session_cookie_passes(client):
    _session_cookie(client)
    assert client.get("/desk", headers=_edge()).status_code == 200


def test_a_token_from_a_previous_epoch_does_not_pass(client):
    _session_cookie(client, epoch=0)
    r = client.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


def test_bumping_the_epoch_kills_a_cookie_already_in_flight():
    """"Sign out everywhere" must not need a restart.

    This is why ``epoch`` is a CALLABLE and not an int: the gate re-reads it per
    request. Frozen at construction, the bump would take effect only on the next
    service start -- and the whole point of revoking is that it is immediate.
    """
    live = {"epoch": 1}
    client = _client(_app(session_key=lambda: KEY,
                          epoch=lambda: live["epoch"]))
    _session_cookie(client, epoch=1)
    assert client.get("/desk", headers=_edge()).status_code == 200

    live["epoch"] = 2
    r = client.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


def test_the_session_key_is_reread_per_request():
    """Same reasoning as the epoch, on the other callable."""
    live = {"key": KEY}
    client = _client(_app(session_key=lambda: live["key"], epoch=lambda: 1))
    _session_cookie(client)
    assert client.get("/desk", headers=_edge()).status_code == 200

    live["key"] = "j" * 43
    r = client.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


def test_a_tampered_cookie_is_refused_rather_than_raising(client):
    client.cookies.set(auth_middleware.SESSION_COOKIE, "not-a-token")
    r = client.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


# --- the remember-device cookie authorises NOTHING on its own ---------------

def test_a_remember_device_cookie_alone_does_not_admit(client):
    """A 30-day cookie is not a session, and the gate must not treat it as one.

    ``auth.py`` gave the two tokens distinct kinds precisely so a stolen
    remember-device cookie could not be replayed in the session slot. Accepting
    it HERE would re-open that hole from the other end: the kind check would
    still pass on its own terms while the gate handed over a full session with
    no password and no code. What the remember cookie is for is skipping the
    TOTP prompt at the next login -- a Task 10 concern, on the login route.
    """
    client.cookies.set(auth_middleware.REMEMBER_COOKIE,
                       auth.mint_token(KEY, kind=auth.KIND_REMEMBER, epoch=1))
    r = client.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


def test_even_a_session_kind_token_in_the_remember_slot_does_not_admit(client):
    """THE DISCRIMINATING ONE -- the test above passes for the wrong reason.

    Mutation-tested: rewrite the gate to fall back to the remember slot
    (``_cookie(SESSION) or _cookie(REMEMBER)``) and every other test in this
    file still passes, because ``auth.verify_token`` refuses the mismatched
    KIND and quietly does the gate's job for it. That is defence in depth
    working, not the gate being right -- and it would evaporate the moment
    someone wrote the natural-looking "...or a valid remember-device cookie"
    with the matching kind.

    So this one puts a perfectly valid SESSION token in the remember slot. The
    kind check cannot save it; only the gate reading one cookie name can.
    """
    client.cookies.set(auth_middleware.REMEMBER_COOKIE,
                       auth.mint_token(KEY, kind=auth.KIND_SESSION, epoch=1))
    r = client.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


def test_a_remember_token_placed_in_the_session_cookie_does_not_admit(client):
    """The other half: right slot, wrong kind."""
    client.cookies.set(auth_middleware.SESSION_COOKIE,
                       auth.mint_token(KEY, kind=auth.KIND_REMEMBER, epoch=1))
    r = client.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


def test_a_form_token_is_not_a_session(client):
    """The third kind, minted by an UNAUTHENTICATED GET /login. If the gate
    accepted it, fetching the login page would BE the login."""
    import login_page
    client.cookies.set(auth_middleware.SESSION_COOKIE,
                       login_page.mint_form_token(KEY, epoch=1))
    r = client.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


# --- the wall exemption: BOTH conditions, and the negatives that matter -----

def test_the_kiosk_reaches_the_wall_from_loopback_without_the_edge_header(client):
    r = client.get("/wall")
    assert r.status_code == 200


def test_the_kiosk_reaches_the_iframed_pages_too(client):
    """/wall's three iframes are the real /desk, /market and /sentiment/momentum."""
    assert client.get("/desk").status_code == 200


def test_the_wall_is_refused_when_the_request_came_through_the_edge(client):
    """Condition 2. Caddy 404s /wall, but the app must not depend on that."""
    r = client.get("/wall", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


def test_the_wall_is_refused_from_a_non_loopback_peer(remote_client):
    """Condition 1. If the bind is ever widened, the exemption must not be a
    bypass -- an outside client's peer address is its own IP, so it can never
    take this branch however its headers are shaped."""
    r = remote_client.get("/wall", follow_redirects=False)
    assert r.status_code == 303


def test_a_path_outside_the_wall_set_is_gated_even_for_the_kiosk(client):
    """Condition 3. The exemption is scoped to what the wall actually renders;
    loopback alone is not a licence to reach the whole app unauthenticated.

    /settings can rotate credentials and /terminate stops the stack, so "any
    local process may do anything" is exactly the grant not to hand out.
    """
    r = client.get("/settings", follow_redirects=False)
    assert r.status_code == 303


def test_a_wall_prefix_is_matched_as_a_prefix_not_a_substring(client):
    """``/evil/static/x`` contains "/static/" but does not start with it."""
    r = client.get("/evil/static/x", follow_redirects=False)
    assert r.status_code == 303


# --- default deny -----------------------------------------------------------

def test_an_app_with_no_credentials_configured_refuses_a_valid_looking_cookie(
        client_no_creds):
    """`auth_store.load()` returns None for BOTH 'no file yet' and 'not
    configured', so the fail-closed duty lands here, on the caller.

    The tempting bug is the friendly one: treat "no password set" as "nothing to
    check" and let requests through, so first-boot is easy. On a public hostname
    that is an open door, and it would look like the app simply working.

    The cookie matters. Asserting only that a BARE request is redirected would
    pass against a gate with no credential check at all -- it has no cookie to
    accept either way. Presenting a well-formed session token is what makes this
    test discriminate between "refuses" and "had nothing to admit".
    """
    _session_cookie(client_no_creds)
    r = client_no_creds.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303, "an unconfigured app must refuse, not admit"


def test_an_app_with_no_credentials_configured_refuses_the_kiosk_path_too(
        client_no_creds):
    """Not even the wall. An unconfigured app has no notion of who anyone is."""
    assert client_no_creds.get("/wall", follow_redirects=False).status_code == 303


def test_a_corrupt_credentials_file_refuses_rather_than_500(
        client_corrupt_creds):
    """``auth_store.load`` RAISES on a file it cannot parse -- deliberately, so
    a corrupt file cannot default to "no password". An unhandled raise inside
    the gate would be a 500 on every page, which is at least loud; the danger is
    the opposite reflex, catching it into a pass. It refuses."""
    _session_cookie(client_corrupt_creds)
    r = client_corrupt_creds.get("/desk", headers=_edge(),
                                 follow_redirects=False)
    assert r.status_code == 303


def test_an_empty_session_secret_is_not_a_usable_key(client_no_creds):
    """An empty string is falsy, and a key of "" would verify tokens anyone can
    mint. Same refusal as no credentials at all."""
    empty = _client(_app(session_key=lambda: "", epoch=lambda: 1))
    empty.cookies.set(auth_middleware.SESSION_COOKIE,
                      auth.mint_token("", kind=auth.KIND_SESSION, epoch=1))
    r = empty.get("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


# --- the redirect ------------------------------------------------------------

def test_the_redirect_preserves_the_path_and_query(client):
    r = client.get("/options/gamma?symbol=SPY", headers=_edge(),
                   follow_redirects=False)
    assert r.headers["location"] == "/login?next=%2Foptions%2Fgamma%3Fsymbol%3DSPY"


@pytest.mark.parametrize("path, expected", [
    ("//evil.example/x", "/login?next=%2Fdesk"),   # protocol-relative
    (r"/\evil.example/x", "/login?next=%2Fdesk"),  # browsers fold \ to /
    ("/desk\n", "/login?next=%2Fdesk"),            # response splitting
    ("/desk", "/login?next=%2Fdesk"),              # the ordinary case
])
def test_the_redirect_never_carries_an_offsite_or_forged_next(path, expected):
    """``next`` lands in a ``Location:`` header, and ``login_page.safe_next`` is
    the ONE validator for that value. Do not grow a second one here.

    DRIVEN THROUGH A HAND-BUILT SCOPE, not ``TestClient``. No HTTP client will
    send these: httpx resolves ``//evil.example/x`` against the base URL and
    turns it into a request for a different HOST with path ``/x``, so the test
    would silently assert something else entirely. A real client on the wire can
    put any of these on the request line, and starlette hands them through to
    ``scope["path"]`` verbatim -- so the scope is where they have to be injected.
    """
    assert _location(path) == expected


def _location(path: str, query: bytes = b"") -> str:
    """Run one unauthenticated GET through the gate and read ``Location:``."""
    import asyncio

    sent = []

    async def app(scope, receive, send):        # never reached
        raise AssertionError("the gate should have refused")

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    gate = auth_middleware.AuthGate(app, session_key=lambda: KEY,
                                    epoch=lambda: 1)
    asyncio.run(gate({"type": "http", "path": path, "query_string": query,
                      "headers": [(b"x-edge", b"1")],
                      "client": REMOTE_PEER}, receive, send))
    headers = dict(sent[0]["headers"])
    return headers[b"location"].decode()


def test_a_non_utf8_query_string_round_trips_byte_for_byte():
    """A query string is bytes and need not be valid UTF-8.

    Decoding it as latin-1 and re-quoting as UTF-8 double-encodes every byte
    above 0x7f, so the post-login landing would be a path the user never asked
    for; decoding with ``replace`` loses the bytes outright. ``%FF`` is the same
    single byte that went in.
    """
    assert _location("/desk", query=b"q=\xff") == "/login?next=%2Fdesk%3Fq%3D%FF"


def test_the_redirect_is_not_cacheable():
    """A shared cache holding this would serve one visitor's ``next`` to the
    next visitor."""
    import asyncio

    sent = []

    async def app(scope, receive, send):
        raise AssertionError("the gate should have refused")

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    gate = auth_middleware.AuthGate(app, session_key=lambda: KEY,
                                    epoch=lambda: 1)
    asyncio.run(gate({"type": "http", "path": "/desk", "query_string": b"",
                      "headers": [(b"x-edge", b"1")],
                      "client": REMOTE_PEER}, receive, send))
    assert dict(sent[0]["headers"])[b"cache-control"] == b"no-store"


def test_the_redirect_is_303_so_a_post_becomes_a_get(client):
    """A 307 would replay the POST body at /login."""
    r = client.post("/desk", headers=_edge(), follow_redirects=False)
    assert r.status_code == 303


# --- websockets --------------------------------------------------------------

def test_an_unauthenticated_websocket_is_closed(client):
    """The socket is the boundary, not defence in depth: nothing needs
    ``/_nicegui_ws/`` before login, because /login is a plain HTML form."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(WS_PATH, headers=_edge()):
            pass


def test_a_websocket_from_a_non_loopback_peer_is_closed(remote_client):
    with pytest.raises(WebSocketDisconnect):
        with remote_client.websocket_connect(WS_PATH):
            pass


def test_an_authenticated_websocket_connects(client):
    _session_cookie(client)
    with client.websocket_connect(WS_PATH, headers=_edge()) as ws:
        assert ws.receive_text() == "open"


def test_the_kiosk_websocket_connects(client):
    """The wall's iframes are real NiceGUI pages, so the exemption has to cover
    the socket they run on -- otherwise the three panels render once and freeze."""
    with client.websocket_connect(WS_PATH) as ws:
        assert ws.receive_text() == "open"


def test_the_websocket_close_code_is_policy_violation(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(WS_PATH, headers=_edge()):
            pass
    assert exc.value.code == auth_middleware.WS_CLOSE_POLICY_VIOLATION


# --- everything else passes through -----------------------------------------

def test_a_lifespan_scope_passes_through_untouched():
    """A gate that answered ``lifespan`` would stop the app booting -- and the
    symptom is a hang at startup, not a test failure."""
    app = _app(session_key=lambda: KEY, epoch=lambda: 1)
    with _client(app):          # entering the context runs the lifespan
        pass


def test_an_unknown_scope_type_is_passed_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    gate = auth_middleware.AuthGate(inner, session_key=lambda: KEY,
                                    epoch=lambda: 1)

    import asyncio

    async def noop(*_a, **_k):
        return {}

    asyncio.run(gate({"type": "something_new"}, noop, noop))
    assert seen == ["something_new"]
