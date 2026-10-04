"""The one-minute poll marks its requests for the proxy's priority lane.

Audit PF-02: the poll shared the proxy's limiter first come, first served with
a quarter-hour scan's chain burst, ran past its minute and lost the next slot.
The proxy now serves marked requests first (schwab-proxy/rate_gate.py); this is
the collector asking for it.

The mark is passed ONLY to a client that says it understands it
(``supports_priority is True``), so a client double with a fixed signature, or
a Mock whose every attribute is truthy, gets exactly the call it got before.
"""
from datetime import datetime
from unittest.mock import MagicMock

import gex_collector as gc


class _Resp:
    status_code = 500

    def json(self):
        return None


class _Conn:
    def commit(self):
        pass


class _Options:
    class ContractType:
        ALL = "ALL"


class _PriorityClient:
    supports_priority = True
    Options = _Options

    def __init__(self):
        self.chains, self.quotes = [], []

    def get_option_chain(self, symbol, **kw):
        self.chains.append((symbol, kw))
        return _Resp()

    def get_quotes(self, symbols, **kw):
        self.quotes.append((list(symbols), kw))
        return _Resp()


class _PlainClient:
    """A client written before the lane existed: a fixed signature."""
    Options = _Options

    def __init__(self):
        self.chains = []

    def get_option_chain(self, symbol, contract_type=None, from_date=None,
                         to_date=None):
        self.chains.append(symbol)
        return _Resp()

    def get_quotes(self, symbols):
        return _Resp()


def _poll(client, monkeypatch, symbols=("SPY", "QQQ")):
    monkeypatch.setattr(gc, "poll_term_once", lambda *a, **k: None)
    gc.poll_once(client, object(), _Conn(), symbols=list(symbols))


def test_every_chain_fetch_of_the_poll_is_marked(monkeypatch):
    client = _PriorityClient()
    _poll(client, monkeypatch)
    assert sorted(s for s, _ in client.chains) == ["QQQ", "SPY"]
    assert all(kw.get("priority") is True for _, kw in client.chains)


def test_a_client_that_does_not_know_the_mark_is_called_as_before(monkeypatch):
    client = _PlainClient()
    _poll(client, monkeypatch)          # a TypeError here would log, not raise
    assert sorted(client.chains) == ["QQQ", "SPY"]


def test_a_mock_client_is_not_mistaken_for_one_that_supports_it(monkeypatch):
    """Every attribute of a Mock is truthy; only a literal True counts."""
    client = MagicMock()
    client.get_option_chain.return_value = _Resp()
    _poll(client, monkeypatch, symbols=("SPY",))
    assert "priority" not in client.get_option_chain.call_args.kwargs


def test_the_mark_helper_is_strict():
    assert gc._priority_kwargs(_PriorityClient()) == {"priority": True}
    assert gc._priority_kwargs(_PlainClient()) == {}
    assert gc._priority_kwargs(MagicMock()) == {}

    class _Truthy:
        supports_priority = 1

    assert gc._priority_kwargs(_Truthy()) == {}


def test_the_term_structure_fetch_is_marked_too():
    client = _PriorityClient()
    gc.poll_term_once(client, object(), _Conn())
    assert client.chains and client.chains[0][1].get("priority") is True


def test_the_off_hours_price_fetch_is_marked(monkeypatch):
    client = _PriorityClient()
    monkeypatch.setattr(gc, "_is_regular_hours", lambda now: False)
    gc._reanchor_spots(client, ["SPY"], [("SPY", {"underlyingPrice": 1.0})],
                       datetime(2026, 10, 5, 6, 0), spots_out={})
    assert client.quotes == [(["SPY"], {"priority": True})]


def test_the_carry_forward_price_fetch_is_marked():
    client = _PriorityClient()
    tiers = {"fresh_max_age_sec": 20}
    gc._carry_forward(client, [("SPY", {"underlyingPrice": 1.0})], {"SPY": 120.0},
                      tiers, datetime(2026, 10, 5, 10, 0))
    assert client.quotes == [(["SPY"], {"priority": True})]
