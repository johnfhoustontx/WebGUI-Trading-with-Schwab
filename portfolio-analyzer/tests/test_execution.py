# portfolio-analyzer/tests/test_execution.py
import pytest
from src.execution import build_order_body, ExecutionClient


# ----------------------------- build_order_body ----------------------------- #

def test_build_order_body_market_buy_equity_exact_shape():
    body = build_order_body("AAPL", "BUY", 10)
    assert body == {
        "orderType": "MARKET",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {"instruction": "BUY", "quantity": 10,
             "instrument": {"symbol": "AAPL", "assetType": "EQUITY"}}
        ],
    }
    assert "price" not in body


def test_build_order_body_limit_includes_price_and_order_type():
    body = build_order_body("MSFT", "SELL", 5, order_type="LIMIT", price=123.45)
    assert body["orderType"] == "LIMIT"
    assert body["price"] == 123.45
    assert body["orderLegCollection"][0]["instruction"] == "SELL"


def test_build_order_body_limit_without_price_raises():
    with pytest.raises(ValueError):
        build_order_body("AAPL", "BUY", 10, order_type="LIMIT")


def test_build_order_body_invalid_instruction_raises():
    with pytest.raises(ValueError):
        build_order_body("AAPL", "HOLD", 10)


def test_build_order_body_non_positive_quantity_raises():
    with pytest.raises(ValueError):
        build_order_body("AAPL", "BUY", 0)
    with pytest.raises(ValueError):
        build_order_body("AAPL", "BUY", -3)


def test_build_order_body_invalid_order_type_raises():
    with pytest.raises(ValueError):
        build_order_body("AAPL", "BUY", 10, order_type="STOP")


def test_build_order_body_uppercases_symbol():
    body = build_order_body("aapl", "BUY", 10)
    assert body["orderLegCollection"][0]["instrument"]["symbol"] == "AAPL"


# ------------------------------ preview_order ------------------------------- #

def test_preview_order_returns_body_without_http_call(monkeypatch):
    client = ExecutionClient(base_url="http://x")

    def _boom(*a, **k):
        raise AssertionError("preview_order must not make an HTTP call")

    monkeypatch.setattr(client.session, "post", _boom)
    body = client.preview_order("AAPL", "BUY", 10)
    assert body == build_order_body("AAPL", "BUY", 10)


# ------------------------------- place_order -------------------------------- #

class _FakeResp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def test_place_order_posts_to_right_url_and_returns_json(monkeypatch):
    client = ExecutionClient(base_url="http://x")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp({"status": "submitted"})

    monkeypatch.setattr(client.session, "post", fake_post)
    body = build_order_body("AAPL", "BUY", 10)
    result = client.place_order("HASH123", body)

    assert captured["url"] == "http://x/orders/HASH123"
    assert captured["json"] == body
    assert result == {"status": "submitted"}
