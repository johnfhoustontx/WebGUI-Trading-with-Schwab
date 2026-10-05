"""Publishing the Market read: one reading per clock slot, from the views the
other services have already published, to ``cache:market:read``.

Design: docs/plans/2026-10-05-market-read-scorecard-design.md.
"""
import copy
import datetime as dt
import inspect
import time
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.market_svc import handlers, market_read, scheduler
from shared import market_read_config
from shared.bus import Bus
from shared.contracts.market import MarketRead

CT = ZoneInfo("America/Chicago")
MON = "2026-10-05"
T, H, N, X = market_read.TAILWIND, market_read.HEADWIND, market_read.NEUTRAL, market_read.NONE


def _at(hh, mm, ss=0, day=MON):
    y, m, d = (int(x) for x in day.split("-"))
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=CT)


def _tile(name, pct=None, last=None, state="flat"):
    return {"display": name, "change_pct": pct, "last": last, "color_state": state}


def _dashboard(spx=0.69, ndx=0.79):
    return {"categories": [
        {"category": "Cash Index", "tiles": [_tile("SPX", spx, 7776.0), _tile("NDX", ndx, 31050.0)]},
        {"category": "Volatility", "tiles": [_tile("VIX", 1.57, 15.55), _tile("VIX1D", -21.0, 8.49),
                                             _tile("VIX3M", 0.2, 18.04)]},
        {"category": "Sector SPDR", "tiles": [_tile(f"X{i}", 1.0, 10.0, "risk_on_mild")
                                              for i in range(10)]},
        {"category": "Fixed Income / Credit ETF",
         "tiles": [_tile("TLT", -0.9, 76.8, "risk_on_mild"), _tile("HYG", -0.1, 76.8, "flat")]},
        {"category": "Currency", "tiles": [_tile("$DXY", 0.45, 29.0, "risk_off_mild")]}]}


def _matrix():
    def row(sym, spot, flip, ceiling):
        return {"symbol": sym, "spot": spot, "flip": flip, "call_wall": ceiling,
                "net_gex": 1e9, "gex_regime": "above"}
    return {"rows": [row("SPY", 774.77, 770.41, 775.0), row("QQQ", 755.23, 750.96, 756.0)]}


def _flow(date=MON, n=12, public=True):
    contracts = {f"c{i}": {"poll": {"bought": 700.0, "sold": 300.0, "unlabelled": 0.0},
                           "stream": None, "volume": 1000.0,
                           "side": "call", "osi": f"OSI{i}"} for i in range(n)}
    return {"date": date, "public": public, "contracts": contracts}


def _status(age=40.0):
    return {"status_label": "Live", "age_seconds": age, "last_scan": "12:44:20 PM"}


@pytest.fixture
def bus():
    b = Bus()
    b.cache_set(scheduler.READ_INPUTS["dashboard"], _dashboard())
    b.cache_set(scheduler.READ_INPUTS["matrix"], _matrix())
    b.cache_set(scheduler.READ_INPUTS["gex_status"], _status())
    b.cache_set(scheduler.READ_INPUTS["sides"], _flow())
    return b


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    cfg = copy.deepcopy(market_read_config.DEFAULTS)
    monkeypatch.setattr(market_read_config, "load", lambda: cfg)
    return cfg


def _view(bus):
    env = bus.cache_get(handlers.CACHE_READ)
    return env.payload if env else None


def test_a_due_slot_publishes_one_reading(bus):
    state = {}
    assert scheduler.refresh_read(bus, state, now=_at(12, 46)) == "12:45"
    view = _view(bus)
    assert (view["date"], view["slot"], view["next_slot"]) == (MON, "12:45", "13:00")
    assert [r["key"] for r in view["rows"]] == list(market_read.ROW_KEYS)
    assert [r["verdict"] for r in view["rows"]] == [T, T, H, H, T, N]
    assert view["public"] is True and view["final"] is False
    MarketRead(**view)                      # the published payload fits its contract


def test_the_same_slot_is_not_published_twice(bus):
    state = {}
    scheduler.refresh_read(bus, state, now=_at(12, 46))
    ver = bus.cache_version(handlers.CACHE_READ)
    assert scheduler.refresh_read(bus, state, now=_at(12, 50)) is None
    assert scheduler.refresh_read(bus, state, now=_at(12, 59, 58)) is None
    assert bus.cache_version(handlers.CACHE_READ) == ver


def test_the_next_slot_carries_what_changed(bus):
    state = {}
    scheduler.refresh_read(bus, state, now=_at(12, 46))
    bus.cache_set(scheduler.READ_INPUTS["dashboard"], _dashboard(spx=0.10, ndx=0.12))
    assert scheduler.refresh_read(bus, state, now=_at(13, 0, 3)) == "13:00"
    view = _view(bus)
    d = view["rows"][0]
    assert d["verdict"] == N and d["prev"]["verdict"] == T
    assert d["prev"]["facts"] == {"spx_pct": 0.69, "ndx_pct": 0.79}
    assert [h["slot"] for h in view["history"]] == ["12:45", "13:00"]


def test_a_restart_does_not_republish_the_slot_and_keeps_the_history(bus):
    scheduler.refresh_read(bus, {}, now=_at(12, 46))
    ver = bus.cache_version(handlers.CACHE_READ)
    state = {}                              # the service restarts
    assert scheduler.refresh_read(bus, state, now=_at(12, 52)) is None
    assert bus.cache_version(handlers.CACHE_READ) == ver
    assert scheduler.refresh_read(bus, state, now=_at(13, 1)) == "13:00"
    view = _view(bus)
    assert [h["slot"] for h in view["history"]] == ["12:45", "13:00"]
    assert view["rows"][0]["prev"]["verdict"] == T


def test_yesterdays_reading_is_not_todays_previous(bus):
    scheduler.refresh_read(bus, {}, now=_at(15, 0, day="2026-10-02"))
    state = {}
    assert scheduler.refresh_read(bus, state, now=_at(8, 45)) == "08:45"
    view = _view(bus)
    assert [h["slot"] for h in view["history"]] == ["08:45"]
    assert all(r["prev"] is None for r in view["rows"])


def test_nothing_is_published_outside_the_session(bus):
    for now in (_at(8, 30), _at(15, 20), _at(12, 45, day="2026-10-03")):
        assert scheduler.refresh_read(bus, {}, now=now) is None
    assert _view(bus) is None


def test_the_close_reading_is_final(bus):
    scheduler.refresh_read(bus, {}, now=_at(15, 0, 4))
    view = _view(bus)
    assert view["slot"] == "15:00" and view["final"] is True and view["next_slot"] is None


def test_empty_sources_publish_no_reading_on_every_row_never_neutral():
    """Driven from the PRODUCER: a consumer-side guard proves nothing on its
    own. With no source published at all, every row must come out as ``none``."""
    bus = Bus()
    assert scheduler.refresh_read(bus, {}, now=_at(12, 46)) == "12:45"
    view = _view(bus)
    assert [r["verdict"] for r in view["rows"]] == [X] * 6
    assert view["tally"] == {"tailwind": 0, "headwind": 0, "neutral": 0, "none": 6}


def test_a_dashboard_with_no_tiles_publishes_no_reading_for_its_rows(bus):
    bus.cache_set(scheduler.READ_INPUTS["dashboard"], {"categories": []})
    scheduler.refresh_read(bus, {}, now=_at(12, 46))
    got = {r["key"]: r["verdict"] for r in _view(bus)["rows"]}
    assert (got["direction"], got["breadth"], got["volatility"], got["cross_asset"]) \
        == (X, X, X, X)
    assert got["structure"] == H and got["flow"] == T      # their own sources are fine


def _verdicts(bus):
    return {r["key"]: r["verdict"] for r in _view(bus)["rows"]}


def test_every_stale_source_is_treated_as_absent(bus, _cfg):
    # An hour past the moment the sources were written.
    scheduler.refresh_read(bus, {}, now=_at(12, 46), wall=time.time() + 3600)
    assert list(_verdicts(bus).values()) == [X] * 6
    # The flow view too. options_svc rewrites it every minute once a contract
    # is flagged, and an unchanged write still refreshes its stamp, so an old
    # one means the tally has stopped moving, not that the tape is quiet.
    assert _view(bus)["rows"][4]["facts"]["contracts"] is None


def test_the_flow_view_from_another_day_is_treated_as_absent(bus):
    bus.cache_set(scheduler.READ_INPUTS["sides"], _flow(date="2026-10-02"))
    scheduler.refresh_read(bus, {}, now=_at(12, 46))
    assert _verdicts(bus)["flow"] == X


def test_the_two_stale_limits_are_read_from_config(bus, _cfg):
    _cfg["stale_after_sec"] = 7200
    scheduler.refresh_read(bus, {}, now=_at(12, 46), wall=time.time() + 3600)
    got = _verdicts(bus)
    assert got["flow"] == T                 # judged by the general limit
    assert got["direction"] == X            # the dashboard has a limit of its own
    _cfg["dashboard_stale_after_sec"] = 7200
    scheduler.refresh_read(bus, {}, now=_at(13, 1), wall=time.time() + 3600)
    assert _verdicts(bus)["direction"] == T


def test_the_dashboard_is_held_to_a_shorter_limit_than_the_rest(bus):
    """Its tiles are republished every three seconds: two minutes old means the
    poll has stopped, long before the general five-minute limit."""
    scheduler.refresh_read(bus, {}, now=_at(12, 46), wall=time.time() + 120)
    got = _verdicts(bus)
    assert (got["direction"], got["breadth"], got["volatility"], got["cross_asset"]) \
        == (X, X, X, X)
    assert got["flow"] == T


# --- structure follows the collector, not the matrix ---------------------------

def test_a_stalled_collector_costs_structure_its_reading(bus):
    """The matrix is republished every minute whether or not the collector
    ran, so the matrix being fresh proves nothing about the levels in it."""
    bus.cache_set(scheduler.READ_INPUTS["gex_status"], _status(age=1900.0))
    scheduler.refresh_read(bus, {}, now=_at(12, 46))
    row = _view(bus)["rows"][2]
    assert row["verdict"] == X and row["facts"]["stale"] is True


@pytest.mark.parametrize("status", [None, {"status_label": "Collector status unknown",
                                           "age_seconds": None}])
def test_an_unknown_collector_age_costs_structure_its_reading(status):
    bus = Bus()                 # not the fixture: the status may be absent
    bus.cache_set(scheduler.READ_INPUTS["dashboard"], _dashboard())
    bus.cache_set(scheduler.READ_INPUTS["matrix"], _matrix())
    if status is not None:
        bus.cache_set(scheduler.READ_INPUTS["gex_status"], status)
    scheduler.refresh_read(bus, {}, now=_at(12, 46))
    got = _verdicts(bus)
    assert got["structure"] == X and got["direction"] == T


def test_the_collectors_age_includes_how_old_the_status_itself_is(bus):
    """``age_seconds`` was true when the status was written. A status
    publisher that stops must not leave the collector looking fresh."""
    bus.cache_set(scheduler.READ_INPUTS["gex_status"], _status(age=100.0))
    scheduler.refresh_read(bus, {}, now=_at(12, 46), wall=time.time() + 40)
    assert _verdicts(bus)["structure"] == H            # 140 s: still current
    scheduler.refresh_read(bus, {}, now=_at(13, 1), wall=time.time() + 55)
    assert _verdicts(bus)["structure"] == X            # 155 s: past the 150 limit


@pytest.mark.parametrize("enabled", [False, "true", None])
def test_disabled_publishes_nothing(bus, _cfg, enabled):
    _cfg["enabled"] = enabled
    assert scheduler.refresh_read(bus, {}, now=_at(12, 46)) is None
    assert _view(bus) is None


# --- the two switches apply between slots ---------------------------------------

def test_switching_it_off_takes_the_reading_down_without_waiting_for_a_slot(bus, _cfg):
    state = {}
    scheduler.refresh_read(bus, state, now=_at(12, 46))
    _cfg["enabled"] = False
    assert scheduler.refresh_read(bus, state, now=_at(12, 47)) is None
    view = _view(bus)
    assert view["enabled"] is False and view["rows"] == [] and view["public"] is False
    assert view["history"] == [] and view["slot"] == ""
    MarketRead(**view)
    # ...once, not on every poll.
    ver = bus.cache_version(handlers.CACHE_READ)
    scheduler.refresh_read(bus, state, now=_at(12, 47, 3))
    scheduler.refresh_read(bus, {}, now=_at(12, 47, 6))      # nor after a restart
    assert bus.cache_version(handlers.CACHE_READ) == ver


def test_a_retraction_that_fails_to_publish_is_still_owed(bus, _cfg, monkeypatch):
    state = {}
    scheduler.refresh_read(bus, state, now=_at(12, 46))
    _cfg["enabled"] = False
    real = handlers.publish_read
    monkeypatch.setattr(_degrade, "degraded", lambda area, **k: None)
    monkeypatch.setattr(handlers, "publish_read",
                        lambda *a: (_ for _ in ()).throw(ConnectionError("redis")))
    scheduler.refresh_read(bus, state, now=_at(12, 47))
    assert _view(bus)["enabled"] is True                 # still up: the write failed
    monkeypatch.setattr(handlers, "publish_read", real)
    scheduler.refresh_read(bus, state, now=_at(12, 47, 3))   # inside the pause
    assert _view(bus)["enabled"] is True
    scheduler.refresh_read(bus, state, now=_at(12, 47, 31))
    assert _view(bus)["enabled"] is False


def test_switching_it_back_on_takes_a_reading_at_once(bus, _cfg):
    state = {}
    scheduler.refresh_read(bus, state, now=_at(12, 46))
    _cfg["enabled"] = False
    scheduler.refresh_read(bus, state, now=_at(12, 47))
    _cfg["enabled"] = True
    assert scheduler.refresh_read(bus, state, now=_at(12, 48)) == "12:45"
    view = _view(bus)
    assert view["enabled"] is True and len(view["rows"]) == 6
    assert all(r["prev"] is None for r in view["rows"])


def test_a_changed_public_switch_republishes_the_reading_between_slots(bus, _cfg):
    state = {}
    scheduler.refresh_read(bus, state, now=_at(12, 46))
    before = _view(bus)
    _cfg["public"] = False
    assert scheduler.refresh_read(bus, state, now=_at(12, 47)) is None
    after = _view(bus)
    assert after["public"] is False
    assert {**after, "public": True} == before          # the reading itself is untouched
    ver = bus.cache_version(handlers.CACHE_READ)
    scheduler.refresh_read(bus, state, now=_at(12, 47, 3))
    assert bus.cache_version(handlers.CACHE_READ) == ver
    _cfg["public"] = True
    scheduler.refresh_read(bus, state, now=_at(12, 48))
    assert _view(bus) == before


def test_the_public_switch_reaches_yesterdays_reading_before_the_open(bus, _cfg):
    """It is still on the Desk, greyed, until the first slot replaces it."""
    scheduler.refresh_read(bus, {}, now=_at(15, 0, day="2026-10-02"))
    _cfg["public"] = False
    scheduler.refresh_read(bus, {}, now=_at(7, 0))
    view = _view(bus)
    assert view["public"] is False and view["date"] == "2026-10-02"


def test_the_flow_rows_public_flag_follows_the_estimates_own_switch(bus):
    bus.cache_set(scheduler.READ_INPUTS["sides"], _flow(public=False))
    scheduler.refresh_read(bus, {}, now=_at(12, 46))
    view = _view(bus)
    assert view["public"] is True                       # the reading is public...
    assert view["rows"][4]["key"] == "flow" and view["rows"][4]["public"] is False
    assert all("public" not in r for i, r in enumerate(view["rows"]) if i != 4)


# --- slots never go backwards ----------------------------------------------------

def test_a_changed_interval_never_publishes_an_earlier_slot(bus, _cfg):
    state = {}
    scheduler.refresh_read(bus, state, now=_at(12, 46))
    _cfg["interval_min"] = 30
    assert scheduler.refresh_read(bus, state, now=_at(12, 50)) is None
    assert scheduler.refresh_read(bus, state, now=_at(13, 0, 2)) == "13:00"
    assert [h["slot"] for h in _view(bus)["history"]] == ["12:45", "13:00"]


def test_a_naive_clock_is_central_not_the_machines_own_zone(bus):
    assert scheduler.refresh_read(bus, {}, now=dt.datetime(2026, 10, 5, 12, 46)) == "12:45"


@pytest.mark.parametrize("public,want", [(True, True), (False, False), ("true", False)])
def test_the_public_flag_follows_config_and_fails_closed(bus, _cfg, public, want):
    _cfg["public"] = public
    scheduler.refresh_read(bus, {}, now=_at(12, 46))
    assert _view(bus)["public"] is want


def test_the_interval_is_read_from_config(bus, _cfg):
    _cfg["interval_min"] = 30
    assert scheduler.refresh_read(bus, {}, now=_at(12, 46)) == "12:30"
    view = _view(bus)
    assert (view["interval_min"], view["next_slot"]) == (30, "13:00")


def test_a_failing_build_leaves_the_last_reading_and_counts_a_degrade(bus, monkeypatch):
    state = {}
    scheduler.refresh_read(bus, state, now=_at(12, 46))
    seen, real_build = [], market_read.build
    monkeypatch.setattr(_degrade, "degraded", lambda area, **k: seen.append(area))
    monkeypatch.setattr(market_read, "build",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert scheduler.refresh_read(bus, state, now=_at(13, 1)) is None
    assert seen == ["market.read"]
    assert _view(bus)["slot"] == "12:45"
    # The slot is still owed: the next tick tries again.
    monkeypatch.setattr(market_read, "build", real_build)
    assert scheduler.refresh_read(bus, state, now=_at(13, 2)) == "13:00"


def test_a_failing_build_is_retried_after_a_pause_not_on_every_poll(bus, monkeypatch, _cfg):
    """The poll is every three seconds. A build that fails must not log a
    traceback and count a degrade twenty times a minute."""
    state, seen = {}, []
    monkeypatch.setattr(_degrade, "degraded", lambda area, **k: seen.append(area))
    monkeypatch.setattr(market_read, "build",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    for sec in (1, 4, 7, 28):
        assert scheduler.refresh_read(bus, state, now=_at(13, 0, sec)) is None
    assert seen == ["market.read"]
    scheduler.refresh_read(bus, state, now=_at(13, 0, 32))       # past retry_sec = 30
    assert seen == ["market.read"] * 2
    # ...and the pause is the configured one.
    _cfg["retry_sec"] = 5
    scheduler.refresh_read(bus, state, now=_at(13, 1, 3))
    assert seen == ["market.read"] * 3
    scheduler.refresh_read(bus, state, now=_at(13, 1, 6))
    assert seen == ["market.read"] * 3
    scheduler.refresh_read(bus, state, now=_at(13, 1, 9))
    assert seen == ["market.read"] * 4


def test_the_read_is_published_whole_every_time():
    """It carries its own ``ts``, so ``skip_unchanged`` could never skip it,
    and a view that did skip would keep a stale stamp."""
    assert "skip_unchanged=True" not in inspect.getsource(handlers.publish_read)


def test_the_loop_refreshes_the_read_after_the_dashboard():
    src = inspect.getsource(scheduler.loop)
    assert "refresh_read" in src
    assert src.index("handlers.publish, bus, payload") < src.index("refresh_read")
    # The hand-off to the executor is guarded like the two beside it.
    assert "except Exception" in src[src.index("refresh_read"):src.index("refresh_summary")]
