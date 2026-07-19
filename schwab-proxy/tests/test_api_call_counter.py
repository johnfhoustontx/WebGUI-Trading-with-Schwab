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
    assert acc.stats()["today"] == 0                 # stats degrade to zeros too


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
