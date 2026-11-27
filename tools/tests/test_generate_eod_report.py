"""The scheduled EOD report run (tools/generate_eod_report.py).

The interesting behaviour is all in the GATES, not in the report -- the report
itself is ``webgui/pages/eod.py``, tested in ``webgui/tests/test_eod.py``. What
this run adds is: do not fire on a holiday, and never overwrite a real archive
with a page of "No data" notes.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "webgui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import generate_eod_report as g  # noqa: E402


class _FakeEod:
    """Stands in for ``pages.eod`` so the gates are tested without a bus."""

    def __init__(self, snap, *, has_data=True, root=pathlib.Path("/tmp/eod")):
        self._snap = snap
        self._has = has_data
        self.ARCHIVE_ROOT = root
        self.generated_with = None

    def read_snapshot(self):
        return self._snap

    def has_data(self, snap):
        assert snap is self._snap
        return self._has

    def generate(self, snap=None):
        self.generated_with = snap
        return {"date": self._snap["date"]}


@pytest.fixture
def fake(monkeypatch):
    eod = _FakeEod({"date": "2026-09-17", "scan": {"signals": [1]}})
    monkeypatch.setattr(g, "_eod", lambda: eod)
    monkeypatch.setattr(g, "is_trading_day", lambda d: True)
    return eod


def _freeze(monkeypatch, when):
    """Pin ``datetime.now(CT)`` inside the module under test."""
    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    monkeypatch.setattr(g, "datetime", _DT)


# --- the happy path ---------------------------------------------------------
def test_a_trading_day_writes_the_report(fake, monkeypatch, capsys):
    _freeze(monkeypatch, dt.datetime(2026, 9, 17, 15, 15, tzinfo=g.CT))
    assert g.main([]) == 0
    assert fake.generated_with is fake._snap
    assert "2026-09-17" in capsys.readouterr().out


def test_the_snapshot_examined_is_the_snapshot_written(fake, monkeypatch):
    """``generate`` is handed the snapshot the gate inspected, never left to
    re-read one. A second read would mean the bytes checked for emptiness were
    not the bytes archived -- and at 15:15 the caches are still moving."""
    _freeze(monkeypatch, dt.datetime(2026, 9, 17, 15, 15, tzinfo=g.CT))
    g.main([])
    assert fake.generated_with is fake.read_snapshot()


# --- the trading-day gate ---------------------------------------------------
def test_a_holiday_writes_nothing_and_is_not_a_failure(fake, monkeypatch, capsys):
    """Exit 0: a correct skip must not show up in `systemctl --user --failed`,
    or a Thanksgiving would look like a broken schedule."""
    monkeypatch.setattr(g, "is_trading_day", lambda d: False)
    _freeze(monkeypatch, dt.datetime(2026, 11, 26, 15, 15, tzinfo=g.CT))  # a Thursday
    assert g.main([]) == 0
    assert fake.generated_with is None
    assert "market holiday" in capsys.readouterr().out


def test_a_weekend_says_weekend_not_holiday(fake, monkeypatch, capsys):
    monkeypatch.setattr(g, "is_trading_day", lambda d: False)
    _freeze(monkeypatch, dt.datetime(2026, 9, 19, 15, 15, tzinfo=g.CT))  # a Saturday
    assert g.main([]) == 0
    assert "weekend" in capsys.readouterr().out


def test_force_generates_on_a_non_trading_day(fake, monkeypatch):
    monkeypatch.setattr(g, "is_trading_day", lambda d: False)
    _freeze(monkeypatch, dt.datetime(2026, 9, 19, 15, 15, tzinfo=g.CT))
    assert g.main(["--force"]) == 0
    assert fake.generated_with is not None


# --- the empty-cache gate ---------------------------------------------------
def test_an_empty_snapshot_writes_nothing_and_FAILS(monkeypatch, capsys):
    """The one that matters. Every builder in pages/eod.py degrades to a "No
    data" note, so a run against a stopped stack produces a complete-looking
    report -- and the archive is keyed by date and overwrites in place, so
    writing it would destroy the day's real one. Exit 1 so the failure is
    visible in `systemctl --user --failed` rather than as a blank report."""
    eod = _FakeEod({"date": "2026-09-17"}, has_data=False)
    monkeypatch.setattr(g, "_eod", lambda: eod)
    monkeypatch.setattr(g, "is_trading_day", lambda d: True)
    _freeze(monkeypatch, dt.datetime(2026, 9, 17, 15, 15, tzinfo=g.CT))

    assert g.main([]) == 1
    assert eod.generated_with is None
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "MEMURAI_PASSWORD" in err     # names the likeliest cause


def test_allow_empty_is_the_deliberate_override(monkeypatch):
    eod = _FakeEod({"date": "2026-09-17"}, has_data=False)
    monkeypatch.setattr(g, "_eod", lambda: eod)
    monkeypatch.setattr(g, "is_trading_day", lambda d: True)
    _freeze(monkeypatch, dt.datetime(2026, 9, 17, 15, 15, tzinfo=g.CT))
    assert g.main(["--allow-empty"]) == 0
    assert eod.generated_with is not None


# --- the half-day schedule (--scheduled, from the timer) ----------------------
# The timer fires at [slots.eod_report] at (15:15) AND half_day_at (12:15) every
# weekday; --scheduled keeps only the firing that is today's: half_day_at on a
# half day ([early_close] in config/sessions.toml), at on any other day.
_HALF = (2026, 11, 27)      # a half day in the tracked config: closes 12:00 CT
_REG = (2026, 11, 30)       # a regular Monday


def _at(day, hh, mm):
    return dt.datetime(*day, hh, mm, tzinfo=g.CT)


@pytest.mark.parametrize("day, hh, mm, writes", [
    (_HALF, 12, 15, True),      # a half day's report, after its 12:00 close
    (_HALF, 15, 15, False),     # ...and not again at 15:15
    (_REG, 15, 15, True),       # a regular day is unchanged
    (_REG, 12, 15, False),      # ...and a 12:15 firing on it is not a run
    (_REG, 15, 34, True),       # a late firing inside grace_min still runs
    (_REG, 15, 36, False),      # past grace_min it does not
])
def test_a_scheduled_run_writes_only_at_todays_slot(fake, monkeypatch, capsys,
                                                    day, hh, mm, writes):
    _freeze(monkeypatch, _at(day, hh, mm))
    assert g.main(["--scheduled"]) == 0          # a skip is never a failure
    assert (fake.generated_with is not None) is writes
    if not writes:
        assert "Skipped" in capsys.readouterr().out


def test_a_skipped_firing_says_when_todays_report_runs(fake, monkeypatch, capsys):
    _freeze(monkeypatch, _at(_HALF, 15, 15))
    g.main(["--scheduled"])
    assert "12:15" in capsys.readouterr().out


def test_a_skipped_firing_never_imports_the_webgui(monkeypatch):
    def _boom():
        raise AssertionError("pages.eod imported for a firing that is not today's")
    monkeypatch.setattr(g, "_eod", _boom)
    monkeypatch.setattr(g, "is_trading_day", lambda d: True)
    _freeze(monkeypatch, _at(_REG, 12, 15))
    assert g.main(["--scheduled"]) == 0


def test_a_run_by_hand_ignores_the_schedule(fake, monkeypatch):
    """Without --scheduled the tool is the Generate button: it writes whenever it
    is run, as it always has."""
    _freeze(monkeypatch, _at(_REG, 12, 15))
    assert g.main([]) == 0
    assert fake.generated_with is not None


def test_the_half_day_slot_follows_the_config(fake, monkeypatch):
    """Read from [slots.eod_report] half_day_at, never a literal 12:15."""
    from shared import market_calendar as mc
    real = mc.slot_times
    monkeypatch.setattr(g.mc, "slot_times", lambda name: {
        **real(name), "half_day_at": dt.time(12, 40)} if name == "eod_report" else real(name))
    _freeze(monkeypatch, _at(_HALF, 12, 15))
    g.main(["--scheduled"])
    assert fake.generated_with is None
    _freeze(monkeypatch, _at(_HALF, 12, 40))
    g.main(["--scheduled"])
    assert fake.generated_with is not None


def test_the_half_day_slot_is_after_the_early_close_and_before_the_regular_one():
    """A half-day report built before its close is a mid-day report; one at or
    after the regular slot would collide with that firing's window."""
    from shared import market_calendar as mc
    times = mc.slot_times("eod_report")
    assert mc.early_close_time() < times["half_day_at"] < times["at"]


# --- the import is deferred -------------------------------------------------
def test_the_holiday_path_never_imports_the_webgui(monkeypatch, capsys):
    """A holiday firing prints one line and exits; it must not pay NiceGUI's
    import to do it. Also keeps this module importable without the webgui."""
    def _boom():
        raise AssertionError("pages.eod imported on a non-trading day")
    monkeypatch.setattr(g, "_eod", _boom)
    monkeypatch.setattr(g, "is_trading_day", lambda d: False)
    _freeze(monkeypatch, dt.datetime(2026, 9, 19, 15, 15, tzinfo=g.CT))
    assert g.main([]) == 0


# --- the real module, imported for real -------------------------------------
def test_the_real_eod_module_exposes_what_this_tool_calls():
    """Guards the seam between the tool and the page module: a rename on either
    side would otherwise surface only at 15:15 on the prod box."""
    eod = g._eod()
    assert isinstance(eod, types.ModuleType)
    for name in ("read_snapshot", "has_data", "generate", "ARCHIVE_ROOT"):
        assert hasattr(eod, name), name
