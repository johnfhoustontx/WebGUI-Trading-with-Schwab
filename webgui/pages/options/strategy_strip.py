"""The public site's Strategy Tools tab strip (2026-10-24).

On the private app the three tools are one rail group, and the shell draws the
group's tab strip (``main.STRATEGY_TOOLS_CHILDREN``). The public origin has no
shell to draw one - its header carries no navigation by design
(``live_main._header``) - so each public tool draws this strip at the top of its
own page instead. The site's Live Desk gives each of the three a card.

It links only to tools THIS origin serves: each tab is a private route resolved
through ``shell`` (``can_navigate`` before drawing it, ``navigate_to`` on a
click), the seam ``tests/test_live_navigation.py`` holds every page to. On the
private app it draws nothing, because the shell's own strip is already there.

The labels are the private group's, pinned equal by
``tests/test_strategy_strip.py``.
"""
import shell as _shell

#: (private route, label), in the private group's order.
TOOLS = (
    ("/options/calculator", "Calculator"),
    ("/options/simulator", "Simulator"),
    ("/options/expected-move", "Expected Move"),
)


def tabs():
    """The tools this process can navigate to, in order."""
    return [(route, label) for route, label in TOOLS if _shell.can_navigate(route)]


def render(active):
    """Draw the strip with ``active`` selected, or nothing: on the private app
    (the shell draws it) and wherever fewer than two tools are served."""
    if not _shell.is_public():
        return None
    shown = tabs()
    if len(shown) < 2:
        return None
    from nicegui import ui

    strip = ui.tabs(value=active).classes("compact-subtabs w-full").props(
        "dense no-caps align=left inline-label")
    with strip:
        for route, label in shown:
            ui.tab(route, label=label)
    strip.on_value_change(lambda e: _shell.navigate_to(e.value)
                          if e.value != active else None)
    return strip
