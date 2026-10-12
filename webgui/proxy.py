"""Thin proxy client for the NiceGUI webgui app.

Four HTTP helpers over the schwab-proxy, none of which raises. Three read:
``health()`` (the shell's proxy-down banner + the Status page),
``api_call_stats()`` (the Settings "API usage" card) and ``auth_info()``
(Schwab's login link, for the Status page's Authorize dialog). One writes:
``auth_exchange()`` hands the proxy the address Schwab redirected to, which is
how the 7-day sign-in is renewed from inside the app. Every call goes to the
proxy on loopback; nothing here talks to Schwab.

This module used to also build ``schwab_py_client`` / ``schwab_client``
singletons out of ``schwab-proxy/proxy_client.py``, which meant Tier 1 carried
sys.path glue into a hyphenated app folder and held live Schwab-capable clients.
Nothing had called them since the 3-tier migration - every page reads Redis - so
they went on 2026-08-21, completing "remove the last sys.path engine-glue from
webgui" from the 3-tier plan. If a page ever appears to need one, that is the
signal it should be reading a cache view instead.

Run order reminder: the schwab-proxy must be started first (``:8100``); every
feature backend resolves its Schwab client + market data through it.
"""
import pathlib
import sys

import requests

# Repo root on sys.path -> repo_paths is importable.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import PROXY_URL  # noqa: E402


def health(base_url: str = PROXY_URL, timeout: float = 3.0) -> dict:
    """Return proxy health as a dict; never raises.

    Always includes an ``"up"`` bool. When the proxy is unreachable returns
    ``{"up": False}``. When reachable, the proxy's ``/health`` JSON (status,
    token info, ...) is merged in with ``up`` set from HTTP 200 + status "ok".
    """
    try:
        resp = requests.get(f"{base_url}/health", timeout=timeout)
    except Exception:
        return {"up": False}
    if resp.status_code != 200:
        return {"up": False, "status_code": resp.status_code}
    try:
        data = resp.json()
    except Exception:
        return {"up": False}
    data["up"] = data.get("status") == "ok"
    return data


def api_call_stats(base_url: str = PROXY_URL, timeout: float = 3.0) -> dict | None:
    """Outbound Schwab API-call counts from the proxy's ``/stats/api_calls``
    (``{"today", "last_7_days", "last_30_days", "since"}``), or None when the
    proxy is unreachable / predates the endpoint. Never raises — the Settings
    "API usage" card renders a friendly placeholder on None."""
    try:
        resp = requests.get(f"{base_url}/stats/api_calls", timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) and "today" in data else None
    except Exception:
        return None


def auth_info(base_url: str = PROXY_URL, timeout: float = 3.0) -> dict | None:
    """``{"auth_url", "callback_url"}`` from the proxy's ``/auth/info``, or None
    when the proxy is unreachable, predates the route, or names anything but an
    https link. Never raises. ``auth_url`` is Schwab's own login page; it opens
    in the viewer's browser."""
    try:
        resp = requests.get(f"{base_url}/auth/info", timeout=timeout)
        data = resp.json() if resp.status_code == 200 else None
    except Exception:
        return None
    url = data.get("auth_url") if isinstance(data, dict) else None
    if not (isinstance(url, str) and url.startswith("https://")):
        return None
    return data


# The proxy gives Schwab 30 s to answer the exchange; wait a little longer.
SIGN_IN_TIMEOUT_SEC = 45.0


def auth_exchange(pasted, base_url: str = PROXY_URL,
                  timeout: float = SIGN_IN_TIMEOUT_SEC) -> tuple:
    """Sign in to Schwab from the pasted redirect address: ``(ok, message)``.

    ``(True, "")`` only when the proxy answers 200 with ``{"ok": true}``;
    anything else is ``(False, why)``. Never raises. Blocking for as long as
    Schwab takes - call it off the event loop. The address travels in the
    request BODY so its one-time code stays out of the proxy's access log."""
    try:
        resp = requests.post(f"{base_url}/auth/exchange", json={"url": pasted},
                             timeout=timeout)
    except Exception:
        return (False, "The proxy did not answer. Check it on this page, then "
                       "try again.")
    try:
        body = resp.json()
    except Exception:
        body = None
    if resp.status_code == 200 and isinstance(body, dict) and body.get("ok") is True:
        return (True, "")
    error = body.get("error") if isinstance(body, dict) else None
    if not (isinstance(error, str) and error):
        error = f"The proxy answered {resp.status_code}."
    return (False, error)
