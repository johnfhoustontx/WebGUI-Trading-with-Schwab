"""Persistence identity + map for the day's scan union."""
import pytest

from services.options_svc import compute


def test_setup_key_excludes_strikes():
    a = {"symbol": "MU", "type": "PCS", "expiration": "2026-10-17",
         "short_strike": 180, "long_strike": 175}
    b = dict(a, short_strike=185, long_strike=180)
    assert compute.setup_key(a) == compute.setup_key(b) == "MU|PCS|2026-10-17"


def test_setup_key_separates_expirations_and_structures():
    base = {"symbol": "MU", "type": "PCS", "expiration": "2026-10-17"}
    assert compute.setup_key(base) != compute.setup_key(dict(base, type="CCS"))
    assert compute.setup_key(base) != compute.setup_key(
        dict(base, expiration="2026-10-24"))


def test_setup_key_normalises_case_and_timestamped_expiry():
    assert compute.setup_key(
        {"symbol": "mu", "type": "pcs",
         "expiration": "2026-10-17T00:00:00"}) == "MU|PCS|2026-10-17"


def test_setup_key_uses_the_front_leg_expiry_for_directional():
    row = {"symbol": "NVDA", "type": "LONG_CALL",
           "legs": [{"expiry": "2026-11-21"}, {"expiry": "2026-10-17"}]}
    assert compute.setup_key(row) == "NVDA|LONG_CALL|2026-10-17"


@pytest.mark.parametrize("row", [
    None, "MU", {}, {"symbol": "MU", "type": "PCS"},
    {"symbol": "", "type": "PCS", "expiration": "2026-10-17"},
    {"symbol": "MU", "type": "PCS", "expiration": ""},
    {"symbol": "MU", "type": "PCS", "legs": [{"expiry": ""}]},
])
def test_setup_key_is_none_when_any_component_is_missing(row):
    # A row with no derivable key has NO persistence. It must never be folded
    # into another setup's group, which a "" or partial key would do.
    assert compute.setup_key(row) is None


@pytest.mark.parametrize("bad", [
    float("nan"), 1792713600000, True, "soon", "10/17/2026", "", None,
])
def test_an_unparseable_expiration_yields_no_key_never_a_fabricated_one(bad):
    # The slice this replaced minted "MU|PCS|nan" / "MU|PCS|1792713600".
    assert compute.setup_key(
        {"symbol": "MU", "type": "PCS", "expiration": bad}) is None


def test_padded_expirations_do_not_collide():
    # " 2026-10-17 "[:10] and " 2026-10-19 "[:10] both gave " 2026-10-1".
    a = compute.setup_key({"symbol": "MU", "type": "PCS",
                           "expiration": " 2026-10-17 "})
    b = compute.setup_key({"symbol": "MU", "type": "PCS",
                           "expiration": " 2026-10-19 "})
    assert a == "MU|PCS|2026-10-17"
    assert b == "MU|PCS|2026-10-19"


def test_the_leg_fallback_accepts_the_producers_own_spelling():
    # strategy_scanner._leg_from writes "expiration"; the normalized Tier-1 leg
    # dict writes "expiry". Both must resolve.
    row = {"symbol": "NVDA", "type": "LONG_CALL",
           "legs": [{"expiration": "2026-11-21"}, {"expiration": "2026-10-17"}]}
    assert compute.setup_key(row) == "NVDA|LONG_CALL|2026-10-17"


def test_the_top_level_expiration_wins_and_already_is_the_front_leg():
    # _assemble emits BOTH, with expiration already == min(leg expirations), so
    # the two cannot disagree for a real row. Pinning the precedence anyway.
    row = {"symbol": "NVDA", "type": "LONG_CALL", "expiration": "2026-10-17",
           "legs": [{"expiration": "2026-10-17"}, {"expiration": "2026-11-21"}]}
    assert compute.setup_key(row) == "NVDA|LONG_CALL|2026-10-17"


def test_an_unparseable_top_level_falls_through_to_the_legs():
    row = {"symbol": "NVDA", "type": "LONG_CALL", "expiration": "soon",
           "legs": [{"expiration": "2026-10-17"}]}
    assert compute.setup_key(row) == "NVDA|LONG_CALL|2026-10-17"


def test_setup_expiry_honours_its_own_contract_on_a_non_dict():
    assert compute._setup_expiry("MU") == ""
    assert compute._setup_expiry(None) == ""
