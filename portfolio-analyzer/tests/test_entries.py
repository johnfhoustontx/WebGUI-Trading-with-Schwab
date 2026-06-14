from src.entries import weighted_avg_entry


def test_weighted_avg_entry_combines_buys():
    trades = [
        {"symbol": "AAPL", "instruction": "BUY", "quantity": 10, "price": 150.0, "trade_date": "2026-05-20"},
        {"symbol": "AAPL", "instruction": "BUY", "quantity": 30, "price": 160.0, "trade_date": "2026-05-30"},
    ]
    e = weighted_avg_entry(trades)["AAPL"]
    assert abs(e["avg_price"] - 157.5) < 1e-6
    assert e["entry_date"] == "2026-05-28"  # weighted toward the larger, later buy
    assert e["total_quantity"] == 40.0


def test_sells_are_ignored():
    trades = [
        {"symbol": "AAPL", "instruction": "BUY", "quantity": 10, "price": 150.0, "trade_date": "2026-05-20"},
        {"symbol": "AAPL", "instruction": "SELL", "quantity": 5, "price": 200.0, "trade_date": "2026-05-25"},
    ]
    e = weighted_avg_entry(trades)["AAPL"]
    # SELL must not influence price, date, or quantity.
    assert abs(e["avg_price"] - 150.0) < 1e-6
    assert e["entry_date"] == "2026-05-20"
    assert e["total_quantity"] == 10.0


def test_symbol_with_only_sells_does_not_appear():
    trades = [
        {"symbol": "MSFT", "instruction": "SELL", "quantity": 5, "price": 400.0, "trade_date": "2026-05-25"},
    ]
    assert weighted_avg_entry(trades) == {}


def test_multiple_symbols_handled_independently():
    trades = [
        {"symbol": "AAPL", "instruction": "BUY", "quantity": 10, "price": 150.0, "trade_date": "2026-05-20"},
        {"symbol": "MSFT", "instruction": "BUY", "quantity": 4, "price": 400.0, "trade_date": "2026-01-10"},
    ]
    result = weighted_avg_entry(trades)
    assert set(result) == {"AAPL", "MSFT"}
    assert abs(result["AAPL"]["avg_price"] - 150.0) < 1e-6
    assert result["AAPL"]["entry_date"] == "2026-05-20"
    assert abs(result["MSFT"]["avg_price"] - 400.0) < 1e-6
    assert result["MSFT"]["entry_date"] == "2026-01-10"


def test_empty_input_returns_empty_dict():
    assert weighted_avg_entry([]) == {}


def test_single_buy_passthrough():
    trades = [
        {"symbol": "NVDA", "instruction": "BUY", "quantity": 7, "price": 123.45, "trade_date": "2026-03-15"},
    ]
    e = weighted_avg_entry(trades)["NVDA"]
    assert abs(e["avg_price"] - 123.45) < 1e-6
    assert e["entry_date"] == "2026-03-15"
    assert e["total_quantity"] == 7.0


def test_int_inputs_are_handled():
    trades = [
        {"symbol": "F", "instruction": "BUY", "quantity": 100, "price": 12, "trade_date": "2026-04-01"},
        {"symbol": "F", "instruction": "BUY", "quantity": 100, "price": 14, "trade_date": "2026-04-01"},
    ]
    e = weighted_avg_entry(trades)["F"]
    assert isinstance(e["avg_price"], float)
    assert abs(e["avg_price"] - 13.0) < 1e-6
    assert e["entry_date"] == "2026-04-01"
