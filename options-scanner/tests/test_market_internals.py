"""Tests for _fetch_market_internals — quote fetch + graceful per-ticker failure."""
from unittest.mock import MagicMock
from gamma_tool import _fetch_market_internals


def _stub_quote_client(values):
    """values: dict mapping requested symbol -> dict value to return.

    Use None for a value to simulate a fetch failure for that symbol.
    """
    client = MagicMock()

    def _get_quote(sym):
        resp = MagicMock()
        v = values.get(sym)
        if v is None:
            resp.status_code = 500
            resp.json.return_value = None
            return resp
        resp.status_code = 200
        resp.json.return_value = {sym: {"quote": {"lastPrice": v}}}
        return resp

    client.get_quote.side_effect = _get_quote
    return client


def test_all_three_indicators_returned():
    client = _stub_quote_client({"$CPCE": 0.78, "$ADD": 1450.0, "SKEW": 138.4})
    result = _fetch_market_internals(client)
    assert result["cpce"] == 0.78
    assert result["ad"] == 1450
    assert result["skew"] == 138.4


def test_ad_can_be_negative():
    """$ADD is a signed daily net — must preserve negative values."""
    client = _stub_quote_client({"$CPCE": 0.78, "$ADD": -1850.0, "SKEW": 138.4})
    result = _fetch_market_internals(client)
    assert result["ad"] == -1850


def test_one_failure_does_not_break_others():
    client = _stub_quote_client({"$CPCE": 0.78, "$ADD": None, "SKEW": 138.4})
    result = _fetch_market_internals(client)
    assert result["cpce"] == 0.78
    assert result["ad"] is None
    assert result["skew"] == 138.4


def test_all_failures_returns_dict_of_nones():
    client = _stub_quote_client({"$CPCE": None, "$ADD": None, "SKEW": None})
    result = _fetch_market_internals(client)
    assert result == {"cpce": None, "ad": None, "skew": None}


def test_exception_handled_per_ticker():
    """A client raising an exception on one symbol should still allow others."""
    client = MagicMock()

    def _get_quote(sym):
        if sym == "$ADD":
            raise RuntimeError("network down")
        resp = MagicMock(status_code=200,
                         json=lambda s=sym: {s: {"quote": {"lastPrice": 1.0}}})
        return resp

    client.get_quote.side_effect = _get_quote
    result = _fetch_market_internals(client)
    assert result["ad"] is None
    assert result["cpce"] is not None
    assert result["skew"] is not None


def test_internals_block_renders_all_three():
    from gamma_tool import build_internals_block
    out = build_internals_block({"cpce": 0.78, "ad": 1450, "skew": 138.4})
    assert "=== MARKET INTERNALS ===" in out
    assert "0.78" in out
    assert "1450" in out or "+1450" in out
    assert "138.4" in out
    assert "fear" in out.lower() or "greed" in out.lower()


def test_internals_block_handles_failure():
    from gamma_tool import build_internals_block
    out = build_internals_block({"cpce": None, "ad": 1450, "skew": None})
    assert ("CPCE: fetch failed" in out
            or ("Put/Call" in out and "fetch failed" in out))
    assert "SKEW" in out and "fetch failed" in out
    assert "1450" in out or "+1450" in out


def test_internals_block_ad_bullish_strong():
    """A/D net well above +1500 is broadly bullish."""
    from gamma_tool import build_internals_block
    out = build_internals_block({"cpce": 0.78, "ad": 2200, "skew": 130.0})
    assert "broadly bullish" in out.lower()
    assert "+2200" in out


def test_internals_block_ad_bearish_strong():
    """A/D net well below -1500 is broadly bearish."""
    from gamma_tool import build_internals_block
    out = build_internals_block({"cpce": 0.78, "ad": -2000, "skew": 130.0})
    assert "broadly bearish" in out.lower()
    assert "-2000" in out


def test_internals_block_ad_balanced():
    """A/D within +/-500 reads as mixed / balanced."""
    from gamma_tool import build_internals_block
    out = build_internals_block({"cpce": 0.78, "ad": 120, "skew": 130.0})
    assert "mixed" in out.lower() or "balanced" in out.lower()


def test_internals_block_ad_leaning():
    """A/D between thresholds reads as 'leaning' rather than 'broadly'."""
    from gamma_tool import build_internals_block
    out = build_internals_block({"cpce": 0.78, "ad": 900, "skew": 130.0})
    assert "leaning" in out.lower()
