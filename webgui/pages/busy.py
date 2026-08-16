"""Inline "this region is refreshing" spinner (app-wide, Tier-1).

A rotating spinner on a translucent scrim that covers ONE container — the chart
or table whose data is being replaced — rather than the whole viewport. The rest
of the page stays readable and usable while a background fetch runs, which is
what a symbol change on Dealer Positioning wants: the controls, the status strip
and the nav are all still meaningful while the chart is refetched.

Distinct from ``pages/options/overlay.py``, which is a FULL-SCREEN dimmed
backdrop and stays where it is. That one is used by the Calculator and Simulator,
where loading a new symbol invalidates every control on the page (strikes,
expiries, the whole leg editor), so blocking interaction is the honest signal.
Use this module when one panel is refreshing and the page around it still means
something; use that one when the page's entire premise is being replaced.

Usage — the wait begins where the command is enqueued and ends where the repaint
lands, which for most pages is the version-poll::

    chart_box = ui.element("div").classes("w-full")
    with chart_box:
        chart = ui.highchart(fig)
    spin = busy.build_busy(chart_box, "Loading SPY…")
    ...
    def _request():
        bus_client.request("options", {...})
        spin.show()

    async def _poll():
        if version_moved:
            repaint()
            spin.hide()
"""
import time as _time
from types import SimpleNamespace

from nicegui import ui

# Safety backstop (seconds): hide the spinner if the data NEVER lands (service
# down, command dropped), so a dead fetch leaves a readable page rather than a
# panel spinning forever. The PRIMARY dismissal is always data arrival.
#
# Matches overlay.LOAD_TIMEOUT_SEC deliberately — the slowest legitimate fetch in
# the app is the Simulator's sim_fetch, measured ~19 s for SPY (6870 contracts),
# and a backstop that fires before the data can plausibly arrive is worse than no
# backstop: it says "finished" while the work is still running.
BUSY_TIMEOUT_SEC = 30.0


def build_busy(target, text="Loading…", timeout=BUSY_TIMEOUT_SEC):
    """Mount a hidden spinner covering ``target``. Returns a handle with
    ``element`` / ``show(msg=None)`` / ``hide()`` / ``visible``.

    ``target`` is made ``relative`` here rather than left to the caller: the scrim
    is ``position:absolute``, so it anchors to the nearest POSITIONED ancestor,
    and a target that isn't positioned doesn't fail loudly — the spinner silently
    escapes to cover some outer container instead. Doing it in one place means no
    call site can forget.
    """
    target.classes(add="relative")
    with target:
        scrim = ui.element("div").classes(
            "absolute inset-0 z-[40] flex flex-col items-center justify-center "
            "gap-3 rounded-[12px] bg-[rgba(9,14,28,0.62)]")
        with scrim:
            ui.spinner(size="lg", color="primary")
            label = ui.label(text).classes(
                "text-[12.5px] font-medium text-[#cdd7ec] tracking-[.02em]")
    scrim.set_visibility(False)

    state = {"deadline": None}

    def _tick():
        # A deadline check on a 1 s timer that only runs WHILE busy, rather than a
        # one-shot timer per show(): a fresh timer on every refresh would leak one
        # element per fetch across a long session, and simply re-activating a
        # repeating one can fire early because its sleep phase is not reset.
        if state["deadline"] is not None and _time.monotonic() >= state["deadline"]:
            hide()

    watchdog = ui.timer(1.0, _tick, active=False)

    def show(msg=None):
        if msg is not None:
            label.text = msg
        scrim.set_visibility(True)
        state["deadline"] = _time.monotonic() + timeout
        watchdog.active = True

    def hide():
        scrim.set_visibility(False)
        state["deadline"] = None
        watchdog.active = False

    # ``tick`` and ``timer`` are the watchdog's body and its schedule. They are on
    # the handle so the backstop is testable at all: it is the one behaviour here
    # that cannot be observed by calling show()/hide(), and an untested backstop
    # is exactly the kind of thing that silently stops working.
    return SimpleNamespace(element=scrim, show=show, hide=hide,
                           visible=lambda: scrim.visible,
                           tick=_tick, timer=watchdog)
