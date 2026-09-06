"""The ``/login`` form and the attempt handler -- and, above all, THE ORDER.

Everything ``auth.py`` builds is inert until this module calls it in the right
sequence, and getting the sequence wrong fails silently: the login still works,
the suite still passes, and the protections become decoration. Four orderings
carry the whole task, each with a test named after it in
``tests/test_login_page.py``:

1. **``locked_until`` before Argon2.** A throttle placed behind the expensive
   thing it throttles is not a throttle -- an attacker still gets 19 MiB and
   ~24 ms (2-3x that on the VPS) out of us on every request, no matter how many
   times they have already failed. It is checked before the credentials file is
   even opened.
2. **The form token before Argon2.** Design mitigation 5, and the one this
   deployment specifically earns: the domain is advertised in a Discord and a
   Telegram, and the overwhelming majority of credential-stuffing bots POST
   blind at ``/login`` without ever fetching the form. Every one of those is now
   refused for the cost of an HMAC.
3. **``record_failure`` on EVERY failure path**, the cheap pre-hash refusals
   included. A path that returns without recording is a loop an attacker runs
   for free, and it is the cheap paths where a bare ``return`` gets added.
4. **``record_success`` last, and only after the counter has been persisted.**
   Miss the persist and ``auth.verify_totp``'s replay guard reads
   ``last_counter == 0`` forever, so a captured code stays replayable for its
   whole drift window -- with nothing on screen or in the log to say so.

**No HTTP here.** ``attempt`` returns a small result object and ``render_form``
returns a string; neither touches a ``Response``, a cookie or a header. That is
what makes the whole file testable without a server, and it is why the route
wiring lives in ``main.py`` instead.

**This is a standalone HTML document, deliberately outside the Tailwind-first
standard** -- the same documented out-of-scope category as the EOD reports, the
Gamma Explain infographics and ``/wall``. The palette is hand-written here
rather than read from ``theme.py`` on purpose: the login page must render for
somebody who is not yet authenticated, on a page that pulls in no NiceGUI
runtime, and adding a config read to that path buys nothing but a way for it to
fail.
"""
from __future__ import annotations

import dataclasses
import html
import logging
import re
import time

import auth
import auth_store

# A child of the "webgui" logger, which is where ``logging_setup`` attaches the
# rotating file handler and where an operator already looks. The suffix keeps
# login noise filterable on its own -- under a flood the lockout holds this to
# roughly GLOBAL_THRESHOLD lines a minute, which is bounded but not quiet.
log = logging.getLogger("webgui.login")


# The ONE sentence. It never distinguishes a bad password from a bad code, and
# it is equally the answer to a tripped lockout, a stale form token and a
# corrupt credentials file: a "too many attempts" variant would confirm to a
# scanner that a lockout exists and tell an attacker which of the three gates
# they hit.
GENERIC_FAILURE = "Sign-in failed."

DEFAULT_NEXT = "/desk"

# The route this module's form posts to, and the five field names it emits.
#
# Constants rather than literals in both places because the drift is SILENT: the
# route reads the form by name, so renaming a field in the template while the
# handler still asks for the old one produces a login that refuses every correct
# password, with a generic "Sign-in failed" and nothing in the log to say the
# field was simply absent. The remember checkbox is the worst of the five --
# there the symptom is only that trusting a device quietly stops working, which
# nobody would report as a bug.
ROUTE = "/login"
LOGOUT_ROUTE = "/logout"

FIELD_NEXT = "next"
FIELD_FORM_TOKEN = "form_token"
FIELD_PASSWORD = "password"
FIELD_CODE = "code"
FIELD_REMEMBER = "remember"

# An unchecked box submits NOTHING at all -- the browser omits the field
# entirely rather than sending a falsy value -- so the route tests presence, and
# this is only the value a ticked box carries.
REMEMBER_ON = "1"


# ---------------------------------------------------------------------------
# The failed-attempt counter.
#
# ``auth.LockoutState`` deliberately ships no module-level singleton, so the
# route's one instance lives here -- this module is the only caller, which is
# what makes the class's "not thread-safe and deliberately not locked" note
# hold. The state is in memory and resets on restart, which is acceptable for
# one user and keeps a disk write off the authentication path.
#
# ``reset_lockout`` exists for tests: they share this instance, so a suite that
# floods one address would otherwise leak that flood into every test after it
# and, at 50 global failures in 15 minutes, eventually lock the file out of its
# own fixtures. Shared mutable state across tests needs an explicit reset hook;
# the alternative is tests that only pass in a particular order.

_lockout = auth.LockoutState()


def lockout_state() -> auth.LockoutState:
    """The live counter -- for the route, and for asserting on it in tests."""
    return _lockout


def reset_lockout() -> None:
    global _lockout
    _lockout = auth.LockoutState()


# ---------------------------------------------------------------------------
# The form token (design mitigation 5).

# A THIRD token kind, and it must never collide with the other two. This token
# is minted by an UNAUTHENTICATED ``GET /login``, so a collision here is not the
# credential-downgrade ``auth``'s discriminator was added to stop -- it is a
# complete bypass, in which fetching the login page hands the visitor a session
# cookie. A test pins that it differs from both.
FORM_TOKEN_KIND = "form"

# Long enough to type a password and read a code off a phone, short enough that
# a scraped form is worthless within minutes. A token that has aged out gives
# the same generic refusal as everything else; the reload the user makes anyway
# issues a fresh one.
FORM_TOKEN_MAX_AGE_SEC = 300


def mint_form_token(key: str, *, epoch: int, now: float | None = None) -> str:
    return auth.mint_token(key, kind=FORM_TOKEN_KIND, epoch=epoch, now=now)


def verify_form_token(token: str | None, creds: auth_store.Credentials, *,
                      now: float | None = None) -> bool:
    return auth.verify_token(token, creds.session_secret, kind=FORM_TOKEN_KIND,
                             epoch=creds.epoch,
                             max_age_sec=FORM_TOKEN_MAX_AGE_SEC, now=now)


# ---------------------------------------------------------------------------
# The two cookie tokens.
#
# Their ``kind`` and their ``max_age_sec`` are paired HERE and nowhere else, so
# the route never names either. That is not tidiness: ``auth.mint_token`` and
# ``auth.verify_token`` both take ``kind`` keyword-only with no default
# precisely because a call site that gets it wrong re-opens the substitution the
# discriminator was added to close -- and a route that had to write
# ``kind=auth.KIND_SESSION, max_age_sec=auth.SESSION_MAX_AGE_SEC`` by hand is
# exactly such a call site. There is one place to read, and one to change.

def mint_session_token(key: str, *, epoch: int, now: float | None = None) -> str:
    return auth.mint_token(key, kind=auth.KIND_SESSION, epoch=epoch, now=now)


def mint_remember_token(key: str, *, epoch: int, now: float | None = None) -> str:
    return auth.mint_token(key, kind=auth.KIND_REMEMBER, epoch=epoch, now=now)


def verify_remember_token(token: str | None, creds: auth_store.Credentials, *,
                          now: float | None = None) -> bool:
    """True for an untampered, unexpired remember-device token of THIS epoch.

    Its one power is waiving the TOTP prompt inside ``attempt`` -- never the
    password, and never admission on its own (the gate does not read this
    cookie at all). ``epoch`` comes from the credentials file, so "sign out
    everywhere" kills trusted devices along with live sessions.
    """
    return auth.verify_token(token, creds.session_secret, kind=auth.KIND_REMEMBER,
                             epoch=creds.epoch,
                             max_age_sec=auth.REMEMBER_MAX_AGE_SEC, now=now)


def _issue(kind: str, *, now: float | None = None) -> str | None:
    """Mint a token of ``kind`` against the CURRENT store, or None.

    None rather than an exception: an unconfigured or corrupt credentials file
    must render a login page that refuses, not a traceback on a public URL. The
    reason is logged by ``_load_credentials``; the visitor is told nothing.

    The store is read at CALL time on every one of these, which matters most for
    the session token: ``attempt`` has just written the advanced TOTP counter,
    so minting from a value captured earlier would sign against a stale record.
    """
    creds = _load_credentials()
    if creds is None:
        return None
    return auth.mint_token(creds.session_secret, kind=kind, epoch=creds.epoch,
                           now=now)


def issue_form_token(*, now: float | None = None) -> str | None:
    """The token ``GET /login`` embeds in the form, or None when it cannot."""
    return _issue(FORM_TOKEN_KIND, now=now)


def issue_session_token(*, now: float | None = None) -> str | None:
    """The session cookie's value after a successful ``attempt``, or None."""
    return _issue(auth.KIND_SESSION, now=now)


def issue_remember_token(*, now: float | None = None) -> str | None:
    """The remember-device cookie's value, or None. Only when asked for."""
    return _issue(auth.KIND_REMEMBER, now=now)


# ---------------------------------------------------------------------------
# next=

# Site-relative, and nothing else. A bare ``startswith("/")`` is NOT enough:
# ``//evil.example`` is a protocol-relative URL and browsers normalise the
# backslash in ``/\evil.example`` to a slash, so both leave this site while
# passing that check. The excluded character class covers the other half --
# this value is echoed into a ``Location:`` header, where a newline is response
# splitting.
#
# ``\Z`` and not ``$``: in Python ``$`` also matches immediately before a
# trailing newline, so ``"/desk\n"`` would pass a ``$``-anchored version of this
# pattern -- which is exactly the input the header-forging case is about.
_MAX_NEXT_LEN = 512
_SAFE_NEXT = re.compile(r"\A/(?![/\\])[^\s\x00-\x1f\x7f]*\Z")


def safe_next(candidate: object) -> str:
    """``candidate`` when it is a safe site-relative path, else ``DEFAULT_NEXT``.

    Deliberately NOT called by ``render_form``: the form ESCAPES what it is
    given, and a sanitising renderer would make that escaping untestable (the
    dangerous value would never reach it). The route calls this, then renders.
    """
    if not isinstance(candidate, str) or not (0 < len(candidate) <= _MAX_NEXT_LEN):
        return DEFAULT_NEXT
    return candidate if _SAFE_NEXT.match(candidate) else DEFAULT_NEXT


# ---------------------------------------------------------------------------
# The attempt.

@dataclasses.dataclass(frozen=True)
class AttemptResult:
    """``ok`` plus the sentence to render. Nothing HTTP, on purpose.

    It carries no reason code and no credentials: the reason belongs in the log
    and never on the page, and the caller that needs the credentials to mint a
    cookie reads them itself -- by then this call has already written the
    advanced TOTP counter, so that read sees the current record.
    """
    ok: bool
    message: str


_SUCCESS = AttemptResult(True, "")
_FAILURE = AttemptResult(False, GENERIC_FAILURE)


def _load_credentials() -> auth_store.Credentials | None:
    """The store, or None -- logging why. Never raises.

    Both "nothing configured" and "file is corrupt" are worth a WARNING naming
    the file, because on a single-user app either one means the only person who
    can fix it is locked out of the only UI that can stop the stack. Neither is
    worth telling the visitor: the page shows the same sentence a typo produces.

    ``auth_store.load()`` is called with no path so it resolves the module's
    ``DEFAULT_PATH`` at CALL time. Passing ``auth_store.DEFAULT_PATH`` captured
    at import would be the documented default-argument trap that once had a test
    fixture writing into the live database for six weeks.
    """
    try:
        creds = auth_store.load()
    except auth_store.CredentialsError as exc:
        log.warning("Credentials file %s cannot be read, so every sign-in is "
                    "refused: %s", auth_store.DEFAULT_PATH, exc)
        return None
    if creds is None:
        log.warning("No credentials are configured at %s, so every sign-in is "
                    "refused. Run tools/webgui_credentials.py to set them.",
                    auth_store.DEFAULT_PATH)
        return None
    return creds


def _refuse(client: str, reason: str, *, now: float) -> AttemptResult:
    """Record, log, and hand back the one sentence.

    EVERY failure funnels through here, which is what makes ordering rule 3
    structural rather than a thing to remember: there is no other way to return
    a refusal, so a new early exit cannot skip the counter.

    Recording while the client is ALREADY locked out is deliberate -- it is what
    drives the exponential further out under a hammering, instead of letting the
    attacker sit at the threshold for free. It also re-arms the global window,
    which is the documented and accepted cost: during an active flood the owner
    stays refused, and regains access a minute after it stops.
    """
    _lockout.record_failure(client, now=now)
    log.warning("Sign-in refused for %s (%s)", client, reason)
    return _FAILURE


def attempt(*, password: str, code: str | None, client: str,
            form_token: str | None, remember_token: str | None,
            now: float | None = None) -> AttemptResult:
    """One sign-in attempt. The order of the checks IS the security here.

    ``form_token`` and ``remember_token`` are keyword-only with NO default,
    matching ``auth``'s ``kind`` and for the same reason: a default is precisely
    how a future call site skips the check -- or, for the remember cookie,
    silently stops honouring a trusted device -- without anyone noticing the
    omission at the call.

    **What ``remember_token`` may do, exhaustively: waive the TOTP factor.** It
    never waives the password, it is checked only AFTER the password has already
    verified, and it admits nobody on its own -- the gate does not read that
    cookie at all (``auth_middleware.REMEMBER_COOKIE``). A stolen month-old
    cookie is therefore worth exactly one thing to an attacker who ALSO has the
    password, which is the trade the design accepted.
    """
    at = time.time() if now is None else now

    # 1. The throttle, FIRST -- ahead of the credentials read as well as the
    #    hash. Cheap by construction: a bounded number of timestamp comparisons
    #    that allocate nothing outliving the call.
    if _lockout.locked_until(client, now=at):
        return _refuse(client, "locked out", now=at)

    creds = _load_credentials()
    if creds is None:
        return _refuse(client, "credentials unavailable", now=at)

    # 2. The form token, still before Argon2. Refusing a blind POST costs one
    #    HMAC instead of 19 MiB, and it closes login-CSRF as a side effect.
    if not verify_form_token(form_token, creds, now=at):
        return _refuse(client, "missing or invalid form token", now=at)

    # 3. Only now is it worth paying for a hash. THE PASSWORD IS UNCONDITIONAL
    #    -- this check sits ABOVE the remember-device branch below, so a trusted
    #    device is a device that skips the CODE, not one that skips the login.
    if not auth.verify_password(creds.password_hash, password):
        return _refuse(client, "password", now=at)

    # 4. The second factor, unless this device is already trusted.
    if verify_remember_token(remember_token, creds, now=at):
        # No counter to persist: nothing was consumed, so there is nothing that
        # could be replayed. Worth an INFO line -- "signed in without a code" is
        # the one accepted-sign-in shape an operator might want to account for.
        log.info("Sign-in accepted for %s on a remembered device (no code)",
                 client)
    else:
        ok, counter = auth.verify_totp(
            creds.totp_secret, code, now=at,
            last_counter=creds.last_totp_counter)
        if not ok:
            return _refuse(client, "code", now=at)

        # Persist the accepted step BEFORE declaring success. Without this write
        # the replay guard never advances and the code just used stays valid for
        # the rest of its window.
        try:
            auth_store.save(dataclasses.replace(creds, last_totp_counter=counter))
        except OSError as exc:
            # Refuse rather than accept: a code we cannot record is a code we
            # cannot stop being replayed. Loud in the log, generic on the page.
            log.warning("Could not persist the TOTP counter to %s, so this "
                        "otherwise-valid sign-in is refused: %s",
                        auth_store.DEFAULT_PATH, exc)
            return _refuse(client, "counter persist failed", now=at)
        log.info("Sign-in accepted for %s", client)

    _lockout.record_success(client)
    return _SUCCESS


# ---------------------------------------------------------------------------
# The form.
#
# Raw HTML with no NiceGUI runtime -- which is the point, not an omission. A
# ``@ui.page`` login would force ``/_nicegui_ws/`` and ``/_nicegui/{version}/*``
# open before authentication, and the former carries socket.io's HTTP
# long-polling transport as well as the websocket, so it could not be gated on
# scope type either. With a plain form, nothing needs them before login and the
# websocket becomes a real boundary. A NiceGUI form would also submit over that
# websocket, where a cookie cannot be set.
#
# Palette from the design: page #0c1424, card #101a30, border #213152,
# text #cdd8ee, primary #2563eb.

_CSS = """
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex;
    align-items: center; justify-content: center;
    background: #0c1424; color: #cdd8ee;
    font-family: 'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI',
                 Roboto, Helvetica, Arial, sans-serif;
    font-size: 15px; padding: 24px;
  }
  .card {
    width: 100%; max-width: 360px;
    background: #101a30; border: 1px solid #213152; border-radius: 10px;
    padding: 28px 26px 26px;
  }
  h1 { margin: 0 0 4px; font-size: 19px; font-weight: 600; color: #eaf0fb; }
  .eyebrow {
    margin: 0 0 22px; font-size: 11px; letter-spacing: .12em;
    text-transform: uppercase; color: #7f8db0;
  }
  label {
    display: block; margin-bottom: 6px;
    font-size: 12px; color: #8794b4;
  }
  input[type=password], input[type=text] {
    width: 100%; margin-bottom: 16px; padding: 10px 12px;
    background: #0c1426; color: #e7edf8;
    border: 1px solid #243353; border-radius: 6px;
    font-size: 15px; font-family: inherit;
  }
  input:focus { outline: none; border-color: #3b82f6; }
  input[name=code] { letter-spacing: .28em; font-variant-numeric: tabular-nums; }
  button {
    width: 100%; margin-top: 4px; padding: 11px 12px;
    background: #2563eb; color: #ffffff;
    border: 0; border-radius: 6px;
    font-size: 15px; font-weight: 600; font-family: inherit; cursor: pointer;
  }
  button:hover { background: #1d4fd1; }
  /* The trust-this-device row. A LABEL wrapping the box, so the words are part
     of the hit target -- this is typed on a phone as often as a desk. */
  .trust {
    display: flex; align-items: center; gap: 8px;
    margin: 2px 0 6px; font-size: 12px; color: #8794b4; cursor: pointer;
  }
  .trust input { margin: 0; accent-color: #2563eb; }
  .error {
    margin: 0 0 18px; padding: 10px 12px; border-radius: 6px;
    background: rgba(248, 113, 113, .10); border: 1px solid #7f3341;
    color: #f2b8bf; font-size: 13px;
  }
"""


def render_form(*, next_path: str, error: str | None,
                form_token: str | None) -> str:
    """The complete ``/login`` document.

    Every interpolated value is attacker-influenced (``next`` off the query
    string, the token off a signed cookie-sized round trip) and every one goes
    through ``html.escape(..., quote=True)`` -- ``quote=True`` because all three
    land inside double-quoted attributes, where escaping only ``<`` and ``&``
    lets a value close its own attribute.

    ``form_token`` is required rather than defaulted: a form rendered without
    one produces a login nobody can complete, and that should be a ``TypeError``
    at the call site instead of a mystery in production. It still ACCEPTS None,
    which is what a broken credentials file yields -- the page renders and the
    POST refuses, rather than the page failing to render at all.

    The code field carries no ``maxlength`` and no ``pattern`` on purpose.
    ``auth.verify_totp`` deliberately strips surrounding whitespace because a
    code pasted out of an authenticator app routinely brings a trailing space,
    and either attribute would silently truncate or block exactly that paste --
    handing the user a generic "Sign-in failed" for a code that was correct, on
    the one screen where that is least affordable.
    """
    esc_next = html.escape(next_path, quote=True)
    esc_token = html.escape(form_token or "", quote=True)
    banner = (f'    <p class="error">{html.escape(error, quote=True)}</p>\n'
              if error else "")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>Sign in</title>
  <style>{_CSS}  </style>
</head>
<body>
  <main class="card">
    <h1>Sign in</h1>
    <p class="eyebrow">Password and authenticator code</p>
{banner}    <form method="post" action="{ROUTE}" autocomplete="on">
      <input type="hidden" name="{FIELD_NEXT}" value="{esc_next}">
      <input type="hidden" name="{FIELD_FORM_TOKEN}" value="{esc_token}">
      <label for="{FIELD_PASSWORD}">Password</label>
      <input type="password" id="{FIELD_PASSWORD}" name="{FIELD_PASSWORD}"
             required autofocus autocomplete="current-password">
      <label for="{FIELD_CODE}">Authenticator code</label>
      <input type="text" id="{FIELD_CODE}" name="{FIELD_CODE}" required
             inputmode="numeric" autocomplete="one-time-code">
      <label class="trust" for="{FIELD_REMEMBER}">
        <input type="checkbox" id="{FIELD_REMEMBER}" name="{FIELD_REMEMBER}"
               value="{REMEMBER_ON}">
        Trust this device for 30 days (skip the code, never the password)
      </label>
      <button type="submit">Sign in</button>
    </form>
  </main>
</body>
</html>
"""
