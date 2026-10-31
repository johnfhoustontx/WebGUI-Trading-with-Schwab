"""The Gamma Archive: what survives the 5-session purge, and at what thinning."""
import datetime as dt
import importlib

import gex_archive as ga
import gex_history_db as gh
import pytest

DAY = dt.date(2026, 10, 28)
CFG = {"enabled": True, "grid_interval_min": 5, "strike_band": 0.15, "term_interval_min": 15}
AFTER_CLOSE = dt.datetime(2026, 10, 28, 17, 0)


def _ts(hour, minute, day=DAY):
    start, _end = gh._local_unix_range(day)
    return start + hour * 3600 + minute * 60


def _source(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "src.db")
    conn.executescript(gh._SCHEMA + gh.TERM_SCHEMA_SQL)
    return conn


def _row(conn, symbol="SPY", view="gex", ts=None, spot=100.0, strikes=(50.0, 90.0, 100.0, 110.0, 150.0),
         carried=None, net_total=7.0):
    grid = {s: {"call": s, "put": -s, "net": 0.0} for s in strikes}
    conn.execute(
        "INSERT INTO snapshots (symbol, view, ts, spot, net_total, gex_json, carried_age_sec) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (symbol, view, _ts(9, 0) if ts is None else ts, spot, net_total,
         gh._encode_grid(grid), carried))


def _term(conn, stamp, strike, spot=100.0):
    conn.execute("INSERT INTO gex_term_snapshots VALUES (?, 'SPX', '2026-10-30', ?, 1, -1, 0, ?)",
                 (stamp, strike, spot))


def _archive(tmp_path):
    return ga.connect_archive(tmp_path / "archive.db")


# ── which rows of a bucket ───────────────────────────────────────────────────
def test_a_bucket_keeps_its_latest_fetched_row_over_a_later_carried_one():
    rows = [(600, None), (660, 5.0), (720, None), (780, 60.0), (840, 120.0)]
    assert ga.pick_rows(rows, 300) == [720]


def test_a_bucket_with_only_carried_rows_keeps_its_latest():
    assert ga.pick_rows([(600, 5.0), (660, 65.0)], 300) == [660]


def test_each_bucket_keeps_one_row():
    rows = [(t, None) for t in range(0, 900, 60)]
    assert ga.pick_rows(rows, 300) == [240, 540, 840]


def test_a_fetched_row_is_never_replaced_by_a_carried_one_whatever_the_order():
    assert ga.pick_rows([(660, 5.0), (600, None)], 300) == [600]


# ── the strike band ──────────────────────────────────────────────────────────
def test_the_crop_keeps_the_band_around_the_rows_own_spot_inclusive():
    grid = {s: {} for s in (74.0, 75.0, 100.0, 125.0, 126.0)}
    assert sorted(ga.crop_grid(grid, 100.0, 0.25)) == [75.0, 100.0, 125.0]


@pytest.mark.parametrize("spot", [None, 0.0, -1.0, float("nan"), float("inf")])
def test_no_usable_spot_keeps_the_whole_grid(spot):
    grid = {1.0: {}, 1000.0: {}}
    assert ga.crop_grid(grid, spot, 0.15) == grid


# ── a session across ─────────────────────────────────────────────────────────
def test_a_session_arrives_thinned_and_readable_with_its_summary(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    for minute in range(0, 10):
        _row(src, ts=_ts(9, minute), net_total=float(minute))
    written = ga.archive_session(src, arc, DAY.isoformat(), CFG)
    assert written == (2, 0)
    ts, net_total, grid = arc.execute(
        "SELECT ts, net_total, gex_json FROM snapshots ORDER BY ts").fetchall()[0]
    assert ts == _ts(9, 4) and net_total == 4.0
    assert sorted(gh._decode_grid(grid)) == [90.0, 100.0, 110.0]


def test_the_cropped_values_are_unchanged(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    _row(src)
    ga.archive_session(src, arc, DAY.isoformat(), CFG)
    (grid,) = arc.execute("SELECT gex_json FROM snapshots").fetchone()
    assert gh._decode_grid(grid)[110.0] == {"call": 110.0, "put": -110.0, "net": 0.0}


def test_a_carried_row_stays_marked_as_carried(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    _row(src, carried=45.0)
    ga.archive_session(src, arc, DAY.isoformat(), CFG)
    assert arc.execute("SELECT carried_age_sec FROM snapshots").fetchone() == (45.0,)


def test_a_row_with_no_grid_keeps_its_summary(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    src.execute("INSERT INTO snapshots (symbol, view, ts, spot, flip) "
                "VALUES ('SPY', 'gex', ?, 100.0, 99.0)", (_ts(9, 0),))
    ga.archive_session(src, arc, DAY.isoformat(), CFG)
    assert arc.execute("SELECT flip, gex_json FROM snapshots").fetchone() == (99.0, None)


def test_every_symbol_and_view_comes_across_and_another_day_does_not(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    for symbol, view in (("SPY", "gex"), ("SPY", "tprem"), ("QQQ", "gex")):
        _row(src, symbol, view)
    _row(src, ts=_ts(9, 0, dt.date(2026, 10, 27)))
    ga.archive_session(src, arc, DAY.isoformat(), CFG)
    assert sorted(arc.execute("SELECT symbol, view FROM snapshots").fetchall()) == [
        ("QQQ", "gex"), ("SPY", "gex"), ("SPY", "tprem")]


def test_the_term_table_is_thinned_and_cropped(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    for stamp in ("2026-10-28 09:00:00", "2026-10-28 09:05:00", "2026-10-28 09:15:00"):
        _term(src, stamp, 100.0)
        _term(src, stamp, 200.0)
    _term(src, "2026-10-27 09:00:00", 100.0)
    assert ga.archive_session(src, arc, DAY.isoformat(), CFG) == (0, 2)
    assert arc.execute("SELECT timestamp_ct, strike FROM gex_term_snapshots "
                       "ORDER BY timestamp_ct").fetchall() == [
        ("2026-10-28 09:00:00", 100.0), ("2026-10-28 09:15:00", 100.0)]


def test_the_gex_readers_work_on_the_archive(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    _row(src)
    ga.archive_session(src, arc, DAY.isoformat(), CFG)
    assert gh._distinct_session_dates(arc) == [DAY.isoformat()]
    assert gh.fetched_only_clause(arc)


def test_a_backup_older_than_the_carried_column_still_archives(tmp_path):
    import sqlite3
    src = sqlite3.connect(tmp_path / "old.db")
    src.executescript(gh._SCHEMA.replace("carried_age_sec           REAL,", "") + gh.TERM_SCHEMA_SQL)
    src.execute("INSERT INTO snapshots (symbol, view, ts, spot) VALUES ('SPY', 'gex', ?, 100.0)",
                (_ts(9, 0),))
    arc = _archive(tmp_path)
    assert ga.archive_session(src, arc, DAY.isoformat(), CFG) == (1, 0)


# ── once, whole, and only when closed ────────────────────────────────────────
def test_the_marker_records_how_the_session_was_thinned(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    _row(src)
    ga.archive_session(src, arc, DAY.isoformat(), CFG, code_version="abc1234")
    row = arc.execute("SELECT session_date, snapshot_rows, grid_interval_min, strike_band, "
                      "term_interval_min, code_version FROM archived_sessions").fetchone()
    assert row == (DAY.isoformat(), 1, 5, 0.15, 15, "abc1234")


def test_a_failure_part_way_leaves_nothing_and_the_next_run_redoes_it(tmp_path, monkeypatch):
    src, arc = _source(tmp_path), _archive(tmp_path)
    _row(src, "AAA")
    _row(src, "BBB")
    real = gh._encode_grid
    calls = []

    def fail_on_the_second(grid):
        calls.append(grid)
        if len(calls) == 2:
            raise ValueError("disk")
        return real(grid)

    monkeypatch.setattr(gh, "_encode_grid", fail_on_the_second)
    with pytest.raises(ValueError):
        ga.archive_new_sessions(src, arc, AFTER_CLOSE)
    assert arc.execute("SELECT COUNT(*) FROM snapshots").fetchone() == (0,)
    assert ga.archived_dates(arc) == set()

    monkeypatch.setattr(gh, "_encode_grid", real)
    assert ga.archive_new_sessions(src, arc, AFTER_CLOSE) == [(DAY.isoformat(), 2, 0)]


def test_a_second_run_archives_nothing_new(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    _row(src)
    assert len(ga.archive_new_sessions(src, arc, AFTER_CLOSE)) == 1
    assert ga.archive_new_sessions(src, arc, AFTER_CLOSE) == []


def test_sessions_are_archived_oldest_first(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    _row(src)
    _row(src, ts=_ts(9, 0, dt.date(2026, 10, 27)))
    assert [d[0] for d in ga.archive_new_sessions(src, arc, AFTER_CLOSE)] == [
        "2026-10-27", "2026-10-28"]


def test_todays_session_waits_for_collection_to_stop(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    _row(src)
    _start, stop = ga.mc.window_bounds("collection")
    during = dt.datetime.combine(DAY, stop) - dt.timedelta(minutes=1)
    assert ga.archive_new_sessions(src, arc, during) == []
    assert len(ga.archive_new_sessions(src, arc, dt.datetime.combine(DAY, stop))) == 1


def test_yesterdays_session_is_closed_at_any_hour_of_today(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    _row(src)
    early = dt.datetime(2026, 10, 29, 3, 0)
    assert len(ga.archive_new_sessions(src, arc, early)) == 1


def test_a_purged_session_survives_in_the_archive(tmp_path):
    src, arc = _source(tmp_path), _archive(tmp_path)
    _row(src)
    ga.archive_new_sessions(src, arc, AFTER_CLOSE)
    src.execute("DELETE FROM snapshots")
    assert arc.execute("SELECT COUNT(*) FROM snapshots").fetchone() == (1,)


# ── settings ─────────────────────────────────────────────────────────────────
def test_off_archives_nothing(tmp_path, monkeypatch):
    src, arc = _source(tmp_path), _archive(tmp_path)
    _row(src)
    monkeypatch.setattr(ga, "load", lambda: {"enabled": False})
    assert ga.archive_new_sessions(src, arc, AFTER_CLOSE) == []


def test_the_settings_are_read_from_config_not_literals(tmp_path, monkeypatch):
    src, arc = _source(tmp_path), _archive(tmp_path)
    for minute in range(0, 30):
        _row(src, ts=_ts(9, minute))
    monkeypatch.setattr(ga, "load", lambda: {"grid_interval_min": 15, "strike_band": 0.05})
    assert ga.archive_new_sessions(src, arc, AFTER_CLOSE) == [(DAY.isoformat(), 2, 0)]
    (grid,) = arc.execute("SELECT gex_json FROM snapshots LIMIT 1").fetchone()
    assert sorted(gh._decode_grid(grid)) == [100.0]


@pytest.mark.parametrize("raw", [True, "5", 0, 1000])
def test_a_bad_interval_is_the_default(monkeypatch, raw):
    monkeypatch.setattr(ga, "load", lambda: {"grid_interval_min": raw})
    assert ga.settings()["grid_interval_min"] == ga.DEFAULTS["grid_interval_min"]


@pytest.mark.parametrize("raw", [1, "false"])
def test_enabled_must_be_a_real_bool(monkeypatch, raw):
    monkeypatch.setattr(ga, "load", lambda: {"enabled": raw})
    assert ga.settings()["enabled"] is True


def test_the_shipped_file_matches_the_defaults():
    importlib.reload(ga)
    assert ga.load() == ga.DEFAULTS


def test_the_store_is_never_excluded_from_the_backup():
    """It holds history nothing can rebuild. ``db_paths`` takes every ``*.db``
    but those listed."""
    import repo_paths
    import sys
    sys.path.insert(0, str(repo_paths.REPO_ROOT / "tools"))
    import backup_local
    rel = ga.DB_PATH.relative_to(repo_paths.REPO_ROOT).as_posix()
    assert not any(rel == x or rel.startswith(x + "/") for x in backup_local.DB_EXCLUDE)
