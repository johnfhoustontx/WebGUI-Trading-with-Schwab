# services/blog_svc/tests/conftest.py
import pathlib
import sys

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ``_audit`` is not a test module, so pytest does not rewrite its asserts by
# itself; without this its failures would print without their messages.
pytest.register_assert_rewrite("services.blog_svc.tests._audit")


class NetworkReached(BaseException):
    """A test in this suite tried to send a real request.

    ⚠ A ``BaseException`` on purpose, not an ``Exception``. ``fonts.localize``
    promises never to raise and keeps the promise by catching ``Exception``
    around every fetch - so a ``RuntimeError`` raised here was caught there,
    turned into "1 stylesheet could not be fetched", and the test that forgot
    its ``fetch=`` PASSED. Nothing in the service catches ``BaseException``
    (a shutdown must get through), so this gets through too.
    ``test_fonts_review.py`` pins it."""


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """The repo-root conftest guards SQLite but NOT the network (CLAUDE.md says
    otherwise and is wrong). The cleaner reaches no network, and fonts.py reads
    its typefaces through an injected ``fetch``, so a real request from this
    suite is a mistake - make one fail loudly rather than hang or escape.

    ``fonts.http_fetch``'s own tests monkeypatch ``requests.get`` on top of
    this; that replacement wins, so they are unaffected."""
    def refuse(*_args, **_kwargs):
        raise NetworkReached("blog_svc tests must not reach the network")
    monkeypatch.setattr("requests.sessions.Session.send", refuse)
