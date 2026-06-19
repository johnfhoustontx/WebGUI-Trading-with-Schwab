"""Unit tests for SchwabProxyClient.get_intraday_history.

The /pricehistory endpoint is a thin Schwab passthrough; the unit worth testing
is that the client sends the right minute-frequency params and parses candles.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from proxy_client import SchwabProxyClient


def test_get_intraday_history_params_and_shape():
    c = SchwabProxyClient("http://test")
    captured = {}

    def fake_get(endpoint, params=None):
        captured["endpoint"], captured["params"] = endpoint, params
        return {"candles": [{"datetime": 1700000000000, "open": 1, "high": 2,
                             "low": 1, "close": 2, "volume": 10}]}

    c._proxy_get = fake_get
    df = c.get_intraday_history("SPY", minutes=15, days=1)
    assert captured["endpoint"] == "/pricehistory"
    assert captured["params"]["frequencyType"] == "minute"
    assert captured["params"]["frequency"] == 15
    assert captured["params"]["periodType"] == "day"
    assert list(df["close"]) == [2]
    assert "datetime" in df.columns


def test_get_intraday_history_none_when_no_candles():
    c = SchwabProxyClient("http://test")
    c._proxy_get = lambda endpoint, params=None: {"candles": []}
    assert c.get_intraday_history("SPY") is None
    c._proxy_get = lambda endpoint, params=None: None
    assert c.get_intraday_history("SPY") is None
