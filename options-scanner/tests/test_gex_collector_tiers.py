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
    real = chain_carry.carry_counted

    def flaky(chain, spot, **kw):
        if chain["symbol"] == "AAPL":
            raise RuntimeError("boom")
        return real(chain, spot, **kw)

    monkeypatch.setattr(chain_carry, "carry_counted", flaky)
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


#############################################
# THE FRESH LIMIT HAS A CEILING
#############################################
# fresh_max_age_sec does two jobs: it is the line above which an answer counts
# as carried, AND the age limit sent for every one-minute symbol. Set to 40 or
# more, a core symbol's request is regularly answered with the PREVIOUS minute's
# chain and treated as new: its rows duplicate and the detectors see no new
# volume. So it can never exceed one poll interval less the slack.

FRESH_CEILING = gc.POLL_INTERVAL_MIN * 60 - gc.CARRY_SLACK_SEC


@pytest.fixture
def warned(monkeypatch, caplog):
    """A clean "already warned" memo, and the collector's WARNING lines."""
    monkeypatch.setattr(gc, "_WARNED", set())

    def lines():
        return [r.getMessage() for r in caplog.records
                if r.name == "gex_collector" and r.levelno == logging.WARNING]
    with caplog.at_level(logging.WARNING, logger="gex_collector"):
        yield lines


def test_the_ceiling_is_one_poll_interval_less_the_slack():
    assert FRESH_CEILING == 30                     # at the shipped 1 minute and 30 s


@pytest.mark.parametrize("configured", [31, 40, 45, 60, 600])
def test_a_fresh_limit_above_the_ceiling_is_clamped_in_both_of_its_roles(
        warned, configured):
    seen = []
    c = _client(ages={"SPY": FRESH_CEILING + 5.0}, quotes={"SPY": 512.0})
    _poll(c, ["SPY", "AAPL"], tiers=dict(TIERS, fresh_max_age_sec=configured),
          now=_due("AAPL"), on_chain=lambda s, ch: seen.append(s))
    # Role 1: the age limit sent for a one-minute symbol.
    assert c.asked == {"SPY": FRESH_CEILING, "AAPL": FRESH_CEILING}
    # Role 2: the line above which an answer is carried. 35 s is under every
    # configured value here, and over the ceiling.
    assert seen == ["AAPL"]
    assert c.get_quotes.call_args.args[0] == ["SPY"]


def test_a_clamped_fresh_limit_is_said_once_with_both_numbers(warned):
    tiers = dict(TIERS, fresh_max_age_sec=45)
    _poll(_client(), ["SPY", "AAPL"], tiers=tiers)
    _poll(_client(), ["SPY", "AAPL"], tiers=tiers)           # the next minute
    assert len(warned()) == 1                                # not once a minute
    assert "fresh_max_age_sec" in warned()[0]
    assert "45" in warned()[0] and str(FRESH_CEILING) in warned()[0]


def test_a_different_bad_value_is_said_again(warned):
    _poll(_client(), ["SPY", "AAPL"], tiers=dict(TIERS, fresh_max_age_sec=45))
    _poll(_client(), ["SPY", "AAPL"], tiers=dict(TIERS, fresh_max_age_sec=50))
    assert len(warned()) == 2


@pytest.mark.parametrize("configured", [0, 5, 20, FRESH_CEILING])
def test_a_fresh_limit_at_or_under_the_ceiling_is_used_as_it_is(warned, configured):
    c = _client()
    _poll(c, ["SPY"], tiers=dict(TIERS, fresh_max_age_sec=configured))
    assert c.asked == {"SPY": configured}
    assert warned() == []


def test_the_stored_chain_limit_is_untouched_by_the_clamp(warned):
    c = _client()
    _poll(c, ["AAPL"], tiers=dict(TIERS, fresh_max_age_sec=45), now=_not_due("AAPL"))
    assert c.asked == {"AAPL": 3 * 60 + gc.CARRY_SLACK_SEC}


#############################################
# ONE QUOTE CALL A POLL, IN EVERY SESSION STATE
#############################################
# Outside the regular session the re-anchor has already fetched a live quote for
# every symbol. The carry reuses it instead of asking again.

PRE_OPEN = dt.datetime(2026, 10, 5, 8, 15, tzinfo=CT)      # collecting, not yet open


def test_before_the_open_the_carry_reuses_the_re_anchors_quotes(caplog):
    engine = _engine()
    seen = []
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0, "SPY": 512.0},
                chain=_real_chain)
    with caplog.at_level(logging.INFO, logger="gex_collector"):
        _poll(c, ["SPY", "AAPL"], now=PRE_OPEN, engine=engine,
              on_chain=lambda s, ch: seen.append(s))
    assert c.get_quotes.call_count == 1
    assert c.get_quotes.call_args.args[0] == ["SPY", "AAPL"]     # the re-anchor's
    spots = {call.args[0]["symbol"]: call.args[0]["underlyingPrice"]
             for call in engine.calc_all_from_chain.call_args_list}
    assert spots == {"SPY": 512.0, "AAPL": 103.0}
    assert seen == ["SPY"]                                       # still carried
    # ... and carried through chain_carry, not merely re-priced: the gamma moved.
    got = engine.calc_all_from_chain.call_args_list[1].args[0]
    assert got["callExpDateMap"]["2026-10-09:4"]["100.0"][0]["gamma"] != 0.06
    assert "Carried 1 of 2 chain(s) forward (1 on a live quote)" in [
        r.getMessage() for r in caplog.records]


def test_before_the_open_a_failed_quote_call_is_not_tried_twice():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes_raise=True)
    _poll(c, ["SPY", "AAPL"], now=PRE_OPEN, engine=engine)
    assert c.get_quotes.call_count == 1
    assert [call.args[0]["underlyingPrice"]
            for call in engine.calc_all_from_chain.call_args_list] == [100.0, 100.0]


def test_before_the_open_a_symbol_the_re_anchor_got_no_quote_for_keeps_its_price():
    engine = _engine()
    c = _client(ages={"AAPL": 95.0}, quotes={"SPY": 512.0})      # AAPL: no print
    _poll(c, ["SPY", "AAPL"], now=PRE_OPEN, engine=engine)
    assert c.get_quotes.call_count == 1
    spots = {call.args[0]["symbol"]: call.args[0]["underlyingPrice"]
             for call in engine.calc_all_from_chain.call_args_list}
    assert spots == {"SPY": 512.0, "AAPL": 100.0}


def test_before_the_open_without_tiers_there_is_still_exactly_the_re_anchors_call():
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0, "SPY": 512.0})
    _poll(c, ["SPY", "AAPL"], now=PRE_OPEN, tiers=None)
    assert c.get_quotes.call_count == 1


def test_in_the_regular_session_the_one_call_is_the_carrys_own():
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0})
    _poll(c, ["SPY", "AAPL"], now=RTH)
    assert c.get_quotes.call_count == 1
    assert c.get_quotes.call_args.args[0] == ["AAPL"]


#############################################
# THE CARRY'S TWO LIMITS COME IN WITH THE TIERS
#############################################

def _carry_kwargs(monkeypatch, tiers):
    """The keyword arguments chain_carry.carry_counted was called with."""
    seen = []
    real = chain_carry.carry_counted

    def spy(chain, spot, **kw):
        seen.append(kw)
        return real(chain, spot, **kw)

    monkeypatch.setattr(chain_carry, "carry_counted", spy)
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0}, chain=_real_chain)
    _poll(c, ["AAPL"], tiers=tiers)
    assert len(seen) == 1
    return seen[0]


def test_the_gamma_cap_in_the_tiers_reaches_the_carry(monkeypatch):
    kw = _carry_kwargs(monkeypatch, dict(TIERS, max_gamma_ratio=3.5))
    assert kw == {"age_sec": 95.0, "now": RTH, "max_ratio": 3.5}


def test_tiers_without_a_gamma_cap_use_the_built_in_one(monkeypatch):
    kw = _carry_kwargs(monkeypatch, TIERS)
    assert kw["max_ratio"] == chain_carry.MAX_GAMMA_RATIO == 10.0


@pytest.mark.parametrize("unusable", [None, 0, 0.5, -1, float("nan"), float("inf"),
                                      "10", True])
def test_an_unusable_gamma_cap_is_the_built_in_one_not_a_dead_poll(monkeypatch, unusable):
    kw = _carry_kwargs(monkeypatch, dict(TIERS, max_gamma_ratio=unusable))
    assert kw["max_ratio"] == chain_carry.MAX_GAMMA_RATIO


@pytest.mark.parametrize("slack, stored_limit", [(0, 180), (10, 190), (30, 210),
                                                 (45.0, 225.0)])
def test_the_slack_in_the_tiers_sets_the_stored_chain_limit(slack, stored_limit):
    c = _client()
    _poll(c, ["AAPL"], tiers=dict(TIERS, carry_slack_sec=slack), now=_not_due("AAPL"))
    assert c.asked == {"AAPL": stored_limit}


def test_tiers_without_a_slack_use_the_built_in_one():
    c = _client()
    _poll(c, ["AAPL"], tiers=TIERS, now=_not_due("AAPL"))
    assert c.asked == {"AAPL": 3 * 60 + gc.CARRY_SLACK_SEC}
    assert gc.CARRY_SLACK_SEC == 30


@pytest.mark.parametrize("unusable", [None, -5, float("nan"), float("inf"), "30", True])
def test_an_unusable_slack_is_the_built_in_one_not_a_dead_poll(unusable):
    c = _client()
    _poll(c, ["SPY", "AAPL"], tiers=dict(TIERS, carry_slack_sec=unusable),
          now=_not_due("AAPL"))
    assert c.asked == {"SPY": 20, "AAPL": 3 * 60 + gc.CARRY_SLACK_SEC}


def test_the_fresh_ceiling_follows_the_slack(warned):
    """One poll interval less the slack IN FORCE, not less the built-in one."""
    c = _client()
    _poll(c, ["SPY"], tiers=dict(TIERS, carry_slack_sec=10, fresh_max_age_sec=45))
    assert c.asked == {"SPY": 45} and warned() == []         # 45 <= 60 - 10

    c = _client()
    _poll(c, ["SPY"], tiers=dict(TIERS, carry_slack_sec=10, fresh_max_age_sec=55))
    assert c.asked == {"SPY": 50} and len(warned()) == 1

    c = _client()
    _poll(c, ["SPY"], tiers=dict(TIERS, carry_slack_sec=50, fresh_max_age_sec=20))
    assert c.asked == {"SPY": 10}                            # 60 - 50

    c = _client()
    _poll(c, ["SPY"], tiers=dict(TIERS, carry_slack_sec=90, fresh_max_age_sec=20))
    assert c.asked == {"SPY": 0}                             # never below zero


def test_the_age_limit_helper_reads_the_slack_from_the_tiers():
    not_due = int(_not_due("AAPL").timestamp()) // 60
    assert gc._chain_max_age("AAPL", not_due, dict(TIERS, carry_slack_sec=5)) == 185


#############################################
# AN EMPTY TAIL STILL SENDS THE FRESH LIMIT
#############################################
# While the proxy's chain store is on the collector is always handed tiers, with
# an EMPTY tail when nothing may be carried (interval 1, no watchlist-only
# symbol, a flip alert that watches everything). With no tiers at all it would
# send no age limit and the proxy would apply its own: 1,800 s while every
# session is closed, which the collector's 08:00-08:30 and 15:00-15:20 CT
# minutes are.

NO_TAIL = {"tail": frozenset(), "interval_min": 1, "fresh_max_age_sec": 20}
CLOSED_MINUTES = [dt.datetime(2026, 10, 5, 8, 27, tzinfo=CT),
                  dt.datetime(2026, 10, 5, 15, 17, tzinfo=CT)]


@pytest.mark.parametrize("tiers", [
    NO_TAIL,
    dict(NO_TAIL, interval_min=3),                               # flip watches all
    dict(TIERS, interval_min=1),                                 # a tail, always due
    dict(NO_TAIL, interval_min=0), dict(NO_TAIL, interval_min=-4)])
@pytest.mark.parametrize("now", [RTH, RTH + dt.timedelta(minutes=1),
                                 RTH + dt.timedelta(minutes=2), *CLOSED_MINUTES])
def test_every_chain_request_carries_the_fresh_limit(tiers, now):
    seen = []
    c = _client(quotes={"SPY": 512.0, "AAPL": 103.0, "SOFI": 9.0})
    _poll(c, ["SPY", "AAPL", "SOFI"], tiers=tiers, now=now,
          on_chain=lambda s, ch: seen.append(s))
    assert c.asked == {"SPY": 20, "AAPL": 20, "SOFI": 20}
    assert seen == ["SPY", "AAPL", "SOFI"]          # nothing was carried
    for kw in c.kwargs.values():                    # and nothing else changed
        assert set(kw) == {"contract_type", "from_date", "to_date", "max_age"}


def test_with_an_empty_tail_there_is_no_quote_call_and_the_line_says_zero(caplog):
    c = _client(ages={"SPY": 3.0, "AAPL": 0.0})
    with caplog.at_level(logging.INFO, logger="gex_collector"):
        _poll(c, ["SPY", "AAPL"], tiers=NO_TAIL)
    c.get_quotes.assert_not_called()
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.INFO] == [
        "Carried 0 of 2 chain(s) forward (0 on a live quote)"]


def test_an_empty_tail_is_not_called_unusable(warned):
    _poll(_client(), ["SPY", "AAPL"], tiers=NO_TAIL)
    assert warned() == []
    assert gc._usable_tiers(NO_TAIL)["tail"] == frozenset()


@pytest.mark.parametrize("interval", [1, 0, -4])
def test_an_interval_of_one_or_less_reads_as_one(interval):
    assert gc._usable_tiers(dict(NO_TAIL, interval_min=interval))["interval_min"] == 1


def test_with_an_empty_tail_an_old_answer_is_still_carried_by_its_age():
    """The proxy will not hand back an old chain against a 20-second limit. If
    it ever does, the answer's age decides, as it does for any symbol."""
    seen = []
    engine = _engine()
    c = _client(ages={"SPY": 95.0}, quotes={"SPY": 512.0})
    _poll(c, ["SPY", "AAPL"], tiers=NO_TAIL, engine=engine,
          on_chain=lambda s, ch: seen.append(s))
    assert seen == ["AAPL"]
    assert c.get_quotes.call_args.args[0] == ["SPY"]


def test_the_fresh_ceiling_applies_with_an_empty_tail(warned):
    c = _client()
    _poll(c, ["SPY"], tiers=dict(NO_TAIL, fresh_max_age_sec=45))
    assert c.asked == {"SPY": FRESH_CEILING}
    assert len(warned()) == 1


#############################################
# A CARRY THAT FAILS IS SAID ONCE PER SYMBOL
#############################################

def test_a_failed_carry_is_a_warning_the_first_time_and_debug_after(monkeypatch, caplog):
    monkeypatch.setattr(gc, "_WARNED", set())
    real = chain_carry.carry_counted

    def flaky(chain, spot, **kw):
        if chain["symbol"] in ("AAPL", "UBER"):
            raise RuntimeError("boom")
        return real(chain, spot, **kw)

    monkeypatch.setattr(chain_carry, "carry_counted", flaky)

    def poll():
        caplog.clear()
        c = _client(ages={"AAPL": 95.0, "SOFI": 95.0, "UBER": 95.0},
                    quotes={"AAPL": 103.0, "SOFI": 9.0, "UBER": 70.0})
        with caplog.at_level(logging.DEBUG, logger="gex_collector"):
            _poll(c, ["AAPL", "SOFI", "UBER"])
        failed = [r for r in caplog.records if "Carry-forward failed" in r.getMessage()]
        return sorted((r.levelno, r.getMessage().split()[3].rstrip(";")) for r in failed)

    assert poll() == [(logging.WARNING, "AAPL"), (logging.WARNING, "UBER")]
    assert poll() == [(logging.DEBUG, "AAPL"), (logging.DEBUG, "UBER")]     # the next minute
    assert poll() == [(logging.DEBUG, "AAPL"), (logging.DEBUG, "UBER")]


def test_the_first_failure_warning_carries_the_traceback(monkeypatch, caplog):
    monkeypatch.setattr(gc, "_WARNED", set())
    monkeypatch.setattr(chain_carry, "carry_counted",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    c = _client(ages={"AAPL": 95.0}, quotes={"AAPL": 103.0})
    with caplog.at_level(logging.WARNING, logger="gex_collector"):
        _poll(c, ["AAPL"])
    (record,) = [r for r in caplog.records if "Carry-forward failed" in r.getMessage()]
    assert record.levelno == logging.WARNING and record.exc_info
    assert "stored chain" in record.getMessage()


#############################################
# WHEN THE GAMMA CAP BINDS, THE SYMBOL IS REFETCHED (audit AC-120)
#############################################
# The cap holds growth and not shrinkage, so a carried chain it bound on is
# biased against the move: net GEX can change sign and the flip level and walls
# move. Such a symbol gets a real fetch in the same poll instead.

LIVE_ON_STRIKE = 104.6


def _expiring_chain(symbol, spot=100.0, gamma=0.03):
    """A chain that expires TODAY: late-session gamma at a strike the price
    walks onto grows by far more than the cap."""
    def side(pc, sign):
        return {"2026-10-05:0": {
            str(k): [{"putCall": pc, "strikePrice": k, "gamma": gamma,
                      "delta": sign * 0.3, "volatility": 30.0, "openInterest": 500,
                      "totalVolume": 40, "mark": 1.25}]
            for k in (95.0, 100.0, 105.0)}}
    return {"symbol": symbol, "underlyingPrice": spot,
            "callExpDateMap": side("CALL", 1), "putExpDateMap": side("PUT", -1)}


def _cap_client(binding, refetch_status=200, refetch_raises=False, quotes=None):
    """Every symbol's first answer is 95 seconds old. A symbol in ``binding``
    holds a chain the cap binds on; its second answer is a new chain, 1 second
    old, at the live price."""
    quotes = quotes or {s: LIVE_ON_STRIKE for s in binding}
    client = _client(quotes=quotes)
    client.chain_calls = []

    def get_chain(symbol, **kw):
        again = any(s == symbol for s, _ in client.chain_calls)
        client.chain_calls.append((symbol, kw.get("max_age", "none")))
        resp = MagicMock()
        if again:
            if refetch_raises:
                raise RuntimeError("proxy down")
            resp.status_code = refetch_status
            resp.json.return_value = _expiring_chain(symbol, spot=LIVE_ON_STRIKE,
                                                     gamma=0.2)
            resp.store_age = 1.0
            return resp
        resp.status_code = 200
        resp.json.return_value = (_expiring_chain(symbol) if symbol in binding
                                  else _real_chain(symbol))
        resp.store_age = 95.0
        return resp

    client.get_option_chain.side_effect = get_chain
    return client


def test_the_fixture_really_binds_the_cap():
    assert chain_carry.capped_gammas(_expiring_chain("AAPL"), LIVE_ON_STRIKE,
                                     age_sec=95.0, now=RTH) > 0
    assert chain_carry.capped_gammas(_real_chain("AAPL"), 103.0,
                                     age_sec=95.0, now=RTH) == 0


def test_a_symbol_the_cap_binds_on_is_refetched_in_the_same_poll():
    engine, seen = _engine(), []
    c = _cap_client({"AAPL"})
    _poll(c, ["AAPL"], engine=engine, on_chain=lambda s, ch: seen.append(s))
    # Asked again, with the fresh limit: a real fetch.
    assert c.chain_calls == [("AAPL", c.chain_calls[0][1]), ("AAPL", 20)]
    got = engine.calc_all_from_chain.call_args.args[0]
    assert got == _expiring_chain("AAPL", spot=LIVE_ON_STRIKE, gamma=0.2)
    # It is a fetched chain now: the detectors get it.
    assert seen == ["AAPL"]


def test_a_carry_the_cap_does_not_bind_on_is_not_refetched():
    c = _cap_client(set(), quotes={"AAPL": 103.0})
    _poll(c, ["AAPL"])
    assert [s for s, _ in c.chain_calls] == ["AAPL"]


@pytest.mark.parametrize("kw", [{"refetch_status": 500}, {"refetch_raises": True}])
def test_a_refetch_that_fails_writes_the_carried_chain(kw):
    engine, seen = _engine(), []
    c = _cap_client({"AAPL"}, **kw)
    _poll(c, ["AAPL"], engine=engine, on_chain=lambda s, ch: seen.append(s))
    got = engine.calc_all_from_chain.call_args.args[0]
    assert got == chain_carry.carry_chain(_expiring_chain("AAPL"), LIVE_ON_STRIKE,
                                          age_sec=95.0, now=RTH)
    assert seen == []                      # still a carried chain


def test_at_most_the_configured_number_of_symbols_is_refetched(caplog):
    c = _cap_client({"AAPL", "SOFI", "UBER"})
    with caplog.at_level(logging.INFO, logger="gex_collector"):
        _poll(c, ["AAPL", "SOFI", "UBER"], tiers={**TIERS, "cap_refetch_max": 2})
    assert len(c.chain_calls) == 3 + 2
    info = [r.getMessage() for r in caplog.records
            if r.name == "gex_collector" and r.levelno == logging.INFO]
    assert info == ["Carried 1 of 3 chain(s) forward (1 on a live quote)",
                    "Refetched 2 chain(s) whose carried gamma hit the cap "
                    "(1 left capped)"]


def test_a_limit_of_zero_refetches_nothing():
    c = _cap_client({"AAPL"})
    _poll(c, ["AAPL"], tiers={**TIERS, "cap_refetch_max": 0})
    assert [s for s, _ in c.chain_calls] == ["AAPL"]


@pytest.mark.parametrize("bad", [None, "many", -1, True, float("nan")])
def test_an_unusable_refetch_limit_is_the_built_in_one(bad):
    tiers = gc._usable_tiers({**TIERS, "cap_refetch_max": bad})
    assert tiers["cap_refetch_max"] == gc.CAP_REFETCH_MAX


def test_no_second_line_when_the_cap_never_bound(caplog):
    c = _cap_client(set(), quotes={"AAPL": 103.0})
    with caplog.at_level(logging.INFO, logger="gex_collector"):
        _poll(c, ["AAPL"])
    info = [r.getMessage() for r in caplog.records
            if r.name == "gex_collector" and r.levelno == logging.INFO]
    assert info == ["Carried 1 of 1 chain(s) forward (1 on a live quote)"]


def test_a_refetched_symbols_skew_readings_are_its_new_chains(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(gc.flow_skew, "premium_by_strike",
                        lambda chain: seen.append(chain["underlyingPrice"]) or {})
    c = _cap_client({"AAPL"})
    _poll(c, ["AAPL"])
    assert seen == [LIVE_ON_STRIKE]        # not the stored chain's 100.0


#############################################
# A CARRIED ROW SAYS SO IN STORAGE (audit AC-121)
#############################################
# At an interval of 3, two of every three rows for a watchlist-only symbol hold
# modelled Greeks and repeated volume and premium. Nothing stored told them
# from a fetched row, so a later study would have read them as observations.

def _carried_ages(tmp_path, monkeypatch, client, symbols):
    import gamma_tool as gt
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    gc.poll_once(client, gt.GammaEngine(), conn, symbols=symbols,
                 poll_term=False, now=RTH, tiers=TIERS)
    out = {}
    for symbol, view, age in conn.execute(
            "SELECT symbol, view, carried_age_sec FROM snapshots"):
        out.setdefault(symbol, {})[view] = age
    conn.close()
    return out


def test_every_view_of_a_carried_minute_records_how_old_its_chain_was(
        tmp_path, monkeypatch):
    c = _client(ages={"AAPL": 95.0, "SPY": 3.0}, quotes={"AAPL": 103.0},
                chain=_real_chain)
    ages = _carried_ages(tmp_path, monkeypatch, c, ["SPY", "AAPL"])
    views = {"gex", "charm", "dex", "vanna", "prem"}
    assert ages["AAPL"] == {v: 95.0 for v in views}
    assert ages["SPY"] == {v: None for v in views}      # fetched: no age


def test_a_carried_chain_that_got_no_live_price_is_still_marked_carried(
        tmp_path, monkeypatch):
    # Not re-priced, but still an old chain's volume and premium.
    c = _client(ages={"AAPL": 95.0}, quotes={}, chain=_real_chain)
    ages = _carried_ages(tmp_path, monkeypatch, c, ["AAPL"])
    assert set(ages["AAPL"].values()) == {95.0}


def test_a_symbol_refetched_because_the_cap_bound_is_a_fetched_row(
        tmp_path, monkeypatch):
    c = _cap_client({"AAPL"})
    ages = _carried_ages(tmp_path, monkeypatch, c, ["AAPL"])
    assert set(ages["AAPL"].values()) == {None}


def test_without_tiers_no_row_is_marked_carried(tmp_path, monkeypatch):
    import gamma_tool as gt
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    conn = db.connect()
    db.init_schema(conn)
    c = _client(ages={"AAPL": 95.0}, chain=_real_chain)
    gc.poll_once(c, gt.GammaEngine(), conn, symbols=["AAPL"], poll_term=False,
                 now=RTH, tiers=None)
    assert {r[0] for r in conn.execute(
        "SELECT carried_age_sec FROM snapshots")} == {None}
    conn.close()
