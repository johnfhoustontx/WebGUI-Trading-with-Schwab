"""The per-symbol Gamma snapshots the PUBLIC live screens read.

``cache:options:gamma`` is one symbol-agnostic key holding whatever the private
app last looked at — and ``refresh_gamma_current`` reads the symbol back out of
it, so the slot is sticky and driven by the private page. Four of the published
screens are views of ``/options/gamma`` ($SPX/GEX, Net Prem, and Premium
Divergence on SPY and on QQQ), so without a per-symbol key the "$SPX" screen
renders whatever symbol the private app last selected and the SPY/QQQ screens
render $SPX.

These tests drive the PRODUCER — ``refresh_gamma`` / ``refresh_gamma_current``
with a stubbed snapshot — and then read the keys back off the bus, because a
consumer-side assertion over a hand-written payload proves nothing about what
the service actually writes (this repo has two documented incidents of exactly
that).
"""
import pytest

from services.options_svc import handlers
from shared.bus import Bus


def _snap(symbol, *, views=("GEX", "Charm", "DEX", "Vanna")):
    """A snapshot shaped like ``compute.gamma_snapshot``'s, with history rows."""
    return {
        "symbol": symbol, "spot": 100.0, "dte": 0,
        "views": {v: {"data": {"spot": 100.0, "gex": {}, "strike_count": 0},
                      "summary": {}, "walls": [], "flip": None,
                      "history": [[1, 100.0, None, None, None, 0, {}]]}
                  for v in views},
        "term": {},
    }


def _by_symbol(monkeypatch, *, failing=()):
    """Stub ``gamma_snapshot`` so each symbol yields ITS OWN snapshot."""
    calls = []

    def _fake(symbol, chain=None):
        calls.append(symbol)
        if symbol in failing:
            return None
        return _snap(symbol)

    monkeypatch.setattr(handlers.compute, "gamma_snapshot", _fake)
    return calls


def _pub(bus, symbol):
    env = bus.cache_get(handlers.gamma_pub_key(symbol))
    return env.payload if env else None


# --- the symbol list -------------------------------------------------------

def test_the_published_symbols_are_the_three_the_screens_name():
    assert handlers.PUBLISHED_GAMMA_SYMBOLS == ("$SPX", "SPY", "QQQ")


def test_the_published_keys_are_additive_not_a_rekeying():
    """The private page's key must keep its exact name — re-keying it would
    change the app's behaviour for a public feature."""
    assert handlers.CACHE_GAMMA == "cache:options:gamma"
    assert handlers.gamma_history_key("GEX") == "cache:options:gamma_hist_gex"
    assert handlers.gamma_pub_key("SPY") == "cache:options:gamma_pub:SPY"
    assert handlers.gamma_pub_history_key("SPY", "GEX") == \
        "cache:options:gamma_pub_hist_SPY_gex"


# --- the thing the screens actually need -----------------------------------

def test_each_published_screen_gets_its_own_symbol(monkeypatch):
    """THE TEST THAT MATTERS. Drive one tick with the private page parked on a
    symbol that is published by NOBODY, and every published key must still hold
    its own symbol — not the private page's, and not each other's."""
    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_GAMMA, {"symbol": "AMD", "views": {}})
    _by_symbol(monkeypatch)

    handlers.refresh_gamma_current(bus)

    assert [(_pub(bus, s) or {}).get("symbol")
            for s in handlers.PUBLISHED_GAMMA_SYMBOLS] == ["$SPX", "SPY", "QQQ"]
    # ...and the private key still holds what the private page was looking at.
    assert bus.cache_get(handlers.CACHE_GAMMA).payload["symbol"] == "AMD"


def test_the_private_key_is_untouched_by_the_published_publish(monkeypatch):
    """A published refresh must never move the symbol under the private page."""
    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_GAMMA, {"symbol": "AMD", "views": {}})
    _by_symbol(monkeypatch)

    handlers.refresh_gamma_current(bus)

    assert bus.cache_get(handlers.CACHE_GAMMA).payload["symbol"] == "AMD"
    for view in handlers.GAMMA_HISTORY_VIEWS:
        env = bus.cache_get(handlers.gamma_history_key(view))
        assert env.payload["symbol"] == "AMD"


def test_a_symbol_is_computed_once_even_when_it_is_both(monkeypatch):
    """$SPX is the private page's default AND a published screen. Computing it
    twice would cost a second chain fetch — the stash is consume-once."""
    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_GAMMA, {"symbol": "$SPX", "views": {}})
    calls = _by_symbol(monkeypatch)

    handlers.refresh_gamma_current(bus)

    assert sorted(calls) == ["$SPX", "QQQ", "SPY"]
    assert _pub(bus, "$SPX")["symbol"] == "$SPX"
    assert bus.cache_get(handlers.CACHE_GAMMA).payload["symbol"] == "$SPX"


def test_the_case_of_the_cached_symbol_does_not_double_the_work(monkeypatch):
    """A cached 'spy' and the published 'SPY' are one symbol, not two."""
    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_GAMMA, {"symbol": "spy", "views": {}})
    calls = _by_symbol(monkeypatch)

    handlers.refresh_gamma_current(bus)

    assert len(calls) == 3


# --- degrading ------------------------------------------------------------

def test_a_symbol_with_no_chain_degrades_to_empty_never_to_another_symbol(monkeypatch):
    bus = Bus(fake=True)
    _by_symbol(monkeypatch, failing=("QQQ",))

    handlers.refresh_gamma_current(bus)

    assert _pub(bus, "QQQ") == {"symbol": "QQQ", "spot": None, "dte": None,
                                "views": {}, "term": {}}
    assert _pub(bus, "SPY")["symbol"] == "SPY"


def test_one_symbol_raising_does_not_skip_the_others(monkeypatch):
    bus = Bus(fake=True)

    def _fake(symbol, chain=None):
        if symbol == "SPY":
            raise RuntimeError("proxy down")
        return _snap(symbol)

    monkeypatch.setattr(handlers.compute, "gamma_snapshot", _fake)

    handlers.refresh_gamma_current(bus)      # must not raise

    assert _pub(bus, "QQQ")["symbol"] == "QQQ"
    assert _pub(bus, "SPY") is None


def test_an_unpublished_symbol_writes_no_published_key(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "gamma_snapshot",
                        lambda s, chain=None: _snap(s))

    handlers.refresh_gamma(bus, "AMD")

    assert bus.cache_get(handlers.gamma_pub_key("AMD")) is None
    assert bus.cache_get(handlers.CACHE_GAMMA).payload["symbol"] == "AMD"


def test_a_command_refresh_of_a_published_symbol_keeps_its_screen_fresh(monkeypatch):
    """``refresh_gamma`` is the ``gamma_refresh`` command handler AND the startup
    seed. When its symbol is a published one the snapshot is already computed, so
    writing the published key costs nothing and seeds it on a cold Redis."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "gamma_snapshot",
                        lambda s, chain=None: _snap(s))

    handlers.refresh_gamma(bus, "$SPX")

    assert _pub(bus, "$SPX")["symbol"] == "$SPX"


# --- history: ordering, stamping, emptying --------------------------------

def test_every_published_history_key_carries_its_own_symbol(monkeypatch):
    bus = Bus(fake=True)
    _by_symbol(monkeypatch)

    handlers.refresh_gamma_current(bus)

    for symbol in handlers.PUBLISHED_GAMMA_SYMBOLS:
        for view in handlers.GAMMA_HISTORY_VIEWS:
            env = bus.cache_get(handlers.gamma_pub_history_key(symbol, view))
            assert env is not None, f"{symbol}/{view} history never published"
            assert env.payload["symbol"] == symbol
            assert env.payload["rows"], "the rows were dropped on the way out"


def test_published_history_lands_before_the_payload_that_points_at_it(monkeypatch):
    """The page reacts to the MAIN key's version bump and THEN reads history, so
    history-already-written is the only skew it can observe."""
    bus = Bus(fake=True)
    writes = []
    real = bus.cache_set
    monkeypatch.setattr(bus, "cache_set",
                        lambda key, payload, **kw: (writes.append(key),
                                                    real(key, payload, **kw))[1])
    _by_symbol(monkeypatch)

    handlers.refresh_gamma_current(bus)

    for symbol in handlers.PUBLISHED_GAMMA_SYMBOLS:
        main = writes.index(handlers.gamma_pub_key(symbol))
        for view in handlers.GAMMA_HISTORY_VIEWS:
            assert writes.index(handlers.gamma_pub_history_key(symbol, view)) < main


def test_a_view_the_snapshot_lacks_is_published_empty_not_skipped(monkeypatch):
    """Leaving the previous run's rows in the key would let the page pair them
    with a snapshot that no longer carries that view."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "gamma_snapshot",
                        lambda s, chain=None: _snap(s, views=("GEX",)))

    handlers.refresh_gamma(bus, "SPY")

    env = bus.cache_get(handlers.gamma_pub_history_key("SPY", "Vanna"))
    assert env is not None and env.payload == {
        "symbol": "SPY", "view": "Vanna", "rows": []}


def test_the_published_payload_carries_no_inline_history(monkeypatch):
    """The 2026-08-20 split exists because four inline history blobs made the
    payload 4.99 MB. Three more symbols must not put them back."""
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "gamma_snapshot",
                        lambda s, chain=None: _snap(s))

    handlers.refresh_gamma(bus, "SPY")

    for entry in _pub(bus, "SPY")["views"].values():
        assert "history" not in entry


# --- the API cost ---------------------------------------------------------

def test_the_collector_captures_every_published_symbols_chain(monkeypatch):
    """The whole point: the collector already fetches $SPX/SPY/QQQ every minute
    (config/symbols.toml [collection] base), so the tick's published refreshes
    must reuse those chains. A capture set missing one costs ~440 extra Schwab
    /chains calls a day for it."""
    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_GAMMA, {"symbol": "AMD", "views": {}})
    seen = {}
    monkeypatch.setattr(handlers.compute, "collect_gex_snapshots",
                        lambda capture_symbols=None, **kw: seen.update(
                            capture=capture_symbols) or 0)
    for name in ("publish_flow_skew", "run_flow_alerts", "publish_matrix",
                 "publish_net_premium"):
        monkeypatch.setattr(handlers, name, lambda _bus: None)

    handlers.collect_gex_history(bus)

    assert set(handlers.PUBLISHED_GAMMA_SYMBOLS) <= seen["capture"]
    assert "AMD" in seen["capture"]


def test_the_tick_refresh_never_fetches_a_chain_it_was_handed(monkeypatch):
    """End-to-end over the real stash: chains stashed by the collector must be
    consumed by the tick's refreshes, one apiece, with nothing left to fetch."""
    from services.options_svc import compute

    compute.reset_tick_chain()
    for symbol in ("AMD",) + handlers.PUBLISHED_GAMMA_SYMBOLS:
        compute._stash_tick_chain(symbol, {"chain_for": symbol})

    bus = Bus(fake=True)
    bus.cache_set(handlers.CACHE_GAMMA, {"symbol": "AMD", "views": {}})
    handed = []

    def _fake(symbol, chain=None):
        handed.append((symbol, compute._take_tick_chain(symbol)))
        return _snap(symbol)

    monkeypatch.setattr(handlers.compute, "gamma_snapshot", _fake)

    handlers.refresh_gamma_current(bus)

    assert handed == [("AMD", {"chain_for": "AMD"}),
                      ("$SPX", {"chain_for": "$SPX"}),
                      ("SPY", {"chain_for": "SPY"}),
                      ("QQQ", {"chain_for": "QQQ"})]


@pytest.mark.parametrize("symbol", ["$SPX", "SPY", "QQQ", "AMD"])
def test_the_stash_holds_every_captured_symbol_at_once(symbol):
    """It held exactly ONE before this task; three published symbols plus the
    private page's own is four in flight per tick."""
    from services.options_svc import compute

    compute.reset_tick_chain()
    for s in ("$SPX", "SPY", "QQQ", "AMD"):
        compute._stash_tick_chain(s, {"for": s})
    assert compute._take_tick_chain(symbol) == {"for": symbol}
    assert compute._take_tick_chain(symbol) is None      # still consume-once
