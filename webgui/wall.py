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

**All three panels are ALWAYS laid out at full size, and rotation is nothing but
``opacity`` and ``z-index``.** That is the load-bearing rule here, and it holds
for three separate reasons:

1. An iframe hidden with ``display:none`` has **zero layout size**, and this
   app's Highcharts have no ResizeObserver — a chart that mounts at 0x0 renders
   collapsed and never recovers. None of these three pages draws a chart today,
   so the trap is latent rather than live; the day one gains a chart, or a
   chart-heavy page like ``/options/gamma`` is rotated in, a hidden panel would
   break silently and only on camera.
2. All three stay painted and connected, so a handover is instant. Navigating
   one iframe between the three would instead show a NiceGUI page rebuilding on
   every rotation — a white flash and a websocket reconnect, four times a minute
   for nine hours.
3. A crossfade is **cheaper to encode than a cut**. The encoder runs with
   scene-cut detection off to hold a strict 2-second keyframe interval, so an
   instant full-frame change every 15 s is its worst case; a fade spreads that
   cost over ``FADE_MS`` of frames.

⚠ A related trap this avoids by construction: Chromium throttles timers and
rendering in **backgrounded tabs** — the same mechanism behind the documented
"transitions freeze and ``getComputedStyle`` lies" gotcha. Iframes inside one
foreground document are never backgrounded, so none of the three is throttled. A
three-tab implementation would have been.

⚠ This file is deliberately OUTSIDE the Tailwind-first standard, which binds
NiceGUI components styled with ``.classes()``. It emits a raw ``HTMLResponse``
document — the documented out-of-scope case, the same one ``/desk/live`` and the
EOD report sit in — so it carries its own ``<style>`` block. Its palette is still
read out of ``pages/options/theme`` rather than hand-picked.
"""
import html
import json

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

# How stale a panel may get before it reloads itself. This IS the worst-case
# window a dead panel can sit on camera, so it is chosen against that rather
# than against the cost: 15 minutes is ~36 reloads per panel over a nine-hour
# session, and a reload is a NiceGUI page build whose cache reads measure in
# single-digit milliseconds. Halving it would double that churn to shorten a
# repair window nobody watching would notice the difference of; doubling it
# risks half an hour of a visibly disconnected page on a public broadcast.
RELOAD_MS = 15 * 60 * 1000

# The overlay's standing-text slot, DELIBERATELY EMPTY by operator decision.
# ⚠ Worth revisiting rather than forgetting: this stream shows a paper book's
# positions and P&L publicly and permanently, and an unlabelled P&L reads as a
# track record. It lives here as a single constant precisely so switching it on
# is one line rather than a redesign — the slot itself is already in the bar,
# sized and placed, so turning it on cannot move anything else.
DISCLAIMER = ""

# The console palette — the same hexes ``/desk`` paints with, read from the theme
# rather than copied, so a `config/theme.toml` edit reaches the stream.
_C = theme.CONSOLE_COLORS

# What gets injected INTO each panel, not applied to this page: the app shell is
# navigation, and nobody watching a broadcast can click it. Measured on real
# 1920x1080 frames off the prod capture pipeline, the rail costs 68px of width
# and the header plus tab strip ~170px of height — and since a live stream
# cannot be scrolled, whatever that pushes past 1080px is seen by nobody, ever.
#
# ⚠ ``.q-page-container``'s padding is an INLINE style Quasar writes at runtime,
# so the override MUST be `!important`: an important author declaration beats a
# normal inline one, a normal author declaration does not. ``.compact-tabs`` is
# absent on ``/desk`` (a rail page has no tab strip) and present on the other
# two; a rule matching nothing costs nothing.
PANEL_CSS = """
header.q-header, aside.q-drawer { display: none !important; }
.q-page-container { padding-top: 0 !important; padding-left: 0 !important; }
.compact-tabs { display: none !important; }
"""


_CSS = """
*, *::before, *::after {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; height: 100%; overflow: hidden; }}
/* The ground for the FIRST paint only -- what is on camera in the moment before
   the opening panel has faded in. A handover never reaches it: the outgoing
   panel holds opaque underneath until the incoming one covers it. */
body {{ background: {cell}; color: {text};
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
.stage {{ position: relative; width: 100vw; height: 100vh; }}

/* Every panel is laid out at FULL SIZE, always -- see the module docstring.
   There is deliberately no `display` and no `visibility` here, and a test
   asserts their absence: both would collapse the iframe to zero layout size,
   which is how a charted page silently breaks on camera.

   OFF also holds this panel at FULL opacity for the whole handover and only
   then snaps to 0 -- a DELAY of {fade}ms, not a duration. By then the incoming
   panel is opaque and covering it. Fading both at once instead would composite
   0.5*in + 0.25*out + 0.25*body at the midpoint: stacked back to front, the
   body's weight is (1 - a_in) * (1 - a_out), which is non-zero for the whole
   fade when both sides share one easing curve. That is the near-black page
   blinking through every handover, ~2,160 times a session, on a broadcast. */
.panel {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%;
          border: 0; opacity: 0; transition: opacity 0ms linear {fade}ms; }}
/* ON is the only side that animates, and it must stack ABOVE the panel it is
   covering -- with the outgoing panel now holding opaque, painting the incoming
   one under it would hide the fade completely and turn the crossfade into a
   hard cut. It also makes the visible panel the one that hit-tests, so if
   anyone ever drives this screen with a mouse the clicks land where the eye is. */
.panel.on {{ opacity: 1; z-index: 1;
             transition: opacity {fade}ms ease-in-out 0ms; }}

.overlay {{ position: absolute; z-index: 2; left: 0; right: 0; bottom: 0;
            display: flex; align-items: center; gap: 16px;
            padding: 8px 18px; background: {cell}d9;
            border-top: 1px solid {line}40; }}
.page-label {{ font-size: 13px; letter-spacing: .22em; color: {label}; }}
/* The clock is pushed to the far end and the disclaimer takes the slack, so an
   empty disclaimer changes nothing about where the other two sit. */
.disclaimer {{ flex: 1; font-size: 11px; letter-spacing: .08em; color: {dim}; }}
.clock {{ font-size: 13px; letter-spacing: .12em; color: {text};
          font-variant-numeric: tabular-nums; }}
"""

_JS = """
/* PANELS and LABELS are index-aligned because both come from PAGES, server
   side: the label can never name a screen other than the one fading in. */
const PANELS = Array.from(document.querySelectorAll('.panel'));
const LABELS = {labels};
const RELOAD_MS = {reload};
let idx = 0;

function show(n) {{
  PANELS.forEach((p, i) => p.classList.toggle('on', i === n));
  document.getElementById('page-label').textContent = LABELS[n];
}}

/* A NiceGUI page is a LIVE CLIENT, and these three sit unattended for a whole
   session -- across at least one likely webgui restart (a promote, or
   Restart=on-failure after a blip). A client whose server went away renders a
   disconnected page: on camera, publicly, with nobody watching the server. So
   each panel periodically reloads itself.

   Per-panel timestamps, never one global tick. The showing panel is SKIPPED and
   picked up on a later rotation, so a single shared clock would permanently skip
   whichever panel happened to be up -- leaving the one that had been running
   longest as the only one that never refreshed. */
const SRCS = {srcs};
const LOADED = PANELS.map(() => Date.now());

function reloadStale() {{
  const now = Date.now();
  PANELS.forEach((p, i) => {{
    if (i === idx) return;                 /* on camera -- never reload */
    if (now - LOADED[i] < RELOAD_MS) return;
    LOADED[i] = now;
    /* Reassigning src rather than contentWindow.location.reload(): it raises no
       same-origin question, and it works even when the iframe is currently
       showing an error page -- which is precisely the case being repaired. */
    p.src = SRCS[i];
  }});
}}

/* THE PANELS ARE SAME-ORIGIN -- this page and all three are served by the
   webgui on one port -- so the parent can reach contentDocument and restyle
   them itself. That is what keeps this a wall-page concern: no change to
   main.py, to _layout, or to any of the three pages.

   ⚠ Bound to each panel's LOAD event, never run once at startup. Task 4b
   reassigns src every RELOAD_MS and a reload throws the injected stylesheet
   away with the old document; the load listener fires again on reassignment,
   so this one binding covers the first paint AND every refresh. A one-shot at
   startup looks identical for fifteen minutes and then quietly stops working,
   mid-broadcast, with nobody watching.

   The whole thing is wrapped: if the document is unreachable for any reason the
   panel must render WITH its chrome rather than throw and leave the rotation
   broken. Slightly ugly beats stopped. */
const STRIP_ID = 'wall-strip';
const PANEL_CSS = {panel_css};

function strip(p) {{
  try {{
    const d = p.contentDocument;
    if (!d || !d.head || d.getElementById(STRIP_ID)) return;
    const style = d.createElement('style');
    style.id = STRIP_ID;
    style.textContent = PANEL_CSS;
    d.head.appendChild(style);
  }} catch (e) {{
    /* unreachable document, or one caught mid-navigation -- leave the chrome */
  }}
}}

PANELS.forEach((p) => {{
  p.addEventListener('load', () => strip(p));
  strip(p);   /* in case a panel finished loading before this script ran */
}});

show(0);
setInterval(() => {{
  idx = (idx + 1) % PANELS.length;
  show(idx);
  /* Only AFTER the crossfade. At the instant of rotation the outgoing panel is
     still fully opaque underneath -- that is what makes the fade work -- so
     reloading it now would blank it on camera. */
  setTimeout(reloadStale, {fade});
}}, {dwell});

/* CENTRAL time, explicitly, and not the host's idea of local: this is the
   trading clock every window in this app is expressed in, and a public stream
   stamped in UTC is one nobody watching can use. Reading the zone from the
   browser would also make the stamp depend on how the capture box happens to be
   configured, which is not a thing anyone would think to check. */
function tick() {{
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString('en-US', {{
      timeZone: 'America/Chicago', hour12: false }});
}}
tick();
setInterval(tick, 1000);
"""


def document():
    """The wall page: styling, the three stacked dashboards, and the rotation.

    Nothing here is server-rendered but the shell. Every pixel a viewer sees is
    painted by the three real pages inside the iframes, on their own cadences.

    ``main`` is imported HERE rather than at module scope, and that is not
    style: ``main`` registers this route and therefore imports this module, so a
    module-scope import would be a cycle. ``main._serve_manual`` imports
    ``pages.manuals`` inside the handler for the same reason.
    """
    import main

    css = _CSS.format(cell=_C["cell"], text=_C["text"], line=_C["line"],
                      label=_C["label"], dim=_C["dim"], fade=FADE_MS)
    js = _JS.format(labels=json.dumps([p["label"] for p in PAGES]),
                    srcs=json.dumps([p["path"] for p in PAGES]),
                    dwell=DWELL_MS, fade=FADE_MS, reload=RELOAD_MS,
                    panel_css=json.dumps(PANEL_CSS))
    frames = "\n".join(
        '  <iframe class="panel" src="{path}" title="{label}"></iframe>'.format(
            path=p["path"], label=p["label"]) for p in PAGES)
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wall</title>
{fonts}
<style>{brand}</style>
<style>{css}</style>
</head>
<body>
<div class="stage">
{frames}
  <div class="overlay">
    {lockup}
    <div class="page-label" id="page-label"></div>
    <div class="disclaimer" id="disclaimer">{disclaimer}</div>
    <div class="clock" id="clock">&mdash;</div>
  </div>
</div>
<script>
{js}
</script>
</body>
</html>
""".format(css=css, frames=frames, js=js,
           fonts=theme.BRAND_FONT_HEAD_HTML, brand=theme.BRAND_CSS,
           # The app's OWN wordmark, not a second one: a rename in
           # config/theme.toml has to reach the stream, and two hand-written
           # copies of a two-tone gradient lockup would drift the first time
           # one of them was touched. ``mark=False`` because the logo image is
           # the drawer's pin control on the real pages and has no job here.
           lockup=main.brand_lockup_html(mark=False),
           disclaimer=html.escape(DISCLAIMER))
