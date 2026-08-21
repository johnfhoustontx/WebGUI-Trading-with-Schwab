"""Tier-2 merge for the Bull / Bear Map: the nightly momentum cascade plus one
batched live ``/quotes`` call -> ``cache:sentiment:bullbear``.

Every quote fixture below uses the FLATTENED shape
``SchwabProxyClient.get_quotes`` returns (``schwab-proxy/proxy_client.py:284``):
``{symbol: {"last", "change", "change_pct", "high", "low", "volume"}}``. There is
no ``{"quote": {...}}`` envelope — a fixture inventing one would leave every
row's ``day_pct`` None while this suite stayed green.
"""
import asyncio
import copy
import datetime as _dt

import pytest

from services import _proxy
from services.sentiment_svc import compute, handlers, scheduler
from shared.bus import Bus
from shared.contracts.envelope import Command


def test_bullbear_symbols_covers_all_three_levels_deduped():
    """An industry ETF is often a scored stock too; ask the quote call once."""
    levels = {"sector": [{"symbol": "XLV"}], "industry": [{"symbol": "XBI"}],
              "stock": [{"symbol": "AMGN"}, {"symbol": "XBI"}]}
    assert compute.bullbear_symbols(levels) == ["XLV", "XBI", "AMGN"]


def test_bullbear_symbols_skips_rows_with_no_usable_symbol():
    levels = {"sector": [{"symbol": ""}, {"symbol": None}, {}, None,
                         {"symbol": "XLV"}]}
    assert compute.bullbear_symbols(levels) == ["XLV"]


def test_merge_live_attaches_the_day_move_at_every_level():
    """0.0 must survive as 0.0: a genuinely flat day is a reading, not a gap."""
    levels = {"sector": [{"symbol": "XLV"}], "industry": [{"symbol": "XBI"}],
              "stock": [{"symbol": "AMGN"}]}
    quotes = {"XLV": {"change_pct": 1.25}, "XBI": {"change_pct": -0.5},
              "AMGN": {"change_pct": 0.0}}
    merged = compute.merge_live(levels, quotes)
    assert [merged[k][0]["day_pct"] for k in compute.BULLBEAR_LEVELS] == \
        [1.25, -0.5, 0.0]


def test_merge_live_leaves_day_pct_none_rather_than_zero_without_a_quote():
    """A dash, not "unchanged" — a different claim. Live, this is a symbol the
    proxy omitted from its reply; the key-present-but-empty case guards a
    different client, since ``_extract_change_pct`` always returns a float for a
    symbol ``get_quotes`` does return."""
    levels = {"sector": [{"symbol": "XLV"}]}
    assert compute.merge_live(levels, {})["sector"][0]["day_pct"] is None
    assert compute.merge_live(levels, {"XLV": {}})["sector"][0]["day_pct"] is None


def test_merge_live_reads_the_shape_get_quotes_actually_returns():
    """Captured off the running proxy on 2026-08-20. Named for the bug it
    guards: an earlier draft read ``["quote"]["netPercentChange"]``, a shape
    this producer never emits, which would have killed the whole live column."""
    real = {"XLV": {"last": 174.7, "change": -0.98, "change_pct": -0.55783242,
                    "high": 175.19, "low": 173.63, "volume": 4546574}}
    merged = compute.merge_live({"sector": [{"symbol": "XLV"}]}, real)
    assert merged["sector"][0]["day_pct"] == -0.55783242


def test_merge_live_leaves_the_cached_momentum_payload_untouched():
    """The momentum tree is shared with /sentiment/momentum. Deep-compared, so a
    write into a nested ``raw``/``components`` dict is caught as well as a
    top-level one."""
    levels = {"sector": [{"symbol": "XLV", "raw": {"trend": 0.4}}],
              "industry": [{"symbol": "XBI", "raw": {}}]}
    before = copy.deepcopy(levels)
    compute.merge_live(levels, {"XLV": {"change_pct": 1.0}})
    assert levels == before


class _RecordingClient:
    """Stand-in for ``services._proxy.schwab_client``; records every ask."""

    def __init__(self, result=None):
        self.asked = []
        self.result = result or {}

    def get_quotes(self, symbols):
        self.asked.append(list(symbols))
        return self.result


def test_bullbear_quotes_forwards_the_symbol_list_to_the_shared_proxy_client(
        monkeypatch):
    """The one test pinning the wiring to the real client — everything below
    stubs ``_bullbear_quotes`` out."""
    client = _RecordingClient({"XLV": {"change_pct": 1.0}})
    monkeypatch.setattr(_proxy, "schwab_client", client)
    assert compute._bullbear_quotes(["XLV", "AMGN"]) == {"XLV": {"change_pct": 1.0}}
    assert client.asked == [["XLV", "AMGN"]]


def test_bullbear_quotes_does_not_ask_the_proxy_for_an_empty_symbol_list(
        monkeypatch):
    """A cold cascade has no symbols, and ``/quotes?symbols=`` is a wasted call."""
    client = _RecordingClient()
    monkeypatch.setattr(_proxy, "schwab_client", client)
    assert compute._bullbear_quotes([]) == {}
    assert client.asked == []


def test_bullbear_view_carries_the_nightly_tree_and_the_live_moves(monkeypatch):
    monkeypatch.setattr(compute, "_bullbear_quotes",
                        lambda s: {"XLV": {"change_pct": 2.0}})
    nightly = {"session_date": "2026-08-19",
               "computed_at": "2026-08-19T16:20:00-05:00",
               "regime": {"state": "risk_on"},
               "levels": {"sector": [{"symbol": "XLV"}]}}
    view = compute.bullbear_view(nightly)
    assert view["session_date"] == "2026-08-19"
    assert view["computed_at"] == nightly["computed_at"]
    assert view["regime"] == {"state": "risk_on"}
    assert view["levels"]["sector"][0]["day_pct"] == 2.0


def test_bullbear_view_stamps_quoted_at_now_and_offset_aware(monkeypatch):
    """Two clocks on purpose: ``computed_at`` dates last night's SCORES,
    ``quoted_at`` the day-moves taken just now. Stamped exactly the way
    ``compute_momentum`` stamps ``computed_at`` — the page renders the pair side
    by side, so one formatter has to read both."""
    monkeypatch.setattr(compute, "_bullbear_quotes", lambda s: {})
    view = compute.bullbear_view({"computed_at": "2026-08-19T16:20:00-05:00"})
    stamped = _dt.datetime.fromisoformat(view["quoted_at"])
    assert stamped.tzinfo is not None
    assert abs((_dt.datetime.now().astimezone() - stamped).total_seconds()) < 60


def test_bullbear_view_asks_for_quotes_once_for_every_distinct_symbol(monkeypatch):
    """Measured 374 symbols returning in a SINGLE call; a per-row fetch would be
    374 proxy round-trips every 30 s."""
    calls = []
    monkeypatch.setattr(compute, "_bullbear_quotes",
                        lambda s: calls.append(list(s)) or {})
    compute.bullbear_view({"levels": {
        "sector": [{"symbol": "XLV"}],
        "stock": [{"symbol": "XLV"}, {"symbol": "AMGN"}]}})
    assert calls == [["XLV", "AMGN"]]


def test_bullbear_view_degrades_to_the_nightly_tree_when_the_quote_call_fails(
        monkeypatch):
    """A dead proxy costs the day-move column and nothing else. Raising instead
    would publish no view at all and lose a perfectly good nightly tree."""
    def _boom(symbols):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(compute, "_bullbear_quotes", _boom)
    view = compute.bullbear_view({"session_date": "2026-08-19",
                                  "levels": {"sector": [{"symbol": "XLV"}]}})
    assert view["quoted_at"] is None           # the tell: moves absent, not flat
    assert view["session_date"] == "2026-08-19"
    assert view["levels"]["sector"][0]["day_pct"] is None


def test_bullbear_view_on_a_cold_momentum_cache_yields_an_empty_tree(monkeypatch):
    """The map's 30 s poll starts before the first nightly cascade on a fresh
    install. All three levels must be present and empty — the page indexes them
    by name."""
    monkeypatch.setattr(compute, "_bullbear_quotes", lambda s: {})
    view = compute.bullbear_view(None)
    assert view["session_date"] is None
    assert view["levels"] == {"sector": [], "industry": [], "stock": []}


def test_bullbear_view_does_not_swallow_a_malformed_momentum_tree(monkeypatch):
    """The degrade wraps the QUOTE CALL only. A shape drift in the cascade's own
    payload is a real bug, and hiding it behind an all-None day-move column is
    the exact failure mode this feature already nearly shipped once.

    The property is "does not swallow", so the type is deliberately unpinned — a
    guard refactor that changes AttributeError to ValueError is not a regression
    of anything this test is about."""
    monkeypatch.setattr(compute, "_bullbear_quotes", lambda s: {})
    with pytest.raises(Exception):
        compute.bullbear_view({"levels": {"sector": ["XLV"]}})


# --- publish + schedule (handlers / scheduler) --------------------------------

def _bus_with_momentum(levels=None, **extra):
    bus = Bus(fake=True)
    payload = {"session_date": "2026-08-19", "regime": {"state": "risk_on"},
               "levels": levels if levels is not None
               else {"sector": [{"symbol": "XLV"}]}}
    bus.cache_set(handlers.CACHE_MOMENTUM, dict(payload, **extra))
    return bus


def test_publish_bullbear_caches_the_view_and_publishes_its_version(monkeypatch):
    monkeypatch.setattr(handlers.compute, "_bullbear_quotes",
                        lambda s: {"XLV": {"change_pct": 1.5}})
    bus = _bus_with_momentum()
    sub = bus.subscribe(handlers.EVENT_BULLBEAR)
    handlers.publish_bullbear(bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()
    env = bus.cache_get(handlers.CACHE_BULLBEAR)
    assert env.payload["levels"]["sector"][0]["day_pct"] == 1.5
    assert msg is not None and msg.get("version") == env.version


def test_publish_bullbear_survives_a_cold_momentum_cache(monkeypatch):
    """The map's poll starts before the first nightly cascade on a fresh install.
    It must publish an empty tree, not raise into the scheduler."""
    monkeypatch.setattr(handlers.compute, "_bullbear_quotes", lambda s: {})
    bus = Bus(fake=True)
    handlers.publish_bullbear(bus)
    payload = bus.cache_get(handlers.CACHE_BULLBEAR).payload
    assert payload["levels"] == {"sector": [], "industry": [], "stock": []}


def test_publish_bullbear_does_not_bump_the_version_when_nothing_moved(monkeypatch):
    """``quoted_at`` moves on EVERY successful build, so a payload carrying a
    fresh one is never equal to the stored one and ``skip_unchanged`` could never
    fire — measured 1, 2, 3 over three static ticks. Off-hours that is the whole
    cost the flag exists to prevent: 374 frozen quotes waking every open tab."""
    monkeypatch.setattr(handlers.compute, "_bullbear_quotes",
                        lambda s: {"XLV": {"change_pct": 1.5}})
    bus = _bus_with_momentum()
    versions = [handlers.publish_bullbear(bus) or bus.cache_get(
        handlers.CACHE_BULLBEAR).version for _ in range(3)]
    assert versions == [versions[0]] * 3


def test_publish_bullbear_does_bump_when_a_day_move_actually_changes(monkeypatch):
    """The other half of the throttle: holding ``quoted_at`` must not freeze a
    view whose numbers really moved."""
    pct = [1.5]
    monkeypatch.setattr(handlers.compute, "_bullbear_quotes",
                        lambda s: {"XLV": {"change_pct": pct[0]}})
    bus = _bus_with_momentum()
    handlers.publish_bullbear(bus)
    first = bus.cache_get(handlers.CACHE_BULLBEAR).version
    pct[0] = 2.5
    handlers.publish_bullbear(bus)
    assert bus.cache_get(handlers.CACHE_BULLBEAR).version > first


_ANCIENT = "2000-01-01T00:00:00+00:00"


def test_publish_bullbear_keeps_quoted_at_moving_while_the_data_moves(monkeypatch):
    """Carried forward ONLY on a skip. On a real change ``quoted_at`` must be the
    fresh stamp, or it would read as a "last changed" clock that never changes.

    Checked against a planted sentinel rather than against a second wall-clock
    reading: ``datetime.now()`` is ~1 ms granular on Windows, so two back-to-back
    publishes can legitimately stamp the identical string and an ``a > b`` form
    of this test FLAKES (observed once in a full run, then 8/8 green alone)."""
    pct = [1.5]
    monkeypatch.setattr(handlers.compute, "_bullbear_quotes",
                        lambda s: {"XLV": {"change_pct": pct[0]}})
    bus = _bus_with_momentum()
    handlers.publish_bullbear(bus)
    stored = bus.cache_get(handlers.CACHE_BULLBEAR).payload
    bus.cache_set(handlers.CACHE_BULLBEAR, dict(stored, quoted_at=_ANCIENT))
    pct[0] = 2.5                                   # a REAL change, so no skip
    handlers.publish_bullbear(bus)
    assert bus.cache_get(handlers.CACHE_BULLBEAR).payload["quoted_at"] != _ANCIENT


def test_refresh_bullbear_command_is_dispatched(monkeypatch):
    bus = Bus(fake=True)
    calls = []
    monkeypatch.setattr(handlers, "publish_bullbear", lambda b: calls.append(b))
    handlers.handle_command(bus, Command(type="refresh_bullbear"))
    assert calls == [bus]


def test_bullbear_publishes_every_tick_through_every_open_session():
    """GTH (07:00) and curb (15:10) move the tape just as RTH does, and extended
    hours are live since 2026-08-17. A gate keyed on RTH alone would leave the
    Today column stale exactly when a reader is watching it most closely."""
    for hh, mm in ((7, 0), (10, 0), (15, 10)):
        now = _dt.datetime(2026, 8, 19, hh, mm, tzinfo=scheduler._CT)
        due, slot = scheduler.bullbear_due(now, None)
        assert due is True, f"{hh}:{mm:02d} not due"
        assert scheduler.bullbear_due(now, slot)[0] is True   # every tick, not once


def test_bullbear_throttles_only_once_the_tape_is_closed():
    night = _dt.datetime(2026, 8, 19, 22, 0, tzinfo=scheduler._CT)
    due, slot = scheduler.bullbear_due(night, None)
    assert due is True                            # the first closed tick still fires
    assert scheduler.bullbear_due(night, slot)[0] is False
    later = night + _dt.timedelta(minutes=scheduler.BULLBEAR_CLOSED_INTERVAL_MIN)
    assert scheduler.bullbear_due(later, slot)[0] is True


def test_bullbear_closed_cadence_stays_inside_the_status_board_threshold():
    """A frozen tape publishes byte-identical payloads that skip_unchanged drops,
    so a closed tick buys only ``{key}:ts`` freshness — and webgui/pages/status.py
    flags a scheduled view stale at _STALE_AFTER_SEC = 600. Reusing the composite
    refresh's 15 min here would sit past that; the number is duplicated rather
    than imported because a service must not import Tier-1."""
    assert scheduler.BULLBEAR_CLOSED_INTERVAL_MIN * 60 <= 300


# --- scheduler task lifecycle -------------------------------------------------
# Driven with a bare asyncio loop rather than pytest-asyncio, which is NOT in
# this venv. Both properties below are load-bearing and were previously unpinned:
# a surviving mutant moved the task creation back after start_consumer, and
# another dropped the cancel entirely (a task leak on teardown).

def _run_scheduler_briefly(monkeypatch, start_consumer=lambda bus: None):
    """Start ``scheduler.loop``, let it spawn its side tasks, then cancel it.

    Returns the ordered start/cancel events its two side loops recorded.
    """
    events = []

    def _side(name):
        async def _task(bus, loop_):
            events.append(f"{name}:started")
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                events.append(f"{name}:cancelled")
                raise
        return _task

    monkeypatch.setattr(handlers, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "refresh_rotation", lambda *a, **k: None)
    monkeypatch.setattr(scheduler.order_flow_consumer, "start_consumer", start_consumer)
    monkeypatch.setattr(scheduler.order_flow_consumer, "start_option_consumer",
                        lambda bus: None)
    monkeypatch.setattr(scheduler, "_bullbear_publish_loop", _side("bullbear"))
    monkeypatch.setattr(scheduler, "_order_flow_publish_loop", _side("orderflow"))

    async def _drive():
        task = asyncio.create_task(scheduler.loop(Bus(fake=True)))
        for _ in range(100):                      # bounded: the startup awaits are stubs
            await asyncio.sleep(0.01)
            if events:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for _ in range(100):                      # let the finally-block cancels land
            await asyncio.sleep(0.01)
            if any(e.endswith(":cancelled") for e in events):
                break
        # Snapshot from INSIDE the loop: asyncio.run cancels whatever is still
        # pending as it closes, so a list read after it returns would show the
        # task as cancelled even if loop()'s finally never touched it — which
        # silently made this a test of asyncio.run rather than of the scheduler.
        return list(events)

    return asyncio.run(_drive())


def test_scheduler_starts_the_bullbear_loop_before_the_order_flow_consumer(monkeypatch):
    """``start_consumer`` is the statement in that ``try`` that can realistically
    raise, and the map has nothing to do with order-flow — it must not lose its
    publish loop because a streaming consumer failed to start."""
    def _boom(bus):
        raise RuntimeError("stream refused")

    events = _run_scheduler_briefly(monkeypatch, start_consumer=_boom)
    assert "bullbear:started" in events
    assert "orderflow:started" not in events      # proves the raise really landed


def test_scheduler_cancels_the_bullbear_loop_on_shutdown(monkeypatch):
    """Without the cancel in the ``finally`` the task leaks on teardown, and
    nothing else in the suite would notice."""
    events = _run_scheduler_briefly(monkeypatch)
    assert "bullbear:cancelled" in events


# --- version-gated payload memos (2026-08-20) ---------------------------------

def _count_gets(bus):
    """Wrap cache_get so a test can count full-payload deserializes per key."""
    calls = {}
    real = bus.cache_get

    def _counting(key, *a, **k):
        calls[key] = calls.get(key, 0) + 1
        return real(key, *a, **k)

    bus.cache_get = _counting
    return calls


def test_publish_bullbear_reads_the_momentum_payload_once_per_version(monkeypatch):
    """The momentum view is a NIGHTLY cascade; this poll runs every ~30 s. Reading
    it deserialized 304 KB (134 KB of which is `rank_history` the builder never
    touches) ~2,880 times a day for data that changes once. Gate it on the cheap
    :ver probe, the same trick as market_svc._NETPREM_CACHE."""
    monkeypatch.setattr(handlers.compute, "_bullbear_quotes",
                        lambda s: {"XLV": {"change_pct": 1.5}})
    handlers.reset_bullbear_memos()
    bus = _bus_with_momentum()
    calls = _count_gets(bus)
    for _ in range(3):
        handlers.publish_bullbear(bus)
    assert calls.get(handlers.CACHE_MOMENTUM) == 1


def test_publish_bullbear_rereads_momentum_when_the_cascade_republishes(monkeypatch):
    """The memo must not outlive its version, or a nightly recompute would never
    reach the map."""
    monkeypatch.setattr(handlers.compute, "_bullbear_quotes",
                        lambda s: {"XLV": {"change_pct": 1.5}, "XLK": {"change_pct": 2.0}})
    handlers.reset_bullbear_memos()
    bus = _bus_with_momentum()
    handlers.publish_bullbear(bus)
    bus.cache_set(handlers.CACHE_MOMENTUM,
                  {"session_date": "2026-08-20", "regime": {"state": "risk_off"},
                   "levels": {"sector": [{"symbol": "XLK"}]}})
    handlers.publish_bullbear(bus)
    syms = [r["symbol"] for r in bus.cache_get(
        handlers.CACHE_BULLBEAR).payload["levels"]["sector"]]
    assert syms == ["XLK"]


def test_publish_bullbear_does_not_reread_its_own_output_for_the_stamp(monkeypatch):
    """The stamp compare re-read the 190 KB payload this function itself had just
    written. It is the only writer, so while the stored version still matches the
    memo that read is pure waste.

    ONE read of the key per tick remains and cannot be removed here: it happens
    inside ``cache_set(skip_unchanged=True)``, which must compare against what is
    actually stored before deciding to skip, and must refresh ``{key}:ts`` either
    way so the Status board cannot read a legitimately-static publisher as dead.
    So the tick goes from three full deserializes (304 KB + 190 KB + 190 KB) to
    one (190 KB).
    """
    monkeypatch.setattr(handlers.compute, "_bullbear_quotes",
                        lambda s: {"XLV": {"change_pct": 1.5}})
    handlers.reset_bullbear_memos()
    bus = _bus_with_momentum()
    handlers.publish_bullbear(bus)          # first write, memo cold
    calls = _count_gets(bus)
    for _ in range(3):
        handlers.publish_bullbear(bus)
    assert calls.get(handlers.CACHE_BULLBEAR) == 3      # cache_set's, not ours
    assert calls.get(handlers.CACHE_MOMENTUM) is None   # ours is gone entirely


def test_publish_bullbear_still_holds_the_stamp_through_the_memo(monkeypatch):
    """Regression guard: the throttle in
    test_publish_bullbear_does_not_bump_the_version_when_nothing_moved must keep
    working when the comparison payload comes from the memo rather than a read."""
    monkeypatch.setattr(handlers.compute, "_bullbear_quotes",
                        lambda s: {"XLV": {"change_pct": 1.5}})
    handlers.reset_bullbear_memos()
    bus = _bus_with_momentum()
    versions = []
    for _ in range(3):
        handlers.publish_bullbear(bus)
        versions.append(bus.cache_get(handlers.CACHE_BULLBEAR).version)
    assert versions == [versions[0]] * 3
