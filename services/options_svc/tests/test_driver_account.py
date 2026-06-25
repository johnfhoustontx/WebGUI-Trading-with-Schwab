"""Tests for the isolated driver paper account (compute.ensure_driver_account /
driver_account_view / driver_account_perf).

Every test monkeypatches ``compute.DRIVER_PAPER_DB`` to a ``tmp_path`` DB so the
real ``options-scanner/data/paper_account*.db`` files are NEVER touched. The
``paper_account_db.connect`` helper auto-creates the schema for a new path, so a
fresh tmp DB needs no fixture beyond ``ensure_driver_account``.
"""
from services.options_svc import compute


def test_ensure_and_view_driver_account(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account(starting_balance=25000.0)
    v = compute.driver_account_view()
    assert v["has_account"] is True
    assert v["snapshot"]["cash"] == 25000.0
    assert v["snapshot"]["session_pnl"] == 0.0
    assert v["positions"] == [] and v["snapshot"]["open_count"] == 0


def test_driver_account_perf_reads_db(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    perf = compute.driver_account_perf()
    assert perf["total_trades"] == 0 and perf["win_rate"] == 0.0
