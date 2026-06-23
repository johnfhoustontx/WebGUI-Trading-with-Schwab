from pages.options import leg_editor as LE


def test_normalize_keeps_only_normalized_keys():
    legs = [{"option_type": "call", "side": "long", "strike": 100.0,
             "expiry": "2026-07-17", "qty": 1, "premium": 2.5,
             "_strike_widget": object(), "junk": 1}]
    out = LE.normalize_legs(legs)
    assert set(out[0]) == {"option_type", "side", "strike", "expiry", "qty", "premium"}
    assert out[0]["qty"] == 1


def test_normalize_coerces_qty_and_defaults():
    out = LE.normalize_legs([{"option_type": "put", "side": "short",
                              "strike": 95, "expiry": "2026-07-17"}])
    assert out[0]["qty"] == 1            # default
    assert out[0]["premium"] is None     # default


def test_normalize_drops_premium_when_requested():
    legs = [{"option_type": "put", "side": "short", "strike": 95,
             "expiry": "2026-07-17", "qty": 2, "premium": 1.1}]
    out = LE.normalize_legs(legs, keep_premium=False)
    assert out[0]["premium"] is None
    assert out[0]["qty"] == 2


def test_legs_to_payload_uppercases_symbol_and_strips_dollar():
    legs = [{"option_type": "call", "side": "long", "strike": 100,
             "expiry": "2026-07-17", "qty": 1, "premium": 2.5}]
    p = LE.legs_to_payload("$spx", legs, keep_premium=False)
    assert p["symbol"] == "SPX"
    assert p["legs"][0]["premium"] is None
    assert set(p["legs"][0]) == {"option_type", "side", "strike", "expiry", "qty", "premium"}
