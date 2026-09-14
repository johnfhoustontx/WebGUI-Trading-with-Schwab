# services/options_svc/tests/conftest.py
import datetime as _dt
import sqlite3 as _sqlite3
import sys as _sys

import pytest

from services.options_svc import compute, handlers
from shared import market_calendar as mc


@pytest.fixture(autouse=True)
def _no_live_claude(monkeypatch):
    """Neutralize real Claude-client resolution across the whole options_svc suite.

    ``compute._make_analyze_client()`` resolves a real ``anthropic.Anthropic``
    client from the environment / gitignored ``shared/anthropic_key.txt`` — on a
    machine with a real key configured (this dev box has one), any test that
    exercises the ``client = client or _make_analyze_client()`` fallback without
    injecting its own fake client would fire a REAL, BILLED API call. This
    already happened once (the ``_research_news`` no-key test silently made a
    live web-search call). Forcing the resolver to ``None`` makes every test's
    real-client path degrade to the documented no-key behavior — no network,
    regardless of which call site (``gamma_analyze``, ``_research_news``, and
    whatever Task 4 adds next) reaches it. Tests that inject their own fake
    client (the overwhelmingly common case in this file) are unaffected — the
    ``client or _make_analyze_client()`` fallback short-circuits before this
    patched function is ever called. Mirrors ``services/market_svc/tests/conftest.py``.
    """
    monkeypatch.setattr(compute, "_make_analyze_client", lambda: None)


# Wed 2026-08-12, 10:00 CT: a plain trading day inside the 08:00–15:20 CT
# collection window and BEFORE the 2026-08-17 extended-hours activation date.
_RTH_NOW = _dt.datetime(2026, 8, 12, 10, 0, tzinfo=mc.CT)


@pytest.fixture(autouse=True)
def _pin_flow_window_clock(monkeypatch):
    """Pin ``handlers._alert_now`` so the flow-window gate is deterministic.

    ``run_flow_alerts`` / ``publish_flow_skew`` are gated on
    ``handlers._flow_window_open()``, which reads the clock. Without this, every
    test exercising them would pass or fail depending on the hour the suite ran
    (green at 10:00 CT on a weekday, red at midnight or on a Saturday). Pinning
    to a fixed in-window moment makes "the gate is open" the default, matching
    what those tests were written against.

    A test that cares about the gate overrides this by monkeypatching
    ``_alert_now`` (or passing ``now=``) in its own body — a later
    ``monkeypatch.setattr`` wins and is undone LIFO. See
    ``test_flow_alert_window.py``.
    """
    monkeypatch.setattr(handlers, "_alert_now", lambda: _RTH_NOW)


@pytest.fixture(autouse=True)
def _in_memory_gex_db(monkeypatch):
    """Give ``gex_history_db.connect`` an EMPTY IN-MEMORY database.

    ``run_flow_alerts`` opens the real ``gex_history.db`` and then reads its
    series behind ``if conn is not None``. That file is gitignored DATA, so on a
    fresh checkout the connect raises, ``conn`` stays None, and the guard
    silently skips the loader the test just monkeypatched — detection sees an
    empty series and the test fails with "lost its alerts".

    The result was a suite that passed or failed on MACHINE STATE: green in a
    checkout that happened to have collected GEX history, red in a fresh worktree
    or on CI. Exactly the same class of problem as ``_pin_flow_window_clock``
    above, which pins the clock so tests do not depend on the hour they run.

    An in-memory DB with the real schema is the honest stand-in: connect
    succeeds, so patched loaders are actually reached, and a test that does NOT
    patch one reads a genuinely empty table instead of a machine's leftovers.
    Tests that fake the whole module via ``sys.modules`` are unaffected — their
    ``setitem`` replaces the module this fixture patched.
    """
    try:
        import gex_history_db as gh
    except Exception:                                   # pragma: no cover
        return                                          # nothing to patch

    def _connect(read_only: bool = False):
        conn = _sqlite3.connect(":memory:", isolation_level=None)
        try:
            gh.init_schema(conn)
        except Exception:                               # pragma: no cover
            pass
        return conn

    monkeypatch.setattr(gh, "connect", _connect)
    # handlers imports it lazily INSIDE the function, so the patch has to land on
    # the module object every later `import gex_history_db` resolves to.
    if "gex_history_db" in _sys.modules:
        monkeypatch.setattr(_sys.modules["gex_history_db"], "connect", _connect,
                            raising=False)


class _NoExpirationList:
    """A non-200 proxy response: ``option_expirations`` reads it as ``[]``."""
    status_code = 503

    def json(self):
        return {}


@pytest.fixture(autouse=True)
def _no_live_expiration_list(monkeypatch):
    """Task 1's grouped fetch lists expirations first; unstubbed, every scan test
    makes a live expirationchain call, which on the VPS reaches Schwab."""
    # On the shared CLIENT, not ``compute.option_expirations``: a test that stubs
    # either one still wins, since its own setattr runs after this. With no list
    # a scan takes the single-fetch fallback those tests were written against.
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_expirations",
                        lambda symbol: _NoExpirationList(), raising=False)


# ── The Strategy Finder's whole-chain scan (2026-09-14) ──────────────────────
# Expiries at DTE 3 (under the 7-day front floor), 10 and 38 (a calendar pair
# 28 days apart), 14 (inside the 10's 7-day gap, so too near to be its back
# month, yet a front of its own) and 400 (the long end no DTE max used to reach).
SCAN_ENV_DTES = (3, 10, 14, 38, 400)


def _whole_chain(dtes=SCAN_ENV_DTES, spot=540.0, delta_iv=0.18, price_iv=0.234,
                 lo=400.0, hi=700.0, step=2.5):
    """A symmetric Black-Scholes chain whose marks are priced RICH: at
    ``price_iv`` (1.3x) against deltas at ``delta_iv``. A flat, fairly priced
    chain builds no credit spread at all - ``screen_spreads``'s edge floor wants
    credit/width above |delta| - and only the rich marks give the real engine
    PCS and CCS on more than one expiry. $2.50 strikes, because a $5 width at
    540 cannot be sized under the $250 per-trade cap."""
    import datetime as dt

    import options_calculator as oc

    calls, puts = {}, {}
    for dte in dtes:
        key = f"{(dt.date.today() + dt.timedelta(days=dte)).isoformat()}:{dte}"
        T = dte / 365.0
        calls[key], puts[key] = {}, {}
        n = int(round((hi - lo) / step))
        for i in range(n + 1):
            k = lo + i * step
            for kind, m in (("call", calls[key]), ("put", puts[key])):
                mark = round(max(oc.bs_price(spot, k, T, oc.RISK_FREE_RATE, price_iv, kind),
                                 0.05), 2)
                m[f"{k}"] = [{
                    "delta": round(oc.bs_delta(spot, k, T, oc.RISK_FREE_RATE, delta_iv, kind), 4),
                    "mark": mark, "bid": round(max(mark - 0.05, 0.01), 2),
                    "ask": round(mark + 0.05, 2), "theta": -0.05, "vega": 0.30,
                    "gamma": 0.01, "volatility": price_iv * 100.0,
                    "totalVolume": 1000, "openInterest": 5000}]
    return {"underlyingPrice": spot, "callExpDateMap": calls, "putExpDateMap": puts}


@pytest.fixture
def scan_env(monkeypatch):
    """``swing_scan`` over :func:`_whole_chain` with every non-chain input
    stubbed, the quality cut off, and the REAL builders and ``screen_spreads``.

    Exposes ``chain``, ``ssn`` (strategy_scanner), ``spot``, ``atm_iv`` (the
    value swing_scan derives from the stubbed $5.00 daily move), ``nearest``
    (the DTE-3 expiry) and ``exp_by_dte``."""
    import math
    import types

    import strategy_scanner as ssn

    chain = _whole_chain()
    spot, daily_move = 540.0, 5.0
    monkeypatch.setattr(compute, "fetch_scan_chain", lambda symbol, dte_max, **_: (chain, 0))
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda symbol: {"last": spot})
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda client, symbol: {"h": 1})
    monkeypatch.setattr(compute.se, "calc_technicals",
                        lambda hist: {"trend": "NEUTRAL", "rsi14": 50,
                                      "price": spot, "sma20": spot})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda client, symbol, price=None, hist=None, chain=None:
                        {"iv_rank": 50.0,
                         "expected_moves": {"daily": {"move_dollars": daily_move}}})
    # The liquidity gate skips its volume and spread checks outside market hours;
    # pinned so the spread set does not depend on the hour the suite runs.
    monkeypatch.setattr(compute.se, "_is_options_market_open", lambda: False)
    monkeypatch.setattr(compute, "SWING_MIN_SCORE", 0.0)
    monkeypatch.setattr(compute, "SWING_EXCLUDED_GRADES", ())
    exp_by_dte = {int(k.split(":")[1]): k.split(":")[0] for k in chain["callExpDateMap"]}
    return types.SimpleNamespace(
        chain=chain, ssn=ssn, spot=spot, atm_iv=daily_move * math.sqrt(365.0) / spot,
        nearest=exp_by_dte[min(exp_by_dte)], exp_by_dte=exp_by_dte)
