"""The quote store: per-symbol quotes with their own fetch times."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import market_store as ms  # noqa: E402


def q(last):
    return {"assetMainType": "EQUITY", "quote": {"lastPrice": last}}


def test_fresh_symbols_are_served_and_the_rest_reported_missing():
    s = ms.QuoteStore()
    s.put_many({"SPY": q(500.0), "QQQ": q(400.0)}, now=100.0)
    fresh, missing, oldest = s.split(["SPY", "IWM", "QQQ"], max_age=5, now=103.0)
    assert fresh == {"SPY": q(500.0), "QQQ": q(400.0)}
    assert missing == ["IWM"] and oldest == 3.0


def test_a_stale_symbol_is_missing():
    s = ms.QuoteStore()
    s.put_many({"SPY": q(500.0)}, now=100.0)
    fresh, missing, _ = s.split(["SPY"], max_age=5, now=105.1)
    assert fresh == {} and missing == ["SPY"]


def test_an_age_limit_of_zero_never_hits():
    s = ms.QuoteStore()
    s.put_many({"SPY": q(500.0)}, now=100.0)
    assert s.split(["SPY"], max_age=0, now=100.0)[1] == ["SPY"]


def test_schwabs_errors_block_is_not_a_symbol():
    s = ms.QuoteStore()
    s.put_many({"SPY": q(500.0), "errors": {"invalidSymbols": ["$DXY"]}}, now=100.0)
    assert s.split(["errors"], max_age=5, now=100.0)[1] == ["errors"]


def test_a_malformed_payload_stores_nothing():
    s = ms.QuoteStore()
    for bad in (None, [], "x", {"SPY": 5}):
        s.put_many(bad, now=100.0)
    assert s.get("SPY", max_age=5, now=100.0) is None


def test_get_returns_one_fresh_quote_or_none():
    s = ms.QuoteStore()
    s.put_many({"SPY": q(500.0)}, now=100.0)
    assert s.get("SPY", max_age=120, now=200.0) == q(500.0)
    assert s.get("SPY", max_age=120, now=221.0) is None
