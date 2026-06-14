"""Tests for sectors_ref — the non-tk sector workbook loader."""
import sectors_ref


def test_load_sectors_data_returns_sector_rows_with_weights():
    rows = sectors_ref.load_sectors_data()
    assert rows, "expected rows from Sectors_Industries_ETFs.xlsx"
    sectors = [r for r in rows if r.get("kind") == "sector"]
    # 11 GICS sectors in the reference workbook.
    assert len(sectors) == 11
    # Every sector row carries the cap weight used by sector_perf.
    weighted = [r for r in sectors if r.get("sp_weight", 0) > 0]
    assert len(weighted) == 11
    # Each sector has an ETF symbol.
    assert all(r.get("etf") for r in sectors)


def test_weights_sum_about_100():
    total = sum(sectors_ref.SP500_SECTOR_WEIGHTS.values())
    assert 95.0 <= total <= 105.0


def test_missing_workbook_returns_empty():
    rows = sectors_ref.load_sectors_data(xlsx_path="does_not_exist.xlsx")
    assert rows == []
