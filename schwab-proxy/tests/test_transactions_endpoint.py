import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from schwab_proxy import _normalize_transactions


def test_normalize_trade_transaction():
    raw = [{
        "activityId": 123456789, "time": "2026-05-20T14:30:00+0000",
        "type": "TRADE",
        "transferItems": [{
            "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
            "amount": 10, "price": 150.0, "positionEffect": "OPENING",
        }],
    }]
    out = _normalize_transactions(raw)
    assert out == [{
        "trade_id": "123456789", "symbol": "AAPL", "asset_type": "EQUITY",
        "underlying": "AAPL", "quantity": 10.0, "price": 150.0,
        "instruction": "BUY", "trade_date": "2026-05-20",
    }]


def test_normalize_skips_non_trades():
    raw = [{"activityId": 1, "type": "DIVIDEND_OR_INTEREST", "transferItems": []}]
    assert _normalize_transactions(raw) == []


def test_normalize_sell_uses_negative_amount():
    raw = [{
        "activityId": 222, "time": "2026-05-21T10:00:00+0000",
        "type": "TRADE",
        "transferItems": [{
            "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
            "amount": -7, "price": 152.0, "positionEffect": "CLOSING",
        }],
    }]
    out = _normalize_transactions(raw)
    assert out[0]["instruction"] == "SELL"
    assert out[0]["quantity"] == 7.0


def test_normalize_option_carries_underlying():
    raw = [{
        "activityId": 333, "time": "2026-05-22T11:00:00+0000",
        "type": "TRADE",
        "transferItems": [{
            "instrument": {
                "symbol": "AAPL  260116C00150000",
                "assetType": "OPTION",
                "underlyingSymbol": "AAPL",
            },
            "amount": 1, "price": 3.25, "positionEffect": "OPENING",
        }],
    }]
    out = _normalize_transactions(raw)
    assert out[0]["underlying"] == "AAPL"
    assert out[0]["asset_type"] == "OPTION"


def test_normalize_picks_security_leg_over_currency():
    # Real Schwab TRADEs carry a CURRENCY (cash) leg alongside the security leg.
    # We must capture the security, not the cash movement.
    raw = [{
        "activityId": 444, "time": "2026-05-23T12:00:00+0000",
        "type": "TRADE",
        "transferItems": [
            {"instrument": {"symbol": "CURRENCY_USD", "assetType": "CURRENCY"},
             "amount": 6600.0, "price": 0.0},
            {"instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
             "amount": 44, "price": 150.0, "positionEffect": "OPENING"},
        ],
    }]
    out = _normalize_transactions(raw)
    assert len(out) == 1
    assert out[0]["symbol"] == "AAPL"
    assert out[0]["asset_type"] == "EQUITY"
    assert out[0]["quantity"] == 44.0
    assert out[0]["price"] == 150.0


def test_normalize_skips_currency_only_trade():
    # A TRADE with only a cash/currency leg (no security) is not a real trade
    # for our purposes — skip it entirely rather than emitting a CURRENCY row.
    raw = [{
        "activityId": 555, "time": "2026-05-24T09:00:00+0000",
        "type": "TRADE",
        "transferItems": [
            {"instrument": {"symbol": "CURRENCY_USD", "assetType": "CURRENCY"},
             "amount": 0.65, "price": 0.0},
        ],
    }]
    assert _normalize_transactions(raw) == []
