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


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """The repo-root conftest guards SQLite but NOT the network (CLAUDE.md says
    otherwise and is wrong). The cleaner reaches no network, and fonts.py reads
    its typefaces through an injected ``fetch``, so a real request from this
    suite is a mistake - make one fail loudly rather than hang or escape.

    ``fonts.http_fetch``'s own tests monkeypatch ``requests.get`` on top of
    this; that replacement wins, so they are unaffected."""
    def refuse(*_args, **_kwargs):
        raise RuntimeError("blog_svc tests must not reach the network")
    monkeypatch.setattr("requests.sessions.Session.send", refuse)
