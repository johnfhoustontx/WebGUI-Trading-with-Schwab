"""Tests for driver_svc.secrets — Anthropic API key resolution.

The resolver must prefer the ``ANTHROPIC_API_KEY`` env var and degrade to
``None`` (never raise) when the key is unset, so the decider can fall back to a
stand-down decision rather than crashing the autonomous cycle.
"""
from services.driver_svc import secrets


def test_api_key_prefers_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    assert secrets.anthropic_api_key() == "sk-test-123"


def test_api_key_falls_back_to_file(tmp_path, monkeypatch):
    """Env unset → read (and strip) the gitignored shared key file."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "SHARED_DIR", tmp_path)
    (tmp_path / "anthropic_key.txt").write_text("  sk-from-file-456  \n", encoding="utf-8")
    assert secrets.anthropic_api_key() == "sk-from-file-456"


def test_api_key_missing_returns_none(tmp_path, monkeypatch):
    """Env unset AND no key file → None (hermetic: point at an empty dir)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(secrets, "SHARED_DIR", tmp_path)  # empty → no key file
    assert secrets.anthropic_api_key() is None
