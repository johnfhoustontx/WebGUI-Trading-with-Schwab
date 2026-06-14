from src.sectors import classify_positions, normalize_sector_name, sector_weights


def fake_classify(underlying):
    table = {"AAPL": ("XLK", "Technology"), "XOM": ("XLE", "Energy")}
    etf, name = table.get(underlying, ("SPY", "Unknown"))
    return {"sector_etf": etf, "sector": name}


def raising_classify(underlying):
    # Proves the classifier is never invoked for futures.
    raise AssertionError(f"classifier must not be called (got {underlying!r})")


def test_options_roll_into_underlying_sector():
    positions = [
        {"underlying": "AAPL", "asset_type": "OPTION", "market_value": 1000.0},
        {"underlying": "AAPL", "asset_type": "EQUITY", "market_value": 1750.0},
    ]
    rows = classify_positions(positions, classify=fake_classify)
    assert all(r["sector"] == "Technology" for r in rows)
    assert all(r["sector_etf"] == "XLK" for r in rows)


def test_futures_go_to_separate_bucket():
    positions = [{"underlying": "/ES", "asset_type": "FUTURE", "market_value": 5000.0}]
    rows = classify_positions(positions, classify=fake_classify)
    assert rows[0]["sector"] == "Futures"
    assert rows[0]["sector_etf"] is None


def test_futures_do_not_call_classifier():
    positions = [{"underlying": "/ES", "asset_type": "FUTURE", "market_value": 5000.0}]
    # raising_classify would blow up if invoked for the future row.
    rows = classify_positions(positions, classify=raising_classify)
    assert rows[0]["sector"] == "Futures"
    assert rows[0]["sector_etf"] is None


def test_inputs_not_mutated_and_keys_preserved():
    positions = [
        {"underlying": "AAPL", "asset_type": "EQUITY", "market_value": 1750.0, "symbol": "AAPL"},
    ]
    rows = classify_positions(positions, classify=fake_classify)
    # Original dict untouched.
    assert "sector" not in positions[0]
    assert "sector_etf" not in positions[0]
    # Original keys preserved on the enriched copy.
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["market_value"] == 1750.0
    assert rows[0]["asset_type"] == "EQUITY"


def test_classify_empty_input():
    assert classify_positions([], classify=fake_classify) == []


def test_sector_weights_sum_to_one():
    rows = [
        {"sector": "Technology", "market_value": 3000.0},
        {"sector": "Energy", "market_value": 1000.0},
    ]
    w = sector_weights(rows)
    assert abs(w["Technology"] - 0.75) < 1e-9
    assert abs(w["Energy"] - 0.25) < 1e-9
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_sector_weights_groups_multiple_rows_per_sector():
    rows = [
        {"sector": "Technology", "market_value": 1000.0},
        {"sector": "Technology", "market_value": 2000.0},
        {"sector": "Energy", "market_value": 1000.0},
    ]
    w = sector_weights(rows)
    assert abs(w["Technology"] - 0.75) < 1e-9
    assert abs(w["Energy"] - 0.25) < 1e-9


def test_sector_weights_uses_absolute_denominator():
    # A short position (negative MV) must not blow up the denominator into
    # nonsense. Denominator is sum of absolute market values: |400| + |-100| = 500.
    rows = [
        {"sector": "Technology", "market_value": 400.0},
        {"sector": "Energy", "market_value": -100.0},
    ]
    w = sector_weights(rows)
    assert abs(w["Technology"] - 0.8) < 1e-9
    assert abs(w["Energy"] - (-0.2)) < 1e-9


def test_sector_weights_empty_input():
    assert sector_weights([]) == {}


def test_sector_weights_zero_total():
    rows = [
        {"sector": "Technology", "market_value": 0.0},
        {"sector": "Energy", "market_value": 0.0},
    ]
    assert sector_weights(rows) == {}


def test_normalize_sector_name_finviz_to_gics():
    # FinViz/Yahoo-style names map to the GICS spellings used by the benchmark.
    assert normalize_sector_name("Consumer Cyclical") == "Consumer Discretionary"
    assert normalize_sector_name("Consumer Defensive") == "Consumer Staples"
    assert normalize_sector_name("Financial") == "Financials"
    assert normalize_sector_name("Financial Services") == "Financials"
    assert normalize_sector_name("Basic Materials") == "Materials"


def test_normalize_sector_name_gics_passthrough():
    # Already-GICS names are unchanged.
    assert normalize_sector_name("Technology") == "Technology"
    assert normalize_sector_name("Communication Services") == "Communication Services"


def test_normalize_sector_name_unknown_and_unmapped_passthrough():
    assert normalize_sector_name("Unknown") == "Unknown"
    assert normalize_sector_name("Some New Sector") == "Some New Sector"
