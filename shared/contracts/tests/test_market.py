from shared.contracts.market import MarketDashboard


def test_round_trip_and_defaults():
    md = MarketDashboard(
        categories=[{"category": "Volatility",
                     "tiles": [{"display": "VIX", "last": 16.1, "change_pct": 3.6,
                                "color_state": "risk_off_strong"}]}],
        proxy_up=True)
    d = md.model_dump()
    assert d["categories"][0]["category"] == "Volatility"
    assert d["proxy_up"] is True
    # defaults
    assert MarketDashboard().categories == []
    assert MarketDashboard().proxy_up is False
    # envelope-validation round trip
    assert MarketDashboard.from_json(md.to_json()).proxy_up is True
