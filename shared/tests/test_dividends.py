"""The dividends store: a forward ex-date calendar beside the earnings one."""
import ast
import math
from pathlib import Path

import pytest

from shared import dividends


def test_default_path_resolves_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setattr(dividends, "DEFAULT_DB_PATH", tmp_path / "d.db")
    conn = dividends.init_db()
    dividends.close_db(conn)
    assert (tmp_path / "d.db").exists()


def test_default_path_is_the_repo_paths_constant():
    import repo_paths
    assert dividends.DEFAULT_DB_PATH == repo_paths.DIVIDENDS_DB
    assert repo_paths.DIVIDENDS_DB.parent == repo_paths.EARNINGS_CALENDAR_DB.parent


def test_upsert_and_upcoming_window(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [{"symbol": "JPM", "ex_date": "2026-10-06", "pay_date": "2026-10-31",
                          "amount": 1.40, "frequency": 4, "declared_date": None}], now="t")
    dividends.upsert(c, [{"symbol": "JPM", "ex_date": "2026-10-06", "pay_date": "2026-10-31",
                          "amount": 1.45, "frequency": 4, "declared_date": None}], now="t2")
    rows = dividends.upcoming(c, ["JPM", "AAPL"], "2026-10-01", "2026-10-31")
    assert rows == [{"symbol": "JPM", "ex_date": "2026-10-06", "pay_date": "2026-10-31",
                     "amount": 1.45, "frequency": 4}]


def test_upcoming_excludes_dates_outside_the_window_and_other_symbols(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [
        {"symbol": "JPM", "ex_date": "2026-09-30", "pay_date": None, "amount": 1.0,
         "frequency": 4, "declared_date": None},
        {"symbol": "JPM", "ex_date": "2026-11-01", "pay_date": None, "amount": 1.0,
         "frequency": 4, "declared_date": None},
        {"symbol": "KO", "ex_date": "2026-10-10", "pay_date": None, "amount": 0.5,
         "frequency": 4, "declared_date": None},
        {"symbol": "AAPL", "ex_date": "2026-10-12", "pay_date": None, "amount": 0.26,
         "frequency": 4, "declared_date": None},
        {"symbol": "JPM", "ex_date": "2026-10-20", "pay_date": None, "amount": 1.0,
         "frequency": 4, "declared_date": None},
    ], now="t")
    rows = dividends.upcoming(c, ["jpm", "AAPL"], "2026-10-01", "2026-10-31")
    assert [(r["symbol"], r["ex_date"]) for r in rows] == [
        ("AAPL", "2026-10-12"), ("JPM", "2026-10-20")]


def test_non_finite_amount_is_stored_null(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [{"symbol": "X", "ex_date": "2026-10-06", "pay_date": None,
                          "amount": float("nan"), "frequency": 4, "declared_date": None},
                         {"symbol": "Y", "ex_date": "2026-10-06", "pay_date": None,
                          "amount": math.inf, "frequency": 4, "declared_date": None}],
                     now="t")
    rows = dividends.upcoming(c, ["X", "Y"], "2026-10-01", "2026-10-31")
    assert [r["amount"] for r in rows] == [None, None]


def test_coverage_is_three_valued(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.set_coverage(c, {"JPM": "ok", "NVDA": "none", "XYZ": "error"}, day="2026-09-26")
    assert dividends.coverage(c, ["JPM", "NVDA", "XYZ", "AAPL"]) == {
        "JPM": "ok", "NVDA": "none", "XYZ": "error", "AAPL": "unknown"}


def test_last_run_day(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    assert dividends.last_run_day(c) is None
    dividends.set_last_run_day(c, "2026-09-26")
    assert dividends.last_run_day(c) == "2026-09-26"


def test_import_set_is_stdlib_plus_repo_paths():
    src = Path(dividends.__file__).read_text()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names <= {"datetime", "logging", "sqlite3", "pathlib", "repo_paths"}
    assert "repo_paths" in names


def _row(sym, ex, pay=None, amount=1.0):
    return {"symbol": sym, "ex_date": ex, "pay_date": pay, "amount": amount,
            "frequency": 4, "declared_date": None}


def _all(c, sym):
    return [tuple(r) for r in c.execute(
        "SELECT ex_date, pay_date, amount FROM dividends WHERE symbol = ? "
        "ORDER BY ex_date", (sym,))]


def test_replace_symbol_revised_date_leaves_one_row(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2026-10-06")], now="t")
    dividends.replace_symbol(c, "JPM", [_row("JPM", "2026-10-08")], today="2026-09-26")
    assert _all(c, "JPM") == [("2026-10-08", None, 1.0)]


def test_replace_symbol_with_no_rows_clears_a_suspended_dividend(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2026-10-06")], now="t")
    dividends.replace_symbol(c, "JPM", [], today="2026-09-26")
    assert _all(c, "JPM") == []
    assert dividends.upcoming(c, ["JPM"], "2026-09-26", "2026-12-31") == []


def test_replace_symbol_leaves_past_rows_and_other_symbols_alone(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2026-07-06"), _row("JPM", "2026-09-26"),
                         _row("KO", "2026-10-10")], now="t")
    dividends.replace_symbol(c, "jpm", [_row("JPM", "2026-10-08")], today="2026-09-26")
    assert _all(c, "JPM") == [("2026-07-06", None, 1.0), ("2026-10-08", None, 1.0)]
    assert _all(c, "KO") == [("2026-10-10", None, 1.0)]


def test_replace_symbol_ignores_rows_for_another_symbol(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.replace_symbol(c, "JPM", [_row("KO", "2026-10-10")], today="2026-09-26")
    assert _all(c, "KO") == []


def test_prune_removes_rows_older_than_the_cutoff(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2025-01-06"), _row("JPM", "2026-07-06"),
                         _row("JPM", "2026-10-06")], now="t")
    removed = dividends.prune(c, "2026-01-01")
    assert removed == 1
    assert [r[0] for r in _all(c, "JPM")] == ["2026-07-06", "2026-10-06"]


def test_dates_are_normalised_so_a_timestamp_falls_inside_the_window(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2026-10-31T00:00:00Z", pay="2026-11-15T00:00:00Z")],
                     now="t")
    rows = dividends.upcoming(c, ["JPM"], "2026-10-01", "2026-10-31")
    assert [(r["ex_date"], r["pay_date"]) for r in rows] == [("2026-10-31", "2026-11-15")]


def test_two_spellings_of_one_ex_date_make_one_row(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2026-10-06", amount=1.40)], now="t")
    dividends.upsert(c, [_row("JPM", "2026-10-06T00:00:00Z", amount=1.45)], now="t2")
    assert _all(c, "JPM") == [("2026-10-06", None, 1.45)]


def test_replace_symbol_normalises_dates_too(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.replace_symbol(c, "JPM", [_row("JPM", "2026-10-06T00:00:00Z",
                                             pay="garbage")], today="2026-09-26")
    assert _all(c, "JPM") == [("2026-10-06", None, 1.0)]


def test_unusable_ex_date_is_skipped_and_unusable_pay_date_stored_null(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    n = dividends.upsert(c, [_row("JPM", "not-a-date"), _row("JPM", "2026-13-40"),
                             _row("KO", "2026-10-10", pay="soon")], now="t")
    assert n == 1
    assert _all(c, "JPM") == []
    assert _all(c, "KO") == [("2026-10-10", None, 1.0)]


def test_set_coverage_maps_an_unknown_status_to_error(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.set_coverage(c, {"JPM": "OK?", "KO": None, "PG": "none"}, day="2026-09-26")
    assert dividends.coverage(c, ["JPM", "KO", "PG"]) == {
        "JPM": "error", "KO": "error", "PG": "none"}


def test_replace_symbol_rolls_back_the_delete_when_the_insert_fails(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2026-10-06")], now="t")

    class _FailingInsert:
        def __init__(self, conn):
            self._c = conn

        def __enter__(self):
            return self._c.__enter__()

        def __exit__(self, *exc):
            return self._c.__exit__(*exc)

        def execute(self, *a, **k):
            return self._c.execute(*a, **k)

        def executemany(self, *a, **k):
            raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        dividends.replace_symbol(_FailingInsert(c), "JPM", [_row("JPM", "2026-10-08")],
                                 today="2026-09-26")
    assert _all(c, "JPM") == [("2026-10-06", None, 1.0)]


@pytest.mark.parametrize("symbol", [None, "", "   ", 42])
def test_replace_symbol_refuses_an_unusable_symbol(tmp_path, symbol):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2026-10-06")], now="t")
    with pytest.raises(ValueError):
        dividends.replace_symbol(c, symbol, [_row("JPM", "2026-10-08")], today="2026-09-26")
    assert _all(c, "JPM") == [("2026-10-06", None, 1.0)]


@pytest.mark.parametrize("today", [None, "", "soon", "2026-13-40", "20260926"])
def test_replace_symbol_refuses_an_unusable_today(tmp_path, today):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2026-10-06")], now="t")
    with pytest.raises(ValueError):
        dividends.replace_symbol(c, "JPM", [_row("JPM", "2026-10-08")], today=today)
    assert _all(c, "JPM") == [("2026-10-06", None, 1.0)]


@pytest.mark.parametrize("before", [None, "", "garbage", "2026-1-1"])
def test_prune_refuses_an_unusable_cutoff(tmp_path, before):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2025-01-06")], now="t")
    with pytest.raises(ValueError):
        dividends.prune(c, before)
    assert [r[0] for r in _all(c, "JPM")] == ["2025-01-06"]


def test_replace_symbol_accepts_a_date_object_for_today(tmp_path):
    import datetime as dt
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2026-10-06")], now="t")
    n = dividends.replace_symbol(c, "JPM", [_row("JPM", "2026-10-08")],
                                 today=dt.date(2026, 9, 26))
    assert n == 1
    assert _all(c, "JPM") == [("2026-10-08", None, 1.0)]


def test_upsert_counts_duplicate_keys_once_and_the_last_wins(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    n = dividends.upsert(c, [_row("JPM", "2026-10-06", amount=1.40),
                             _row("jpm", "2026-10-06T00:00:00Z", amount=1.45),
                             _row("KO", "2026-10-10")], now="t")
    assert n == 2
    assert _all(c, "JPM") == [("2026-10-06", None, 1.45)]


def test_replace_symbol_counts_duplicate_keys_once_and_the_last_wins(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    n = dividends.replace_symbol(c, "JPM", [_row("JPM", "2026-10-06", amount=1.40),
                                            _row("JPM", "2026-10-06", amount=1.45)],
                                 today="2026-09-26")
    assert n == 1
    assert _all(c, "JPM") == [("2026-10-06", None, 1.45)]


def test_replace_symbol_ignores_rows_before_today(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2026-07-06", amount=0.9)], now="t")
    n = dividends.replace_symbol(c, "JPM", [_row("JPM", "2026-07-06", amount=5.0),
                                            _row("JPM", "2026-09-01"),
                                            _row("JPM", "2026-09-26"),
                                            _row("JPM", "2026-10-06")],
                                 today="2026-09-26")
    assert n == 2
    assert _all(c, "JPM") == [("2026-07-06", None, 0.9), ("2026-09-26", None, 1.0),
                              ("2026-10-06", None, 1.0)]


def test_set_coverage_stores_an_unhashable_status_as_error(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    dividends.set_coverage(c, {"JPM": ["ok"], "KO": {"s": 1}, "PG": 3, "XOM": "ok"},
                           day="2026-09-26")
    assert dividends.coverage(c, ["JPM", "KO", "PG", "XOM"]) == {
        "JPM": "error", "KO": "error", "PG": "error", "XOM": "ok"}


@pytest.mark.parametrize("bad", ["2026-10-061", "20261006", "2026-10-06x",
                                 "2026-W40-1", "2026-10-6", "+2026-10-06"])
def test_iso_date_is_strict(bad):
    assert dividends._iso_date(bad) is None


@pytest.mark.parametrize("good", ["2026-10-06", "2026-10-06T00:00:00Z",
                                  "2026-10-06 09:30:00", " 2026-10-06 "])
def test_iso_date_accepts_a_date_or_a_timestamp(good):
    assert dividends._iso_date(good) == "2026-10-06"


def test_strict_dates_reject_a_row_with_trailing_digits(tmp_path):
    c = dividends.init_db(tmp_path / "d.db")
    n = dividends.upsert(c, [_row("JPM", "2026-10-061"), _row("JPM", "20261006")], now="t")
    assert n == 0
    assert _all(c, "JPM") == []


def test_upcoming_accepts_date_objects_and_timestamps_for_the_window(tmp_path):
    import datetime as dt
    c = dividends.init_db(tmp_path / "d.db")
    dividends.upsert(c, [_row("JPM", "2026-10-31"), _row("JPM", "2026-11-01")], now="t")
    by_date = dividends.upcoming(c, ["JPM"], dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    by_stamp = dividends.upcoming(c, ["JPM"], "2026-10-01T00:00:00",
                                  "2026-10-31T23:59:59Z")
    assert [r["ex_date"] for r in by_date] == ["2026-10-31"]
    assert by_stamp == by_date


@pytest.mark.parametrize("start,end", [(None, "2026-10-31"), ("2026-10-01", "garbage")])
def test_upcoming_refuses_an_unusable_window(tmp_path, start, end):
    c = dividends.init_db(tmp_path / "d.db")
    with pytest.raises(ValueError):
        dividends.upcoming(c, ["JPM"], start, end)
