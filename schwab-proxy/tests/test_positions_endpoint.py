import sys, pathlib
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import schwab_proxy
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


# -- _merge_positions: fold same-symbol rows across accounts ----------------

def _pos(symbol="AAPL", asset_type="EQUITY", underlying=None, quantity=10.0,
         avg_price=150.0, market_value=1500.0, day_pl=5.0, total_pl=100.0):
    return {
        "symbol": symbol, "asset_type": asset_type,
        "underlying": underlying or symbol, "quantity": quantity,
        "avg_price": avg_price, "market_value": market_value,
        "day_pl": day_pl, "total_pl": total_pl,
    }


def test_merge_positions_empty_is_empty():
    assert schwab_proxy._merge_positions([]) == []


def test_merge_positions_distinct_symbols_pass_through_in_order():
    rows = [_pos("AAPL"), _pos("MSFT", market_value=900.0)]
    out = schwab_proxy._merge_positions(rows)
    # Distinct symbols are untouched (including avg_price) and keep first-seen order.
    assert [p["symbol"] for p in out] == ["AAPL", "MSFT"]
    assert out[0] == _pos("AAPL")


def test_merge_positions_combines_same_symbol_across_accounts():
    rows = [
        _pos("AAPL", quantity=10.0, avg_price=150.0, market_value=1500.0,
             day_pl=5.0, total_pl=100.0),   # account #1
        _pos("AAPL", quantity=5.0, avg_price=180.0, market_value=900.0,
             day_pl=2.0, total_pl=50.0),    # account #2
    ]
    out = schwab_proxy._merge_positions(rows)
    assert len(out) == 1
    merged = out[0]
    assert merged["symbol"] == "AAPL"
    assert merged["quantity"] == 15.0
    assert merged["market_value"] == 2400.0
    assert merged["day_pl"] == 7.0
    assert merged["total_pl"] == 150.0
    # Average price is re-weighted by quantity: (10*150 + 5*180) / 15 = 160.
    assert merged["avg_price"] == 160.0


# -- get_positions_default: aggregate ALL linked accounts -------------------

def _accounts_then_positions(by_hash):
    """Build a fake trader_request that serves accountNumbers + per-account chains."""
    def fake_trader_request(method, endpoint, json_body=None):
        if endpoint == "/accounts/accountNumbers":
            return {"status_code": 200,
                    "data": [{"hashValue": h} for h in by_hash],
                    "error": None}
        for h, payload in by_hash.items():
            if endpoint == f"/accounts/{h}?fields=positions":
                if payload is None:  # simulate an account that errors upstream
                    return {"status_code": 500, "data": None, "error": "boom"}
                return {"status_code": 200,
                        "data": {"securitiesAccount": {"positions": payload}},
                        "error": None}
        raise AssertionError(f"unexpected endpoint {endpoint}")
    return fake_trader_request


def _raw(symbol, longq, avg, mv):
    return {"instrument": {"symbol": symbol, "assetType": "EQUITY"},
            "longQuantity": longq, "shortQuantity": 0, "averagePrice": avg,
            "marketValue": mv, "currentDayProfitLoss": 0.0, "longOpenProfitLoss": 0.0}


def test_get_positions_default_merges_across_all_accounts(monkeypatch):
    by_hash = {
        "H1": [_raw("AAPL", 10, 150.0, 1500.0)],
        "H2": [_raw("AAPL", 5, 180.0, 900.0), _raw("MSFT", 3, 300.0, 900.0)],
    }
    monkeypatch.setattr(schwab_proxy, "trader_request",
                        _accounts_then_positions(by_hash))
    out = schwab_proxy.get_positions_default()
    by_symbol = {p["symbol"]: p for p in out["positions"]}
    assert set(by_symbol) == {"AAPL", "MSFT"}
    assert by_symbol["AAPL"]["quantity"] == 15.0        # 10 from H1 + 5 from H2
    assert by_symbol["AAPL"]["avg_price"] == 160.0      # quantity-weighted
    assert by_symbol["MSFT"]["quantity"] == 3.0         # only in H2


def test_get_positions_default_skips_a_failed_account(monkeypatch):
    # The FIRST account errors upstream; aggregation must still return the
    # second account's positions (the old hashes[0]-only path would raise here).
    by_hash = {"H1": None, "H2": [_raw("MSFT", 3, 300.0, 900.0)]}
    monkeypatch.setattr(schwab_proxy, "trader_request",
                        _accounts_then_positions(by_hash))
    out = schwab_proxy.get_positions_default()
    assert [p["symbol"] for p in out["positions"]] == ["MSFT"]
