"""Tests for the NiceGUI-free sentiment compute module.

These call real engine code, so we test the safe/defensive paths by
monkeypatching the proxy accessor so nothing requires a live proxy.
"""
import sys

from services import _proxy
from services.sentiment_svc import compute


def test_load_live_returns_none_on_client_error(monkeypatch):
    """load_live swallows engine errors and returns None (defensive).

    We make sectors_ref.load_sectors_data raise so the exception escapes into
    load_live's try/except (compute_live itself tolerates a broken client and
    returns a zeroed snapshot, so patching the client alone won't trip it)."""
    import sectors_ref

    def _raise(*a, **k):
        raise RuntimeError("sectors error")

    monkeypatch.setattr(sectors_ref, "load_sectors_data", _raise)
    assert compute.load_live() is None


def test_proxy_up_false_when_health_raises(monkeypatch):
    """proxy_up returns False when the health check raises (defensive)."""
    def _raise(*a, **k):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(_proxy, "health", _raise)
    assert compute.proxy_up() is False


def test_compute_imports_clean():
    """compute imports without pulling in nicegui or the webgui UI tier."""
    import services.sentiment_svc.compute as c  # noqa: F401
    # The module must not expose a NiceGUI `ui` handle (the page's render does).
    assert not hasattr(compute, "ui")
    # And it must not import nicegui at module scope.
    assert "nicegui" not in compute.__dict__
