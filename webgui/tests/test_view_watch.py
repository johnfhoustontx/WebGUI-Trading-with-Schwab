"""Tests for pages/view_watch.py — the shared version-gated repaint helper.

The `_maybe_repaint` closure (probe read_version, compare to state["ver"],
re-read + repaint, hang it on a 2 s ui.timer) was written out longhand on 22
pages. This is that idiom, once.
"""
import bus_client
from pages import view_watch


def setup_function(_fn):
    bus_client.reset()


class _FakeTimer:
    """Captures what watch_view would hang on ui.timer."""
    last = None

    def __init__(self, interval, cb):
        self.interval, self.cb = interval, cb
        _FakeTimer.last = self


def _watch(view, on_change, **kw):
    return view_watch.watch_view(view, on_change, timer=_FakeTimer, **kw)


def test_watch_view_seeds_the_version_and_does_not_fire_on_the_first_tick():
    """Seeding matters: without it the first tick always reports 'changed' and
    every page repaints once for nothing on load."""
    bus_client.bus().cache_set("cache:sentiment:rotation", {"a": 1})
    fired = []
    _watch("sentiment:rotation", lambda: fired.append(1))
    _FakeTimer.last.cb()
    assert fired == []


def test_watch_view_fires_when_the_version_moves():
    b = bus_client.bus()
    b.cache_set("cache:sentiment:rotation", {"a": 1})
    fired = []
    _watch("sentiment:rotation", lambda: fired.append(1))
    b.cache_set("cache:sentiment:rotation", {"a": 2})
    _FakeTimer.last.cb()
    assert fired == [1]


def test_watch_view_fires_once_per_change_not_once_per_tick():
    b = bus_client.bus()
    b.cache_set("cache:sentiment:rotation", {"a": 1})
    fired = []
    _watch("sentiment:rotation", lambda: fired.append(1))
    b.cache_set("cache:sentiment:rotation", {"a": 2})
    for _ in range(4):
        _FakeTimer.last.cb()
    assert fired == [1]


def test_watch_view_uses_the_default_two_second_cadence():
    _watch("sentiment:rotation", lambda: None)
    assert _FakeTimer.last.interval == 2.0
    _watch("sentiment:rotation", lambda: None, interval=5.0)
    assert _FakeTimer.last.interval == 5.0


def test_watch_view_on_a_cold_view_fires_when_it_first_appears():
    """A page built before its service has published must repaint when the
    first payload lands — the seed is None, and None -> 1 is a change."""
    fired = []
    _watch("sentiment:rotation", lambda: fired.append(1))
    bus_client.bus().cache_set("cache:sentiment:rotation", {"a": 1})
    _FakeTimer.last.cb()
    assert fired == [1]


def test_watch_view_lets_a_real_repaint_error_propagate():
    """A genuine repaint bug must NOT be swallowed — this repo treats
    try/except-degrade guards that hide errors as harmful, and NiceGUI's timer
    logs the exception and keeps ticking anyway. Only the deleted-client case is
    absorbed (by ui_guard), because there is nothing left to paint."""
    import pytest

    b = bus_client.bus()
    b.cache_set("cache:sentiment:rotation", {"a": 1})

    def _boom():
        raise RuntimeError("repaint failed")

    _watch("sentiment:rotation", _boom)
    b.cache_set("cache:sentiment:rotation", {"a": 2})
    with pytest.raises(RuntimeError, match="repaint failed"):
        _FakeTimer.last.cb()


def test_watch_view_absorbs_a_deleted_client_repaint():
    """The case ui_guard exists for: the tab went away mid-tick."""
    b = bus_client.bus()
    b.cache_set("cache:sentiment:rotation", {"a": 1})

    def _gone():
        raise RuntimeError("The client this element belongs to has been deleted.")

    _watch("sentiment:rotation", _gone)
    b.cache_set("cache:sentiment:rotation", {"a": 2})
    _FakeTimer.last.cb()          # clean no-op, no traceback
