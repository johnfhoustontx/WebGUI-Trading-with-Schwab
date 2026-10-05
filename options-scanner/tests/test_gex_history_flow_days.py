"""flow_contract_days: one row per flagged flow-alert contract per session --
its bought/sold tallies and, once the next session has read it, the open
interest it moved to. Design: docs/plans/2026-10-04-flow-alert-sides-design.md."""
import math
import sqlite3

import pytest

import gex_history_db as gh


def _conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "g.db"))
    gh.init_schema(conn)
    return conn


def _row(alert_id="SPY|uoa|call|770|2026-10-09", session_date="2026-10-02", **over):
    row = {"session_date": session_date, "alert_id": alert_id, "symbol": "SPY",
           "osi": "SPY   261009C00770000", "side": "call", "strike": 770.0,
           "expiry": "2026-10-09", "alert_type": "uoa", "fired_ts": 1000,
           "oi_prev": 9985.0, "volume": 1000.0,
           "poll_bought": 400.0, "poll_sold": 100.0, "poll_unlabelled": 500.0,
           "stream_bought": None, "stream_sold": None, "stream_unlabelled": None}
    row.update(over)
    return row


def test_schema_is_created_and_idempotent(tmp_path):
    conn = _conn(tmp_path)
    gh.init_schema(conn)
    gh.init_flow_day_schema(conn)
    assert gh.load_flow_contract_days(conn, "2026-10-02") == []


def test_upsert_and_load_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    gh.upsert_flow_contract_days(conn, [_row()])
    (got,) = gh.load_flow_contract_days(conn, "2026-10-02")
    assert got == {**_row(), "oi_next": None, "oi_next_date": None,
                   "verdict": None, "oi_ratio": None}
    assert gh.load_flow_contract_days(conn, "2026-10-01") == []


def test_load_is_ordered_by_fired_time(tmp_path):
    conn = _conn(tmp_path)
    gh.upsert_flow_contract_days(conn, [
        _row("b", fired_ts=300), _row("a", fired_ts=100), _row("c", fired_ts=200)])
    assert [r["alert_id"] for r in gh.load_flow_contract_days(conn, "2026-10-02")] \
        == ["a", "c", "b"]


def test_a_second_upsert_replaces_the_tallies(tmp_path):
    """State, not flow: the caller holds the running tally and writes it whole."""
    conn = _conn(tmp_path)
    gh.upsert_flow_contract_days(conn, [_row()])
    gh.upsert_flow_contract_days(conn, [_row(
        volume=1500.0, oi_prev=9990.0, poll_bought=700.0, poll_sold=200.0,
        poll_unlabelled=600.0, stream_bought=50.0, stream_sold=10.0,
        stream_unlabelled=5.0)])
    (got,) = gh.load_flow_contract_days(conn, "2026-10-02")
    assert (got["volume"], got["oi_prev"]) == (1500.0, 9990.0)
    assert (got["poll_bought"], got["poll_sold"], got["poll_unlabelled"]) \
        == (700.0, 200.0, 600.0)
    assert (got["stream_bought"], got["stream_sold"], got["stream_unlabelled"]) \
        == (50.0, 10.0, 5.0)


def test_a_later_upsert_never_clears_a_resolution(tmp_path):
    conn = _conn(tmp_path)
    gh.upsert_flow_contract_days(conn, [_row()])
    gh.resolve_flow_contract_day(conn, "2026-10-02", _row()["alert_id"],
                                 oi_next=10600.0, oi_next_date="2026-10-05",
                                 verdict="opened", oi_ratio=0.615)
    gh.upsert_flow_contract_days(conn, [_row(volume=1200.0)])
    (got,) = gh.load_flow_contract_days(conn, "2026-10-02")
    assert got["volume"] == 1200.0
    assert (got["oi_next"], got["oi_next_date"], got["verdict"], got["oi_ratio"]) \
        == (10600.0, "2026-10-05", "opened", 0.615)


@pytest.mark.parametrize("key", ["poll_bought", "poll_sold", "poll_unlabelled"])
@pytest.mark.parametrize("bad", [math.nan, math.inf, None])
def test_a_non_finite_tally_raises_before_anything_is_written(tmp_path, key, bad):
    """SQLite stores an infinity silently; checked here, as hiro_minutes is."""
    conn = _conn(tmp_path)
    good = _row("good")
    with pytest.raises((TypeError, ValueError)):
        gh.upsert_flow_contract_days(conn, [good, _row("bad", **{key: bad})])
    assert gh.load_flow_contract_days(conn, "2026-10-02") == []


def test_unresolved_rows_before_a_date(tmp_path):
    conn = _conn(tmp_path)
    gh.upsert_flow_contract_days(conn, [
        _row("thu", session_date="2026-10-01"),
        _row("fri", session_date="2026-10-02"),
        _row("fri-done", session_date="2026-10-02"),
        _row("today", session_date="2026-10-05")])
    gh.resolve_flow_contract_day(conn, "2026-10-02", "fri-done", oi_next=1.0,
                                 oi_next_date="2026-10-05", verdict="mixed",
                                 oi_ratio=0.0)
    got = gh.load_unresolved_flow_days(conn, before="2026-10-05")
    assert sorted(r["alert_id"] for r in got) == ["fri", "thu"]


def test_a_resolution_can_be_overwritten_by_a_re_read(tmp_path):
    conn = _conn(tmp_path)
    gh.upsert_flow_contract_days(conn, [_row()])
    aid = _row()["alert_id"]
    gh.resolve_flow_contract_day(conn, "2026-10-02", aid, oi_next=9985.0,
                                 oi_next_date="2026-10-05", verdict="mixed",
                                 oi_ratio=0.0)
    gh.resolve_flow_contract_day(conn, "2026-10-02", aid, oi_next=10600.0,
                                 oi_next_date="2026-10-05", verdict="opened",
                                 oi_ratio=0.615)
    (got,) = gh.load_flow_contract_days(conn, "2026-10-02")
    assert (got["oi_next"], got["verdict"]) == (10600.0, "opened")


def test_a_verdict_with_no_reading_stores_null_figures(tmp_path):
    conn = _conn(tmp_path)
    gh.upsert_flow_contract_days(conn, [_row()])
    gh.resolve_flow_contract_day(conn, "2026-10-02", _row()["alert_id"],
                                 oi_next=None, oi_next_date="2026-10-05",
                                 verdict="expired", oi_ratio=None)
    (got,) = gh.load_flow_contract_days(conn, "2026-10-02")
    assert (got["oi_next"], got["verdict"], got["oi_ratio"]) == (None, "expired", None)
    assert gh.load_unresolved_flow_days(conn, before="2026-10-05") == []


def test_latest_session_before(tmp_path):
    conn = _conn(tmp_path)
    assert gh.latest_flow_session_before(conn, "2026-10-05") is None
    gh.upsert_flow_contract_days(conn, [
        _row("a", session_date="2026-09-30"), _row("b", session_date="2026-10-02"),
        _row("c", session_date="2026-10-05")])
    assert gh.latest_flow_session_before(conn, "2026-10-05") == "2026-10-02"
    assert gh.latest_flow_session_before(conn, "2026-10-06") == "2026-10-05"


def test_purge_keeps_the_newest_sessions(tmp_path):
    conn = _conn(tmp_path)
    gh.upsert_flow_contract_days(conn, [
        _row("a", session_date="2026-09-29"), _row("b", session_date="2026-09-30"),
        _row("c", session_date="2026-10-01"), _row("d", session_date="2026-10-02"),
        _row("e", session_date="2026-10-02")])
    assert gh.purge_flow_contract_days(conn, keep_sessions=2) == 2
    assert gh.load_flow_contract_days(conn, "2026-09-30") == []
    assert len(gh.load_flow_contract_days(conn, "2026-10-01")) == 1
    assert len(gh.load_flow_contract_days(conn, "2026-10-02")) == 2
    assert gh.purge_flow_contract_days(conn, keep_sessions=2) == 0


def test_purge_keeps_at_least_one_session(tmp_path):
    conn = _conn(tmp_path)
    gh.upsert_flow_contract_days(conn, [_row()])
    assert gh.purge_flow_contract_days(conn, keep_sessions=0) == 0
    assert len(gh.load_flow_contract_days(conn, "2026-10-02")) == 1
