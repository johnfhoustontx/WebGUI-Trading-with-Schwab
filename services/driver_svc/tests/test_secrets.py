"""Tests for driver_svc.secrets — Anthropic API key resolution.

The resolver must prefer the ``ANTHROPIC_API_KEY`` env var and degrade to
``None`` (never raise) when the key is unset, so the decider can fall back to a
stand-down decision rather than crashing the autonomous cycle.
"""
from services.driver_svc import secrets


def test_api_key_prefers_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    assert secrets.anthropic_api_key() == "sk-test-123"


def test_api_key_missing_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # no shared file in the test env
    assert secrets.anthropic_api_key() in (None, "")
