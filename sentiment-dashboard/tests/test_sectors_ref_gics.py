"""GICS Map tab loader — sector -> sub-industry -> representative symbols.

The momentum cascade (and through it the Bull / Bear Map and the Momentum
page) takes its middle level and its stock universe from this tab.
"""
import pytest

import sectors_ref


@pytest.fixture(autouse=True)
def _clear_cache():
    sectors_ref.reset_gics_cache()
    yield
    sectors_ref.reset_gics_cache()


def test_loads_every_sub_industry_row():
    # 163 GICS sub-industries (the March 2023 structure).
    assert len(sectors_ref.load_gics_map()) == 163


def test_row_shape_carries_the_hierarchy_and_its_symbols():
    row = sectors_ref.load_gics_map()[0]

    assert set(row) == {"sector", "industry_group", "industry",
                        "sub_industry", "code", "symbols"}
    assert row["sector"] == "Energy"
    assert row["sub_industry"] == "Oil & Gas Drilling"
    assert row["code"] == "10101010"
    assert row["symbols"] == ["NE", "RIG", "VAL", "HP", "PTEN"]


def test_codes_are_eight_digit_strings_and_unique():
    codes = [r["code"] for r in sectors_ref.load_gics_map()]

    assert len(codes) == len(set(codes))
    assert all(isinstance(c, str) and len(c) == 8 and c.isdigit() for c in codes)


def test_blank_symbol_cells_are_dropped_not_kept_as_empty_strings():
    rows = {r["sub_industry"]: r for r in sectors_ref.load_gics_map()}

    # Three symbols listed, two blank cells.
    assert rows["Industrial Gases"]["symbols"] == ["LIN", "APD", "AIQUY"]
    # A sub-industry with no listed symbol is still a row — the cascade reports
    # it rather than silently losing it.
    assert rows["Drug Retail"]["symbols"] == []


def test_sector_names_match_the_sector_etf_rows():
    # The cascade joins a GICS sector to its SPDR ETF by NAME, so a spelling
    # drift here would drop a whole sector from the map.
    gics = {r["sector"] for r in sectors_ref.load_gics_map()}
    etf_rows = {r["sector"] for r in sectors_ref.load_sectors_data()
                if r["kind"] == "sector"}

    assert gics == etf_rows
    assert len(gics) == 11


def test_gics_symbols_is_the_deduped_universe_in_workbook_order():
    symbols = sectors_ref.gics_symbols()

    assert len(symbols) == 719
    assert len(symbols) == len(set(symbols))
    assert symbols[:3] == ["NE", "RIG", "VAL"]


def test_renamed_tickers_carry_their_new_symbols():
    # Operator corrections, 2026-09-27: each old ticker returned no quote and no
    # history from Schwab; the new one quotes.
    rows = {r["sub_industry"]: r["symbols"] for r in sectors_ref.load_gics_map()}

    assert "BNY" in rows["Asset Management & Custody Banks"]
    assert "MRSH" in rows["Insurance Brokers"]
    assert "ECHO" in rows["Cable & Satellite"]
    assert "ACH" in rows["Health Care Distributors"]


def test_symbols_schwab_calls_invalid_are_gone():
    # Schwab lists these six in ``invalidSymbols`` (measured on prod
    # 2026-09-27); the operator dropped them from the map.
    dropped = {"BK", "MMC", "SATS", "OMI",
               "TEF", "NSA", "LEG", "ATGE", "PCH", "SMNEY"}

    assert not dropped & set(sectors_ref.gics_symbols())
    rows = {r["sub_industry"]: r["symbols"] for r in sectors_ref.load_gics_map()}
    # A removal closes the gap rather than leaving a hole mid-row.
    assert rows["Home Furnishings"] == ["MHK", "SGI", "LZB", "ETD"]
    assert rows["Timber REITs"] == ["WY", "RYN"]


def test_share_classes_are_spelled_the_way_schwab_quotes_them():
    # The workbook writes BRK.B; Schwab answers only BRK/B (measured on prod
    # 2026-09-27: BRK.B returned no bars and no quote, BRK/B both).
    symbols = set(sectors_ref.gics_symbols())

    assert {"BRK/B", "BF/B"} <= symbols
    assert not any("." in s for s in symbols)


def test_a_missing_workbook_yields_an_empty_map(tmp_path):
    assert sectors_ref.load_gics_map(tmp_path / "absent.xlsx") == []
