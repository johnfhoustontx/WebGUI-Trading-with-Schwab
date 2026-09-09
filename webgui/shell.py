"""The page-to-shell seam: the handful of shell services a PAGE may call.

⚠ This module exists so that a page never has to ``import main``. main.py's
module body registers every ``@_page`` route, so importing it from a second
NiceGUI process -- the public live screens on ``live_main.py`` -- would publish
``/terminate`` and ``/settings`` to the internet while looking correct. Keeping
the seam here means the public process is STRUCTURALLY incapable of holding the
app's route table, rather than incapable by inspection.

Both entrypoints provide it: ``main.py`` populates the slots from ``_layout``;
``live_main.py`` leaves them empty, and every function here is already a no-op
without a mounted header. Not to be confused with ``pages/trade_shell.py``,
which is the Trade section's shared page body.
"""
from nicegui import ui

from pages.ui_guard import guard


# ── which ORIGIN this process is ─────────────────────────────────────────────
# A process is either the private app (``main.py``) or the public live site
# (``live_main.py``) — never both — so this is process state, not per-request
# state, and it is set once at startup by the entrypoint that knows.
#
# It lives HERE, in the seam, because it is the same fact both entrypoints
# already differ on, and because ``shell.py`` is the one module every page may
# import and the public entrypoint may too. ⚠ It must stay a LEAF: the route
# map is PASSED IN by ``live_main`` rather than read from ``live_screens``, so
# this module keeps importing nothing but ``nicegui`` and ``pages.ui_guard``
# (``test_shell_seam.test_the_shell_stays_a_leaf_module``).
_ORIGIN: dict = {"public": False, "routes": {}}


def publish(routes: dict | None = None) -> None:
    """Declare this process the PUBLIC origin. Called once by ``live_main``.

    ``routes`` is ``{private route: the route THIS origin serves it at}`` —
    ``live_screens.PUBLIC_ROUTES``, passed IN rather than imported, so this
    module stays a leaf."""
    _ORIGIN["public"] = True
    _ORIGIN["routes"] = dict(routes or {})


def unpublish() -> None:
    """Undo :func:`publish` (test helper; nothing in the app calls it)."""
    _ORIGIN["public"] = False
    _ORIGIN["routes"] = {}


def is_public() -> bool:
    """True when this process serves the unauthenticated live screens."""
    return _ORIGIN["public"]


def may_enqueue() -> bool:
    """Whether a page built in THIS process may put a command on a ``cmd:`` stream.

    Every such command spends something the owner pays for — a Schwab
    option-chain fetch against a budget already running 68-76k/day, a paid
    Claude call, a sentiment refresh that pulls eleven sector chains plus their
    histories. On the public origin the page is served to anyone with the URL,
    so a control that reaches one is an open tap.

    ``bus_client.request`` REFUSES an enqueue in that process regardless; that
    is the backstop, and this is the design — a button that cannot work must not
    be drawn. Both, not either. (Relying on the backstop alone also costs a full
    traceback in the journal per click, since ``ui_guard.guard`` re-raises
    anything that is not the deleted-slot error.)

    Named after ``pages/options/gamma.py``'s ``may_enqueue``, which asks the
    same question from its own pins: every page resolves it once into a local
    ``_may_enqueue``, and one source-level test walks the published modules for
    command sites and checks that local. See ``tests/test_live_commands.py``."""
    return not _ORIGIN["public"]


def route_for(route: str):
    """Where ``route`` lives in THIS process, or ``None`` if it lives nowhere.

    A page names the route the PRIVATE app serves — the address it has always
    known, and the one the breadcrumb, the nav and every other reader use. The
    public origin publishes most of those pages at a DIFFERENT path
    (``/options/matrix`` at ``/opportunity``) and some of them nowhere at all,
    so a page asks rather than assumes.

    Unpublished process → the identity, so the private app is unchanged.
    Published process → the mapped route, or ``None`` for a page this origin
    does not serve. ``None`` is deliberately not "fall back to the private
    path": that path 404s here, and linking to the private HOST would be worse
    still — the public site must never advertise it."""
    if not _ORIGIN["public"]:
        return route
    return _ORIGIN["routes"].get(route)


def can_navigate(route: str) -> bool:
    """Whether a control pointing at ``route`` can work in this process.

    Ask BEFORE drawing the affordance — the ``cursor-pointer``, the hover wash,
    the click handler. A row dressed as a link that leads nowhere is the same
    defect as a button that cannot work: it reads as broken rather than as
    absent."""
    return route_for(route) is not None


def navigate_to(route: str, new_tab: bool = False) -> None:
    """Navigate to ``route``'s address in this process; a no-op if it has none.

    The no-op is the backstop under :func:`can_navigate`, in the same
    both-not-either shape as :func:`may_enqueue` and ``bus_client.request``:
    the control is not drawn, AND the handler declines.

    ⚠ EVERY internal navigation in a page goes through here, not only the ones
    a published screen draws today — ``tests/test_live_navigation.py``
    enumerates it. In the private app this is exactly ``ui.navigate.to``, so
    the uniformity is free; a route carrying a query string (``/options/
    analyze?v=3``) is not in any map and so resolves to itself privately and to
    nothing publicly, which is the right answer for both."""
    target = route_for(route)
    if target is None:
        return
    ui.navigate.to(target, new_tab=new_tab)


# ── Page-level CSS both entrypoints inject ───────────────────────────────────
# These are not nav chrome. They style widgets a PAGE mounts, which is why they
# live here: `live_main.py` renders the same page modules and cannot import
# `main`. Both were in main.py until 2026-09-07, and the published /opportunity,
# /flow and /net-premium screens rendered without them.
#
# ⚠ NOT the whole of main's `_NAV_CSS`. The rail, the top tab strip
# (.compact-tabs), the page-help tooltips, the market pill and the brand lockup
# are all chrome the public process does not mount, and moving them here would
# ship rules for elements that do not exist.

# Global table chrome (app-wide standard): EVERY data table gets a fixed (sticky)
# header over a bounded, scrolling body, so the column headers stay visible as a long
# table scrolls. Injected once per page by each entrypoint. Per-page table CSS
# (.paper-table / .captured-table / .driver-table) may still set its own max-height —
# its more-specific selector + later injection win over this baseline.
TABLE_CSS = """
.q-table__middle { max-height: 65vh; }
/* Deep Slate table header: sticky, dark #141a30 inset, with uppercase faint
   column labels (10.5px / 600 / .06em) — the trading-terminal look. */
.q-table thead tr th {
  position: sticky; top: 0; z-index: 1; background: #141a30;
  font-size: 10.5px; font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; color: #6d76a0;
}
/* Faint row dividers (Deep Slate) between body rows. */
.q-table tbody tr:not(:last-child) td { border-bottom: 1px solid rgba(255,255,255,.04); }
"""

# Subtab row (a page's own view tabs, e.g. Gamma GEX/Charm/DEX/Vanna/Flow/Term)
# — the same pill shape one size smaller, on a fainter inset container so the
# hierarchy under the main strip reads clearly.
#
# A page mounts this row ITSELF (into `subtab_slot()` when the shell offers one,
# inline when it does not — see gamma.py), so it follows the page, not the shell:
# the public /net-premium screen builds its group picker with this class and had
# been drawing it as stock Quasar tabs.
SUBTAB_CSS = """
.compact-subtabs {
  background: #0f1428; border-radius: 10px; padding: 3px 4px; min-height: 0;
}
.compact-subtabs .q-tab {
  min-height: 26px; padding: 0 11px; margin-right: 2px;
  border-radius: 7px; background: transparent; color: #8891ab;
}
.compact-subtabs .q-tab--active { background: rgba(255,255,255,.08); color: #eef1f6; }
.compact-subtabs .q-tab__indicator { display: none; }
.compact-subtabs .q-tab__label { font-size: 12px; }
"""

# ── a dashboard panel that has run out of width ──────────────────────────────
# ⚠ THIS REVERSES A POSITION `desk.py` HELD FROM 2026-08-20, AND THE POSITION IT
# REVERSES WAS RIGHT. That note read: "`overflow-x-auto` is deliberately NOT the
# fallback: a dashboard you scroll sideways to read defeats the page's purpose."
# It does. But refusing the scroll did not PREVENT sideways scrolling — it only
# decided WHERE it happened. A Desk panel is a stack of CSS grids sharing one
# `grid-template-columns` of `minmax()` tracks, and a grid never shrinks a track
# below its floor, so a panel given less width than its floors add up to does
# not reflow: the rows paint out through the card, the page's padding chain
# absorbs a little, and then the DOCUMENT scrolls sideways — carrying the panel
# heading AND the row's identity column off screen with everything else. The
# objection was against losing your place, and the document scroll loses more of
# it than a panel scroll ever could. What it actually asked for is this: the
# scroll CONTAINED at the panel, with the identity column pinned, so the heading
# and the symbol stay while the numbers move under them.
#
# Four decisions, each measured in a real browser rather than reasoned about:
#
# 1. NO `min-width` HERE. The sketch this came from said `min-width: max-content`
#    on every child. `max-content` on a grid resolves its `fr` tracks to their
#    widest CELL, so one long rationale string would widen a panel far past the
#    floors and make it scroll when it did not need to — and it would hit the
#    "waiting for the options service" placeholder, which is a child of the same
#    container. The width is per-panel data: `pages/panel_scroll.grid_min_width_px`
#    derives it from the panel's own grid string, and it belongs on the grid
#    elements as a Tailwind `min-w-[...]`.
#
# 2. THE SCROLL CONTAINER IS THE ROWS' CONTAINER, NOT THE CARD. The card also
#    holds the panel heading, and a heading that scrolls away is half of what
#    this exists to prevent. The head ROW must be inside it, though — head and
#    data rows share one track list, so anything that scrolled one without the
#    other would slide every label off its column.
#
# 3. THE PIN IS A `::after`, NOT A BACKGROUND ON THE CELL. A background on the
#    cell covers only the CELL, and these rows are `items-center` with cells of
#    unequal height — a one-line symbol beside a two-line stack or a 34px
#    structure map. Measured: the scrolling numbers ran through the gap above and
#    below the pinned symbol, which reads as corruption, exactly as feared. The
#    pseudo takes `align-self: stretch` instead, so the backdrop is the full row
#    height while the real cell keeps its own alignment — including the Positions
#    chip's `self-start`, which a `stretch` on the cell would have overridden and
#    stretched the chip itself. ⚠ `grid-area: 1 / 1` on BOTH: a definitely-placed
#    pseudo occupies that cell, and without the same placement on the first child
#    every real cell auto-places one column to the right and the last one wraps
#    to a second row (measured — the row grew from 57px to 73px).
#
# 4. THE COLOURS ARE FLAT, AND THAT IS A COMPROMISE. The panel is a 160deg
#    gradient (`theme.CONSOLE_CARD`, both stops at 95% over the page wash), so no
#    flat colour matches at every row. Sampled off a rendered panel, the ground
#    behind the first column runs #0e161d at the top to #0a1117 at the foot;
#    #0c131a is its middle, and the residual mismatch measured 0-4 levels per
#    channel, invisible against the ~#0d151e row rule the panel already draws
#    flat across the same gradient. #121920 is that ground under the row's own
#    hover wash (`_C['line']` at 6%), measured 1-2 levels off the real hovered
#    row: without it the pinned column stays dark while the rest of the row
#    lights, which reads as the pin not belonging to the row.
#
# ⚠ `.cursor-pointer` gates the hover, and it is the right predicate rather than
# a convenient one: in `desk.py` `_ROW` is `_ROW_STATIC` plus that class, and the
# hover wash rides the identical condition (`can_open`), so a row that is a link
# and a row that lights up are the same set by construction. On the public origin
# every Positions row is static, and an ungated rule would light the pin alone on
# rows whose remaining nine cells do nothing.
#
# ⚠ TWO THINGS THE ARRIVAL GLOW (`desk.DESK_NEON_CSS`) LOSES, both accepted.
# (a) It animates the ROW's background, and the pin's opaque backdrop sits over
# its first column; matching it would mean duplicating those keyframes, which
# belong to that page and not to this seam. (b) `overflow-x: auto` computes
# `overflow-y` to `auto` as well, so the container CLIPS painted overflow —
# measured, the glow's outer `0 0 18px -2px` shadow bleeds ~18px above an
# unclipped first row and is cut flat at the container's edge inside one. Only
# the first and last rows can touch that edge, and it costs a halo, not a row.
#
# -8px is `desk._GAP` / `panel_scroll.COL_GAP_PX` — the backdrop reaches into the
# column gap, or an 8px strip of moving digits shows beside the pinned column.
# Pinned by `test_the_pin_backdrop_covers_the_column_gap`, since a literal here
# cannot follow that constant on its own.
PANEL_SCROLL_CSS = """
.ns-panel-scroll { overflow-x: auto; overscroll-behavior-x: contain; }
/* The identity cell rides above its own backdrop, which rides above the cells
   scrolling under both. */
.ns-panel-row > :first-child { grid-area: 1 / 1; position: sticky; left: 0; z-index: 2; }
.ns-panel-row::after {
  content: ""; grid-area: 1 / 1; align-self: stretch;
  position: sticky; left: 0; z-index: 1;
  margin-right: -8px; background: #0c131a;
}
.ns-panel-row.cursor-pointer:hover::after { background: #121920; }
"""


def play_alert(sound: str, volume: float) -> None:
    """Play a bundled alert WAV in the connected browser at the given volume."""
    sound = sound if sound in ("chime", "bell", "ping") else "chime"
    vol = max(0.0, min(1.0, float(volume if volume is not None else 0.6)))
    ui.run_javascript(
        f"(() => {{ const a = document.getElementById('alert-audio'); if (!a) return; "
        f"a.src = '/static/sounds/{sound}.wav'; a.volume = {vol}; "
        f"a.play().catch(() => {{}}); }})()")



# Breadcrumb crumb styling — the trailing crumb is the thing you are looking at,
# everything before it is context. Named because the LEAF swaps them at runtime.
_CRUMB_LEAF = "text-[13px] text-[#e6ecf9] font-semibold"
_CRUMB_CONTEXT = "text-[13px] text-[#5d6a88]"

# The optional FOURTH crumb: a page's own active view (Dealer Positioning ›
# Gamma, Simulator › Replay …). Single-user module state, rebuilt per layout like
# the badge refs. "parent" is the last trail crumb, which has to be demoted to
# context when a view is named after it.
_breadcrumb_leaf: dict = {}


def _view_name(value):
    """A subtab's name from whatever a ``ui.tabs`` element holds.

    Pages build their tabs either by NAME (``ui.tab("Replay")`` → the value is
    the string) or by ELEMENT (Rescue passes the tab object to ``ui.tab_panels``,
    so the value can be the element). Reading the element's ``name`` prop covers
    both without every call site having to know which it is."""
    if value is None:
        return ""
    props = getattr(value, "_props", None)
    if isinstance(props, dict) and props.get("name"):
        return str(props["name"])
    return str(value)


def set_breadcrumb_leaf(label, refs=None) -> None:
    """Show ``label`` as the last breadcrumb crumb, or hide the leaf when falsy.

    ``refs`` targets a SPECIFIC header's elements instead of whichever page built
    the layout most recently. That matters because ``_breadcrumb_leaf`` is
    module-level, and unlike the badge refs — which every client rewrites with the
    same numbers, so the sharing is invisible — each page writes a DIFFERENT view
    name here. With two tabs open the second page's build reassigns the module
    state and the first tab's tab-change handler then writes into the second
    tab's header: caught in prod, where the promote script opens a tab on the
    Scanner and Dealer Positioning's header went on to read "› 0-DTE".

    Never raises and is a no-op without a mounted header, so a page may call it
    unconditionally."""
    refs = _breadcrumb_leaf if refs is None else refs
    if not refs.get("label"):
        return
    text = str(label or "").strip()
    refs["label"].text = text
    refs["label"].set_visibility(bool(text))
    refs["caret"].set_visibility(bool(text))
    parent = refs.get("parent")
    if parent is not None:
        # The page name stops being the leaf the moment a view is named after it.
        parent.classes(remove=f"{_CRUMB_LEAF} {_CRUMB_CONTEXT}",
                       add=_CRUMB_CONTEXT if text else _CRUMB_LEAF)


def bind_breadcrumb_leaf(tabs, labeller=None, initial=None) -> None:
    """Track a page's own view tabs in the breadcrumb: ``… › Page › View``.

    A page's subtabs ARE a level of the hierarchy — Dealer Positioning read the
    same in the header whether you were on Gamma or on Net Prem — but they switch
    CLIENT-side without rebuilding the layout, so the crumb has to ride the same
    event that switches the view.

    Registers an ADDITIONAL ``on_value_change`` handler (NiceGUI appends them), so
    a page's existing handler is untouched, and paints the initial value at build
    time so the first render is already correct rather than correcting itself on
    the first click. ``labeller`` maps the raw tab value to what the header should
    read — Gamma needs it, since its "GEX" tab is displayed as "Gamma".

    ``initial`` is required by every page that builds a bare ``ui.tabs()`` and
    names its default on the ``ui.tab_panels`` instead — four of the five do. That
    default reaches the tabs element through NiceGUI's BINDING, which propagates
    on a later cycle, so ``tabs.value`` is still None while the page is being
    built and the crumb would sit blank until the first click.

    The header elements are CAPTURED here rather than looked up when the handler
    fires: ``_breadcrumb_leaf`` is module-level, so a second tab's page build
    reassigns it and this page's handler would otherwise write its view name into
    the other tab's header (see ``set_breadcrumb_leaf``)."""
    fmt = labeller or _view_name
    refs = dict(_breadcrumb_leaf)
    set_breadcrumb_leaf(fmt(tabs.value if tabs.value is not None else initial), refs)

    @guard
    def _sync(e) -> None:
        set_breadcrumb_leaf(fmt(e.value), refs)

    tabs.on_value_change(_sync)


# Per-page-build slot directly under the top tab strip, where a page can mount
# its own view SUBTABS (see _layout; e.g. the Gamma GEX/Charm/... row). Rebuilt
# on every _layout; None on pages without a strip.
_SUBTAB_SLOT: dict = {"el": None}


def subtab_slot():
    """The container under the main tab strip for a page's view subtabs (or None)."""
    return _SUBTAB_SLOT["el"]


# --- the screenshot session's chrome suppression -----------------------------
# Set by tools/capture_gallery_shots.py's bootstrap redirect, alongside the
# session cookie. Cookies ignore PORT, so the bootstrap server on its ephemeral
# 127.0.0.1 port sets one the app on :8500 receives -- the same mechanism that
# delivers the session itself.
CAPTURE_COOKIE = "ns_capture"

# ⚠ SUPPRESSES A REAL SIGNAL, DELIBERATELY AND NARROWLY.
#
# `#popup.nicegui-error-popup` is NiceGUI's own "Connection lost. Trying to
# reconnect..." banner (templates/index.html), and during a capture it is not
# lying -- the client really does conclude the socket died. The cause is
# --virtual-time-budget: Chrome races the CLIENT's clock through
# socket.io's ping_interval (4s) + ping_timeout (2s) in milliseconds of real
# time, while the SERVER still pings on the wall clock. The busiest pages keep
# scheduling work, so virtual time runs furthest on exactly the screens whose
# tiles matter most.
#
# It fires AFTER the page has painted its data -- verified by reading the
# captures, which carry live spot, premium and leaderboard values under the
# banner -- so what this hides is a cosmetic artifact of how the shutter works,
# not a page that failed to load. It hides ONE element and nothing else, so a
# genuinely empty render still photographs as empty.
#
# ⚠ NOT injected by live_main, and it must not be: on the public origin any
# visitor could set the cookie and suppress their own disconnect warning. The
# capture only ever drives the private app on loopback.
CAPTURE_CHROME_CSS = """
#popup.nicegui-error-popup { display: none !important; }
"""


def capture_chrome_css(cookies):
    """CSS for a screenshot session, or ``None`` for a real visitor. PURE.

    Takes the cookie mapping rather than a request so it is testable without
    NiceGUI, and matches the value exactly -- a cookie merely being PRESENT is
    not the contract, so a stray empty `ns_capture=` does not suppress anything.
    """
    if not cookies:
        return None
    return CAPTURE_CHROME_CSS if cookies.get(CAPTURE_COOKIE) == "1" else None
