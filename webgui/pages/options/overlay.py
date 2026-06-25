"""Shared full-screen wait overlay for the Options pages (Tier-1).

A ``position:fixed`` dimmed backdrop with a centered spinner shown while a symbol
loads (Calculator Load / Simulator Fetch). Built once per page render; toggled via
the returned ``show(msg)`` / ``hide()`` handle. The overlay covers the viewport
(blocks clicks) and sits above the page (high z-index) regardless of DOM nesting,
so it can be created anywhere in render()."""
from types import SimpleNamespace

from nicegui import ui

_OVERLAY_STYLE = (
    "position:fixed;inset:0;z-index:9999;display:flex;flex-direction:column;"
    "align-items:center;justify-content:center;gap:14px;"
    "background:rgba(6,12,24,0.55);backdrop-filter:blur(1px);"
)

# Safety-timeout (seconds) for the wait overlay: a backstop that hides the spinner if
# the load NEVER lands (e.g. the service is down). The PRIMARY dismissal is data
# arrival, so this must comfortably exceed the slowest legitimate fetch — the
# Simulator's sim_fetch builds a full ChainSnapshot and measured ~19s for SPY
# (6870 contracts), so 15s fired prematurely. Shared by both pages so they don't drift.
LOAD_TIMEOUT_SEC = 30.0


def build_loading_overlay(text="Loading…"):
    """Mount a hidden full-screen wait overlay. Returns a handle with
    ``element`` / ``show(msg=None)`` / ``hide()``."""
    overlay = ui.element("div").style(_OVERLAY_STYLE)
    with overlay:
        ui.spinner(size="lg", color="primary")
        label = ui.label(text).style(
            "color:#e7edf8;font-weight:600;letter-spacing:.02em;")
    overlay.set_visibility(False)

    def show(msg=None):
        if msg is not None:
            label.text = msg
        overlay.set_visibility(True)

    def hide():
        overlay.set_visibility(False)

    return SimpleNamespace(element=overlay, show=show, hide=hide)
