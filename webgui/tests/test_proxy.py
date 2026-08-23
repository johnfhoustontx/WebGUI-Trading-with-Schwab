"""Tests for webgui/proxy.py — the thin proxy client wrapper."""
import pathlib

import requests

import proxy as proxy_mod
from repo_paths import PROXY_URL


def test_proxy_url_from_repo_paths():
    """The wrapper sources its base URL from repo_paths.PROXY_URL."""
    assert proxy_mod.PROXY_URL == PROXY_URL


def test_proxy_health_handles_down(monkeypatch):
    """When the proxy is unreachable, health() returns {"up": False}."""
    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError("proxy down")

    monkeypatch.setattr(proxy_mod.requests, "get", boom)
    assert proxy_mod.health() == {"up": False}


def test_tier1_holds_no_schwab_client():
    """Tier 1 renders; it does not hold a Schwab-capable client.

    proxy.py used to build SchwabProxyClient / SchwabPyProxyClient singletons,
    which required sys.path glue into the hyphenated schwab-proxy folder. Both
    went on 2026-08-21. This is a SOURCE-level check because an attribute check
    alone would not catch the sys.path insertion coming back.
    """
    src = pathlib.Path(proxy_mod.__file__).read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]          # skip the module docstring
    for banned in ("proxy_client", "SCHWAB_PROXY", "SchwabProxyClient",
                   "SchwabPyProxyClient"):
        assert banned not in body, f"webgui/proxy.py regrew engine glue: {banned}"
    assert not hasattr(proxy_mod, "schwab_client")
    assert not hasattr(proxy_mod, "schwab_py_client")
