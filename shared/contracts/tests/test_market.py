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


def test_market_read_defaults_and_shape():
    from shared.contracts.market import MarketRead
    empty = MarketRead()
    assert (empty.rows, empty.history, empty.tally) == ([], [], {})
    assert empty.public is False and empty.final is False and empty.next_slot is None
    mr = MarketRead(date="2026-10-05", ts=1, slot="12:45", interval_min=15,
                    next_slot="13:00", public=True,
                    tally={"tailwind": 1, "headwind": 0, "neutral": 0, "none": 5},
                    rows=[{"key": "direction", "verdict": "tailwind",
                           "facts": {"spx_pct": 0.69, "ndx_pct": 0.79}, "prev": None}],
                    history=[{"slot": "12:45", "verdicts": {"direction": "tailwind"}}])
    assert MarketRead.from_json(mr.to_json()) == mr
    assert mr.model_dump()["rows"][0]["facts"]["spx_pct"] == 0.69
