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


def _flow(date=MON, n=12):
    alerts = [{"type": "uoa", "id": f"c{i}", "side": "call", "osi": f"OSI{i}"} for i in range(n)]
    contracts = {a["id"]: {"poll": {"bought": 700.0, "sold": 300.0, "unlabelled": 0.0},
                           "stream": None, "volume": 1000.0} for a in alerts}
    return ({"date": date, "public": True, "contracts": contracts},
            {"date": date, "alerts": alerts})


@pytest.fixture
def bus():
    b = Bus()
    sides, alerts = _flow()
    b.cache_set(scheduler.READ_INPUTS["dashboard"], _dashboard())
    b.cache_set(scheduler.READ_INPUTS["matrix"], _matrix())
    b.cache_set(scheduler.READ_INPUTS["sides"], sides)
    b.cache_set(scheduler.READ_INPUTS["alerts"], alerts)
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


def test_a_stale_dashboard_and_matrix_are_treated_as_absent(bus, _cfg):
    # An hour past the moment the sources were written.
    scheduler.refresh_read(bus, {}, now=_at(12, 46), wall=time.time() + 3600)
    got = {r["key"]: r["verdict"] for r in _view(bus)["rows"]}
    assert (got["direction"], got["structure"]) == (X, X)
    # The flow views are published only when something changes, so a quiet tape
    # leaves them legitimately old: they are judged by their DATE, not their age.
    assert got["flow"] == T


def test_flow_views_from_another_day_are_treated_as_absent(bus):
    sides, alerts = _flow(date="2026-10-02")
    bus.cache_set(scheduler.READ_INPUTS["sides"], sides)
    bus.cache_set(scheduler.READ_INPUTS["alerts"], alerts)
    scheduler.refresh_read(bus, {}, now=_at(12, 46))
    assert {r["key"]: r["verdict"] for r in _view(bus)["rows"]}["flow"] == X


def test_the_stale_limit_is_read_from_config(bus, _cfg):
    _cfg["stale_after_sec"] = 7200
    scheduler.refresh_read(bus, {}, now=_at(12, 46), wall=time.time() + 3600)
    assert {r["key"]: r["verdict"] for r in _view(bus)["rows"]}["direction"] == T


@pytest.mark.parametrize("enabled", [False, "true", None])
def test_disabled_publishes_nothing(bus, _cfg, enabled):
    _cfg["enabled"] = enabled
    assert scheduler.refresh_read(bus, {}, now=_at(12, 46)) is None
    assert _view(bus) is None


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


def test_the_loop_refreshes_the_read_after_the_dashboard():
    src = inspect.getsource(scheduler.loop)
    assert "refresh_read" in src
    assert src.index("handlers.publish, bus, payload") < src.index("refresh_read")
