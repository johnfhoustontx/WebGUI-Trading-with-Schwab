# services/blog_svc/tests/conftest.py
import pathlib
import shutil
import sys
import threading

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ``_audit`` and ``_fonts_kit`` are not test modules, so pytest does not rewrite
# their asserts by itself; without this their failures would print without
# their messages.
pytest.register_assert_rewrite("services.blog_svc.tests._audit",
                               "services.blog_svc.tests._fonts_kit")


class NetworkReached(BaseException):
    """A test in this suite tried to send a real request.

    ⚠ A ``BaseException`` on purpose, not an ``Exception``. ``fonts.localize``
    promises never to raise and keeps the promise by catching ``Exception``
    around every fetch - so a ``RuntimeError`` raised here was caught there,
    turned into "1 stylesheet could not be fetched", and the test that forgot
    its ``fetch=`` PASSED. Nothing in the service catches ``BaseException``
    (a shutdown must get through), so on the thread the test runs on this gets
    through too.

    ⚠ On that thread ONLY. Raised in a worker thread nobody joins and checks,
    it ends the thread, pytest reports a warning, and the test passes; raised
    inside a thread pool it is kept on a future and nothing is reported at all.
    Raising is therefore half the guard. The other half is ``NetworkGuard``,
    which records every refusal and fails the test when it ends.
    ``test_fonts_fetch.py`` pins both halves."""


class NetworkGuard:
    """What stands where ``requests`` sends, for one test.

    Every call is RECORDED (the name of the thread that made it) before it is
    refused, and ``check`` - run by the fixture when the test ends - fails if
    anything was recorded. Recorded at the point of refusal rather than caught
    in ``threading.excepthook``, because the hook is only called for an
    exception that ESCAPES a thread: a pool, or any worker that catches
    ``BaseException``, never lets it get that far."""

    def __init__(self):
        self.reached = []

    def refuse(self, *_args, **_kwargs):
        self.reached.append(threading.current_thread().name)
        raise NetworkReached("blog_svc tests must not reach the network")

    def check(self) -> None:
        assert not self.reached, (
            "this test tried to reach the network, on thread(s) "
            f"{', '.join(self.reached)}: give localize a fake fetch=, or replace requests.get")

    def expected(self) -> list:
        """For a test OF the guard: what was recorded, forgotten, so that the
        test which reached it on purpose does not fail for it."""
        seen, self.reached = self.reached, []
        return seen


@pytest.fixture(autouse=True)
def _blog_store_in_tmp(monkeypatch, tmp_path):
    """Point the store's DEFAULT paths at this test's own folder.

    ``store.Store()`` with no arguments reads ``repo_paths.BLOG_DATA`` and
    ``BLOG_DB`` when it is made, so patching the two names is enough - and it
    has to be done here, for every test, because the store keeps documents and
    typefaces as plain FILES. The repo-root guard watches ``sqlite3.connect``;
    it would refuse the live database and never notice a draft's document
    landing in the live folder beside it. (The store connects before it makes
    any folder for exactly that reason; ``test_store.py`` pins both halves.)

    ⚠ This shares the test's ``monkeypatch``. A test that calls
    ``monkeypatch.undo()`` takes this off, and the repo-root guard with it:
    use ``with monkeypatch.context()`` for a patch that must end early."""
    import repo_paths
    data = tmp_path / "blog-data"
    monkeypatch.setattr(repo_paths, "BLOG_DATA", data)
    monkeypatch.setattr(repo_paths, "BLOG_DB", data / "blog.db")


@pytest.fixture(autouse=True)
def _blog_site_in_tmp(monkeypatch, tmp_path):
    """Point the site writer at this test's own folder, never at ``deploy/site``.

    ``sitewriter.SITE_ROOT`` is the served root of the public site. A test that
    published an entry would otherwise write ``blog.json`` and ``blog/<slug>/``
    into this checkout (the options_svc conftest does the same for
    ``site_ideas``). The folder gets a COPY of the tracked ``blog.html``,
    because the writer lifts the menu out of that file and writes nothing
    without it; a test about a checkout that lacks the page deletes the copy.

    ⚠ This shares the test's ``monkeypatch``, like the store fixture above."""
    from services.blog_svc import sitewriter
    site = tmp_path / "site"
    site.mkdir()
    shutil.copyfile(_REPO_ROOT / "deploy" / "site" / "blog.html", site / "blog.html")
    monkeypatch.setattr(sitewriter, "SITE_ROOT", site)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """The repo-root conftest guards SQLite but NOT the network. The cleaner
    reaches no network, and fonts.py reads its typefaces through an injected
    ``fetch``, so a real request from this suite is a mistake - make one fail
    loudly rather than hang or escape.

    ``fonts.http_fetch``'s own tests monkeypatch ``requests.get`` on top of
    this; that replacement wins, so they are unaffected.

    A request is refused where it is made (``NetworkReached``) AND fails the
    test when the test ends, whichever thread made it: see ``NetworkGuard``.
    The guard is what a test gets by asking for this fixture.

    ⚠ This shares the test's ``monkeypatch``, like the store fixture above: a
    test that calls ``monkeypatch.undo()`` takes the guard off."""
    yield from guard_the_network(monkeypatch)


def guard_the_network(monkeypatch):
    """The fixture above, as the plain generator it is: put the guard on, hand
    it over, and when the test is done FAIL if it was reached. Separate so that
    the last step - the one nothing else would notice missing - can be driven
    by a test."""
    guard = NetworkGuard()
    monkeypatch.setattr("requests.sessions.Session.send", guard.refuse)
    yield guard
    guard.check()
