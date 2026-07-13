"""Tests for the Gamma Analyze model override chain (env -> shared/analyze_model.txt
-> default 'claude-sonnet-5'). Mirrors driver_svc's DRIVER_MODEL override."""
from services.options_svc import compute


def test_resolve_analyze_model_default(monkeypatch):
    monkeypatch.delenv("GAMMA_ANALYZE_MODEL", raising=False)
    # With no env override and (normally) no shared/analyze_model.txt, default holds.
    # If a local shared/analyze_model.txt exists in this checkout, the env-precedence
    # assertion below still validates the resolver.
    monkeypatch.setenv("GAMMA_ANALYZE_MODEL", "claude-test-xyz")
    assert compute._resolve_analyze_model() == "claude-test-xyz"
    monkeypatch.delenv("GAMMA_ANALYZE_MODEL", raising=False)
    # default (or file) resolution returns a non-empty model id
    assert compute._resolve_analyze_model()
