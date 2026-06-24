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


def test_coerce_strike_snaps_to_nearest_in_options():
    assert LE.coerce_strike(736, [735, 736, 737]) == 736         # exact stays
    assert LE.coerce_strike(737.5, [735, 736, 737, 738]) in (737, 738)  # nearest (tie)
    assert LE.coerce_strike(725, [735, 736, 737]) == 735         # below range -> nearest
    assert LE.coerce_strike(999, [735, 736, 737]) == 737         # above range -> nearest
    assert LE.coerce_strike(737.5, []) is None                   # no options -> None
    assert LE.coerce_strike(None, [735]) is None                 # None stays None


def test_coerce_choice_prefers_value_else_first():
    assert LE.coerce_choice("b", ["a", "b", "c"]) == "b"         # present stays
    assert LE.coerce_choice("z", ["a", "b"]) == "a"              # absent -> first available
    assert LE.coerce_choice(None, ["a"]) == "a"                  # None -> first
    assert LE.coerce_choice("a", []) is None                     # no options -> None


def test_leg_editor_coerces_out_of_options_values_without_raising():
    """Regression: NiceGUI ``ui.select`` raises ``ValueError: Invalid value`` when
    a leg's strike/expiry isn't in the select's options. A leg built off the
    cross-expiry strike union, or copied in from the Simulator, can carry a strike
    (e.g. 737.5) or expiry absent from the chosen expiry's chain — the editor must
    coerce it, not crash ``_render``."""
    from nicegui import ui

    strikes = [735, 736, 737, 738]
    with ui.card() as container:
        ed = LE.build_leg_editor(
            container,
            strikes_for=lambda exp, otype: strikes,
            expiries_for=lambda: ["2026-06-23", "2026-06-26"],
            show_premium=True)
        ed.set_legs([
            {"option_type": "call", "side": "long", "strike": 737.5,   # not in strikes
             "expiry": "2026-06-23", "qty": 1, "premium": None},
            {"option_type": "call", "side": "short", "strike": 999,    # far above
             "expiry": "2099-12-31", "qty": 1, "premium": None},       # expiry absent
        ])  # must NOT raise

    legs = ed.get_legs()
    assert legs[0]["strike"] in strikes          # 737.5 snapped into the options
    assert legs[1]["strike"] in strikes          # 999 snapped to nearest (738)
    assert legs[1]["expiry"] == "2026-06-23"     # absent expiry -> first available
