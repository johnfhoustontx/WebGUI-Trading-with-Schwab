"""The environment gate on market_svc's Claude client factory (Task 4).

A dev environment must not spend money on the Anthropic API. ``ENV_FLAGS
["allow_claude"]`` is False there, and ``_make_summary_client`` then returns
``None`` BEFORE the API key is ever looked up.

``None`` is deliberately NOT a new code path: it is what a production box with
no configured key already returns, so ``generate_summary`` yields the documented
empty narrative and the ticker simply shows its live data items.

NOTE on the autouse fixture: ``conftest._no_live_claude`` replaces
``compute._make_summary_client`` with ``lambda: None`` for the whole suite.
These tests need the REAL function, so it is captured at module import — which
pytest performs during collection, before any fixture body runs — and put back
with ``monkeypatch.setattr`` inside each test. That fixture is now belt-and-
braces (the guard already forces None under pytest) and is deliberately kept:
it also covers a hypothetical run where the pytest gate is bypassed.
"""
from services.market_svc import compute

_REAL_MAKE_CLIENT = compute._make_summary_client


def _boom():
    raise AssertionError(
        "the API key was looked up — the allow_claude guard did not short-circuit")


def test_make_summary_client_is_none_when_claude_suppressed(monkeypatch):
    """allow_claude=False → None, without ever resolving a key."""
    monkeypatch.setattr(compute, "_make_summary_client", _REAL_MAKE_CLIENT)
    monkeypatch.setattr(compute, "_anthropic_api_key", _boom)
    monkeypatch.setitem(compute.ENV_FLAGS, "allow_claude", False)

    assert compute._make_summary_client() is None


def test_make_summary_client_reaches_key_lookup_when_claude_allowed(monkeypatch):
    """allow_claude=True → the guard is inert and the key lookup runs.

    Non-vacuity partner: it asserts pre-existing behavior, so by construction it
    still passes if the guard is deleted.
    """
    monkeypatch.setattr(compute, "_make_summary_client", _REAL_MAKE_CLIENT)
    calls = []

    def _spy():
        calls.append(1)
        return None

    monkeypatch.setattr(compute, "_anthropic_api_key", _spy)
    monkeypatch.setitem(compute.ENV_FLAGS, "allow_claude", True)

    assert compute._make_summary_client() is None
    assert calls == [1]


def test_generate_summary_degrades_to_empty_narrative(monkeypatch):
    """The CALLER degrades rather than raising: no client → empty narrative."""
    monkeypatch.setattr(compute, "_make_summary_client", _REAL_MAKE_CLIENT)
    monkeypatch.setattr(compute, "_anthropic_api_key", _boom)
    monkeypatch.setitem(compute.ENV_FLAGS, "allow_claude", False)

    assert compute.generate_summary({}, {}) == {"narrative": ""}
