"""Re-photograph the private app for the marketing gallery on ``neuralstrike.co``.

``deploy/site/gallery.html`` shows twenty-two screenshots of the trading app.
They were taken before the branding landed, so every one of them carries the old
header lockup -- and the branding lives in the app HEADER, which is exactly what
the public read-only screens (``webgui/live_main.py``) deliberately do not
render. So these cannot be re-shot from the public origin the way
``tools/capture_live_shots.py`` shoots the live grid. They have to come from the
PRIVATE app on loopback, which is behind a password and a TOTP code.

WHY THAT IS POSSIBLE WITHOUT A PASSWORD. ``webgui/auth.mint_token`` is stateless
-- there is no server-side registry of issued tokens, by design -- so given the
store's ``session_secret`` and ``epoch``, a valid session cookie is a pure
computation. This script runs as the owner, on the owner's box, and reads a file
the owner owns (``shared/webgui_auth.json``, mode 0600). It mints nothing it
could not equally well have obtained by typing the password into the form.

FOUR DECISIONS ARE WORTH KNOWING BEFORE EDITING THIS.

1. **The refusal is total when there is no session.** Every route on ``:8500``
   303s to ``/login`` without one, and the login form is a perfectly good HTTP
   200 that Chrome screenshots happily. A run that ignored that would publish
   twenty-two pictures of a login box over the real gallery -- so a missing or
   corrupt credentials file raises ``SystemExit`` before a browser is even
   looked for, and the session is VERIFIED against the rendered DOM before the
   first file is written. Verified first, not "after the first capture": by then
   a good tile has already been overwritten, and that file is the one thing
   there is no way to get back.

2. **The cookie reaches Chrome through a loopback redirect, not through argv or
   a profile.** ``--headless --screenshot`` has no flag that takes a cookie or a
   header. Writing one into a throwaway ``--user-data-dir`` means forging
   Chrome's OS-encrypted cookie store (DPAPI/app-bound on Windows, OSCrypt on
   Linux) -- version-dependent, and silently wrong when it breaks. Driving
   ``Network.setCookie`` over CDP needs a websocket client this tool would be
   the only user of. So instead the browser is pointed at a tiny loopback server
   that answers ``302`` + ``Set-Cookie`` and forwards it to the app: Chrome
   stores the cookie through its own code path, and because cookies are not
   isolated by port (RFC 6265 s8.5) the one set from ``127.0.0.1:<ephemeral>``
   is then sent to ``127.0.0.1:8500`` -- including on the ``/_nicegui_ws/``
   handshake, which is an ordinary HTTP request. **The token never appears in a
   command line**; this repo has a documented incident where a live stream key
   was readable in ``pgrep -af``. The one thing that would break this is Chrome
   enabling origin-bound cookies (scheme + PORT binding) by default; it is a
   long-standing experiment and off today. If it lands, the verification render
   below fails and the run exits non-zero saying the session was refused --
   which is the point of doing it before anything is published.

3. **A shot whose view the URL cannot name is SKIPPED, not guessed.** Three of
   the twenty-two are one Simulator route told apart by an in-page tab
   (``Shot.subtab``), and ``simulator.render()`` takes no arguments -- clicking
   needs CDP. Capturing the page default three times would publish one identical
   picture under three different captions, which is precisely the failure this
   gallery already shipped once, with a caption reading "What-if" over a Replay
   screenshot. A tile that is older is a smaller lie than a tile that is wrong.
   See ``unreachable_reason``.

4. **Every capture is one fixed viewport.** The shipped images are hand-cropped
   and no two are the same size; ``gallery.html`` declares each one's exact
   width/height. A recapture makes them uniform, so those attributes need
   updating in the same change that publishes new files -- otherwise every tile
   letterboxes and it reads as a CSS bug.

Run it by hand on the box that serves the app, with the app running::

    .venv/bin/python tools/capture_gallery_shots.py
"""
import argparse
import contextlib
import http.server
import logging
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
# APPENDED, not inserted -- the reasoning ``tools/webgui_credentials.py`` records:
# ``webgui/`` contains a ``tools/`` directory of its own, and putting it ahead of
# the repo root would shadow this package for anything that later does a fresh
# ``import tools``. Nothing else on the path defines these four names.
sys.path.append(str(_REPO_ROOT / "webgui"))

import repo_paths  # noqa: E402
from tools import gallery_screens  # noqa: E402

import auth              # noqa: E402  (webgui/auth.py)
import auth_middleware   # noqa: E402  (webgui/auth_middleware.py)
import auth_store        # noqa: E402  (webgui/auth_store.py)
import login_page        # noqa: E402  (webgui/login_page.py)
import shell             # noqa: E402  (webgui/shell.py -- the capture cookie)

log = logging.getLogger("capture_gallery_shots")

# The credentials file, held as a module attribute so a test can repoint it
# without ever touching the checkout's real one. Never restated as a path: it is
# the same file ``auth_middleware`` gates every request against, and two spellings
# of it would be two things to keep in step.
AUTH_STORE = auth_store.DEFAULT_PATH

# Imported, never spelled out. A tool that wrote the cookie name itself would
# keep working after a rename and simply stop authenticating -- which from the
# outside looks identical to the app being down.
SESSION_COOKIE = auth_middleware.SESSION_COOKIE

# The PRIVATE app on loopback. Not ``APP_HOST``: that path goes through DNS, TLS
# and Caddy, and Caddy sets ``X-Edge``, which is the header the kiosk exemption
# reads. Loopback is also the only place this app is bound.
APP_URL = repo_paths.NICEGUI_URL

# Where ``gallery.html`` already points its ``<img src>``. The stems come from
# the table, so nothing here restates a filename.
OUT_DIR = pathlib.Path(repo_paths.SITE_ROOT) / "assets" / "shots"

# One geometry for every shot -- see decision 4 in the module docstring.
VIEWPORT_WIDTH, VIEWPORT_HEIGHT = 1840, 920

# How long Chrome is told to let the page settle. Longer than the live grid's
# budget: these routes render the rail, a tab strip and, on most of them,
# Highcharts, on top of the same Redis-on-a-watcher-tick paint.
#
# VIRTUAL TIME IS NOT WALL TIME. Chrome runs its own clock as fast as the page
# allows, pausing for pending NETWORK fetches -- so it waits reliably for the
# document and its assets and does NOT reliably wait for a value that arrives
# later over the page's websocket. Raising this is the cheap thing to try if
# captures come back showing skeletons; it is not guaranteed to fix it.
SETTLE_MS = 12000

# A shorter budget for the verification render: it only has to reach a document,
# not a settled one.
VERIFY_MS = 8000

# Wall-clock ceiling per shot, in case Chrome hangs rather than exits.
SHOT_TIMEOUT_SEC = 60

# 85 rather than the live grid's 80: these are the product's showcase images and
# are viewed at close to full size, where the live tiles are 640px thumbnails.
WEBP_QUALITY = 85

# --- what makes a render "the app" and not the login form --------------------
#
# HTTP 200 IS NOT THE CHECK. ``/login`` is a 200, and Chrome screenshots it
# without complaint. These two markers are read off the rendered DOM instead.
#
# The positive one is structural rather than cosmetic: ``/login`` is served as
# plain HTML with NO NiceGUI runtime -- deliberately, because that is what makes
# the websocket a real authentication boundary -- so it cannot carry a
# ``/_nicegui/`` asset URL, while every page of the app carries several in its
# head. A test pins that against ``login_page.py``'s source, so a redesign that
# mounts the runtime on the login page breaks the test rather than the detector.
#
# The negative one is belt-and-braces, and the check demands BOTH: absence of a
# login form is not presence of the app -- an error page has neither.
APP_DOM_MARKER = "/_nicegui/"
LOGIN_DOM_MARKER = f'action="{login_page.ROUTE}"'


# ---------------------------------------------------------------------------
# The capture list. PURE.

def _flat_shots():
    """Every ``Shot`` in the table, in the gallery's own order.

    Order is load-bearing twice over in ``gallery_screens`` -- section order is
    the gallery's rail, and a section's shot order pairs each figure with its
    caption BY POSITION -- so it is preserved rather than sorted.
    """
    return [shot for screen in gallery_screens.SCREENS for shot in screen.shots]


def shot_url(shot):
    """The loopback URL for one shot, query string included."""
    return f"{APP_URL}{shot.route}"


def shot_out(shot):
    """The file ``gallery.html`` already references for one shot."""
    return OUT_DIR / f"{shot.image}.webp"


def targets():
    """``[(url, output path), ...]`` for every shot in the table. PURE.

    The whole seam between this script and the site, with no side effects, so a
    test can read what would be captured and where without a browser, an app or
    a credential. Includes the shots ``unreachable_reason`` declines to take --
    the list is what the gallery CONTAINS, not what this run will write.
    """
    return [(shot_url(s), shot_out(s)) for s in _flat_shots()]


def unreachable_reason(shot):
    """Why this shot cannot be captured today, or ``""`` when it can.

    A ``subtab`` names a view that lives in page state rather than in the URL,
    so reaching it means clicking -- which needs CDP, which needs a websocket
    client this tool would be the only user of. Until then the honest answer is
    to leave the older tile alone: it shows the right view with the wrong
    branding, where a default-view capture would show the wrong view under a
    caption that says otherwise.
    """
    if shot.subtab:
        return (f"the {shot.subtab!r} view lives in page state, not the URL, and "
                "this tool cannot click; capturing the page default would "
                "publish an identical picture under a caption saying otherwise")
    return ""


def unreachable_shots():
    """The shots this tool declines to take, for the run summary and for tests."""
    return [s for s in _flat_shots() if unreachable_reason(s)]


# ---------------------------------------------------------------------------
# The credential.

def session_token(path=None):
    """Mint a session cookie value from the credentials file, or ``None``.

    ``None`` means the file is not there -- nothing has been enrolled yet.
    A file that exists and cannot be parsed RAISES ``CredentialsError`` out of
    ``auth_store.load``, deliberately, so a corrupt store can never be read as
    "no password set". The caller treats both as "do not capture", but the log
    line says which.

    ``path=None`` looks ``AUTH_STORE`` up NOW rather than binding it as a
    default argument -- the trap that let a fixture write into a live database
    for six weeks (see the repo-root conftest).
    """
    creds = auth_store.load(pathlib.Path(path) if path is not None else AUTH_STORE)
    if creds is None:
        return None
    return auth.mint_token(creds.session_secret, kind=auth.KIND_SESSION,
                           epoch=creds.epoch)


def session_is_live(dom):
    """True when this rendered DOM is the app rather than the login form.

    Both halves are required. Demanding only the absence of the login form
    would accept an error page, a proxy banner or an empty document as proof of
    a session; demanding only the presence of the runtime would be enough today
    and would stop being enough the moment anything else on the box answers.
    """
    if not isinstance(dom, str) or not dom:
        return False
    if LOGIN_DOM_MARKER in dom:
        return False
    return APP_DOM_MARKER in dom


# ---------------------------------------------------------------------------
# The cookie bootstrap.

class _Bootstrap:
    """A running loopback server that hands out one cookie and one redirect."""

    def __init__(self, server, nonce):
        self._server = server
        self._nonce = nonce

    @property
    def host(self):
        return self._server.server_address[0]

    @property
    def port(self):
        return self._server.server_address[1]

    def url_for(self, target):
        """The URL to point Chrome at so it arrives at ``target`` signed in.

        The target is percent-encoded into a query parameter rather than
        concatenated: these routes carry query strings of their own
        (``?view=Net+Prem``), and a bare join would let the app's ``view``
        parameter be read as the bootstrap's.
        """
        quoted = urllib.parse.quote(target, safe="")
        return f"http://{self.host}:{self.port}/{self._nonce}?to={quoted}"


def _handler_class(nonce, token):
    """The one-route handler, closed over the run's nonce and token.

    A closure, not attributes on the server, so neither value is reachable from
    anything that merely holds a reference to the socket.
    """
    # A browser-session cookie (no Max-Age), so it dies with the throwaway
    # profile. HttpOnly because nothing needs to read it from page script and a
    # captured page is running the whole application's JavaScript.
    cookie = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax"
    # Tells the app this is a screenshot session, so it suppresses NiceGUI's
    # "Connection lost" banner -- which --virtual-time-budget provokes by racing
    # the client's socket.io heartbeat while the server pings on the wall clock.
    # NOT HttpOnly-sensitive and carries no authority: shell.capture_chrome_css
    # hides one cosmetic element and nothing else, and the public origin does
    # not read it at all. See shell.CAPTURE_CHROME_CSS for why it is narrow.
    capture_cookie = (f"{shell.CAPTURE_COOKIE}=1; Path=/; HttpOnly; "
                      f"SameSite=Lax")

    class _Handler(http.server.BaseHTTPRequestHandler):
        # HTTP/1.1 so Chrome does not have to see a connection close to know the
        # response ended; every branch below therefore sets Content-Length.
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            target = urllib.parse.parse_qs(parts.query).get("to", [""])[0]
            # BOTH conditions. The nonce is what stops another process on the
            # box simply asking this port for a session cookie; the prefix check
            # stops the same request being turned into an open redirect that
            # carries a Set-Cookie with it.
            if parts.path != f"/{nonce}" or not target.startswith(APP_URL):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(302)
            # Two Set-Cookie headers, not one joined by a comma: RFC 6265 §3
            # says a response sends one cookie per header line, and joining
            # them makes the browser read the second as an attribute of the
            # first and drop it silently.
            self.send_header("Set-Cookie", cookie)
            self.send_header("Set-Cookie", capture_cookie)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            """Silence. The default writes the request line to stderr, and the
            request line contains the nonce."""

    return _Handler


@contextlib.contextmanager
def cookie_bootstrap(token):
    """Run the redirect server for the duration of the block.

    Bound to ``127.0.0.1`` explicitly and to an ephemeral port: a
    cookie-dispensing server on ``0.0.0.0`` would be a credential endpoint, and
    a fixed port would be one a stranger could learn.

    It stays up for the WHOLE run rather than per shot, because every shot gets
    a fresh ``--user-data-dir`` and so has to be handed the cookie again.
    """
    nonce = secrets.token_urlsafe(16)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                             _handler_class(nonce, token))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="gallery-cookie-bootstrap")
    thread.start()
    try:
        yield _Bootstrap(server, nonce)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# The browser.

def find_chrome():
    """The browser binary, or ``None``.

    The same resolution ``tools/capture_live_shots.py`` and ``stream_wall.sh``
    do: one browser, three names depending on how it was installed.
    """
    for name in ("google-chrome", "chromium-browser", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _chrome_argv(chrome, url, png, profile, *, settle_ms=SETTLE_MS, dump_dom=False):
    """The command line, split out so a test can read it without running it.

    ``url`` is always a BOOTSTRAP url -- the token is in that server's response
    headers, never here. Anything on the box can read this argv.
    """
    argv = [
        chrome,
        # Plain --headless, not --headless=new: Chrome 132 removed the old mode,
        # so on anything current the plain flag already IS the new one, while an
        # older packaged chromium refuses to start on the suffix.
        "--headless",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--window-size={VIEWPORT_WIDTH},{VIEWPORT_HEIGHT}",
        "--force-device-scale-factor=1",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        f"--virtual-time-budget={settle_ms}",
    ]
    argv.append("--dump-dom" if dump_dom else f"--screenshot={png}")
    argv.append(url)
    return argv


def _render_png(chrome, url, png, *, timeout=SHOT_TIMEOUT_SEC):
    """Drive the browser for one screenshot. Untestable here by construction."""
    with tempfile.TemporaryDirectory(prefix="gallery-shot-profile-") as profile:
        subprocess.run(_chrome_argv(chrome, url, png, profile),
                       check=True, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _dump_dom(chrome, url, *, timeout=SHOT_TIMEOUT_SEC):
    """The rendered DOM as text -- the verification render.

    Deliberately NOT logged anywhere: it is a whole application page, and
    dumping it into the journal to debug a failed sign-in would be a new way to
    leak whatever is on screen.
    """
    with tempfile.TemporaryDirectory(prefix="gallery-verify-profile-") as profile:
        argv = _chrome_argv(chrome, url, None, profile,
                            settle_ms=VERIFY_MS, dump_dom=True)
        done = subprocess.run(argv, check=True, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return done.stdout.decode("utf-8", "replace")


def capture_one(chrome, nav_url, out):
    """Render ``nav_url`` and publish it at ``out`` as WebP. Raises on failure.

    Published by RENAME. The site tree is served literally, so a file being
    written is a file being served. The temp name is a SIBLING of the
    destination rather than a system temp path, because ``os.replace`` is only
    atomic within one filesystem.
    """
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="gallery-shot-") as tmp:
        png = pathlib.Path(tmp) / "shot.png"
        _render_png(chrome, nav_url, png)
        if not png.is_file() or png.stat().st_size == 0:
            # Chrome can exit 0 having written nothing. Encoding that would
            # replace a good tile with a broken one -- worse than the older tile
            # it was.
            raise RuntimeError("chrome wrote no screenshot")

        part = out.with_name(out.name + ".part")
        try:
            with Image.open(png) as im:
                im.convert("RGB").save(part, "WEBP", quality=WEBP_QUALITY, method=6)
            os.replace(part, out)
        finally:
            part.unlink(missing_ok=True)


# ---------------------------------------------------------------------------

def main(argv=None):
    """``argv=None`` means NO ARGUMENTS, not ``sys.argv``.

    ``__main__`` passes ``sys.argv[1:]`` explicitly; letting argparse fall back
    to ``sys.argv`` would make ``main()`` inside a test parse pytest's own
    command line and exit 2.

    Exit codes: 0 when the run happened (some tiles may have been skipped or
    failed -- both are logged), 1 when nothing could be captured, and
    ``SystemExit`` with a message for the one failure that must never be
    ignorable: no usable session. A return code can be dropped by a caller that
    forgets to check it; twenty-two login forms over the gallery cannot be
    undone.
    """
    ap = argparse.ArgumentParser(
        description="Re-capture the marketing gallery from the private app.")
    ap.parse_args([] if argv is None else argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        token = session_token()
    except auth_store.CredentialsError as exc:
        raise SystemExit(
            f"the credentials file cannot be read ({exc}) - refusing to "
            "capture, because every route would render the login form") from exc
    if token is None:
        raise SystemExit(
            f"no credentials at {AUTH_STORE} - refusing to capture, because "
            "every route would render the login form and the run would publish "
            "22 pictures of it over the gallery")

    chrome = find_chrome()
    if chrome is None:
        log.error("no chrome/chromium on PATH - cannot capture the gallery")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    shots = _flat_shots()
    with cookie_bootstrap(token) as boot:
        # VERIFY BEFORE PUBLISHING ANYTHING. The probe is a real render of a
        # real route through the same bootstrap the captures use, so it proves
        # the whole chain -- the minted token, the cookie reaching Chrome, and
        # the app answering -- rather than only the arithmetic.
        probe = next((s for s in shots if not unreachable_reason(s)), None)
        if probe is None:
            log.error("every shot in the table is unreachable - nothing to do")
            return 1
        try:
            dom = _dump_dom(chrome, boot.url_for(shot_url(probe)))
        except Exception as exc:  # noqa: BLE001 - any failure here is total
            log.error("could not render %s to check the session: %s",
                      probe.route, exc)
            return 1
        if not session_is_live(dom):
            log.error(
                "%s did not render as the app - the session was refused, or "
                "the app is not running on %s. NOTHING was published; a login "
                "form over the gallery is worse than a stale one.",
                probe.route, APP_URL)
            return 1

        skipped, failed, taken = [], [], 0
        for shot in shots:
            reason = unreachable_reason(shot)
            if reason:
                log.warning("skipping %s (%s): %s", shot.image, shot.route, reason)
                skipped.append(shot.image)
                continue
            try:
                capture_one(chrome, boot.url_for(shot_url(shot)), shot_out(shot))
                taken += 1
            except Exception as exc:  # noqa: BLE001 - one bad shot is not the run
                log.warning("capture failed for %s (%s): %s",
                            shot.image, shot.route, exc)
                failed.append(shot.image)

    log.info("captured %d of %d shots into %s", taken, len(shots), OUT_DIR)
    if skipped:
        log.warning("%d shot(s) left at their existing capture: %s",
                    len(skipped), ", ".join(skipped))
    if failed:
        log.warning("%d shot(s) did not capture: %s", len(failed), ", ".join(failed))
    if failed and not taken:
        # One failure among nineteen is a gap. NINETEEN failures is the browser,
        # the app or the box, and it must not exit 0 -- a green run that wrote
        # nothing is how a gallery goes stale behind a healthy-looking timer.
        log.error("every capturable shot failed - nothing was written")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
