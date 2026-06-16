"""Unit tests for SchwabProxyClient.get_fundamentals unwrapping.

The /instruments endpoint itself is a thin passthrough to Schwab (verified live);
the unit worth testing is the client's unwrapping of
``instruments[0].fundamental`` and its graceful-None handling.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from proxy_client import SchwabProxyClient


def _client(stub):
    c = SchwabProxyClient("http://test")
    c._proxy_get = lambda path, params=None: stub
    return c


def test_get_fundamentals_unwraps_inner_dict():
    fundamental = {"peRatio": 35.9, "revChangeTTM": 12.7, "returnOnEquity": 141.4}
    c = _client({"instruments": [{"symbol": "AAPL", "fundamental": fundamental}]})
    assert c.get_fundamentals("AAPL") == fundamental


def test_get_fundamentals_none_when_proxy_empty():
    assert _client(None).get_fundamentals("AAPL") is None


def test_get_fundamentals_none_when_no_instruments():
    assert _client({"instruments": []}).get_fundamentals("AAPL") is None
    assert _client({}).get_fundamentals("AAPL") is None


def test_get_fundamentals_none_when_instrument_lacks_fundamental():
    c = _client({"instruments": [{"symbol": "AAPL"}]})
    assert c.get_fundamentals("AAPL") is None
