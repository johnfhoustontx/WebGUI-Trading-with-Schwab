"""Tests for the tape reader's freshness check and its multi-instrument shape.

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
from tools import nq_instruments as ni  # noqa: E402


def _bus(age_s=1.0, nq=27554.25, ndx=27192.31, es=6925.50, spx=6900.10,
         vix=20.66):
    """Stub Bus returning a dashboard payload written ``age_s`` ago."""
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    prices = [(ni.NQ.future_tile, nq), (ni.NQ.cash_tile, ndx),
              (ni.ES.future_tile, es), (ni.ES.cash_tile, spx),
              (hud.TILE_VIX, vix)]
    tiles = [{"display": d, "last": v, "change_pct": 0.5}
             for d, v in prices if v is not None]
    payload = {"categories": [{"category": "x", "tiles": tiles}]}
    return SimpleNamespace(
        cache_get=lambda key: SimpleNamespace(ts=ts.isoformat(), payload=payload))


#############################################
# FRESHNESS
#############################################

def test_fresh_tape_reads_ok():
    tape = hud.read_tape(_bus(age_s=2.0))
    assert tape["ok"] is True
    assert tape["nq"]["fut"] == 27554.25
    assert tape["nq"]["cash"] == 27192.31


def test_stale_tape_is_not_ok():
    """market_svc polls every 2-5s. Minutes of silence means it is dead, and
    its last prices must not be presented as current.
    """
    tape = hud.read_tape(_bus(age_s=hud.TAPE_STALE_AFTER_SEC + 30))
    assert tape["ok"] is False
    # The values are still returned — the panel can show them greyed — but the
    # ok flag is what drives the health line.
    assert tape["nq"]["fut"] == 27554.25


def test_the_real_world_case_twelve_hours_frozen():
    tape = hud.read_tape(_bus(age_s=43255))
    assert tape["ok"] is False
    assert tape["age_s"] == pytest.approx(43255, abs=5)


@pytest.mark.parametrize("age", [0.0, 1.0, 30.0])
def test_ages_inside_the_threshold_stay_ok(age):
    assert hud.read_tape(_bus(age_s=age))["ok"] is True


def test_unreadable_timestamp_fails_closed():
    """An age we cannot establish is treated as untrustworthy rather than
    assumed fresh — the whole point is to stop showing stale data as live.
    """
    bus = SimpleNamespace(cache_get=lambda key: SimpleNamespace(
        ts="not-a-timestamp",
        payload={"categories": [{"category": "x", "tiles": [
            {"display": ni.NQ.future_tile, "last": 27554.25,
             "change_pct": 0.1}]}]}))
    tape = hud.read_tape(bus)
    assert tape["age_s"] is None
    assert tape["ok"] is False


def test_missing_cache_entry_degrades_quietly():
    bus = SimpleNamespace(cache_get=lambda key: None)
    tape = hud.read_tape(bus)
    assert tape["ok"] is False and tape["nq"]["fut"] is None


def test_a_raising_bus_never_propagates():
    def boom(key):
        raise RuntimeError("redis gone")
    tape = hud.read_tape(SimpleNamespace(cache_get=boom))
    assert tape["ok"] is False and tape["nq"]["fut"] is None


def test_threshold_is_well_clear_of_the_publish_cadence():
    # market_svc publishes ~2s RTH / 5s off-hours; the threshold must not trip
    # on normal jitter, but must catch a dead service within a minute or so.
    assert 30 <= hud.TAPE_STALE_AFTER_SEC <= 120


#############################################
# MULTI-INSTRUMENT SHAPE
#############################################

def test_one_read_serves_every_instrument():
    """Re-reading per pane would double the Redis traffic AND let the two panes
    see different snapshots of the tape."""
    calls = []
    inner = _bus()

    def counting_get(key):
        calls.append(key)
        return inner.cache_get(key)

    tape = hud.read_tape(SimpleNamespace(cache_get=counting_get))
    assert len(calls) == 1
    assert tape["nq"]["fut"] == 27554.25
    assert tape["es"]["fut"] == 6925.50


def test_each_instrument_gets_its_own_future_and_cash():
    tape = hud.read_tape(_bus())
    assert tape["nq"]["cash"] == 27192.31    # NDX
    assert tape["es"]["cash"] == 6900.10     # SPX
    assert tape["nq"]["fut"] != tape["es"]["fut"]


def test_vix_is_shared_not_per_instrument():
    tape = hud.read_tape(_bus())
    assert tape["vix"] == 20.66
    assert "vix" not in tape["nq"]


def test_every_instrument_key_is_present_even_with_no_data():
    """The panes index into this unconditionally, so a missing key would be an
    AttributeError on the poll thread rather than a blank readout."""
    tape = hud.read_tape(SimpleNamespace(cache_get=lambda key: None))
    for spec in ni.INSTRUMENTS:
        assert tape[spec.key] == {"fut": None, "fut_pct": None, "cash": None}


#############################################
# PER-PANE USABILITY
#############################################

def test_a_pane_missing_its_own_price_is_not_usable():
    """One instrument's tile can vanish while the other's is fine — the shared
    ok flag cannot express that, so tape_usable checks per pane."""
    tape = hud.read_tape(_bus(es=None))
    assert tape["ok"] is True                      # NQ still publishing
    assert hud.tape_usable(tape, ni.NQ) is True
    assert hud.tape_usable(tape, ni.ES) is False


def test_a_pane_missing_its_cash_index_is_not_usable():
    """Both legs are required: the basis is their difference, so a missing cash
    price makes every converted level in that pane wrong."""
    tape = hud.read_tape(_bus(spx=None))
    assert hud.tape_usable(tape, ni.ES) is False


def test_no_pane_is_usable_when_the_publisher_is_stale():
    tape = hud.read_tape(_bus(age_s=hud.TAPE_STALE_AFTER_SEC + 30))
    for spec in ni.INSTRUMENTS:
        assert hud.tape_usable(tape, spec) is False


def test_missing_every_price_is_not_ok_even_when_fresh():
    assert hud.read_tape(_bus(nq=None, es=None))["ok"] is False
