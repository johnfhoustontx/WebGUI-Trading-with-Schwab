"""The /ES and /NQ chain feed's consumer: a captured snapshot becomes stored rows
and a cached chain, or a refusal with a reason a person can read.

Runs on the fake bus (one Redis per test, as in prod) and a real SQLite file in
``tmp_path`` standing in for ``gex_history.db``: the conftest's in-memory stand-in
would hand every call a fresh empty database and nothing could be read back.
Design: docs/plans/2026-10-31-futures-0dte-chain-feed-design.md, sections 5.6, 5.9.
"""
import ast
import contextlib
import importlib
import io
import json
import pathlib
import sqlite3
import types
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.options_svc import compute  # noqa: F401  puts options-scanner on sys.path
from services.options_svc import futures_chain_feed as feed
from shared import futures_chain
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command

import gex_history_db as gh

_CT = ZoneInfo("America/Chicago")
MON = date(2026, 11, 2)
TUE = date(2026, 11, 3)
T = datetime(2026, 11, 2, 9, 0, tzinfo=_CT).timestamp()       # Monday 09:00 CT
SERIES = {"ES": "E1AX26", "NQ": "Q1AX26"}
MULTIPLIER = {"ES": 50, "NQ": 20}
PRICE = {"ES": 7600.0, "NQ": 26000.0}
STEP = {"ES": 5, "NQ": 10}


def ct(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_CT)


# ── the clock ────────────────────────────────────────────────────────────────

_CLOCK = {"fixed": None, "now": T + 5}


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    """A capture is handled five seconds after it was taken, unless a test freezes
    the clock (``frozen_clock``) to make it arrive early, late or from the future."""
    _CLOCK.update(fixed=None, now=T + 5)
    monkeypatch.setattr(feed, "_now", _read_clock)
    yield


def _read_clock():
    return _CLOCK["fixed"] if _CLOCK["fixed"] is not None else _CLOCK["now"]


def now():
    return _read_clock()


@contextlib.contextmanager
def frozen_clock(when):
    _CLOCK["fixed"] = when.timestamp() if isinstance(when, datetime) else float(when)
    try:
        yield
    finally:
        _CLOCK["fixed"] = None


def handle(bus, command):
    if _CLOCK["fixed"] is None and isinstance(command.args.get("captured_at"), (int, float)):
        _CLOCK["now"] = command.args["captured_at"] + 5
    feed.handle(bus, command)


# ── builders ─────────────────────────────────────────────────────────────────

def snapshot(*, instrument="ES", captured=T, price=None, expiry=MON, n=2, volume=5,
             open_int=10, iv="15%"):
    """One instrument's capture as the CLI puts it on the stream: ``n`` contracts
    (calls and puts alternating) around the price, each marked at 10 points (a call
    and a put at the money agree on the future) and reading a volatility."""
    price = PRICE[instrument] if price is None else price
    series = SERIES[instrument]
    underlying = f"/{instrument}Z26:XCME"
    text = (f"{underlying} 1/{MULTIPLIER[instrument]} {futures_chain.date_text(expiry)} "
            f"({expiry:%A}) /{series}:XCME 7600 CALL")
    contracts = []
    for i in range(n):
        strike = round(PRICE[instrument]) + STEP[instrument] * (i // 2 - n // 4)
        contracts.append({"series": series, "side": "C" if i % 2 == 0 else "P",
                          "strike": strike, "mark": 10.0, "bid": None, "ask": None,
                          "volume": volume, "open_int": open_int, "iv": iv, "delta": None})
    return {"instrument": instrument, "expiry": expiry.isoformat(),
            "captured_at": captured, "descriptions": {series: text},
            "underlying_symbol": underlying, "underlying_price": price,
            "contracts": contracts}


def cmd(args):
    return Command(type="snapshot", args=args)


def cmd_type(kind, args):
    return Command(type=kind, args=args)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def bus():
    reset_fake_bus()
    return Bus(fake=True)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real ``gex_history.db`` file for this test; its path."""
    path = tmp_path / "gex_history.db"
    monkeypatch.setattr(gh, "connect", lambda read_only=False: sqlite3.connect(str(path)))
    return path


@pytest.fixture
def degrade_count():
    _degrade.reset()
    yield lambda: _degrade.counts().get(feed.AREA, 0)
    _degrade.reset()


def rows(db):
    if not db.exists():
        return 0
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    except sqlite3.OperationalError:          # nothing was ever written: no table
        return 0
    finally:
        conn.close()


def views_at(db, symbol, ts):
    conn = sqlite3.connect(str(db))
    try:
        return {v for (v,) in conn.execute(
            "SELECT view FROM snapshots WHERE symbol = ? AND ts = ?", (symbol, ts))}
    finally:
        conn.close()


def ts_floor_minute(when):
    return int(when) // 60 * 60


def latest(bus, symbol):
    return feed.latest(bus, symbol)


def frozen(bus, symbol):
    return feed.frozen(bus, symbol)


def status(bus):
    return bus.cache_get(futures_chain.STATUS_KEY).payload


# ── a snapshot lands ─────────────────────────────────────────────────────────

def test_a_good_snapshot_writes_all_views_at_the_capture_time(bus, db):
    handle(bus, cmd(snapshot(captured=T)))
    assert views_at(db, "FOP.ES", ts_floor_minute(T)) == {"gex", "charm", "dex", "vanna", "prem"}


def test_the_rows_are_stamped_with_the_capture_minute_not_the_clock(bus, db):
    when = T + 17                 # 09:00:17
    handle(bus, cmd(snapshot(captured=when)))
    assert views_at(db, "FOP.ES", ts_floor_minute(when)) != set()
    assert views_at(db, "FOP.ES", ts_floor_minute(when + 60)) == set()


def test_the_chain_is_cached_for_the_page(bus, db):
    handle(bus, cmd(snapshot(captured=T)))
    got = latest(bus, "FOP.ES")
    assert got["captured_at"] == T and got["expiry"] == "2026-11-02"
    assert got["hours_left"] == pytest.approx(6.0)
    assert list(got["chain"]["callExpDateMap"]) == ["2026-11-02:0"]


def test_nothing_is_cached_for_a_symbol_that_was_never_captured(bus, db):
    assert latest(bus, "FOP.ES") is None and frozen(bus, "FOP.ES") is None


def test_a_good_snapshot_is_reported_in_the_status_view(bus, db):
    handle(bus, cmd(snapshot(captured=T, n=4)))
    entry = status(bus)["FOP.ES"]
    assert (entry["captured_at"], entry["expiry"], entry["listed"]) == (T, "2026-11-02", 4)
    assert entry["with_gamma"] == 4 and entry["reason"] == "" and entry["repeats"] == 0
    assert entry["refused_at"] is None


def test_the_feed_writes_only_its_own_keys(bus, db):
    """These symbols never reach a public key: every key the consumer writes is
    under the feed's own prefix."""
    handle(bus, cmd(snapshot(captured=T)))
    keys = {k for k in bus._r.keys("*") if not k.startswith("cmd:")}
    assert keys and all(k.startswith("cache:options:futures_chain") for k in keys), keys


# ── a snapshot that cannot be used ───────────────────────────────────────────

def test_an_invalid_payload_is_not_published_and_counts_a_degrade(bus, db, degrade_count):
    handle(bus, cmd({"instrument": "ES", "contracts": "rubbish"}))
    assert latest(bus, "FOP.ES") is None and degrade_count() == 1
    assert rows(db) == 0


def test_an_invalid_payload_for_a_known_instrument_says_so_in_the_status(bus, db):
    handle(bus, cmd({"instrument": "ES", "contracts": "rubbish"}))
    assert "could not be read" in status(bus)["FOP.ES"]["reason"]


def test_an_invalid_payload_naming_no_instrument_writes_no_status(bus, db, degrade_count):
    handle(bus, cmd({"contracts": []}))
    assert bus.cache_get(futures_chain.STATUS_KEY) is None and degrade_count() == 1


def test_an_unknown_instrument_is_a_degrade_and_writes_nothing(bus, db, degrade_count):
    handle(bus, cmd(snapshot() | {"instrument": "CL"}))
    assert degrade_count() == 1 and rows(db) == 0
    assert bus.cache_get(futures_chain.STATUS_KEY) is None


def test_a_refusal_is_reported_with_its_reason_and_writes_nothing(bus, db):
    handle(bus, cmd(snapshot(price=0)))
    assert "price" in status(bus)["FOP.ES"]["reason"] and rows(db) == 0
    assert latest(bus, "FOP.ES") is None


def test_a_refusal_keeps_the_last_good_chain_and_when_it_was_refused(bus, db):
    handle(bus, cmd(snapshot(captured=T)))
    handle(bus, cmd(snapshot(captured=T + 300, price=0)))
    entry = status(bus)["FOP.ES"]
    assert latest(bus, "FOP.ES")["captured_at"] == T
    assert entry["captured_at"] == T and entry["refused_at"] == T + 305
    assert entry["reason"]


def test_a_good_snapshot_clears_an_earlier_refusal(bus, db):
    handle(bus, cmd(snapshot(captured=T, price=0)))
    handle(bus, cmd(snapshot(captured=T + 300)))
    assert status(bus)["FOP.ES"]["reason"] == ""


def test_an_old_snapshot_is_dropped_on_age(bus, db):
    with frozen_clock(T + 5):
        handle(bus, cmd(snapshot(captured=now() - 901)))
    assert rows(db) == 0 and latest(bus, "FOP.ES") is None
    assert "old when it arrived" in status(bus)["FOP.ES"]["reason"]


def test_a_snapshot_at_the_age_limit_is_taken(bus, db):
    with frozen_clock(T + 900):
        handle(bus, cmd(snapshot(captured=T)))
    assert latest(bus, "FOP.ES") is not None


def test_a_snapshot_from_the_future_is_dropped(bus, db):
    with frozen_clock(T):
        handle(bus, cmd(snapshot(captured=now() + 31)))
    assert rows(db) == 0 and latest(bus, "FOP.ES") is None
    assert "ahead" in status(bus)["FOP.ES"]["reason"]


def test_a_snapshot_a_little_ahead_is_taken(bus, db):
    with frozen_clock(T):
        handle(bus, cmd(snapshot(captured=now() + 30)))
    assert latest(bus, "FOP.ES") is not None


def test_one_instrument_failing_does_not_touch_the_other(bus, db):
    handle(bus, cmd(snapshot(instrument="NQ", price=0)))
    handle(bus, cmd(snapshot(instrument="ES")))
    assert latest(bus, "FOP.ES") and latest(bus, "FOP.NQ") is None
    assert status(bus)["FOP.ES"]["reason"] == "" and status(bus)["FOP.NQ"]["reason"]


def test_the_nq_chain_is_stored_under_its_own_symbol(bus, db):
    handle(bus, cmd(snapshot(instrument="NQ")))
    assert views_at(db, "FOP.NQ", ts_floor_minute(T)) >= {"gex", "prem"}
    assert latest(bus, "FOP.NQ")["chain"]["symbol"] == "FOP.NQ"


def test_a_chain_with_no_volatility_is_refused_in_words(bus, db):
    handle(bus, cmd(snapshot(iv="N/A")))
    assert "volatility" in status(bus)["FOP.ES"]["reason"] and rows(db) == 0


def test_a_capture_that_stamps_no_time_is_refused_not_stored(bus, db):
    with frozen_clock(T):
        handle(bus, cmd(snapshot(captured=float("nan"))))
    assert rows(db) == 0 and status(bus)["FOP.ES"]["reason"]


@pytest.mark.parametrize("stamp", [float("inf"), float("-inf")])
def test_an_infinite_capture_time_is_refused_with_a_reason_not_a_crash(bus, db, stamp):
    with frozen_clock(T):
        handle(bus, cmd(snapshot(captured=stamp)))
    assert rows(db) == 0 and latest(bus, "FOP.ES") is None
    assert "not a time" in status(bus)["FOP.ES"]["reason"]


def test_a_capture_time_that_is_not_a_number_is_refused_in_words(bus, db):
    with frozen_clock(T):
        handle(bus, cmd(snapshot(captured=float("nan"))))
    assert "not a time" in status(bus)["FOP.ES"]["reason"]


@pytest.mark.parametrize("seconds", [float("inf"), float("-inf"), float("nan"), -5, 0, 1e300])
def test_a_length_of_time_can_always_be_worded(seconds):
    assert isinstance(feed._span(seconds), str) and feed._span(seconds)


# ── Review Focus 1: a feed that has stopped moving ───────────────────────────

def test_identical_values_repeating_are_marked_as_not_moving(bus, db):
    for i in range(3):
        handle(bus, cmd(snapshot(captured=T + 300 * i)))
    assert status(bus)["FOP.ES"]["repeats"] == 3
    assert "not changing" in status(bus)["FOP.ES"]["reason"]


def test_two_identical_captures_are_not_yet_a_frozen_feed(bus, db):
    for i in range(2):
        handle(bus, cmd(snapshot(captured=T + 300 * i)))
    assert status(bus)["FOP.ES"]["repeats"] == 2 and status(bus)["FOP.ES"]["reason"] == ""


def test_a_changed_value_clears_the_repeat(bus, db):
    for i in range(3):
        handle(bus, cmd(snapshot(captured=T + 300 * i)))
    handle(bus, cmd(snapshot(captured=T + 900, price=7601)))
    assert status(bus)["FOP.ES"]["repeats"] == 0 and not status(bus)["FOP.ES"]["reason"]


def test_a_change_in_volume_alone_clears_the_repeat(bus, db):
    for i in range(3):
        handle(bus, cmd(snapshot(captured=T + 300 * i)))
    handle(bus, cmd(snapshot(captured=T + 900, volume=6)))
    assert status(bus)["FOP.ES"]["repeats"] == 0


def test_a_repeating_capture_is_still_stored(bus, db):
    for i in range(3):
        handle(bus, cmd(snapshot(captured=T + 300 * i)))
    assert views_at(db, "FOP.ES", ts_floor_minute(T + 600)) != set()


def test_the_repeat_count_is_how_many_captures_in_a_row_when_the_limit_is_raised(
        bus, db, monkeypatch):
    from shared import futures_chain_config as cfg
    real = cfg.capture
    monkeypatch.setattr(cfg, "capture", lambda: {**real(), "frozen_repeats": 5})
    for i in range(4):
        handle(bus, cmd(snapshot(captured=T + 300 * i)))
    assert status(bus)["FOP.ES"]["repeats"] == 4 and status(bus)["FOP.ES"]["reason"] == ""


def test_a_new_session_does_not_continue_the_last_sessions_repeats(bus, db):
    for i in range(3):
        handle(bus, cmd(snapshot(captured=T + 300 * i)))
    handle(bus, cmd(snapshot(captured=T + 8000, expiry=TUE)))
    assert status(bus)["FOP.ES"]["repeats"] == 0


# ── Review Focus 2: a half-filled capture ────────────────────────────────────

def test_a_half_filled_capture_is_refused_against_the_last_count(bus, db):
    handle(bus, cmd(snapshot(captured=T, n=300)))
    handle(bus, cmd(snapshot(captured=T + 300, n=30)))
    assert latest(bus, "FOP.ES")["captured_at"] == T
    assert "contracts" in status(bus)["FOP.ES"]["reason"]


def test_a_capture_just_inside_the_share_is_taken(bus, db):
    handle(bus, cmd(snapshot(captured=T, n=100)))
    handle(bus, cmd(snapshot(captured=T + 300, n=80)))          # 80 percent of 100
    assert latest(bus, "FOP.ES")["captured_at"] == T + 300


def test_a_capture_just_under_the_share_is_refused(bus, db):
    handle(bus, cmd(snapshot(captured=T, n=100)))
    handle(bus, cmd(snapshot(captured=T + 300, n=79)))
    assert latest(bus, "FOP.ES")["captured_at"] == T


def test_a_new_session_is_not_compared_with_the_last_sessions_count(bus, db):
    handle(bus, cmd(snapshot(captured=T, expiry=MON, n=300)))
    handle(bus, cmd(snapshot(captured=T + 8000, expiry=TUE, n=100)))
    assert latest(bus, "FOP.ES")["expiry"] == TUE.isoformat()


def test_a_resized_sheet_is_taken_when_the_next_capture_repeats_its_count(bus, db):
    """Narrowing the strike width drops the count on purpose. A half-filled sheet
    does not repeat the same short count; a resized one does."""
    handle(bus, cmd(snapshot(captured=T, n=300)))
    handle(bus, cmd(snapshot(captured=T + 300, n=100)))
    refused = status(bus)["FOP.ES"]
    assert latest(bus, "FOP.ES")["captured_at"] == T
    assert refused["refused_listed"] == 100 and "will be accepted" in refused["reason"]
    assert "100 contracts" in refused["reason"]
    handle(bus, cmd(snapshot(captured=T + 600, n=100)))
    assert latest(bus, "FOP.ES")["captured_at"] == T + 600
    taken = status(bus)["FOP.ES"]
    assert taken["listed"] == 100 and taken["reason"] == "" and taken["refused_listed"] is None


def test_the_new_count_is_the_baseline_after_a_resize(bus, db):
    handle(bus, cmd(snapshot(captured=T, n=300)))
    handle(bus, cmd(snapshot(captured=T + 300, n=100)))
    handle(bus, cmd(snapshot(captured=T + 600, n=100)))
    handle(bus, cmd(snapshot(captured=T + 900, n=30)))          # half filled against 100
    assert latest(bus, "FOP.ES")["captured_at"] == T + 600


def test_two_different_short_counts_are_not_a_resize(bus, db):
    handle(bus, cmd(snapshot(captured=T, n=300)))
    handle(bus, cmd(snapshot(captured=T + 300, n=100)))
    handle(bus, cmd(snapshot(captured=T + 600, n=60)))          # still filling
    assert latest(bus, "FOP.ES")["captured_at"] == T
    assert status(bus)["FOP.ES"]["refused_listed"] == 60
    handle(bus, cmd(snapshot(captured=T + 900, n=100)))         # 100 was not the last refusal
    assert latest(bus, "FOP.ES")["captured_at"] == T


def test_a_refusal_that_is_not_a_count_leaves_the_refused_count_alone(bus, db):
    handle(bus, cmd(snapshot(captured=T, n=300)))
    handle(bus, cmd(snapshot(captured=T + 300, n=100)))
    handle(bus, cmd(snapshot(captured=T + 600, n=100, price=0)))
    handle(bus, cmd(snapshot(captured=T + 900, n=100)))
    assert latest(bus, "FOP.ES")["captured_at"] == T + 900


def test_a_full_capture_after_a_short_one_is_taken_and_forgets_the_refused_count(bus, db):
    handle(bus, cmd(snapshot(captured=T, n=300)))
    handle(bus, cmd(snapshot(captured=T + 300, n=100)))
    handle(bus, cmd(snapshot(captured=T + 600, n=300)))
    assert latest(bus, "FOP.ES")["captured_at"] == T + 600
    assert status(bus)["FOP.ES"]["refused_listed"] is None


def test_a_resized_sheet_is_still_remembered_after_a_restart(bus, db):
    handle(bus, cmd(snapshot(captured=T, n=300)))
    handle(bus, cmd(snapshot(captured=T + 300, n=100)))
    importlib.reload(feed)
    feed._now = lambda: T + 605
    feed.handle(Bus(fake=True), cmd(snapshot(captured=T + 600, n=100)))
    assert latest(bus, "FOP.ES")["captured_at"] == T + 600


def test_the_last_count_survives_a_restart(bus, db):
    """The previous capture's figures live in Redis, so a restarted service refuses
    a half-filled capture exactly as a running one does."""
    handle(bus, cmd(snapshot(captured=T, n=300)))
    importlib.reload(feed)                       # a new process: no module state
    feed._now = lambda: T + 305
    feed.handle(Bus(fake=True), cmd(snapshot(captured=T + 300, n=30)))
    assert latest(bus, "FOP.ES")["captured_at"] == T
    assert "contracts" in status(bus)["FOP.ES"]["reason"]


# ── Review Focus 3: out of order and late ────────────────────────────────────

def test_an_older_capture_never_replaces_a_newer_one_in_the_cache(bus, db):
    handle(bus, cmd(snapshot(captured=T + 300)))
    handle(bus, cmd(snapshot(captured=T)))
    assert latest(bus, "FOP.ES")["captured_at"] == T + 300


def test_an_older_capture_writes_no_row(bus, db):
    handle(bus, cmd(snapshot(captured=T + 300)))
    before = rows(db)
    handle(bus, cmd(snapshot(captured=T)))
    assert rows(db) == before


def test_the_same_capture_twice_is_taken_once(bus, db):
    handle(bus, cmd(snapshot(captured=T)))
    before = (rows(db), bus.cache_version(futures_chain.chain_key("FOP.ES")))
    handle(bus, cmd(snapshot(captured=T)))
    assert (rows(db), bus.cache_version(futures_chain.chain_key("FOP.ES"))) == before


def test_a_late_arrival_keeps_its_own_expiry(bus, db):
    with frozen_clock(ct(2026, 11, 2, 15, 1)):
        handle(bus, cmd(snapshot(captured=ct(2026, 11, 2, 14, 59).timestamp(), expiry=MON)))
    assert latest(bus, "FOP.ES")["expiry"] == "2026-11-02"


def test_the_frozen_key_keeps_the_last_capture_before_the_cutoff(bus, db):
    handle(bus, cmd(snapshot(captured=ct(2026, 11, 2, 13, 55).timestamp())))
    handle(bus, cmd(snapshot(captured=ct(2026, 11, 2, 14, 30).timestamp())))
    assert frozen(bus, "FOP.ES")["captured_at"] == ct(2026, 11, 2, 13, 55).timestamp()
    assert latest(bus, "FOP.ES")["captured_at"] == ct(2026, 11, 2, 14, 30).timestamp()


def test_the_frozen_key_takes_a_capture_exactly_at_the_cutoff(bus, db):
    handle(bus, cmd(snapshot(captured=ct(2026, 11, 2, 14, 0).timestamp())))
    assert frozen(bus, "FOP.ES") is not None


def test_no_frozen_key_when_every_capture_is_too_close_to_expiry(bus, db):
    handle(bus, cmd(snapshot(captured=ct(2026, 11, 2, 14, 30).timestamp())))
    assert frozen(bus, "FOP.ES") is None


def test_the_frozen_cutoff_comes_from_the_config(bus, db, monkeypatch):
    from shared import futures_chain_config as cfg
    real = cfg.capture
    monkeypatch.setattr(cfg, "capture", lambda: {**real(), "frozen_before_expiry_min": 10})
    handle(bus, cmd(snapshot(captured=ct(2026, 11, 2, 14, 30).timestamp())))
    assert frozen(bus, "FOP.ES") is not None


# ── the write path ───────────────────────────────────────────────────────────

def test_each_snapshot_gets_its_own_engine(bus, db, monkeypatch):
    """The engine keeps the last expiry it saw in instance state: one shared
    between two instruments, or two sessions, would price against the wrong one."""
    from gamma_tool import GammaEngine
    made = []

    class Counting(GammaEngine):
        def __init__(self):
            super().__init__()
            made.append(self)

    real = feed._engines
    monkeypatch.setattr(feed, "_engines", lambda: (*real()[:2], Counting))
    handle(bus, cmd(snapshot(instrument="ES", captured=T)))
    handle(bus, cmd(snapshot(instrument="NQ", captured=T)))
    handle(bus, cmd(snapshot(instrument="ES", captured=T + 300)))
    assert len(made) == 3 and len({id(e) for e in made}) == 3


def test_a_failing_store_write_is_a_degrade_a_reason_and_no_chain(bus, db, degrade_count,
                                                                  monkeypatch):
    def broken(adapted):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(feed, "_store", broken)
    handle(bus, cmd(snapshot(captured=T)))
    assert degrade_count() == 1 and latest(bus, "FOP.ES") is None
    assert "store" in status(bus)["FOP.ES"]["reason"]


def test_a_store_that_wrote_no_gamma_row_is_a_reason_and_no_chain(bus, db, monkeypatch):
    monkeypatch.setattr(feed, "_store", lambda adapted: False)
    handle(bus, cmd(snapshot(captured=T)))
    assert latest(bus, "FOP.ES") is None and "gamma" in status(bus)["FOP.ES"]["reason"]


def test_the_store_reports_whether_the_gamma_row_landed(bus, db, monkeypatch):
    """``derive_minute`` logs and skips a chain it cannot price instead of raising,
    so the write is only known good once the row is read back."""
    gex_collector, *_rest = feed._engines()
    monkeypatch.setattr(gex_collector, "derive_minute", lambda *args, **kwargs: None)
    handle(bus, cmd(snapshot(captured=T)))
    assert rows(db) == 0 and latest(bus, "FOP.ES") is None
    assert "gamma" in status(bus)["FOP.ES"]["reason"]


def test_a_bug_in_the_adapter_is_a_degrade_not_a_crash(bus, db, degrade_count, monkeypatch):
    def broken(snapshot):
        raise ZeroDivisionError("float division by zero")
    monkeypatch.setattr(feed, "build_chain", broken)
    handle(bus, cmd(snapshot(captured=T)))
    assert degrade_count() == 1 and latest(bus, "FOP.ES") is None
    assert status(bus)["FOP.ES"]["reason"]


# ── the PC's own report, and the stream's other commands ─────────────────────

def test_a_pc_status_lands_in_the_status_view(bus):
    handle(bus, cmd_type("pc_status", {"reason": "ThinkorSwim logged out", "at": T}))
    assert status(bus)["pc"] == {"reason": "ThinkorSwim logged out", "at": T}


def test_a_pc_status_with_no_time_is_stamped_on_arrival(bus):
    handle(bus, cmd_type("pc_status", {"reason": ""}))
    assert status(bus)["pc"] == {"reason": "", "at": T + 5}


def test_a_pc_status_is_cut_to_a_readable_length_and_never_a_non_string(bus):
    handle(bus, cmd_type("pc_status", {"reason": "x" * 5000, "at": T}))
    assert len(status(bus)["pc"]["reason"]) == 300
    handle(bus, cmd_type("pc_status", {"reason": ["not", "text"], "at": "yesterday"}))
    assert status(bus)["pc"] == {"reason": "", "at": T + 5}


def test_a_pc_status_leaves_the_instruments_alone(bus, db):
    handle(bus, cmd(snapshot(captured=T)))
    handle(bus, cmd_type("pc_status", {"reason": "Excel did not start", "at": T}))
    assert status(bus)["FOP.ES"]["captured_at"] == T


def test_a_snapshot_leaves_the_pc_report_alone(bus, db):
    handle(bus, cmd_type("pc_status", {"reason": "Excel did not start", "at": T}))
    handle(bus, cmd(snapshot(captured=T)))
    assert status(bus)["pc"]["reason"] == "Excel did not start"


def test_an_unknown_command_does_nothing(bus, db):
    handle(bus, cmd_type("rescan", {"symbol": "SPY"}))
    handle(bus, cmd_type("paper_create", {}))
    assert bus.cache_get(futures_chain.STATUS_KEY) is None and rows(db) == 0


# ── from the CLI, through the stream, to the handler ─────────────────────────

def test_what_the_cli_queues_is_what_the_consumer_stores(bus, db):
    """The whole path on the fake bus: the ingest command puts the snapshot on the
    stream as JSON, the consumer group reads it back, the handler stores it."""
    from tools import futures_chain_cli
    batch = {"snapshots": [snapshot(captured=T), snapshot(instrument="NQ", captured=T)],
             "status": {"reason": "", "at": T}}
    assert futures_chain_cli.main(["ingest"], stdin=io.StringIO(json.dumps(batch)),
                                  stdout=io.StringIO()) == 0
    read = bus.consume_commands(futures_chain.STREAM, "options-svc", "test", block_ms=10)
    assert [c.type for _id, c in read] == ["snapshot", "snapshot", "pc_status"]
    for _id, command in read:
        handle(bus, command)
    assert latest(bus, "FOP.ES") and latest(bus, "FOP.NQ")
    assert status(bus)["pc"] == {"reason": "", "at": T}


# ── no Schwab, no proxy ──────────────────────────────────────────────────────

@pytest.fixture
def network_guard(monkeypatch):
    """Every outbound HTTP attempt, on the three stacks the root conftest guards."""
    import urllib.request

    import requests.adapters

    guard = types.SimpleNamespace(calls=[])

    def refuse(*args, **kwargs):
        guard.calls.append(args)
        raise ConnectionError("no network in this test")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    return guard


def test_no_schwab_or_proxy_call_is_made(bus, db, network_guard):
    handle(bus, cmd(snapshot(captured=T)))
    handle(bus, cmd(snapshot(instrument="NQ", captured=T)))
    assert network_guard.calls == [] and latest(bus, "FOP.NQ") is not None


def _imports(module):
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


def test_the_consumer_imports_no_client_and_nothing_from_compute():
    names = _imports(feed)
    forbidden = ("requests", "urllib", "httpx", "schwab", "proxy", "anthropic")
    assert not [n for n in names if any(part in n.lower() for part in forbidden)], names
    assert not [n for n in names if n.endswith("compute")]


def test_the_stream_is_the_features_own_and_never_the_options_stream():
    assert futures_chain.STREAM == "cmd:futures_chain"


def test_the_consumer_is_registered_on_the_options_service():
    from services.options_svc import app as options_app
    source = pathlib.Path(options_app.__file__).read_text(encoding="utf-8")
    assert "(futures_chain.STREAM, futures_chain_feed.handle)" in source
