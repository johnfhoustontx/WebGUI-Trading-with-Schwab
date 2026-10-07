"""A symbol with nothing listed inside the collection window is not asked every
minute.

Found on prod 2026-10-06, the first day with a wider watchlist. Eight names with
monthly options only (AON, AZO, CMI, FANG, HCA, HONA, ILMN, LIN) had no
expiration inside the collector's seven-day window. Schwab answers such a window
with 200, ``status: "SUCCESS"``, both expiration maps empty and
``underlyingPrice: 0.0``. Nothing is written for it, and the proxy's store never
keeps an empty answer, so a watchlist-only symbol that should cost one real
fetch in three minutes cost one EVERY minute: measured, AON was requested 18
times in 15 minutes against ANET's 17, and two requests by hand a second apart
were both misses.
"""
import datetime as dt
import logging
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import gex_collector as gc

CT = ZoneInfo("America/Chicago")
T0 = dt.datetime(2026, 10, 6, 10, 0, tzinfo=CT)
TIERS = {"tail": frozenset(), "interval_min": 1, "fresh_max_age_sec": 20}


def nothing_listed(symbol):
    """Schwab's answer for a window holding no expiration, as measured."""
    return {"symbol": symbol, "status": "SUCCESS", "underlying": None,
            "strategy": "SINGLE", "underlyingPrice": 0.0, "numberOfContracts": 0,
            "callExpDateMap": {}, "putExpDateMap": {}}


def listed(symbol):
    contract = {"symbol": f"{symbol} 261009C00100000", "putCall": "CALL",
                "strikePrice": 100.0, "gamma": 0.05, "openInterest": 10,
                "totalVolume": 1, "mark": 1.0, "delta": 0.5, "volatility": 20.0}
    return {"symbol": symbol, "status": "SUCCESS", "underlyingPrice": 100.0,
            "numberOfContracts": 2,
            "callExpDateMap": {"2026-10-09:3": {"100.0": [contract]}},
            "putExpDateMap": {"2026-10-09:3": {"100.0": [dict(contract, putCall="PUT")]}}}


class Client:
    """Counts chain requests per symbol and answers as told."""

    def __init__(self, answers):
        self.answers = answers            # symbol -> callable(symbol) -> chain | None
        self.asked = []
        self.quoted = []
        self.Options = MagicMock()
        self.Options.ContractType.ALL = "ALL"

    def get_option_chain(self, symbol, **kw):
        self.asked.append(symbol)
        chain = self.answers[symbol](symbol)
        resp = MagicMock()
        resp.status_code = 200 if chain is not None else 502
        resp.json.return_value = chain
        resp.store_age = None
        return resp

    def get_quotes(self, symbols, **kw):
        self.quoted.append(list(symbols))
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {s: {"quote": {"lastPrice": 100.0}} for s in symbols}
        return resp

    def count(self, symbol):
        return self.asked.count(symbol)


def _engine():
    engine = MagicMock()
    engine._last_dte = 0
    engine.calc_all_from_chain.return_value = (None, None, None, None)
    return engine


def poll(client, symbols, minute=0, tiers=TIERS, on_chain=None, engine=None):
    gc.poll_once(client, engine or _engine(), MagicMock(), symbols=list(symbols),
                 on_chain=on_chain, poll_term=False,
                 now=T0 + dt.timedelta(minutes=minute), tiers=tiers)


@pytest.fixture(autouse=True)
def _fresh_memory():
    gc.reset_nothing_listed()
    yield
    gc.reset_nothing_listed()


# ---- the prod case -----------------------------------------------------------

def test_a_symbol_with_nothing_listed_is_asked_once_then_left_alone():
    client = Client({"AON": nothing_listed, "KO": listed})
    for minute in range(15):
        poll(client, ["AON", "KO"], minute)
    assert client.count("AON") == 1                 # was 15
    assert client.count("KO") == 15                 # an ordinary symbol is untouched


def test_it_is_asked_again_when_the_wait_is_over():
    client = Client({"AON": nothing_listed})
    poll(client, ["AON"], 0)
    for minute in (1, 30, 59):
        poll(client, ["AON"], minute)
    assert client.count("AON") == 1
    poll(client, ["AON"], 60)                       # the built-in wait is an hour
    assert client.count("AON") == 2
    poll(client, ["AON"], 61)
    assert client.count("AON") == 2                 # still nothing: it waits again


def test_a_symbol_that_starts_listing_is_collected_every_minute_again():
    answer = {"fn": nothing_listed}
    client = Client({"AON": lambda s: answer["fn"](s)})
    poll(client, ["AON"], 0)
    answer["fn"] = listed                           # the week before its monthly expiry
    poll(client, ["AON"], 60)
    for minute in (61, 62, 63):
        poll(client, ["AON"], minute)
    assert client.count("AON") == 5
    assert "AON" not in gc.resting_symbols()


def test_any_symbol_can_rest_not_only_a_watchlist_one():
    tiers = dict(TIERS, tail=frozenset({"KO"}), interval_min=3)
    client = Client({"$VIX": nothing_listed, "KO": listed})
    for minute in range(6):
        poll(client, ["$VIX", "KO"], minute, tiers=tiers)
    assert client.count("$VIX") == 1


# ---- what must NOT put a symbol to rest ---------------------------------------

def test_a_failed_fetch_is_asked_again_the_next_minute():
    client = Client({"AON": lambda s: None})
    for minute in range(5):
        poll(client, ["AON"], minute)
    assert client.count("AON") == 5


def test_an_answer_schwab_marks_failed_is_asked_again_the_next_minute():
    failed = lambda s: dict(nothing_listed(s), status="FAILED")   # noqa: E731
    client = Client({"AON": failed})
    for minute in range(5):
        poll(client, ["AON"], minute)
    assert client.count("AON") == 5


def test_an_empty_answer_without_schwabs_success_mark_is_asked_again():
    # Only Schwab's own "success, nothing listed" answer counts. Anything less
    # certain is asked again: resting a symbol that does list would blank it.
    bare = lambda s: {"symbol": s, "callExpDateMap": {}, "putExpDateMap": {}}   # noqa: E731
    client = Client({"AON": bare})
    for minute in range(5):
        poll(client, ["AON"], minute)
    assert client.count("AON") == 5


@pytest.mark.parametrize("side", ["callExpDateMap", "putExpDateMap"])
def test_a_chain_listing_on_one_side_only_is_a_real_chain(side):
    def one_side(s):
        chain = nothing_listed(s)
        chain[side] = listed(s)[side]
        return chain
    client = Client({"AON": one_side})
    for minute in range(4):
        poll(client, ["AON"], minute)
    assert client.count("AON") == 4


def test_a_failed_retry_does_not_start_a_new_wait():
    answer = {"fn": nothing_listed}
    client = Client({"AON": lambda s: answer["fn"](s)})
    poll(client, ["AON"], 0)
    answer["fn"] = lambda s: None                   # Schwab did not answer the retry
    poll(client, ["AON"], 60)
    poll(client, ["AON"], 61)
    assert client.count("AON") == 3                 # asked again at once, not in an hour


# ---- the setting ------------------------------------------------------------------

def test_the_wait_comes_from_the_settings():
    tiers = dict(TIERS, empty_retry_min=10)
    client = Client({"AON": nothing_listed})
    for minute in range(25):
        poll(client, ["AON"], minute, tiers=tiers)
    assert client.count("AON") == 3                 # minutes 0, 10, 20


def test_a_wait_of_zero_asks_every_minute_as_before():
    tiers = dict(TIERS, empty_retry_min=0)
    client = Client({"AON": nothing_listed})
    for minute in range(6):
        poll(client, ["AON"], minute, tiers=tiers)
    assert client.count("AON") == 6
    assert gc.resting_symbols() == set()


def test_turning_the_wait_off_wakes_a_resting_symbol_at_once():
    client = Client({"AON": nothing_listed})
    poll(client, ["AON"], 0)
    poll(client, ["AON"], 1, tiers=dict(TIERS, empty_retry_min=0))
    assert client.count("AON") == 2


@pytest.mark.parametrize("bad", [None, -5, True, "60", 1.5, float("nan")])
def test_an_unusable_wait_means_the_built_in_hour(bad):
    tiers = dict(TIERS, empty_retry_min=bad)
    client = Client({"AON": nothing_listed})
    for minute in (0, 59, 60):
        poll(client, ["AON"], minute, tiers=tiers)
    assert client.count("AON") == 2


def test_a_wait_above_the_ceiling_is_the_ceiling():
    tiers = dict(TIERS, empty_retry_min=100000)
    client = Client({"AON": nothing_listed})
    poll(client, ["AON"], 0, tiers=tiers)
    poll(client, ["AON"], gc.EMPTY_RETRY_MAX_MIN - 1, tiers=tiers)
    assert client.count("AON") == 1
    poll(client, ["AON"], gc.EMPTY_RETRY_MAX_MIN, tiers=tiers)
    assert client.count("AON") == 2


def test_without_tiers_the_built_in_wait_still_applies():
    # The rest does not depend on the proxy's store or on the tiers at all.
    client = Client({"AON": nothing_listed})
    for minute in range(10):
        poll(client, ["AON"], minute, tiers=None)
    assert client.count("AON") == 1


def test_tiers_that_cannot_be_read_still_poll_and_still_rest():
    client = Client({"AON": nothing_listed, "KO": listed})
    for minute in range(3):
        poll(client, ["AON", "KO"], minute, tiers="not a mapping")
    assert client.count("KO") == 3 and client.count("AON") == 1


# ---- a resting symbol costs nothing else either -----------------------------------

def test_a_resting_symbol_is_not_computed_handed_on_or_quoted():
    seen = []
    engine = _engine()
    client = Client({"AON": nothing_listed, "KO": listed})
    poll(client, ["AON", "KO"], 0, on_chain=lambda s, c: seen.append(s), engine=engine)
    engine.calc_all_from_chain.reset_mock()
    seen.clear()
    poll(client, ["AON", "KO"], 1, on_chain=lambda s, c: seen.append(s), engine=engine)
    assert seen == ["KO"]
    assert engine.calc_all_from_chain.call_count == 1
    assert all("AON" not in batch for batch in client.quoted)


def test_when_every_symbol_rests_the_poll_asks_nothing_and_survives():
    client = Client({"AON": nothing_listed, "AZO": nothing_listed})
    poll(client, ["AON", "AZO"], 0)
    poll(client, ["AON", "AZO"], 1)
    assert client.asked == ["AON", "AZO"]


def test_it_is_said_once_at_info_when_a_symbol_goes_to_rest(caplog):
    client = Client({"AON": nothing_listed})
    with caplog.at_level(logging.DEBUG, logger="gex_collector"):
        for minute in (0, 1, 2, 60, 61):
            poll(client, ["AON"], minute)
    said = [r for r in caplog.records
            if r.levelno >= logging.INFO and "AON" in r.getMessage()]
    assert len(said) == 1 and "60 minutes" in said[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_symbol_no_longer_polled_is_forgotten_once_its_wait_is_over():
    client = Client({"AON": nothing_listed, "KO": listed})
    poll(client, ["AON", "KO"], 0)
    poll(client, ["KO"], 5)
    assert "AON" in gc.resting_symbols()            # its wait is not over: kept
    poll(client, ["KO"], 61)
    assert "AON" not in gc.resting_symbols()
