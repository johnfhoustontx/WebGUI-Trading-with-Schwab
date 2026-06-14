import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from schwab_proxy import _normalize_positions

def test_normalize_positions_equity():
    raw = {"securitiesAccount": {"positions": [
        {"instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
         "longQuantity": 10, "shortQuantity": 0, "averagePrice": 150.0,
         "marketValue": 1750.0, "currentDayProfitLoss": 12.0,
         "longOpenProfitLoss": 250.0},
    ]}}
    out = _normalize_positions(raw)
    assert out == [{
        "symbol": "AAPL", "asset_type": "EQUITY", "underlying": "AAPL",
        "quantity": 10.0, "avg_price": 150.0, "market_value": 1750.0,
        "day_pl": 12.0, "total_pl": 250.0,
    }]

def test_normalize_positions_option_uses_underlying():
    raw = {"securitiesAccount": {"positions": [
        {"instrument": {"symbol": "AAPL  260116C00150000", "assetType": "OPTION",
                        "underlyingSymbol": "AAPL"},
         "longQuantity": 2, "shortQuantity": 0, "averagePrice": 5.0,
         "marketValue": 1000.0, "currentDayProfitLoss": -20.0,
         "longOpenProfitLoss": 100.0},
    ]}}
    out = _normalize_positions(raw)
    assert out[0]["asset_type"] == "OPTION"
    assert out[0]["underlying"] == "AAPL"

def test_normalize_positions_short_quantity_is_negative():
    raw = {"securitiesAccount": {"positions": [
        {"instrument": {"symbol": "TSLA", "assetType": "EQUITY"},
         "longQuantity": 0, "shortQuantity": 5, "averagePrice": 200.0,
         "marketValue": -1000.0, "currentDayProfitLoss": 0.0,
         "longOpenProfitLoss": 0.0},
    ]}}
    out = _normalize_positions(raw)
    assert out[0]["quantity"] == -5.0

def test_normalize_positions_short_uses_short_open_pl():
    raw = {"securitiesAccount": {"positions": [
        {"instrument": {"symbol": "TSLA", "assetType": "EQUITY"},
         "longQuantity": 0, "shortQuantity": 5, "averagePrice": 200.0,
         "marketValue": -1000.0, "currentDayProfitLoss": 0.0,
         "shortOpenProfitLoss": 150.0},
    ]}}
    out = _normalize_positions(raw)
    assert out[0]["total_pl"] == 150.0
