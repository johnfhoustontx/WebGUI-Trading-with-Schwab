"""Tests for the NinjaTrader state export (schema 2 — NQ + ES in one document).

Run: ``python -m pytest tools/tests -q`` from the repo root.
Every write goes to tmp_path — no test may touch the real state file.
"""
import json
import pathlib
import re
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import nq_instruments as ni  # noqa: E402
from tools import nq_state as ns  # noqa: E402

KW = {"stale_after_sec": 150}


def _pane(spec, fut, cash, basis, flip, over=None):
    pane = {
        "spec": spec,
        "tape": {"fut": fut, "fut_pct": 0.78, "cash": cash},
        "gamma": {"symbol": spec.sources[0], "session_date": date(2026, 7, 29),
                  "snap_age_s": 55766.4},
        "scale": 1.0,
        "basis": basis,
        "atr_pts": 771.38,
        "regime": "unknown",
        "dist": None,
        "regime_stale": "cash index closed",
        "levels_cash": {"flip": flip, "call_wall": flip + 320.0,
                        "put_wall": flip - 15.0, "pin": flip + 10.0,
                        "pin_top_pos": flip + 60.0, "flip_stored": flip + 5.0},
        "levels": {"flip": flip + basis, "call_wall": flip + 320.0 + basis,
                   "put_wall": flip - 15.0 + basis, "pin": flip + 10.0 + basis,
                   "pin_top_pos": flip + 60.0 + basis,
                   "flip_stored": flip + 5.0 + basis},
        "verdict": {"action": "WAIT", "reason": "Pre-open.",
                    "entry": None, "stop": None, "target": None},
        "verdict_cash": {"action": "WAIT", "reason": "Pre-open.",
                         "entry": None, "stop": None, "target": None},
    }
    pane.update(over or {})
    return pane


def _state(nq_over=None, es_over=None, **over):
    st = {
        "now": datetime(2026, 7, 30, 6, 9, 35),
        "phase": "premarket",
        "tape": {"vix": 20.66, "age_s": 1.2, "ok": True},
        "panes": {
            "nq": _pane(ni.NQ, 27554.25, 27192.3064, 361.94, 27190.0,
                        nq_over),
            "es": _pane(ni.ES, 6925.50, 6900.10, 25.40, 6890.0,
                        es_over),
        },
    }
    st.update(over)
    return st


#############################################
# SHAPE — the C# side parses by regex, so the document must stay FLAT
#############################################

def test_document_is_flat():
    """NinjaScript has no bundled JSON parser; the house convention is regex
    extraction by key. A nested object would make "flip" ambiguous between
    instruments and frames, so no value may be a dict or list.
    """
    payload = ns.build_state(_state(), **KW)
    for key, value in payload.items():
        assert not isinstance(value, (dict, list)), f"{key} is nested"


def test_every_level_key_is_instrument_and_frame_prefixed():
    payload = ns.build_state(_state(), **KW)
    for stem in ("flip", "call_wall", "put_wall", "pin", "spot"):
        # Bare and frame-only names would be ambiguous with two instruments.
        assert stem not in payload, f"{stem} must be prefixed"
        assert f"cash_{stem}" not in payload, f"cash_{stem} must name its instrument"
        for key in ("nq", "es"):
            assert f"{key}_cash_{stem}" in payload
            assert f"{key}_fut_{stem}" in payload


def test_prefixing_is_safe_under_the_readers_actual_regex():
    """The NinjaScript accessor anchors on the opening quote::

        "\\"" + key + "\\"\\s*:\\s*(number)"

    so "cash_flip" must NOT match inside "nq_cash_flip". Replicated here
    because the whole prefixing scheme depends on that anchor, and getting it
    wrong would have one instrument's levels silently read as the other's.
    """
    doc = json.dumps(ns.build_state(_state(), **KW), indent=1)

    def get_num(key):
        m = re.search('"' + re.escape(key)
                      + r'"\s*:\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)', doc)
        return None if m is None else float(m.group(1))

    assert get_num("cash_flip") is None, "unprefixed key must not match"
    assert get_num("nq_cash_flip") == 27190.0
    assert get_num("es_cash_flip") == 6890.0


def test_carries_a_schema_version_and_it_is_two():
    """Schema 1 was the NQ-only, unprefixed layout. An indicator built for it
    would misread this document, so the bump has to be real."""
    assert ns.SCHEMA_VERSION == 2
    assert ns.build_state(_state(), **KW)["schema"] == 2


def test_instrument_list_lets_the_reader_discover_prefixes():
    p = ns.build_state(_state(), **KW)
    assert p["instruments"] == "nq,es"


#############################################
# CONTENT
#############################################

def test_exports_both_frames_and_the_basis_between_them():
    p = ns.build_state(_state(), **KW)
    assert p["nq_cash_flip"] == 27190.0
    assert p["nq_fut_flip"] == pytest.approx(27551.94)
    assert p["nq_basis"] == 361.94
    assert p["nq_contract"] == "/NQU26"
    assert p["es_contract"] == "/ESU26"


@pytest.mark.parametrize("key", ["nq", "es"])
def test_cash_spot_is_present_so_the_indicator_can_rebase(key):
    """The one field that makes the export contract-agnostic: with cash_spot the
    indicator computes its OWN basis from its OWN Close[0], which is correct for
    any expiry and for a back-adjusted continuous contract.
    """
    p = ns.build_state(_state(), **KW)
    assert p[f"{key}_cash_spot"] is not None
    # The relationship the indicator relies on must hold in the exported data.
    assert p[f"{key}_fut_flip"] == pytest.approx(
        p[f"{key}_cash_flip"] + p[f"{key}_basis"], abs=0.01)


def test_labels_come_from_the_spec():
    p = ns.build_state(_state(), **KW)
    assert p["nq_label"] == "NQ" and p["es_label"] == "ES"
    assert p["nq_source_symbol"] == "$NDX"
    assert p["es_source_symbol"] == "$SPX"


def test_exports_the_withheld_regime_and_its_reason_per_instrument():
    p = ns.build_state(_state(), **KW)
    for key in ("nq", "es"):
        assert p[f"{key}_regime"] == "unknown"
        assert p[f"{key}_regime_stale"] == "cash index closed"


def test_snapshot_staleness_is_per_instrument():
    """One collector can stall on $NDX while $SPX keeps publishing; a single
    shared flag would hide that."""
    fresh_es = {"gamma": {"symbol": "$SPX", "session_date": None,
                          "snap_age_s": 40.0}}
    p = ns.build_state(_state(es_over=fresh_es), **KW)
    assert p["nq_snapshot_stale"] is True
    assert p["es_snapshot_stale"] is False


def test_shared_facts_are_not_duplicated_per_instrument():
    """VIX, the clock and the tape health describe the whole app, so they stay
    top-level — duplicating them would invite the two copies to disagree."""
    p = ns.build_state(_state(), **KW)
    assert p["vix"] == 20.66
    assert p["tape_ok"] is True
    assert "nq_vix" not in p and "es_vix" not in p


def test_timestamp_is_exported_as_both_iso_and_epoch():
    # ISO for humans reading the file; epoch so the indicator can age it
    # without parsing timezones in C#.
    p = ns.build_state(_state(), **KW)
    assert p["ts"].startswith("2026-07-30T06:09:35")
    assert isinstance(p["ts_epoch"], float)


def test_missing_values_are_null_never_the_string_none():
    blank = {"tape": {}, "levels": {}, "levels_cash": {}, "verdict": {},
             "verdict_cash": {}}
    p = ns.build_state(_state(nq_over=blank, es_over=blank), **KW)
    assert p["nq_fut_spot"] is None
    assert p["nq_cash_flip"] is None
    assert p["es_entry"] is None
    assert "None" not in json.dumps(p)


@pytest.mark.parametrize("over", [
    {"tape": None}, {"gamma": None}, {"levels": None}, {"levels_cash": None},
    {"verdict": None}, {"verdict_cash": None}, {"spec": None}, {},
])
def test_build_survives_a_degraded_pane(over):
    """A degraded HUD must still emit a valid document — "cannot tell" and
    "process gone" have to be distinguishable downstream, and only a written
    file expresses the first.
    """
    p = ns.build_state(_state(nq_over=over), **KW)
    assert p["schema"] == ns.SCHEMA_VERSION
    json.dumps(p)


@pytest.mark.parametrize("over", [{"now": None}, {"tape": None},
                                  {"panes": {}}, {"panes": None}])
def test_build_survives_a_degraded_top_level(over):
    p = ns.build_state(_state(**over), **KW)
    assert p["schema"] == ns.SCHEMA_VERSION
    json.dumps(p)


def test_one_broken_pane_does_not_blank_the_other():
    """The panes are independent reads; ES failing must leave NQ fully
    populated, or a single bad symbol takes the whole readout down."""
    p = ns.build_state(_state(es_over={"levels_cash": None, "gamma": None}), **KW)
    assert p["nq_cash_flip"] == 27190.0
    assert p["es_cash_flip"] is None


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
    assert back["nq_source_symbol"] == "$NDX"
    assert back["es_cash_flip"] == 6890.0


def test_write_overwrites_rather_than_appends(tmp_path):
    p = tmp_path / "nq_state.json"
    ns.write_state(ns.build_state(_state(), **KW), p)
    ns.write_state(ns.build_state(_state(nq_over={"regime": "positive"}), **KW), p)
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back["nq_regime"] == "positive"


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
    assert w.write(_state(now=datetime(2026, 7, 30, 6, 9, 37))) is True
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back["ts"].endswith("06:09:37")


def test_writer_writes_both_panes_in_one_atomic_document(tmp_path):
    """Two files could be read mid-update and show mismatched instants. One
    os.replace means the pair is always internally consistent."""
    p = tmp_path / "nq_state.json"
    ns.StateWriter(p, **KW).write(_state())
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back["nq_fut_spot"] == 27554.25
    assert back["es_fut_spot"] == 6925.50


def test_writer_never_raises_on_a_malformed_state(tmp_path):
    w = ns.StateWriter(tmp_path / "nq_state.json", **KW)
    for bad in (None, {}, {"panes": 5}, {"panes": {"nq": "x"}}):
        assert w.write(bad) in (True, False)


def test_default_path_is_under_the_gitignored_data_dir():
    assert ns.STATE_PATH.parent.name == "data"
    assert ns.STATE_PATH.parent.parent.name == "options-scanner"
    assert ns.STATE_PATH.suffix == ".json"


def test_written_text_is_real_utf8_not_escaped(tmp_path):
    r"""The C# accessor unescapes only the quote and backslash forms (the house
    ModelConfigLoader convention), so a json.dump left at its default
    ensure_ascii=True would put the literal seven characters "\u2014" mid-
    sentence in the verdict reason — which reads as corruption on the panel.
    The verdict text is full of em dashes, so this is not hypothetical: it was
    caught by running the C# accessors' regexes over a real exported document.
    """
    p = tmp_path / "nq_state.json"
    reason = "Positive gamma at the call wall — fade toward the pin."
    st = _state(nq_over={"verdict": {"action": "SHORT", "reason": reason,
                                     "entry": 1.0, "stop": 2.0, "target": 3.0}})
    assert ns.write_state(ns.build_state(st, **KW), p) is True

    raw = p.read_text(encoding="utf-8")
    assert "\\u" not in raw, "non-ASCII must be written as UTF-8, not escaped"
    assert "—" in raw
    assert json.loads(raw)["nq_reason"] == reason
