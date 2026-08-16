"""Tests for the shared inline busy spinner (pages/busy.py)."""
import time

from nicegui import ui

from pages import busy


def _mount(**kw):
    with ui.card():
        target = ui.element("div")
        handle = busy.build_busy(target, **kw)
    return target, handle


def test_starts_hidden_and_shows_on_demand():
    target, spin = _mount()
    assert not spin.element.visible, "a page must not open mid-spin"
    spin.show()
    assert spin.element.visible
    spin.hide()
    assert not spin.element.visible


def test_target_is_made_relative():
    """The scrim is position:absolute, so it anchors to the nearest POSITIONED
    ancestor. A target that isn't positioned does not fail loudly — the spinner
    silently escapes and covers some outer container — so the helper does it
    rather than trusting every call site to remember."""
    target, _spin = _mount()
    assert "relative" in target.classes


def test_scrim_is_mounted_inside_the_target():
    """Re-parenting guard: the same absolute-positioning argument means the scrim
    has to be a CHILD of the region it covers."""
    target, spin = _mount()
    assert spin.element in target.default_slot.children


def test_show_can_replace_the_message():
    _target, spin = _mount(text="Loading…")
    labels = [c for c in spin.element.default_slot.children if isinstance(c, ui.label)]
    assert labels[0].text == "Loading…"
    spin.show("Loading NVDA…")
    assert labels[0].text == "Loading NVDA…"
    # ...and a show() with no message leaves the previous one in place.
    spin.show()
    assert labels[0].text == "Loading NVDA…"


def test_watchdog_runs_only_while_busy():
    """A 1s timer ticking forever on every page would be pure waste — the backstop
    is only meaningful between show() and hide()."""
    _target, spin = _mount()
    assert spin.timer.active is False, "idle pages must not run a watchdog"
    spin.show()
    assert spin.timer.active is True
    spin.hide()
    assert spin.timer.active is False


def test_backstop_hides_a_fetch_that_never_lands():
    """Data arrival is the PRIMARY dismissal; this is what stops a dead service
    leaving a panel spinning forever."""
    _target, spin = _mount(timeout=0.01)
    spin.show()
    assert spin.element.visible
    spin.tick()                      # before the deadline — must NOT hide
    assert spin.element.visible
    time.sleep(0.02)
    spin.tick()                      # past it
    assert not spin.element.visible
    assert spin.timer.active is False, "the watchdog stops with the spinner"


def test_tick_is_inert_when_not_busy():
    """The timer is deactivated on hide, but a tick already in flight must not
    resurrect anything or raise."""
    _target, spin = _mount(timeout=0.01)
    spin.tick()
    assert not spin.element.visible


def test_timeout_default_matches_the_full_screen_overlay():
    """Both waits back the same fetches — the slowest measured legitimate one is
    the Simulator's ~19s sim_fetch — so a backstop that differs between them would
    make one of the two wrong."""
    from pages.options import overlay
    assert busy.BUSY_TIMEOUT_SEC == overlay.LOAD_TIMEOUT_SEC
