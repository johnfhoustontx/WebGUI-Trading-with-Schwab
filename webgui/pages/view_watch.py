"""The version-gated repaint idiom, once.

Every data page polls the same way: probe a view's cheap ``{key}:ver`` counter on
a 2 s timer, and repaint only when it moves. That closure — seed the version,
compare, store, call the page's own re-read/apply — was written out longhand on
22 pages (2026-08-20). Written once here, a page needs one line.

The helper lives beside ``ui_guard`` rather than in ``bus_client`` on purpose:
this one owns a ``ui.timer``, and the bus client must stay free of NiceGUI so it
can be imported from anywhere (including tests with no UI context).
"""
from nicegui import ui

import bus_client

from .ui_guard import guard


def watch_view(view, on_change, *, interval: float = 2.0, timer=None):
    """Call ``on_change()`` whenever ``view``'s cache version moves.

    Seeds the current version first, so the first tick does NOT fire — without
    that every page repaints once on load for no reason. A cold view seeds as
    None, so the first real publish still counts as a change and the page fills
    in when its service comes up.

    ``on_change`` is wrapped in :func:`guard`, so a repaint that outlives its
    client is a clean no-op rather than a traceback per tick. ``timer`` is an
    injection point for tests; production always uses ``ui.timer``.
    """
    state = {"ver": bus_client.read_version(view)}

    @guard
    def _tick():
        ver = bus_client.read_version(view)
        if ver == state["ver"]:
            return
        state["ver"] = ver
        on_change()

    return (timer or ui.timer)(interval, _tick)
