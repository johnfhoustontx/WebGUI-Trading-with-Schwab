"""The public Gamma page's HOT symbols, as the 1-minute tick publishes them.

Driven through the PRODUCER (``collect_gex_history`` / ``refresh_gamma_current``)
with a stubbed snapshot, then read back off the bus. The promise under test is
that a hot symbol costs no Schwab call: it is built only from a chain the
tick's collect kept, and never with Term.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import compute, gamma_public, handlers
from shared import public_gamma as pg
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command

CT = ZoneInfo("America/Chicago")
OPEN = dt.datetime(2026, 9, 22, 10, 0, tzinfo=CT)
VIEWS = ("GEX", "Charm", "DEX", "Vanna")


def _snap(symbol):
    return {"symbol": symbol, "spot": 100.0, "dte": 0,
            "views": {v: {"data": {}, "summary": {}, "walls": [], "flip": None,
                          "history": [[1, 100.0, None, None, None, 0, {}]]}
                      for v in VIEWS},
            "term": {}}


@pytest.fixture
def bus(monkeypatch):
    reset_fake_bus()
    gamma_public.reset()
    compute.reset_tick_chain()
    b = Bus(fake=True)
    b.cache_set(handlers.CACHE_GAMMA, {"symbol": "AMD", "views": {}})
    b.cache_set(pg.SYMBOLS_KEY, {"symbols": ["$SPX", "SPY", "QQQ", "AMD", "NVDA",
                                             "AAPL"]})
    monkeypatch.setattr(gamma_public, "_now", lambda: OPEN)
    yield b
    gamma_public.reset()
    compute.reset_tick_chain()


@pytest.fixture
def builds(monkeypatch):
    """Every gamma_snapshot call, with the chain and the Term flag it got."""
    calls = []

    def fake(symbol, chain=None, with_term=True):
        calls.append({"symbol": symbol, "chain": chain, "with_term": with_term})
        return _snap(symbol)

    monkeypatch.setattr(handlers.compute, "gamma_snapshot", fake)
    return calls


def _grant(bus, *symbols):
    for s in symbols:
        gamma_public.handle(bus, Command(type=pg.COMMAND_TYPE, args={"symbol": s},
                                         ts=OPEN.isoformat()))


def _tick(bus, kept=()):
    """begin_tick as the collect does, the collect's kept chains, then the refresh."""
    gamma_public.begin_tick(bus, OPEN)
    for s in kept:
        compute._stash_tick_chain(s, {"chain_for": s})
    handlers.refresh_gamma_current(bus)


def _ttl(bus, key):
    return bus._r.ttl(key)


# ── a visitor-picked symbol ─────────────────────────────────────────────────

def test_a_hot_symbol_is_published_from_its_kept_chain_without_term(bus, builds):
    _grant(bus, "NVDA")
    _tick(bus, kept=("NVDA",))
    nvda = [c for c in builds if c["symbol"] == "NVDA"]
    assert nvda == [{"symbol": "NVDA", "chain": {"chain_for": "NVDA"},
                     "with_term": False}]
    env = bus.cache_get(handlers.gamma_pub_key("NVDA"))
    assert env.payload["symbol"] == "NVDA"
    for view in VIEWS:
        hist = bus.cache_get(handlers.gamma_pub_history_key("NVDA", view))
        assert hist.payload["symbol"] == "NVDA" and hist.payload["rows"]


def test_a_hot_symbols_keys_expire(bus, builds):
    """Visitors choose these symbols, so every key they get carries a TTL."""
    _grant(bus, "NVDA")
    _tick(bus, kept=("NVDA",))
    keep = pg.hot()["keep_min"] * 60
    keys = [handlers.gamma_pub_key("NVDA")] + [
        handlers.gamma_pub_history_key("NVDA", v) for v in VIEWS]
    for key in keys:
        assert 0 < _ttl(bus, key) <= keep, key


def test_no_kept_chain_means_no_build_and_no_fetch(bus, builds, monkeypatch):
    """Granted after the tick took its hot set, or the collect failed for it:
    skipped this minute. The one thing it must never do is fetch a chain."""
    fetched = []
    monkeypatch.setattr(handlers.compute, "_gamma_fetch_chain",
                        lambda s: fetched.append(s))
    _grant(bus, "NVDA")
    _tick(bus, kept=())
    assert not [c for c in builds if c["symbol"] == "NVDA"]
    assert fetched == []
    assert bus.cache_get(handlers.gamma_pub_key("NVDA")) is None


def test_a_symbol_granted_mid_tick_waits_for_the_next(bus, builds):
    gamma_public.begin_tick(bus, OPEN)             # the collect started
    _grant(bus, "NVDA")                            # a visitor picks, mid-tick
    compute._stash_tick_chain("NVDA", {"chain_for": "NVDA"})
    handlers.refresh_gamma_current(bus)
    assert not [c for c in builds if c["symbol"] == "NVDA"]
    _tick(bus, kept=("NVDA",))                     # the next tick
    assert [c["symbol"] for c in builds].count("NVDA") == 1


def test_the_private_symbol_being_hot_is_built_once(bus, builds):
    """AMD is the private page's symbol AND hot: one build (with Term, for the
    private page) lands in both key families."""
    _grant(bus, "AMD")
    _tick(bus, kept=("AMD",))
    assert [c["symbol"] for c in builds].count("AMD") == 1
    assert bus.cache_get(handlers.gamma_pub_key("AMD")).payload["symbol"] == "AMD"
    assert bus.cache_get(handlers.CACHE_GAMMA).payload["symbol"] == "AMD"


def test_an_expired_lease_stops_the_private_refresh_writing_public_keys(
        bus, builds, monkeypatch):
    """Ticks stop at the window's end, so the last tick's hot set would read as
    hot all night. The private page's own refresh of a once-picked symbol must
    not keep its public keys alive after the lease ran out."""
    _grant(bus, "AMD")
    _tick(bus, kept=("AMD",))
    assert gamma_public.tick_symbols() == ("AMD",)
    late = OPEN + dt.timedelta(minutes=pg.hot()["lease_min"] + 1)
    monkeypatch.setattr(gamma_public, "_now", lambda: late)
    assert gamma_public.tick_symbols() == ()
    bus._r.delete(handlers.gamma_pub_key("AMD"))
    handlers.refresh_gamma(bus, "AMD")                 # the owner's gamma_refresh
    assert bus.cache_get(handlers.gamma_pub_key("AMD")) is None
    assert bus.cache_get(handlers.CACHE_GAMMA).payload["symbol"] == "AMD"


# ── a permanent symbol ──────────────────────────────────────────────────────

def test_a_lease_on_spy_adds_its_missing_views_with_a_ttl(bus, builds):
    """SPY publishes GEX history only. Held hot, it gains Charm, DEX and Vanna,
    as TTL'd keys, while its own main key and GEX keep no TTL."""
    _grant(bus, "SPY")
    _tick(bus)
    assert [c["symbol"] for c in builds].count("SPY") == 1
    assert _ttl(bus, handlers.gamma_pub_key("SPY")) == -1
    assert _ttl(bus, handlers.gamma_pub_history_key("SPY", "GEX")) == -1
    for view in ("Charm", "DEX", "Vanna"):
        key = handlers.gamma_pub_history_key("SPY", view)
        assert bus.cache_get(key).payload["symbol"] == "SPY"
        assert _ttl(bus, key) > 0


def test_spy_without_a_lease_publishes_exactly_what_it_did(bus, builds):
    _tick(bus)
    for view in ("Charm", "DEX", "Vanna"):
        assert bus.cache_get(handlers.gamma_pub_history_key("SPY", view)) is None


# ── the collect ─────────────────────────────────────────────────────────────

def test_the_collect_captures_the_hot_symbols_chains(bus, monkeypatch):
    seen = {}
    monkeypatch.setattr(handlers.compute, "collect_gex_snapshots",
                        lambda capture_symbols=None, **kw: seen.update(
                            capture=capture_symbols) or 0)
    for name in ("publish_flow_skew", "run_flow_alerts", "publish_matrix",
                 "publish_net_premium"):
        monkeypatch.setattr(handlers, name, lambda _bus: None)
    _grant(bus, "NVDA", "AAPL")
    handlers.collect_gex_history(bus)
    assert {"NVDA", "AAPL", "AMD"} | set(handlers.PUBLISHED_GAMMA_SYMBOLS) \
        == seen["capture"]
    assert gamma_public.tick_symbols() == ("NVDA", "AAPL")


def test_the_dropdown_list_is_published_for_the_public_page(monkeypatch):
    reset_fake_bus()
    b = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "gamma_symbol_options",
                        lambda: ["$SPX", "SPY", "NVDA"])
    handlers.publish_gamma_symbols(b)
    assert b.cache_get(pg.SYMBOLS_KEY).payload == {"symbols": ["$SPX", "SPY", "NVDA"]}
    assert b.cache_get(handlers.CACHE_GAMMA_SYMBOLS).payload == \
        {"symbols": ["$SPX", "SPY", "NVDA"]}
