"""Tests for the tape reader's freshness check.

Run: ``python -m pytest tools/tests -q`` from the repo root.

WHY THIS EXISTS: read_tape computed the cache age and discarded it, reporting
ok=True purely because a price was present. With market_svc dead the cache sat
frozen for 12 hours and the HUD painted those prices as live — and because the
basis is derived from them, every converted level was built on a stale number
while the panel showed green.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import nq_hud as hud  # noqa: E402


def _bus(age_s=1.0, nq=27554.25, ndx=27192.31, vix=20.66):
    """Stub Bus returning a dashboard payload written ``age_s`` ago."""
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    tiles = []
    for display, last in ((hud.TILE_NQ, nq), (hud.TILE_NDX, ndx), (hud.TILE_VIX, vix)):
        if last is not None:
            tiles.append({"display": display, "last": last, "change_pct": 0.5})
    payload = {"categories": [{"category": "x", "tiles": tiles}]}
    return SimpleNamespace(
        cache_get=lambda key: SimpleNamespace(ts=ts.isoformat(), payload=payload))


def test_fresh_tape_reads_ok():
    tape = hud.read_tape(_bus(age_s=2.0))
    assert tape["ok"] is True
    assert tape["nq"] == 27554.25
    assert tape["ndx"] == 27192.31


def test_stale_tape_is_not_ok():
    """market_svc polls every 2-5s. Minutes of silence means it is dead, and
    its last prices must not be presented as current.
    """
    tape = hud.read_tape(_bus(age_s=hud.TAPE_STALE_AFTER_SEC + 30))
    assert tape["ok"] is False
    # The values are still returned — the panel can show them greyed — but the
    # ok flag is what drives the health line.
    assert tape["nq"] == 27554.25


def test_the_real_world_case_twelve_hours_frozen():
    tape = hud.read_tape(_bus(age_s=43255))
    assert tape["ok"] is False
    assert tape["age_s"] == pytest.approx(43255, abs=5)


@pytest.mark.parametrize("age", [0.0, 1.0, 30.0])
def test_ages_inside_the_threshold_stay_ok(age):
    assert hud.read_tape(_bus(age_s=age))["ok"] is True


def test_missing_price_is_not_ok_even_when_fresh():
    assert hud.read_tape(_bus(age_s=1.0, nq=None))["ok"] is False


def test_unreadable_timestamp_fails_closed():
    """An age we cannot establish is treated as untrustworthy rather than
    assumed fresh — the whole point is to stop showing stale data as live.
    """
    bus = SimpleNamespace(cache_get=lambda key: SimpleNamespace(
        ts="not-a-timestamp",
        payload={"categories": [{"category": "x", "tiles": [
            {"display": hud.TILE_NQ, "last": 27554.25, "change_pct": 0.1}]}]}))
    tape = hud.read_tape(bus)
    assert tape["age_s"] is None
    assert tape["ok"] is False


def test_missing_cache_entry_degrades_quietly():
    bus = SimpleNamespace(cache_get=lambda key: None)
    tape = hud.read_tape(bus)
    assert tape["ok"] is False and tape["nq"] is None


def test_a_raising_bus_never_propagates():
    def boom(key):
        raise RuntimeError("redis gone")
    tape = hud.read_tape(SimpleNamespace(cache_get=boom))
    assert tape["ok"] is False and tape["nq"] is None


def test_threshold_is_well_clear_of_the_publish_cadence():
    # market_svc publishes ~2s RTH / 5s off-hours; the threshold must not trip
    # on normal jitter, but must catch a dead service within a minute or so.
    assert 30 <= hud.TAPE_STALE_AFTER_SEC <= 120
