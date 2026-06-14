# portfolio-analyzer/tests/test_data.py
import pytest
from src.data import PortfolioData

class _FakeResp:
    def __init__(self, data): self._d = data; self.status_code = 200
    def json(self): return self._d
    def raise_for_status(self): pass

def test_get_positions_parses_proxy_payload(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    monkeypatch.setattr(pd_.session, "get",
        lambda *a, **k: _FakeResp({"positions": [{"symbol": "AAPL"}]}))
    assert pd_.get_positions() == [{"symbol": "AAPL"}]


def test_get_accounts_returns_proxy_list(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    monkeypatch.setattr(pd_.session, "get",
        lambda *a, **k: _FakeResp([{"hashValue": "ABC"}, {"hashValue": "DEF"}]))
    assert pd_.get_accounts() == [{"hashValue": "ABC"}, {"hashValue": "DEF"}]


def test_first_account_hash_returns_first(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    monkeypatch.setattr(pd_.session, "get",
        lambda *a, **k: _FakeResp([{"hashValue": "ABC"}, {"hashValue": "DEF"}]))
    assert pd_.first_account_hash() == "ABC"


def test_first_account_hash_none_when_empty(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    monkeypatch.setattr(pd_.session, "get", lambda *a, **k: _FakeResp([]))
    assert pd_.first_account_hash() is None


def test_get_daily_history_returns_dataframe(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    candles = [
        {"datetime": 1700000000000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
        {"datetime": 1700086400000, "open": 1.5, "high": 2.5, "low": 1, "close": 2, "volume": 200},
    ]
    monkeypatch.setattr(pd_.session, "get",
        lambda *a, **k: _FakeResp({"candles": candles}))
    df = pd_.get_daily_history("AAPL", months=12)
    assert df is not None
    assert "datetime" in df.columns
    assert len(df) == 2


def test_get_daily_history_returns_none_when_no_candles(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    monkeypatch.setattr(pd_.session, "get",
        lambda *a, **k: _FakeResp({"candles": []}))
    assert pd_.get_daily_history("AAPL", months=12) is None


@pytest.mark.parametrize("months,exp_type,exp_period", [
    (1, "month", 1),
    (3, "month", 3),
    (4, "month", 6),   # the latent bug: 4 must NOT yield period=4 (Schwab rejects it)
    (5, "month", 6),
    (6, "month", 6),
    (12, "year", 1),
])
def test_get_daily_history_uses_valid_schwab_buckets(monkeypatch, months, exp_type, exp_period):
    pd_ = PortfolioData(base_url="http://x")
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResp({"candles": [{"datetime": 1700000000000, "close": 1}]})

    monkeypatch.setattr(pd_.session, "get", fake_get)
    pd_.get_daily_history("AAPL", months=months)
    assert captured["params"]["periodType"] == exp_type
    assert captured["params"]["period"] == exp_period
