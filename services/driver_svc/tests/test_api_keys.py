"""Tests for driver_svc.api_keys — Anthropic API key resolution.

The resolver must prefer the ``ANTHROPIC_API_KEY`` env var and degrade to
``None`` (never raise) when the key is unset, so the decider can fall back to a
stand-down decision rather than crashing the autonomous cycle.

NOTE: this module is named ``api_keys`` (NOT ``secrets``) on purpose — running
``app.py`` as a script puts ``services/driver_svc`` on ``sys.path``, and a module
named ``secrets`` would SHADOW the Python stdlib ``secrets`` that starlette imports
(``from secrets import token_hex``), crashing the service on launch. See
``test_no_module_shadows_stdlib`` below.
"""
import pathlib
import sys

from services.driver_svc import api_keys


def test_api_key_prefers_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    assert api_keys.anthropic_api_key() == "sk-test-123"


def test_api_key_falls_back_to_file(tmp_path, monkeypatch):
    """Env unset → read (and strip) the gitignored shared key file."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(api_keys, "SHARED_DIR", tmp_path)
    (tmp_path / "anthropic_key.txt").write_text("  sk-from-file-456  \n", encoding="utf-8")
    assert api_keys.anthropic_api_key() == "sk-from-file-456"


def test_api_key_missing_returns_none(tmp_path, monkeypatch):
    """Env unset AND no key file → None (hermetic: point at an empty dir)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(api_keys, "SHARED_DIR", tmp_path)  # empty → no key file
    assert api_keys.anthropic_api_key() is None


def test_no_module_shadows_stdlib():
    """No driver_svc module may shadow a Python stdlib module.

    ``app.py`` runs as a script, which puts ``services/driver_svc`` on ``sys.path``;
    a module here named e.g. ``secrets`` / ``token`` / ``queue`` would shadow the
    stdlib and break a dependency that imports it by bare name (starlette does
    ``from secrets import token_hex``). Regression for the launch crash caused by the
    key resolver originally living in ``secrets.py``.
    """
    pkg_dir = pathlib.Path(__file__).resolve().parents[1]  # services/driver_svc
    stems = {p.stem for p in pkg_dir.glob("*.py") if p.stem != "__init__"}
    clashes = stems & set(sys.stdlib_module_names)
    assert not clashes, (
        f"driver_svc modules shadow stdlib modules: {sorted(clashes)} — rename them "
        "(running app.py as a script puts this dir on sys.path)."
    )
