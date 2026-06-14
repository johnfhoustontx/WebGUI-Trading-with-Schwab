from order_preview import preview_bucket_b, preview_bucket_c, preview_bucket_a


def test_bucket_b_buy_sizing_matches_executor_math():
    p = preview_bucket_b({"instrument": "QQQ", "side": "BUY", "max_risk": 150.0}, price=500.0)
    assert p["side"] == "BUY"
    assert p["quantity"] == 15
    assert p["quantity_unit"] == "shares"
    assert p["stop"] == 490.0
    assert p["target"] == 520.0
    assert p["est_entry"] == 500.0
    assert p["max_risk"] == 150.0


def test_bucket_b_short_inverts_stop_and_target():
    p = preview_bucket_b({"instrument": "QQQ", "side": "SELL_SHORT", "max_risk": 150.0}, price=500.0)
    assert p["stop"] == 510.0
    assert p["target"] == 480.0


def test_bucket_b_zero_price_returns_none_sizing():
    p = preview_bucket_b({"instrument": "QQQ", "side": "BUY", "max_risk": 150.0}, price=0.0)
    assert p["quantity"] is None
    assert p["stop"] is None


def test_bucket_c_buy_points():
    p = preview_bucket_c({"instrument": "/MESO", "side": "BUY", "contracts": 2, "stop_ticks": 10, "target_ticks": 30}, spx_price=5000.0)
    assert p["side"] == "BUY"
    assert p["quantity"] == 2
    assert p["quantity_unit"] == "contracts"
    assert p["stop"] == 4997.5
    assert p["target"] == 5007.5


def test_bucket_a_reports_contracts_and_max_risk():
    p = preview_bucket_a({"structure": "iron_condor", "contracts": 1, "max_risk": 300.0, "profit_target": 0.50, "strikes": {"put_short": 4950, "put_long": 4925, "call_short": 5050, "call_long": 5075}})
    assert p["quantity"] == 1
    assert p["quantity_unit"] == "contracts"
    assert p["max_risk"] == 300.0
    assert p["profit_target_dollars"] == 150.0
    assert p["structure"] == "iron_condor"
