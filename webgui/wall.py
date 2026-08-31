"""The wall display: three dashboards on one screen, rotating on a timer.

This page exists to be POINTED A CAMERA AT — literally. On the prod host, Xvfb
runs a kiosk Chrome on this one URL and ffmpeg encodes that framebuffer to a
public YouTube live stream. Nobody clicks it, nobody scrolls it, and nothing
navigates it for the whole session; it is opened once in the morning and left.

That single constraint is what makes it a separate document rather than a fourth
NiceGUI page. A page built out of ``ui.*`` elements would be a fourth live client
with a websocket, a Vue runtime and a reconnect story — all of which exist to
serve someone interacting with it, and none of which survives a nine-hour
unattended session any better than an ``<iframe>`` does. So this is a raw
``HTMLResponse`` shell whose entire content is three iframes onto pages that
already work, plus a timer that decides which one is on top.

**It composes; it renders nothing of its own.** Every number on screen is drawn
by ``/desk``, ``/market`` and ``/sentiment/momentum`` — the real pages, running
their own watchers, in their own iframes. That is the whole design: a wall view
that re-derived so much as a rounding rule would be a fourth screen quietly
disagreeing with the three it mirrors, which is the shape of a bug this repo
already carries one documented instance of.

⚠ This file is deliberately OUTSIDE the Tailwind-first standard, which binds
NiceGUI components styled with ``.classes()``. It emits a raw ``HTMLResponse``
document — the documented out-of-scope case, the same one ``/desk/live`` and the
EOD report sit in — so it carries its own ``<style>`` block. Its palette is still
read out of ``pages/options/theme`` rather than hand-picked.
"""
from pages.options import theme

# The route this module owns. A constant because ``main`` registers it and the
# capture script's Chrome command line names it; a literal in three places is how
# a rename half-lands.
PAGE_ROUTE = "/wall"

# Rotation order, and the single source of it. The iframe list, the overlay label
# and the JS rotation all read this, so adding a fourth screen is one entry here
# and nothing else. The label is not decoration: a viewer landing mid-rotation
# has no other way to know which dashboard they are looking at.
PAGES = [
    {"path": "/desk", "label": "DESK"},
    {"path": "/market", "label": "MACRO BOARD"},
    {"path": "/sentiment/momentum", "label": "MOMENTUM"},
]

# How long each page holds the screen, and how long the handover takes.
DWELL_MS = 15_000
FADE_MS = 400

# The console palette — the same hexes ``/desk`` paints with, read from the theme
# rather than copied, so a `config/theme.toml` edit reaches the stream.
_C = theme.CONSOLE_COLORS


_CSS = """
*, *::before, *::after {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; height: 100%; overflow: hidden; }}
/* The ground the crossfade dips through: for {fade}ms neither panel is fully
   opaque, so whatever sits behind them is briefly on camera. */
body {{ background: {cell}; color: {text};
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
.stage {{ position: relative; width: 100vw; height: 100vh; }}
"""


def document():
    """The wall page: styling, and the three dashboards stacked in one stage.

    Nothing here is server-rendered but the shell. Every pixel a viewer sees is
    painted by the three real pages inside the iframes, on their own cadences.
    """
    css = _CSS.format(cell=_C["cell"], text=_C["text"], fade=FADE_MS)
    frames = "\n".join(
        '  <iframe class="panel" src="{path}" title="{label}"></iframe>'.format(
            path=p["path"], label=p["label"]) for p in PAGES)
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wall</title>
<style>{css}</style>
</head>
<body>
<div class="stage">
{frames}
</div>
</body>
</html>
""".format(css=css, frames=frames)
