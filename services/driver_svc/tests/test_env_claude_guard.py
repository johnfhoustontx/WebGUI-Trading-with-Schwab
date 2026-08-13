"""The environment gate on driver_svc's Claude client factory (Task 4).

A dev environment must not spend money on the Anthropic API. ``ENV_FLAGS
["allow_claude"]`` is False there, and ``decider._make_client`` then returns
``None`` BEFORE the API key is ever looked up.

``None`` is deliberately NOT a new code path: it is what a production box with
no configured key already returns, so ``decide`` produces its existing
``reason="no_key"`` stand-down — the safe failure mode the whole decider is
built around — rather than anything novel.
"""
from services.driver_svc import decider


def _boom():
    raise AssertionError(
        "the API key was looked up — the allow_claude guard did not short-circuit")


def test_make_client_is_none_when_claude_suppressed(monkeypatch):
    """allow_claude=False → None, without ever resolving a key."""
    monkeypatch.setattr(decider.api_keys, "anthropic_api_key", _boom)
    monkeypatch.setitem(decider.ENV_FLAGS, "allow_claude", False)

    assert decider._make_client() is None


def test_make_client_reaches_key_lookup_when_claude_allowed(monkeypatch):
    """allow_claude=True → the guard is inert and the key lookup runs.

    Non-vacuity partner: it asserts pre-existing behavior, so by construction it
    still passes if the guard is deleted.
    """
    calls = []

    def _spy():
        calls.append(1)
        return None  # no key → the documented None return

    monkeypatch.setattr(decider.api_keys, "anthropic_api_key", _spy)
    monkeypatch.setitem(decider.ENV_FLAGS, "allow_claude", True)

    assert decider._make_client() is None
    assert calls == [1]


def test_decide_stands_down_when_claude_suppressed(monkeypatch):
    """The CALLER degrades: no client → the existing 'no_key' stand-down."""
    monkeypatch.setattr(decider.api_keys, "anthropic_api_key", _boom)
    monkeypatch.setitem(decider.ENV_FLAGS, "allow_claude", False)

    d = decider.decide({"menu": [{"id": "m0"}]})  # must not raise

    assert d["stand_down"] is True
    assert d["trades"] == []
    assert d["reason"] == decider.REASON_NO_KEY
