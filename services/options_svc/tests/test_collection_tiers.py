"""Which symbols get a real chain fetch every minute, and which every third."""
import datetime as dt
import sys
import types
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.options_svc import compute
from shared import marketdata_config as mdc

UNIVERSE = ["$SPX", "SPY", "XLK", "NVDA", "AAPL", "SOFI", "UBER", "HOOD"]
BASE = ["$SPX", "SPY", "XLK", "NVDA", "AAPL"]


@pytest.fixture
def cfg(monkeypatch):
    def set_(mode="on", chains=True, interval=3):
        monkeypatch.setattr(mdc, "mode", lambda: mode)
        monkeypatch.setattr(mdc, "store_on", lambda name: chains)
        monkeypatch.setattr(mdc, "section", lambda name: {
            "tail_interval_min": interval, "fresh_max_age_sec": 20})
    return set_


def tiers(**kw):
    return compute.collection_tiers(UNIVERSE, base=BASE, **kw)


#############################################
# THE PLAN'S SPECIFICATION
#############################################

def test_watchlist_only_symbols_are_the_tail(cfg):
    cfg()
    t = tiers()
    assert t == {"tail": frozenset({"SOFI", "UBER", "HOOD"}),
                 "interval_min": 3, "fresh_max_age_sec": 20}


def test_the_viewed_symbol_and_the_public_hot_symbols_stay_on_one_minute(cfg):
    cfg()
    assert tiers(capture={"SOFI"})["tail"] == frozenset({"UBER", "HOOD"})


def test_hedging_flow_symbols_stay_on_one_minute(cfg):
    cfg()
    assert tiers(hiro={"HOOD"})["tail"] == frozenset({"SOFI", "UBER"})


@pytest.mark.parametrize("kw", [dict(mode="shadow"), dict(mode="off"),
                                dict(chains=False), dict(interval=1), dict(interval=0)])
def test_no_tiers_unless_the_store_is_on_and_the_interval_is_above_one(cfg, kw):
    cfg(**kw)
    assert tiers() is None


def test_no_tiers_when_nothing_is_watchlist_only(cfg):
    cfg()
    assert compute.collection_tiers(BASE, base=BASE) is None


def test_unreadable_config_means_no_tiers(monkeypatch):
    monkeypatch.setattr(mdc, "mode", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert tiers() is None


#############################################
# collection_tiers — the rest of its edges
#############################################

def test_the_store_is_asked_about_chains_and_the_section_about_collection(monkeypatch):
    asked = []
    monkeypatch.setattr(mdc, "mode", lambda: "on")
    monkeypatch.setattr(mdc, "store_on", lambda name: asked.append(("store", name)) or True)
    monkeypatch.setattr(mdc, "section", lambda name: asked.append(("section", name)) or {
        "tail_interval_min": 3, "fresh_max_age_sec": 20})
    assert tiers() is not None
    assert asked == [("store", "chains"), ("section", "collection")]


def test_the_interval_and_the_fresh_limit_come_from_the_settings(monkeypatch):
    monkeypatch.setattr(mdc, "mode", lambda: "on")
    monkeypatch.setattr(mdc, "store_on", lambda name: True)
    monkeypatch.setattr(mdc, "section", lambda name: {
        "tail_interval_min": 5.0, "fresh_max_age_sec": 12.0})
    t = tiers()
    assert (t["interval_min"], t["fresh_max_age_sec"]) == (5, 12)
    assert type(t["interval_min"]) is int and type(t["fresh_max_age_sec"]) is int


def test_capture_and_hiro_add_up_and_take_any_iterable(cfg):
    cfg()
    t = compute.collection_tiers(UNIVERSE, base=tuple(BASE), capture=["SOFI"],
                                 hiro=("UBER", "NOT-POLLED"))
    assert t["tail"] == frozenset({"HOOD"})
    assert compute.collection_tiers(UNIVERSE, base=BASE, capture=None, hiro=None)[
        "tail"] == frozenset({"SOFI", "UBER", "HOOD"})
    assert compute.collection_tiers(UNIVERSE, base=BASE, capture=set(), hiro=set())[
        "tail"] == frozenset({"SOFI", "UBER", "HOOD"})


def test_a_tail_emptied_by_the_capture_set_is_no_tiers(cfg):
    cfg()
    assert tiers(capture={"SOFI", "UBER"}, hiro={"HOOD"}) is None


def test_the_tail_is_only_ever_symbols_being_polled(cfg):
    """During the early extended-hours stretch only a subset is polled."""
    cfg()
    t = compute.collection_tiers(["SPY", "SOFI"], base=BASE)
    assert t["tail"] == frozenset({"SOFI"})


@pytest.mark.parametrize("section", [
    {},                                                    # keys missing
    {"tail_interval_min": "three", "fresh_max_age_sec": 20},
    {"tail_interval_min": 3, "fresh_max_age_sec": None},
    None])
def test_settings_that_cannot_be_read_leave_a_trace_and_no_tiers(monkeypatch, section):
    monkeypatch.setattr(mdc, "mode", lambda: "on")
    monkeypatch.setattr(mdc, "store_on", lambda name: True)
    monkeypatch.setattr(mdc, "section", lambda name: section)
    _degrade.reset()
    assert tiers() is None
    assert _degrade.counts() == {"options.collection_tiers": 1}
    _degrade.reset()


def test_a_universe_that_cannot_be_read_leaves_a_trace_and_no_tiers(cfg):
    cfg()
    _degrade.reset()
    assert compute.collection_tiers(None, base=BASE) is None
    assert compute.collection_tiers(UNIVERSE, base=5) is None
    assert _degrade.counts() == {"options.collection_tiers": 2}
    _degrade.reset()


def test_the_ordinary_off_answers_are_not_degrades(cfg):
    _degrade.reset()
    for kw in (dict(mode="shadow"), dict(chains=False), dict(interval=1)):
        cfg(**kw)
        assert tiers() is None
    cfg()
    assert compute.collection_tiers(BASE, base=BASE) is None
    assert _degrade.counts() == {}


def test_the_shipped_settings_mean_no_tiers():
    """config/marketdata.toml ships with the interval at 1: the feature is off
    until the operator turns it on."""
    assert mdc.section("collection")["tail_interval_min"] == 1
    assert tiers() is None


#############################################
# collect_gex_snapshots hands them to the collector
#############################################

CT = ZoneInfo("America/Chicago")
RTH = dt.datetime(2026, 8, 17, 10, 0, tzinfo=CT)           # a plain Monday
POLLED = ["$SPX", "SPY", "NVDA", "TSLA", "AAPL", "HOOD"]


def _collector(monkeypatch, *, strict=False, hiro_cfg=None):
    """The lazily-imported collector modules, faked. ``rec["kw"]`` is every
    keyword ``poll_once`` was called with. ``strict`` gives the stand-in the
    signature the collector had BEFORE tiers existed, so an unexpected keyword
    is a TypeError."""
    from services.options_svc import flow_alerts
    from services.options_svc import scheduler as _sched

    rec = {"poll_n": 0, "kw": None}

    class _Conn:
        def close(self):
            pass

    if strict:
        def _poll(client, engine, conn, lock=None, symbols=None, on_chain=None):
            rec["poll_n"] += 1
            rec["kw"] = {"symbols": symbols, "on_chain": on_chain}
    else:
        def _poll(client, engine, conn, **kw):
            rec["poll_n"] += 1
            rec["kw"] = kw

    fake_gc = types.SimpleNamespace(
        LOCK_PATH="LOCK", SYMBOLS=["$SPX", "SPY"],
        collection_symbols=lambda: list(POLLED),
        acquire_collector_lock=lambda path, **kw: True,
        touch_lock=lambda path, **kw: None,
        ensure_file_logging=lambda *a, **k: None,
        poll_once=_poll,
        log=types.SimpleNamespace(info=lambda *a, **k: None,
                                  debug=lambda *a, **k: None),
    )
    fake_gh = types.SimpleNamespace(connect=lambda: _Conn(),
                                    init_schema=lambda conn: None,
                                    purge_keep_sessions=lambda conn, **kw: 0,
                                    purge_hiro=lambda conn, **kw: 0)
    monkeypatch.setitem(sys.modules, "gex_collector", fake_gc)
    monkeypatch.setitem(sys.modules, "gex_history_db", fake_gh)
    monkeypatch.setitem(sys.modules, "gamma_tool",
                        types.SimpleNamespace(GammaEngine=lambda: "ENGINE"))
    monkeypatch.setattr(compute, "_LAST_PURGE_DATE", None)
    monkeypatch.setattr(compute, "_GEX_SCHEMA_READY", False)
    monkeypatch.setattr(compute, "_publish_eth_eligibility", lambda seen: None)
    monkeypatch.setattr(_sched, "_market_now", lambda: RTH)
    monkeypatch.setattr(flow_alerts, "load_thresholds",
                        lambda: {} if hiro_cfg is None else {"hiro": hiro_cfg})
    return rec


def test_with_the_shipped_settings_poll_once_gets_no_tiers_argument(monkeypatch):
    """The stand-ins for poll_once elsewhere in this suite predate the argument,
    and so does anything else that calls the collector the old way."""
    rec = _collector(monkeypatch, strict=True)
    assert compute.collect_gex_snapshots(now=RTH) == len(POLLED)
    assert rec["poll_n"] == 1

    rec = _collector(monkeypatch)
    compute.collect_gex_snapshots(now=RTH)
    assert set(rec["kw"]) == {"symbols", "on_chain"}


def test_the_tail_is_the_polled_symbols_less_base_capture_and_hedging_flow(
        monkeypatch, cfg):
    cfg()
    rec = _collector(monkeypatch, hiro_cfg={"enabled": True, "symbols": ["NVDA"]})
    compute.collect_gex_snapshots(capture_symbols={"TSLA"}, now=RTH)
    assert rec["kw"]["tiers"] == {"tail": frozenset({"AAPL", "HOOD"}),
                                  "interval_min": 3, "fresh_max_age_sec": 20}
    assert rec["kw"]["symbols"] is None            # the universe itself is unchanged


@pytest.mark.parametrize("hiro_cfg", [
    None,                                          # no [hiro] table at all
    {},                                            # an empty one
    {"enabled": True, "symbols": []},
    {"enabled": True},                             # no symbols key
    5,                                             # a scalar override of the table
    {"enabled": True, "symbols": 5},               # a scalar symbols list
])
def test_an_empty_or_broken_hedging_flow_config_still_gives_the_right_tail(
        monkeypatch, cfg, hiro_cfg):
    cfg()
    rec = _collector(monkeypatch, hiro_cfg=hiro_cfg)
    if hiro_cfg is None:
        from services.options_svc import flow_alerts
        monkeypatch.setattr(flow_alerts, "load_thresholds", lambda: {})
    compute.collect_gex_snapshots(capture_symbols={"TSLA"}, now=RTH)
    assert rec["kw"]["tiers"]["tail"] == frozenset({"NVDA", "AAPL", "HOOD"})


def test_configured_hedging_flow_symbols_stay_core_while_the_detector_is_off(
        monkeypatch, cfg):
    """The conservative reading: a symbol named in [hiro] keeps its one-minute
    fetch whether or not rows are being measured this minute (the detector is
    off, or the session has not opened). Never fewer fresh chains than HIRO
    could want the moment it is switched on."""
    cfg()
    rec = _collector(monkeypatch, hiro_cfg={"enabled": False, "symbols": ["NVDA"]})
    compute.collect_gex_snapshots(now=RTH)
    assert rec["kw"]["tiers"]["tail"] == frozenset({"TSLA", "AAPL", "HOOD"})


def test_no_capture_set_is_an_empty_one(monkeypatch, cfg):
    cfg()
    rec = _collector(monkeypatch)
    compute.collect_gex_snapshots(now=RTH)
    assert rec["kw"]["tiers"]["tail"] == frozenset({"NVDA", "TSLA", "AAPL", "HOOD"})


def test_a_narrowed_poll_takes_its_tail_from_the_narrowed_list(monkeypatch, cfg):
    cfg()
    rec = _collector(monkeypatch)
    monkeypatch.setattr(compute, "_gth_symbols", lambda now=None: ["SPY", "TSLA"])
    assert compute.collect_gex_snapshots(now=RTH) == 2
    assert rec["kw"]["symbols"] == ["SPY", "TSLA"]
    assert rec["kw"]["tiers"]["tail"] == frozenset({"TSLA"})


def test_tiers_that_cannot_be_decided_never_stop_the_poll(monkeypatch):
    monkeypatch.setattr(mdc, "mode", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    rec = _collector(monkeypatch, strict=True)
    assert compute.collect_gex_snapshots(now=RTH) == len(POLLED)
    assert rec["poll_n"] == 1
