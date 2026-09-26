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


# ── get_quote_raw: the dividend pull's one-symbol /quotes passthrough ───────

def _recording_client(stub):
    calls = []
    c = SchwabProxyClient("http://test")

    def fake(path, params=None):
        calls.append((path, params))
        return stub
    c._proxy_get = fake
    return c, calls


def test_get_quote_raw_is_one_symbol_through_the_passthrough():
    payload = {"JPM": {"fundamental": {"divAmount": 5.6}}}
    c, calls = _recording_client(payload)
    assert c.get_quote_raw("JPM") == payload
    assert calls == [("/passthrough", {"endpoint": "/quotes",
                                       "params": "symbols=JPM,fields=fundamental"})]


def test_get_quote_raw_refuses_a_comma():
    """The proxy splits `params` on commas, so a second symbol would be mangled."""
    import pytest
    c, calls = _recording_client({})
    with pytest.raises(ValueError):
        c.get_quote_raw("JPM,KO")
    assert calls == []


def test_get_quote_raw_passes_a_proxy_failure_through_as_none():
    c, _ = _recording_client(None)
    assert c.get_quote_raw("JPM") is None
