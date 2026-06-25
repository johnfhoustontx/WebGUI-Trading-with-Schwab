"""Tests for the portfolio compute module (Task #20).

``compute`` is the NiceGUI-free engine layer: it builds the portfolio model from
``portfolio-analyzer/src``, computes the slow per-symbol baselines, and formats
the whole thing into display-ready rows via that app's ``view_model`` — the same
orchestration the legacy desktop ``portfolio_analyzer.py`` did across its worker
thread + Tk redraws. Everything is defensive (degrades, never raises) and offline
here: ``data`` is a fake and ``classify`` is injected so no proxy/network/heavy
classifier is touched.
"""
import pandas as pd

from services.portfolio_svc import compute


def _flat_classify(_underlying):
    return {"sector": "Technology", "sector_etf": "XLK"}


class _FakeData:
    """Minimal stand-in for ``PortfolioData`` (offline, injectable)."""

    def __init__(self, positions=None, history=None, account_hash="ACC",
                 transactions=None, raise_positions=False, raise_history=False):
        self._positions = positions or []
        self._history = history or {}
        self._account_hash = account_hash
        self._transactions = transactions or []
        self._raise_positions = raise_positions
        self._raise_history = raise_history

    def get_positions(self):
        if self._raise_positions:
            raise RuntimeError("boom")
        return self._positions

    def get_daily_history(self, symbol, months=12):
        if self._raise_history:
            raise RuntimeError("boom")
        return self._history.get(symbol)

    def first_account_hash(self):
        return self._account_hash

    def account_hashes(self):
        return [self._account_hash] if self._account_hash else []

    def get_transactions(self, account_hash, start, end):
        return self._transactions


def _equity_position(symbol="AAPL", mv=1100.0):
    return {"symbol": symbol, "underlying": symbol, "asset_type": "EQUITY",
            "quantity": 10, "avg_price": 100.0, "market_value": mv,
            "day_pl": 10.0, "total_pl": 100.0}


def _hist(closes):
    n = len(closes)
    dates = pd.date_range("2026-01-02", periods=n, freq="D")
    return pd.DataFrame({"datetime": dates, "open": closes, "high": closes,
                         "low": closes, "close": closes, "volume": [1000] * n})


# --- baseline_signature (recompute-only-when-changed) ----------------------

def _eq_holding(symbol="AAPL", etf="XLK"):
    return {"symbol": symbol, "sector_etf": etf, "asset_type": "EQUITY"}


_BUY = {"instruction": "BUY", "symbol": "AAPL", "quantity": 10, "price": 150.0,
        "trade_date": "2026-01-02"}


def test_baseline_signature_stable_for_same_inputs():
    model = {"holdings": [_eq_holding()], "sectors": []}
    s1 = compute.baseline_signature(model, [_BUY], day="2026-06-19")
    s2 = compute.baseline_signature(model, [dict(_BUY)], day="2026-06-19")
    assert s1 == s2


def test_baseline_signature_changes_on_new_holding():
    base = {"holdings": [_eq_holding()], "sectors": []}
    more = {"holdings": [_eq_holding(), _eq_holding("MSFT", "XLK")], "sectors": []}
    assert (compute.baseline_signature(base, [_BUY], day="2026-06-19")
            != compute.baseline_signature(more, [_BUY], day="2026-06-19"))


def test_baseline_signature_changes_on_new_trade():
    model = {"holdings": [_eq_holding()], "sectors": []}
    extra = dict(_BUY, quantity=5, trade_date="2026-02-02")
    assert (compute.baseline_signature(model, [_BUY], day="2026-06-19")
            != compute.baseline_signature(model, [_BUY, extra], day="2026-06-19"))


def test_baseline_signature_changes_on_day():
    model = {"holdings": [_eq_holding()], "sectors": []}
    assert (compute.baseline_signature(model, [_BUY], day="2026-06-19")
            != compute.baseline_signature(model, [_BUY], day="2026-06-20"))


def test_baseline_signature_ignores_non_equity():
    eq = {"holdings": [_eq_holding()], "sectors": []}
    plus_opt = {"holdings": [_eq_holding(),
                             {"symbol": "AAPL_OPT", "asset_type": "OPTION"}],
                "sectors": []}
    assert (compute.baseline_signature(eq, [_BUY], day="2026-06-19")
            == compute.baseline_signature(plus_opt, [_BUY], day="2026-06-19"))


# --- format_payload --------------------------------------------------------

def test_format_payload_builds_display_rows():
    model = {
        "holdings": [{"symbol": "AAPL", "asset_type": "EQUITY",
                      "sector": "Technology", "sector_etf": "XLK",
                      "quantity": 10, "avg_price": 100.0, "market_value": 1100.0,
                      "day_pl": 10.0, "total_pl": 100.0,
                      "vs_sector_rs": {"1M": 110.0}, "since_purchase_excess": 0.05}],
        "sectors": [{"sector": "Technology", "weight": 1.0,
                     "benchmark_delta": 0.02, "tailwind": None}],
    }
    payload = compute.format_payload(model, {}, proxy_up=True, streaming=True,
                                     errors=[])
    assert payload["holdings_rows"][0]["symbol"] == "AAPL"
    assert payload["holdings_rows"][0]["market_value"] == "$1,100.00"
    assert payload["sector_rows"][0]["weight"] == "100.0%"
    assert isinstance(payload["performance_rows"], list)
    # a card + a suggestion exist for the holding (REVIEW with no baseline)
    assert "AAPL" in payload["suggestions"]
    assert payload["suggestions"]["AAPL"]
    assert payload["proxy_up"] is True and payload["streaming"] is True
    assert payload["errors"] == []
    assert payload["timestamp"]


def test_format_payload_empty_model():
    payload = compute.format_payload({"holdings": [], "sectors": []}, {},
                                     proxy_up=False, streaming=False,
                                     errors=["x"])
    assert payload["holdings_rows"] == []
    assert payload["sector_rows"] == []
    assert payload["performance_rows"] == []
    assert payload["suggestions"] == {}
    assert payload["errors"] == ["x"]


# --- build_raw_model -------------------------------------------------------

def test_build_raw_model_classifies():
    data = _FakeData(positions=[_equity_position()])
    model, errs = compute.build_raw_model(data, trades=[], benchmark={},
                                          classify=_flat_classify)
    assert errs == []
    assert model["holdings"][0]["symbol"] == "AAPL"
    assert model["holdings"][0]["sector"] == "Technology"
    assert model["sectors"][0]["sector"] == "Technology"


def test_build_raw_model_defensive_on_error():
    data = _FakeData(raise_positions=True)
    model, errs = compute.build_raw_model(data, trades=[], benchmark={},
                                          classify=_flat_classify)
    assert model == {"holdings": [], "sectors": []}
    assert errs and "boom" in errs[0]


# --- compute_baselines -----------------------------------------------------

def test_compute_baselines_happy_and_skips_non_equity():
    model = {"holdings": [
        {"symbol": "AAPL", "asset_type": "EQUITY", "sector_etf": "XLK",
         "avg_price": 100.0},
        {"symbol": "SPX 240621C", "asset_type": "OPTION", "sector_etf": "XLK"},
    ]}
    data = _FakeData(history={"AAPL": _hist([100, 101, 102, 103, 104]),
                             "XLK": _hist([50, 50.5, 51, 51.5, 52]),
                             "SPY": _hist([400, 401, 402, 403, 404])})
    baselines = compute.compute_baselines(data, model, trades=[])
    assert "AAPL" in baselines
    assert baselines["AAPL"]["symbol"] == "AAPL"
    assert "SPX 240621C" not in baselines  # options skipped


def test_compute_baselines_defensive():
    model = {"holdings": [{"symbol": "AAPL", "asset_type": "EQUITY",
                           "sector_etf": "XLK", "avg_price": 100.0}]}
    data = _FakeData(raise_history=True)
    # A history failure for SPY/symbol must not raise — just yields no baseline.
    assert compute.compute_baselines(data, model, trades=[]) == {}


# --- load_trades -----------------------------------------------------------

def test_load_trades_reads_store_and_survives_sync(tmp_path):
    store = tmp_path / "entries.json"
    store.write_text('{"last_sync": "2026-06-16", "trades": ['
                     '{"trade_id": "t1", "symbol": "AAPL", "asset_type": "EQUITY",'
                     ' "underlying": "AAPL", "quantity": 10, "price": 100.0,'
                     ' "instruction": "BUY", "trade_date": "2026-01-02"}]}',
                     encoding="utf-8")
    data = _FakeData(account_hash=None)  # no account -> sync no-ops
    trades = compute.load_trades(data, path=store, today="2026-06-16")
    assert len(trades) == 1 and trades[0]["symbol"] == "AAPL"


def test_load_trades_missing_store_is_empty(tmp_path):
    data = _FakeData(account_hash=None)
    assert compute.load_trades(data, path=tmp_path / "nope.json",
                               today="2026-06-16") == []


# --- proxy_up + re-exports -------------------------------------------------

def test_proxy_up(monkeypatch):
    monkeypatch.setattr(compute._proxy, "health", lambda: {"up": True})
    assert compute.proxy_up() is True
    monkeypatch.setattr(compute._proxy, "health", lambda: {"up": False})
    assert compute.proxy_up() is False
    monkeypatch.setattr(compute._proxy, "health",
                        lambda: (_ for _ in ()).throw(RuntimeError()))
    assert compute.proxy_up() is False


def test_apply_tick_and_stream_symbols():
    model = {"holdings": [{"symbol": "AAPL", "asset_type": "EQUITY",
                           "sector": "Technology", "sector_etf": "XLK",
                           "quantity": 10, "avg_price": 100.0,
                           "market_value": 1000.0}],
             "sectors": [{"sector": "Technology", "weight": 1.0}]}
    out = compute.apply_tick(model, {"symbol": "AAPL", "last": 110.0,
                                     "net_change": 1.0})
    assert out["holdings"][0]["market_value"] == 1100.0
    assert compute.stream_symbols(model) == ["AAPL", "XLK", "SPY"]
