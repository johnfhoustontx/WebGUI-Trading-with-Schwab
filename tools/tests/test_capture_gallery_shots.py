"""``tools/capture_gallery_shots.py`` -- the marketing gallery's screenshots.

The same shape of problem ``test_capture_live_shots.py`` has, plus a credential.
The part that renders is a headless Chrome pointed at a RUNNING private app, and
no test here can execute it -- so the script is a pure target list, a pure
credential mint, a pure login-detector and a thin subprocess call, and what is
pinned is every decision whose failure would be silent and public.

Four of those matter more than the rest:

* **A run without a valid session publishes 22 screenshots of a login form over
  the real gallery.** Every route on ``:8500`` 303s to ``/login``, and a login
  page is a perfectly good 200 that Chrome screenshots happily. So the refusal
  is total (``SystemExit``), and the detector cannot be satisfied by a status
  code.
* **The token must never be a credential in git, nor a string in argv.** This
  repo has a documented incident where a live stream key was readable in
  ``pgrep -af``; a session cookie on a command line is the same mistake.
* **A capture list that drifts from the table** is a tile showing a page it
  never claimed to show.
* **A shot whose view the URL cannot name must not be captured wrong.** The
  gallery has already shipped a caption saying "What-if" over a Replay
  screenshot; publishing the page default under three different captions would
  reproduce that by machine.
"""
import http.client
import pathlib
import re
import sys
import urllib.parse

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import repo_paths  # noqa: E402
from tools import capture_gallery_shots as c  # noqa: E402
from tools import gallery_screens as g  # noqa: E402

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "tools" / "capture_gallery_shots.py"

auth = c.auth
auth_store = c.auth_store


@pytest.fixture(autouse=True)
def _never_the_live_store(tmp_path, monkeypatch):
    """No test may read (or write) the checkout's real credentials file.

    Mirrors ``test_webgui_credentials.py``'s fixture. Every test below is
    explicit about the store it uses; this only catches the one that forgets,
    and it is what lets the suite pass on a checkout that HAS credentials as
    well as on one that does not.
    """
    monkeypatch.setattr(c, "AUTH_STORE", tmp_path / "unused_auth.json")
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", tmp_path / "unused_auth.json")


@pytest.fixture
def store(tmp_path):
    """A synthetic credentials file. NOT a real secret -- see the module note.

    The password hash is deliberately junk: nothing here verifies a password,
    and a real Argon2 hash would cost 19 MiB and ~25 ms per test for nothing.
    """
    path = tmp_path / "webgui_auth.json"
    auth_store.save(auth_store.Credentials(
        password_hash="not-a-hash",
        totp_secret="A" * 32,
        session_secret="test-session-secret-not-a-real-one",
        epoch=7,
    ), path)
    return path


# --- the capture list is not a second copy of the table ----------------------

def test_it_captures_every_shot_in_the_map():
    """One source. A shot added to ``gallery_screens`` cannot be missing a
    capture, and a capture cannot outlive the shot that named it."""
    assert len(c.targets()) == sum(len(s.shots) for s in g.SCREENS)


def test_it_captures_from_loopback_never_the_public_host():
    """The gallery's whole subject is the PRIVATE app, which is only ever bound
    to loopback. Going through a public hostname would either fail (there is no
    such route) or, worse, quietly photograph the public read-only screens --
    which render no header, and so carry none of the branding this recapture
    exists for."""
    for url, _out in c.targets():
        assert url.startswith(f"http://127.0.0.1:{repo_paths.NICEGUI_PORT}")
        assert repo_paths.APP_HOST not in url
        assert repo_paths.LIVE_HOST not in url


def test_it_writes_the_filenames_the_gallery_already_references():
    """``deploy/site/assets/shots/<image>.webp`` -- the paths ``gallery.html``
    already has in its ``<img src>``. A stem that drifts writes a file nothing
    references while the stale tile keeps serving."""
    shots_dir = pathlib.Path(repo_paths.SITE_ROOT) / "assets" / "shots"
    stems = [sh.image for s in g.SCREENS for sh in s.shots]
    outs = [out for _url, out in c.targets()]
    assert [o.parent for o in outs] == [shots_dir] * len(outs)
    assert [o.name for o in outs] == [f"{stem}.webp" for stem in stems]


def test_the_query_string_survives_into_the_url():
    """``/options/gamma?view=Flow`` is a route parameter, so it belongs in the
    URL verbatim. (Whether the route HONOURS it is the route's business -- see
    the note in ``gallery_screens``; passing it through is what makes this tool
    correct the day it is wired.)"""
    urls = {url for url, _out in c.targets()}
    assert any(u.endswith("/options/gamma?view=Flow") for u in urls)


# --- the credential ----------------------------------------------------------

def test_a_missing_auth_store_refuses_rather_than_capturing_a_login_form():
    """THE ONE THAT MATTERS. Without a valid session every route 303s to
    ``/login``, and a run that ignored that would cheerfully publish 22
    screenshots of a login form over the real gallery."""
    with pytest.raises(SystemExit):
        c.main()


def test_a_corrupt_auth_store_refuses_too(tmp_path, monkeypatch):
    """``auth_store.load`` RAISES on a corrupt file rather than returning None,
    deliberately, so it cannot be read as "no password set". Both arrive here as
    the same fact -- there is no credential -- and both must stop the run."""
    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(c, "AUTH_STORE", bad)
    with pytest.raises(SystemExit):
        c.main()


def test_refusing_captures_nothing(monkeypatch):
    """The other half, and the one a stray ``raise`` in the wrong place would
    leave green: refusing must mean it never rendered, not that it rendered and
    threw the result away."""
    monkeypatch.setattr(c, "capture_one",
                        lambda *a, **k: pytest.fail("captured without a session"))
    monkeypatch.setattr(c, "find_chrome",
                        lambda: pytest.fail("looked for a browser without a session"))
    with pytest.raises(SystemExit):
        c.main()


def test_the_session_cookie_is_minted_not_hardcoded():
    """Source-level: a checked-in token would be a credential in git."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "mint_token" in src
    assert not re.search(r'ns_session\s*=\s*["\'][A-Za-z0-9._-]{16,}', src)


def test_the_minted_cookie_is_one_the_gate_would_accept(store):
    """The proof that the mint is right, rather than merely present: the token
    is run back through ``auth.verify_token`` with exactly the arguments
    ``auth_middleware._has_session`` uses -- kind, epoch and max age."""
    token = c.session_token(store)
    creds = auth_store.load(store)
    assert auth.verify_token(token, creds.session_secret,
                             kind=auth.KIND_SESSION, epoch=creds.epoch,
                             max_age_sec=auth.SESSION_MAX_AGE_SEC)


def test_the_minted_cookie_is_a_session_not_a_remember_device_token(store):
    """The kind discriminator is what stops the 30-day device cookie being
    replayed as a 12-hour session. A tool that minted the wrong kind would be
    refused by the gate, so this is really a "fails closed, loudly" pin."""
    token = c.session_token(store)
    creds = auth_store.load(store)
    assert not auth.verify_token(token, creds.session_secret,
                                 kind=auth.KIND_REMEMBER, epoch=creds.epoch,
                                 max_age_sec=auth.REMEMBER_MAX_AGE_SEC)


def test_it_names_the_cookie_the_gate_reads(store):
    """Imported from ``auth_middleware``, never restated: a tool that spelled
    the cookie itself would keep working after a rename and stop authenticating,
    which looks identical to the app being down."""
    assert c.SESSION_COOKIE is c.auth_middleware.SESSION_COOKIE
    assert "ns_session" not in SCRIPT.read_text(encoding="utf-8")


# --- the token is not a command-line argument --------------------------------

def test_the_token_never_reaches_the_command_line(store):
    """``pgrep -af`` prints argv to every user on the box, and this repo has a
    documented incident where a live stream key was readable exactly there."""
    token = c.session_token(store)
    argv = c._chrome_argv("/usr/bin/google-chrome",
                          "http://127.0.0.1:1/abc?to=x",
                          pathlib.Path("/tmp/x.png"), pathlib.Path("/tmp/prof"))
    joined = " ".join(argv)
    assert token not in joined
    assert c.SESSION_COOKIE not in joined


# --- the bootstrap that actually delivers the cookie -------------------------

def _get(url, *, timeout=5):
    """One request, NOT following redirects -- the redirect is the subject."""
    parts = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    try:
        conn.request("GET", parts.path + (f"?{parts.query}" if parts.query else ""))
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def test_the_bootstrap_sets_the_cookie_and_redirects(store):
    """The mechanism, exercised for real.

    Chrome's ``--headless --screenshot`` has no way to be handed a cookie, and
    forging one into a ``--user-data-dir`` profile means writing Chrome's
    OS-encrypted cookie store. So the browser is pointed at a loopback server
    that answers 302 + ``Set-Cookie`` and sends it on to the app: Chrome stores
    the cookie through its own code path, and cookies are not isolated by port
    (RFC 6265 s8.5), so it is then sent to ``:8500``.

    This test is the closest a machine here can get to proving that -- it pins
    OUR half of it exactly.
    """
    token = c.session_token(store)
    target = f"{c.APP_URL}/options/gamma?view=Flow"
    with c.cookie_bootstrap(token) as boot:
        status, headers, _body = _get(boot.url_for(target))
    assert status == 302
    assert headers["Location"] == target
    cookie = headers["Set-Cookie"]
    assert cookie.startswith(f"{c.SESSION_COOKIE}={token};")
    assert "Path=/" in cookie


def test_the_bootstrap_answers_only_its_own_nonce_path(store):
    """Anything on the box can reach a loopback port. The one-shot random path
    means a stray process cannot simply ask this server for a session cookie."""
    token = c.session_token(store)
    with c.cookie_bootstrap(token) as boot:
        status, headers, _body = _get(f"http://127.0.0.1:{boot.port}/")
    assert status == 404
    assert "Set-Cookie" not in headers


def test_the_bootstrap_binds_loopback_only(store):
    """A cookie-dispensing server on 0.0.0.0 would be a credential endpoint."""
    with c.cookie_bootstrap(c.session_token(store)) as boot:
        assert boot.host == "127.0.0.1"


# --- a login form is not the app ---------------------------------------------

def test_the_login_detector_reads_the_dom_not_the_status():
    """HTTP 200 is NOT the check: the login form is a perfectly good 200, and a
    run that trusted the status code would publish 22 pictures of it. So the
    detector is a predicate over the rendered DOM, and it is POSITIVE -- it
    demands a marker the app emits, rather than merely the absence of one the
    login page emits."""
    login_dom = ('<html><head><title>Sign in</title></head><body>'
                 '<form method="post" action="/login"></form></body></html>')
    app_dom = ('<html><head><title>Desk</title>'
               '<script src="/_nicegui/2.0/static/nicegui.js"></script>'
               '</head><body></body></html>')
    assert c.session_is_live(app_dom)
    assert not c.session_is_live(login_dom)
    assert not c.session_is_live("")
    # An error page is not a session either: absence of the login form is not
    # presence of the app.
    assert not c.session_is_live("<html><body>502 Bad Gateway</body></html>")


def test_the_detector_is_pinned_against_the_real_login_document():
    """Both markers, checked against the document ``/login`` actually serves.

    ``/login`` is plain HTML with NO NiceGUI runtime -- deliberately, because
    that is what makes the websocket a real authentication boundary -- so it
    structurally cannot carry a ``/_nicegui/`` asset URL. This renders the real
    form and asserts exactly that, so a redesign that mounts the runtime on the
    login page (or renames the form's action) fails HERE rather than silently
    turning every future run into 22 pictures of a login box.
    """
    doc = c.login_page.render_form(next_path="/desk", error=None,
                                   form_token="not-a-real-token")
    assert c.LOGIN_DOM_MARKER in doc
    assert c.APP_DOM_MARKER not in doc
    assert not c.session_is_live(doc)


def test_it_verifies_before_it_publishes_anything(monkeypatch, store):
    """Verification FIRST, not "after the first capture". A first capture that
    turns out to be a login form has already overwritten a good tile, and the
    file it overwrote is the one thing that cannot be got back."""
    monkeypatch.setattr(c, "AUTH_STORE", store)
    monkeypatch.setattr(c, "find_chrome", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(c, "_dump_dom", lambda *a, **k: "<html>Sign in</html>")
    monkeypatch.setattr(c, "capture_one",
                        lambda *a, **k: pytest.fail("published over a live gallery "
                                                    "without checking the session"))
    assert c.main() != 0


def test_a_verified_session_goes_on_to_capture(monkeypatch, store):
    """The converse, which a `return 1` in the wrong branch would leave green."""
    monkeypatch.setattr(c, "AUTH_STORE", store)
    monkeypatch.setattr(c, "find_chrome", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(c, "_dump_dom",
                        lambda *a, **k: '<script src="/_nicegui/2/x.js"></script>')
    seen = []
    monkeypatch.setattr(c, "capture_one",
                        lambda chrome, url, out: seen.append(out.name))
    assert c.main() == 0
    assert len(seen) == len(c.targets()) - len(c.unreachable_shots())


# --- the views a URL cannot name ---------------------------------------------

def test_a_subtab_shot_is_refused_rather_than_captured_wrong():
    """``simulator.render()`` takes no arguments, so its three shots are one
    route told apart by a click this tool cannot perform. Capturing the page
    default three times would publish one identical picture under three
    captions -- exactly the failure this gallery already shipped once, where a
    caption said "What-if" over a Replay screenshot. So they are SKIPPED, the
    correct-but-older tiles are left in place, and the run says so."""
    unreachable = c.unreachable_shots()
    assert unreachable, "the simulator's three subtab shots have gone missing"
    assert {sh.image for sh in unreachable} == {
        sh.image for s in g.SCREENS for sh in s.shots if sh.subtab}
    for shot in unreachable:
        assert c.unreachable_reason(shot)
    for shot in (sh for s in g.SCREENS for sh in s.shots if not sh.subtab):
        assert not c.unreachable_reason(shot)


def test_a_skipped_shot_is_not_a_failed_run(monkeypatch, store):
    """A tile deliberately left alone is a known gap, not a broken timer."""
    monkeypatch.setattr(c, "AUTH_STORE", store)
    monkeypatch.setattr(c, "find_chrome", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(c, "_dump_dom", lambda *a, **k: c.APP_DOM_MARKER)
    monkeypatch.setattr(c, "capture_one", lambda *a, **k: None)
    assert c.main() == 0


# --- failure is per shot, except the failures that are total -----------------

def test_one_shot_failing_does_not_abort_the_rest(monkeypatch, store):
    """A missing tile is a gap in a gallery; a run that aborts on the second
    shot is twenty stale ones."""
    seen = []

    def _fake(chrome, url, out):
        seen.append(out.name)
        if len(seen) == 2:
            raise RuntimeError("chrome fell over")

    monkeypatch.setattr(c, "AUTH_STORE", store)
    monkeypatch.setattr(c, "find_chrome", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(c, "_dump_dom", lambda *a, **k: c.APP_DOM_MARKER)
    monkeypatch.setattr(c, "capture_one", _fake)
    assert c.main() == 0
    assert len(seen) == len(c.targets()) - len(c.unreachable_shots())


def test_every_shot_failing_is_not_a_green_run(monkeypatch, store):
    """One failure among nineteen is a gap; nineteen is the browser, the app or
    the box. Exiting 0 there is how a gallery goes stale behind a healthy timer
    -- the same reasoning that makes a missing browser exit non-zero."""
    monkeypatch.setattr(c, "AUTH_STORE", store)
    monkeypatch.setattr(c, "find_chrome", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(c, "_dump_dom", lambda *a, **k: c.APP_DOM_MARKER)

    def _always_fails(chrome, url, out):
        raise RuntimeError("chrome fell over")

    monkeypatch.setattr(c, "capture_one", _always_fails)
    assert c.main() == 1


def test_a_missing_browser_is_a_failure_not_a_silent_success(monkeypatch, store):
    """Every shot would fail identically, forever, behind a green exit."""
    monkeypatch.setattr(c, "AUTH_STORE", store)
    monkeypatch.setattr(c, "find_chrome", lambda: None)
    monkeypatch.setattr(c, "capture_one",
                        lambda *a, **k: pytest.fail("captured with no browser"))
    assert c.main() == 1


# --- publishing is atomic ----------------------------------------------------

def test_a_capture_is_published_by_rename_never_written_in_place(tmp_path,
                                                                 monkeypatch):
    """The site tree is served literally, so a file being written is a file
    being served. The temp name is a SIBLING because ``os.replace`` is only
    atomic within one filesystem."""
    from PIL import Image

    src = tmp_path / "shot.png"
    Image.new("RGB", (64, 40), (12, 24, 48)).save(src)
    monkeypatch.setattr(c, "_render_png",
                        lambda chrome, url, png, **kw: png.write_bytes(src.read_bytes()))

    out = tmp_path / "shots" / "image1.webp"
    out.parent.mkdir()
    c.capture_one("/usr/bin/google-chrome", "http://127.0.0.1:1/nonce?to=x", out)

    assert out.is_file()
    with Image.open(out) as im:
        assert im.format == "WEBP"
    assert not list(out.parent.glob("*.part")), "a temp file was left behind"


def test_an_empty_render_is_refused_rather_than_published(tmp_path, monkeypatch):
    """Chrome can exit 0 having written nothing. Encoding that would replace a
    good tile with a broken one -- worse than the older tile it was."""
    monkeypatch.setattr(c, "_render_png", lambda *a, **k: None)
    out = tmp_path / "image1.webp"
    with pytest.raises(RuntimeError):
        c.capture_one("/usr/bin/google-chrome", "http://127.0.0.1:1/nonce?to=x", out)
    assert not out.exists()


# --- the browser -------------------------------------------------------------

def test_it_looks_for_the_same_browsers_the_live_capture_does(monkeypatch):
    """One box, one browser; the name depends on how it was installed."""
    names = []
    monkeypatch.setattr(c.shutil, "which", lambda n: names.append(n) or None)
    assert c.find_chrome() is None
    assert names[:2] == ["google-chrome", "chromium-browser"]
    assert "chromium" in names


def test_each_shot_gets_a_fresh_browser_profile():
    """A ``--user-data-dir`` that was not shut down cleanly makes Chrome open a
    restore-pages bubble, and here that bubble would be IN the picture. A fresh
    profile also means the cookie has to be re-delivered every time, which is
    why the bootstrap runs for the whole run rather than once."""
    argv = c._chrome_argv("/usr/bin/google-chrome", "http://127.0.0.1:1/n?to=x",
                          pathlib.Path("/tmp/x.png"), pathlib.Path("/tmp/prof"))
    assert any(a.startswith("--user-data-dir=") for a in argv)
    assert any(a.startswith("--headless") for a in argv)
    assert argv[-1] == "http://127.0.0.1:1/n?to=x"


def test_the_settle_delay_is_declared_not_left_to_luck():
    """These pages paint from Redis on a watcher tick, so a screenshot taken at
    load is a page of skeletons -- which looks like a rendering bug and is a
    timing one. The private app also has more to settle than the public
    screens: a rail, a tab strip and, on most of these routes, Highcharts."""
    assert c.SETTLE_MS >= 8000
    assert f"--virtual-time-budget={c.SETTLE_MS}" in " ".join(
        c._chrome_argv("/usr/bin/google-chrome", "http://127.0.0.1:1/n",
                       pathlib.Path("/tmp/x.png"), pathlib.Path("/tmp/p")))


def test_it_does_not_import_the_app():
    """It reads one pure table, two credential modules and a constant. An
    ``import main`` would register every route -- including ``/terminate`` --
    inside a cron-shaped process, and drag NiceGUI and the bus in with it."""
    text = SCRIPT.read_text(encoding="utf-8")
    for banned in ("import nicegui", "from nicegui", "import main", "shared.bus"):
        assert banned not in text, f"the capture script pulls in {banned}"
    assert "gallery_screens" in text


# --- the shots are generated state, so git must not carry the generated ones --
#
# The pair below is one invariant read from both ends. It is derived from
# ``unreachable_reason`` and never from a list of filenames: a hardcoded trio in
# .gitignore is exactly the thing that drifts the day a fourth shot becomes
# unreachable, or the day CDP lands and none of them is.

SHOTS_DIR = "deploy/site/assets/shots"


def _shot_paths():
    """``(regenerated, kept)`` -- repo-relative paths, split by the tool's own answer."""
    shots = [sh for scr in g.SCREENS for sh in scr.shots]
    regenerated = {f"{SHOTS_DIR}/{sh.image}.webp"
                   for sh in shots if not c.unreachable_reason(sh)}
    kept = {f"{SHOTS_DIR}/{sh.image}.webp"
            for sh in shots if c.unreachable_reason(sh)}
    assert kept, ("no shot is unreachable any more -- the negations in "
                  ".gitignore are now dead weight, and this pair of tests with them")
    return regenerated, kept


def _git(*args):
    """Ask git, rather than re-implementing its rules here.

    gitignore negation has a real gotcha -- a negation cannot re-include a file
    whose parent DIRECTORY is excluded -- so a test that reasoned about the
    patterns itself could agree with a .gitignore that does not work.
    """
    import subprocess

    done = subprocess.run(["git", *args],
                          cwd=str(pathlib.Path(repo_paths.REPO_ROOT)),
                          capture_output=True, text=True)
    # check-ignore exits 1 when nothing matched, which is an ANSWER here.
    assert done.returncode in (0, 1), done.stderr
    return {line.strip().replace("\\", "/")
            for line in done.stdout.splitlines() if line.strip()}


def test_git_ignores_exactly_the_shots_the_capture_tool_rewrites():
    """A CAPTURE RUN MUST NOT DIRTY THE TREE, AND MUST NOT UNTRACK THE REST.

    ``tools/promote.sh`` refuses a dirty tree before it stops anything, so a
    daily capture on the box that serves the site would block every promote --
    the same trap, and the same fix, as ``deploy/site/live/*.webp``.

    But a wholesale ignore is wrong here in a way it is not there. The shots
    ``unreachable_reason`` declines are never regenerated by anything, so
    ignoring them would leave a fresh clone with no picture for those tiles
    EVER -- not "until the timer next runs". They stay in git.
    """
    regenerated, kept = _shot_paths()
    ignored = _git("check-ignore", "--no-index", "--", *sorted(regenerated | kept))
    assert ignored == regenerated, (
        f"ignored but the tool never rewrites them: {sorted(ignored & kept)}; "
        f"rewritten every run but still committed: {sorted(regenerated - ignored)}")


def test_the_shots_git_still_carries_are_exactly_the_ones_nothing_regenerates():
    """AN IGNORED FILE THAT IS STILL TRACKED IS STILL DIRTIED.

    .gitignore does not apply to a path already in the index, so the pattern
    above buys nothing until ``git rm --cached`` runs. The two halves look
    identical from the .gitignore alone, and only this side can tell them apart.
    """
    regenerated, kept = _shot_paths()
    tracked = _git("ls-files", "--", SHOTS_DIR)
    assert tracked == kept, (
        f"tracked but rewritten by every capture run: {sorted(tracked & regenerated)}; "
        f"nothing regenerates them and git does not carry them: {sorted(kept - tracked)}")


def test_the_settle_wait_stays_under_the_gamma_pages_auto_refresh_cadence():
    """⚠ SETTLE_MS IS ONE COMMENT AWAY FROM BUYING A DAILY SCHWAB FETCH.

    ``/options/gamma`` mounts ``ui.timer(120.0, _auto_refresh)`` when the page
    may enqueue, and that callback enqueues a ``gamma_refresh`` -- a real chain
    fetch against a budget already running at 68-76k calls a day. The unpinned
    gamma tile is captured with ``_may_enqueue`` true, so that timer exists on
    it; the capture costs nothing today only because Chrome is torn down after
    ``SETTLE_MS`` (12s) and the first fire never arrives.

    Nothing else states that relationship, and the capture tool's own comment
    invites raising the settle time ("the cheap thing to try if captures come
    back showing skeletons"). Raised past the cadence it stops being a rendering
    tweak and becomes a scheduled paid fetch, with nothing on the page or in the
    log to say so. Half the cadence is the bar, not the whole of it: at 119s the
    margin would be one slow page load.

    Read out of the source rather than imported -- ``gamma`` pulls in nicegui
    and the whole page module, and this is a one-number fact.
    """
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "webgui" / "pages" / "options" / "gamma.py").read_text(encoding="utf-8")
    found = re.findall(r"ui\.timer\(([\d.]+),\s*_auto_refresh\)", src)
    assert len(found) == 1, f"expected one gamma auto-refresh timer, found {found}"
    cadence_ms = float(found[0]) * 1000
    assert c.SETTLE_MS < cadence_ms / 2, (
        f"SETTLE_MS={c.SETTLE_MS}ms is no longer comfortably under the "
        f"{cadence_ms:.0f}ms gamma auto-refresh -- the capture would enqueue a "
        f"chain fetch on every run")
