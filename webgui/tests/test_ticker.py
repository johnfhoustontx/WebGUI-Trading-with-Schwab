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
        {"category": "Magnificent 7", "tiles": [
            {"display": "MAG7", "basket": True, "avg_pct": 0.25, "breadth_text": "3/7 up",
             "change_pct": 0.25, "color_state": "risk_on_mild"},
            {"display": "NVDA", "last": 201.0, "change_pct": -0.85, "color_state": "risk_off_mild"}]},
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


def test_ticker_includes_mag7_composite():
    items = ticker.ticker_items(_dash(), _sent())
    mag = [i for i in items if i["text"].startswith("MAG7")]
    assert len(mag) == 1
    # ticker uses 1-decimal %, consistent with the other items (e.g. "SPX -0.3%")
    assert "+0.2%" in mag[0]["text"] and "3/7 up" in mag[0]["text"]
    assert mag[0]["tone"] == "risk_on"


def test_item_class_maps_every_tone_to_fixed_class():
    for tone in ("risk_on", "risk_off", "neutral", "warn"):
        assert isinstance(ticker.item_class(tone), str) and ticker.item_class(tone)
    assert ticker.item_class("bogus") == ticker.item_class("neutral")


def test_ticker_items_empty_caches_safe():
    assert ticker.ticker_items(None, None) == []
    assert ticker.ticker_items({}, {}) == []


def test_ticker_items_dashboard_only_and_sentiment_only_safe():
    # Dashboard-only (no sentiment) → tile-derived items, no raise.
    dash_items = ticker.ticker_items(_dash(), None)
    assert dash_items and all(i["tone"] in {"risk_on", "risk_off", "neutral", "warn"}
                              for i in dash_items)
    assert "VIX" in " ".join(i["text"] for i in dash_items)
    # Sentiment-only (no dashboard) → sentiment/trend/breadth items, no raise.
    sent_items = ticker.ticker_items(None, _sent())
    texts = " ".join(i["text"] for i in sent_items)
    assert "Cautious" in texts and "Neutral" in texts and "1.34" in texts


def test_put_call_tone_flips_around_one():
    hi = ticker.ticker_items(None, {"live": {"sector_pcr": 1.34}})
    assert any(i["text"].startswith("P/C") and i["tone"] == "risk_off" for i in hi)
    lo = ticker.ticker_items(None, {"live": {"sector_pcr": 0.72}})
    assert any(i["text"].startswith("P/C") and i["tone"] == "risk_on" for i in lo)


def test_movers_section_caps_at_four():
    dash = {"categories": [{"category": "Sector SPDR", "tiles": [
        {"display": f"S{n}", "change_pct": float(n), "color_state": "risk_on_mild"}
        for n in range(1, 8)]}]}  # 7 sector tiles
    items = ticker.ticker_items(dash, None)
    # Only the mover items exist here (no sentiment/vol/index); at most 4.
    assert len(items) == 4
    # And they are the largest-|change| movers (S7..S4).
    assert {i["text"].split()[0] for i in items} == {"S7", "S6", "S5", "S4"}


def test_speed_class_maps_numbers_to_finite_buckets():
    # Higher seconds = slower scroll.
    assert ticker.speed_class(90) == "mkt-dur-slow"
    assert ticker.speed_class(60) == "mkt-dur-med"
    assert ticker.speed_class(35) == "mkt-dur-fast"
    # Every result is one of the three finite classes.
    for v in (10, 45, 46, 74, 75, 200):
        assert ticker.speed_class(v) in {"mkt-dur-slow", "mkt-dur-med", "mkt-dur-fast"}
    # Unparseable falls back to the medium bucket (never raises).
    assert ticker.speed_class(None) == "mkt-dur-med"
    assert ticker.speed_class("nope") == "mkt-dur-med"


def test_speed_class_used_in_ticker_css():
    # Each bucket class must be defined in the marquee CSS escape hatch.
    for cls in ("mkt-dur-slow", "mkt-dur-med", "mkt-dur-fast"):
        assert f".{cls}" in ticker._TICKER_CSS
