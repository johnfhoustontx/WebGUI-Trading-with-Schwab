"""Tests for webgui/proxy.py — the thin proxy client wrapper."""
import requests

import proxy as proxy_mod
from repo_paths import PROXY_URL


def test_proxy_url_from_repo_paths():
    """The wrapper sources its base URL from repo_paths.PROXY_URL."""
    assert proxy_mod.PROXY_URL == PROXY_URL
    assert proxy_mod.schwab_py_client.base == PROXY_URL
    assert proxy_mod.schwab_client.base == PROXY_URL


def test_proxy_health_handles_down(monkeypatch):
    """When the proxy is unreachable, health() returns {"up": False}."""
    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError("proxy down")

    monkeypatch.setattr(proxy_mod.requests, "get", boom)
    assert proxy_mod.health() == {"up": False}
