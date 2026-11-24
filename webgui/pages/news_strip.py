"""The public site's Market News tab strip: Headlines | X Feed.

On the private app Market News is a rail group (``main.MARKET_NEWS_CHILDREN``)
and the shell draws its tab strip. The public origin has no shell to draw one -
its header carries no navigation by design (``live_main._header``) - so each
public news page draws this strip at the top of its own page instead, as the
Strategy Tools do (``pages/options/strategy_strip.py``).

It links only to pages THIS origin serves: each tab is a private route resolved
through ``shell`` (``can_navigate`` before drawing it, ``navigate_to`` on a
click). On the private app it draws nothing, because the shell's own strip is
already there. The labels are the private group's, pinned equal by
``tests/test_xfeed_live.py``.
"""
import shell as _shell

HEADLINES = "/news"
X_FEED = "/news/x"

#: (private route, label), in the private group's order.
PAGES = (
    (HEADLINES, "Headlines"),
    (X_FEED, "X Feed"),
)


def tabs():
    """The news pages this process can navigate to, in order."""
    return [(route, label) for route, label in PAGES if _shell.can_navigate(route)]


def render(active):
    """Draw the strip with ``active`` selected, or nothing: on the private app
    (the shell draws it) and wherever fewer than two pages are served."""
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
