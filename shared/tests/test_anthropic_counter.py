"""Tests for the shared per-day Anthropic (Claude) call counter."""
import datetime as dt

import pytest
from shared import anthropic_counter as ac


@pytest.fixture(autouse=True)
def _fresh_conn(monkeypatch):
    """Each test gets its own in-memory DB (the module global is reset)."""
    monkeypatch.setattr(ac, "_conn", ac.connect(":memory:"))


def test_record_and_rollups():
    today = dt.date(2026, 7, 13)
    ac.record(2, day="2026-07-13")
    ac.record(1, day="2026-07-13")
    ac.record(4, day="2026-07-07")     # 6 days ago -> in 7d window
    ac.record(9, day="2026-07-06")     # 7 days ago -> out of 7d, in 30d
    s = ac.stats(today=today)
    assert s["today"] == 3
    assert s["last_7_days"] == 7       # 3 + 4
    assert s["last_30_days"] == 16
    assert s["since"] == "2026-07-06"


def test_empty_and_failure_degrade_to_zero(monkeypatch):
    assert ac.stats(today=dt.date(2026, 7, 13)) == {
        "today": 0, "last_7_days": 0, "last_30_days": 0, "since": None}
    monkeypatch.setattr(ac, "_get_conn", lambda: (_ for _ in ()).throw(RuntimeError))
    ac.record(1)                       # must not raise
    assert ac.stats()["today"] == 0


def test_connect_default_is_memory_under_pytest():
    """Tests must never touch the real counts file (the intraday-DB lesson)."""
    conn = ac.connect()
    assert conn.execute("PRAGMA database_list").fetchall()[0][2] in ("", None)
