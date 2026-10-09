"""``tools/check_traded_premium.py`` -- does a stored traded-premium total ever
fall, and what does the view add to the day's stored bytes.

Synthetic rows and an in-memory database; no store is opened here.
"""
import datetime as dt
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import check_traded_premium as tool  # noqa: E402

gh = tool.gh
DAY = dt.date(2026, 10, 5)
CLEAN = {"symbols": 1, "rows": 2, "steps": 2, "fell": 0, "dollars": 0.0, "worst": []}


def _cell(call, put):
    return {"call": call, "put": put, "net": call - put}


def _row(i, grid):
    return (1000 + 60 * i, 100.0, None, None, None, 0, grid)


def _db():
    conn = sqlite3.connect(":memory:")
    gh.init_schema(conn)
    return conn


def _put(conn, symbol, view, minute, grid):
    ts = int(dt.datetime(2026, 10, 5, 10, minute).timestamp())
    gh.insert_snapshot(conn, symbol, view, {"ts": ts, "spot": 100.0}, grid, None)


def test_a_total_that_only_rises_has_no_falls():
    rows = [_row(0, {}), _row(1, {100.0: _cell(5.0, 0.0)}),
            _row(2, {100.0: _cell(5.0, 2.0)})]
    assert tool.falls(rows) == (2, 0, 0.0)


def test_a_fall_is_counted_even_across_missing_minutes():
    """A running total must not fall across a gap either."""
    rows = [_row(0, {100.0: _cell(9.0, 1.0)}), _row(5, {100.0: _cell(7.0, 1.0)})]
    assert tool.falls(rows) == (2, 1, 2.0)


def test_check_reads_every_symbol_with_rows_that_day():
    conn = _db()
    _put(conn, "SPY", "tprem", 0, {100.0: _cell(5.0, 1.0)})
    _put(conn, "SPY", "tprem", 1, {100.0: _cell(6.0, 1.0)})
    _put(conn, "QQQ", "tprem", 0, {50.0: _cell(9.0, 0.0)})
    _put(conn, "QQQ", "tprem", 1, {50.0: _cell(4.0, 0.0)})
    _put(conn, "SPY", "gex", 0, {100.0: _cell(1.0, 1.0)})       # another view
    result = tool.check(conn, DAY)
    assert (result["symbols"], result["rows"], result["steps"]) == (2, 4, 4)
    assert (result["fell"], result["dollars"]) == (1, 5.0)
    assert result["worst"] == [(5.0, "QQQ", 1)]


def test_sizes_are_counted_by_view():
    conn = _db()
    _put(conn, "SPY", "tprem", 0, {100.0: _cell(5.0, 1.0)})
    _put(conn, "SPY", "tprem", 1, {100.0: _cell(6.0, 1.0)})
    _put(conn, "SPY", "gex", 0, {100.0: _cell(1.0, 1.0)})
    by_view = tool.sizes(conn, DAY)
    assert set(by_view) == {"gex", "tprem"}
    assert by_view["tprem"][0] == 2 and by_view["tprem"][1] > 0


def test_the_report_calls_a_fall_a_bug_and_says_what_the_view_adds():
    result = dict(CLEAN, fell=1, dollars=5.0, worst=[(5.0, "QQQ", 1)])
    text = "\n".join(tool.report(DAY, result, {"gex": (2, 800), "tprem": (2, 100)}))
    assert "must never fall" in text and "QQQ: 1 falls, $5" in text
    assert "tprem adds 12.5% to the day's grid bytes" in text


def test_a_clean_day_is_reported_clean():
    text = "\n".join(tool.report(DAY, CLEAN, {"gex": (2, 800), "tprem": (2, 100)}))
    assert "no side of any strike ever fell" in text


def test_the_report_says_when_nothing_is_stored():
    result = dict(CLEAN, symbols=0, rows=0, steps=0)
    assert "nothing stored" in "\n".join(tool.report(DAY, result, {"gex": (2, 800)}))
