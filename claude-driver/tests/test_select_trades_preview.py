from unittest.mock import MagicMock

import trade_selector


def test_preview_price_reads_nested_schwab_quote_shape(monkeypatch):
    # Schwab /quotes nests lastPrice under a per-symbol "quote" key.
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json.return_value = {"QQQ": {"quote": {"lastPrice": 512.34}}}
    monkeypatch.setattr(trade_selector.requests, "get", lambda *a, **k: resp)
    assert trade_selector._preview_price("QQQ", spx_spot=5000.0) == 512.34


def test_preview_price_returns_zero_on_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("proxy down")
    monkeypatch.setattr(trade_selector.requests, "get", boom)
    assert trade_selector._preview_price("QQQ", spx_spot=5000.0) == 0.0


def test_preview_attached_per_bucket(monkeypatch):
    # Stub the price fetch used for sizing previews.
    monkeypatch.setattr(trade_selector, "_preview_price",
                        lambda inst, spx_spot: 500.0 if inst == "QQQ" else spx_spot)
    trades = [
        {"bucket": "A", "structure": "iron_condor", "contracts": 1, "max_risk": 300.0,
         "profit_target": 0.50, "strikes": {"put_short": 4950}},
        {"bucket": "B", "instrument": "QQQ", "side": "BUY", "max_risk": 150.0},
        {"bucket": "C", "instrument": "/MESO", "side": "BUY", "contracts": 2,
         "stop_ticks": 10, "target_ticks": 30},
    ]
    out = trade_selector.attach_previews(trades, spx_spot=5000.0)
    assert out[0]["preview"]["quantity_unit"] == "contracts"
    assert out[1]["preview"]["quantity"] == 15
    assert out[2]["preview"]["stop"] == 4997.5
