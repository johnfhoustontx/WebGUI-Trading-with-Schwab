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


def test_account_hashes_returns_all(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    monkeypatch.setattr(pd_.session, "get",
        lambda *a, **k: _FakeResp([{"hashValue": "ABC"}, {"hashValue": "DEF"}]))
    assert pd_.account_hashes() == ["ABC", "DEF"]


def test_account_hashes_empty_when_no_accounts(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    monkeypatch.setattr(pd_.session, "get", lambda *a, **k: _FakeResp([]))
    assert pd_.account_hashes() == []


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


# ── the proxy's shared secret (audit SE-02) ──────────────────────────────────
# The proxy's account routes refuse a caller without the secret. This client is
# the only thing that calls them, and it sent no header at all - so the routes
# could never be locked without taking the Portfolio page down.

def test_the_session_carries_the_proxy_secret_when_one_is_configured(monkeypatch):
    monkeypatch.setenv("PROXY_SHARED_SECRET", "s3cret")
    assert PortfolioData(base_url="http://x").session.headers["X-Proxy-Secret"] == "s3cret"


def test_the_session_carries_no_secret_header_when_none_is_configured(monkeypatch):
    import proxy_client
    monkeypatch.delenv("PROXY_SHARED_SECRET", raising=False)
    monkeypatch.setattr(proxy_client, "_client_secret", lambda: None)
    assert "X-Proxy-Secret" not in PortfolioData(base_url="http://x").session.headers


def test_the_session_names_its_caller():
    assert PortfolioData(base_url="http://x").session.headers.get("X-Caller")


class _Refused:
    def __init__(self, status, detail):
        self.status_code, self._detail = status, detail

    def json(self):
        return {"detail": self._detail}

    def raise_for_status(self):
        import requests
        raise requests.HTTPError(f"{self.status_code} Server Error")


def test_a_locked_account_route_reports_the_proxys_own_reason(monkeypatch):
    """The proxy answers 503 with the reason when no secret is configured. A bare
    "503 Server Error" on the Portfolio page says nothing about what to do."""
    pd_ = PortfolioData(base_url="http://x")
    reason = "account routes are locked: no PROXY_SHARED_SECRET is configured"
    monkeypatch.setattr(pd_.session, "get", lambda *a, **k: _Refused(503, reason))
    for call in (pd_.get_positions, pd_.get_accounts,
                 lambda: pd_.get_transactions("H", "2026-01-01", "2026-01-31")):
        with pytest.raises(RuntimeError, match="PROXY_SHARED_SECRET"):
            call()


def test_a_wrong_secret_reports_the_proxys_own_reason(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    monkeypatch.setattr(pd_.session, "get",
                        lambda *a, **k: _Refused(401, "invalid or missing X-Proxy-Secret"))
    with pytest.raises(RuntimeError, match="X-Proxy-Secret"):
        pd_.get_positions()


def test_get_account_positions_keeps_each_account(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")

    def fake_get(url, **k):
        if url.endswith("/accounts"):
            return _FakeResp([{"accountNumber": "11", "hashValue": "H1"},
                              {"accountNumber": "22", "hashValue": "H2"}])
        h = url.rsplit("/", 1)[1]
        body = {"positions": [{"symbol": h}]}
        if h == "H1":
            body["balances"] = {"cash": 1500.0}
        return _FakeResp(body)

    monkeypatch.setattr(pd_.session, "get", fake_get)
    # H2's answer carries no balances (an older proxy): its cash is None
    assert pd_.get_account_positions() == [
        {"account_number": "11", "positions": [{"symbol": "H1"}], "cash": 1500.0},
        {"account_number": "22", "positions": [{"symbol": "H2"}], "cash": None}]


def test_get_account_positions_skips_one_failed_account_and_raises_on_all(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    down = {"H1"}

    def fake_get(url, **k):
        if url.endswith("/accounts"):
            return _FakeResp([{"accountNumber": "11", "hashValue": "H1"},
                              {"accountNumber": "22", "hashValue": "H2"}])
        if url.rsplit("/", 1)[1] in down:
            raise ConnectionError("down")
        return _FakeResp({"positions": []})

    monkeypatch.setattr(pd_.session, "get", fake_get)
    assert [a["account_number"] for a in pd_.get_account_positions()] == ["22"]
    down.add("H2")
    with pytest.raises(RuntimeError):
        pd_.get_account_positions()


def test_stream_options_reads_the_option_stream_with_symbols_as_they_are(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    seen = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_lines(self, decode_unicode=True):
            yield 'data: {"symbol": "SPY   261218P00600000", "mark": 1.5}'

    def fake_get(url, params=None, **k):
        seen["url"], seen["params"] = url, params
        return _Resp()

    monkeypatch.setattr(pd_.session, "get", fake_get)
    ticks = []
    pd_.stream_options(["SPY   261218P00600000"], ticks.append, lambda: False)
    assert seen["url"] == "http://x/stream/options"
    assert seen["params"] == {"symbols": "SPY   261218P00600000"}
    assert ticks == [{"symbol": "SPY   261218P00600000", "mark": 1.5}]



def test_get_names_reads_the_instrument_search_in_one_call(monkeypatch):
    pd_ = PortfolioData(base_url="http://x")
    seen = []

    def fake_get(url, params=None, **k):
        seen.append((url, params))
        return _FakeResp({"instruments": [
            {"symbol": "AAPL", "description": "Apple Inc", "assetType": "EQUITY"},
            {"symbol": "MSFT", "description": ""},
            {"symbol": "ZZZ", "description": "not asked for"},
            "junk"]})

    monkeypatch.setattr(pd_.session, "get", fake_get)
    assert pd_.get_names(["MSFT", "AAPL", "AAPL", ""]) == {"AAPL": "Apple Inc"}
    assert seen == [("http://x/instruments",
                     {"symbol": "AAPL,MSFT", "projection": "symbol-search"})]
    assert pd_.get_names([]) == {}
    assert len(seen) == 1                       # nothing to ask, no call
