"""Tests for the NinjaTrader state export.

Run: ``python -m pytest tools/tests -q`` from the repo root.
Every write goes to tmp_path — no test may touch the real state file.
"""
import json
import pathlib
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import nq_state as ns  # noqa: E402

KW = {"nq_contract": "/NQU26", "stale_after_sec": 150}


def _state(**over):
    st = {
        "now": datetime(2026, 7, 30, 6, 9, 35),
        "phase": "premarket",
        "regime": "unknown",
        "regime_stale": "cash index closed",
        "dist": None,
        "basis": 361.94,
        "scale": 1.0,
        "atr_nq": 771.38,
        "tape": {"nq": 27554.25, "nq_pct": 0.78, "ndx": 27192.3064,
                 "vix": 20.66, "ok": True},
        "gamma": {"symbol": "$NDX", "session_date": date(2026, 7, 29),
                  "snap_age_s": 55766.4},
        "levels_cash": {"flip": 27190.0, "call_wall": 27510.0,
                        "put_wall": 27175.0, "pin": 27200.0},
        "levels": {"flip": 27551.94, "call_wall": 27871.94,
                   "put_wall": 27536.94, "pin": 27561.94},
        "verdict": {"action": "WAIT", "reason": "Pre-open.",
                    "entry": None, "stop": None, "target": None},
    }
    st.update(over)
    return st


#############################################
# SHAPE — the C# side parses by regex, so the document must stay FLAT
#############################################

def test_document_is_flat():
    """NinjaScript has no bundled JSON parser; the house convention is regex
    extraction by key. A nested object would make "flip" ambiguous between the
    cash and NQ blocks, so no value may be a dict or list.
    """
    payload = ns.build_state(_state(), **KW)
    for key, value in payload.items():
        assert not isinstance(value, (dict, list)), f"{key} is nested"


def test_every_key_is_unique_and_frame_prefixed():
    payload = ns.build_state(_state(), **KW)
    for stem in ("flip", "call_wall", "put_wall", "pin", "spot"):
        assert stem not in payload, f"{stem} must be frame-prefixed"
        assert f"cash_{stem}" in payload
    for stem in ("flip", "call_wall", "put_wall", "pin", "spot"):
        assert f"nq_{stem}" in payload


def test_carries_a_schema_version():
    assert ns.build_state(_state(), **KW)["schema"] == ns.SCHEMA_VERSION


#############################################
# CONTENT
#############################################

def test_exports_both_frames_and_the_basis_between_them():
    p = ns.build_state(_state(), **KW)
    assert p["cash_flip"] == 27190.0
    assert p["nq_flip"] == 27551.94
    assert p["basis"] == 361.94
    assert p["nq_contract"] == "/NQU26"


def test_cash_spot_is_present_so_the_indicator_can_rebase():
    """The one field that makes the export contract-agnostic: with cash_spot the
    indicator computes its OWN basis from its OWN Close[0], which is correct for
    any expiry and for a back-adjusted continuous contract.
    """
    p = ns.build_state(_state(), **KW)
    assert p["cash_spot"] == pytest.approx(27192.3064)
    # The relationship the indicator relies on must hold in the exported data.
    assert p["nq_flip"] == pytest.approx(p["cash_flip"] + p["basis"], abs=0.01)


def test_exports_the_withheld_regime_and_its_reason():
    p = ns.build_state(_state(), **KW)
    assert p["regime"] == "unknown"
    assert p["regime_stale"] == "cash index closed"


def test_flags_a_stale_snapshot_against_the_threshold():
    assert ns.build_state(_state(), **KW)["snapshot_stale"] is True
    fresh = _state(gamma={"symbol": "$NDX", "session_date": None, "snap_age_s": 40.0})
    assert ns.build_state(fresh, **KW)["snapshot_stale"] is False


def test_timestamp_is_exported_as_both_iso_and_epoch():
    # ISO for humans reading the file; epoch so the indicator can age it
    # without parsing timezones in C#.
    p = ns.build_state(_state(), **KW)
    assert p["ts"].startswith("2026-07-30T06:09:35")
    assert isinstance(p["ts_epoch"], float)


def test_missing_values_are_null_never_the_string_none():
    p = ns.build_state(_state(tape={}, levels={}, levels_cash={}, verdict={}), **KW)
    assert p["nq_spot"] is None and p["cash_flip"] is None and p["entry"] is None
    assert "None" not in json.dumps(p)


@pytest.mark.parametrize("over", [
    {"tape": None}, {"gamma": None}, {"levels": None}, {"levels_cash": None},
    {"verdict": None}, {"now": None}, {},
])
def test_build_survives_a_degraded_state(over):
    """A degraded HUD must still emit a valid document — "cannot tell" and
    "process gone" have to be distinguishable downstream, and only a written
    file expresses the first.
    """
    p = ns.build_state(_state(**over), **KW)
    assert p["schema"] == ns.SCHEMA_VERSION
    json.dumps(p)


def test_build_is_pure():
    st = _state()
    before = repr(st)
    ns.build_state(st, **KW)
    assert repr(st) == before


#############################################
# WRITE
#############################################

def test_write_produces_parseable_json(tmp_path):
    p = tmp_path / "nq_state.json"
    assert ns.write_state(ns.build_state(_state(), **KW), p) is True
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back["source_symbol"] == "$NDX"
    assert back["cash_flip"] == 27190.0


def test_write_overwrites_rather_than_appends(tmp_path):
    p = tmp_path / "nq_state.json"
    ns.write_state(ns.build_state(_state(), **KW), p)
    ns.write_state(ns.build_state(_state(regime="positive"), **KW), p)
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back["regime"] == "positive"


def test_write_leaves_no_temp_file_behind(tmp_path):
    p = tmp_path / "nq_state.json"
    ns.write_state(ns.build_state(_state(), **KW), p)
    assert list(tmp_path.iterdir()) == [p], "atomic write must clean up its temp"


def test_write_never_raises_on_an_unwritable_path(tmp_path):
    p = tmp_path / "nq_state.json"
    p.mkdir()
    assert ns.write_state(ns.build_state(_state(), **KW), p) is False


def test_write_refuses_nan_rather_than_emitting_invalid_json(tmp_path):
    """json.dump would happily write NaN, which is not valid JSON and would
    break a strict parser on the C# side.
    """
    p = tmp_path / "nq_state.json"
    assert ns.write_state({"x": float("nan")}, p) is False


#############################################
# WRITER
#############################################

def test_writer_refreshes_every_call_so_ts_is_a_heartbeat(tmp_path):
    """On-change writing would leave the indicator unable to tell a quiet market
    from a dead HUD. Every poll rewrites, so a frozen ts means the HUD stopped.
    """
    p = tmp_path / "nq_state.json"
    w = ns.StateWriter(p, **KW)
    assert w.write(_state()) is True
    later = _state(now=datetime(2026, 7, 30, 6, 9, 37))
    assert w.write(later) is True
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back["ts"].endswith("06:09:37")


def test_writer_never_raises_on_a_malformed_state(tmp_path):
    w = ns.StateWriter(tmp_path / "nq_state.json", **KW)
    for bad in (None, {}, {"tape": 5}, {"gamma": "x"}):
        assert w.write(bad) in (True, False)


def test_default_path_is_under_the_gitignored_data_dir():
    assert ns.STATE_PATH.parent.name == "data"
    assert ns.STATE_PATH.parent.parent.name == "options-scanner"
    assert ns.STATE_PATH.suffix == ".json"
