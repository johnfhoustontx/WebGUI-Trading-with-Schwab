"""The PUBLIC read-only screens: a second NiceGUI process on its own origin.

Serves the fourteen screens in ``live_screens.SCREENS`` at ``live.neuralstrike.co``,
unauthenticated, to anyone. It renders the REAL page modules — the same ones the
app renders — so a published screen cannot drift from the private one.
``webgui/wall.py`` makes the same argument at length.

⚠ THIS MODULE MUST NEVER ``import main``. main.py's body registers every
``@_page`` route, so importing it here would publish ``/terminate`` (Stop All
Services) and ``/settings`` to the internet, silently, while looking entirely
correct. The seam the pages need lives in ``shell.py``; every page reaches the
shell through it. ``tests/test_live_main.py`` pins the absence twice — at source
level here, and by running this file ALONE in a fresh interpreter and asserting
both that ``main`` never entered ``sys.modules`` and that the only routes served
are the published fourteen. The second is the one that can see a TRANSITIVE
import, which is how such a thing would actually arrive.

Read-only is enforced at four layers, of which this file installs three:

1. a Redis ACL user with read commands only (from ``REDIS_LIVE_URL``) — the
   STRUCTURAL one, enforced by the server rather than by this process. ⚠ It is
   the one layer that can be ABSENT while everything looks correct: unset, the
   Bus falls back to the stack's ordinary full read/write credential. Nothing
   in the generated unit sets the variable — the unit loads a FILE, and a
   forgotten line, an empty value or a typo all land in the same place. So
   ``resolve_acl_url`` warns and ``require_acl_url`` refuses to serve prod
   without it;
2. ``bus_client.set_read_only(True)`` — refuses every command enqueue, which on
   these pages covers ``gamma_analyze`` and ``gamma_explain`` (PAID Claude
   calls) and ``gamma_refresh`` / sentiment ``refresh`` (Schwab fetches against
   a budget already running 68-76k/day);
3. ``app_settings.freeze(...)`` — pins each screen's published state AND stops
   this process reading or racing the app's single-user settings.json;
4. (structural) no rail, no Settings, no Terminate, no Sign-out, no auth
   middleware — those routes do not exist in this process at all.

⚠ Order matters: 1-3 are installed BEFORE any page module is imported, so a page
cannot capture an unrefused bus, or the owner's real settings, at import time.
That is what the ``# noqa: E402`` imports below are buying, and the reason they
are not tidied up into one block at the top.

⚠ NEVER call ``main``'s ``sync_ticker_setting`` /
``sync_captured_autoclose_setting`` / ``sync_manual_paper_lifecycle_setting``,
and do not grow a copy of them here "for symmetry". They read like harmless
``app_settings.get`` readers and are not: each then does
``bus_client.request(...)``, so they are cross-process WRITERS to Tier-2
services. Against a frozen store they would read the PINNED value and re-assert
it — ``ticker_enabled`` defaults True, so this process would re-enable the
~20-minute paid Claude verdict the owner may have deliberately switched off.
"""
import importlib
import logging
import os
import pathlib
import sys
import urllib.parse

_HERE = pathlib.Path(__file__).resolve().parent
for _p in (str(_HERE.parent), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app_settings                                   # noqa: E402
import bus_client                                     # noqa: E402
import live_screens                                   # noqa: E402

from repo_paths import (ENV_NAME, LIVE_HOST,          # noqa: E402
                        NICEGUI_LIVE_PORT, REDIS_DB)

log = logging.getLogger("webgui.live")

# The environment variable carrying the read-only Redis ACL user. Named once so
# every message below spells it the same way the operator has to type it.
ACL_URL_VAR = "REDIS_LIVE_URL"


def _db_index(url):
    """The Redis DB index a connection URL selects, or ``None`` if it names none.

    ``redis://user:pw@host:6379/1`` selects db 1. The index is the URL's PATH,
    which is precisely why it is worth reading back out: ``REDIS_LIVE_URL``
    carries it, so it BYPASSES ``repo_paths.REDIS_DB`` -- the one value that
    keeps dev off prod's data. A URL copied from prod's env file into dev's
    points dev's public process at **prod db 0**, and every published screen
    then renders prod's real book while looking like dev.

    Returns ``None`` rather than 0 for a URL with no path, because "unstated"
    and "explicitly db 0" are different claims and only one of them is worth
    warning about.
    """
    try:
        path = urllib.parse.urlparse(url).path.strip("/")
    except ValueError:                      # a malformed URL is the Bus's problem
        return None
    return int(path) if path.isdigit() else None


def resolve_acl_url(environ=None):
    """``REDIS_LIVE_URL`` from the environment, with the fallback made LOUD.

    Unset, this returns ``None`` and ``Bus`` falls back to ``MEMURAI_URL`` --
    **the same full read/write credential every service in the stack holds**.
    That is layer 1 of the four this file's docstring lists, and it is the only
    one the server rather than this process enforces, so losing it silently is
    the whole four-layer design quietly becoming three.

    ⚠ This function only WARNS. Refusing is :func:`require_acl_url`'s job, and
    it is deliberately a separate step: importing this module must stay possible
    on a box with no ACL user (the test suite does it, and so does anyone
    reading the route table), while SERVING without one must not.

    It also warns when the URL selects a Redis DB other than this checkout's --
    see :func:`_db_index`.
    """
    environ = os.environ if environ is None else environ
    url = (environ.get(ACL_URL_VAR) or "").strip() or None
    if url is None:
        log.warning(
            "%s is not set: the PUBLIC read-only screens will connect to Redis "
            "as the stack's ordinary full read/write user. The Redis ACL is the "
            "only read-only layer the SERVER enforces; without it the four-layer "
            "design is three, all of them in-process.", ACL_URL_VAR)
        return None
    db = _db_index(url)
    if db is not None and db != REDIS_DB:
        log.warning(
            "%s selects Redis db %s but this is the %r checkout, which uses db "
            "%s. The public screens will publish ANOTHER environment's data.",
            ACL_URL_VAR, db, ENV_NAME, REDIS_DB)
    return url


def require_acl_url(url, env_name=ENV_NAME):
    """Refuse to SERVE prod's public origin without the read-only credential.

    **Prod refuses; anything else warns and continues.** The asymmetry is the
    whole decision, and both halves cost something:

    * In **prod** this process is on the public internet with no login. Starting
      it holding a credential that can ``SET`` every cache key and ``XADD`` every
      command stream is exactly the failure this repo keeps paying for -- a
      control that reads as configured and does not exist. A refusal is loud,
      lands in ``systemctl --user --failed`` next to a journal line naming the
      variable, and is recoverable in the seconds it takes to add one line to
      ``.env.live``. The cost is that the public screens are down until then;
      the alternative cost is that they are up and unprotected, which nothing
      reports.
    * In **dev** the origin is not fronted by Caddy at all -- the Caddyfile
      generator refuses to run outside prod -- so there is no public exposure to
      protect, and a dev box may legitimately have no ACL user provisioned.
      Refusing there would block the standing rule that work is *verified
      running in dev* before it is promoted, which buys nothing.

    ⚠ Called from the ``__main__`` block, NOT from the module body. Import must
    stay possible without the credential (see :func:`resolve_acl_url`).
    """
    if url:
        return
    if env_name == "prod":
        raise SystemExit(
            f"refusing to serve the PUBLIC live screens without {ACL_URL_VAR}.\n"
            f"Unset, this process connects to Redis as the stack's ordinary "
            f"full read/write user -- on an origin with no login.\n"
            f"Put the read-only ACL user's URL in {ACL_URL_VAR} in this "
            f"checkout's .env.live and restart.")
    log.warning(
        "serving the public live screens WITHOUT %s. Allowed because this is "
        "the %r checkout, whose live origin is not fronted by the edge; prod "
        "refuses.", ACL_URL_VAR, env_name)


# ── The refusals, installed before any page module is imported ───────────────
# Layer 1: the read-only Redis ACL user. Unset -> the ordinary URL, WARNED about
# here and REFUSED outright before serving in prod (require_acl_url, below).
_ACL_URL = resolve_acl_url()
bus_client.set_url(_ACL_URL)
bus_client.set_read_only(True)
app_settings.freeze(live_screens.SETTINGS_PINS)

import shell                                          # noqa: E402
from nicegui import app as nicegui_app                # noqa: E402
from nicegui import ui                                # noqa: E402
from pages.options import theme                       # noqa: E402

# Layer 5, and the only one a PAGE can act on: this process says which origin it
# is, so a page can decline to draw a control that cannot work here. The pages
# read it through ``shell.may_enqueue()``; ``bus_client``'s refusal above stays
# the backstop. Both, not either — relying on the refusal alone leaves live
# buttons that raise a full traceback into journald on every anonymous click.
#
# ⚠ Called BEFORE any page module is imported, for the same reason as 1-3.
#
# It also hands over the private→published ROUTE MAP, so a page's click-through
# lands where that page actually lives here (``/options/matrix`` is published at
# ``/opportunity``) and draws no link at all for a page this origin does not
# serve. The map is DERIVED from the screen table — see
# ``live_screens._public_routes`` — and passed IN rather than imported by
# ``shell``, which must stay a leaf module.
shell.publish(live_screens.PUBLIC_ROUTES)

_STATIC_DIR = shell._STATIC_DIR

# ── the ONE non-page route this process serves ───────────────────────────────
# ⚠ A DELIBERATE ADDITION TO "the fourteen routes and nothing else", and it is
# published to the internet like everything else here.
#
# The header's brand mark is ``[brand].mark`` — a URL under ``/static`` — and
# this process serves its own ASGI app. Measured on prod before this line
# existed: ``127.0.0.1:8500/static/img/neuralstrike-mark.svg`` answered 200 (the
# app mounts it) and ``:8501`` answered 404, so a published header would have
# drawn a broken-image icon while the markup read as entirely correct.
#
# ⚠ Mounting a DIRECTORY publishes every file in it, now and later. This tree is
# seven files — three alert WAVs and four brand images — with no config, no
# credentials and no data under it; ``webgui/data`` is a different tree and is
# NOT mounted, which is also why ``/voice`` does not exist in this process.
# ``tests/test_live_main.py`` pins both halves: that this is the only route
# beyond the published fourteen, and that nothing but bundled assets lives here.
#
# Guarded on ``is_dir()`` exactly as ``main.py``'s mount is: a checkout without
# the directory must degrade to a wordmark, not fail to start over chrome.
if _STATIC_DIR.is_dir():
    nicegui_app.add_static_files("/static", str(_STATIC_DIR))
else:                                   # pragma: no cover - a broken checkout
    log.warning("no static directory at %s: the public header will render the "
                "wordmark without its mark", _STATIC_DIR)

# The private app's content container, minus its ``pb-10``. That padding exists
# solely to clear the fixed market-summary marquee ``_layout`` mounts, and this
# process mounts no marquee.
#
# ⚠ Deliberately NOT ``theme.PAGE``. That token is a rounded, bordered, padded
# radial-gradient panel, and ``_layout`` does not apply it — every one of the
# fourteen pages already supplies its own top-level wrap and background
# (``CONSOLE_PAGE`` on the Desk, ``RT_VOID_BG`` on the four rotation screens,
# ``macro-board`` on the Macro Board, ``calc-v2 PAGE`` on the Opportunity
# Board). Wrapping them again would draw a second frame around each, and a
# navy gradient behind the void-black ones. A neutral container is what "cannot
# drift from the private page" actually means here.
_CONTENT = "w-full p-4 gap-3"

# ── the public header ────────────────────────────────────────────────────────
# The brand reached these screens through the browser TAB TITLE alone, which is
# invisible on the YouTube wall stream and on a kiosk — so a stranger opening a
# published screen saw a dense trading board with nothing saying whose it is.
#
# It is the app header's LEFT half and nothing else: the lockup, a hairline, the
# screen's name. ⚠ NO NAVIGATION OF ANY KIND, and not because there is nothing
# worth linking to — Settings, Terminate, Sign out and the whole rail DO NOT
# EXIST in this process, and that origin separation IS the security control. A
# link to a route this origin does not serve reads as broken; one pointing at
# the private host would advertise it. There is nothing here to click.
_HEADER = ("live-header w-full items-center gap-3 no-wrap px-4 pb-2 "
           "border-b border-white/[0.06]")

# The app's own "the thing you are looking at" crumb style, taken from the seam
# rather than restated, so the public screen name and the private breadcrumb
# leaf cannot drift apart.
_SCREEN_NAME = shell._CRUMB_LEAF

# The ONE `ui.add_css` this entrypoint owns, and it is the documented escape
# hatch: `.brand-mark` lives inside a raw HTML string (`brand_lockup_html`), so
# no `.classes()` can reach it.
#
# The app sizes that mark 44px, which is right for a header bar that also holds
# a breadcrumb and a market pill. These screens are dense by design and this
# header is pure identity, so it must not cost a row of data: 32px keeps the SVG
# legible across a room while holding the whole band to ~40px — about 4% of a
# 1080p wall display, and it is the page's first element rather than a fixed
# bar, so nothing is pushed off the bottom that was not already there.
# (Measured in a real browser at this size: band 41px, mark 32x32.)
#
# ⚠ The app's `.brand-mark` hairline (`_NAV_CSS`, "so the logo doesn't float on
# the bar") is deliberately NOT copied. It belongs to a header BAR with its own
# ground; here the mark sits on whatever the page paints, and a 1px rounded box
# around a transparent chevron would read as a plate that is not there.
LIVE_HEADER_CSS = """
.live-header .brand-mark { width: 32px; height: 32px; border-radius: 9px; }
"""


def _header(screen) -> None:
    """The brand lockup, a hairline, and the name of the screen you are on."""
    with ui.row().classes(_HEADER):
        # mark=True — unlike `main._layout`, which passes mark=False because
        # there the logo is a real button (the menu's pin control) built beside
        # the wordmark. This origin has no menu, so the mark rides in the string.
        ui.html(shell.brand_lockup_html())
        ui.element("div").classes(
            "w-px h-[22px] bg-white/[0.09] mx-1 flex-none")
        ui.label(screen.title).classes(_SCREEN_NAME)


def _render(screen) -> None:
    """Import and render one screen inside the minimal public shell."""
    # The PAGE-level CSS ``_layout`` injects, in ``_layout``'s own order. Not nav
    # chrome -- all three style widgets the page itself mounts, so without them
    # the published /opportunity and /flow tables lose their sticky Deep Slate
    # headers, /net-premium's group picker draws as stock Quasar tabs, and the
    # published /desk's panels clip their rows and then scroll the whole document
    # sideways. They live in ``shell`` precisely because this process cannot
    # import ``main``.
    ui.add_css(shell.TABLE_CSS)
    ui.add_css(shell.SUBTAB_CSS)
    ui.add_css(shell.PANEL_SCROLL_CSS)
    # The app-wide text presentation ``_layout`` injects. Not nav chrome: it is
    # the font and the text-category sizes from ``config/theme.toml``, and
    # without them a published page renders in a different typeface from the
    # private one it is supposed to mirror. ``add_head_html``/``add_css`` during
    # a page build are client-scoped, so this is per-page, as in the app.
    if theme.FONT_HEAD_HTML:
        ui.add_head_html(theme.FONT_HEAD_HTML)
    if theme.TYPOGRAPHY_CSS:
        ui.add_css(theme.TYPOGRAPHY_CSS)
    if theme.MENU_ACCENT:
        # [menu].accent -> the Quasar primary, which reaches only Quasar-coloured
        # controls (switches, sliders, color=primary buttons). Nothing nav-related
        # rides it, so it is safe on a page with no rail.
        ui.colors(primary=theme.MENU_ACCENT)
    # The brand identity, in ``main._layout``'s own order. The wordmark is two
    # gradients clipped to text, which the bundled Tailwind JIT will not emit —
    # so without BRAND_CSS the lockup paints two TRANSPARENT words, i.e. nothing
    # at all. The brand font is loaded SEPARATELY from the body font above
    # ([brand].font_url against [typography].font_url): it styles the wordmark
    # only, never the data tables.
    if theme.BRAND_FONT_HEAD_HTML:
        ui.add_head_html(theme.BRAND_FONT_HEAD_HTML)
    ui.add_css(theme.BRAND_CSS)
    ui.add_css(LIVE_HEADER_CSS)
    module = importlib.import_module(f"pages.{screen.module}")
    # The header is a SIBLING of the page's own container, not a Quasar
    # ``ui.header``: a fixed bar would need the layout padding `main._NAV_CSS`
    # supplies and would float over the very charts these screens exist to show.
    with ui.column().classes("w-full gap-0"):
        _header(screen)
        with ui.column().classes(_CONTENT):
            module.render(**screen.kwargs)


def _register(screen):
    """Register one screen's route, and hand back the page function.

    ⚠ ``screen`` is bound by THIS FUNCTION'S PARAMETER, which is what makes each
    route render its own screen. A closure over the ``for`` variable below would
    make all fourteen render the LAST one — a bug that reads as "the site works"
    until you open a second tile.

    ⚠ And the binding is done here rather than as a ``def _page(_s=screen)``
    default, because NiceGUI hands the page function's signature to FastAPI: a
    parameter becomes a QUERY PARAMETER a stranger can set. Measured —
    ``GET /desk?_s=anything`` replaced the Screen with the string ``'anything'``,
    which reaches ``_render`` and its ``import_module(f"pages.{screen.module}")``.
    The page function below takes no arguments, so there is nothing to inject.
    """
    # No per-page ``favicon=``. The app has one per route because the owner runs
    # a dozen tabs and the colour is how they tell them apart; a public site
    # wants one mark. And a per-page favicon is not free: NiceGUI registers a
    # ``<route>/favicon.ico`` route for each, which on the SHARED global app
    # object means fourteen more paths that the private app's auth sweep would
    # then be enumerating on this process's behalf. ``ui.run(favicon=…)`` below
    # is the fallback ``get_favicon_url`` reads when a page declares none.
    @ui.page(screen.route, title=f"{screen.title} · {theme.BRAND_NAME}")
    def _page() -> None:
        _render(screen)
    return _page


for _screen in live_screens.SCREENS:
    _register(_screen)


if __name__ in {"__main__", "__mp_main__"}:
    # ⚠ BEFORE ui.run, and this ordering is the point: a check that runs after
    # the server is listening has already served the first anonymous request
    # with the stack's write credential in hand.
    require_acl_url(_ACL_URL)
    # 127.0.0.1 only. Caddy terminates TLS for LIVE_HOST and is the only thing
    # that should ever talk to this port — the same rule the app follows, and
    # for the same reason. ⚠ Never widen this to 0.0.0.0.
    ui.run(host="127.0.0.1", port=NICEGUI_LIVE_PORT,
           title=f"{theme.BRAND_NAME} Live ({LIVE_HOST})",
           favicon=_STATIC_DIR / "img" / "favicon.ico",
           dark=True, reload=False, show=False)
