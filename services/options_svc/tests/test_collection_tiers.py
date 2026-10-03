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


def no_tail(interval):
    """The tiers while the store is on and nothing may be carried: every chain
    request still carries the fresh-age limit."""
    return {"tail": frozenset(), "interval_min": interval, "fresh_max_age_sec": 20}


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
                                dict(chains=False),
                                dict(mode="shadow", interval=1),
                                dict(chains=False, interval=1)])
def test_no_tiers_unless_the_store_is_on(cfg, kw):
    cfg(**kw)
    assert tiers() is None


@pytest.mark.parametrize("interval", [1, 0, -2])
def test_the_store_on_at_an_interval_of_one_is_an_empty_tail_not_no_tiers(cfg, interval):
    """With no tiers the collector sends NO age limit, and the proxy's own
    applies: 45 s in session, 1,800 s while every session is closed. Collection
    runs 08:00-15:20 CT around an 08:30-15:00 session, so the polls at
    08:26-08:29 and 15:16-15:19 were each answered with the 08:25 / 15:15
    chain: four repeated rows per symbol, twice a day."""
    cfg(interval=interval)
    assert tiers() == no_tail(1)


def test_nothing_watchlist_only_is_an_empty_tail(cfg):
    cfg()
    assert compute.collection_tiers(BASE, base=BASE) == no_tail(3)


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


def test_a_tail_emptied_by_the_capture_set_is_an_empty_tail(cfg):
    cfg()
    assert tiers(capture={"SOFI", "UBER"}, hiro={"HOOD"}) == no_tail(3)


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
    for kw in (dict(mode="shadow"), dict(chains=False)):
        cfg(**kw)
        assert tiers() is None
    cfg(interval=1)
    assert tiers() == no_tail(1)
    cfg()
    assert compute.collection_tiers(BASE, base=BASE) == no_tail(3)
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


def _collector(monkeypatch, *, strict=False, hiro_cfg=None, flow_cfg=None):
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
    cfg = dict(flow_cfg or {})
    if hiro_cfg is not None:
        cfg["hiro"] = hiro_cfg
    monkeypatch.setattr(flow_alerts, "load_thresholds", lambda: cfg)
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


#############################################
# THE INTERVAL HAS A CEILING
#############################################
# The Opportunity Board's flow acceleration reads a 15-minute window. An
# interval above 5 leaves it too few real fetches to mean anything.

@pytest.fixture
def warned(monkeypatch, caplog):
    import logging
    monkeypatch.setattr(compute, "_TIER_WARNED", set())

    def lines():
        return [r.getMessage() for r in caplog.records
                if r.name == compute.log.name and r.levelno == logging.WARNING]
    with caplog.at_level(logging.WARNING, logger=compute.log.name):
        yield lines


@pytest.mark.parametrize("configured", [6, 7, 10, 60])
def test_an_interval_above_five_is_five(cfg, warned, configured):
    cfg(interval=configured)
    assert tiers()["interval_min"] == compute.MAX_TAIL_INTERVAL_MIN == 5
    assert len(warned()) == 1
    assert "tail_interval_min" in warned()[0]
    assert str(configured) in warned()[0] and "5" in warned()[0]


def test_a_clamped_interval_is_said_once_not_once_a_minute(cfg, warned):
    cfg(interval=9)
    tiers()
    tiers()
    assert len(warned()) == 1
    cfg(interval=12)
    tiers()
    assert len(warned()) == 2


@pytest.mark.parametrize("configured", [2, 3, 4, 5])
def test_an_interval_up_to_five_is_used_as_it_is(cfg, warned, configured):
    cfg(interval=configured)
    assert tiers()["interval_min"] == configured
    assert warned() == []


#############################################
# THE GAMMA-FLIP ALERT NEVER FIRES FROM MODELLED GAMMA
#############################################
# A carried row's flip level comes from carried gammas. The symbols the alert
# watches therefore keep a real fetch every minute.

def test_flip_alert_symbols_stay_on_one_minute(cfg):
    cfg()
    assert tiers(flip={"HOOD"})["tail"] == frozenset({"SOFI", "UBER"})
    assert tiers(flip=["HOOD", "NOT-POLLED"])["tail"] == frozenset({"SOFI", "UBER"})


@pytest.mark.parametrize("everything", [set(), [], (), frozenset()])
def test_a_flip_alert_that_watches_every_symbol_leaves_no_tail(cfg, everything):
    """An empty list is the alert's way of saying "the whole universe"."""
    cfg()
    assert tiers(flip=everything) == no_tail(3)


def test_no_flip_alert_at_all_changes_nothing(cfg):
    cfg()
    assert tiers(flip=None) == tiers()


def test_a_whole_universe_flip_alert_is_not_a_degrade(cfg):
    cfg()
    _degrade.reset()
    assert tiers(flip=[]) == no_tail(3)
    assert _degrade.counts() == {}


FLIP_ON = {"enabled": True, "band_pct": 0.0015, "cooldown_min": 60}


def _flip_tiers(monkeypatch, cfg, flow_cfg, **kw):
    cfg()
    rec = _collector(monkeypatch, flow_cfg=flow_cfg, **kw)
    compute.collect_gex_snapshots(now=RTH)
    return rec["kw"].get("tiers")


def test_the_symbols_listed_for_the_flip_alert_are_core(monkeypatch, cfg):
    got = _flip_tiers(monkeypatch, cfg,
                      {"gamma_flip": dict(FLIP_ON, symbols=["HOOD", "TSLA"])})
    assert got["tail"] == frozenset({"NVDA", "AAPL"})


def test_the_shipped_flip_list_is_already_core(monkeypatch, cfg):
    """No [gamma_flip] table at all reads as the built-in list ($SPX, SPY, QQQ,
    IWM), every one of them in the collection base."""
    got = _flip_tiers(monkeypatch, cfg, {})
    assert got["tail"] == frozenset({"NVDA", "TSLA", "AAPL", "HOOD"})


@pytest.mark.parametrize("symbols", [[], None, ()])
def test_an_empty_flip_list_with_the_alert_on_means_no_tail(monkeypatch, cfg, symbols):
    """handlers._run_gamma_flip reads ``symbols or the whole flow universe``."""
    assert _flip_tiers(monkeypatch, cfg,
                       {"gamma_flip": dict(FLIP_ON, symbols=symbols)}) == no_tail(3)


def test_no_flip_symbols_key_with_the_alert_on_means_no_tail(monkeypatch, cfg):
    assert _flip_tiers(monkeypatch, cfg, {"gamma_flip": dict(FLIP_ON)}) == no_tail(3)


@pytest.mark.parametrize("flow_cfg", [
    {"gamma_flip": {"enabled": False, "symbols": []}},          # the alert is off
    {"gamma_flip": {"enabled": 0, "symbols": []}},              # falsy, as handlers read it
    {"enabled": False, "gamma_flip": dict(FLIP_ON, symbols=[])},   # every flow alert is off
])
def test_an_empty_flip_list_with_the_alert_off_keeps_the_tail(monkeypatch, cfg, flow_cfg):
    got = _flip_tiers(monkeypatch, cfg, flow_cfg)
    assert got["tail"] == frozenset({"NVDA", "TSLA", "AAPL", "HOOD"})


def test_a_hand_typed_enabled_string_is_on_as_the_alert_itself_reads_it(monkeypatch, cfg):
    """``handlers`` tests ``not gf.get("enabled", True)``, so "false" is ON
    there. The tiers must agree with the detector, not with good sense."""
    assert _flip_tiers(monkeypatch, cfg,
                       {"gamma_flip": {"enabled": "false", "symbols": []}}) == no_tail(3)


def test_listed_flip_symbols_stay_core_while_the_alert_is_off(monkeypatch, cfg):
    """The conservative reading, as for the hedging-flow symbols: never fewer
    real fetches than the alert could want the moment it is switched on."""
    got = _flip_tiers(monkeypatch, cfg,
                      {"gamma_flip": {"enabled": False, "symbols": ["HOOD"]}})
    assert got["tail"] == frozenset({"NVDA", "TSLA", "AAPL"})


@pytest.mark.parametrize("gamma_flip", [5, "off", {"enabled": True, "symbols": 5},
                                        {"enabled": True, "symbols": [5, None]}])
def test_a_broken_flip_config_never_stops_the_poll_and_never_opens_a_tail_by_accident(
        monkeypatch, cfg, gamma_flip):
    cfg()
    rec = _collector(monkeypatch, flow_cfg={"gamma_flip": gamma_flip})
    assert compute.collect_gex_snapshots(now=RTH) == len(POLLED)
    assert rec["poll_n"] == 1
    got = rec["kw"].get("tiers")
    # A scalar table reads as the built-in list (flow_alerts.section); a list
    # that names no usable symbol reads as "everything".
    if isinstance(gamma_flip, dict):
        assert got == no_tail(3)
    else:
        assert got["tail"] == frozenset({"NVDA", "TSLA", "AAPL", "HOOD"})


def test_a_bare_string_flip_symbol_is_one_symbol(monkeypatch, cfg):
    got = _flip_tiers(monkeypatch, cfg, {"gamma_flip": dict(FLIP_ON, symbols="HOOD")})
    assert got["tail"] == frozenset({"NVDA", "TSLA", "AAPL"})


def test_a_flip_table_with_no_switch_is_on(monkeypatch, cfg):
    """``gf.get("enabled", True)``: a table that does not say is running."""
    assert _flip_tiers(monkeypatch, cfg, {"gamma_flip": {"symbols": []}}) == no_tail(3)


def test_a_flip_reading_that_fails_means_every_fetch_is_real(monkeypatch, cfg):
    """Unknown is read as "every symbol": the poll runs, with no tail, and the
    failure leaves a trace."""
    cfg()
    rec = _collector(monkeypatch)

    def boom(flow_cfg):
        raise RuntimeError("bad flip config")

    monkeypatch.setattr(compute, "_flip_alert_symbols", boom)
    _degrade.reset()
    assert compute.collect_gex_snapshots(now=RTH) == len(POLLED)
    assert rec["poll_n"] == 1
    assert rec["kw"]["tiers"] == no_tail(3)        # the limit is sent; no tail
    assert _degrade.counts() == {"options.flip_tier_setup": 1}
    _degrade.reset()


#############################################
# THE CARRY'S TWO LIMITS RIDE WITH THE TIERS
#############################################

def test_the_gamma_cap_and_the_slack_are_handed_to_the_collector(monkeypatch):
    monkeypatch.setattr(mdc, "mode", lambda: "on")
    monkeypatch.setattr(mdc, "store_on", lambda name: True)
    monkeypatch.setattr(mdc, "section", lambda name: {
        "tail_interval_min": 3, "fresh_max_age_sec": 20,
        "max_gamma_ratio": 4.0, "carry_slack_sec": 15})
    assert tiers() == {"tail": frozenset({"SOFI", "UBER", "HOOD"}),
                       "interval_min": 3, "fresh_max_age_sec": 20,
                       "max_gamma_ratio": 4.0, "carry_slack_sec": 15}


def test_with_the_real_settings_the_tiers_carry_the_shipped_limits(monkeypatch):
    """Only the mode, the store switch and the interval are turned on here; the
    rest is config/marketdata.toml as shipped."""
    real = mdc.section
    monkeypatch.setattr(mdc, "mode", lambda: "on")
    monkeypatch.setattr(mdc, "store_on", lambda name: True)
    monkeypatch.setattr(mdc, "section",
                        lambda name: dict(real(name), tail_interval_min=3))
    t = tiers()
    assert (t["max_gamma_ratio"], t["carry_slack_sec"]) == (10.0, 30)
    assert t["fresh_max_age_sec"] == 20


#############################################
# THE FRESH LIMIT IS SENT WHENEVER THE STORE IS ON
#############################################

def test_the_store_on_at_interval_one_hands_the_collector_an_empty_tail(monkeypatch, cfg):
    cfg(interval=1)
    rec = _collector(monkeypatch)
    compute.collect_gex_snapshots(capture_symbols={"TSLA"}, now=RTH)
    assert rec["kw"]["tiers"] == no_tail(1)


@pytest.mark.parametrize("kw", [dict(mode="shadow"), dict(mode="off"),
                                dict(chains=False), dict(mode="shadow", interval=1)])
def test_without_the_store_poll_once_gets_no_tiers_argument_at_all(monkeypatch, cfg, kw):
    """Shadow, off or chain reuse off: the request the collector has always made."""
    cfg(**kw)
    rec = _collector(monkeypatch, strict=True)     # a tiers keyword is a TypeError
    assert compute.collect_gex_snapshots(now=RTH) == len(POLLED)
    assert rec["poll_n"] == 1


def test_an_empty_tail_keeps_the_carrys_two_limits_too(monkeypatch):
    monkeypatch.setattr(mdc, "mode", lambda: "on")
    monkeypatch.setattr(mdc, "store_on", lambda name: True)
    monkeypatch.setattr(mdc, "section", lambda name: {
        "tail_interval_min": 1, "fresh_max_age_sec": 20,
        "max_gamma_ratio": 4.0, "carry_slack_sec": 15})
    assert tiers() == dict(no_tail(1), max_gamma_ratio=4.0, carry_slack_sec=15)


def test_an_interval_above_five_with_no_tail_is_still_clamped(cfg, warned):
    cfg(interval=9)
    assert tiers(flip=[]) == no_tail(5)
    assert len(warned()) == 1


class _Chains:
    """A stand-in proxy client for the REAL collector."""
    class Options:
        class ContractType:
            ALL = "ALL"

    def __init__(self):
        self.kwargs, self.quote_calls = {}, 0

    def get_option_chain(self, symbol, **kw):
        self.kwargs[symbol] = kw
        return types.SimpleNamespace(
            status_code=200, store_age=None,
            json=lambda: {"symbol": symbol, "underlyingPrice": 100.0,
                          "callExpDateMap": {}, "putExpDateMap": {}})

    def get_quotes(self, symbols, **kw):
        self.quote_calls += 1
        return types.SimpleNamespace(status_code=200, json=lambda: {})


def _real_poll(tiers_, now):
    """One poll of the real ``gex_collector.poll_once`` with these tiers."""
    import gex_collector as gc
    from unittest.mock import MagicMock
    client, seen = _Chains(), []
    engine = MagicMock()
    engine._last_dte = 0
    engine.calc_all_from_chain.return_value = (None, None, None, None)
    kw = {"tiers": tiers_} if tiers_ else {}
    gc.poll_once(client, engine, MagicMock(), symbols=list(UNIVERSE), poll_term=False,
                 now=now, on_chain=lambda sym, chain: seen.append(sym), **kw)
    return client, seen


SESSION_CLOSED = [dt.datetime(2026, 8, 17, 8, 27, tzinfo=CT),      # before the open
                  dt.datetime(2026, 8, 17, 15, 17, tzinfo=CT)]     # after the close


@pytest.mark.parametrize("now", [RTH, *SESSION_CLOSED])
@pytest.mark.parametrize("kw", [dict(interval=1), dict(interval=3)])
def test_end_to_end_the_store_on_sends_the_fresh_limit_for_every_symbol(cfg, now, kw):
    """The decision here handed to the real collector: at interval 1, and at
    interval 3 with the flip alert watching every symbol, every chain request
    carries the fresh-age limit and nothing is carried."""
    cfg(**kw)
    decided = tiers(flip=[]) if kw["interval"] == 3 else tiers()
    assert decided["tail"] == frozenset()
    client, seen = _real_poll(decided, now)
    assert {s: k.get("max_age") for s, k in client.kwargs.items()} == {
        s: 20 for s in UNIVERSE}
    assert seen == list(UNIVERSE)                  # every chain reached the detectors


@pytest.mark.parametrize("kw", [dict(mode="shadow"), dict(mode="off"), dict(chains=False)])
def test_end_to_end_in_shadow_the_request_is_exactly_todays(cfg, kw):
    cfg(**kw)
    assert tiers() is None
    client, _seen = _real_poll(tiers(), RTH)
    today = RTH.date()
    assert client.kwargs == {s: {"contract_type": "ALL", "from_date": today,
                                 "to_date": today + dt.timedelta(days=7)}
                             for s in UNIVERSE}
    assert client.quote_calls == 0
