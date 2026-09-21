"""The public chain holder: quoted chains live in the worker's memory, and what
reaches Redis is stripped of every quote unless the one quotes switch is on.

The two Schwab calls - the Calculator's lazy chain load and the one-expiration
fetch - are stubbed and recorded, with the same chain shape the Rescue worker's
tests use: it carries bid, ask, mark and delta, so their absence in a published
payload is proven rather than assumed.
"""
import datetime as dt
import math
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute
from services.options_svc import public_chain as pc
from shared import public_rescue as pr
from shared import public_scan
from shared import public_tools
from shared.bus import Bus
from shared.bus.client import reset_fake_bus

CT = ZoneInfo("America/Chicago")
OPEN = dt.datetime(2026, 9, 21, 10, 0, tzinfo=CT)
NEAR, MID, FAR = "2026-10-02", "2026-10-09", "2027-01-15"
LISTED = [NEAR, MID, "2026-10-16", FAR]
QUOTE_FIELDS = {"bid", "ask", "mark", "delta"}


def _chain(expiries, *, bid=1.0):
    maps = {f"{e}:9": {f"{k:.1f}": [{"bid": bid, "ask": 1.2, "mark": 1.1,
                                     "delta": -0.3, "gamma": 0.01,
                                     "theta": -0.05, "vega": 0.1,
                                     "volatility": 22.0, "openInterest": 900,
                                     "totalVolume": 40}]
                       for k in (495.0, 500.0, 505.0)} for e in expiries}
    return {"callExpDateMap": dict(maps), "putExpDateMap": dict(maps)}


class Clock:
    t = 1000.0


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    reset_fake_bus()
    pc.reset()
    Clock.t = 1000.0
    monkeypatch.setattr(pc, "_mono", lambda: Clock.t)
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: False)
    yield
    pc.reset()


@pytest.fixture
def schwab(monkeypatch):
    calls = []

    class Stub:
        load_fails = False
        fetch_fails = False
        bid = 1.0

    def load(symbol, lazy=False, expiries=None):
        calls.append(("load", symbol, tuple(expiries or ())))
        if Stub.load_fails:
            return {"symbol": symbol, "api": symbol, "price": None, "chain": None}
        loaded = [NEAR, MID] + [e for e in (expiries or []) if e in LISTED]
        return {"symbol": symbol, "api": symbol, "price": 502.37,
                "expirations": list(LISTED), "chain": _chain(loaded, bid=Stub.bid)}

    def fetch(api, runs):
        calls.append(("fetch", api, tuple(tuple(r) for r in runs)))
        return None if Stub.fetch_fails else _chain(runs[0], bid=Stub.bid)

    monkeypatch.setattr(compute, "calc_load_symbol", load)
    monkeypatch.setattr(compute, "_fetch_thin_runs", fetch)
    Stub.calls = calls
    return Stub


def _first(symbol="SPY"):
    payload, outcome = pc.load(symbol, None, None, OPEN, fresh=False)
    assert outcome == "done"
    return payload


def _held_expiries(symbol="SPY"):
    chain = pc.held(symbol)["chain"]
    return {k.split(":")[0] for k in chain["putExpDateMap"]}


# ── what is held, what is published ─────────────────────────────────────────

def test_a_load_holds_the_quoted_chain_and_publishes_strikes_only(schwab):
    payload = _first()
    text = repr(payload)
    for quote in QUOTE_FIELDS:
        assert quote not in text, f"the published chain carries {quote}"
    assert "quotes" not in payload
    assert payload["strikes"][NEAR]["put"] == [495.0, 500.0, 505.0]
    assert payload["spot"] == 502.37 and payload["expirations"] == LISTED
    held_text = repr(pc.held("SPY")["chain"])
    for quote in QUOTE_FIELDS:
        assert quote in held_text, f"the held chain lost {quote}"


def test_with_quotes_on_the_published_chain_carries_only_four_quote_fields(
        schwab, monkeypatch):
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: True)
    payload = _first()
    q = payload["quotes"][NEAR]["put"]["500.0"]
    assert set(q) == QUOTE_FIELDS
    assert q == {"bid": 1.0, "ask": 1.2, "mark": 1.1, "delta": -0.3}
    text = repr(payload)
    for other in ("gamma", "theta", "vega", "volatility", "openInterest",
                  "totalVolume"):
        assert other not in text, f"the published chain carries {other}"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"),
                                 True, "1.0", None])
def test_a_non_finite_quote_is_published_as_none(schwab, monkeypatch, bad):
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: True)
    schwab.bid = bad
    payload = _first()
    for exp in payload["quotes"].values():
        for side in exp.values():
            for q in side.values():
                assert q["bid"] is None
                assert all(v is None or math.isfinite(v) for v in q.values())


def test_quotes_from_chain_reads_only_the_first_row_and_skips_junk():
    chain = {"putExpDateMap": {"2026-10-02:9": {
        "500": [{"bid": 2.0, "ask": 2.1, "mark": 2.05, "delta": -0.4}],
        "x": [{"bid": 1.0}], "505.0": [], "510.0": "junk"}}}
    out = pc.quotes_from_chain(chain)
    assert out == {"2026-10-02": {"put": {"500.0": {
        "bid": 2.0, "ask": 2.1, "mark": 2.05, "delta": -0.4}}}}
    assert pc.quotes_from_chain(None) == {}


# ── the memory ──────────────────────────────────────────────────────────────

def test_held_chains_are_bounded(schwab, monkeypatch):
    real = public_tools.limits

    def limits():
        return {**real(), "chain_hold_limit": 2}
    monkeypatch.setattr(public_tools, "limits", limits)
    for sym in ("SPY", "QQQ", "IWM"):
        _first(sym)
    assert pc.held("SPY") is None, "the oldest chain was not evicted"
    assert pc.held("QQQ") is not None and pc.held("IWM") is not None


def test_a_held_chain_expires(schwab):
    _first()
    Clock.t += pr.limits()["ladder_ttl_min"] * 60 - 1
    assert pc.held("SPY") is not None
    Clock.t += 2
    assert pc.held("SPY") is None


# ── the merge ───────────────────────────────────────────────────────────────

def test_an_expiry_merge_updates_the_held_chain_too(schwab):
    first = _first()
    schwab.calls.clear()
    payload, outcome = pc.load("SPY", FAR, first, OPEN, fresh=True)
    assert outcome == "done"
    assert schwab.calls == [("fetch", "SPY", ((FAR,),))]
    assert set(payload["strikes"]) == {NEAR, MID, FAR}
    assert payload["loaded_at"] == first["loaded_at"]
    assert _held_expiries() == {NEAR, MID, FAR}


def test_a_merge_with_no_held_chain_does_a_full_load(schwab):
    first = _first()
    pc.reset()                                    # e.g. the service restarted
    schwab.calls.clear()
    payload, outcome = pc.load("SPY", FAR, first, OPEN, fresh=True)
    assert outcome == "done"
    kind, _sym, wanted = schwab.calls[0]
    assert kind == "load" and FAR in wanted and {NEAR, MID} <= set(wanted)
    assert _held_expiries() >= {NEAR, MID, FAR}
    assert set(payload["strikes"]) >= {NEAR, MID, FAR}


@pytest.mark.parametrize("path", ["full", "merge"])
def test_a_failed_fetch_holds_nothing_and_publishes_nothing(schwab, path):
    if path == "full":
        schwab.load_fails = True
        payload, outcome = pc.load("SPY", None, None, OPEN, fresh=False)
        assert pc.held("SPY") is None
    else:
        first = _first()
        before = pc.held("SPY")
        schwab.fetch_fails = True
        payload, outcome = pc.load("SPY", FAR, first, OPEN, fresh=True)
        assert pc.held("SPY") == before, "a failed merge changed the held chain"
        assert _held_expiries() == {NEAR, MID}
    assert payload is None and outcome == "error"


def test_the_quotes_switch_is_read_at_write_time(schwab, monkeypatch):
    first = _first()
    assert "quotes" not in first
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: True)
    payload, outcome = pc.load("SPY", FAR, first, OPEN, fresh=True)
    assert outcome == "done"
    assert set(payload["quotes"]) == {NEAR, MID, FAR}


def test_publish_writes_the_shared_chain_key_with_a_ttl(schwab):
    bus = Bus(fake=True)
    payload = _first()
    pc.publish(bus, "SPY", payload, 180)
    env = bus.cache_get(pr.cache_key(pr.ladder_view("SPY")))
    assert env.payload == payload
    ttl = bus._r.ttl(pr.cache_key(pr.ladder_view("SPY")))
    assert 0 < ttl <= 180 * 60


# ── from the Task 5 review ──────────────────────────────────────────────────

def test_switch_on_at_load_then_off_at_merge_publishes_no_quotes(schwab, monkeypatch):
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: True)
    first = _first()
    assert "quotes" in first
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: False)
    payload, outcome = pc.load("SPY", FAR, first, OPEN, fresh=True)
    assert outcome == "done"
    assert "quotes" not in payload
    for quote in QUOTE_FIELDS:
        assert quote not in repr(payload)


def test_a_merge_keeps_the_held_chain_age(schwab):
    ttl = pr.limits()["ladder_ttl_min"] * 60
    first = _first()
    Clock.t += ttl - 10
    payload, outcome = pc.load("SPY", FAR, first, OPEN, fresh=True)
    assert outcome == "done" and pc.held("SPY") is not None
    Clock.t += 20                          # past the ttl from the ORIGINAL load
    assert pc.held("SPY") is None, "a merge made the held chain look newer"


def test_reconcile_strips_or_rebuilds_the_quotes_block(schwab, monkeypatch):
    first = _first()                                   # switch off: no quotes
    assert pc.reconcile(first, "SPY") is None          # already consistent
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: True)
    rebuilt = pc.reconcile(first, "SPY")
    assert rebuilt["quotes"][NEAR]["put"]["500.0"]["mark"] == 1.1
    assert pc.reconcile(rebuilt, "SPY") is None
    pc.reset()
    assert pc.reconcile(first, "SPY") is None, "no held chain: nothing to build from"
    monkeypatch.setattr(public_scan, "show_leg_quotes", lambda: False)
    stripped = pc.reconcile(rebuilt, "SPY")
    assert "quotes" not in stripped and stripped["strikes"] == first["strikes"]
    assert pc.reconcile({"symbol": "ZZZZ", "no_options": True}, "ZZZZ") is None
    assert pc.reconcile(None, "SPY") is None


def test_a_non_finite_strike_key_is_skipped():
    row = [{"bid": 1.0, "ask": 1.1, "mark": 1.05, "delta": -0.2}]
    chain = {"putExpDateMap": {"2026-10-02:9": {
        "nan": row, "1e400": row, "-inf": row, "500.0": row}}}
    assert pc.strikes_from_chain(chain)["2026-10-02"]["put"] == [500.0]
    assert list(pc.quotes_from_chain(chain)["2026-10-02"]["put"]) == ["500.0"]


# ── two consumer threads on one symbol ──────────────────────────────────────
# Rescue's worker and the tools worker each call ``ladder_request`` on their own
# thread. Without a per-symbol lock, two merges on one symbol within a second
# each merge into the chain they READ, and the second to finish wins: an
# expiration the published list names can be missing from the held chain (it
# then answers ``cached`` while the math on it answers ``off_ladder``), and two
# identical requests can each spend the budget.

import threading  # noqa: E402

X = "2026-10-16"
Y = FAR


def _caller(bus, spent, label):
    """One worker's callbacks: its OWN dedup memory, as Rescue and the tools
    worker each have, so dedup cannot hide a race between them."""
    ran = set()

    def run(expiry):
        return pc.ladder_request(
            bus, "SPY", expiry, age=0.0, now=OPEN, max_wait_sec=60,
            window="tools_public", recently=lambda: expiry in ran,
            spend=lambda: spent.append(label) or True,
            start=lambda: ran.add(expiry), area=f"test.{label}")
    return run


def _published_expiries(bus):
    return set((pc.published(bus, "SPY") or {}).get("strikes") or {})


def _blocking_fetch(monkeypatch, schwab, block_on):
    """The one-expiration fetch for ``block_on`` waits until released, so the
    other thread is dispatched while it is in flight."""
    started, release = threading.Event(), threading.Event()
    real = compute._fetch_thin_runs

    def fetch(api, runs):
        if runs[0] == [block_on]:
            started.set()
            assert release.wait(5), "never released"
        return real(api, runs)
    monkeypatch.setattr(compute, "_fetch_thin_runs", fetch)
    return started, release


@pytest.fixture
def in_window(monkeypatch):
    monkeypatch.setattr(pc.market_calendar, "in_window", lambda window, now: True)


def _race(first, second, started, release):
    a = threading.Thread(target=first)
    a.start()
    assert started.wait(5)
    b = threading.Thread(target=second)
    b.start()
    # Unserialized, the second request runs to the end while the first is held;
    # serialized, it waits on the symbol's lock. Either way, release after.
    b.join(0.5)
    release.set()
    a.join(5)
    b.join(5)
    assert not a.is_alive() and not b.is_alive()


def test_two_different_merges_on_one_symbol_both_land_held_and_published(
        schwab, monkeypatch, in_window):
    bus = Bus(fake=True)
    spent = []
    rescue, tools = _caller(bus, spent, "rescue"), _caller(bus, spent, "tools")
    assert tools(None) == "done"                       # the list, NEAR and MID
    started, release = _blocking_fetch(monkeypatch, schwab, X)
    out = {}
    _race(lambda: out.__setitem__("x", rescue(X)),
          lambda: out.__setitem__("y", tools(Y)), started, release)
    assert out == {"x": "done", "y": "done"}
    assert _held_expiries() == {NEAR, MID, X, Y}
    assert _published_expiries(bus) == _held_expiries()


def test_two_identical_requests_on_two_workers_spend_the_budget_once(
        schwab, monkeypatch, in_window):
    bus = Bus(fake=True)
    spent = []
    rescue, tools = _caller(bus, spent, "rescue"), _caller(bus, spent, "tools")
    assert tools(None) == "done"
    spent.clear()
    started, release = _blocking_fetch(monkeypatch, schwab, X)
    out = {}
    _race(lambda: out.__setitem__("a", rescue(X)),
          lambda: out.__setitem__("b", tools(X)), started, release)
    assert out == {"a": "done", "b": "cached"}
    assert spent == ["rescue"]
    assert [c for c in schwab.calls if c[0] == "fetch"] == [("fetch", "SPY", ((X,),))]


def test_the_symbol_locks_do_not_accumulate(schwab, in_window):
    bus = Bus(fake=True)
    run = _caller(bus, [], "tools")
    for sym in ("SPY", "QQQ", "IWM"):
        pc.ladder_request(bus, sym, None, age=0.0, now=OPEN, max_wait_sec=60,
                          window="tools_public", recently=lambda: False,
                          spend=lambda: True, start=lambda: None, area="test")
    assert run(None) == "cached"
    assert pc._SYMBOL_LOCKS == {}
