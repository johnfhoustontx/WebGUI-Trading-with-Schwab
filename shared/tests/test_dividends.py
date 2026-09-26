"""The dividends store: a forward ex-date calendar beside the earnings one."""
import ast
import math
from pathlib import Path

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
