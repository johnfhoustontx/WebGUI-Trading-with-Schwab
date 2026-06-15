"""Tests for the options service compute module (Task 2.2).

``compute.run_scan`` is a thin wrapper over ``scanner_engine.run_full_scan``
called with the shared schwab-py-compatible proxy client. We monkeypatch the
eagerly-imported ``run_full_scan`` name so nothing touches a live proxy.

Also asserts the ``options_scoring()`` collision guard (used in the GUI's
``webgui/pages/options/scanner.py``) was intentionally NOT ported: this service
process loads no sentiment code, so ``import scoring`` resolves to
options-scanner's unambiguously and the guard is unnecessary.
"""
from services import _proxy
from services.options_svc import compute


def test_run_scan_calls_engine(monkeypatch):
    sentinel = {"signals_0dte": [], "signals_swing": []}
    seen = {"args": None}

    def _rec(client, *a, **k):
        seen["args"] = client
        return sentinel

    monkeypatch.setattr(compute, "run_full_scan", _rec)

    out = compute.run_scan()
    assert out is sentinel
    assert seen["args"] is _proxy.schwab_py_client


def test_compute_no_scoring_guard():
    # The options_scoring() collision guard must NOT be ported here.
    assert not hasattr(compute, "options_scoring")


# ── Swing scan (moved from webgui/pages/options/swing.py) ────────────────────
def test_assign_ids_adds_unique_ids():
    out = compute.assign_ids([{"symbol": "MU"}, {"symbol": "MU"}], "MU")
    ids = [s["id"] for s in out]
    assert len(set(ids)) == 2
    assert all(i.startswith("MU") for i in ids)


def test_assign_ids_preserves_existing():
    assert compute.assign_ids([{"symbol": "MU", "id": "keep"}], "MU")[0]["id"] == "keep"


def test_swing_scan_pipeline_wiring(monkeypatch):
    """The swing pipeline calls each engine step with the right clients/args and
    scores directly (no ``options_scoring`` guard)."""
    calls = {}

    chain = {"underlyingPrice": 540.0}
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None: (
                            calls.__setitem__("chain_client", client), chain)[1])
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda symbol: (calls.__setitem__("quote_symbol", symbol),
                                        {"last": 541.0})[1])
    monkeypatch.setattr(compute.se, "fetch_price_history",
                        lambda client, symbol: {"hist": True})
    monkeypatch.setattr(compute.se, "calc_technicals", lambda hist: {"rsi": 50})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda client, symbol, price=None, hist=None, chain=None: (
                            calls.__setitem__("iv_price", price),
                            {"expected_moves": {"daily": {"move_dollars": 3.2}}})[1])

    def _screen(chain, symbol, dte_min, dte_max, put_d_min, put_d_max,
                call_d_min, call_d_max, min_cr, kind, spot=None, daily_expected_move=None):
        calls["screen"] = dict(min_cr=min_cr, kind=kind, spot=spot,
                               dem=daily_expected_move)
        return [{"symbol": symbol, "type": "PCS", "short_strike": 530}]

    monkeypatch.setattr(compute.se, "screen_spreads", _screen)
    monkeypatch.setattr(compute.se, "build_iron_condors", lambda spreads: [])

    # ``scoring`` is imported lazily inside swing_scan; patch the module object in
    # sys.modules so the in-function ``import scoring`` resolves to this fake (no
    # collision-guard ceremony — the service binds options-scanner's scoring).
    import sys as _sys
    import types as _types
    fake_scoring = _types.SimpleNamespace(
        score_all_signals=lambda signals, ivs, techs: calls.__setitem__("scored", True))
    monkeypatch.setitem(_sys.modules, "scoring", fake_scoring)

    out = compute.swing_scan("SPY", 5, 30, -0.20, -0.10, 0.10, 0.20, 0.10)

    assert calls["chain_client"] is compute._proxy.schwab_py_client
    assert calls["quote_symbol"] == "SPY"
    assert calls["iv_price"] == 541.0          # quote.last wins over chain price
    assert calls["screen"]["min_cr"] == 0.10   # passed as a fraction, not %
    assert calls["screen"]["kind"] == "SWING"
    assert calls["screen"]["spot"] == 541.0
    assert calls["screen"]["dem"] == 3.2
    assert calls["scored"] is True
    # assign_ids ran -> signal has a unique id.
    assert out and out[0]["id"].startswith("SPY")


# ── Header helpers (moved from webgui/tests/test_options_header.py) ──────────
def test_sentiment_dot_no_data_when_inactive():
    assert compute.sentiment_dot({"active": False})[1] == "No data"
    assert compute.sentiment_dot(None)[1] == "No data"


def test_sentiment_dot_bullish_when_ccs_blocked():
    assert compute.sentiment_dot(
        {"active": True, "allow_ccs": False, "allow_pcs": True})[1] == "Bullish"


def test_sentiment_dot_bearish_when_pcs_blocked():
    assert compute.sentiment_dot(
        {"active": True, "allow_ccs": True, "allow_pcs": False})[1] == "Bearish"


def test_sentiment_dot_neutral_when_both_allowed():
    assert compute.sentiment_dot(
        {"active": True, "allow_ccs": True, "allow_pcs": True})[1] == "Neutral"


def test_quote_last_extracts_last_price():
    raw = {"SPY": {"quote": {"lastPrice": 742.36}}}
    assert compute.quote_last(raw, "SPY") == 742.36


def test_quote_last_missing_returns_none():
    assert compute.quote_last({}, "SPY") is None
    assert compute.quote_last(None, "SPY") is None


class _FakeQuotesResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_refresh_header_shape(monkeypatch):
    raw = {
        "$SPX": {"quote": {"lastPrice": 5400.12}},
        "SPY": {"quote": {"lastPrice": 742.36}},
        "QQQ": {"quote": {"lastPrice": 480.0}},
        "$VIX": {"quote": {"lastPrice": 14.2}},
    }
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_quotes",
                        lambda syms: _FakeQuotesResp(raw))
    monkeypatch.setattr(compute, "vix_regime",
                        lambda v: {"label": "Calm", "color": "#1D9E75"})
    monkeypatch.setattr(compute, "evaluate_regime",
                        lambda: {"active": True, "allow_ccs": True, "allow_pcs": True})

    out = compute.refresh_header()
    assert out["prices"] == {"$SPX": 5400.12, "SPY": 742.36, "QQQ": 480.0}
    assert out["vix"] == 14.2
    assert out["vix_regime"] == {"label": "Calm", "color": "#1D9E75"}
    assert out["sentiment"] == {"color": "#EFC347", "label": "Neutral"}  # neutral dot


def test_refresh_header_quotes_failure_is_blank(monkeypatch):
    def _boom(syms):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_quotes", _boom)
    monkeypatch.setattr(compute, "evaluate_regime", lambda: None)

    out = compute.refresh_header()
    assert out["prices"] == {"$SPX": None, "SPY": None, "QQQ": None}
    assert out["vix"] is None
    assert out["vix_regime"] == {}
    # No active regime -> no-data dot.
    assert out["sentiment"] == {"color": "#666666", "label": "No data"}


def test_refresh_header_sentiment_failure_is_no_data(monkeypatch):
    raw = {"$VIX": {"quote": {"lastPrice": 22.0}}}
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_quotes",
                        lambda syms: _FakeQuotesResp(raw))
    monkeypatch.setattr(compute, "vix_regime", lambda v: {"label": "Elevated"})

    def _boom():
        raise RuntimeError("bridge missing")

    monkeypatch.setattr(compute, "evaluate_regime", _boom)

    out = compute.refresh_header()
    assert out["vix"] == 22.0
    assert out["sentiment"] == {"color": "#666666", "label": "No data"}
