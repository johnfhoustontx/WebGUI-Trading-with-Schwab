"""The intraday recorder (scalp_record): one row per scalp symbol per slot.

Every source goes through the fake bus and ``scalp_confirm.sources``; every
write goes to ``tmp_path``. The shapes come from test_scalp_confirm's fixtures.
"""
import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.options_svc import scalp_call, scalp_confirm, scalp_record, scalp_store
from services.options_svc.tests import test_scalp_call as call_fx
from services.options_svc.tests.test_scalp_confirm import (
    alert, flow, full_matrix, netprem, sentiment)
from shared import scalp_config
from shared.bus import Bus
from shared.bus.client import reset_fake_bus

CT = ZoneInfo("America/Chicago")
DAY = dt.date(2026, 10, 20)               # a Tuesday
SLOT = "09:06"
NOW = dt.datetime(2026, 10, 20, 9, 6, 30, tzinfo=CT)
ENTRY = scalp_config.intraday_entry_ts(DAY, SLOT)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "scalp.db"


@pytest.fixture
def bus():
    reset_fake_bus()
    return Bus(fake=True)


@pytest.fixture(autouse=True)
def _fresh_degrades():
    _degrade.reset()
    yield
    _degrade.reset()


def _publish(bus):
    """The four sources, as the service has them at 09:06 on the 20th."""
    now_iso = NOW.astimezone(dt.timezone.utc).isoformat()
    bus.cache_set(scalp_confirm.MATRIX, full_matrix())
    bus.cache_set(scalp_confirm.NETPREM, {
        "session_date": "2026-10-20", "ts": now_iso, "error": None,
        "series": {"SPY": [[ENTRY - 60, 600.0, 400.0]]}})
    bus.cache_set(scalp_confirm.FLOW, flow([
        alert("crossover", "calls_over", "SPY", minutes_ago=3),
        alert("hiro_surge", "dealers_buying", "NVDA", minutes_ago=7)]))
    bus.cache_set(scalp_confirm.SENTIMENT, sentiment("bullish"))


def test_record_writes_three_rows_without_directions(db, bus):
    _publish(bus)
    assert scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db) == 3
    rows = scalp_store.readings_on(DAY.isoformat(), db_path=db)
    assert sorted(rows) == [(SLOT, s) for s in sorted(scalp_config.symbols())]
    spy = rows[(SLOT, "SPY")]
    assert spy["slot"] == SLOT and spy["entry_ts"] == ENTRY
    assert spy["direction"] is None
    assert spy["recorded_at"] == NOW.isoformat()
    # Raw inputs are kept even with no direction, verdicts are not scored.
    comp = spy["components"]
    assert comp["score"] is None and comp["word"] is None
    assert comp["netprem"]["raw"]["call"] == 600.0
    assert comp["flow"]["raw"]["bull"] == 2          # SPY's and NVDA's (big ten)
    assert comp["sentiment"]["raw"]["state"] == "bullish"
    # The snapshot carries the walls of the big ten, windowed to this entry.
    snap = spy["snapshot"]
    assert snap["matrix"]["SPY"]["call_wall"] == 775.0
    assert "NVDA" in snap["matrix"]
    assert snap["flow"]["window"]["n_alerts"] == 2
    assert json.dumps(spy, allow_nan=False)


def test_record_scores_a_symbol_given_a_direction(db, bus):
    _publish(bus)
    n = scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db,
                            directions={"SPY": 1}, win_points={"SPY": 3.0})
    assert n == 3
    rows = scalp_store.readings_on(DAY.isoformat(), db_path=db)
    assert rows[(SLOT, "SPY")]["direction"] == 1
    assert rows[(SLOT, "SPY")]["components"]["word"] is not None
    assert rows[(SLOT, "SPY")]["components"]["dealer"]["raw"]["wall_kind"] == "call"
    assert rows[(SLOT, "QQQ")]["direction"] is None
    assert rows[(SLOT, "QQQ")]["components"]["word"] is None


def test_record_measures_room_from_the_calls_entry_spot_and_keeps_every_age(db, bus):
    # The matrix says 769.44 (room 5.56 to the 775 call wall); the call entered
    # at 773.00 (room 2.00, inside a 3-point target).
    _publish(bus)
    scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db, directions={"SPY": 1},
                        win_points={"SPY": 3.0}, spots={"SPY": 773.0})
    spy = scalp_store.readings_on(DAY.isoformat(), db_path=db)[(SLOT, "SPY")]
    raw = spy["components"]["dealer"]["raw"]
    assert raw["room_spot"] == 773.0 and raw["room_spot_source"] == "entry"
    assert raw["spot"] == 769.44 and raw["own_verdict"] == -1
    assert spy["components"]["n_present"] >= 2
    ages = spy["snapshot"]["sources"]
    assert {k: v["state"] for k, v in ages.items()}["sentiment"] == "ok"
    assert ages["sentiment"]["age_min"] is not None and ages["matrix"]["age_min"] is not None
    assert spy["snapshot"]["sentiment"]["age_min"] == ages["sentiment"]["age_min"]


def test_record_is_idempotent_per_slot(db, bus):
    _publish(bus)
    assert scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db) == 3
    assert scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db) == 0
    assert len(scalp_store.readings_on(DAY.isoformat(), db_path=db)) == 3
    scalp_record.record(bus, DAY, "09:21", now=NOW, db_path=db)
    assert len(scalp_store.readings_on(DAY.isoformat(), db_path=db)) == 6


def test_record_with_no_sources_still_records_what_was_absent(db, bus):
    assert scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db) == 3
    spy = scalp_store.readings_on(DAY.isoformat(), db_path=db)[(SLOT, "SPY")]
    assert spy["snapshot"]["sources"]["matrix"]["state"] == "absent"
    assert spy["components"]["netprem"]["raw"]["state"] == "absent"


def test_one_symbol_failing_never_stops_the_others(db, bus, monkeypatch):
    _publish(bus)
    real = scalp_confirm.components

    def boom(src, symbol, *a, **k):
        if symbol == "SPY":
            raise RuntimeError("bad")
        return real(src, symbol, *a, **k)

    monkeypatch.setattr(scalp_confirm, "components", boom)
    assert scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db) == 2
    assert _degrade.counts().get("options.scalp_record") == 1


def test_record_never_raises_when_the_store_fails(db, bus, monkeypatch):
    _publish(bus)

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(scalp_store, "save_reading", boom)
    assert scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db) == 0
    assert _degrade.counts().get("options.scalp_record") == 3


def test_record_never_raises_when_the_bus_fails(db):
    class Dead:
        def cache_get(self, key):
            raise ConnectionError("down")

    # sources() swallows each read; the rows are still written, all absent.
    assert scalp_record.record(Dead(), DAY, SLOT, now=NOW, db_path=db) == 3


# --- the 08:36 call -------------------------------------------------------------

def _call_sources(bus):
    bus.cache_set(scalp_confirm.MATRIX, full_matrix())
    bus.cache_set(scalp_confirm.SENTIMENT, sentiment("bullish"))


def test_0836_publish_also_records(db, bus):
    call_fx._seeded(db)
    _call_sources(bus)
    scalp_call.run(bus, now=call_fx.NOW, db_path=db, client=call_fx.FakeClient())
    day = call_fx.DAY.isoformat()
    rows = scalp_store.readings_on(day, db_path=db)
    assert sorted(s for (slot, s) in rows if slot == "08:36") == sorted(call_fx.PRICES)
    spy = rows[("08:36", "SPY")]
    published = bus.cache_get(scalp_call.VIEW).payload["symbols"]["SPY"]
    assert spy["direction"] == published["direction"] == 1
    assert spy["entry_ts"] == published["entry_ts"]          # the call's own entry
    dealer = spy["components"]["dealer"]["raw"]
    assert dealer["win_points"] == published["roles"]["long_with"]["win_points"]
    assert spy["components"]["word"] is not None


def test_a_later_refresh_does_not_replace_the_0836_row(db, bus):
    call_fx._seeded(db)
    _call_sources(bus)
    scalp_call.run(bus, now=call_fx.NOW, db_path=db, client=call_fx.FakeClient())
    day = call_fx.DAY.isoformat()
    first = scalp_store.readings_on(day, db_path=db)[("08:36", "SPY")]
    later = call_fx.NOW.replace(hour=10, minute=0)
    scalp_call.run(bus, now=later, db_path=db, client=call_fx.FakeClient())
    assert scalp_store.readings_on(day, db_path=db)[("08:36", "SPY")] == first


def test_a_recording_failure_never_touches_the_call(db, bus, monkeypatch):
    call_fx._seeded(db)

    def boom(*a, **k):
        raise RuntimeError("record broke")

    monkeypatch.setattr(scalp_record, "record", boom)
    scalp_call.run(bus, now=call_fx.NOW, db_path=db, client=call_fx.FakeClient())
    assert bus.cache_get(scalp_call.VIEW).payload["state"] == "call"
    assert set(scalp_store.calls_on(call_fx.DAY.isoformat(), db_path=db))         == set(call_fx.PRICES)
    assert _degrade.counts().get("options.scalp_record") == 1


def test_a_call_that_did_not_publish_is_not_recorded(db, bus, monkeypatch):
    call_fx._seeded(db)

    def refuse(*a, **k):
        raise RuntimeError("bus down")

    monkeypatch.setattr(bus, "cache_set", refuse)
    scalp_call.run(bus, now=call_fx.NOW, db_path=db, client=call_fx.FakeClient())
    assert scalp_store.readings_on(call_fx.DAY.isoformat(), db_path=db) == {}


# --- write-once -----------------------------------------------------------------

def _stored(db):
    return scalp_store.readings_on(DAY.isoformat(), db_path=db)


def _frozen(rows):
    """The rows as one comparable string (the store's keys are tuples)."""
    return json.dumps({f"{slot}|{sym}": body for (slot, sym), body in rows.items()},
                      sort_keys=True)


def test_a_recorded_slot_is_never_rewritten(db, bus):
    # Ruling (Task 8, fix round 1): a direction-less row may be upgraded by a row
    # WITH a direction (see the upgrade tests below); everything else is still
    # write-once. So the later write here names a direction for SPY only, and
    # every other row must stay byte for byte, as must a direction-less retry.
    _publish(bus)
    assert scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db) == 3
    first = _frozen(_stored(db))
    # Different sources, a later clock, no direction: still the same rows.
    bus.cache_set(scalp_confirm.MATRIX, full_matrix(call_wall=790.0))
    bus.cache_set(scalp_confirm.SENTIMENT, sentiment("bearish"))
    later = NOW + dt.timedelta(minutes=3)
    assert scalp_record.record(bus, DAY, SLOT, now=later, db_path=db) == 0
    assert _frozen(_stored(db)) == first
    before = _stored(db)
    assert scalp_record.record(bus, DAY, SLOT, now=later, db_path=db,
                               directions={"SPY": 1}, win_points={"SPY": 3.0}) == 1
    after = _stored(db)
    for sym in scalp_config.symbols():
        if sym != "SPY":
            assert after[(SLOT, sym)] == before[(SLOT, sym)]
    assert after[(SLOT, "SPY")]["direction"] == 1


def test_a_directionless_row_is_upgraded_once_and_then_frozen(db, bus):
    _publish(bus)
    scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db)
    later = NOW + dt.timedelta(minutes=2)
    assert scalp_record.record(bus, DAY, SLOT, now=later, db_path=db,
                               directions={"SPY": 1, "QQQ": -1},
                               win_points={"SPY": 3.0, "QQQ": 2.0}) == 2
    upgraded = _frozen(_stored(db))
    rows = _stored(db)
    assert rows[(SLOT, "SPY")]["direction"] == 1
    assert rows[(SLOT, "QQQ")]["direction"] == -1
    assert rows[(SLOT, "SPY")]["components"]["word"] is not None
    assert rows[(SLOT, "SPY")]["recorded_at"] == later.isoformat()
    # A row WITH a direction is never replaced: not by the other direction, not
    # by a direction-less write, not by new sources.
    bus.cache_set(scalp_confirm.SENTIMENT, sentiment("bearish"))
    for directions in ({"SPY": -1, "QQQ": 1}, {}, {"SPY": 1}):
        assert scalp_record.record(bus, DAY, SLOT, now=later + dt.timedelta(minutes=1),
                                   db_path=db, directions=directions) == 0
    assert _frozen(_stored(db)) == upgraded


def test_a_bool_is_not_a_direction_for_the_upgrade(db, bus):
    _publish(bus)
    scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db)
    first = _frozen(_stored(db))
    assert scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db,
                               directions={"SPY": True, "QQQ": 0}) == 0
    assert _frozen(_stored(db)) == first


def test_record_reads_the_sources_it_is_given(db, bus):
    # The caller's own read (the call's) is what is recorded, not a fresh one.
    _publish(bus)
    src = scalp_confirm.sources(bus, NOW)
    bus.cache_set(scalp_confirm.SENTIMENT, sentiment("bearish"))
    scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db, src=src)
    spy = _stored(db)[(SLOT, "SPY")]
    assert spy["components"]["sentiment"]["raw"]["state"] == "bullish"


def test_only_the_missing_symbol_of_a_slot_is_written(db, bus):
    import sqlite3

    _publish(bus)
    scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db)
    before = _stored(db)
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("DELETE FROM intraday_readings WHERE slot = ? AND symbol = ?",
                     (SLOT, "QQQ"))
    conn.close()
    bus.cache_set(scalp_confirm.SENTIMENT, sentiment("bearish"))
    assert scalp_record.record(bus, DAY, SLOT, now=NOW, db_path=db) == 1
    after = _stored(db)
    for sym in scalp_config.symbols():
        if sym == "QQQ":
            assert after[(SLOT, sym)]["components"]["sentiment"]["raw"]["state"] == "bearish"
        else:
            assert after[(SLOT, sym)] == before[(SLOT, sym)]


def test_a_refresh_six_minutes_after_the_call_leaves_the_0836_rows_identical(db, bus):
    call_fx._seeded(db)
    _call_sources(bus)
    scalp_call.run(bus, now=call_fx.NOW, db_path=db, client=call_fx.FakeClient())
    day = call_fx.DAY.isoformat()
    first = _frozen(scalp_store.readings_on(day, db_path=db))
    # Inside the call slot's grace (20 minutes), so the grace guard alone would
    # let it record again: only write-once keeps the 08:36 rows.
    bus.cache_set(scalp_confirm.MATRIX, full_matrix(call_wall=790.0, put_wall=750.0))
    bus.cache_set(scalp_confirm.SENTIMENT, sentiment("bearish"))
    scalp_call.run(bus, now=call_fx.NOW + dt.timedelta(minutes=6), db_path=db,
                   client=call_fx.FakeClient())
    assert _frozen(scalp_store.readings_on(day, db_path=db)) == first
