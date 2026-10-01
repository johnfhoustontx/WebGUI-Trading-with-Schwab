"""hiro_minutes: the per-minute HIRO-model hedge impact store, with its own
retention (the HIRO baseline needs 5 sessions BEFORE today, the GEX grids keep
5 sessions in total)."""
import datetime as dt
import sqlite3

import pytest

import gex_history_db as gh


def _conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "g.db"))
    gh.init_schema(conn)
    return conn


def _ts(d, hh, mm):
    return int(dt.datetime(d.year, d.month, d.day, hh, mm).timestamp())   # local, as gh does


def test_hiro_insert_and_load_day_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    d = dt.date(2026, 10, 1)
    gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 0), {"spot": 500.0, "impact": 1.5e6,
                       "classified_vol": 10.0, "unclassified_vol": 2.0})
    gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 1), {"spot": 501.0, "impact": -2.0e6,
                       "classified_vol": 5.0, "unclassified_vol": 0.0})
    rows = gh.load_hiro_day(conn, "SPY", d)
    assert [r["impact"] for r in rows] == [1.5e6, -2.0e6]
    assert rows[0] == {"ts": _ts(d, 9, 0), "spot": 500.0, "impact": 1.5e6,
                       "classified_vol": 10.0, "unclassified_vol": 2.0}
    assert gh.load_hiro_day(conn, "QQQ", d) == []


def test_hiro_insert_same_minute_replaces(tmp_path):
    conn = _conn(tmp_path)
    d = dt.date(2026, 10, 1)
    row = {"spot": 1.0, "impact": 1.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 0), row)
    gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 0), {**row, "impact": 2.0})
    assert [r["impact"] for r in gh.load_hiro_day(conn, "SPY", d)] == [2.0]


def test_hiro_prior_sessions_newest_first_and_excludes_today(tmp_path):
    conn = _conn(tmp_path)
    row = {"spot": 1.0, "impact": 1.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    days = [dt.date(2026, 9, 28), dt.date(2026, 9, 29), dt.date(2026, 9, 30), dt.date(2026, 10, 1)]
    for i, d in enumerate(days):
        gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 0), {**row, "impact": float(i)})
    got = gh.load_hiro_prior_sessions(conn, "SPY", 2, before=dt.date(2026, 10, 1))
    assert [[r["impact"] for r in s] for s in got] == [[2.0], [1.0]]


def test_purge_hiro_keeps_last_n_sessions(tmp_path):
    conn = _conn(tmp_path)
    row = {"spot": 1.0, "impact": 1.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    for day in (28, 29, 30):
        gh.insert_hiro_row(conn, "SPY", _ts(dt.date(2026, 9, day), 9, 0), row)
    assert gh.purge_hiro(conn, keep_sessions=2) == 1
    assert gh.load_hiro_day(conn, "SPY", dt.date(2026, 9, 28)) == []
    assert gh.load_hiro_day(conn, "SPY", dt.date(2026, 9, 29)) != []


def test_purge_keep_sessions_does_not_touch_hiro(tmp_path):
    """The GEX retention (5 sessions) must not cut the hiro baseline.

    Two snapshot rows on two dates make the GEX purge genuinely run and delete
    something (with an empty snapshots table it returns before any DELETE, and
    this test would pass vacuously). The hiro row sits BEFORE the GEX cutoff,
    so it would be deleted if purge_keep_sessions reached hiro_minutes."""
    conn = _conn(tmp_path)
    row = {"spot": 1.0, "impact": 1.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    gh.insert_hiro_row(conn, "SPY", _ts(dt.date(2026, 9, 1), 9, 0), row)
    for d in (dt.date(2026, 8, 31), dt.date(2026, 9, 2)):
        gh.insert_snapshot(conn, "SPY", "gex", {"ts": _ts(d, 9, 0), "spot": 1.0},
                           {}, 0)
    conn.commit()
    assert gh.purge_keep_sessions(conn, keep_sessions=1) == 1   # the 08-31 snapshot
    assert gh.load_hiro_day(conn, "SPY", dt.date(2026, 9, 1)) != []


def test_init_schema_is_idempotent_with_hiro(tmp_path):
    conn = _conn(tmp_path)
    d = dt.date(2026, 10, 1)
    row = {"spot": 1.0, "impact": 3.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 0), row)
    gh.init_schema(conn)          # second call must not raise
    gh.init_schema(conn)
    assert [r["impact"] for r in gh.load_hiro_day(conn, "SPY", d)] == [3.0]
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "hiro_minutes" in names


def test_init_schema_alone_creates_hiro_table(tmp_path):
    """init_schema (not only init_hiro_schema) must create hiro_minutes."""
    conn = sqlite3.connect(str(tmp_path / "fresh.db"))
    gh.init_schema(conn)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "hiro_minutes" in names


def test_load_hiro_prior_sessions_spans_symbols_independently(tmp_path):
    """A session stored only for QQQ is not a SPY session."""
    conn = _conn(tmp_path)
    row = {"spot": 1.0, "impact": 1.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    gh.insert_hiro_row(conn, "QQQ", _ts(dt.date(2026, 9, 30), 9, 0), row)
    gh.insert_hiro_row(conn, "SPY", _ts(dt.date(2026, 9, 29), 9, 0), row)
    got = gh.load_hiro_prior_sessions(conn, "SPY", 5, before=dt.date(2026, 10, 1))
    assert len(got) == 1
    assert got[0][0]["ts"] == _ts(dt.date(2026, 9, 29), 9, 0)


def test_load_hiro_prior_sessions_n_zero_is_empty(tmp_path):
    conn = _conn(tmp_path)
    row = {"spot": 1.0, "impact": 1.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    gh.insert_hiro_row(conn, "SPY", _ts(dt.date(2026, 9, 29), 9, 0), row)
    assert gh.load_hiro_prior_sessions(conn, "SPY", 1, before=dt.date(2026, 10, 1)) != []
    assert gh.load_hiro_prior_sessions(conn, "SPY", 0, before=dt.date(2026, 10, 1)) == []


def test_insert_hiro_rows_batches_symbols_in_one_call(tmp_path):
    conn = _conn(tmp_path)
    d = dt.date(2026, 10, 1)
    gh.insert_hiro_rows(conn, [
        ("SPY", _ts(d, 9, 0), {"spot": 500.0, "impact": 1.0,
                               "classified_vol": 1.0, "unclassified_vol": 0.0}),
        ("QQQ", _ts(d, 9, 0), {"spot": 400.0, "impact": -2.0,
                               "classified_vol": 2.0, "unclassified_vol": 1.0}),
    ])
    assert [r["impact"] for r in gh.load_hiro_day(conn, "SPY", d)] == [1.0]
    assert [r["impact"] for r in gh.load_hiro_day(conn, "QQQ", d)] == [-2.0]


def test_insert_hiro_none_spot_stored_null(tmp_path):
    conn = _conn(tmp_path)
    d = dt.date(2026, 10, 1)
    gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 0), {"spot": None, "impact": 1.0,
                       "classified_vol": 1.0, "unclassified_vol": 0.0})
    assert gh.load_hiro_day(conn, "SPY", d)[0]["spot"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_insert_hiro_rows_non_finite_raises_and_writes_nothing(tmp_path, bad):
    """SQLite would store an infinity silently (only NaN trips NOT NULL), so
    the check is in code - and a bad row anywhere in a batch writes nothing."""
    conn = _conn(tmp_path)
    d = dt.date(2026, 10, 1)
    good = {"spot": 1.0, "impact": 1.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    with pytest.raises(ValueError):
        gh.insert_hiro_rows(conn, [("SPY", _ts(d, 9, 0), good),
                                   ("QQQ", _ts(d, 9, 0), {**good, "impact": bad})])
    conn.commit()
    assert gh.load_hiro_day(conn, "SPY", d) == []
    assert gh.load_hiro_day(conn, "QQQ", d) == []
