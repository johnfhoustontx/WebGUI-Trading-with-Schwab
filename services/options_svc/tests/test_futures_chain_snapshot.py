"""The /ES and /NQ Dealer Positioning snapshot: built from the stored chain, priced
at the capture, on the session of its expiry, and never by a call to Schwab.

Runs the real ingest (``futures_chain_feed.handle``), the real engine and a real
SQLite file in ``tmp_path`` for ``gex_history.db``, on the fake bus. Design:
docs/plans/2026-10-31-futures-0dte-chain-feed-design.md, sections 5.7 to 5.9, 5.11.
"""
import ast
import datetime as _dt
import json
import pathlib
import re
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from services import _degrade
from services.options_svc import compute, forward_grid, handlers, scheduler
from services.options_svc import futures_chain_feed as feed
from services.options_svc import futures_chain_snapshot as chain_snapshot
from services.options_svc.futures_chain_snapshot import snapshot
from shared import futures_chain, futures_chain_config, public_gamma
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command
from shared.futures_chain import is_feed_symbol

import gamma_tool as gt
import gex_collector
import gex_history_db as gh

_CT = ZoneInfo("America/Chicago")
PRICE = {"ES": 7600.0, "NQ": 26000.0}
SERIES = {"ES": "E1AX26", "NQ": "Q1AX26"}
MULTIPLIER = {"ES": 50, "NQ": 20}
STEP = {"ES": 5, "NQ": 10}


def ct(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_CT)


def fill_step() -> int:
    return futures_chain_config.capture()["fill_step_sec"]


# ── builders ─────────────────────────────────────────────────────────────────

def capture_args(when, *, instrument="ES", n=8, volume=5, open_int=10, iv="15%"):
    """One capture as the CLI puts it on the stream, taken at ``when`` for the
    session ``when`` belongs to."""
    expiry = futures_chain.session_expiry(when)
    series, underlying = SERIES[instrument], f"/{instrument}Z26:XCME"
    text = (f"{underlying} 1/{MULTIPLIER[instrument]} {futures_chain.date_text(expiry)} "
            f"({expiry:%A}) /{series}:XCME 7600 CALL")
    contracts = []
    for i in range(n):
        strike = round(PRICE[instrument]) + STEP[instrument] * (i // 2 - n // 4)
        contracts.append({"series": series, "side": "C" if i % 2 == 0 else "P",
                          "strike": strike, "mark": 10.0, "bid": None, "ask": None,
                          "volume": volume, "open_int": open_int, "iv": iv, "delta": None})
    return {"instrument": instrument, "expiry": expiry.isoformat(),
            "captured_at": when.timestamp(), "descriptions": {series: text},
            "underlying_symbol": underlying, "underlying_price": PRICE[instrument],
            "contracts": contracts}


def cmd(args):
    return Command(type="snapshot", args=args)


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
    conn = sqlite3.connect(str(path))
    gh.init_schema(conn)
    conn.commit()
    conn.close()
    compute.reset_gamma_history_memo()
    yield path
    compute.reset_gamma_history_memo()


@pytest.fixture
def write_captures(bus, monkeypatch):
    """Ingest captures through the real consumer, each arriving seconds after it
    was taken. The page's symbol is not one of these unless a test says so."""
    def _write(*whens, instrument="ES", **kwargs):
        for when in whens:
            monkeypatch.setattr(feed, "_now", lambda at=when.timestamp() + 5: at)
            feed.handle(bus, cmd(capture_args(when, instrument=instrument, **kwargs)))
    return _write


class _Spy:
    def __init__(self):
        self.calls = []

    def get_option_chain(self, symbol, **kwargs):
        self.calls.append(symbol)
        raise AssertionError(f"a chain was requested for {symbol}")


@pytest.fixture
def proxy_spy(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(compute._proxy, "schwab_py_client", spy)
    return spy


class _CalcSpy:
    """Records the LAST call: the ingest prices too, and the snapshot comes after."""
    now = hours_left = "never called"


@pytest.fixture
def spy_calc_all(monkeypatch):
    spy, real = _CalcSpy(), gt.GammaEngine.calc_all_from_chain

    def _calc(self, chain, use_volume=False, now=None, hours_left=None):
        spy.now, spy.hours_left = now, hours_left
        return real(self, chain, use_volume=use_volume, now=now, hours_left=hours_left)
    monkeypatch.setattr(gt.GammaEngine, "calc_all_from_chain", _calc)
    return spy


@pytest.fixture
def spy_refresh(monkeypatch):
    spy = _Spy()
    monkeypatch.setattr(handlers, "refresh_gamma", lambda bus, symbol: spy.calls.append(symbol))
    return spy


@pytest.fixture
def show_true(monkeypatch):
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "show_in_dropdown": True})


@pytest.fixture
def repeated_rows(monkeypatch):
    """A step below the interval, which an operator can choose: the shipped step
    repeats nothing, so two captures would not exercise the filled path."""
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "fill_step_sec": 100})


def set_current_gamma_symbol(bus, symbol):
    bus.cache_set(handlers.CACHE_GAMMA, {"symbol": symbol, "views": {}})


def history_ts(snap, view="GEX"):
    return [r[0] for r in snap["views"][view]["history"]]


# ── the session's rows ───────────────────────────────────────────────────────

@pytest.fixture
def from_the_evening(monkeypatch):
    """A start of 17:00, which puts a session on two calendar dates."""
    monkeypatch.setattr(futures_chain_config, "session",
                        lambda: {"open_ct": "17:00", "close_ct": "15:00"})


def test_rows_before_the_capture_starts_are_left_out(bus, db, write_captures):
    """A capture taken by hand overnight is kept in the store and is not drawn."""
    write_captures(ct(2026, 11, 2, 5, 55), ct(2026, 11, 2, 6, 0))
    ts = history_ts(snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 6, 1)))
    assert ts == [int(ct(2026, 11, 2, 6, 0).timestamp())]


def test_snapshot_loads_rows_from_both_calendar_dates(bus, db, write_captures, from_the_evening):
    write_captures(ct(2026, 11, 1, 18, 0), ct(2026, 11, 2, 9, 0))
    ts = set(history_ts(snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1))))
    assert int(ct(2026, 11, 1, 18, 0).timestamp()) in ts
    assert int(ct(2026, 11, 2, 9, 0).timestamp()) in ts


def test_rows_of_the_session_before_are_left_out(bus, db, write_captures, from_the_evening):
    write_captures(ct(2026, 10, 30, 14, 0), ct(2026, 11, 1, 18, 0))
    ts = history_ts(snapshot(bus, "FOP.ES", now=ct(2026, 11, 1, 18, 1)))
    assert ts == [int(ct(2026, 11, 1, 18, 0).timestamp())]


def test_rows_are_filled_to_the_step_and_a_gap_stays(bus, db, write_captures):
    start = ct(2026, 11, 2, 9, 0)
    write_captures(start, ct(2026, 11, 2, 9, 5), ct(2026, 11, 2, 10, 0))
    ts = history_ts(snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 10, 1)))
    assert int(start.timestamp()) + fill_step() in ts
    assert int(ct(2026, 11, 2, 9, 30).timestamp()) not in ts


def test_the_store_keeps_one_row_per_capture(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0), ct(2026, 11, 2, 9, 5))
    snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 6))
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM snapshots WHERE symbol = 'FOP.ES' "
                         "AND view = 'gex'").fetchone()[0]
    conn.close()
    assert count == 2


def test_the_shipped_step_repeats_no_row(bus, db, write_captures):
    first = ct(2026, 11, 2, 9, 0)
    write_captures(first, first + _dt.timedelta(seconds=360), first + _dt.timedelta(seconds=600))
    snap = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 11))
    assert [ts - int(first.timestamp()) for ts in history_ts(snap)] == [0, 360, 600]


def test_the_level_tracks_stay_parallel_with_the_filled_rows(bus, db, write_captures, repeated_rows):
    write_captures(ct(2026, 11, 2, 9, 0), ct(2026, 11, 2, 9, 5))
    gex = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 6))["views"]["GEX"]
    assert len(gex["history"]) > 2
    assert len(gex["levels"]["flip"]) == len(gex["history"])


def test_the_hedge_panel_is_filled_onto_the_heatmaps_columns(bus, db, write_captures, repeated_rows):
    write_captures(ct(2026, 11, 2, 9, 0), ct(2026, 11, 2, 9, 5))
    snap = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 6))
    hedge = [row["ts"] for row in snap["hedge_history"]]
    assert len(hedge) > 2 and set(hedge) <= set(history_ts(snap))


def test_flow_and_ladder_read_the_whole_session_unfilled(bus, db, write_captures, from_the_evening):
    evening, morning = ct(2026, 11, 1, 18, 0), ct(2026, 11, 2, 9, 0)
    write_captures(evening, morning)
    snap = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1))
    wanted = [int(evening.timestamp()), int(morning.timestamp())]
    assert [row["ts"] for row in snap["flow"]] == wanted
    assert [row["ts"] for row in snap["prem_ladder"]] == wanted


def test_a_feed_snapshot_does_not_disturb_another_symbols_memo(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    conn = sqlite3.connect(str(db))
    compute._history_rows_incremental(gh, conn, "$SPX", "gex", date(2026, 11, 2))
    conn.close()
    memo_date = compute._HIST_MEMO["date"]
    snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1))
    assert compute._HIST_MEMO["date"] == memo_date
    assert ("$SPX", "gex") in compute._HIST_MEMO["data"]
    assert not [key for key in compute._HIST_MEMO["data"] if is_feed_symbol(key[0])]


# ── priced at the capture ────────────────────────────────────────────────────

def test_priced_at_the_capture_not_the_wall_clock(bus, db, write_captures, spy_calc_all):
    write_captures(ct(2026, 11, 1, 18, 0))
    snapshot(bus, "FOP.ES", now=ct(2026, 11, 1, 18, 4))
    assert spy_calc_all.now == ct(2026, 11, 1, 18, 0)
    assert spy_calc_all.hours_left == pytest.approx(21.0)


def test_building_a_snapshot_never_reads_the_market_clock(bus, db, write_captures, monkeypatch):
    """Every read of the clock in the path is a bug: the figures are as of the
    capture. ``now`` decides only the feed block's words."""
    write_captures(ct(2026, 11, 2, 9, 0), ct(2026, 11, 2, 9, 5))

    def _boom(*_args, **_kwargs):
        raise AssertionError("the market clock was read")

    class _NoNow(datetime):
        now = classmethod(_boom)

    monkeypatch.setattr(scheduler, "_market_now", _boom)
    monkeypatch.setattr(scheduler, "active_session_date", _boom)
    monkeypatch.setattr(compute, "_display_session_date", _boom)
    monkeypatch.setattr(compute, "_rth_bounds", _boom)
    monkeypatch.setattr(gt, "datetime", _NoNow)
    monkeypatch.setattr(gh._dt, "datetime", _NoNow)
    snap = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 6))
    assert snap["views"]["GEX"]["data"] and snap["views"]["GEX"]["history"]


def test_the_session_passes_the_stock_days_filter_through(bus, db, write_captures):
    """A row at 07:00 is outside 08:30 to 15:00 and is still shown."""
    write_captures(ct(2026, 11, 2, 7, 0))
    snap = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 7, 1))
    assert history_ts(snap) == [int(ct(2026, 11, 2, 7, 0).timestamp())]


def test_no_term_for_the_feed(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    assert snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1))["term"] == {}


@pytest.mark.parametrize("when", [
    ct(2026, 11, 1, 18, 0),      # Sunday evening, not a trading day
    ct(2026, 11, 2, 18, 0),      # the evening before a Tuesday expiry
    ct(2026, 11, 3, 3, 0),       # overnight
    ct(2026, 11, 3, 8, 29),      # a minute before the stock open
])
def test_forward_marks_are_empty_before_the_expiry_days_stock_open(when):
    assert forward_grid.forward_marks(when, 5) == []


def test_no_projected_band_before_the_expiry_days_stock_open(bus, db, write_captures):
    write_captures(ct(2026, 11, 1, 18, 0))
    gex = snapshot(bus, "FOP.ES", now=ct(2026, 11, 1, 18, 4))["views"]["GEX"]
    assert "forward" not in gex


def test_the_projected_band_starts_at_the_capture_inside_the_stock_window(
        bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    band = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 4))["views"]["GEX"]["forward"]
    assert band["ts"][0] == int(ct(2026, 11, 2, 9, 0).timestamp())


# ── the Desk's row ───────────────────────────────────────────────────────────

def desk_entry(bus, symbol="FOP.ES"):
    return bus.cache_get(futures_chain.DESK_KEY).payload[symbol]


def test_a_capture_writes_the_figures_the_page_shows(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    row = desk_entry(bus)["latest"]
    gex = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1))["views"]["GEX"]
    assert row["captured_at"] == ct(2026, 11, 2, 9, 0).timestamp()
    assert row["expiry"] == "2026-11-02" and row["spot"] == PRICE["ES"]
    assert row["flip"] == gex["flip"]
    assert sorted(w for w in (row["put_wall"], row["call_wall"]) if w) == sorted(gex["walls"])
    assert row["not_moving"] is False


def test_the_last_hour_does_not_replace_the_kept_row(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 13, 55), ct(2026, 11, 2, 14, 30))
    entry = desk_entry(bus)
    assert entry["frozen"]["captured_at"] == ct(2026, 11, 2, 13, 55).timestamp()
    assert entry["latest"]["captured_at"] == ct(2026, 11, 2, 14, 30).timestamp()


def test_each_symbol_keeps_its_own_row(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    write_captures(ct(2026, 11, 2, 9, 1), instrument="NQ")
    assert desk_entry(bus, "FOP.ES")["latest"]["spot"] == PRICE["ES"]
    assert desk_entry(bus, "FOP.NQ")["latest"]["spot"] == PRICE["NQ"]


def test_a_failed_desk_row_costs_the_row_and_not_the_capture(
        bus, db, write_captures, monkeypatch):
    from services.options_svc import futures_chain_desk
    def boom(*a, **k):
        raise RuntimeError("no row")
    monkeypatch.setattr(futures_chain_desk, "publish", boom)
    before = _degrade.total()
    write_captures(ct(2026, 11, 2, 9, 0))
    assert feed.latest(bus, "FOP.ES") is not None
    assert bus.cache_get(futures_chain.DESK_KEY) is None
    assert _degrade.total() == before + 1


def test_the_desk_key_is_not_a_public_one():
    assert futures_chain.DESK_KEY == f"cache:{futures_chain.DESK_VIEW}"
    assert "public" not in futures_chain.DESK_KEY


# ── stale and frozen ─────────────────────────────────────────────────────────

def test_a_current_capture_is_neither_stale_nor_frozen(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    feed_block = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1))["feed"]
    assert feed_block["stale"] is False and feed_block["frozen"] is False
    assert feed_block["label"] == "/ES" and feed_block["expiry"] == "2026-11-02"
    assert feed_block["reason"] == ""


def test_stale_after_the_limit_with_the_numbers_kept_and_marked(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    snap = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 20))
    assert snap["feed"]["stale"] is True and snap["views"]["GEX"]["data"]
    assert "20 minutes" in snap["feed"]["reason"]


def test_the_cause_travels_apart_from_the_age_written_with_the_payload(bus, db, write_captures):
    """The page works out its own age; a sentence of age from when the payload was
    written would contradict it once the payload has sat in the cache."""
    write_captures(ct(2026, 11, 2, 9, 0))
    block = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 20))["feed"]
    assert block["why"] == "" and "20 minutes" in block["reason"]
    feed.handle(bus, Command(type="pc_status",
                             args={"reason": "ThinkorSwim is logged out",
                                   "at": ct(2026, 11, 2, 9, 3).timestamp()}))
    block = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 20))["feed"]
    assert block["why"] == "ThinkorSwim is logged out"


def test_a_capture_too_old_at_build_carries_no_projection(bus, db, write_captures):
    """Nothing about the hours since is known, so nothing is drawn as a forecast."""
    write_captures(ct(2026, 11, 2, 9, 0))
    fresh = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 4))
    assert fresh["views"]["GEX"]["forward"]
    old = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 13, 0))
    assert old["feed"]["stale"] is True
    assert not [v for v, entry in old["views"].items() if "forward" in entry]


def test_the_block_says_where_the_rows_end(bus, db, write_captures):
    write_captures(ct(2026, 11, 6, 13, 55), ct(2026, 11, 6, 14, 55))
    block = snapshot(bus, "FOP.ES", now=ct(2026, 11, 7, 12, 0))["feed"]
    assert block["captured_at"] == ct(2026, 11, 6, 13, 55).timestamp()
    assert block["last_row_at"] == int(ct(2026, 11, 6, 14, 55).timestamp())


def test_another_instruments_reason_stays_off_this_label(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    feed.handle(bus, Command(type="pc_status", args={
        "reason": "ES: every contract reads N/A; NQ: read 100 of 400 contracts tried; not sent",
        "at": ct(2026, 11, 2, 9, 3).timestamp()}))
    from services.options_svc.futures_chain_snapshot import _why_not_capturing
    assert _why_not_capturing(bus, "FOP.ES") == "/ES: every contract reads N/A"
    assert _why_not_capturing(bus, "FOP.NQ") == "/NQ: read 100 of 400 contracts tried; not sent"
    feed.handle(bus, Command(type="pc_status", args={
        "reason": "NQ: read 100 of 400 contracts tried; not sent",
        "at": ct(2026, 11, 2, 9, 3).timestamp()}))
    assert _why_not_capturing(bus, "FOP.ES") == ""
    feed.handle(bus, Command(type="pc_status", args={
        "reason": "ThinkorSwim is logged out", "at": ct(2026, 11, 2, 9, 3).timestamp()}))
    assert _why_not_capturing(bus, "FOP.ES") == "ThinkorSwim is logged out"


def _script_separator():
    """The separator the capture on the PC really joins its reasons with."""
    script = pathlib.Path(__file__).resolve().parents[3] / "tools" / "tos_capture" / "tos_capture.ps1"
    found = re.search(r"\$reason = \$reasons -join '([^']*)'",
                      script.read_text(encoding="utf-8"))
    assert found, "the capture's status line moved"
    return found.group(1)


def test_the_pcs_own_wording_reaches_each_instruments_own_label(bus, db, write_captures):
    """ThinkorSwim logged out overnight, the commonest failure, in the script's exact
    words and its own separator. Each sentence holds a semicolon of its own."""
    write_captures(ct(2026, 11, 2, 9, 0))
    logged_out = "every contract reads N/A; ThinkorSwim may be logged out"
    wire = _script_separator().join([f"ES: {logged_out}", f"NQ: {logged_out}"])
    feed.handle(bus, Command(type="pc_status", args={
        "reason": wire, "at": ct(2026, 11, 2, 9, 3).timestamp()}))
    from services.options_svc.futures_chain_snapshot import _why_not_capturing
    assert _why_not_capturing(bus, "FOP.ES") == f"/ES: {logged_out}"
    assert _why_not_capturing(bus, "FOP.NQ") == f"/NQ: {logged_out}"


def test_the_pcs_mixed_reasons_stay_with_their_own_instrument(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    wire = _script_separator().join([
        "ES: the sheet was still filling when it was written; it is read at the next boundary",
        "NQ: read 100 of 400 contracts tried; not sent"])
    feed.handle(bus, Command(type="pc_status", args={
        "reason": wire, "at": ct(2026, 11, 2, 9, 3).timestamp()}))
    from services.options_svc.futures_chain_snapshot import _why_not_capturing
    assert _why_not_capturing(bus, "FOP.ES") == (
        "/ES: the sheet was still filling when it was written; it is read at the next boundary")
    assert _why_not_capturing(bus, "FOP.NQ") == "/NQ: read 100 of 400 contracts tried; not sent"


def test_an_old_reason_does_not_follow_a_frozen_label_through_a_weekend(bus, db, write_captures):
    """Friday's cause, read on Saturday: it is not why nothing is arriving now."""
    write_captures(ct(2026, 11, 6, 13, 55))
    feed.handle(bus, Command(type="pc_status", args={
        "reason": "ThinkorSwim is logged out", "at": ct(2026, 11, 6, 14, 30).timestamp()}))
    block = snapshot(bus, "FOP.ES", now=ct(2026, 11, 7, 12, 0))["feed"]
    assert block["frozen"] is True and block["stale"] is False
    assert block["why"] == "" and "logged out" not in block["reason"]


def test_a_frozen_label_in_the_next_session_still_says_why(bus, db, write_captures):
    """Capture has started and nothing has arrived: the cause is current."""
    write_captures(ct(2026, 11, 6, 13, 55))
    feed.handle(bus, Command(type="pc_status", args={
        "reason": "ThinkorSwim is logged out", "at": ct(2026, 11, 9, 6, 10).timestamp()}))
    block = snapshot(bus, "FOP.ES", now=ct(2026, 11, 9, 6, 20))["feed"]
    assert block["frozen"] is True and block["stale"] is True
    assert block["why"] == "ThinkorSwim is logged out"


def test_the_levels_and_hedge_stay_parallel_with_the_rows_at_the_shipped_step(bus, db, write_captures):
    """The page lays them out by position, one value per row: at the shipped step,
    with one column per capture, each must have exactly one entry per row."""
    assert fill_step() == futures_chain_config.capture()["interval_sec"]
    start = ct(2026, 11, 2, 9, 0)
    write_captures(*(start + _dt.timedelta(minutes=5 * i) for i in range(6)))
    snap = snapshot(bus, "FOP.ES", now=start + _dt.timedelta(minutes=26))
    for view in ("GEX", "DEX"):
        entry = snap["views"][view]
        rows = entry["history"]
        assert len(rows) == 6, view
        levels = entry.get("levels")
        assert levels, view
        for key, values in levels.items():
            assert len(values) == len(rows), (view, key)
    times = [row[0] for row in snap["views"]["GEX"]["history"]]
    hedge = snap.get("hedge_history") or []
    assert hedge and [row["ts"] for row in hedge] == times


def test_a_changed_interval_changes_the_columns_it_repeats_to(bus, db, write_captures, monkeypatch):
    """Rows at 120 with a step of 300 are one column a capture, not a repeat of 300."""
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "interval_sec": 120, "fill_step_sec": 60})
    start = ct(2026, 11, 2, 9, 0)
    write_captures(start, start + _dt.timedelta(minutes=2))
    rows = snapshot(bus, "FOP.ES", now=start + _dt.timedelta(minutes=3))["views"]["GEX"]["history"]
    assert rows[1][0] - rows[0][0] == 60 and len(rows) == 3
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "interval_sec": 120, "fill_step_sec": 300})
    rows = snapshot(bus, "FOP.ES", now=start + _dt.timedelta(minutes=3))["views"]["GEX"]["history"]
    assert [b[0] - a[0] for a, b in zip(rows, rows[1:])] == [120]


def test_a_stale_snapshot_says_why_the_capture_stopped(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    feed.handle(bus, Command(type="pc_status",
                             args={"reason": "ThinkorSwim is logged out",
                                   "at": ct(2026, 11, 2, 9, 3).timestamp()}))
    reason = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 20))["feed"]["reason"]
    assert "ThinkorSwim is logged out" in reason


def _identical_captures(count):
    """``count`` captures five minutes apart with every value the same, as when
    ThinkorSwim stops updating while Excel goes on answering."""
    return [ct(2026, 11, 2, 9, 0) + _dt.timedelta(minutes=5 * i) for i in range(count)]


def test_captures_that_stopped_changing_are_stale_however_young(bus, db, write_captures):
    whens = _identical_captures(futures_chain_config.capture()["frozen_repeats"])
    write_captures(*whens)
    snap = snapshot(bus, "FOP.ES", now=whens[-1] + _dt.timedelta(minutes=1))
    assert snap["feed"]["stale"] is True and snap["views"]["GEX"]["data"]
    assert "not changing" in snap["feed"]["reason"]


def test_one_capture_fewer_than_the_limit_is_not_stale(bus, db, write_captures):
    whens = _identical_captures(futures_chain_config.capture()["frozen_repeats"] - 1)
    write_captures(*whens)
    block = snapshot(bus, "FOP.ES", now=whens[-1] + _dt.timedelta(minutes=1))["feed"]
    assert block["stale"] is False and block["reason"] == ""


def test_between_sessions_shows_the_frozen_capture_labelled_with_its_time(
        bus, db, write_captures):
    write_captures(ct(2026, 11, 6, 13, 55), ct(2026, 11, 6, 14, 55))
    snap = snapshot(bus, "FOP.ES", now=ct(2026, 11, 7, 12, 0))
    assert snap["feed"]["frozen"] is True and snap["feed"]["stale"] is False
    assert snap["feed"]["captured_at"] == ct(2026, 11, 6, 13, 55).timestamp()
    assert snap["feed"]["expiry"] == "2026-11-06"
    assert snap["views"]["GEX"]["history"]        # the heatmap keeps the session
    assert int(ct(2026, 11, 6, 14, 55).timestamp()) in history_ts(snap)
    assert "13:55" in snap["feed"]["reason"]


def test_a_frozen_snapshot_is_priced_at_its_own_capture(bus, db, write_captures, spy_calc_all):
    write_captures(ct(2026, 11, 6, 13, 55), ct(2026, 11, 6, 14, 55))
    snapshot(bus, "FOP.ES", now=ct(2026, 11, 7, 12, 0))
    assert spy_calc_all.now == ct(2026, 11, 6, 13, 55)
    assert spy_calc_all.hours_left == pytest.approx(65 / 60)


def test_a_frozen_snapshot_projects_no_afternoon(bus, db, write_captures):
    """The rows run past the frozen capture, so a band from it would be drawn over
    a session that has already happened."""
    write_captures(ct(2026, 11, 6, 13, 55), ct(2026, 11, 6, 14, 55))
    snap = snapshot(bus, "FOP.ES", now=ct(2026, 11, 7, 12, 0))
    assert not [v for v, entry in snap["views"].items() if "forward" in entry]


def test_a_session_open_with_no_capture_yet_is_frozen_and_stale(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 13, 0))
    block = snapshot(bus, "FOP.ES", now=ct(2026, 11, 3, 6, 20))["feed"]
    assert block["frozen"] is True and block["stale"] is True


@pytest.mark.parametrize("when", [ct(2026, 11, 2, 18, 0), ct(2026, 11, 3, 5, 59)])
def test_overnight_the_last_session_is_shown_and_is_not_stale(bus, db, write_captures, when):
    write_captures(ct(2026, 11, 2, 13, 0))
    block = snapshot(bus, "FOP.ES", now=when)["feed"]
    assert block["frozen"] is True and block["stale"] is False


def test_an_expired_session_with_nothing_frozen_is_no_snapshot(bus, db, write_captures):
    """The last hour's gamma is never left on screen, even for want of anything else."""
    write_captures(ct(2026, 11, 6, 14, 55))
    assert snapshot(bus, "FOP.ES", now=ct(2026, 11, 7, 12, 0)) is None


def test_the_feed_block_states_the_captured_strike_range(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    block = snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1))["feed"]
    assert block["strike_low"] < block["strike_high"]
    assert block["strike_low"] <= PRICE["ES"] <= block["strike_high"]


def test_the_two_instruments_do_not_share_a_snapshot(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0), instrument="NQ")
    snap = snapshot(bus, "FOP.NQ", now=ct(2026, 11, 2, 9, 1))
    assert snap["spot"] == PRICE["NQ"] and snap["feed"]["label"] == "/NQ"
    assert snapshot(bus, "FOP.ES", now=ct(2026, 11, 2, 9, 1)) is None


# ── the handlers ─────────────────────────────────────────────────────────────

def test_no_capture_ever_is_the_empty_view(bus, db, proxy_spy):
    handlers.refresh_gamma(bus, "FOP.NQ")
    snap = bus.cache_get(handlers.CACHE_GAMMA).payload
    assert snap["views"] == {} and snap["symbol"] == "FOP.NQ" and proxy_spy.calls == []


def test_a_capture_repaints_only_when_the_page_is_on_that_symbol(
        bus, db, write_captures, spy_refresh):
    set_current_gamma_symbol(bus, "$SPX")
    write_captures(ct(2026, 11, 2, 9, 0))
    assert spy_refresh.calls == []
    set_current_gamma_symbol(bus, "FOP.ES")
    write_captures(ct(2026, 11, 2, 9, 5))
    assert spy_refresh.calls == ["FOP.ES"]
    set_current_gamma_symbol(bus, "FOP.NQ")
    write_captures(ct(2026, 11, 2, 9, 10))
    assert spy_refresh.calls == ["FOP.ES"]


def test_a_refused_capture_does_not_repaint(bus, db, write_captures, spy_refresh):
    set_current_gamma_symbol(bus, "FOP.ES")
    write_captures(ct(2026, 11, 2, 9, 0), iv="N/A")
    assert spy_refresh.calls == []


def test_a_repaint_that_fails_leaves_the_capture_stored(bus, db, write_captures, monkeypatch):
    def _boom(_bus, _symbol):
        raise RuntimeError("repaint")
    monkeypatch.setattr(handlers, "refresh_gamma", _boom)
    set_current_gamma_symbol(bus, "FOP.ES")
    _degrade.reset()
    write_captures(ct(2026, 11, 2, 9, 0))
    assert feed.latest(bus, "FOP.ES") is not None
    assert _degrade.counts().get(feed.AREA) == 1
    _degrade.reset()


@pytest.mark.parametrize("fn", [lambda: compute._gamma_fetch_chain("FOP.ES"),
                                lambda: compute._gamma_fetch_chain("FOP.NQ"),
                                lambda: compute.gamma_snapshot("FOP.ES")])
def test_the_chain_fetch_refuses_feed_symbols_without_a_call(fn, proxy_spy):
    assert fn() is None and proxy_spy.calls == []


def test_the_chain_fetch_still_asks_for_a_real_ticker(monkeypatch):
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"symbol": "ES"}

    class _Client:
        calls = []

        def get_option_chain(self, symbol, **_kwargs):
            self.calls.append(symbol)
            return _Resp()
    monkeypatch.setattr(compute._proxy, "schwab_py_client", _Client())
    assert compute._gamma_fetch_chain("ES") == {"symbol": "ES"}      # Eversource
    assert _Client.calls == ["ES"]


def test_refresh_gamma_for_the_feed_makes_no_schwab_call(bus, db, write_captures, proxy_spy):
    write_captures(ct(2026, 11, 2, 9, 0))
    handlers.refresh_gamma(bus, "FOP.ES")
    payload = bus.cache_get(handlers.CACHE_GAMMA).payload
    assert proxy_spy.calls == [] and payload["symbol"] == "FOP.ES"
    assert payload["feed"]["label"] == "/ES"


def test_the_rows_land_in_the_private_history_key_not_the_main_one(bus, db, write_captures, repeated_rows):
    write_captures(ct(2026, 11, 2, 9, 0), ct(2026, 11, 2, 9, 5))
    handlers.refresh_gamma(bus, "FOP.ES")
    payload = bus.cache_get(handlers.CACHE_GAMMA).payload
    assert "history" not in payload["views"]["GEX"]
    rows = bus.cache_get(handlers.gamma_history_key("GEX")).payload["rows"]
    assert len(rows) > 2


def test_the_one_minute_tick_makes_no_schwab_call_on_a_feed_symbol(
        bus, db, write_captures, proxy_spy, monkeypatch):
    write_captures(ct(2026, 11, 2, 9, 0))
    set_current_gamma_symbol(bus, "FOP.ES")
    monkeypatch.setattr(handlers, "PUBLISHED_GAMMA_SYMBOLS", ())
    handlers.refresh_gamma_current(bus)
    payload = bus.cache_get(handlers.CACHE_GAMMA).payload
    assert proxy_spy.calls == [] and payload["views"]["GEX"]["data"]


def test_existing_symbol_snapshot_is_unchanged_by_the_new_parameter(db, monkeypatch):
    fixed = ct(2026, 11, 2, 10, 0)

    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(gt, "datetime", _Fixed)
    monkeypatch.setattr(scheduler, "_market_now", lambda: fixed)
    chain = {"underlyingPrice": 600.0,
             "callExpDateMap": {"2026-11-02:0": {"600.0": [
                 {"gamma": 0.05, "delta": 0.5, "volatility": 15.0, "openInterest": 100,
                  "totalVolume": 10, "daysToExpiration": 0}]}},
             "putExpDateMap": {"2026-11-02:0": {"600.0": [
                 {"gamma": 0.05, "delta": -0.5, "volatility": 15.0, "openInterest": 80,
                  "totalVolume": 10, "daysToExpiration": 0}]}}}
    plain = compute.gamma_snapshot("SPY", chain=chain, with_term=False)
    assert plain == compute.gamma_snapshot("SPY", chain=chain, with_term=False, session=None)
    assert plain["views"]["GEX"]["data"]["gex"] and "feed" not in plain


def test_an_existing_symbol_prices_with_no_new_arguments(db, monkeypatch):
    seen = []
    real = gt.GammaEngine.calc_all_from_chain

    def _calc(self, chain, *args, **kwargs):
        seen.append((args, kwargs))
        return real(self, chain, *args, **kwargs)
    monkeypatch.setattr(gt.GammaEngine, "calc_all_from_chain", _calc)
    chain = {"underlyingPrice": 600.0,
             "callExpDateMap": {"2026-11-02:0": {"600.0": [
                 {"gamma": 0.05, "volatility": 15.0, "openInterest": 100}]}},
             "putExpDateMap": {}}
    compute.gamma_snapshot("SPY", chain=chain, with_term=False)
    assert seen == [((), {})]


# ── the lists and the public side ────────────────────────────────────────────

def test_the_feed_stays_out_of_the_collection(show_true):
    assert not any(is_feed_symbol(s) for s in gex_collector.collection_symbols())


def test_a_feed_symbol_on_the_watchlist_is_never_collected(monkeypatch):
    """``clean_symbol`` passes ``FOP.ES``, so a watchlist entry would otherwise send a
    chain request to Schwab every minute. Nothing else on the list changes."""
    import watchlist
    monkeypatch.setattr(watchlist, "get_scan_symbols", lambda: ["SPY", "FOP.ES", "MU", "FOP.NQ"])
    out = gex_collector.collection_symbols()
    assert "FOP.ES" not in out and "FOP.NQ" not in out
    assert out == [*gex_collector.SYMBOLS,
                   *(s for s in ("SPY", "MU") if s not in gex_collector.SYMBOLS)]


def test_the_private_list_gets_the_feed_only_when_shown(bus, show_true, monkeypatch):
    monkeypatch.setattr(compute, "gamma_symbol_options", lambda: ["$SPX", "SPY"])
    handlers.publish_gamma_symbols(bus)
    private = bus.cache_get(handlers.CACHE_GAMMA_SYMBOLS).payload["symbols"]
    assert private == ["$SPX", "SPY", "FOP.ES", "FOP.NQ"]
    public = bus.cache_get(public_gamma.SYMBOLS_KEY).payload["symbols"]
    assert public == ["$SPX", "SPY"]


def test_the_private_list_is_unchanged_while_not_shown(bus, monkeypatch):
    monkeypatch.setattr(compute, "gamma_symbol_options", lambda: ["$SPX", "SPY"])
    handlers.publish_gamma_symbols(bus)
    assert bus.cache_get(handlers.CACHE_GAMMA_SYMBOLS).payload["symbols"] == ["$SPX", "SPY"]


def test_private_symbol_options_does_not_change_its_argument(show_true):
    given = ["$SPX"]
    assert chain_snapshot.private_symbol_options(given) == ["$SPX", "FOP.ES", "FOP.NQ"]
    assert given == ["$SPX"]


@pytest.mark.parametrize("hot", [(), ("FOP.ES",), ("fop.es",)])
def test_a_feed_snapshot_never_gets_a_public_target(hot):
    assert handlers._gamma_pub_targets("FOP.ES", hot=hot) == ()


def test_a_feed_refresh_writes_no_public_key(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    handlers.refresh_gamma(bus, "FOP.ES")
    assert bus.cache_get(handlers.gamma_pub_key("FOP.ES")) is None
    for view in public_gamma.HISTORY_VIEWS:
        assert bus.cache_get(handlers.gamma_pub_history_key("FOP.ES", view)) is None


def test_the_public_page_cannot_be_handed_a_feed_symbol(bus, show_true, monkeypatch):
    from services.options_svc import gamma_public
    monkeypatch.setattr(compute, "gamma_symbol_options", lambda: ["$SPX", "SPY"])
    handlers.publish_gamma_symbols(bus)
    assert not any(is_feed_symbol(s) for s in gamma_public.allowed_symbols(bus))


def test_the_explain_path_answers_without_a_chain(proxy_spy):
    result = compute.gamma_explain("FOP.ES")
    assert proxy_spy.calls == [] and "No chain data" in result["html"]


# ── the module ───────────────────────────────────────────────────────────────

def test_the_module_imports_nothing_from_compute_at_module_level():
    source = pathlib.Path(chain_snapshot.__file__).read_text(encoding="utf-8")
    names = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    assert not [n for n in names if n.endswith("compute") or n.endswith("handlers")], names


def test_the_snapshot_is_json_and_the_clock_type_is_not_leaked(bus, db, write_captures):
    write_captures(ct(2026, 11, 2, 9, 0))
    snap = snapshot(bus, "FOP.ES", now=_dt.datetime(2026, 11, 2, 9, 1))     # naive: Central
    assert json.loads(json.dumps(snap))["feed"]["stale"] is False
