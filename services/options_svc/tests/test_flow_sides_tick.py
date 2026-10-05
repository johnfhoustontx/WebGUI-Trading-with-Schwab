"""flow_sides_tick: the session state around the pure tally -- what the poll
books, what a flagged contract's row carries, how yesterday's rows are resolved
from today's open interest, and what is published.

Design: docs/plans/2026-10-04-flow-alert-sides-design.md.
"""
import datetime as dt
import inspect
import logging
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.options_svc import flow_alerts
from services.options_svc import flow_sides_tick as tick
from shared.bus import Bus

import gex_history_db as gh

CT = ZoneInfo("America/Chicago")
FRI, MON = "2026-10-02", "2026-10-05"       # two consecutive trading days
OSI = "SPY   261009C00770000"               # expires the FOLLOWING Friday
OSI_0DTE = "SPY   261002C00770000"          # expires on its alert day
AID = "SPY|uoa|call|770|2026-10-09"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    # The suite's conftest hands every ``gh.connect()`` a NEW in-memory
    # database. This module is about what survives from one call to the next
    # (a restart, the next session), so it needs one file-backed store per test.
    path = tmp_path / "gex_history.db"

    def _connect(read_only: bool = False):
        return sqlite3.connect(str(path))

    monkeypatch.setattr(gh, "connect", _connect)
    tick.reset()
    _set_cfg(monkeypatch)
    yield
    tick.reset()


def _set_cfg(monkeypatch, **over):
    """The built-in defaults with ``sides`` / ``followup`` keys overridden. The
    ACCESSOR is patched, so a test proves the value is read, not merely equal."""
    cfg = flow_alerts._merge(flow_alerts._DEFAULTS, over)
    monkeypatch.setattr(flow_alerts, "load_thresholds", lambda: cfg)


def _at(day, hh, mm, ss=0):
    y, m, d = (int(x) for x in day.split("-"))
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=CT)


def _c(osi, vol, last=1.05, bid=1.00, ask=1.10, oi=1000):
    return {"symbol": osi, "totalVolume": vol, "last": last, "bid": bid,
            "ask": ask, "openInterest": oi}


def _chain(*contracts):
    return {"underlyingPrice": 770.0,
            "callExpDateMap": {"2026-10-09:7": {
                f"{700 + i}.0": [c] for i, c in enumerate(contracts)}},
            "putExpDateMap": {}}


def _alert(aid=AID, osi=OSI, kind="uoa", expiry="2026-10-09", ts=None):
    a = {"type": kind, "id": aid, "symbol": "SPY", "osi": osi, "side": "call",
         "strike": 770.0, "expiry": expiry}
    if ts is not None:
        a["ts"] = ts
    return a


def _after(fresh, now):
    bus = Bus()
    tick.after_alerts(bus, fresh, now.date().isoformat(), int(now.timestamp()))
    return bus


def _view(bus, key):
    env = bus.cache_get(key)
    return env.payload if env else None


def _rows(day):
    conn = gh.connect()
    try:
        gh.init_flow_day_schema(conn)
        return gh.load_flow_contract_days(conn, day)
    finally:
        conn.close()


# --- the poll tally ---------------------------------------------------------

def test_alert_reports_volume_from_before_it_fired():
    # Minute 1 seeds, minute 2 books 400 at the ask, THEN the alert fires. A
    # stream subscribed at the alert could never see those 400.
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    tick.on_chain("SPY", _chain(_c(OSI, 1400, last=1.10)), _at(FRI, 9, 31))
    bus = _after([_alert()], _at(FRI, 9, 31))
    view = _view(bus, tick.CACHE_SIDES)
    assert view["date"] == FRI
    assert view["contracts"][AID] == {
        "poll": {"bought": 400.0, "sold": 0.0, "unlabelled": 1000.0},
        "stream": None, "volume": 1400.0}


def test_published_tally_sums_to_the_volume():
    reads = [(1000, 1.05), (1400, 1.10), (1650, 1.00), (2100, 1.05)]
    for i, (vol, last) in enumerate(reads):
        tick.on_chain("SPY", _chain(_c(OSI, vol, last=last)), _at(FRI, 9, 30 + i))
    c = _view(_after([_alert()], _at(FRI, 9, 33)), tick.CACHE_SIDES)["contracts"][AID]
    assert sum(c["poll"].values()) == c["volume"] == 2100.0


def test_the_view_keeps_updating_after_the_alert():
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    _after([_alert()], _at(FRI, 9, 30))
    tick.on_chain("SPY", _chain(_c(OSI, 1300, last=1.00)), _at(FRI, 9, 31))
    c = _view(_after([], _at(FRI, 9, 31)), tick.CACHE_SIDES)["contracts"][AID]
    assert c["poll"] == {"bought": 0.0, "sold": 300.0, "unlabelled": 1000.0}


def test_a_poll_gap_is_unlabelled():
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    late = _at(FRI, 9, 30) + dt.timedelta(seconds=tick.MAX_GAP_SEC + 1)
    tick.on_chain("SPY", _chain(_c(OSI, 1900, last=1.10)), late)
    c = _view(_after([_alert()], late), tick.CACHE_SIDES)["contracts"][AID]
    assert c["poll"] == {"bought": 0.0, "sold": 0.0, "unlabelled": 1900.0}


def test_the_slowest_collection_step_is_still_labelled():
    # A watchlist-only symbol is fetched as rarely as every
    # MAX_TAIL_INTERVAL_MIN minutes by design: that step must not read as a gap.
    from services.options_svc import collection_tiers
    step = dt.timedelta(minutes=collection_tiers.MAX_TAIL_INTERVAL_MIN)
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    tick.on_chain("SPY", _chain(_c(OSI, 1400, last=1.10)), _at(FRI, 9, 30) + step)
    c = _view(_after([_alert()], _at(FRI, 9, 36)), tick.CACHE_SIDES)["contracts"][AID]
    assert c["poll"]["bought"] == 400.0


@pytest.mark.parametrize("bad", [None, {}, {"error": "token"}, 5,
                                 {"callExpDateMap": {}, "putExpDateMap": {}}])
def test_an_unusable_chain_is_not_a_first_poll(bad):
    # Were it counted as one, the next real chain would read the whole day's
    # volume as new and give it one minute's label.
    tick.on_chain("SPY", bad, _at(FRI, 9, 30))
    tick.on_chain("SPY", _chain(_c(OSI, 1000, last=1.10)), _at(FRI, 9, 31))
    c = _view(_after([_alert()], _at(FRI, 9, 31)), tick.CACHE_SIDES)["contracts"][AID]
    assert c["poll"] == {"bought": 0.0, "sold": 0.0, "unlabelled": 1000.0}


def test_a_new_session_date_starts_clean():
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    _after([_alert()], _at(FRI, 9, 30))
    tick.on_chain("SPY", _chain(_c(OSI, 50, last=1.10)), _at(MON, 9, 30))
    assert tick.wanted_osis() == []
    # Nothing is flagged on Monday, so nothing is published for it: Friday's
    # view is left as it was, and its date is what tells a reader it is old.
    assert _view(_after([], _at(MON, 9, 30)), tick.CACHE_SIDES)["date"] == FRI
    bus = _after([_alert()], _at(MON, 9, 31))
    view = _view(bus, tick.CACHE_SIDES)
    assert view["date"] == MON
    assert view["contracts"][AID]["poll"] == {
        "bought": 0.0, "sold": 0.0, "unlabelled": 50.0}


def test_open_interest_is_read_in_the_regular_session_only():
    # 08:10 CT is inside the flow window and before the 08:30 open, where
    # index open interest reads zero.
    tick.on_chain("SPY", _chain(_c(OSI, 100, oi=0)), _at(FRI, 8, 10))
    _after([_alert()], _at(FRI, 8, 10))
    assert _rows(FRI)[0]["oi_prev"] is None
    tick.on_chain("SPY", _chain(_c(OSI, 200, oi=9985)), _at(FRI, 8, 31))
    _after([], _at(FRI, 8, 31))
    assert _rows(FRI)[0]["oi_prev"] == 9985.0
    assert _rows(FRI)[0]["volume"] == 200.0     # ...while volume is always booked


# --- registration and the store ---------------------------------------------

def test_a_flagged_contract_is_stored_with_its_identity_and_tally():
    tick.on_chain("SPY", _chain(_c(OSI, 1000, oi=9985)), _at(FRI, 9, 30))
    tick.on_chain("SPY", _chain(_c(OSI, 1400, last=1.10, oi=9985)), _at(FRI, 9, 31))
    _after([_alert(ts=1234)], _at(FRI, 9, 31))
    (row,) = _rows(FRI)
    assert row == {
        "session_date": FRI, "alert_id": AID, "symbol": "SPY", "osi": OSI,
        "side": "call", "strike": 770.0, "expiry": "2026-10-09",
        "alert_type": "uoa", "fired_ts": 1234, "oi_prev": 9985.0,
        "volume": 1400.0, "poll_bought": 400.0, "poll_sold": 0.0,
        "poll_unlabelled": 1000.0, "stream_bought": None, "stream_sold": None,
        "stream_unlabelled": None, "oi_next": None, "oi_next_date": None,
        "verdict": None, "oi_ratio": None}


@pytest.mark.parametrize("alert", [
    {"type": "crossover", "id": "SPY|crossover", "symbol": "SPY"},
    {"type": "hiro_surge", "id": "SPY|hiro_surge|buy|1", "symbol": "SPY", "osi": OSI},
    _alert(osi=None), _alert(osi=""), _alert(aid=None), "not a dict", None,
])
def test_only_a_contract_alert_with_a_contract_symbol_is_registered(alert):
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    bus = _after([alert], _at(FRI, 9, 30))
    assert _view(bus, tick.CACHE_SIDES) is None
    assert _rows(FRI) == [] and tick.wanted_osis() == []


def test_both_alert_kinds_on_one_contract_get_their_own_row():
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    bd = "SPY|big_delta|call|770|2026-10-09"
    bus = _after([_alert(), _alert(aid=bd, kind="big_delta")], _at(FRI, 9, 30))
    assert set(_view(bus, tick.CACHE_SIDES)["contracts"]) == {AID, bd}
    assert tick.wanted_osis() == [OSI]          # one contract, streamed once


def test_nothing_is_published_until_a_contract_is_flagged():
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    assert _view(_after([], _at(FRI, 9, 30)), tick.CACHE_SIDES) is None


def test_restart_books_missed_volume_as_unlabelled():
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    tick.on_chain("SPY", _chain(_c(OSI, 1400, last=1.10)), _at(FRI, 9, 31))
    _after([_alert()], _at(FRI, 9, 31))         # stored: bought 400 / unlabelled 1000
    tick.reset()                                # the service restarts
    # 100 more contracts printed while it was down, then the next poll.
    tick.on_chain("SPY", _chain(_c(OSI, 1500, last=1.10)), _at(FRI, 10, 0))
    bus = _after([], _at(FRI, 10, 0))
    c = _view(bus, tick.CACHE_SIDES)["contracts"][AID]
    assert c["poll"] == {"bought": 400.0, "sold": 0.0, "unlabelled": 1100.0}
    assert c["volume"] == 1500.0
    assert tick.wanted_osis() == [OSI]          # and it is streamed again
    # The minute after, it labels normally.
    tick.on_chain("SPY", _chain(_c(OSI, 1600, last=1.10)), _at(FRI, 10, 1))
    c = _view(_after([], _at(FRI, 10, 1)), tick.CACHE_SIDES)["contracts"][AID]
    assert c["poll"] == {"bought": 500.0, "sold": 0.0, "unlabelled": 1100.0}


def test_restart_before_the_symbols_next_fetch_keeps_the_stored_tally():
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    tick.on_chain("SPY", _chain(_c(OSI, 1400, last=1.10)), _at(FRI, 9, 31))
    _after([_alert()], _at(FRI, 9, 31))
    tick.reset()
    bus = _after([], _at(FRI, 10, 0))           # no chain for SPY yet
    c = _view(bus, tick.CACHE_SIDES)["contracts"][AID]
    assert c["poll"] == {"bought": 400.0, "sold": 0.0, "unlabelled": 1000.0}
    tick.on_chain("SPY", _chain(_c(OSI, 1500, last=1.10)), _at(FRI, 10, 1))
    c = _view(_after([], _at(FRI, 10, 1)), tick.CACHE_SIDES)["contracts"][AID]
    assert c["poll"] == {"bought": 400.0, "sold": 0.0, "unlabelled": 1100.0}


# --- the stream -------------------------------------------------------------

def test_stream_ticks_reach_the_view_and_the_store():
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    _after([_alert()], _at(FRI, 9, 30))
    tick.stream_tick({"symbol": OSI, "total_volume": 1000.0, "last": 1.10,
                      "bid": 1.00, "ask": 1.10})                # seeds
    tick.stream_tick({"symbol": OSI, "total_volume": 1250.0})   # 250 at the ask
    tick.stream_tick({"symbol": OSI, "total_volume": 1300.0}, label=False)
    c = _view(_after([], _at(FRI, 9, 31)), tick.CACHE_SIDES)["contracts"][AID]
    assert c["stream"] == {"bought": 250.0, "sold": 0.0, "unlabelled": 50.0}
    (row,) = _rows(FRI)
    assert (row["stream_bought"], row["stream_sold"], row["stream_unlabelled"]) \
        == (250.0, 0.0, 50.0)


def test_a_stored_stream_tally_survives_a_restart():
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    _after([_alert()], _at(FRI, 9, 30))
    tick.stream_tick({"symbol": OSI, "total_volume": 1000.0, "last": 1.10,
                      "bid": 1.00, "ask": 1.10})
    tick.stream_tick({"symbol": OSI, "total_volume": 1250.0})
    _after([], _at(FRI, 9, 31))
    tick.reset()
    _after([], _at(FRI, 10, 0))
    tick.stream_tick({"symbol": OSI, "total_volume": 2000.0, "last": 1.10,
                      "bid": 1.00, "ask": 1.10})                # re-seeds, books nothing
    tick.stream_tick({"symbol": OSI, "total_volume": 2040.0})
    c = _view(_after([], _at(FRI, 10, 1)), tick.CACHE_SIDES)["contracts"][AID]
    assert c["stream"] == {"bought": 290.0, "sold": 0.0, "unlabelled": 0.0}


def test_the_stream_set_is_capped(monkeypatch):
    _set_cfg(monkeypatch, sides={"stream_max_contracts": 2})
    alerts = [_alert(aid=f"a{i}", osi=f"SPY   261009C0077{i}000") for i in range(4)]
    _after(alerts, _at(FRI, 9, 30))
    assert tick.wanted_osis() == [a["osi"] for a in alerts[:2]]
    assert len(_rows(FRI)) == 4                 # every alert still has its row


@pytest.mark.parametrize("setting,want", [
    (10_000, tick.STREAM_HARD_MAX),     # one request line holds only so many
    (-5, 0), (0, 0), (2.9, 2),
    ("200", 200), (None, 200), (float("nan"), 200),   # unusable -> the default
])
def test_the_stream_cap_is_bounded_whatever_the_setting(setting, want):
    assert tick._stream_cap({"stream_max_contracts": setting}) == want


def test_the_configuration_page_offers_no_more_than_the_hard_cap():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[3]
           / "webgui" / "config_schema.py").read_text(encoding="utf-8")
    field = src[src.index('"sides.stream_max_contracts"'):]
    field = field[:field.index("),")]
    assert f"max={tick.STREAM_HARD_MAX}," in field


@pytest.mark.parametrize("stream", [False, "true", 1, None])
def test_the_stream_switch_must_be_a_real_true(monkeypatch, stream):
    _set_cfg(monkeypatch, sides={"stream": stream})
    _after([_alert()], _at(FRI, 9, 30))
    assert tick.wanted_osis() == []


# --- switches ---------------------------------------------------------------

@pytest.mark.parametrize("enabled", [False, "true", 1, None])
def test_a_disabled_switch_measures_stores_and_publishes_nothing(monkeypatch, enabled):
    _set_cfg(monkeypatch, sides={"enabled": enabled})
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    bus = _after([_alert()], _at(FRI, 9, 30))
    assert _view(bus, tick.CACHE_SIDES) is None
    assert _rows(FRI) == [] and tick.wanted_osis() == []


@pytest.mark.parametrize("public,want", [(True, True), (False, False),
                                         ("true", False), (None, False)])
def test_the_public_flag_follows_config_and_fails_closed(monkeypatch, public, want):
    _set_cfg(monkeypatch, sides={"public": public})
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    bus = _after([_alert()], _at(FRI, 9, 30))
    assert _view(bus, tick.CACHE_SIDES)["public"] is want


# --- next-day open interest --------------------------------------------------

def _friday_alert(osi=OSI, aid=AID, expiry="2026-10-09", vol=1000, oi=1000):
    """One flagged contract on Friday: volume ``vol``, open interest ``oi``."""
    tick.on_chain("SPY", _chain(_c(osi, vol, oi=oi)), _at(FRI, 9, 30))
    _after([_alert(aid=aid, osi=osi, expiry=expiry)], _at(FRI, 9, 30))
    tick.reset()        # the weekend: Monday is a fresh process state


def test_followup_resolves_from_the_next_sessions_chain():
    _friday_alert()
    tick.on_chain("SPY", _chain(_c(OSI, 5, oi=1700)), _at(MON, 8, 31))
    _after([], _at(MON, 8, 31))                 # loads Friday's row, watches it
    tick.on_chain("SPY", _chain(_c(OSI, 9, oi=1700)), _at(MON, 8, 32))
    bus = _after([], _at(MON, 8, 32))
    (row,) = _rows(FRI)
    assert (row["oi_next"], row["oi_next_date"], row["verdict"]) \
        == (1700.0, MON, "opened")
    assert row["oi_ratio"] == pytest.approx(0.7)
    view = _view(bus, tick.CACHE_FOLLOWUP)
    assert view["date"] == FRI and view["public"] is True
    assert [r["verdict"] for r in view["rows"]] == ["opened"]


def test_followup_reads_a_contract_that_does_not_trade_the_next_day():
    _friday_alert()
    _after([], _at(MON, 8, 31))
    tick.on_chain("SPY", _chain(_c(OSI, 0, oi=400)), _at(MON, 8, 32))
    _after([], _at(MON, 8, 32))
    (row,) = _rows(FRI)
    assert (row["oi_next"], row["verdict"]) == (400.0, "closed")


def test_followup_is_published_as_waiting_before_it_resolves():
    _friday_alert()
    view = _view(_after([], _at(MON, 8, 5)), tick.CACHE_FOLLOWUP)
    assert view["date"] == FRI
    assert [r["verdict"] for r in view["rows"]] == [None]


def test_followup_waits_for_the_regular_session():
    # Before 08:30 CT index open interest reads zero: it must not be taken as
    # "everything closed".
    _friday_alert()
    _after([], _at(MON, 8, 5))
    tick.on_chain("SPY", _chain(_c(OSI, 0, oi=0)), _at(MON, 8, 10))
    _after([], _at(MON, 8, 10))
    assert _rows(FRI)[0]["verdict"] is None


def test_followup_is_re_read_and_logs_when_the_figure_moves(caplog):
    # When Schwab's chain picks up the new figure is not yet measured: a first
    # read may repeat Friday's.
    _friday_alert()
    _after([], _at(MON, 8, 31))
    tick.on_chain("SPY", _chain(_c(OSI, 5, oi=1000)), _at(MON, 8, 32))
    _after([], _at(MON, 8, 32))
    assert _rows(FRI)[0]["verdict"] == "mixed"
    with caplog.at_level(logging.INFO, logger=tick.log.name):
        tick.on_chain("SPY", _chain(_c(OSI, 9, oi=1700)), _at(MON, 9, 15))
        bus = _after([], _at(MON, 9, 15))
    assert _rows(FRI)[0]["verdict"] == "opened"
    assert "open interest moved" in caplog.text
    assert _view(bus, tick.CACHE_FOLLOWUP)["rows"][0]["oi_next"] == 1700.0


def test_an_unchanged_figure_is_not_rewritten(caplog):
    _friday_alert()
    _after([], _at(MON, 8, 31))
    tick.on_chain("SPY", _chain(_c(OSI, 5, oi=1700)), _at(MON, 8, 32))
    _after([], _at(MON, 8, 32))
    with caplog.at_level(logging.INFO, logger=tick.log.name):
        tick.on_chain("SPY", _chain(_c(OSI, 9, oi=1700)), _at(MON, 8, 33))
        _after([], _at(MON, 8, 33))
    assert "open interest moved" not in caplog.text


def test_a_same_day_expiry_resolves_as_expired_with_no_chain():
    _friday_alert(osi=OSI_0DTE, aid="SPY|uoa|call|770|2026-10-02",
                  expiry="2026-10-02")
    bus = _after([], _at(MON, 8, 5))
    (row,) = _rows(FRI)
    assert (row["verdict"], row["oi_next"], row["oi_ratio"]) == ("expired", None, None)
    assert _view(bus, tick.CACHE_FOLLOWUP)["rows"][0]["verdict"] == "expired"


def test_the_verdict_ratios_are_read_from_config(monkeypatch):
    _friday_alert()
    _set_cfg(monkeypatch, followup={"opened_ratio": 0.9})
    _after([], _at(MON, 8, 31))
    tick.on_chain("SPY", _chain(_c(OSI, 5, oi=1700)), _at(MON, 8, 32))
    _after([], _at(MON, 8, 32))
    assert _rows(FRI)[0]["verdict"] == "mixed"      # 0.7 is below the 0.9 bar


def test_followup_switched_off_resolves_and_publishes_nothing(monkeypatch):
    _friday_alert()
    _set_cfg(monkeypatch, followup={"enabled": False})
    _after([], _at(MON, 8, 31))
    tick.on_chain("SPY", _chain(_c(OSI, 5, oi=1700)), _at(MON, 8, 32))
    bus = _after([], _at(MON, 8, 32))
    assert _rows(FRI)[0]["verdict"] is None
    assert _view(bus, tick.CACHE_FOLLOWUP) is None


def test_old_sessions_are_purged_with_the_configured_retention(monkeypatch):
    _set_cfg(monkeypatch, followup={"keep_sessions": 1})
    _friday_alert()
    tick.on_chain("SPY", _chain(_c(OSI, 10)), _at(MON, 9, 30))
    _after([_alert()], _at(MON, 9, 30))
    assert _rows(FRI) == [] and len(_rows(MON)) == 1


def test_the_purge_runs_once_per_session(monkeypatch):
    calls = []
    real = gh.purge_flow_contract_days
    monkeypatch.setattr(gh, "purge_flow_contract_days",
                        lambda conn, keep: calls.append(keep) or real(conn, keep))
    for minute in (30, 31, 32):
        _after([], _at(FRI, 9, minute))
    assert calls == [20]


# --- it must never break the caller -----------------------------------------

def test_after_alerts_never_raises_and_counts_a_degrade(monkeypatch):
    class _Boom:
        def cache_set(self, *a, **k):
            raise RuntimeError("redis down")
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    seen = []
    monkeypatch.setattr(_degrade, "degraded", lambda area, **k: seen.append(area))
    tick.after_alerts(_Boom(), [_alert()], FRI, 0)
    assert seen == ["options.flow_sides.after_alerts"]


def test_on_chain_never_raises_and_counts_a_degrade(monkeypatch):
    seen = []
    monkeypatch.setattr(_degrade, "degraded", lambda area, **k: seen.append(area))
    monkeypatch.setattr(tick.flow_sides, "advance",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))
    assert seen == ["options.flow_sides.on_chain"]


def test_on_chain_never_opens_the_database(monkeypatch):
    # It runs inside the poll: a slow or locked store must not cost a minute.
    monkeypatch.setattr(gh, "connect", lambda *a, **k: pytest.fail("opened the store"))
    tick.on_chain("SPY", _chain(_c(OSI, 1000)), _at(FRI, 9, 30))


# --- wiring ------------------------------------------------------------------

def test_the_poll_hook_calls_on_chain():
    """A refactor of compute's on_chain must not silently drop the tally."""
    from services.options_svc import compute
    src = inspect.getsource(compute.collect_gex_snapshots)
    hook = src[src.index("def on_chain("):]
    hook = hook[:hook.index("# Decided HERE")]
    assert "flow_sides_tick.on_chain(sym, chain, now)" in hook


def test_run_flow_alerts_calls_after_alerts():
    from services.options_svc import handlers
    src = inspect.getsource(handlers.run_flow_alerts)
    assert "flow_sides_tick.after_alerts(bus, fresh, today, now_ts)" in src
    # ...on every tick, not only when a new alert fired.
    assert src.index("flow_sides_tick.after_alerts") < src.index("if fresh:")
