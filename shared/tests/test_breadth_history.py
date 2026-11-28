"""shared/breadth_history.py - the daily breadth store."""
import datetime as dt

import pytest

from shared import breadth_history as bh

TODAY = dt.date(2026, 11, 27)        # a Friday; the 26th was Thanksgiving


@pytest.fixture
def conn(tmp_path):
    c = bh.connect(tmp_path / "breadth.db")
    yield c
    c.close()


def test_write_then_read(conn):
    got = bh.upsert(conn, [("nyse_adv", "2026-11-25", 1886.0, "schwab_history"),
                           ("nyse_dec", "2026-11-25", 751.0, "schwab_history")], TODAY)
    assert got == {"written": 2, "unchanged": 0, "locked": 0, "outranked": 0, "invalid": 0}
    assert bh.day_row(conn, "2026-11-25") == {"nyse_adv": (1886.0, "schwab_history"),
                                              "nyse_dec": (751.0, "schwab_history")}
    assert bh.series_values(conn, "nyse_adv") == [("2026-11-25", 1886.0)]


def test_series_values_honours_the_window(conn):
    bh.upsert(conn, [("nyse_adv", d, v, "schwab_history") for d, v in
                     (("2026-11-23", 2323.0), ("2026-11-24", 1415.0), ("2026-11-25", 1886.0))],
              TODAY)
    assert bh.series_values(conn, "nyse_adv", start="2026-11-24", end="2026-11-24") == [
        ("2026-11-24", 1415.0)]
    assert [d for d, _ in bh.series_values(conn, "nyse_adv", start="2026-11-24")] == [
        "2026-11-24", "2026-11-25"]


def test_the_same_row_twice_is_unchanged(conn):
    row = [("nyse_adv", "2026-11-25", 1886.0, "schwab_history")]
    bh.upsert(conn, row, TODAY)
    assert bh.upsert(conn, row, TODAY)["unchanged"] == 1


def test_history_corrects_a_close_quote(conn):
    bh.upsert(conn, [("nyse_adv", "2026-11-25", 1880.0, "schwab_close_quote")], TODAY)
    bh.upsert(conn, [("nyse_adv", "2026-11-25", 1886.0, "schwab_history")], TODAY)
    assert bh.day_row(conn, "2026-11-25")["nyse_adv"] == (1886.0, "schwab_history")


def test_a_close_quote_never_replaces_a_history_value(conn):
    bh.upsert(conn, [("nyse_adv", "2026-11-25", 1886.0, "schwab_history")], TODAY)
    got = bh.upsert(conn, [("nyse_adv", "2026-11-25", 1.0, "schwab_close_quote")], TODAY, force=True)
    assert got["outranked"] == 1
    assert bh.day_row(conn, "2026-11-25")["nyse_adv"] == (1886.0, "schwab_history")


def test_an_old_row_is_locked_unless_forced(conn):
    old = [("nyse_adv", "2026-11-20", 500.0, "schwab_history")]
    bh.upsert(conn, old, dt.date(2026, 11, 20))
    changed = [("nyse_adv", "2026-11-20", 501.0, "schwab_history")]
    assert bh.upsert(conn, changed, TODAY)["locked"] == 1
    assert bh.day_row(conn, "2026-11-20")["nyse_adv"] == (500.0, "schwab_history")
    assert bh.upsert(conn, changed, TODAY, force=True)["written"] == 1
    assert bh.day_row(conn, "2026-11-20")["nyse_adv"] == (501.0, "schwab_history")


def test_an_old_day_with_no_row_is_written(conn):
    """The lock refuses to CHANGE an old row, never to fill a missing one: the
    backfill writes twenty years of old days."""
    got = bh.upsert(conn, [("nyse_adv", "2006-11-27", 615.0, "schwab_history")], TODAY)
    assert got["written"] == 1


def test_locked_counts_sessions_not_days():
    assert bh.locked(dt.date(2026, 11, 25), TODAY) is False   # Wed -> Fri is one session later
    assert bh.locked(dt.date(2026, 11, 24), TODAY) is False   # two sessions
    assert bh.locked(dt.date(2026, 11, 23), TODAY) is True


def test_an_unusable_value_is_not_stored(conn):
    got = bh.upsert(conn, [("nyse_trin", "2026-11-25", float("nan"), "schwab_history"),
                           ("nyse_trin", "not-a-day", 0.7, "schwab_history")], TODAY)
    assert got["invalid"] == 2 and bh.day_row(conn, "2026-11-25") == {}


@pytest.mark.parametrize("row", [
    ("made_up", "2026-11-25", 1.0, "schwab_history"),
    ("nyse_adv", "2026-11-25", 1.0, "guess"),
    ("tv_pct_above_50", "2026-11-25", 42.5, "schwab_history"),
    ("nyse_adv", "2026-11-25", 1.0, "tradingview"),
])
def test_a_row_outside_the_vocabulary_raises(conn, row):
    with pytest.raises(ValueError):
        bh.upsert(conn, [row], TODAY)


def test_a_refused_batch_writes_nothing(conn):
    with pytest.raises(ValueError):
        bh.upsert(conn, [("nyse_adv", "2026-11-25", 1886.0, "schwab_history"),
                         ("made_up", "2026-11-25", 1.0, "schwab_history")], TODAY)
    assert bh.day_row(conn, "2026-11-25") == {}


def test_coverage(conn):
    bh.upsert(conn, [("nyse_adv", "2026-11-24", 1415.0, "schwab_history"),
                     ("nyse_adv", "2026-11-25", 1886.0, "schwab_history")], TODAY)
    assert bh.coverage(conn) == [("nyse_adv", "2026-11-24", "2026-11-25", 2)]


def test_the_default_path_is_resolved_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setattr(bh, "DEFAULT_DB_PATH", tmp_path / "elsewhere.db")
    bh.connect().close()
    assert (tmp_path / "elsewhere.db").exists()


def test_the_store_is_never_excluded_from_the_backup():
    import sys
    import repo_paths
    sys.path.insert(0, str(repo_paths.REPO_ROOT / "tools"))
    import backup_local
    rel = repo_paths.BREADTH_DB.relative_to(repo_paths.REPO_ROOT).as_posix()
    assert not any(rel == x or rel.startswith(x + "/") for x in backup_local.DB_EXCLUDE)
