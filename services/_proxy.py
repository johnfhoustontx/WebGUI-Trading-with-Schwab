"""Shared Schwab proxy accessor for backend services.

Mirrors ``webgui/proxy.py``'s client construction so backend services get a
Schwab client WITHOUT importing anything from ``webgui/`` — the services tier
must not depend on the GUI tier. This is shared by all future services.

Wraps the existing ``schwab-proxy/proxy_client.py`` adapter classes (so services
reuse the battle-tested client code instead of reimplementing it) and adds a
``health()`` helper.

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
