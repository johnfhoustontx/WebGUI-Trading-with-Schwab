"""Watchlist-only symbols get a real chain fetch every Nth minute and are
carried forward in between."""
import datetime as dt
import logging
import zlib
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import chain_carry
import gex_collector as gc
import gex_history_db as db

CT = ZoneInfo("America/Chicago")
RTH = dt.datetime(2026, 10, 5, 10, 0, tzinfo=CT)
TIERS = {"tail": frozenset({"AAPL", "SOFI", "UBER"}), "interval_min": 3,
         "fresh_max_age_sec": 20}


def _chain(symbol):
    return {"symbol": symbol, "underlyingPrice": 100.0,
            "callExpDateMap": {}, "putExpDateMap": {}}


def _client(ages=None, quotes=None, quotes_raise=False, chain=_chain,
            quotes_status=200):
    client = MagicMock()
    client.Options.ContractType.ALL = "ALL"
    client.asked = {}
    client.kwargs = {}

    def get_chain(symbol, **kw):
        client.asked[symbol] = kw.get("max_age", "none")
        client.kwargs[symbol] = kw
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = chain(symbol)
        resp.store_age = (ages or {}).get(symbol)
        return resp

    def get_quotes(symbols, **kw):
        if quotes_raise:
            raise RuntimeError("proxy down")
        resp = MagicMock()
        resp.status_code = quotes_status
        resp.json.return_value = {s: {"quote": {"lastPrice": (quotes or {}).get(s, 0)}}
                                  for s in symbols}
        return resp

    client.get_option_chain.side_effect = get_chain
    client.get_quotes.side_effect = get_quotes
    return client


def _engine():
    engine = MagicMock()
    engine._last_dte = 0
    engine.calc_all_from_chain.return_value = (None, None, None, None)
    return engine


def _poll(client, symbols, tiers=TIERS, now=RTH, on_chain=None, engine=None):
    gc.poll_once(client, engine or _engine(), MagicMock(), symbols=symbols,
                 on_chain=on_chain, poll_term=False, now=now, tiers=tiers)


def _not_due(symbol, interval=3, start=RTH):
    """The first instant at or after ``start`` on which ``symbol`` is NOT due."""
    for offset in range(interval):
        now = start + dt.timedelta(minutes=offset)
        if not gc.tail_due(symbol, int(now.timestamp()) // 60, interval):
            return now
    raise AssertionError("always due")


def _due(symbol, interval=3, start=RTH):
    for offset in range(interval):
        now = start + dt.timedelta(minutes=offset)
        if gc.tail_due(symbol, int(now.timestamp()) // 60, interval):
            return now
    raise AssertionError("never due")


#############################################
# THE PLAN'S SPECIFICATION
#############################################

def test_due_is_one_minute_in_n_and_staggered_by_symbol():
    for sym in ("AAPL", "SOFI", "UBER", "HOOD", "INTC", "PLD"):
        due = [m for m in range(30) if gc.tail_due(sym, m, 3)]
        assert len(due) == 10 and all(b - a == 3 for a, b in zip(due, due[1:]))
    phases = {min(m for m in range(3) if gc.tail_due(s, m, 3))
              for s in ("AAPL", "SOFI", "UBER", "HOOD", "INTC", "PLD", "META", "T")}
    assert len(phases) > 1                       # not all on the same minute


def test_an_interval_of_one_is_always_due():
    assert all(gc.tail_due("AAPL", m, 1) for m in range(5))


def test_without_tiers_no_age_limit_is_sent():
    c = _client()
    _poll(c, ["SPY", "AAPL"], tiers=None)
    assert c.asked == {"SPY": "none", "AAPL": "none"}


def test_core_symbols_always_ask_for_a_fresh_chain():
    c = _client()
    _poll(c, ["SPY", "AAPL"])
    assert c.asked["SPY"] == 20


def test_a_tail_symbol_asks_fresh_when_due_and_stored_otherwise():
    minute = int(RTH.timestamp()) // 60
    seen = set()
    for offset in range(3):
        now = RTH + dt.timedelta(minutes=offset)
        c = _client()
        _poll(c, ["AAPL"], now=now)
        want = 20 if gc.tail_due("AAPL", minute + offset, 3) else 210
        assert c.asked["AAPL"] == want
        seen.add(want)
    assert seen == {20, 210}


def test_a_carried_chain_is_priced_at_the_live_quote():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0})
    _poll(c, ["SPY", "AAPL"], engine=engine)
    spots = {call.args[0]["symbol"]: call.args[0]["underlyingPrice"]
             for call in engine.calc_all_from_chain.call_args_list}
    assert spots == {"SPY": 100.0, "AAPL": 103.0}
    assert c.get_quotes.call_args.args[0] == ["AAPL"]      # one batch, carried only


def test_no_quote_call_when_nothing_was_carried():
    c = _client(ages={"AAPL": 3.0})
    _poll(c, ["SPY", "AAPL"])
    c.get_quotes.assert_not_called()


def test_a_carried_chain_is_not_handed_to_the_detectors():
    seen = []
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0})
    _poll(c, ["SPY", "AAPL"], on_chain=lambda s, ch: seen.append(s))
    assert seen == ["SPY"]


def test_a_carried_chain_is_still_written():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0})
    _poll(c, ["AAPL"], engine=engine)
    assert engine.calc_all_from_chain.call_count == 1


def test_a_failed_quote_call_keeps_the_stored_price_and_the_poll_alive():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes_raise=True)
    _poll(c, ["SPY", "AAPL"], engine=engine)
    spots = [call.args[0]["underlyingPrice"]
             for call in engine.calc_all_from_chain.call_args_list]
    assert spots == [100.0, 100.0]


def test_a_carried_symbol_with_no_live_price_keeps_the_stored_price():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 0})     # the no-print sentinel
    _poll(c, ["AAPL"], engine=engine)
    assert engine.calc_all_from_chain.call_args.args[0]["underlyingPrice"] == 100.0


def test_an_answer_with_no_readable_age_is_treated_as_fresh():
    # A MagicMock attribute, an old proxy, a garbled header: none may read as
    # "carried", or the detectors would be skipped for a chain that is new.
    seen = []
    c = _client()                                            # store_age is None
    _poll(c, ["AAPL"], on_chain=lambda s, ch: seen.append(s))
    assert seen == ["AAPL"]
    c.get_quotes.assert_not_called()


#############################################
# tail_due / _chain_max_age / _answer_age — pure
#############################################

def test_a_symbols_minute_is_stable_across_processes():
    """``hash()`` is salted per process; a restart would move every symbol's
    minute. The phase is pinned to crc32, which is not."""
    for sym in ("AAPL", "SOFI", "$SPX", "BRK/B"):
        phase = zlib.crc32(sym.encode()) % 3
        assert [m for m in range(6) if gc.tail_due(sym, m, 3)] == \
            [m for m in range(6) if (m + phase) % 3 == 0]


@pytest.mark.parametrize("interval", [1, 0, -3])
def test_an_interval_of_one_or_less_is_every_minute(interval):
    assert all(gc.tail_due("AAPL", m, interval) for m in range(7))


def test_about_a_third_of_a_real_tail_is_due_each_minute():
    names = [f"SYM{i}" for i in range(64)]
    for minute in range(3):
        due = sum(gc.tail_due(s, minute, 3) for s in names)
        assert 12 <= due <= 30


def test_the_age_limit_for_each_kind_of_symbol():
    due = int(_due("AAPL").timestamp()) // 60
    not_due = int(_not_due("AAPL").timestamp()) // 60
    assert gc._chain_max_age("AAPL", not_due, None) is None
    assert gc._chain_max_age("AAPL", not_due, {}) is None
    assert gc._chain_max_age("SPY", not_due, TIERS) == 20          # core
    assert gc._chain_max_age("AAPL", due, TIERS) == 20             # tail, due
    assert gc._chain_max_age("AAPL", not_due, TIERS) == 3 * 60 + gc.CARRY_SLACK_SEC
    five = dict(TIERS, interval_min=5)
    waiting = next(m for m in range(5) if not gc.tail_due("AAPL", m, 5))
    assert gc._chain_max_age("AAPL", waiting, five) == 5 * 60 + gc.CARRY_SLACK_SEC


class _Resp:
    def __init__(self, age):
        self.store_age = age


@pytest.mark.parametrize("age, want", [
    (95, 95.0), (95.5, 95.5), (0, 0.0), (0.0, 0.0),
    (None, None), (True, None), (False, None), ("95", None),
    (float("nan"), None), (float("inf"), None), (float("-inf"), None),
    (-1, None), (-0.5, None), ([95], None)])
def test_only_a_real_finite_age_at_or_above_zero_is_an_age(age, want):
    got = gc._answer_age(_Resp(age))
    assert got == want and (want is None or type(got) is float)


def test_a_test_doubles_attribute_and_a_missing_attribute_are_no_age():
    assert gc._answer_age(MagicMock()) is None
    assert gc._answer_age(object()) is None
    assert gc._answer_age(None) is None


#############################################
# tiers=None IS THE COLLECTOR AS IT WAS
#############################################

def test_tiers_none_sends_exactly_the_request_made_before_tiers_existed():
    today = RTH.date()
    legacy = {"contract_type": "ALL", "from_date": today,
              "to_date": today + dt.timedelta(days=7)}
    plain, explicit, tiered = _client(), _client(), _client()
    gc.poll_once(plain, _engine(), MagicMock(), symbols=["SPY", "AAPL"],
                 poll_term=False, now=RTH)                         # argument omitted
    _poll(explicit, ["SPY", "AAPL"], tiers=None)
    _poll(tiered, ["SPY", "AAPL"])
    assert plain.kwargs == {"SPY": legacy, "AAPL": legacy}
    assert explicit.kwargs == plain.kwargs
    # ... and with tiers the ONLY difference is the age limit.
    for sym in ("SPY", "AAPL"):
        extra = dict(tiered.kwargs[sym])
        assert isinstance(extra.pop("max_age"), int)
        assert extra == legacy


def test_tiers_none_ignores_the_answers_age_entirely(caplog):
    """A proxy that reports an old answer must change nothing while tiers are
    off: every chain reaches the detectors, none is re-priced, nothing is said."""
    seen = []
    engine = _engine()
    c = _client(ages={"SPY": 400.0, "AAPL": 95.0}, quotes={"AAPL": 103.0, "SPY": 9.0})
    with caplog.at_level(logging.INFO, logger="gex_collector"):
        _poll(c, ["SPY", "AAPL"], tiers=None, engine=engine,
              on_chain=lambda s, ch: seen.append(s))
    assert seen == ["SPY", "AAPL"]
    c.get_quotes.assert_not_called()
    assert [call.args[0]["underlyingPrice"]
            for call in engine.calc_all_from_chain.call_args_list] == [100.0, 100.0]
    assert not [r for r in caplog.records if "arried" in r.getMessage()]


@pytest.mark.parametrize("bad", [
    {"tail": frozenset({"AAPL"})},                                   # keys missing
    {"tail": frozenset({"AAPL"}), "interval_min": "x", "fresh_max_age_sec": 20},
    {"tail": frozenset({"AAPL"}), "interval_min": 3, "fresh_max_age_sec": None},
    {"tail": frozenset({"AAPL"}), "interval_min": 3, "fresh_max_age_sec": float("nan")},
    {"tail": frozenset({"AAPL"}), "interval_min": 3, "fresh_max_age_sec": True},
    {"tail": frozenset({"AAPL"}), "interval_min": 3, "fresh_max_age_sec": -5},
    {"tail": 7, "interval_min": 3, "fresh_max_age_sec": 20},
    {"tail": frozenset(), "interval_min": 3, "fresh_max_age_sec": 20},
    {"tail": frozenset({"AAPL"}), "interval_min": 1, "fresh_max_age_sec": 20},
    "tiers", 7])
def test_tiers_that_cannot_be_read_are_no_tiers_and_never_a_dead_poll(bad):
    seen = []
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0})
    _poll(c, ["SPY", "AAPL"], tiers=bad, engine=engine,
          on_chain=lambda s, ch: seen.append(s))
    assert c.asked == {"SPY": "none", "AAPL": "none"}
    assert seen == ["SPY", "AAPL"]
    assert engine.calc_all_from_chain.call_count == 2
    c.get_quotes.assert_not_called()


#############################################
# CARRIED IS DECIDED BY THE ANSWER'S AGE, NOT BY WHO WAS DUE
#############################################

def test_a_due_symbol_answered_from_a_five_second_old_chain_is_fresh():
    seen = []
    c = _client(ages={"AAPL": 5.0}, quotes={"AAPL": 103.0})
    engine = _engine()
    _poll(c, ["AAPL"], now=_due("AAPL"), engine=engine,
          on_chain=lambda s, ch: seen.append(s))
    assert c.asked["AAPL"] == 20                    # it was due
    assert seen == ["AAPL"]
    c.get_quotes.assert_not_called()
    assert engine.calc_all_from_chain.call_args.args[0]["underlyingPrice"] == 100.0


@pytest.mark.parametrize("age", [None, 0.0, 3.0, 20.0])
def test_a_symbol_that_was_not_due_but_got_a_fresh_fetch_is_fresh(age):
    """The store was empty, or off: the proxy went to Schwab anyway."""
    seen = []
    c = _client(ages={"AAPL": age}, quotes={"AAPL": 103.0})
    _poll(c, ["AAPL"], now=_not_due("AAPL"), on_chain=lambda s, ch: seen.append(s))
    assert c.asked["AAPL"] == 210                   # it was not due
    assert seen == ["AAPL"]
    c.get_quotes.assert_not_called()


def test_an_answer_just_past_the_fresh_limit_is_carried():
    seen = []
    c = _client(ages={"AAPL": 20.5}, quotes={"AAPL": 103.0})
    _poll(c, ["AAPL"], on_chain=lambda s, ch: seen.append(s))
    assert seen == []
    assert c.get_quotes.call_count == 1


def test_a_core_symbol_answered_with_an_old_chain_is_carried_too():
    """Whatever the reason the proxy handed back an old chain, it has no new
    volume for the detectors and its price is behind."""
    seen = []
    engine = _engine()
    c = _client(ages={"SPY": 95.0}, quotes={"SPY": 512.0})
    _poll(c, ["SPY"], engine=engine, on_chain=lambda s, ch: seen.append(s))
    assert seen == []
    assert engine.calc_all_from_chain.call_args.args[0]["underlyingPrice"] == 512.0


def test_a_failed_chain_fetch_is_never_carried():
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0},
                chain=lambda s: None)
    engine = _engine()
    _poll(c, ["AAPL"], engine=engine)
    c.get_quotes.assert_not_called()
    engine.calc_all_from_chain.assert_not_called()


#############################################
# THE CARRY ITSELF
#############################################

def _real_chain(symbol):
    def side(pc, sign):
        return {"2026-10-09:4": {
            str(k): [{"putCall": pc, "strikePrice": k, "gamma": g,
                      "delta": sign * d, "volatility": 30.0, "openInterest": 500,
                      "totalVolume": 40, "mark": 1.25}]
            for k, g, d in ((95.0, 0.03, 0.7), (100.0, 0.06, 0.5), (105.0, 0.03, 0.3))}}
    return {"symbol": symbol, "underlyingPrice": 100.0,
            "callExpDateMap": side("CALL", 1), "putExpDateMap": side("PUT", -1)}


def test_the_engine_gets_the_chain_carried_by_its_age_at_this_polls_clock():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0}, chain=_real_chain)
    _poll(c, ["AAPL"], engine=engine)
    got = engine.calc_all_from_chain.call_args.args[0]
    assert got == chain_carry.carry_chain(_real_chain("AAPL"), 103.0,
                                          age_sec=95.0, now=RTH)
    assert got["callExpDateMap"]["2026-10-09:4"]["100.0"][0]["gamma"] != 0.06
    # ... and neither a different age nor a different clock gives the same chain.
    assert got != chain_carry.carry_chain(_real_chain("AAPL"), 103.0,
                                          age_sec=0, now=RTH)
    assert got != chain_carry.carry_chain(_real_chain("AAPL"), 103.0, age_sec=95.0,
                                          now=RTH + dt.timedelta(days=1))


def test_the_quote_call_is_one_batch_of_the_carried_symbols_only():
    c = _client(ages={"AAPL": 95.0, "SOFI": 150.0, "UBER": 4.0},
                quotes={"AAPL": 103.0, "SOFI": 9.0, "UBER": 70.0})
    _poll(c, ["SPY", "UBER", "SOFI", "AAPL"])
    assert c.get_quotes.call_count == 1
    assert c.get_quotes.call_args.args[0] == ["AAPL", "SOFI"]


def test_a_symbol_missing_from_the_quote_answer_keeps_the_stored_price():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0, "SOFI": 95.0}, quotes={"SOFI": 9.0})
    c.get_quotes.side_effect = lambda symbols, **kw: MagicMock(
        status_code=200, json=lambda: {"SOFI": {"quote": {"lastPrice": 9.0}}})
    _poll(c, ["AAPL", "SOFI"], engine=engine)
    spots = {call.args[0]["symbol"]: call.args[0]["underlyingPrice"]
             for call in engine.calc_all_from_chain.call_args_list}
    assert spots == {"AAPL": 100.0, "SOFI": 9.0}


def test_a_refused_quote_call_keeps_the_stored_prices():
    engine = _engine()
    seen = []
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0}, quotes_status=503)
    _poll(c, ["AAPL"], engine=engine, on_chain=lambda s, ch: seen.append(s))
    assert engine.calc_all_from_chain.call_args.args[0]["underlyingPrice"] == 100.0
    assert seen == []                               # still an old chain: no detectors


def test_a_quote_call_that_raises_still_keeps_the_old_chain_from_the_detectors():
    """No live price does not make the chain new: it still has no new volume."""
    seen = []
    c = _client(ages={"AAPL": 95.0}, quotes_raise=True)
    _poll(c, ["SPY", "AAPL"], on_chain=lambda s, ch: seen.append(s))
    assert seen == ["SPY"]


def test_a_quote_for_a_symbol_that_was_not_carried_is_ignored():
    """The answer may name more symbols than were asked for. A fresh chain's
    own price is the price it was fetched at."""
    engine = _engine()
    c = _client(ages={"AAPL": 95.0})
    c.get_quotes.side_effect = lambda symbols, **kw: MagicMock(
        status_code=200, json=lambda: {"AAPL": {"quote": {"lastPrice": 103.0}},
                                       "SPY": {"quote": {"lastPrice": 512.0}}})
    _poll(c, ["SPY", "AAPL"], engine=engine)
    spots = {call.args[0]["symbol"]: call.args[0]["underlyingPrice"]
             for call in engine.calc_all_from_chain.call_args_list}
    assert spots == {"SPY": 100.0, "AAPL": 103.0}


@pytest.mark.parametrize("quote", [0, -3.0, None, "103", True, float("nan"),
                                   float("inf")])
def test_a_chain_is_never_priced_at_an_unusable_quote(quote):
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": quote})
    _poll(c, ["AAPL"], engine=engine)
    assert engine.calc_all_from_chain.call_args.args[0]["underlyingPrice"] == 100.0


def test_a_carry_that_raises_for_one_symbol_leaves_the_others_carried(monkeypatch):
    real = chain_carry.carry_chain

    def flaky(chain, spot, **kw):
        if chain["symbol"] == "AAPL":
            raise RuntimeError("boom")
        return real(chain, spot, **kw)

    monkeypatch.setattr(chain_carry, "carry_chain", flaky)
    engine = _engine()
    c = _client(ages={"AAPL": 95.0, "SOFI": 95.0}, quotes={"AAPL": 103.0, "SOFI": 9.0})
    _poll(c, ["AAPL", "SOFI"], engine=engine)
    spots = {call.args[0]["symbol"]: call.args[0]["underlyingPrice"]
             for call in engine.calc_all_from_chain.call_args_list}
    assert spots == {"AAPL": 100.0, "SOFI": 9.0}


def test_the_stored_chain_object_is_not_mutated_by_the_carry():
    """The proxy client hands back parsed JSON; the same object may be what a
    test double (or a future in-process store) returns next minute."""
    stored = _real_chain("AAPL")
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0}, chain=lambda s: stored)
    _poll(c, ["AAPL"])
    assert stored == _real_chain("AAPL")


#############################################
# WHAT THE OPERATOR SEES
#############################################

def test_one_info_line_per_poll_counts_the_carried_chains(caplog):
    c = _client(ages={"AAPL": 95.0, "SOFI": 95.0, "UBER": 2.0},
                quotes={"AAPL": 103.0},                    # SOFI has no live print
                chain=lambda s: None if s == "HOOD" else _chain(s))
    with caplog.at_level(logging.INFO, logger="gex_collector"):
        _poll(c, ["SPY", "AAPL", "SOFI", "UBER", "HOOD"])
    info = [r.getMessage() for r in caplog.records
            if r.name == "gex_collector" and r.levelno == logging.INFO]
    # HOOD's fetch failed: four chains, two of them old, one re-priced.
    assert info == ["Carried 2 of 4 chain(s) forward (1 on a live quote)"]


def test_a_chain_that_could_not_be_moved_is_not_counted_as_repriced(caplog):
    """A stored chain with no price of its own cannot be modelled forward. It
    is written as it is, and the line must not claim otherwise."""
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0},
                chain=lambda s: dict(_chain(s), underlyingPrice=0))
    with caplog.at_level(logging.INFO, logger="gex_collector"):
        _poll(c, ["AAPL"], engine=engine)
    assert engine.calc_all_from_chain.call_args.args[0]["underlyingPrice"] == 0
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.INFO] == \
        ["Carried 1 of 1 chain(s) forward (0 on a live quote)"]


def test_the_line_is_written_with_tiers_even_when_nothing_was_carried(caplog):
    """A run of zeros is how the operator sees the store is not answering."""
    with caplog.at_level(logging.INFO, logger="gex_collector"):
        _poll(_client(), ["SPY", "AAPL"])
    info = [r.getMessage() for r in caplog.records
            if r.name == "gex_collector" and r.levelno == logging.INFO]
    assert info == ["Carried 0 of 2 chain(s) forward (0 on a live quote)"]


#############################################
# THE DATABASE: ALL FIVE VIEWS, AT THE LIVE PRICE
#############################################

def test_a_carried_chain_writes_all_five_views_at_the_live_price(tmp_path, monkeypatch):
    import gamma_tool as gt
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0}, chain=_real_chain)
    gc.poll_once(c, gt.GammaEngine(), conn, symbols=["SPY", "AAPL"],
                 poll_term=False, now=RTH, tiers=TIERS)
    rows = conn.execute(
        "SELECT symbol, view, spot, ts, call_vol FROM snapshots").fetchall()
    by_symbol = {}
    for symbol, view, spot, ts, call_vol in rows:
        by_symbol.setdefault(symbol, {})[view] = (spot, ts, call_vol)
    views = {"gex", "charm", "dex", "vanna", "prem"}
    assert set(by_symbol["AAPL"]) == views and set(by_symbol["SPY"]) == views
    assert {spot for spot, _, _ in by_symbol["AAPL"].values()} == {103.0}
    assert {spot for spot, _, _ in by_symbol["SPY"].values()} == {100.0}
    # Same minute, and the volume repeats what was fetched: nothing is invented.
    assert {ts for v in by_symbol.values() for _, ts, _ in v.values()} == \
        {int(RTH.timestamp())}
    assert by_symbol["AAPL"]["gex"][2] == by_symbol["SPY"]["gex"][2]
    conn.close()


#############################################
# CARRIED MINUTES REPEAT THE FETCHED SKEW READINGS
#############################################
# atm_iv and rr_25d pick a strike by price and by delta. Read off a carried
# chain they would mix a live price and carried deltas with volatilities from
# the fetch, and hop between strikes on carried minutes. They, the volume and
# premium totals and the per-strike premium grid are read from the chain AS
# STORED; only the four Greek views come from the carried chain.

SKEW_COLUMNS = ("rr_25d", "call_vol", "put_vol", "call_prem", "put_prem", "atm_iv")
LIVE = 104.5


def _skew_chain(symbol):
    """Volatility falls with the strike, so the at-the-money strike and the
    25-delta picks each read a different number at 100 than at 104.5."""
    rows = ((95.0, 40.0, 0.02, 0.85, -0.15),
            (100.0, 30.0, 0.06, 0.52, -0.48),
            (105.0, 20.0, 0.03, 0.22, -0.78))

    def side(pc, col, volume, oi):
        return {"2026-10-09:4": {
            str(k): [{"putCall": pc, "strikePrice": k, "gamma": g, "delta": d[col],
                      "volatility": iv, "openInterest": oi,
                      "totalVolume": volume, "mark": 1.25}]
            for k, iv, g, *d in rows}}
    return {"symbol": symbol, "underlyingPrice": 100.0,
            "callExpDateMap": side("CALL", 0, 40, 500),
            "putExpDateMap": side("PUT", 1, 25, 300)}


def _skew_of(chain):
    rr = gc.flow_skew.risk_reversal_25d(chain) or {}
    vol = gc.flow_skew.index_call_put_volume(chain) or {}
    prem = gc.flow_skew.index_call_put_premium(chain) or {}
    return {"rr_25d": rr.get("rr"), "call_vol": vol.get("call_vol"),
            "put_vol": vol.get("put_vol"), "call_prem": prem.get("call_prem"),
            "put_prem": prem.get("put_prem"),
            "atm_iv": gc.iv_analysis.extract_atm_iv(chain)}


def _written(tmp_path, monkeypatch, *, tiers, symbols=("SPY", "AAPL"), **client_kw):
    """Run one poll into a real database with the real engine.
    Returns ``{symbol: {view: {"spot": ..., <skew columns>}}}``."""
    import gamma_tool as gt
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    c = _client(chain=_skew_chain, **client_kw)
    gc.poll_once(c, gt.GammaEngine(), conn, symbols=list(symbols),
                 poll_term=False, now=RTH, tiers=tiers)
    out = {}
    for symbol, view, spot, *skew in conn.execute(
            "SELECT symbol, view, spot, " + ", ".join(SKEW_COLUMNS)
            + " FROM snapshots").fetchall():
        out.setdefault(symbol, {})[view] = dict(zip(SKEW_COLUMNS, skew), spot=spot)
    conn.close()
    return out


def _skew_only(row):
    return {k: row[k] for k in SKEW_COLUMNS}


def test_the_fixture_really_reads_differently_once_carried():
    """Without this the tests below could pass on a chain where the stored and
    the carried readings happen to agree."""
    stored = _skew_of(_skew_chain("AAPL"))
    carried = _skew_of(chain_carry.carry_chain(_skew_chain("AAPL"), LIVE,
                                               age_sec=95.0, now=RTH))
    assert stored["atm_iv"] == 30.0 and carried["atm_iv"] == 20.0
    assert stored["rr_25d"] is not None and carried["rr_25d"] is not None
    assert stored["rr_25d"] != carried["rr_25d"]
    # The carry never touches volume or marks, so these agree either way.
    for col in ("call_vol", "put_vol", "call_prem", "put_prem"):
        assert stored[col] == carried[col] and stored[col]


def test_a_carried_minute_writes_the_skew_readings_of_the_stored_chain(
        tmp_path, monkeypatch):
    rows = _written(tmp_path, monkeypatch, tiers=TIERS,
                    ages={"AAPL": 95.0}, quotes={"AAPL": LIVE})
    stored = _skew_of(_skew_chain("AAPL"))
    assert set(rows["AAPL"]) == {"gex", "charm", "dex", "vanna", "prem"}
    for view, row in rows["AAPL"].items():
        assert _skew_only(row) == stored, view
        assert row["spot"] == LIVE, view           # the Greeks ARE at the live price


def test_a_carried_minutes_greek_views_still_come_from_the_carried_chain(
        tmp_path, monkeypatch):
    import gamma_tool as gt
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    c = _client(chain=_skew_chain, ages={"AAPL": 95.0}, quotes={"AAPL": LIVE})
    gc.poll_once(c, gt.GammaEngine(), conn, symbols=["AAPL"], poll_term=False,
                 now=RTH, tiers=TIERS)
    (net_total,) = conn.execute(
        "SELECT net_total FROM snapshots WHERE symbol='AAPL' AND view='gex'").fetchone()
    conn.close()

    def net(chain):
        gex, *_ = gt.GammaEngine().calc_all_from_chain(chain, use_volume=False)
        return gt.GammaEngine.snapshot_summary(gex)["net_total"]

    carried = chain_carry.carry_chain(_skew_chain("AAPL"), LIVE, age_sec=95.0, now=RTH)
    assert net_total == pytest.approx(net(carried), rel=1e-6)
    assert net_total != pytest.approx(net(_skew_chain("AAPL")), rel=1e-3)
    assert net_total != pytest.approx(
        net(dict(_skew_chain("AAPL"), underlyingPrice=LIVE)), rel=1e-3)


def test_a_fresh_symbol_in_the_same_poll_is_unaffected(tmp_path, monkeypatch):
    rows = _written(tmp_path, monkeypatch, tiers=TIERS,
                    ages={"AAPL": 95.0, "SPY": 2.0}, quotes={"AAPL": LIVE, "SPY": LIVE})
    fresh = _skew_of(_skew_chain("SPY"))
    for view, row in rows["SPY"].items():
        assert _skew_only(row) == fresh, view
        assert row["spot"] == 100.0, view


def test_without_tiers_every_reading_is_the_fetched_chains_own(tmp_path, monkeypatch):
    """Old answers and a live quote on offer: with tiers off none of it is used."""
    rows = _written(tmp_path, monkeypatch, tiers=None,
                    ages={"AAPL": 95.0, "SPY": 400.0}, quotes={"AAPL": LIVE, "SPY": LIVE})
    own = _skew_of(_skew_chain("AAPL"))
    for symbol in ("AAPL", "SPY"):
        assert set(rows[symbol]) == {"gex", "charm", "dex", "vanna", "prem"}
        for view, row in rows[symbol].items():
            assert _skew_only(row) == own, (symbol, view)
            assert row["spot"] == 100.0, (symbol, view)


def test_the_premium_grid_is_read_from_the_stored_chain_and_placed_at_the_live_spot(
        tmp_path, monkeypatch):
    seen = {}
    real = gc.flow_skew.premium_by_strike

    def spy(chain):
        seen[chain["symbol"]] = chain["underlyingPrice"]
        return real(chain)

    monkeypatch.setattr(gc.flow_skew, "premium_by_strike", spy)
    rows = _written(tmp_path, monkeypatch, tiers=TIERS,
                    ages={"AAPL": 95.0}, quotes={"AAPL": LIVE})
    assert seen == {"SPY": 100.0, "AAPL": 100.0}   # the chain as stored
    assert rows["AAPL"]["prem"]["spot"] == LIVE    # spot from the Greek pass
    assert rows["SPY"]["prem"]["spot"] == 100.0


@pytest.mark.parametrize("module, name", [
    ("flow_skew", "risk_reversal_25d"), ("flow_skew", "index_call_put_volume"),
    ("flow_skew", "index_call_put_premium"), ("flow_skew", "premium_by_strike"),
    ("iv_analysis", "extract_atm_iv")])
def test_every_skew_reader_is_handed_the_stored_chain(tmp_path, monkeypatch,
                                                      module, name):
    """Volume and premium read the same off either chain today, so only WHICH
    chain each reader is given can pin it: the stored one is at 100, the
    carried one at 104.5."""
    seen = {}
    mod = getattr(gc, module)
    real = getattr(mod, name)

    def spy(chain):
        seen[chain["symbol"]] = chain["underlyingPrice"]
        return real(chain)

    monkeypatch.setattr(mod, name, spy)
    _written(tmp_path, monkeypatch, tiers=TIERS,
             ages={"AAPL": 95.0}, quotes={"AAPL": LIVE})
    assert seen == {"SPY": 100.0, "AAPL": 100.0}


def test_a_carried_chain_that_got_no_live_price_writes_its_own_readings(
        tmp_path, monkeypatch):
    rows = _written(tmp_path, monkeypatch, tiers=TIERS, symbols=("AAPL",),
                    ages={"AAPL": 95.0}, quotes_raise=True)
    stored = _skew_of(_skew_chain("AAPL"))
    for view, row in rows["AAPL"].items():
        assert _skew_only(row) == stored and row["spot"] == 100.0, view
