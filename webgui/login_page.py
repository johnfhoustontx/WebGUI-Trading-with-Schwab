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


def issue_form_token(*, now: float | None = None) -> str | None:
    """The token ``GET /login`` embeds in the form, or None when it cannot.

    None rather than an exception: an unconfigured or corrupt credentials file
    must render a login page that refuses, not a traceback on a public URL. The
    reason is logged; the visitor is told nothing.
    """
    creds = _load_credentials()
    if creds is None:
        return None
    return mint_form_token(creds.session_secret, epoch=creds.epoch, now=now)


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
            form_token: str | None, now: float | None = None) -> AttemptResult:
    """One sign-in attempt. The order of the checks IS the security here.

    ``form_token`` is keyword-only with NO default, matching ``auth``'s ``kind``
    and for the same reason: a default is precisely how a future call site
    skips the check without anyone noticing the omission at the call.
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

    # 3. Only now is it worth paying for a hash.
    if not auth.verify_password(creds.password_hash, password):
        return _refuse(client, "password", now=at)

    ok, counter = auth.verify_totp(creds.totp_secret, code,
                                   now=at, last_counter=creds.last_totp_counter)
    if not ok:
        return _refuse(client, "code", now=at)

    # 4. Persist the accepted step BEFORE declaring success. Without this write
    #    the replay guard never advances and the code just used stays valid for
    #    the rest of its window.
    try:
        auth_store.save(dataclasses.replace(creds, last_totp_counter=counter))
    except OSError as exc:
        # Refuse rather than accept: a code we cannot record is a code we cannot
        # stop being replayed. Loud in the log, generic on the page.
        log.warning("Could not persist the TOTP counter to %s, so this "
                    "otherwise-valid sign-in is refused: %s",
                    auth_store.DEFAULT_PATH, exc)
        return _refuse(client, "counter persist failed", now=at)

    _lockout.record_success(client)
    log.info("Sign-in accepted for %s", client)
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
{banner}    <form method="post" action="/login" autocomplete="on">
      <input type="hidden" name="next" value="{esc_next}">
      <input type="hidden" name="form_token" value="{esc_token}">
      <label for="password">Password</label>
      <input type="password" id="password" name="password" required autofocus
             autocomplete="current-password">
      <label for="code">Authenticator code</label>
      <input type="text" id="code" name="code" required
             inputmode="numeric" autocomplete="one-time-code">
      <button type="submit">Sign in</button>
    </form>
  </main>
</body>
</html>
"""
