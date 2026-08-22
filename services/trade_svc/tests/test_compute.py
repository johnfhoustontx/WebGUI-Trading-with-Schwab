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
    """Stand-in for ``_proxy.schwab_client`` returning synthetic data.

    The analyzed symbol gets a strong uptrend; SPY and the sector ETFs (symbols
    starting ``XL``) get a mild uptrend, so the analyzed symbol outperforms on
    relative strength (drives the RS-vs-SPY/sector factors positive).
    """

    def __init__(self, quote_last=120.0, quote=_UNSET, fundamentals=None):
        self.quote_last = quote_last
        self._quote = ({"last": quote_last, "symbol": "TEST", "volume": 5_000_000}
                       if quote is _UNSET else quote)
        self._fundamentals = fundamentals

    def get_quote(self, symbol):
        if self._quote is None:
            return None
        q = dict(self._quote)
        q.setdefault("symbol", symbol)
        return q

    def get_fundamentals(self, symbol):
        return self._fundamentals

    def _request(self, endpoint, params):
        sym = params.get("symbol", "")
        drift = 0.0003 if (sym == "SPY" or sym.startswith("XL")) else 0.004
        if params.get("frequencyType") == "daily":
            return {"candles": _candles(300, step_ms=86_400_000,
                                        base=self.quote_last, drift=drift)}
        # intraday 5-min bars spread across a few days
        return {"candles": _candles(200, step_ms=300_000,
                                    base=self.quote_last, drift=drift)}


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


def test_analyze_without_fundamentals_degrades_to_hold(monkeypatch):
    """No fundamentals feed -> InvestorVerdict insufficient-data HOLD, flag False."""
    _patch(monkeypatch, FakeClient(quote_last=120.0))  # get_fundamentals -> None
    res = compute.analyze("AAPL")
    assert res["fundamentals_available"] is False
    assert res["investor_verdict"]["verdict"] == "HOLD"
    assert "Insufficient fundamental data" in res["investor_verdict"]["top_reasons"]
    # Display view present but empty of real values.
    assert res["fundamentals"]["pe_ratio"] is None


def test_analyze_with_fundamentals_runs_investor(monkeypatch):
    """Sufficient Schwab fundamentals -> InvestorVerdict runs on real data."""
    strong = {
        "peRatio": 28.0, "pegRatio": 0.8, "revChangeTTM": 20.0,
        "epsChangePercentTTM": 25.0, "returnOnEquity": 30.0,
        "operatingMarginTTM": 25.0, "operatingMarginMRQ": 28.0,
    }
    _patch(monkeypatch, FakeClient(quote_last=120.0, fundamentals=strong))
    res = compute.analyze("AAPL")

    assert res["fundamentals_available"] is True
    iv = res["investor_verdict"]
    # Real computation, not the insufficient-data shortcut.
    assert iv["breakdown"]
    assert "Insufficient fundamental data" not in iv["top_reasons"]
    assert iv["gates_triggered"] != ["No fundamentals"]
    assert iv["verdict"] == "BUY"  # strong fundamentals + RS outperformance

    # Display dict carries the parsed (percent->fraction) values.
    fd = res["fundamentals"]
    assert fd["pe_ratio"] == 28.0
    assert abs(fd["rev_growth_ttm"] - 0.20) < 1e-9
    assert abs(fd["eps_growth_ttm"] - 0.25) < 1e-9
    assert abs(fd["roe"] - 0.30) < 1e-9
    assert fd["margin_expanding"] is True


def test_analyze_fundamentals_fetch_failure_degrades(monkeypatch):
    """A get_fundamentals exception must degrade to insufficient, not raise."""
    class BoomFund(FakeClient):
        def get_fundamentals(self, symbol):
            raise RuntimeError("instruments endpoint down")

    _patch(monkeypatch, BoomFund(quote_last=120.0))
    res = compute.analyze("AAPL")
    assert res["errors"] == []  # analysis still succeeds
    assert res["fundamentals_available"] is False
    assert res["investor_verdict"]["verdict"] == "HOLD"


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


# ── sector P/E median (Phase 1) ──────────────────────────────────────────────

class _PeerPEClient(FakeClient):
    """Serves a P/E per symbol and counts how many fundamental fetches happen."""

    def __init__(self, pes):
        super().__init__()
        self._pes = pes
        self.fetches = 0

    def get_fundamentals(self, symbol):
        self.fetches += 1
        pe = self._pes.get(symbol)
        return {"peRatio": pe} if pe is not None else None


def test_sector_pe_median_is_the_median_of_that_sectors_peers(monkeypatch):
    """The Investor `valuation` component compares a symbol's P/E to its
    SECTOR's median, but live ``analyze`` passed ``sector_pe_median=None``
    unconditionally — so that half never scored, and averaging its structural 0
    halved the surviving PEG score for every symbol ever analyzed.

    The peers are the ones the static sector map already names."""
    client = _PeerPEClient({"AAPL": 35.0, "MSFT": 30.0, "NVDA": 50.0, "AVGO": 40.0})
    _patch(monkeypatch, client)
    compute.reset_sector_pe_cache()
    assert compute.sector_pe_median("AAPL") == 37.5      # median of 30/35/40/50


def test_sector_pe_median_ignores_non_positive_and_missing(monkeypatch):
    """A loss-making peer reports a negative P/E, which is not a valuation the
    median should be dragged by; a peer with no fundamentals contributes
    nothing rather than a zero."""
    client = _PeerPEClient({"AAPL": 20.0, "MSFT": 30.0, "NVDA": -12.0, "AVGO": None})
    _patch(monkeypatch, client)
    compute.reset_sector_pe_cache()
    assert compute.sector_pe_median("AAPL") == 25.0      # median of 20/30 only


def test_sector_pe_median_is_memoized_per_sector(monkeypatch):
    """One fan-out per sector per day — not one per analyze. Without the memo
    every analysis would re-fetch a dozen peers to compute a number that moves
    once a quarter."""
    client = _PeerPEClient({"AAPL": 35.0, "MSFT": 30.0})
    _patch(monkeypatch, client)
    compute.reset_sector_pe_cache()
    compute.sector_pe_median("AAPL")
    first = client.fetches
    assert first > 1                                     # it really did fan out
    compute.sector_pe_median("MSFT")                     # same sector
    assert client.fetches == first                       # served from the memo


def test_sector_pe_median_none_for_an_unmapped_symbol(monkeypatch):
    """An unknown symbol has no sector, so there is no peer set — return None
    (the Investor verdict then scores valuation on PEG alone) rather than a
    median of everything."""
    _patch(monkeypatch, _PeerPEClient({"ZZZZ": 11.0}))
    compute.reset_sector_pe_cache()
    assert compute.sector_pe_median("ZZZZ") is None
