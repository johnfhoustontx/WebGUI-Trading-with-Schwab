"""traded_premium: new volume priced once, kept as a running total per strike.

Design: docs/plans/2026-10-09-traded-premium-increment-design.md.
"""
import ast
import datetime as dt
import logging
import pathlib
import sqlite3
import time
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from services import _degrade
from services.options_svc import traded_premium as tp

import gex_history_db as gh

CT = ZoneInfo("America/Chicago")
MON = "2026-10-05"                  # a trading day


def _c(osi, vol, mark=1.00, **more):
    c = {"symbol": osi, "totalVolume": vol, **more}
    if mark is not None:
        c["mark"] = mark
    return c


def _chain(calls=None, puts=None, spot=500.0):
    """``calls`` / ``puts``: {strike text: [contract, ...]} in one expiration."""
    return {"underlyingPrice": spot,
            "callExpDateMap": {"2026-10-16:11": calls or {}},
            "putExpDateMap": {"2026-10-16:11": puts or {}}}


def _advance(state, chain, ts, late_sec=90):
    return tp.advance(state, chain, ts, late_sec=late_sec)


# ---- the arithmetic -----------------------------------------------------------

def test_the_first_reading_sets_baselines_and_books_nothing():
    s = tp.new_state()
    assert _advance(s, _chain({"100.0": [_c("C1", 1000, 5.0)]}), 60) == 0.0
    assert s["total"] == {} and s["hw"] == {"C1": 1000.0}
    assert s["seeded"] is True and s["since_ts"] == 60 and s["first_vol"] == 1000.0


def test_new_volume_is_priced_once_at_the_mark_it_arrived_at():
    """The design's example: 1,000 contracts at $5.00, then the mark decays."""
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0, 5.0)]}), 0)
    assert _advance(s, _chain({"100.0": [_c("C1", 1000, 5.0)]}), 60) == 500_000.0
    for ts, mark in ((120, 2.0), (180, 0.2)):
        assert _advance(s, _chain({"100.0": [_c("C1", 1000, mark)]}), ts) == 0.0
    assert tp.grid(s) == {100.0: {"call": 500_000.0, "put": 0.0, "net": 500_000.0}}


def test_a_contract_first_seen_later_counts_from_zero():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 10)]}), 0)
    booked = _advance(s, _chain({"100.0": [_c("C1", 10)],
                                 "105.0": [_c("C2", 7, 2.0)]}), 60)
    assert booked == 1400.0
    assert tp.grid(s)[105.0]["call"] == 1400.0


def test_a_glitch_read_of_zero_books_nothing_and_cannot_rebook_the_day():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 1000)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 1010)]}), 60)        # 10 x $1 x 100
    assert _advance(s, _chain({"100.0": [_c("C1", 0)]}), 120) == 0.0
    assert _advance(s, _chain({"100.0": [_c("C1", 1010)]}), 180) == 0.0
    assert s["booked"] == 1000.0


def test_volume_with_no_usable_mark_waits_for_one():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 100)]}), 0)
    assert _advance(s, _chain({"100.0": [_c("C1", 130, mark=None)]}), 60) == 0.0
    assert s["hw"]["C1"] == 100.0                  # the baseline did not move
    assert _advance(s, _chain({"100.0": [_c("C1", 150, 2.0)]}), 120) == 10_000.0


def test_the_mark_falls_back_to_the_middle_of_the_quote():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0)]}), 0)
    chain = _chain({"100.0": [_c("C1", 10, mark=None, bid=1.0, ask=1.2)]})
    assert _advance(s, chain, 60) == pytest.approx(1100.0)


@pytest.mark.parametrize("mark", [float("inf"), float("nan"), 0, -1.0])
def test_an_unusable_mark_is_never_priced(mark):
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0)]}), 0)
    assert _advance(s, _chain({"100.0": [_c("C1", 10, mark=mark)]}), 60) == 0.0
    assert s["total"] == {}


def test_strikes_add_up_across_expirations_and_the_sides_stay_apart():
    def chain(a, b, p):
        return {"underlyingPrice": 500.0,
                "callExpDateMap": {"2026-10-09:4": {"100.0": [a]},
                                   "2026-10-16:11": {"100.0": [b]}},
                "putExpDateMap": {"2026-10-09:4": {"100.0": [p]}}}
    s = tp.new_state()
    _advance(s, chain(_c("A", 0), _c("B", 0), _c("P", 0)), 0)
    assert _advance(s, chain(_c("A", 1, 1.0), _c("B", 2, 3.0), _c("P", 5, 0.5)),
                    60) == 950.0
    assert tp.grid(s) == {100.0: {"call": 700.0, "put": 250.0, "net": 450.0}}


@pytest.mark.parametrize("chain", [None, "x", {}, {"error": "Bad Request"},
                                   {"callExpDateMap": {}, "putExpDateMap": {}},
                                   {"callExpDateMap": 5}])
def test_a_chain_with_no_contracts_changes_nothing(chain):
    """An error body must not count as the first reading: the next real chain
    would then read the whole day's volume as new."""
    s = tp.new_state()
    assert _advance(s, chain, 60) is None
    assert s == tp.new_state()


def test_volume_first_seen_after_a_long_step_is_counted_as_priced_late():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 10)]}), 60)           # one minute on
    _advance(s, _chain({"100.0": [_c("C1", 15)]}), 260)          # 200 s on
    assert (s["booked_vol"], s["late_vol"]) == (15.0, 5.0)


def test_the_same_volume_is_also_priced_at_last_where_there_is_one():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0), _c("C2", 0)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 10, 2.0, last=2.5),
                                  _c("C2", 4, 1.0)]}), 60)
    assert s["booked"] == 2400.0                        # everything, at the mark
    assert (s["last_vol"], s["at_last"], s["at_mark_same"]) == (10.0, 2500.0, 2000.0)


def test_the_chains_own_volume_is_read_apart_from_the_booking():
    """What the day's check sets the booking against."""
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 100)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 130, mark=None)]}), 60)
    assert (s["first_vol"], s["chain_vol"], s["booked_vol"]) == (100.0, 130.0, 0.0)


# ---- resuming a stored total --------------------------------------------------

def test_a_resumed_total_lands_in_the_cell_new_volume_uses():
    """The store keeps strikes as float32: 17.63 reads back as 17.6299991607666.
    Two cells for one strike pack to ONE stored key, and one would be lost."""
    stored_key = float(np.float32(17.63))
    assert stored_key != 17.63
    s = tp.new_state()
    tp._resume(s, (0, 500.0, 0.0,
                   {stored_key: {"call": 1000.0, "put": 0.0, "net": 1000.0}}))
    _advance(s, _chain({"17.63": [_c("X", 0)]}), 0)
    _advance(s, _chain({"17.63": [_c("X", 5)]}), 60)
    assert tp.grid(s) == {17.63: {"call": 1500.0, "put": 0.0, "net": 1500.0}}


def test_a_strike_is_keyed_the_same_way_read_or_resumed():
    """Both round to three decimals, so the two can never disagree about which
    cell a strike is."""
    s = tp.new_state()
    tp._resume(s, (0, 1.0, 0.0, {17.6251: {"call": 100.0, "put": 0.0}}))
    _advance(s, _chain({"17.6251": [_c("X", 0)]}), 0)
    _advance(s, _chain({"17.6251": [_c("X", 1)]}), 60)
    assert tp.grid(s) == {17.625: {"call": 200.0, "put": 0.0, "net": 200.0}}


def test_resume_takes_what_is_usable_and_nothing_else():
    s = tp.new_state()
    for prior in (None, (0, 1.0, 0.0, None), (0, 1.0, 0.0, "x")):
        tp._resume(s, prior)
    assert s["total"] == {}
    tp._resume(s, (0, 1.0, 0.0, {100.0: {"call": 5.0, "put": float("nan")},
                                 "bad": {"call": 1.0}, 105.0: "x"}))
    assert tp.grid(s) == {100.0: {"call": 5.0, "put": 0.0, "net": 5.0}}


# ---- the module ---------------------------------------------------------------

def test_the_module_imports_nothing_from_compute():
    tree = ast.parse(pathlib.Path(tp.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    assert not any(n.endswith("compute") for n in names), names


# ---- the session state and the store -----------------------------------------
# Readings are taken mid-morning so the stored rows fall on the same calendar
# date whatever zone the machine running the suite is in.

@pytest.fixture(autouse=True)
def _clean():
    tp.reset()
    yield
    tp.reset()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    gh.init_schema(c)
    yield c
    c.close()


def _switch(monkeypatch, on=True, late_sec=90):
    """The ACCESSOR is patched, so a test proves the value is read."""
    real = tp._mdc.section
    monkeypatch.setattr(tp._mdc, "section", lambda name: dict(
        real(name), traded_premium=on, traded_premium_late_sec=late_sec))


def _at(hh, mm, day=MON):
    y, m, d = (int(x) for x in day.split("-"))
    return dt.datetime(y, m, d, hh, mm, tzinfo=CT)


def _poll(conn, now, chain, symbol="SPY", store=gh):
    tp.on_chain(symbol, chain, now)
    tp.write_rows(store, conn, int(now.timestamp()) // 60 * 60)


def _stored(conn, symbol="SPY"):
    return [(ts, gh._decode_grid(raw), calls, puts)
            for ts, raw, calls, puts in conn.execute(
                "SELECT ts, gex_json, call_prem, put_prem FROM snapshots "
                "WHERE symbol = ? AND view = 'tprem' ORDER BY ts", (symbol,))]


def _call(vol, mark=1.0):
    return _chain({"100.0": [_c("C1", vol, mark)]})


def _degrades(monkeypatch):
    seen = []
    monkeypatch.setattr(_degrade, "degraded", lambda area, **kw: seen.append(area))
    return seen


class _Store:
    """The real store, with one named call made to fail once."""

    def __init__(self, fail):
        self.fail = fail

    def _call(self, name, *a, **k):
        if self.fail == name:
            self.fail = None
            raise sqlite3.OperationalError("database is locked")
        return getattr(gh, name)(*a, **k)

    def latest_grid_row(self, *a, **k):
        return self._call("latest_grid_row", *a, **k)

    def insert_snapshot(self, *a, **k):
        return self._call("insert_snapshot", *a, **k)


def test_it_ships_off_and_then_does_nothing(conn):
    _poll(conn, _at(10, 0), _call(1000))
    _poll(conn, _at(10, 1), _call(1100))
    assert _stored(conn) == [] and tp._S["symbols"] == {}


def test_nothing_is_booked_before_the_regular_open(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(8, 0), _call(50))
    assert _stored(conn) == [] and tp._S["symbols"] == {}


def test_each_poll_stores_the_running_total_and_no_side_ever_falls(monkeypatch, conn):
    _switch(monkeypatch)

    def chain(call_vol, call_mark, put_vol, put_mark):
        return _chain({"100.0": [_c("C1", call_vol, call_mark)]},
                      {"100.0": [_c("P1", put_vol, put_mark)]})

    _poll(conn, _at(10, 0), chain(1000, 5.0, 200, 1.0))
    _poll(conn, _at(10, 1), chain(1100, 5.0, 200, 1.0))    # 100 calls at $5
    _poll(conn, _at(10, 2), chain(1100, 2.0, 250, 1.2))    # the call mark falls
    rows = _stored(conn)
    assert [ts for ts, *_ in rows] == [int(_at(10, m).timestamp()) for m in (0, 1, 2)]
    assert [g for _ts, g, _calls, _puts in rows] == [
        {},                                                # the watch began here
        {100.0: {"call": 50_000.0, "put": 0.0, "net": 50_000.0}},
        {100.0: {"call": 50_000.0, "put": 6_000.0, "net": 44_000.0}}]
    assert rows[-1][2:] == (50_000.0, 6_000.0)             # the symbol's totals


def test_the_row_carries_the_chains_spot_and_the_polls_minute(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _chain({"100.0": [_c("C1", 1)]}, spot=512.5))
    assert conn.execute("SELECT ts, spot, dte FROM snapshots WHERE view = 'tprem'"
                        ).fetchall() == [(int(_at(10, 0).timestamp()), 512.5, None)]


def test_without_the_polls_minute_the_row_takes_the_clocks(monkeypatch, conn):
    _switch(monkeypatch)
    tp.on_chain("SPY", _call(1), _at(10, 0))
    tp.write_rows(gh, conn, None)
    (ts, *_rest), = _stored(conn)
    assert ts % 60 == 0 and abs(ts - time.time()) < 120


def test_a_restart_continues_the_total_and_leaves_the_gap_unbooked(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _call(1000, 5.0))
    _poll(conn, _at(10, 1), _call(1100, 5.0))              # $50,000
    tp.reset()                                             # the process restarted
    _poll(conn, _at(10, 5), _call(1500, 2.0))              # 400 traded unseen
    _poll(conn, _at(10, 6), _call(1510, 2.0))              # 10 x $2 x 100
    assert [g[100.0]["call"] for _ts, g, _calls, _puts in _stored(conn)[1:]] == [
        50_000.0, 50_000.0, 52_000.0]


def test_a_new_session_starts_from_zero(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _call(1000))
    _poll(conn, _at(10, 1), _call(1100))
    _poll(conn, _at(10, 0, day="2026-10-06"), _call(30))
    _poll(conn, _at(10, 1, day="2026-10-06"), _call(40))
    assert _stored(conn)[-1][1] == {100.0: {"call": 1000.0, "put": 0.0, "net": 1000.0}}


def test_switching_it_off_drops_the_state_and_on_again_resumes_from_the_store(
        monkeypatch, conn):
    """Hours of unwatched volume must not be booked at one minute's mark."""
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _call(1000))
    _poll(conn, _at(10, 1), _call(1100))                   # $10,000
    _switch(monkeypatch, on=False)
    _poll(conn, _at(10, 2), _call(5000))
    assert tp._S["date"] is None and len(_stored(conn)) == 2
    _switch(monkeypatch)
    _poll(conn, _at(10, 3), _call(5000))                   # sets baselines again
    _poll(conn, _at(10, 4), _call(5005))                   # $500
    assert _stored(conn)[-1][1][100.0]["call"] == 10_500.0


def test_a_failed_write_never_raises_and_the_next_row_carries_it(monkeypatch, conn):
    _switch(monkeypatch)
    seen = _degrades(monkeypatch)
    store = _Store("insert_snapshot")
    _poll(conn, _at(10, 0), _call(1000))
    _poll(conn, _at(10, 1), _call(1100), store=store)      # this write fails
    _poll(conn, _at(10, 2), _call(1100), store=store)
    assert seen == ["options.traded_premium.write"]
    assert [g for _ts, g, _calls, _puts in _stored(conn)] == [
        {}, {100.0: {"call": 10_000.0, "put": 0.0, "net": 10_000.0}}]


def test_a_failed_resume_is_retried_and_never_counted_twice(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _call(1000))
    _poll(conn, _at(10, 1), _call(1100))                   # $10,000 stored
    tp.reset()
    seen = _degrades(monkeypatch)
    store = _Store("latest_grid_row")
    _poll(conn, _at(10, 2), _call(1100), store=store)      # the read fails
    _poll(conn, _at(10, 3), _call(1105), store=store)      # $500 more
    _poll(conn, _at(10, 4), _call(1105), store=store)
    assert seen == ["options.traded_premium.write"]
    assert [g[100.0]["call"] for _ts, g, _calls, _puts in _stored(conn)[2:]] == [
        10_500.0, 10_500.0]


@pytest.mark.parametrize("chain", [None, "x", {}, {"callExpDateMap": 5}])
def test_a_bad_chain_never_raises_and_writes_no_row(monkeypatch, conn, chain):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), chain)
    assert _stored(conn) == []


def test_on_chain_counts_a_failure_and_carries_on(monkeypatch):
    _switch(monkeypatch)
    seen = _degrades(monkeypatch)
    monkeypatch.setattr(tp, "advance", lambda *a, **k: 1 / 0)
    tp.on_chain("SPY", _call(1), _at(10, 0))               # must not raise
    assert seen == ["options.traded_premium.on_chain"]


# ---- the day's check ----------------------------------------------------------

def test_day_check_sets_what_the_chain_showed_beside_what_was_booked(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _call(100))
    _poll(conn, _at(10, 1), _chain({"100.0": [_c("C1", 130, mark=None)]}))
    check = tp.day_check()
    assert check["date"] == MON
    assert check["symbols"]["SPY"]["seen_vol"] == 30.0
    assert check["symbols"]["SPY"]["booked_vol"] == 0.0
    assert check["pass"]["polls"] == 2 and check["write"]["polls"] == 2


def test_the_days_check_is_logged_once_after_the_regular_close(monkeypatch, conn, caplog):
    _switch(monkeypatch)
    caplog.set_level(logging.INFO, logger=tp.log.name)

    def lines():
        return [r.getMessage() for r in caplog.records if "day_check" in r.getMessage()]

    _poll(conn, _at(14, 58), _call(1000, 2.0))
    _poll(conn, _at(14, 59), _chain({"100.0": [_c("C1", 1010, 2.0, last=2.5)]}))
    assert lines() == []
    _poll(conn, _at(15, 1), _call(1010, 2.0))
    _poll(conn, _at(15, 2), _call(1010, 2.0))
    got = lines()
    assert len(got) == 2                                   # everything, then SPY
    assert got[0].startswith(f"traded_premium day_check {MON} all: 1 symbols; "
                             "booked 10 of 10 contracts seen (100.0%)")
    assert got[1].startswith(f"traded_premium day_check {MON} SPY: since 14:58; ")
    assert "at last $2,500 against $2,000 at the mark" in got[1]
