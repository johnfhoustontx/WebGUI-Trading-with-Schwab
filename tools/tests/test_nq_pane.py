"""Tests for nq_hud.build_pane — the seam where the two reads become a verdict.

Run: ``python -m pytest tools/tests -q`` from the repo root.

build_pane is pure, so the whole per-instrument decision path (scale -> basis ->
two frames -> regime -> freshness guard -> verdict) is exercisable without
Redis, SQLite or tkinter. That matters most for the properties that only appear
once there are TWO instruments: that each pane sizes risk with its OWN stop
band, and that one instrument's missing data cannot contaminate the other.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import nq_hud as hud  # noqa: E402
from tools import nq_instruments as ni  # noqa: E402

# A tape fresh enough that tape_usable passes on both panes.
TAPE = {
    "vix": 14.0, "age_s": 1.0, "ok": True,
    "nq": {"fut": 28000.0, "fut_pct": 0.4, "cash": 27900.0},
    "es": {"fut": 6925.0, "fut_pct": 0.3, "cash": 6900.0},
}


def _gamma(symbol, spot, flip, call_wall, put_wall, pin, **over):
    g = {"ok": True, "reason": "", "symbol": symbol, "spot": spot,
         "flip": flip, "call_wall": call_wall, "put_wall": put_wall,
         "pin": pin, "pin_top_pos": pin, "flip_stored": flip,
         "flip_computed": flip, "net_total": 1.0, "snap_age_s": 30.0,
         "atr_proxy": 120.0, "session_date": None}
    g.update(over)
    return g


# NQ at the call wall, well above a flip that is clear of the +/-0.30% band.
NQ_GAMMA = _gamma("$NDX", 27900.0, 27000.0, 27900.0, 27600.0, 27800.0)
# ES in the same structural position, at ~1/4 the index level.
ES_GAMMA = _gamma("$SPX", 6900.0, 6700.0, 6900.0, 6850.0, 6880.0,
                  atr_proxy=30.0)


#############################################
# THE TWO FRAMES
#############################################

def test_futures_levels_are_the_cash_levels_plus_the_measured_basis():
    pane = hud.build_pane(ni.NQ, TAPE, NQ_GAMMA, "pin")
    assert pane["basis"] == pytest.approx(100.0)      # 28000 - 27900
    for key in ("flip", "call_wall", "put_wall", "pin"):
        assert pane["levels"][key] == pytest.approx(
            pane["levels_cash"][key] + pane["basis"])


def test_the_regime_is_decided_in_the_cash_frame():
    """Basis is future - cash, so comparing the future against a
    future-converted level reduces to cash-vs-level. Computing it in cash makes
    that explicit; the test pins that the futures price genuinely cancels.
    """
    a = hud.build_pane(ni.NQ, TAPE, NQ_GAMMA, "pin")
    moved = dict(TAPE, nq=dict(TAPE["nq"], fut=28950.0))   # future +950
    b = hud.build_pane(ni.NQ, moved, NQ_GAMMA, "pin")
    assert (a["regime"], a["dist"]) == (b["regime"], b["dist"])


def test_a_cash_index_source_needs_no_scaling():
    pane = hud.build_pane(ni.NQ, TAPE, NQ_GAMMA, "pin")
    assert pane["scale"] == 1.0
    assert pane["levels_cash"]["flip"] == 27000.0


def test_an_etf_source_is_scaled_up_to_the_index():
    """QQQ strikes are ~1/41 of NDX; without the scale every level would land
    near zero."""
    qqq = _gamma("QQQ", 680.0, 658.5, 680.0, 673.0, 678.0, atr_proxy=3.0)
    pane = hud.build_pane(ni.NQ, TAPE, qqq, "pin")
    assert pane["scale"] == pytest.approx(27900.0 / 680.0)
    assert pane["levels_cash"]["flip"] == pytest.approx(658.5 * pane["scale"])


#############################################
# PER-INSTRUMENT RISK SIZING (the reason the spec exists)
#############################################

def test_each_pane_sizes_its_stop_with_its_own_band():
    """The same structural setup on both instruments must not produce the same
    point-denominated stop: NDX trades near 4x SPX, so NQ's 15-45 band on ES
    would be roughly four times the intended risk.
    """
    nq = hud.build_pane(ni.NQ, TAPE, NQ_GAMMA, "pin")
    es = hud.build_pane(ni.ES, TAPE, ES_GAMMA, "pin")
    assert nq["verdict"]["action"] == "SHORT"
    assert es["verdict"]["action"] == "SHORT"

    nq_risk = abs(nq["verdict"]["entry"] - nq["verdict"]["stop"])
    es_risk = abs(es["verdict"]["entry"] - es["verdict"]["stop"])
    assert es_risk < nq_risk
    assert ni.ES.min_stop <= es_risk <= ni.ES.max_stop
    assert ni.NQ.min_stop <= nq_risk <= ni.NQ.max_stop


def test_the_es_stop_is_never_sized_off_nqs_ceiling():
    """A large ES session range must clamp to ES's ceiling. At $50/pt, NQ's
    45-point cap would be $2,250 of risk on a single contract."""
    wide = dict(ES_GAMMA, atr_proxy=5000.0)
    es = hud.build_pane(ni.ES, TAPE, wide, "pin")
    risk = abs(es["verdict"]["entry"] - es["verdict"]["stop"])
    assert risk == pytest.approx(ni.ES.max_stop)


def test_risk_levels_are_shifted_into_the_futures_frame():
    """entry/stop/target are what gets typed into NinjaTrader, so they must be
    futures prices; the cash originals are kept for a rebasing consumer."""
    pane = hud.build_pane(ni.NQ, TAPE, NQ_GAMMA, "pin")
    v, vc = pane["verdict"], pane["verdict_cash"]
    assert v["entry"] == pytest.approx(vc["entry"] + pane["basis"])
    assert v["stop"] == pytest.approx(vc["stop"] + pane["basis"])
    # The DISTANCE is frame-invariant, which is what makes the shift safe.
    assert abs(v["entry"] - v["stop"]) == pytest.approx(
        abs(vc["entry"] - vc["stop"]))


#############################################
# THE FRESHNESS GUARD
#############################################

def test_a_stale_tape_withholds_the_regime():
    stale = dict(TAPE, ok=False)
    pane = hud.build_pane(ni.NQ, stale, NQ_GAMMA, "pin")
    assert pane["regime"] == "unknown"
    assert pane["regime_stale"] == "tape not updating"
    assert pane["verdict"]["action"] == "STAND DOWN"


def test_one_panes_missing_price_withholds_only_that_pane():
    """The whole point of a per-pane usability check: ES losing its tile must
    not blank the NQ readout beside it."""
    partial = dict(TAPE, es={"fut": None, "fut_pct": None, "cash": None})
    nq = hud.build_pane(ni.NQ, partial, NQ_GAMMA, "pin")
    es = hud.build_pane(ni.ES, partial, ES_GAMMA, "pin")
    assert nq["regime"] == "positive"
    assert es["regime"] == "unknown"
    assert es["regime_stale"] == "tape not updating"


def test_a_stale_gamma_snapshot_withholds_the_regime():
    old = dict(NQ_GAMMA, snap_age_s=hud.STALE_AFTER_SEC + 60)
    pane = hud.build_pane(ni.NQ, TAPE, old, "pin")
    assert pane["regime"] == "unknown"
    assert "stale" in pane["regime_stale"]


def test_outside_rth_the_regime_is_withheld_even_on_a_fresh_snapshot():
    """The regime is anchored to a cash index that stops ticking; premarket it
    would otherwise show a confident band computed from yesterday's close."""
    pane = hud.build_pane(ni.NQ, TAPE, NQ_GAMMA, "premarket")
    assert pane["regime"] == "unknown"
    assert pane["regime_stale"] == "cash index closed"


#############################################
# DEGRADATION
#############################################

@pytest.mark.parametrize("gamma", [
    {}, {"ok": False, "reason": "no GEX data"},
    _gamma("$NDX", None, None, None, None, None),
    _gamma("$NDX", 27900.0, None, 27900.0, 27600.0, 27800.0),
])
def test_a_broken_gamma_read_degrades_rather_than_raising(gamma):
    pane = hud.build_pane(ni.NQ, TAPE, gamma, "pin")
    assert pane["regime"] in ("unknown", "positive", "negative", "flip_zone")
    assert pane["verdict"]["action"] in ("LONG", "SHORT", "WAIT", "STAND DOWN")


def test_an_empty_tape_leg_degrades_rather_than_raising():
    pane = hud.build_pane(ni.NQ, {"ok": False}, NQ_GAMMA, "pin")
    assert pane["basis"] is None
    assert pane["levels"]["flip"] is None      # no basis -> no futures level
    assert pane["levels_cash"]["flip"] == 27000.0
    assert pane["regime"] == "unknown"


def test_build_pane_is_pure():
    before = (repr(TAPE), repr(NQ_GAMMA))
    hud.build_pane(ni.NQ, TAPE, NQ_GAMMA, "pin")
    assert (repr(TAPE), repr(NQ_GAMMA)) == before


def test_the_pane_carries_its_spec_so_downstream_cannot_mislabel_it():
    """The signal log and the state export both read the instrument label off
    the pane, so it travels with the numbers rather than beside them."""
    assert hud.build_pane(ni.ES, TAPE, ES_GAMMA, "pin")["spec"] is ni.ES


def test_alternative_level_definitions_are_carried_through_both_frames():
    """pin_top_pos and flip_stored are unused by the verdict but logged and
    exported, so the open design questions can be settled on data."""
    pane = hud.build_pane(ni.NQ, TAPE, NQ_GAMMA, "pin")
    for key in ("pin_top_pos", "flip_stored"):
        assert pane["levels_cash"][key] is not None
        assert pane["levels"][key] is not None
