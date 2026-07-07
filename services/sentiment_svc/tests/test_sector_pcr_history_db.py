import datetime as dt

import pytest

from services.sentiment_svc import sector_pcr_history_db as db


def _conn():
    return db.connect(":memory:")


def _iso(d):
    return d.isoformat()


def test_record_and_delta_five_trading_days_back():
    c = _conn()
    base = dt.date(2026, 6, 16)
    # 6 distinct dates: pcr = 0.60, 0.62, ..., 0.70
    for i in range(6):
        db.record(c, _iso(base + dt.timedelta(days=i)), 0.60 + i * 0.02)
    # latest (0.70) - (5-back = the first, 0.60) = 0.10
    assert db.sector_pc_delta(c, lookback=5) == pytest.approx(0.10)


def test_delta_none_when_fewer_than_six_distinct_dates():
    c = _conn()
    base = dt.date(2026, 6, 16)
    for i in range(5):                                   # only 5 distinct dates
        db.record(c, _iso(base + dt.timedelta(days=i)), 0.6 + i * 0.01)
    assert db.sector_pc_delta(c, lookback=5) is None


def test_delta_none_when_a_pcr_is_none():
    c = _conn()
    base = dt.date(2026, 6, 16)
    for i in range(6):
        db.record(c, _iso(base + dt.timedelta(days=i)), 0.6 + i * 0.01)
    # overwrite the 5-back row's pcr with None
    db.record(c, _iso(base), None)
    assert db.sector_pc_delta(c, lookback=5) is None


def test_todays_row_replace_update_wins():
    c = _conn()
    base = dt.date(2026, 6, 16)
    for i in range(6):
        db.record(c, _iso(base + dt.timedelta(days=i)), 0.60 + i * 0.02)
    latest = _iso(base + dt.timedelta(days=5))
    db.record(c, latest, 0.90)                           # same date, new value
    rows = c.execute("SELECT COUNT(*) FROM sector_pcr").fetchone()[0]
    assert rows == 6                                     # no duplicate row
    # latest now 0.90; 5-back still 0.60 -> delta 0.30
    assert db.sector_pc_delta(c, lookback=5) == pytest.approx(0.30)


def test_prune_keeps_window():
    c = _conn()
    base = dt.date(2026, 6, 1)
    for i in range(15):
        db.record(c, _iso(base + dt.timedelta(days=i)), 0.5)
    db.prune(c, keep=8)
    remaining = c.execute("SELECT COUNT(*) FROM sector_pcr").fetchone()[0]
    assert remaining == 8
    min_date = c.execute("SELECT MIN(date) FROM sector_pcr").fetchone()[0]
    assert min_date == _iso(base + dt.timedelta(days=7))   # last 8 of 15


def test_prune_noop_when_within_window():
    c = _conn()
    base = dt.date(2026, 6, 1)
    for i in range(5):
        db.record(c, _iso(base + dt.timedelta(days=i)), 0.5)
    db.prune(c, keep=8)
    assert c.execute("SELECT COUNT(*) FROM sector_pcr").fetchone()[0] == 5


def test_delta_empty_db_is_none():
    assert db.sector_pc_delta(_conn(), lookback=5) is None
