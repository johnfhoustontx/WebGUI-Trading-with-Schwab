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


#############################################
# Elapsed counter — a long wait must look like progress, not a hang
#############################################

def test_elapsed_label_updates_the_message_while_busy():
    """A static "Analyzing…" for 90 seconds is indistinguishable from a hang.

    The watchdog already ticks once a second while busy, so the counter rides on
    the timer that exists rather than adding a second one per panel."""
    seen = []

    def label_for(sec):
        seen.append(sec)
        return f"Working… {int(sec)}s"

    _target, spin = _mount(timeout=100, elapsed_label=label_for)
    spin.show("Working…")
    spin.tick()
    assert seen, "the tick must consult the formatter while busy"
    assert spin.label.text.startswith("Working… ")


def test_elapsed_label_is_not_consulted_when_hidden():
    """The timer is deactivated on hide, but a tick already in flight must not
    rewrite the label of an invisible scrim."""
    seen = []
    _target, spin = _mount(timeout=100, elapsed_label=lambda s: seen.append(s) or "x")
    spin.tick()
    assert seen == []


def test_a_broken_formatter_cannot_take_the_page_down():
    """It runs on a timer inside a live page. A formatter that raises must lose
    the counter, not the spinner and not the session."""
    _target, spin = _mount(timeout=100,
                           elapsed_label=lambda s: 1 / 0)
    spin.show("Working…")
    spin.tick()                       # must not raise
    assert spin.element.visible, "the spinner survives a bad formatter"
    assert spin.label.text == "Working…"


def test_elapsed_is_measured_from_show_not_from_mount():
    """Restarting a wait restarts the count -- otherwise the second analysis of a
    session opens at whatever the first one reached."""
    seen = []
    _target, spin = _mount(timeout=100, elapsed_label=lambda s: seen.append(s) or "x")
    spin.show()
    time.sleep(0.02)
    spin.tick()
    first = seen[-1]
    spin.hide()
    spin.show()
    spin.tick()
    assert seen[-1] < first, "show() must reset the clock"


def test_the_backstop_can_be_lengthened_per_panel():
    """The default is sized for a ~19s fetch. A panel whose work legitimately
    takes minutes must be able to say so, or the backstop hides the spinner while
    the work is still running -- which is the failure the module's own docstring
    warns about, and which happened on the trade page at 96 seconds."""
    _target, spin = _mount(timeout=300)
    spin.show()
    spin.tick()
    assert spin.element.visible
