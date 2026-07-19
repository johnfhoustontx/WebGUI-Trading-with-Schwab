from pages import market


def test_bg_class_maps_every_state():
    for state in ("risk_on_strong", "risk_on_mild", "flat",
                  "risk_off_mild", "risk_off_strong", "no_data"):
        cls = market.bg_class(state)
        assert isinstance(cls, str) and cls  # non-empty fixed class
    # green vs red are distinct
    assert market.bg_class("risk_on_strong") != market.bg_class("risk_off_strong")
    # unknown → neutral fallback
    assert market.bg_class("bogus") == market.bg_class("no_data")


def test_tile_text_formats_last_and_change():
    t = {"display": "VIX", "last": 16.13, "change": None, "change_pct": 3.6,
         "value_only": False}
    txt = market.tile_text(t)
    assert txt["last"] == "16.13"
    assert "3.6" in txt["change"] and "%" in txt["change"]


def test_tile_text_negative_change_signs():
    t = {"display": "SPX", "last": 7503.85, "change": -1.5, "change_pct": -0.8,
         "value_only": False}
    txt = market.tile_text(t)
    assert txt["last"] == "7503.85"
    # negatives: no leading '+', explicit '-' from the float formatting
    assert "+" not in txt["change"]
    assert "-1.50" in txt["change"]
    assert "-0.80%" in txt["change"]


def test_tile_text_value_only_hides_change():
    t = {"display": "$TICK", "last": 300.0, "change": None, "change_pct": None,
         "value_only": True}
    txt = market.tile_text(t)
    assert txt["last"] == "300"
    assert txt["change"] == ""      # no change line for value-only


def test_tile_text_no_data():
    t = {"display": "SPX", "last": None, "change_pct": None, "value_only": False}
    txt = market.tile_text(t)
    assert txt["last"] == "—"


def test_tile_text_basket_shows_avg_and_breadth():
    t = {"display": "MAG7", "basket": True, "avg_pct": 0.32,
         "breadth_text": "3/7 up", "change_pct": 0.32, "color_state": "risk_on_mild"}
    txt = market.tile_text(t)
    assert txt["last"] == "+0.32%"      # equal-weighted avg day move (headline)
    assert txt["change"] == "3/7 up"    # breadth subline
    # a negative avg keeps the sign
    t2 = {"display": "MAG7", "basket": True, "avg_pct": -1.5, "breadth_text": "1/7 up"}
    assert market.tile_text(t2)["last"] == "-1.50%"


def test_poll_reads_payload_off_the_event_loop():
    """The dashboard service publishes every ~2s during RTH, so the version gate
    passes nearly every poll → the full ~48-tile payload read must run OFF the
    event loop (the poll is async, routing bus_client.read through run.io_bound)."""
    import inspect
    src = inspect.getsource(market.render)
    assert "async def _poll" in src
    assert "run.io_bound(bus_client.read" in src
