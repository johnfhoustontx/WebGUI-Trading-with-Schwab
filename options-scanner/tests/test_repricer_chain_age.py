"""How old a chain a mark, a fill and a Rescue apply may be priced from
(audit AC-141).

The proxy can answer a chain request from one it already holds. The repricer
sent no age limit, so a paper fill was priced from a stored chain up to 45
seconds old - often the very snapshot the signal was built on - and the book
recorded no movement between signal and entry.
"""
import types

import paper_broker
import signal_repricer
from shared import paper_limits


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _client(supports=True):
    calls = []

    def get_option_chain(symbol, **kw):
        calls.append(kw)
        return _Resp({"n": len(calls)})

    c = types.SimpleNamespace(
        get_option_chain=get_option_chain,
        Options=types.SimpleNamespace(ContractType=types.SimpleNamespace(ALL="ALL")))
    if supports:
        c.supports_max_age = True
    return c, calls


def setup_function(_fn):
    signal_repricer.clear_chain_cache()


def test_a_mark_states_how_old_its_chain_may_be():
    c, calls = _client()
    signal_repricer._fetch_chain(c, "SPY", "2030-01-18",
                                 max_age=paper_limits.mark_chain_max_age_sec())
    assert calls[0]["max_age"] == 45


def test_a_fill_asks_for_a_chain_fetched_now():
    c, calls = _client()
    signal_repricer._fetch_chain(c, "SPY", "2030-01-18", max_age=0)
    assert calls[0]["max_age"] == 0


def test_a_fill_does_not_reuse_the_cycles_own_chain():
    """The mark fetched this chain earlier in the cycle. The fill must not be
    priced from it."""
    c, calls = _client()
    first = signal_repricer._fetch_chain(c, "SPY", "2030-01-18", max_age=45)
    again = signal_repricer._fetch_chain(c, "SPY", "2030-01-18", max_age=45)
    assert again is first and len(calls) == 1          # marks share one fetch
    fill = signal_repricer._fetch_chain(c, "SPY", "2030-01-18", max_age=0)
    assert len(calls) == 2 and fill == {"n": 2}
    # ... and what the fill fetched is the newest chain, so later marks use it.
    assert signal_repricer._fetch_chain(c, "SPY", "2030-01-18", max_age=45) == {"n": 2}


def test_a_client_that_takes_no_age_limit_is_called_as_before():
    c, calls = _client(supports=False)
    signal_repricer._fetch_chain(c, "SPY", "2030-01-18", max_age=0)
    assert "max_age" not in calls[0]


def test_no_limit_given_sends_none():
    c, calls = _client()
    signal_repricer._fetch_chain(c, "SPY", "2030-01-18")
    assert "max_age" not in calls[0]


def test_the_two_mark_paths_send_the_mark_limit(monkeypatch):
    seen = []

    def _spy(client, symbol, expiration, max_age=None):
        seen.append(max_age)
        return None

    monkeypatch.setattr(signal_repricer, "_fetch_chain", _spy)
    trade = {"symbol": "SPY", "expiration": "2030-01-18", "strategy": "PCS",
             "short_strike": 500.0, "long_strike": 495.0, "entry_credit": 1.0,
             "legs": []}
    signal_repricer.reprice_swing(trade, object())
    signal_repricer.reprice_legs(trade, object())
    assert seen == [45, 45]


def test_the_paper_broker_prices_a_fill_from_a_fresh_chain(monkeypatch):
    import config_paper
    seen = []

    def _spy(client, symbol, expiration, max_age=None):
        seen.append(max_age)
        return None

    monkeypatch.setattr(signal_repricer, "_fetch_chain", _spy)
    monkeypatch.setattr(config_paper, "PAPER_MODE", True)
    paper_broker.submit_order(
        {"side": "SELL_TO_OPEN", "strategy": "PCS", "symbol": "SPY",
         "expiration": "2030-01-18", "quantity": 1, "short_strike": 500.0,
         "long_strike": 495.0}, object())
    assert seen == [0]


def test_the_mark_limit_is_a_setting(monkeypatch):
    monkeypatch.setattr(paper_limits, "load",
                        lambda: {"marks": {"chain_max_age_sec": 20}})
    assert paper_limits.mark_chain_max_age_sec() == 20
    for bad in (-1, "45", None, True, float("nan")):
        monkeypatch.setattr(paper_limits, "load",
                            lambda bad=bad: {"marks": {"chain_max_age_sec": bad}})
        assert paper_limits.mark_chain_max_age_sec() == 45
