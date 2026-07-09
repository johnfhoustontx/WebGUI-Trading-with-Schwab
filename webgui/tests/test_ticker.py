from pages import ticker


def _dash():
    return {"categories": [
        {"category": "Volatility", "tiles": [
            {"display": "VIX", "last": 16.9, "change_pct": 4.8, "color_state": "risk_off_strong"},
            {"display": "SKEW", "last": 150.0, "change_pct": 2.8, "color_state": "risk_off_strong"}]},
        {"category": "Cash Index", "tiles": [
            {"display": "SPX", "last": 7482.0, "change_pct": -0.3, "color_state": "risk_off_mild"},
            {"display": "NDX", "last": 29252.0, "change_pct": 0.3, "color_state": "risk_on_mild"}]},
        {"category": "Sector SPDR", "tiles": [
            {"display": "XLK", "last": 181.0, "change_pct": 1.4, "color_state": "risk_on_strong"},
            {"display": "XLB", "last": 50.0, "change_pct": -2.6, "color_state": "risk_off_strong"}]},
    ]}


def _sent():
    return {"live": {"composite": {"total_score": "3.9", "bias": "Cautious"},
                     "sector_pcr": 1.34,
                     "breadth": {"interpretation": "A/D 0.41:1 - weak"}},
            "derived": {"trend": {"score": 42.7, "label": "Neutral"}}}


def test_ticker_items_composes_expected_items():
    items = ticker.ticker_items(_dash(), _sent())
    texts = " | ".join(i["text"] for i in items)
    assert "Cautious" in texts and "3.9" in texts
    assert "Neutral" in texts and "42.7" in texts
    assert "VIX" in texts and "SKEW" in texts
    assert "SPX" in texts and "NDX" in texts
    assert "1.34" in texts  # put/call
    # every item carries a known tone
    assert all(i["tone"] in {"risk_on", "risk_off", "neutral", "warn"} for i in items)


def test_item_class_maps_every_tone_to_fixed_class():
    for tone in ("risk_on", "risk_off", "neutral", "warn"):
        assert isinstance(ticker.item_class(tone), str) and ticker.item_class(tone)
    assert ticker.item_class("bogus") == ticker.item_class("neutral")


def test_ticker_items_empty_caches_safe():
    assert ticker.ticker_items(None, None) == []
    assert ticker.ticker_items({}, {}) == []
