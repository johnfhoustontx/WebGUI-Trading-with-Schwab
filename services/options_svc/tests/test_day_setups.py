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
