"""What the proxy will and will not forward to Schwab's brokerage-account API.

Audit SE-01 and SE-02 (2026-10-03). The proxy holds the Schwab token, and the
Trader API it can reach places real orders. This application is paper-only.

* SE-01: ``/passthrough`` took its ``endpoint`` from the query string unchecked
  and with no secret. Schwab's base URL ends ``/marketdata/v1``, so
  ``/../../trader/v1/accounts/...`` normalised onto the account API: accounts,
  positions, orders and transactions, to anyone who could reach the port.
* SE-02: the account routes' secret check returned without checking when no
  secret was configured - and on the production box none was (measured
  2026-10-03: ``GET /accounts`` answered 200 with no header). A route that
  forwarded a real ORDER sat behind the same check, and nothing called it.
"""
import ast
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi import HTTPException

import schwab_proxy

SOURCE = pathlib.Path(schwab_proxy.__file__).read_text(encoding="utf-8")


@pytest.fixture
def upstream(monkeypatch):
    """Record every call the proxy would send to Schwab's market-data API."""
    calls = []

    def api_request(endpoint, params=None):
        calls.append((endpoint, params))
        return {"status_code": 200, "data": {"ok": True}, "error": None}

    # ``token_mgr`` is created at startup, so it does not exist at import.
    monkeypatch.setattr(schwab_proxy, "token_mgr",
                        types.SimpleNamespace(api_request=api_request), raising=False)
    return calls


# ── SE-01: the passthrough reaches a fixed set of market-data endpoints ──────

def test_the_passthrough_allow_list_is_the_five_endpoints_its_callers_use():
    # proxy_client: /expirationchain and /quotes. The Deep Dive report's client:
    # /quotes, /instruments, /pricehistory and /chains.
    assert schwab_proxy.PASSTHROUGH_ENDPOINTS == frozenset(
        {"/expirationchain", "/quotes", "/instruments", "/pricehistory", "/chains"})


@pytest.mark.parametrize("endpoint", sorted(
    {"/expirationchain", "/quotes", "/instruments", "/pricehistory", "/chains"}))
def test_an_allowed_endpoint_is_forwarded(upstream, endpoint):
    assert schwab_proxy.passthrough(endpoint, "symbol=SPY") == {"ok": True}
    assert upstream == [(endpoint, {"symbol": "SPY"})]


@pytest.mark.parametrize("endpoint", [
    "/../../trader/v1/accounts/accountNumbers",        # the audit's reproduction
    "/../../trader/v1/accounts/ABC/orders",
    "/quotes/../../../trader/v1/accounts",
    "/chains/../../trader/v1/accounts",
    "/accounts", "/orders", "/markets", "/movers/$SPX",
    "quotes", "//quotes", "/quotes/", "/QUOTES", " /quotes", "/quotes?x=1",
    "/quotes#", "/quotes%2F..", "https://evil.example/quotes", "", ".", "/",
])
def test_anything_else_is_refused_before_any_call_is_made(upstream, endpoint):
    with pytest.raises(HTTPException) as err:
        schwab_proxy.passthrough(endpoint, None)
    assert err.value.status_code == 400
    assert upstream == []


def test_the_passthrough_route_carries_the_secret_check():
    (route,) = [r for r in schwab_proxy.app.routes
                if getattr(r, "path", None) == "/passthrough"]
    assert schwab_proxy.require_secret in [d.call for d in route.dependant.dependencies]


# ── SE-02: no order can be placed, and the account routes fail closed ────────

def test_no_route_places_an_order():
    paths = {getattr(r, "path", "") for r in schwab_proxy.app.routes}
    assert not [p for p in paths if "order" in p.lower()]


def test_the_trader_api_is_only_ever_read():
    """Every ``trader_request`` call in the proxy is a GET. A paper-only
    application has no reason to send the brokerage anything else, so a new
    write to the account API must fail here first."""
    methods = []
    for node in ast.walk(ast.parse(SOURCE)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "trader_request"):
            first = node.args[0] if node.args else None
            methods.append(first.value if isinstance(first, ast.Constant) else "<dynamic>")
    assert methods, "the walk found no trader_request call at all"
    assert set(methods) == {"GET"}


def test_trader_request_refuses_a_method_other_than_get(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a non-GET request reached the token or the network")

    session = types.SimpleNamespace(get=boom, post=boom, put=boom, delete=boom,
                                    request=boom)
    monkeypatch.setattr(schwab_proxy, "token_mgr", types.SimpleNamespace(
        session=session, ensure_valid_token=boom, tokens={"AccessToken": "t"}),
        raising=False)
    for method in ("POST", "PUT", "DELETE", "PATCH", "post"):
        with pytest.raises(ValueError):
            schwab_proxy.trader_request(method, "/accounts/ABC/orders", json_body={})


ACCOUNT_ROUTES = {"/accounts", "/positions", "/positions/{account_hash}",
                  "/transactions/{account_hash}"}


def test_every_account_route_carries_the_fail_closed_check():
    seen = set()
    for route in schwab_proxy.app.routes:
        if getattr(route, "path", None) in ACCOUNT_ROUTES:
            deps = [d.call for d in route.dependant.dependencies]
            assert schwab_proxy.require_account_secret in deps, route.path
            seen.add(route.path)
    assert seen == ACCOUNT_ROUTES


def test_no_other_route_reads_the_trader_api_unguarded():
    """A route that calls ``trader_request`` and is not in the guarded set."""
    tree = ast.parse(SOURCE)
    readers = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        calls_trader = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "trader_request" for n in ast.walk(fn))
        if not calls_trader:
            continue
        for dec in fn.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                    and dec.args and isinstance(dec.args[0], ast.Constant)):
                readers.add(dec.args[0].value)
    assert readers <= ACCOUNT_ROUTES, readers - ACCOUNT_ROUTES


def test_with_no_secret_configured_the_account_routes_refuse(monkeypatch):
    monkeypatch.setattr(schwab_proxy, "PROXY_SHARED_SECRET", None)
    for supplied in (None, "", "anything"):
        with pytest.raises(HTTPException) as err:
            schwab_proxy.require_account_secret(supplied)
        assert err.value.status_code == 503
        assert "PROXY_SHARED_SECRET" in err.value.detail


def test_with_a_secret_configured_only_the_right_header_passes(monkeypatch):
    monkeypatch.setattr(schwab_proxy, "PROXY_SHARED_SECRET", "s3cret")
    assert schwab_proxy.require_account_secret("s3cret") is None
    for bad in (None, "", "wrong", "s3cret "):
        with pytest.raises(HTTPException) as err:
            schwab_proxy.require_account_secret(bad)
        assert err.value.status_code == 401


def test_health_says_whether_the_account_routes_are_locked(monkeypatch):
    monkeypatch.setattr(schwab_proxy, "PROXY_SHARED_SECRET", None)
    assert schwab_proxy.account_routes_state() == "locked_no_secret"
    monkeypatch.setattr(schwab_proxy, "PROXY_SHARED_SECRET", "s3cret")
    assert schwab_proxy.account_routes_state() == "secret_required"
