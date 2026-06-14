from unittest.mock import MagicMock
import order_executor


def test_bucket_b_sizing_equals_preview(monkeypatch):
    from order_preview import preview_bucket_b
    fake_quote = {"QQQ": {"lastPrice": 500.0}}

    resp = MagicMock(); resp.json.return_value = fake_quote; resp.raise_for_status = lambda: None
    monkeypatch.setattr(order_executor.requests, "get", lambda *a, **k: resp)
    captured = {}
    monkeypatch.setattr(order_executor, "_post_order",
                        lambda h, body: captured.setdefault("body", body) or {"success": True})

    trade = {"instrument": "QQQ", "side": "BUY", "max_risk": 150.0}
    order_executor.execute_bucket_b("HASH", trade)

    expected = preview_bucket_b(trade, 500.0)
    leg = captured["body"]["orderLegCollection"][0]
    assert leg["quantity"] == expected["quantity"]
    stop_child = captured["body"]["childOrderStrategies"][0]["childOrderStrategies"][1]
    assert stop_child["stopPrice"] == expected["stop"]


def test_bucket_b_reads_nested_quote_shape(monkeypatch):
    from unittest.mock import MagicMock
    nested = {"QQQ": {"quote": {"lastPrice": 500.0}}}
    resp = MagicMock(); resp.json.return_value = nested; resp.raise_for_status = lambda: None
    monkeypatch.setattr(order_executor.requests, "get", lambda *a, **k: resp)
    captured = {}

    def _capture(h, body):
        captured["body"] = body
        return {"success": True}

    monkeypatch.setattr(order_executor, "_post_order", _capture)
    res = order_executor.execute_bucket_b("HASH", {"instrument": "QQQ", "side": "BUY", "max_risk": 150.0})
    assert res["success"] is True
    assert captured["body"]["orderLegCollection"][0]["quantity"] == 15  # 150/(500*0.02)
