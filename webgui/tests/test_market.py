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
