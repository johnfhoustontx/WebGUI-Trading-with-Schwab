"""Thin proxy client for the NiceGUI webgui app.

Wraps the existing ``schwab-proxy/proxy_client.py`` adapter classes (so pages
reuse the battle-tested client code instead of reimplementing it) and adds a
``health()`` helper the shell uses to render a proxy-down banner.

Run order reminder: the schwab-proxy must be started first (``:8100``); every
feature backend resolves its Schwab client + market data through it.
"""
import pathlib
import sys

import requests

# Repo root on sys.path -> repo_paths + the hyphenated app folders are importable.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import PROXY_URL, SCHWAB_PROXY  # noqa: E402

# schwab-proxy contains a hyphen, so it can't be a normal package import; add the
# folder to sys.path and import the module by name.
if str(SCHWAB_PROXY) not in sys.path:
    sys.path.insert(0, str(SCHWAB_PROXY))

from proxy_client import SchwabProxyClient, SchwabPyProxyClient  # noqa: E402

# Shared singletons callers can reuse.
#   schwab_py_client -> schwab-py compatible (Options Scanner engines)
#   schwab_client    -> SchwabClient compatible (Sentiment / others)
schwab_py_client = SchwabPyProxyClient(PROXY_URL)
schwab_client = SchwabProxyClient(PROXY_URL)


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
