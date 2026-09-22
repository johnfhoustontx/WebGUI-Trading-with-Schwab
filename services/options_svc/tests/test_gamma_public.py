"""The public Gamma page's worker: the hot set, every outcome, and the tick snapshot.

The worker spends nothing (no Schwab call, no snapshot build), so these tests
are about WHICH symbols end up hot, when, and what the page is told.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import gamma_public as gp
from shared import public_gamma as pg
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command

CT = ZoneInfo("America/Chicago")
OPEN = dt.datetime(2026, 9, 22, 10, 0, tzinfo=CT)       # a Tuesday, in session
LIST = ["$SPX", "SPY", "QQQ", "NVDA", "AAPL", "TSLA", "AMD", "META", "IWM",
        "XLU", "XLE", "MSFT", "GOOGL"]


@pytest.fixture(autouse=True)
def _empty_hot_set():
    gp.reset()
    yield
    gp.reset()


@pytest.fixture
def bus(monkeypatch):
    reset_fake_bus()
    b = Bus(fake=True)
    b.cache_set(pg.SYMBOLS_KEY, {"symbols": LIST})
    monkeypatch.setattr(gp, "_now", lambda: OPEN)
    return b


def _cmd(symbol="NVDA", age_s=0.0, now=OPEN):
    ts = (now - dt.timedelta(seconds=age_s)).astimezone(dt.timezone.utc).isoformat()
    return Command(type=pg.COMMAND_TYPE, args={"symbol": symbol}, ts=ts)


def _status(bus):
    env = bus.cache_get(pg.STATUS_KEY)
    return env.payload if env else None


def _decide(bus, symbol, now=OPEN, **kw):
    return gp.decide(bus, _cmd(symbol, now=now, **kw), now)[0]


# ── the outcomes, in order ──────────────────────────────────────────────────

def test_a_first_request_adds_the_symbol_and_tells_the_page(bus):
    gp.handle(bus, _cmd(" nvda "))
    assert gp.hot_symbols(OPEN) == ["NVDA"]
    st = _status(bus)
    assert st["hot"] == ["NVDA"] and st["cap"] == 8 and st["slots_used"] == 1
    assert st["permanent"] == ["$SPX", "QQQ", "SPY"]
    assert st["window"] == {"start": "08:00", "end": "15:20", "tz": "CT"}


def test_a_repeat_request_renews_the_lease(bus):
    assert _decide(bus, "NVDA") == "added"
    later = OPEN + dt.timedelta(minutes=10)
    assert _decide(bus, "NVDA", now=later) == "live"
    # 10 + 15 minutes: alive past the FIRST lease's end
    assert gp.hot_symbols(OPEN + dt.timedelta(minutes=20)) == ["NVDA"]
    assert gp.hot_symbols(OPEN + dt.timedelta(minutes=26)) == []


@pytest.mark.parametrize("raw", ["ZZZZ", "spy; flushall", None, ""])
def test_a_symbol_off_the_dropdown_list_is_invalid(bus, raw):
    assert gp.decide(bus, Command(type=pg.COMMAND_TYPE, args={"symbol": raw},
                                  ts=OPEN.isoformat()), OPEN)[0] == "invalid"
    assert gp.hot_symbols(OPEN) == []


def test_a_cold_list_allows_only_the_permanent_symbols(monkeypatch):
    """A fresh start has not published the list yet. A pick is never checked
    against a list nobody wrote -- only $SPX, SPY and QQQ pass."""
    reset_fake_bus()
    b = Bus(fake=True)
    assert gp.allowed_symbols(b) == {"$SPX", "SPY", "QQQ"}
    assert _decide(b, "NVDA") == "invalid"
    assert _decide(b, "SPY") == "added"


def test_a_stale_or_future_request_is_expired(bus):
    assert _decide(bus, "NVDA", age_s=pg.max_wait_sec() + 1) == "expired"
    assert _decide(bus, "NVDA", age_s=-(gp.FUTURE_SKEW_SEC + 5)) == "expired"
    assert gp.hot_symbols(OPEN) == []


def test_outside_the_window_it_is_closed(bus):
    evening = OPEN.replace(hour=19)
    assert _decide(bus, "NVDA", now=evening) == "closed"
    saturday = dt.datetime(2026, 9, 26, 10, 0, tzinfo=CT)
    assert _decide(bus, "NVDA", now=saturday) == "closed"
    assert gp.hot_symbols(OPEN) == []


def test_the_cap_counts_only_visitor_picked_symbols(bus, monkeypatch):
    monkeypatch.setattr(pg, "hot", lambda: {"cap": 2, "lease_min": 15, "keep_min": 30})
    assert _decide(bus, "NVDA") == "added"
    assert _decide(bus, "SPY") == "added"          # permanent: no slot
    assert _decide(bus, "AAPL") == "added"
    assert _decide(bus, "TSLA") == "full"
    assert _decide(bus, "QQQ") == "added"          # permanent: still fits
    assert gp.hot_symbols(OPEN) == ["NVDA", "SPY", "AAPL", "QQQ"]


def test_a_freed_slot_is_reusable_once_a_lease_runs_out(bus, monkeypatch):
    monkeypatch.setattr(pg, "hot", lambda: {"cap": 1, "lease_min": 15, "keep_min": 30})
    assert _decide(bus, "NVDA") == "added"
    assert _decide(bus, "AAPL") == "full"
    later = OPEN + dt.timedelta(minutes=16)
    assert _decide(bus, "AAPL", now=later) == "added"


def test_a_cap_of_zero_leaves_only_the_permanent_symbols(bus, monkeypatch):
    monkeypatch.setattr(pg, "hot", lambda: {"cap": 0, "lease_min": 15, "keep_min": 30})
    assert _decide(bus, "NVDA") == "full"
    assert _decide(bus, "SPY") == "added"


def test_handle_never_raises(bus, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("x")
    monkeypatch.setattr(gp, "decide", boom)
    gp.handle(bus, _cmd())             # must not raise
    gp.handle(bus, object())           # a malformed command neither


# ── the tick ────────────────────────────────────────────────────────────────

def test_the_tick_takes_its_hot_set_once(bus):
    """A symbol granted AFTER begin_tick waits for the next tick: this tick's
    collect did not keep its chain, so building it now would fetch from Schwab."""
    _decide(bus, "NVDA")
    assert gp.begin_tick(bus, OPEN) == ("NVDA",)
    _decide(bus, "AAPL")
    assert gp.tick_symbols() == ("NVDA",)
    assert gp.begin_tick(bus, OPEN) == ("NVDA", "AAPL")


def test_a_lease_running_out_updates_the_page_at_the_next_tick(bus):
    gp.handle(bus, _cmd("NVDA"))
    assert _status(bus)["hot"] == ["NVDA"]
    gp.begin_tick(bus, OPEN + dt.timedelta(minutes=16))
    assert gp.tick_symbols() == ()
    assert _status(bus)["hot"] == []


def test_the_page_can_read_its_own_outcome(bus, monkeypatch):
    """full and closed change no lease, so without a per-symbol record the page
    could not tell them from a request still in flight."""
    monkeypatch.setattr(pg, "hot", lambda: {"cap": 1, "lease_min": 15, "keep_min": 30})
    gp.handle(bus, _cmd("NVDA"))
    gp.handle(bus, _cmd("AAPL"))
    last = _status(bus)["last"]
    assert last["NVDA"]["outcome"] == "added"
    assert last["AAPL"]["outcome"] == "full"
    assert last["AAPL"]["at"] == OPEN.isoformat()


def test_a_repeated_refusal_is_restamped_for_the_page_that_asked(bus, monkeypatch):
    """The page trusts only a record at least as new as its own request. A
    second "closed" must carry the second request's time, or a page loaded
    after hours reads the first as stale and says "live" (prod, 2026-09-21)."""
    evening = OPEN.replace(hour=19)
    later = evening + dt.timedelta(minutes=5)
    for now in (evening, later):
        monkeypatch.setattr(gp, "_now", lambda now=now: now)
        gp.handle(bus, _cmd("SPY", now=now))
    assert _status(bus)["last"]["SPY"] == {"outcome": "closed",
                                           "at": later.isoformat()}


def _status_version(bus):
    return int(bus._r.get(f"{pg.STATUS_KEY}:ver") or 0)


def test_only_a_renewal_after_a_renewal_writes_nothing(bus):
    gp.handle(bus, _cmd("NVDA"))                     # added: written
    after_add = _status_version(bus)
    gp.handle(bus, _cmd("NVDA"))                     # live after added: written
    assert _status_version(bus) == after_add + 1
    gp.handle(bus, _cmd("NVDA"))                     # live after live: skipped
    assert _status_version(bus) == after_add + 1


def test_an_off_list_symbol_is_never_recorded(bus):
    gp.handle(bus, _cmd("NVDA"))
    gp.handle(bus, _cmd("ZZZZ"))
    assert set(_status(bus)["last"]) == {"NVDA"}


def test_the_hot_set_names_only_dropdown_symbols(bus):
    """The status view is public. Only symbols from the public list can reach it,
    so it never shows a string a visitor typed."""
    for raw in ("NVDA", "NOT_A_TICKER", "ZZZZ", "AAPL"):
        gp.handle(bus, _cmd(raw))
    assert set(_status(bus)["hot"]) <= set(LIST)
    assert set(_status(bus)["last"]) <= set(LIST)
