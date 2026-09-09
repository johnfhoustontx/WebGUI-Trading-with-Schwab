"""The ASGI gate: one chokepoint in front of every route the app serves.

WHY A MIDDLEWARE AND NOT DECORATORS. The webgui is 34 NiceGUI pages, 9 raw
FastAPI routes and two static mounts. A decorator has to be remembered on each
of them, and the failure mode of forgetting one is an unauthenticated page that
looks exactly like an authenticated page to everyone except whoever finds it.
A middleware is default-deny by construction: a route added tomorrow is gated
because it exists, not because someone annotated it.

WHY PURE ASGI AND NOT ``BaseHTTPMiddleware``. ``BaseHTTPMiddleware`` only ever
sees ``http`` scopes. This is a websocket app -- every NiceGUI page runs over
``/_nicegui_ws/`` -- so gating only http would leave the socket that carries all
the actual interaction completely open. Writing the raw three-argument callable
is a few more lines and covers ``websocket`` and ``lifespan`` as first-class
cases instead of invisible ones. (It also sidesteps that class's known
interactions with streaming responses, which this app has.)

WHAT THE GATE ACCEPTS, precisely: a valid SESSION cookie, and nothing else.
Not the remember-device cookie -- see ``REMEMBER_COOKIE`` below, where the
reasoning is spelled out, because "or a valid remember cookie" is the natural
thing to write here and it silently undoes ``auth.py``'s kind discriminator.

Tier 1: stdlib plus starlette, which NiceGUI already brings. No ``services.*``,
no sqlite, no Schwab call.
"""
from __future__ import annotations

import http.cookies
import ipaddress
import logging
import urllib.parse

import auth
import auth_store
import login_page

log = logging.getLogger("webgui.auth")

# Set by Caddy with ``header_up``, which REPLACES any client-supplied value --
# that replacement is what makes the header trustworthy as a "this came from
# outside" marker. Read it as evidence of the edge, never as evidence of a
# client's identity.
EDGE_HEADER = "x-edge"

SESSION_COOKIE = "ns_session"

# NAMED HERE, HONOURED NOWHERE. The remember-device cookie is a 30-day
# credential that lives on disk, and its intended power is exactly one thing:
# letting the next sign-in skip the TOTP prompt (Task 10, on the login route).
# Admitting it here would make a stolen month-old cookie equivalent to a full
# session with neither the password nor the second factor -- promoting the
# weakest, longest-lived credential in the system into the strongest one, which
# is the very substitution ``auth.mint_token``'s ``kind`` discriminator was
# added to prevent. The constant exists so the login route and this module
# cannot disagree about the name.
REMEMBER_COOKIE = "ns_device"

# Two entries, and that is the whole list -- do not widen it without reading
# this. ``/login`` is a plain HTML document with no NiceGUI runtime, so nothing
# needs ``/_nicegui/*`` or ``/_nicegui_ws/`` before authentication. That is what
# makes the websocket a genuine boundary rather than defence in depth; opening
# either prefix here would quietly give it away. ``/favicon.ico`` is open
# because a browser requests it alongside the login page and a redirect there
# is noise, not protection.
OPEN_PATHS = frozenset({"/login", "/favicon.ico"})

# The kiosk surface. ``/wall`` frames the three real pages, and those pages need
# NiceGUI's runtime and static assets to work at all.
WALL_PATHS = frozenset({"/wall", "/desk", "/market", "/sentiment/momentum"})
WALL_PREFIXES = ("/_nicegui/", "/_nicegui_ws/", "/static/")

LOOPBACK = frozenset({"127.0.0.1", "::1"})

# RFC 6455 1008: the connection is refused on policy grounds. Not 1000 (normal
# closure), which would tell a client its socket ended cleanly and invite the
# reconnect loop socket.io runs by default.
WS_CLOSE_POLICY_VIOLATION = 1008


# ---------------------------------------------------------------------------
# The credential providers.
#
# ``session_key`` and ``epoch`` are CALLABLES rather than values, and that is
# load-bearing rather than stylistic: the gate re-reads them on every request,
# so bumping the epoch ("sign out everywhere") kills every outstanding cookie
# immediately instead of at the next service restart. Frozen at construction,
# revocation would need a restart -- and a revocation you have to schedule is
# not a revocation.
#
# Both default to reading ``auth_store``. Each does its own ``load()``, so the
# two values can in principle straddle a concurrent rewrite of the file and
# produce (old key, new epoch). That combination REFUSES, the user retries, and
# the next request is consistent -- a transient fail-closed, which is the right
# side to land on. Sharing one read to close it would mean caching, and a cached
# epoch is exactly the restart-to-revoke problem in a different spelling.


def _credentials() -> auth_store.Credentials | None:
    """The store, or None. NEVER raises, and never falls back to a default.

    ``auth_store.load`` returns None for "nothing configured yet" and RAISES for
    "the file is corrupt" -- deliberately, so a corrupt file cannot be read as
    "no password set". Both arrive here as None, because from the gate's side
    they are the same fact: there is no credential to check anything against.
    They are not the same OPERATIONALLY, so the log line says which.
    """
    try:
        return auth_store.load()
    except auth_store.CredentialsError as exc:
        log.warning("Credentials file %s cannot be read, so every request is "
                    "refused: %s", auth_store.DEFAULT_PATH, exc)
        return None


def default_session_key() -> str | None:
    creds = _credentials()
    return creds.session_secret if creds else None


def default_epoch() -> int | None:
    creds = _credentials()
    return creds.epoch if creds else None


# ---------------------------------------------------------------------------
# Scope reading.

def _is_loopback(host: object) -> bool:
    """True only for an address the kernel could not have routed off the box.

    Parsed rather than string-matched so ``::ffff:127.0.0.1`` and the rest of
    ``127.0.0.0/8`` (systemd-resolved sits on 127.0.0.53) are recognised. An
    unparseable value -- ``TestClient``'s default literal ``"testclient"``, or a
    unix-socket peer -- is NOT loopback: unknown means refused.
    """
    if not isinstance(host, str):
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host in LOOPBACK
    mapped = getattr(addr, "ipv4_mapped", None)
    return (mapped or addr).is_loopback


def _peer(scope) -> str | None:
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        return client[0]
    return None


def _header(scope, name: str) -> str | None:
    """First value of a header, decoded. ASGI gives lowercase byte names."""
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or ():
        if key == wanted:
            return value.decode("latin-1", "replace")
    return None


def _cookie(scope, name: str) -> str | None:
    """One cookie out of the raw header, tolerating anything a client sends.

    ``SimpleCookie.load`` skips morsels it cannot parse rather than raising, but
    it is not documented to be total over arbitrary bytes, and this input is
    fully attacker-controlled on a public endpoint -- where an exception is a
    500 on every page rather than a redirect to the login form. So the parse is
    guarded, and an unparseable cookie header means "no cookie".
    """
    raw = _header(scope, "cookie")
    if not raw:
        return None
    jar = http.cookies.SimpleCookie()
    try:
        jar.load(raw)
    except http.cookies.CookieError:
        return None
    morsel = jar.get(name)
    return morsel.value if morsel is not None else None


# ---------------------------------------------------------------------------

class AuthGate:
    """Refuse every request that is not the owner or the kiosk.

    The decision, in order -- and the order is the specification, not an
    implementation detail::

        scope is neither http nor websocket   -> pass (lifespan MUST pass, or
                                                 the app never boots)
        path in OPEN_PATHS                    -> pass
        no usable credentials                 -> REFUSE, before anything below
        the kiosk exemption, all 3 conditions -> pass
        a valid SESSION cookie                -> pass
        otherwise -> http: 303 /login?next=...  |  websocket: close 1008
    """

    def __init__(self, app, *, session_key=default_session_key,
                 epoch=default_epoch):
        self.app = app
        self._session_key = session_key
        self._epoch = epoch

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if self._allowed(scope):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close",
                        "code": WS_CLOSE_POLICY_VIOLATION})
        else:
            await self._redirect(scope, send)

    # -- the decision -----------------------------------------------------
    def _allowed(self, scope) -> bool:
        path = scope.get("path") or "/"
        if path in OPEN_PATHS:
            return True

        key, epoch = self._session_key(), self._epoch()
        # DEFAULT DENY, and note WHERE it sits: ahead of the kiosk exemption,
        # not only ahead of the cookie check. None means no credentials are
        # configured, or the file is corrupt. The friendly bug is to read that
        # as "nothing to check" and pass -- on a public hostname that is an open
        # door that looks exactly like the app working. An empty key is refused
        # for the same reason with one extra step: it is a usable HMAC key that
        # anyone else can use too.
        #
        # Putting it above the kiosk branch makes "an unconfigured app serves
        # nothing" TOTAL. The tempting alternative -- let the kiosk through,
        # since its exemption never rested on a credential -- would be a rule
        # with one exception, and an exception on a security boundary is a thing
        # to remember rather than a thing that holds. The cost is that the wall
        # is blank until setup, which is the correct thing for it to be.
        if not key or epoch is None:
            return False

        if self._is_kiosk(scope, path):
            return True
        return self._has_session(scope, key, epoch)

    @staticmethod
    def _is_kiosk(scope, path: str) -> bool:
        """ALL THREE conditions, and each one alone would be a bypass.

        1. The peer is loopback. The kiosk Chrome runs ON the box. If the bind
           is ever widened by accident, or a port forwarded, an outside client
           presents its own address here and can never reach this branch.
        2. No ``X-Edge`` header. Caddy sets it with ``header_up``, which
           replaces whatever the client sent -- so a request that came through
           the edge cannot claim to be the kiosk. This is the converse of (1)
           and covers the case (1) cannot: a proxy on the box IS loopback.
        3. The path is one the wall actually renders. Loopback is not a licence
           to reach the whole app: ``/settings`` can rotate the credentials and
           ``/terminate`` stops the stack, so the grant is scoped to the three
           framed pages and the runtime they need.

        ⚠ A ``..`` SEGMENT DISQUALIFIES THE PATH ENTIRELY, before condition 3 is
        asked. ``scope["path"]`` is percent-decoded but NOT normalised, so
        ``/static/../settings`` -- and its ``%2e%2e`` and ``..%2f`` spellings --
        genuinely starts with ``/static/`` and would take the prefix branch. It
        is not exploitable today (the router matches the same unnormalised path,
        so it lands on ``StaticFiles``, which refuses traversal itself), but
        that leaves this module's scoping claim resting on two other
        components' behaviour. Condition 3 says the grant is scoped to what the
        wall renders; this is what makes that sentence true here rather than
        true by luck. Nothing legitimate is refused -- a browser folds ``..``
        during URL resolution, before the request line is written.
        """
        if not _is_loopback(_peer(scope)):
            return False
        if _header(scope, EDGE_HEADER) is not None:
            return False
        if ".." in path.split("/"):
            return False
        return path in WALL_PATHS or path.startswith(WALL_PREFIXES)

    @staticmethod
    def _has_session(scope, key: str, epoch: int) -> bool:
        """A valid session cookie -- not a remember cookie, not a form token.

        The kind is passed explicitly on every call. ``auth.verify_token``
        refuses a payload whose kind differs, so a remember-device token pasted
        into the session slot fails here even though it is perfectly signed.
        """
        token = _cookie(scope, SESSION_COOKIE)
        if not token:
            return False
        return auth.verify_token(token, key, kind=auth.KIND_SESSION,
                                 epoch=epoch,
                                 max_age_sec=auth.SESSION_MAX_AGE_SEC)

    # -- the refusal ------------------------------------------------------
    @staticmethod
    async def _redirect(scope, send) -> None:
        """303 to the login form, carrying where the visitor was going.

        303 and not 307: a 307 replays the request METHOD and BODY at
        ``/login``, so an unauthenticated POST would arrive at the login handler
        as a POST carrying somebody else's form.

        ``next`` goes through ``login_page.safe_next`` -- the ONE validator for
        this value, deliberately not re-implemented here. The path comes from
        our own scope, but ``//evil.example/x`` is a request line a client can
        simply send, and this value lands in a ``Location:`` header where an
        offsite value is an open redirect and a newline is response splitting.
        """
        target = scope.get("path") or "/"
        query = scope.get("query_string") or b""
        if query:
            # ``surrogateescape`` BOTH WAYS, and the pair is the point. The
            # query string is raw bytes that need not be valid UTF-8; decoding
            # with ``replace`` would destroy them, and decoding with
            # ``latin-1`` then quoting as UTF-8 DOUBLE-encodes every byte above
            # 0x7f (0xc3 -> "Ã" -> "%C3%83"), so the user lands somewhere other
            # than where they asked. Surrogates round-trip the original bytes
            # exactly, and cannot raise on the way back out.
            target = f"{target}?{query.decode('utf-8', 'surrogateescape')}"
        nxt = urllib.parse.quote(login_page.safe_next(target), safe="",
                                 encoding="utf-8", errors="surrogateescape")
        location = f"/login?next={nxt}"

        await send({
            "type": "http.response.start",
            "status": 303,
            "headers": [
                (b"location", location.encode("latin-1")),
                (b"content-length", b"0"),
                # An unauthenticated redirect is per-visitor and must never be
                # held by a shared cache, or one user's ``next`` is served to
                # the next.
                (b"cache-control", b"no-store"),
            ],
        })
        await send({"type": "http.response.body", "body": b""})
