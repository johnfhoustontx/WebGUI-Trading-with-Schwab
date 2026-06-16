"""Tests for the trade service compute orchestration (Task #25).

We monkeypatch ``compute._proxy.schwab_client`` with a fake that serves
synthetic OHLCV candles so nothing touches a live proxy. The synthetic series
is a gentle uptrend with enough bars (300 daily / 200 intraday) to satisfy every
indicator's lookback, so ``analyze`` exercises the full orchestration.
"""
import pandas as pd
import pytest

from services.trade_svc import compute


def _candles(n, start_ms=1_600_000_000_000, step_ms=86_400_000,
             base=100.0, drift=0.001):
    """n synthetic candles: a smooth uptrend, strictly positive OHLCV."""
    out = []
    prev = base
    for i in range(n):
        close = base * (1 + drift * i)
        out.append({
            "datetime": start_ms + i * step_ms,
            "open": prev,
            "high": close * 1.01,
            "low": min(prev, close) * 0.99,
            "close": close,
            "volume": 1_000_000 + (i % 7) * 10_000,
        })
        prev = close
    return out


_UNSET = object()


class FakeClient:
    """Stand-in for ``_proxy.schwab_client`` returning synthetic data."""

    def __init__(self, quote_last=120.0, quote=_UNSET):
        self.quote_last = quote_last
        self._quote = ({"last": quote_last, "symbol": "TEST", "volume": 5_000_000}
                       if quote is _UNSET else quote)

    def get_quote(self, symbol):
        if self._quote is None:
            return None
        q = dict(self._quote)
        q.setdefault("symbol", symbol)
        return q

    def _request(self, endpoint, params):
        if params.get("frequencyType") == "daily":
            return {"candles": _candles(300, step_ms=86_400_000,
                                        base=self.quote_last)}
        # intraday 5-min bars spread across a few days
        return {"candles": _candles(200, step_ms=300_000,
                                    base=self.quote_last)}


def _patch(monkeypatch, client):
    monkeypatch.setattr(compute._proxy, "schwab_client", client)


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_rs_percentile_neutral_when_ref_missing():
    s = pd.Series(range(100))
    assert compute.rs_percentile(s, None, 63) == 0.5
    assert compute.rs_percentile(s, pd.Series(range(10)), 63) == 0.5  # ref too short


def test_rs_percentile_outperformance_above_half():
    strong = pd.Series([100 * (1.005 ** i) for i in range(70)])
    flat = pd.Series([100.0] * 70)
    assert compute.rs_percentile(strong, flat, 63) > 0.5


def test_resolve_sector_known_and_unknown():
    assert compute.resolve_sector("AAPL") == {"name": "Technology", "etf": "XLK"}
    assert compute.resolve_sector("nvda") == {"name": "Technology", "etf": "XLK"}
    assert compute.resolve_sector("ZZZZ") == {"name": "", "etf": ""}


# ── orchestration ────────────────────────────────────────────────────────────

def test_analyze_returns_full_structure(monkeypatch):
    _patch(monkeypatch, FakeClient(quote_last=120.0))
    res = compute.analyze("AAPL")

    assert res is not None
    assert res["symbol"] == "AAPL"
    assert res["price"] == 120.0
    assert res["errors"] == []
    assert res["fundamentals_available"] is False

    # Both verdicts present and well-formed.
    for key in ("position_verdict", "investor_verdict"):
        v = res[key]
        assert v["verdict"] in ("BUY", "HOLD", "SELL")
        assert isinstance(v["breakdown"], list)
        assert isinstance(v["gates_triggered"], list)

    # No fundamentals wired -> investor degrades to insufficient-data HOLD.
    assert res["investor_verdict"]["verdict"] == "HOLD"
    assert "Insufficient fundamental data" in res["investor_verdict"]["top_reasons"]

    # Momentum + alignment + sector populated.
    assert set(res["momentum"]) >= {"rsi", "adx", "macd_hist", "relative_volume"}
    assert "alignment_percentage" in res["ema_alignment"]
    assert res["sector"]["etf"] == "XLK"
    assert "score" in res["sector"]["strength"]


def test_analyze_result_is_contract_valid(monkeypatch):
    """The compute dict must project cleanly onto the TradeAnalysis contract."""
    from shared.contracts.trade import TradeAnalysis

    _patch(monkeypatch, FakeClient())
    res = compute.analyze("MSFT")
    ta = TradeAnalysis(**{k: res.get(k) for k in TradeAnalysis.model_fields
                          if k in res})
    assert TradeAnalysis.from_json(ta.to_json()).symbol == "MSFT"


def test_analyze_empty_symbol_returns_none(monkeypatch):
    _patch(monkeypatch, FakeClient())
    assert compute.analyze("") is None
    assert compute.analyze("   ") is None


def test_analyze_no_quote_degrades(monkeypatch):
    _patch(monkeypatch, FakeClient(quote=None))
    res = compute.analyze("AAPL")
    assert res["symbol"] == "AAPL"
    assert res["errors"] and "quote" in res["errors"][0].lower()
    assert "position_verdict" not in res or res.get("position_verdict") in (None, {})


def test_analyze_thin_history_degrades(monkeypatch):
    class ThinClient(FakeClient):
        def _request(self, endpoint, params):
            return {"candles": _candles(10)}  # < 50 daily bars

    _patch(monkeypatch, ThinClient())
    res = compute.analyze("AAPL")
    assert res["errors"] and "history" in res["errors"][0].lower()


def test_analyze_never_raises_on_client_explosion(monkeypatch):
    class BoomClient:
        def get_quote(self, symbol):
            raise RuntimeError("proxy down")

        def _request(self, endpoint, params):
            raise RuntimeError("proxy down")

    _patch(monkeypatch, BoomClient())
    # get_quote raising is caught -> treated as no quote -> graceful error dict.
    res = compute.analyze("AAPL")
    assert res["symbol"] == "AAPL"
    assert res["errors"]
