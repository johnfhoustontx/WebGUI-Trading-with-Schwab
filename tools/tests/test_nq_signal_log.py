"""Tests for the NQ HUD's verdict-transition log.

Run: ``python -m pytest tools/tests -q`` from the repo root.

Every write goes to tmp_path — no test may touch the real
options-scanner/data/nq_signals.csv.
"""
import csv
import pathlib
import sys
from datetime import date, datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import nq_signal_log as sl  # noqa: E402


def _state(regime="positive", action="SHORT", **over):
    st = {
        "now": datetime(2026, 7, 29, 12, 34, 56),
        "phase": "pin",
        "regime": regime,
        "dist": 123.456,
        "basis": 118.75,
        "scale": 1.0,
        "atr_nq": 771.38,
        "tape": {"nq": 27192.31, "nq_pct": 0.4567, "ndx": 27073.56, "vix": 14.2},
        "gamma": {"symbol": "$NDX", "session_date": date(2026, 7, 29),
                  "snap_age_s": 41.7},
        "levels": {"flip": 27190.0, "call_wall": 27510.0, "put_wall": 27175.0,
                   "pin": 27200.0, "pin_top_pos": 27350.0},
        "verdict": {"action": action, "entry": 27192.31, "stop": 27540.0,
                    "target": 27200.0, "reason": "..."},
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
    row = sl.build_row(_state())
    assert set(row) == set(sl.FIELDS), "row keys must match the CSV header exactly"


def test_build_row_values():
    row = sl.build_row(_state())
    assert row["ts_ct"] == "2026-07-29 12:34:56"
    assert row["session_date"] == "2026-07-29"
    assert row["source_symbol"] == "$NDX"
    assert row["regime"] == "positive"
    assert row["action"] == "SHORT"
    assert row["nq_spot"] == 27192.31
    assert row["flip_nq"] == 27190.0
    assert row["call_wall_nq"] == 27510.0
    assert row["put_wall_nq"] == 27175.0
    assert row["snap_age_s"] == 42        # rounded to whole seconds


def test_build_row_records_both_pin_candidates():
    """The whole point of the log: design §6 leaves the pin definition open, so
    max(|net|) and the stored top_pos_strike are both recorded, distinctly.
    """
    row = sl.build_row(_state())
    assert row["pin_nq"] == 27200.0
    assert row["pin_top_pos_nq"] == 27350.0
    assert row["pin_nq"] != row["pin_top_pos_nq"]


def test_build_row_writes_empty_not_none():
    # "None" in a CSV cell poisons a pandas numeric column.
    row = sl.build_row(_state(levels={}, verdict={"action": "STAND DOWN"}))
    assert row["flip_nq"] == ""
    assert row["pin_nq"] == ""
    assert row["entry"] == ""
    assert "None" not in {str(v) for v in row.values()}


@pytest.mark.parametrize("state", [
    {}, {"tape": None}, {"gamma": None}, {"levels": None}, {"verdict": None},
    {"now": None},
])
def test_build_row_survives_a_degraded_state(state):
    """A blind HUD still produces a row — that it was blind at that moment is
    itself the datum worth keeping.
    """
    row = sl.build_row(_state(**state))
    assert set(row) == set(sl.FIELDS)


def test_build_row_is_pure():
    st = _state()
    before = repr(st)
    sl.build_row(st)
    assert repr(st) == before, "build_row must not mutate the state it is given"


#############################################
# APPEND
#############################################

def test_append_writes_header_once(tmp_path):
    p = tmp_path / "nq.csv"
    assert sl.append_row(sl.build_row(_state()), p) is True
    assert sl.append_row(sl.build_row(_state(action="LONG")), p) is True

    rows = list(csv.reader(p.open(encoding="utf-8")))
    assert rows[0] == sl.FIELDS          # header exactly once, at the top
    assert len(rows) == 3                # header + 2 data rows
    assert rows[1][sl.FIELDS.index("action")] == "SHORT"
    assert rows[2][sl.FIELDS.index("action")] == "LONG"


def test_append_creates_missing_parent_dirs(tmp_path):
    p = tmp_path / "deep" / "nested" / "nq.csv"
    assert sl.append_row(sl.build_row(_state()), p) is True
    assert p.exists()


def test_append_never_raises_on_an_unwritable_path(tmp_path):
    # A directory where the file should be: open() fails, must not propagate.
    p = tmp_path / "nq.csv"
    p.mkdir()
    assert sl.append_row(sl.build_row(_state()), p) is False


def test_append_is_round_trippable_by_dictreader(tmp_path):
    p = tmp_path / "nq.csv"
    sl.append_row(sl.build_row(_state()), p)
    back = list(csv.DictReader(p.open(encoding="utf-8")))
    assert len(back) == 1
    assert back[0]["source_symbol"] == "$NDX"
    assert float(back[0]["pin_top_pos_nq"]) == 27350.0


#############################################
# SignalLogger — the stateful wrapper
#############################################

def test_logger_writes_once_per_transition_not_once_per_poll(tmp_path):
    p = tmp_path / "nq.csv"
    lg = sl.SignalLogger(p)

    assert lg.maybe_log(_state(action="WAIT")) is True     # first read
    for _ in range(50):                                    # 50 unchanged polls
        assert lg.maybe_log(_state(action="WAIT")) is False
    assert lg.maybe_log(_state(action="SHORT")) is True     # transition
    assert lg.maybe_log(_state(action="SHORT")) is False

    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert [r["action"] for r in rows] == ["WAIT", "SHORT"]


def test_logger_does_not_retry_a_failed_write_forever(tmp_path):
    """A write failure must not leave the logger re-attempting the same
    transition on every 2s poll for the rest of the session.
    """
    p = tmp_path / "nq.csv"
    p.mkdir()                                  # force append to fail
    lg = sl.SignalLogger(p)
    assert lg.maybe_log(_state(action="WAIT")) is False
    assert lg.maybe_log(_state(action="WAIT")) is False     # state advanced anyway


def test_logger_never_raises_on_a_malformed_state(tmp_path):
    lg = sl.SignalLogger(tmp_path / "nq.csv")
    for bad in (None, {}, {"verdict": None}, {"regime": object()}):
        assert lg.maybe_log(bad) in (True, False)   # returns, never raises


def test_logger_tracks_regime_flips_at_constant_action(tmp_path):
    p = tmp_path / "nq.csv"
    lg = sl.SignalLogger(p)
    assert lg.maybe_log(_state(regime="positive", action="WAIT")) is True
    assert lg.maybe_log(_state(regime="flip_zone", action="WAIT")) is True
    assert lg.maybe_log(_state(regime="negative", action="WAIT")) is True

    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert [r["regime"] for r in rows] == ["positive", "flip_zone", "negative"]


def test_default_log_path_is_under_the_gitignored_data_dir():
    # options-scanner/data/ is gitignored, so session data never lands in git.
    assert sl.LOG_PATH.parent.name == "data"
    assert sl.LOG_PATH.parent.parent.name == "options-scanner"
    assert sl.LOG_PATH.suffix == ".csv"
