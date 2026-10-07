"""The checklist's context on the PUBLIC origin never holds the owner's ledger.

``options:ledger_caps`` is the Paper Ledger's book - what is open, per symbol
and per expiry. The Market Scanner is published as Option Signals, and its
checklist reads a context through ``checks_feed.read_context``; so does the
Trade detail panel, on its own, when its page holds none yet. The refusal
therefore lives in ``read_context`` itself, and these tests drive it there.
"""
import bus_client
import pytest

from pages.options import checks_feed

CAPS = {"open": [{"symbol": "SPY", "risk": 500.0}], "limits": {"per_trade": 750.0}}


@pytest.fixture
def seeded():
    bus_client.reset()
    for memo in checks_feed._memos.values():
        memo.clear()
    bus_client.bus().cache_set(f"cache:{checks_feed.CAPS_VIEW}", CAPS)
    yield
    for memo in checks_feed._memos.values():
        memo.clear()
    bus_client.reset()


@pytest.fixture
def published():
    import shell
    shell.publish({})
    yield
    shell.unpublish()


def test_the_private_origin_reads_the_ledger_caps(seeded):
    """The partner: without it the public test below would pass on a view that
    was never readable at all."""
    assert checks_feed.read_context()["caps"] == CAPS


def test_the_public_origin_never_holds_the_ledger_caps(seeded, published):
    assert checks_feed.read_context()["caps"] is None
    # Whatever the caller asks for: the detail panel's own read takes the default.
    assert checks_feed.read_context(caps=True)["caps"] is None


def test_the_public_origin_never_asks_redis_for_the_ledger_caps(
        seeded, published, monkeypatch):
    """Not held is not enough: a read that was made and thrown away would still
    put the owner's book in this process's memory (the memo keeps it)."""
    asked = []
    real = checks_feed._gated

    def _spy(view):
        asked.append(view)
        return real(view)

    monkeypatch.setattr(checks_feed, "_gated", _spy)
    checks_feed.read_context()
    assert asked, "nothing was read at all - this test would be vacuous"
    assert checks_feed.CAPS_VIEW not in asked
    assert checks_feed._memos[checks_feed.CAPS_VIEW] == {}
