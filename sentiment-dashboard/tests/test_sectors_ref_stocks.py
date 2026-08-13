"""Stocks tab loader — 5 constituents per (sector, industry)."""
import pytest

import sectors_ref


@pytest.fixture(autouse=True)
def _clear_cache():
    sectors_ref.reset_stocks_cache()
    yield
    sectors_ref.reset_stocks_cache()


def test_loads_every_constituent_row():
    assert len(sectors_ref.load_stocks_data()) == 370


def test_row_shape_carries_the_workbook_columns():
    row = sectors_ref.load_stocks_data()[0]

    assert set(row) == {"sector", "industry", "rank", "symbol", "company", "etfs"}
    assert row["symbol"]
    assert row["sector"]
    assert isinstance(row["etfs"], list)


def test_reference_etfs_are_split_into_a_list():
    rows = sectors_ref.load_stocks_data()
    multi = next(r for r in rows if len(r["etfs"]) > 1)

    assert all(e == e.strip() and e for e in multi["etfs"])


def test_covers_seventy_four_industries_with_five_names_each():
    by_industry = sectors_ref.constituents_by_industry()

    assert len(by_industry) == 74
    assert {len(v) for v in by_industry.values()} == {5}


def test_industry_keys_are_sector_industry_pairs():
    by_industry = sectors_ref.constituents_by_industry()
    key = next(iter(by_industry))

    assert isinstance(key, tuple) and len(key) == 2


def test_stock_symbols_are_deduped():
    # 370 rows but 311 unique names — a name legitimately represents more
    # than one industry, and it must be fetched once, not twice.
    symbols = sectors_ref.stock_symbols()

    assert len(symbols) == 311
    assert len(symbols) == len(set(symbols))


def test_stops_at_the_trailing_note_block():
    rows = sectors_ref.load_stocks_data()

    assert all(r["symbol"] for r in rows)
    assert not any("Selection basis" in str(r["sector"]) for r in rows)


def test_missing_workbook_returns_empty_not_a_raise(tmp_path):
    missing = tmp_path / "nope.xlsx"

    assert sectors_ref.load_stocks_data(missing) == []
    assert sectors_ref.stock_symbols(missing) == []
    assert sectors_ref.constituents_by_industry(missing) == {}


def test_cache_returns_the_same_object_until_mtime_changes():
    first = sectors_ref.load_stocks_data()
    second = sectors_ref.load_stocks_data()

    assert first is second

    sectors_ref.reset_stocks_cache()
    assert sectors_ref.load_stocks_data() is not first


def test_stocks_cache_is_separate_from_the_sectors_cache():
    sectors_ref.load_stocks_data()
    sectors_ref.reset_cache()                 # the sectors-tab cache

    assert sectors_ref.load_stocks_data() is sectors_ref.load_stocks_data()
