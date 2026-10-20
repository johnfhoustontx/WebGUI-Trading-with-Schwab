"""A chain with no usable Greeks is named, not drawn as zeros (2026-10-19)."""
from services.options_svc import greeks_census as gc


def _chain(*gammas):
    """One expiry, one strike per gamma, a call and a put each."""
    calls = {f"{700 + i}.0": [{"gamma": g, "openInterest": 100}]
             for i, g in enumerate(gammas)}
    puts = {f"{700 + i}.0": [{"gamma": g, "openInterest": 100}]
            for i, g in enumerate(gammas)}
    return {"underlyingPrice": 701.0,
            "callExpDateMap": {"2026-10-19:0": calls},
            "putExpDateMap": {"2026-10-19:0": puts}}


def test_every_gamma_at_schwabs_placeholder_is_missing():
    assert gc.greeks_missing(_chain(-999.0, -999.0, -999.0)) is True


def test_one_usable_gamma_is_enough():
    assert gc.greeks_missing(_chain(-999.0, 0.02, -999.0)) is False


def test_a_zero_gamma_is_a_reading():
    """A far strike's gamma rounds to 0.0. That is a reading, not an absence."""
    assert gc.greeks_missing(_chain(0.0, 0.0)) is False


def test_nan_and_absent_gammas_are_missing_too():
    chain = _chain(float("nan"), None)
    assert gc.greeks_missing(chain) is True


def test_a_chain_with_no_contracts_is_not_missing_greeks():
    """No contracts is a different absence with its own message on the page."""
    assert gc.greeks_missing({"underlyingPrice": 701.0}) is False
    assert gc.greeks_missing({}) is False
    assert gc.greeks_missing(None) is False


def test_junk_maps_do_not_raise():
    chain = {"callExpDateMap": {"x": "junk"}, "putExpDateMap": ["junk"]}
    assert gc.greeks_missing(chain) is False


def test_mark_clears_walls_and_flip_and_keeps_the_data():
    views = {"GEX": {"data": {"gex": {550.0: {"net": 0.0}}}, "walls": [550.0, 768.0],
                     "flip": 700.0},
             "Charm": {"data": {}, "walls": [], "flip": None}}
    gc.mark_no_greeks(views)
    for entry in views.values():
        assert entry["no_greeks"] is True
        assert entry["walls"] == []
        assert entry["flip"] is None
    assert views["GEX"]["data"] == {"gex": {550.0: {"net": 0.0}}}
