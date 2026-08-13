"""Tests for the dealer-positioning HUD's verdict-transition log.

Run: ``python -m pytest tools/tests -q`` from the repo root.

Every write goes to tmp_path — no test may touch the real
options-scanner/data/dealer_signals.csv.
"""
import csv
import pathlib
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import nq_instruments as ni  # noqa: E402
from tools import nq_signal_log as sl  # noqa: E402


def _pane(spec, regime, action, over=None):
    pane = {
        "spec": spec,
        "tape": {"fut": 27192.31, "fut_pct": 0.4567, "cash": 27073.56},
        "gamma": {"symbol": spec.sources[0], "session_date": date(2026, 7, 29),
                  "snap_age_s": 41.7},
        "scale": 1.0,
        "basis": 118.75,
        "atr_pts": 771.38,
        "regime": regime,
        "dist": 123.456,
        "regime_stale": None,
        "levels": {"flip": 27190.0, "call_wall": 27510.0, "put_wall": 27175.0,
                   "pin": 27200.0, "pin_top_pos": 27350.0,
                   "flip_stored": 27185.0},
        "levels_cash": {"flip": 27071.25},
        "verdict": {"action": action, "entry": 27192.31, "stop": 27540.0,
                    "target": 27200.0, "reason": "..."},
        "verdict_cash": {"action": action, "entry": 27073.56},
    }
    pane.update(over or {})
    return pane


def _state(regime="positive", action="SHORT",
           es_regime="positive", es_action="WAIT", nq_over=None, **over):
    st = {
        "now": datetime(2026, 7, 29, 12, 34, 56),
        "phase": "pin",
        "tape": {"vix": 14.2, "age_s": 1.0, "ok": True},
        "panes": {
            "nq": _pane(ni.NQ, regime, action, nq_over),
            "es": _pane(ni.ES, es_regime, es_action),
        },
    }
    st.update(over)
    return st


#############################################
# TRANSITION DETECTION
#############################################

def test_transition_key_uses_regime_and_action():
    assert sl.transition_key("positive", "SHORT") == ("positive", "SHORT")


def test_first_observation_is_a_transition():
    # prev None = "nothing known yet"; the opening read is worth a row.
    assert sl.should_log(None, ("positive", "WAIT")) is True


def test_same_state_is_not_a_transition():
    assert sl.should_log(("positive", "WAIT"), ("positive", "WAIT")) is False


def test_regime_change_alone_is_a_transition():
    """WAIT under positive gamma -> WAIT under negative is a real change of the
    world, and is exactly what the regime-accuracy question needs. Keying on
    action alone would silently drop it.
    """
    assert sl.should_log(("positive", "WAIT"), ("negative", "WAIT")) is True


def test_action_change_alone_is_a_transition():
    assert sl.should_log(("positive", "WAIT"), ("positive", "SHORT")) is True


#############################################
# ROW BUILDING (pure)
#############################################

def test_build_row_covers_every_declared_field():
    row = sl.build_row(_state(), "nq")
    assert set(row) == set(sl.FIELDS), "row keys must match the CSV header exactly"


def test_build_row_values():
    row = sl.build_row(_state(), "nq")
    assert row["ts_ct"] == "2026-07-29 12:34:56"
    assert row["instrument"] == "NQ"
    assert row["session_date"] == "2026-07-29"
    assert row["source_symbol"] == "$NDX"
    assert row["regime"] == "positive"
    assert row["action"] == "SHORT"
    assert row["fut_spot"] == 27192.31
    assert row["cash_spot"] == 27073.56
    assert row["flip_fut"] == 27190.0
    assert row["call_wall_fut"] == 27510.0
    assert row["put_wall_fut"] == 27175.0
    assert row["vix"] == 14.2          # shared, read from the top-level tape
    assert row["snap_age_s"] == 42     # rounded to whole seconds


def test_the_key_selects_which_pane_is_recorded():
    st = _state(regime="negative", action="LONG",
                es_regime="flip_zone", es_action="STAND DOWN")
    nq, es = sl.build_row(st, "nq"), sl.build_row(st, "es")
    assert (nq["instrument"], nq["regime"], nq["action"]) == ("NQ", "negative", "LONG")
    assert (es["instrument"], es["regime"], es["action"]) == ("ES", "flip_zone",
                                                              "STAND DOWN")


def test_instrument_label_comes_from_the_pane_not_the_caller():
    """The label and the numbers beside it must describe the same instrument,
    so it is read off the pane's own spec rather than passed in alongside."""
    assert sl.build_row(_state(), "es")["instrument"] == "ES"


def test_unknown_pane_key_still_yields_a_full_row():
    row = sl.build_row(_state(), "rty")
    assert set(row) == set(sl.FIELDS)
    assert row["instrument"] == "RTY"       # falls back to the key itself
    assert row["fut_spot"] == ""


def test_build_row_records_both_pin_candidates():
    """The whole point of the log: design §6 leaves the pin definition open, so
    max(|net|) and the stored top_pos_strike are both recorded, distinctly.
    """
    row = sl.build_row(_state(), "nq")
    assert row["pin_fut"] == 27200.0
    assert row["pin_top_pos_fut"] == 27350.0
    assert row["pin_fut"] != row["pin_top_pos_fut"]


def test_build_row_records_both_flip_definitions():
    """Same reasoning for the flip: the HUD uses the grid-recomputed value, and
    the stored nearest-to-spot column is logged beside it so the engine-wide
    choice can be settled on data."""
    row = sl.build_row(_state(), "nq")
    assert row["flip_fut"] == 27190.0
    assert row["flip_stored_fut"] == 27185.0


def test_build_row_writes_empty_not_none():
    # "None" in a CSV cell poisons a pandas numeric column.
    row = sl.build_row(
        _state(nq_over={"levels": {}, "verdict": {"action": "STAND DOWN"}}), "nq")
    assert row["flip_fut"] == ""
    assert row["pin_fut"] == ""
    assert row["entry"] == ""
    assert "None" not in {str(v) for v in row.values()}


@pytest.mark.parametrize("over", [
    {}, {"tape": None}, {"gamma": None}, {"levels": None}, {"verdict": None},
    {"spec": None},
])
def test_build_row_survives_a_degraded_pane(over):
    """A blind HUD still produces a row — that it was blind at that moment is
    itself the datum worth keeping.
    """
    row = sl.build_row(_state(nq_over=over), "nq")
    assert set(row) == set(sl.FIELDS)


@pytest.mark.parametrize("over", [{"now": None}, {"tape": None},
                                  {"panes": {}}, {"panes": None}])
def test_build_row_survives_a_degraded_top_level(over):
    assert set(sl.build_row(_state(**over), "nq")) == set(sl.FIELDS)


def test_build_row_is_pure():
    st = _state()
    before = repr(st)
    sl.build_row(st, "nq")
    assert repr(st) == before, "build_row must not mutate the state it is given"


#############################################
# APPEND
#############################################

def test_append_writes_header_once(tmp_path):
    p = tmp_path / "d.csv"
    assert sl.append_row(sl.build_row(_state(), "nq"), p) is True
    assert sl.append_row(sl.build_row(_state(action="LONG"), "nq"), p) is True

    rows = list(csv.reader(p.open(encoding="utf-8")))
    assert rows[0] == sl.FIELDS          # header exactly once, at the top
    assert len(rows) == 3                # header + 2 data rows
    assert rows[1][sl.FIELDS.index("action")] == "SHORT"
    assert rows[2][sl.FIELDS.index("action")] == "LONG"


def test_both_instruments_share_one_file(tmp_path):
    """The interesting offline question is how the two regimes relate, which is
    a single-file query. The instrument column keeps them separable."""
    p = tmp_path / "d.csv"
    st = _state()
    sl.append_row(sl.build_row(st, "nq"), p)
    sl.append_row(sl.build_row(st, "es"), p)
    back = list(csv.DictReader(p.open(encoding="utf-8")))
    assert [r["instrument"] for r in back] == ["NQ", "ES"]


def test_append_creates_missing_parent_dirs(tmp_path):
    p = tmp_path / "deep" / "nested" / "d.csv"
    assert sl.append_row(sl.build_row(_state(), "nq"), p) is True
    assert p.exists()


def test_append_never_raises_on_an_unwritable_path(tmp_path):
    # A directory where the file should be: open() fails, must not propagate.
    p = tmp_path / "d.csv"
    p.mkdir()
    assert sl.append_row(sl.build_row(_state(), "nq"), p) is False


def test_append_is_round_trippable_by_dictreader(tmp_path):
    p = tmp_path / "d.csv"
    sl.append_row(sl.build_row(_state(), "nq"), p)
    back = list(csv.DictReader(p.open(encoding="utf-8")))
    assert len(back) == 1
    assert back[0]["source_symbol"] == "$NDX"
    assert float(back[0]["pin_top_pos_fut"]) == 27350.0


#############################################
# SignalLogger — the stateful wrapper
#############################################

def test_logger_writes_once_per_transition_not_once_per_poll(tmp_path):
    p = tmp_path / "d.csv"
    lg = sl.SignalLogger(p, instrument="NQ")

    assert lg.maybe_log(_state(action="WAIT"), "nq") is True     # first read
    for _ in range(50):                                          # unchanged polls
        assert lg.maybe_log(_state(action="WAIT"), "nq") is False
    assert lg.maybe_log(_state(action="SHORT"), "nq") is True    # transition
    assert lg.maybe_log(_state(action="SHORT"), "nq") is False

    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert [r["action"] for r in rows] == ["WAIT", "SHORT"]


def test_each_instrument_tracks_its_own_previous_state(tmp_path):
    """Two loggers on one file. NQ changing must not consume ES's transition,
    and vice versa — otherwise one pane's activity silently censors the other's
    history.
    """
    p = tmp_path / "d.csv"
    nq_log = sl.SignalLogger(p, instrument="NQ")
    es_log = sl.SignalLogger(p, instrument="ES")

    st = _state(action="WAIT", es_action="WAIT")
    assert nq_log.maybe_log(st, "nq") is True
    assert es_log.maybe_log(st, "es") is True

    # Only NQ moves.
    moved = _state(action="SHORT", es_action="WAIT")
    assert nq_log.maybe_log(moved, "nq") is True
    assert es_log.maybe_log(moved, "es") is False

    # Now only ES moves.
    moved2 = _state(action="SHORT", es_action="LONG")
    assert nq_log.maybe_log(moved2, "nq") is False
    assert es_log.maybe_log(moved2, "es") is True

    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert [(r["instrument"], r["action"]) for r in rows] == [
        ("NQ", "WAIT"), ("ES", "WAIT"), ("NQ", "SHORT"), ("ES", "LONG")]


def test_logger_does_not_retry_a_failed_write_forever(tmp_path):
    """A write failure must not leave the logger re-attempting the same
    transition on every 2s poll for the rest of the session.
    """
    p = tmp_path / "d.csv"
    p.mkdir()                                  # force append to fail
    lg = sl.SignalLogger(p)
    assert lg.maybe_log(_state(action="WAIT"), "nq") is False
    assert lg.maybe_log(_state(action="WAIT"), "nq") is False   # state advanced


def test_logger_never_raises_on_a_malformed_state(tmp_path):
    lg = sl.SignalLogger(tmp_path / "d.csv")
    for bad in (None, {}, {"panes": None}, {"panes": {"nq": None}},
                {"panes": {"nq": {"regime": object()}}}):
        assert lg.maybe_log(bad, "nq") in (True, False)   # returns, never raises


def test_logger_tracks_regime_flips_at_constant_action(tmp_path):
    p = tmp_path / "d.csv"
    lg = sl.SignalLogger(p)
    assert lg.maybe_log(_state(regime="positive", action="WAIT"), "nq") is True
    assert lg.maybe_log(_state(regime="flip_zone", action="WAIT"), "nq") is True
    assert lg.maybe_log(_state(regime="negative", action="WAIT"), "nq") is True

    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert [r["regime"] for r in rows] == ["positive", "flip_zone", "negative"]


def test_default_log_path_is_under_the_gitignored_data_dir():
    # options-scanner/data/ is gitignored, so session data never lands in git.
    assert sl.LOG_PATH.parent.name == "data"
    assert sl.LOG_PATH.parent.parent.name == "options-scanner"
    assert sl.LOG_PATH.suffix == ".csv"


def test_log_path_moved_off_the_single_instrument_file():
    """The columns were renamed, so appending to nq_signals.csv would leave one
    file with two incompatible headers. A new name keeps the old data readable.
    """
    assert sl.LOG_PATH.name != "nq_signals.csv"
