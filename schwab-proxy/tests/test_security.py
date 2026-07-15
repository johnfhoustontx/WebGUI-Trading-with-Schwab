"""Proxy hardening: CORS allowlist + optional shared-secret on trading endpoints
+ the client-side header. All backward-compatible — with nothing configured the
behavior is byte-for-byte as before.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
import requests
from fastapi import HTTPException

import proxy_client
import schwab_proxy
from schwab_proxy import _resolve_cors_origins, require_secret


# ── CORS allowlist ───────────────────────────────────────────────────────────
def test_cors_default_is_not_wildcard(monkeypatch):
    monkeypatch.delenv("PROXY_CORS_ORIGINS", raising=False)
    origins = _resolve_cors_origins()
    assert "*" not in origins                       # the hole is closed by default
    assert "http://127.0.0.1:8500" in origins       # webgui origin allowed


def test_cors_env_override(monkeypatch):
    monkeypatch.setenv("PROXY_CORS_ORIGINS", "http://a.test, http://b.test")
    assert _resolve_cors_origins() == ["http://a.test", "http://b.test"]


def test_cors_wildcard_is_opt_in(monkeypatch):
    monkeypatch.setenv("PROXY_CORS_ORIGINS", "*")
    assert _resolve_cors_origins() == ["*"]          # explicit only


# ── shared-secret dependency ─────────────────────────────────────────────────
def test_require_secret_noop_when_unset(monkeypatch):
    monkeypatch.setattr(schwab_proxy, "PROXY_SHARED_SECRET", None)
    assert require_secret(None) is None
    assert require_secret("anything") is None        # no check at all — back-compat


def test_require_secret_enforced_when_set(monkeypatch):
    monkeypatch.setattr(schwab_proxy, "PROXY_SHARED_SECRET", "s3cret")
    assert require_secret("s3cret") is None           # exact match → allowed
    for bad in (None, "", "wrong"):
        with pytest.raises(HTTPException) as ei:
            require_secret(bad)
        assert ei.value.status_code == 401


def test_sensitive_endpoints_depend_on_require_secret():
    """The account/order/position/transaction routes carry the require_secret guard."""
    guarded = {"/accounts", "/orders/{account_hash}", "/positions",
               "/positions/{account_hash}", "/transactions/{account_hash}"}
    seen = set()
    for route in schwab_proxy.app.routes:
        if getattr(route, "path", None) in guarded:
            dep_calls = [d.call for d in route.dependant.dependencies]
            assert require_secret in dep_calls, f"{route.path} missing require_secret"
            seen.add(route.path)
    assert seen == guarded


# ── client-side header ───────────────────────────────────────────────────────
def test_client_secret_and_apply(monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "abc")
    assert proxy_client._client_secret() == "abc"
    s = requests.Session()
    proxy_client._apply_secret(s)
    assert s.headers.get("X-Proxy-Secret") == "abc"


def test_clients_attach_header_when_configured(monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "xyz")
    assert proxy_client.SchwabProxyClient().session.headers.get("X-Proxy-Secret") == "xyz"
    assert proxy_client.SchwabPyProxyClient().session.headers.get("X-Proxy-Secret") == "xyz"


def test_clients_send_no_header_when_unset(monkeypatch):
    monkeypatch.delenv("PROXY_SHARED_SECRET", raising=False)
    monkeypatch.setattr(proxy_client, "_client_secret", lambda: None)
    assert "X-Proxy-Secret" not in proxy_client.SchwabProxyClient().session.headers
