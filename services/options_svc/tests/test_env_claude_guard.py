"""The environment gate on options_svc's Claude client factory (Task 4).

A dev environment must not spend money on the Anthropic API. ``ENV_FLAGS
["allow_claude"]`` is False there, and ``_make_analyze_client`` then returns
``None`` BEFORE the API key is ever looked up.

``None`` is deliberately NOT a new code path: it is exactly what a production
box with no configured key already returns, so the gamma-briefing / EOD-recap
callers fall into their existing, exercised "no API key" branch rather than a
novel one. The end-to-end proof of that degradation lives in ``test_compute.py``
beside the other ``gamma_analyze`` no-key tests (which own the chain fakery).

NOTE on the autouse fixture: ``conftest._no_live_claude`` replaces
``compute._make_analyze_client`` with ``lambda: None`` for the whole suite.
These tests need the REAL function, so it is captured at module import — which
pytest performs during collection, before any fixture body runs — and put back
with ``monkeypatch.setattr`` inside each test.
"""
from services.options_svc import compute

_REAL_MAKE_CLIENT = compute._make_analyze_client


def _boom():
    raise AssertionError(
        "the API key was looked up — the allow_claude guard did not short-circuit")


def test_make_analyze_client_is_none_when_claude_suppressed(monkeypatch):
    """allow_claude=False → None, without ever resolving a key."""
    monkeypatch.setattr(compute, "_make_analyze_client", _REAL_MAKE_CLIENT)
    monkeypatch.setattr(compute, "_anthropic_api_key", _boom)
    monkeypatch.setitem(compute.ENV_FLAGS, "allow_claude", False)

    assert compute._make_analyze_client() is None


def test_make_analyze_client_reaches_key_lookup_when_claude_allowed(monkeypatch):
    """allow_claude=True → the guard is inert and the key lookup runs.

    Non-vacuity partner for the test above: it pins that the guard gates on the
    flag rather than disabling Claude outright. It asserts pre-existing
    behavior, so by construction it still passes if the guard is deleted.
    """
    monkeypatch.setattr(compute, "_make_analyze_client", _REAL_MAKE_CLIENT)
    calls = []

    def _spy():
        calls.append(1)
        return None  # no key → the documented None return

    monkeypatch.setattr(compute, "_anthropic_api_key", _spy)
    monkeypatch.setitem(compute.ENV_FLAGS, "allow_claude", True)

    assert compute._make_analyze_client() is None
    assert calls == [1]


def test_make_analyze_client_defaults_to_allowing_claude(monkeypatch):
    """A profile that omits ``allow_claude`` must not silently suppress Claude.

    The guard reads ``.get("allow_claude", True)``, so an older/unknown profile
    keeps today's behavior instead of quietly killing the briefings.
    """
    monkeypatch.setattr(compute, "_make_analyze_client", _REAL_MAKE_CLIENT)
    calls = []
    monkeypatch.setattr(compute, "_anthropic_api_key",
                        lambda: (calls.append(1), None)[1])
    monkeypatch.delitem(compute.ENV_FLAGS, "allow_claude", raising=False)

    assert compute._make_analyze_client() is None
    assert calls == [1]
