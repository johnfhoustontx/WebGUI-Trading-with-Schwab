"""SchwabProxyClient's flattened quote carries the time of the last trade.

The sentiment composite compares $VIX1D and $VIX9D with $VIX. Outside regular
hours $VIX keeps printing while the other two stop at 15:15 CT, so the ratio
paired this morning's VIX with yesterday's VIX1D. The trade time is what lets
the composite tell the two apart; the flattened mapping dropped it.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from proxy_client import SchwabProxyClient

_VIX = {"lastPrice": 17.03, "closePrice": 16.91, "netChange": 0.12,
        "netPercentChange": 0.71, "tradeTime": 1761735000000}


def test_get_quotes_carries_the_trade_time():
    c = SchwabProxyClient("http://test")
    c._proxy_get = lambda endpoint, params=None: {"$VIX": {"quote": dict(_VIX)}}
    q = c.get_quotes(["$VIX"])["$VIX"]
    assert q["trade_time"] == 1761735000000
    assert q["last"] == 17.03


def test_get_quote_carries_the_trade_time():
    c = SchwabProxyClient("http://test")
    c._proxy_get = lambda endpoint, params=None: {"$VIX": {"quote": dict(_VIX)}}
    assert c.get_quote("$VIX")["trade_time"] == 1761735000000


def test_a_quote_without_a_trade_time_says_none():
    c = SchwabProxyClient("http://test")
    c._proxy_get = lambda endpoint, params=None: {"SPY": {"quote": {"lastPrice": 1.0}}}
    assert c.get_quotes(["SPY"])["SPY"]["trade_time"] is None
