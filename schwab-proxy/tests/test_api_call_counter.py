"""Tests for the per-day Schwab API-call counter (Settings → API usage)."""
import datetime as dt

import api_call_counter as acc
import pytest


@pytest.fixture(autouse=True)
def _fresh_conn(monkeypatch):
    """Each test gets its own in-memory DB (the module global is reset)."""
    monkeypatch.setattr(acc, "_conn", acc.connect(":memory:"))


def test_record_and_today():
    acc.record(3, day="2026-07-12")
    acc.record(2, day="2026-07-12")
    s = acc.stats(today=dt.date(2026, 7, 12))
    assert s["today"] == 5
    assert s["since"] == "2026-07-12"


def test_rolling_windows_include_today():
    today = dt.date(2026, 7, 12)
    acc.record(10, day="2026-07-12")                 # today
    acc.record(7, day="2026-07-06")                  # 6 days ago -> in 7d window
    acc.record(1, day="2026-07-05")                  # 7 days ago -> OUT of 7d
    acc.record(30, day="2026-06-13")                 # 29 days ago -> in 30d
    acc.record(99, day="2026-06-01")                 # out of 30d
    s = acc.stats(today=today)
    assert s["today"] == 10
    assert s["last_7_days"] == 17                    # 10 + 7
    assert s["last_30_days"] == 48                   # 10 + 7 + 1 + 30
    assert s["since"] == "2026-06-01"


def test_empty_stats_are_zero():
    s = acc.stats(today=dt.date(2026, 7, 12))
    assert s == {"today": 0, "last_7_days": 0, "last_30_days": 0, "since": None}


def test_record_never_raises(monkeypatch):
    monkeypatch.setattr(acc, "_get_conn", lambda: (_ for _ in ()).throw(RuntimeError))
    acc.record(1)                                    # must not raise
    # A count that could not be read is unknown, never a zero (audit CQ-100).
    assert acc.stats() == {"today": None, "last_7_days": None,
                           "last_30_days": None, "since": None}


def test_connect_default_is_memory_under_pytest():
    """Tests must never touch the real counts file (the intraday-DB lesson)."""
    conn = acc.connect()
    row = conn.execute("PRAGMA database_list").fetchall()[0]
    assert row[2] in ("", None)                      # in-memory has no file


def test_file_connect_uses_wal_and_normal_sync(tmp_path):
    """A file-backed counts DB uses WAL + synchronous=NORMAL so the per-call
    record() commit doesn't fsync — record() runs on the Schwab hot path (~60-70
    calls/min RTH) and the old default (FULL) synced to disk every call."""
    conn = acc.connect(tmp_path / "counts.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1   # NORMAL


def test_detail_counts_by_endpoint_caller_and_outcome():
    acc.record_detail("chains", "options_svc", "upstream", day="2026-10-05")
    acc.record_detail("chains", "options_svc", "upstream", day="2026-10-05")
    acc.record_detail("chains", "options_svc", "subset", day="2026-10-05")
    acc.record_detail("quotes", "market_svc", "hit", n=4, day="2026-10-05")
    s = acc.detail_summary(day="2026-10-05")
    assert s["by_outcome"] == {"upstream": 2, "subset": 1, "hit": 4}
    assert s["served_locally"] == 5                      # subset + hit
    assert {"endpoint": "chains", "caller": "options_svc",
            "outcome": "upstream", "n": 2} in s["rows"]


def test_shadow_outcomes_are_not_counted_as_served_locally():
    acc.record_detail("chains", "x", "shadow_subset_match", day="2026-10-05")
    acc.record_detail("chains", "x", "partial", day="2026-10-05")
    assert acc.detail_summary(day="2026-10-05")["served_locally"] == 0


def test_detail_is_per_day():
    acc.record_detail("chains", "x", "hit", day="2026-10-04")
    assert acc.detail_summary(day="2026-10-05") == {
        "served_locally": 0, "by_outcome": {}, "rows": []}


def test_detail_never_raises(monkeypatch):
    monkeypatch.setattr(acc, "_get_conn", lambda: (_ for _ in ()).throw(RuntimeError))
    acc.record_detail("chains", "x", "hit")              # must not raise
    # Unknown, never a zero nobody counted (audit CQ-100).
    assert acc.detail_summary() == {"served_locally": None, "by_outcome": None,
                                    "rows": None}


def test_long_caller_names_are_cut():
    acc.record_detail("chains", "c" * 500, "hit", day="2026-10-05")
    assert len(acc.detail_summary(day="2026-10-05")["rows"][0]["caller"]) == 40


def _six_hundred_rows():
    """600 rows for one day: caller c000 counted once ... c599 counted 600
    times, alternating between a local outcome and an upstream one."""
    for i in range(600):
        acc.record_detail("chains", f"c{i:03d}", "hit" if i % 2 else "upstream",
                          n=i + 1, day="2026-10-05")


def test_the_totals_cover_every_row_however_many_there_are():
    _six_hundred_rows()
    s = acc.detail_summary(day="2026-10-05")
    hits = sum(i + 1 for i in range(600) if i % 2)
    upstream = sum(i + 1 for i in range(600) if not i % 2)
    assert s["by_outcome"] == {"hit": hits, "upstream": upstream}
    assert s["served_locally"] == hits


def test_the_breakdown_lists_at_most_five_hundred_rows_largest_first():
    _six_hundred_rows()
    rows = acc.detail_summary(day="2026-10-05")["rows"]
    assert acc.MAX_DETAIL_ROWS == 500 and len(rows) == 500
    assert [r["n"] for r in rows] == list(range(600, 100, -1))
    assert rows[0] == {"endpoint": "chains", "caller": "c599",
                       "outcome": "hit", "n": 600}


def test_the_row_cut_keeps_the_stable_order_among_equal_counts(monkeypatch):
    monkeypatch.setattr(acc, "MAX_DETAIL_ROWS", 3)
    for caller in ("zeta", "alpha", "mid", "beta"):
        acc.record_detail("chains", caller, "hit", day="2026-10-05")
    s = acc.detail_summary(day="2026-10-05")
    assert [r["caller"] for r in s["rows"]] == ["alpha", "beta", "mid"]
    assert s["by_outcome"] == {"hit": 4} and s["served_locally"] == 4


def test_the_totals_list_the_largest_outcome_first():
    acc.record_detail("chains", "a", "subset", n=2, day="2026-10-05")
    acc.record_detail("chains", "a", "upstream", n=9, day="2026-10-05")
    acc.record_detail("chains", "b", "hit", n=2, day="2026-10-05")
    assert list(acc.detail_summary(day="2026-10-05")["by_outcome"].items()) == [
        ("upstream", 9), ("hit", 2), ("subset", 2)]


def test_detail_rows_with_equal_counts_come_back_in_a_stable_order():
    for caller in ("zeta", "alpha", "mid"):
        acc.record_detail("chains", caller, "hit", day="2026-10-05")
    acc.record_detail("chains", "big", "hit", n=9, day="2026-10-05")
    callers = [r["caller"] for r in acc.detail_summary(day="2026-10-05")["rows"]]
    assert callers == ["big", "alpha", "mid", "zeta"]


def test_an_empty_day_is_a_real_zero_not_unknown():
    assert acc.detail_summary(day="2020-01-01") == {
        "served_locally": 0, "by_outcome": {}, "rows": []}
