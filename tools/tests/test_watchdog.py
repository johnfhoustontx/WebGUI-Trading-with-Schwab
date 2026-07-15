"""Tests for the process watchdog's pure logic (no network/subprocess).

Run: ``cd tools && ..\\.venv\\Scripts\\python -m pytest tests`` (not in the per-service CI
matrix — tools/ is a utilities dir).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import watchdog as w


def test_under_storm_cap():
    assert w.under_storm_cap([], now=1000) is True
    # 3 restarts all older than the window → allowed again
    assert w.under_storm_cap([100, 200, 300], now=1000, max_restarts=3, window_sec=600) is True
    # 3 restarts inside the window → blocked
    assert w.under_storm_cap([500, 600, 700], now=1000, max_restarts=3, window_sec=600) is False
    # only 2 inside the window → allowed
    assert w.under_storm_cap([700, 800], now=1000, max_restarts=3, window_sec=600) is True


def test_component_targets_cover_all_tiers():
    names = [t["name"] for t in w.component_targets()]
    assert {"memurai", "proxy", "webgui"} <= set(names)
    assert any("options_svc" in n for n in names)
    # every target has a restart argv (memurai is start-if-stopped) except none-configured
    for t in w.component_targets():
        assert t.get("restart")


def test_sweep_no_restart_when_healthy(monkeypatch):
    monkeypatch.setattr(w, "probe", lambda t: True)
    hist = {}
    w.sweep(w.component_targets(), hist, now=1.0)
    assert hist == {}


def test_sweep_dry_run_never_restarts(monkeypatch):
    monkeypatch.setattr(w, "probe", lambda t: t["name"] != "proxy")
    called = []
    monkeypatch.setattr(w, "_restart", lambda t: called.append(t["name"]))
    hist = {}
    w.sweep(w.component_targets(), hist, now=1.0, dry_run=True)
    assert called == [] and hist == {}


def test_sweep_restarts_down_component_and_records(monkeypatch):
    monkeypatch.setattr(w, "probe", lambda t: t["name"] != "proxy")
    called = []
    monkeypatch.setattr(w, "_restart", lambda t: called.append(t["name"]))
    hist = {}
    w.sweep(w.component_targets(), hist, now=5.0)
    assert called == ["proxy"] and hist["proxy"] == [5.0]


def test_sweep_respects_storm_cap(monkeypatch):
    monkeypatch.setattr(w, "probe", lambda t: t["name"] != "proxy")
    called = []
    monkeypatch.setattr(w, "_restart", lambda t: called.append(t["name"]))
    # proxy already restarted MAX_RESTARTS times recently → no more
    hist = {"proxy": [1.0, 2.0, 3.0]}
    w.sweep(w.component_targets(), hist, now=4.0)
    assert called == []
