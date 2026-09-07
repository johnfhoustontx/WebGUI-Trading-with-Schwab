"""The PUBLIC read-only screens: a second NiceGUI process on its own origin.

Serves the fourteen screens in ``live_screens.SCREENS`` at ``live.neuralstrike.co``,
unauthenticated, to anyone. It renders the REAL page modules — the same ones the
app renders — so a published screen cannot drift from the private one.
``webgui/wall.py`` makes the same argument at length.

⚠ THIS MODULE MUST NEVER ``import main``. main.py's body registers every
``@_page`` route, so importing it here would publish ``/terminate`` (Stop All
Services) and ``/settings`` to the internet, silently, while looking entirely
correct. The seam the pages need lives in ``shell.py``; every page reaches the
shell through it, and ``tests/test_live_main.py`` pins the absence at source
level rather than trusting inspection.

Read-only is enforced at four layers, of which this file installs three:

1. a Redis ACL user with read commands only (from ``REDIS_LIVE_URL``) — the
   STRUCTURAL one, enforced by the server rather than by this process;
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
import os
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
for _p in (str(_HERE.parent), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app_settings                                   # noqa: E402
import bus_client                                     # noqa: E402
import live_screens                                   # noqa: E402

from repo_paths import LIVE_HOST, NICEGUI_LIVE_PORT   # noqa: E402

# ── The refusals, installed before any page module is imported ───────────────
# REDIS_LIVE_URL carries the read-only ACL user. Unset -> the ordinary URL, so a
# dev box without the ACL still runs; prod's unit always sets it.
bus_client.set_url(os.environ.get("REDIS_LIVE_URL") or None)
bus_client.set_read_only(True)
app_settings.freeze(live_screens.SETTINGS_PINS)

from nicegui import ui                                # noqa: E402
from pages.options import theme                       # noqa: E402

_STATIC_DIR = _HERE / "static"

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


def _render(screen) -> None:
    """Import and render one screen inside the minimal public shell."""
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
    module = importlib.import_module(f"pages.{screen.module}")
    with ui.column().classes(_CONTENT):
        module.render(**screen.kwargs)


def _register(screen):
    """Register one screen's route. Returns the page function (for tests).

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
    @ui.page(screen.route, title=f"{screen.title} · {theme.BRAND_NAME}",
             favicon=_STATIC_DIR / "img" / "favicon.ico")
    def _page() -> None:
        _render(screen)
    return _page


for _screen in live_screens.SCREENS:
    _register(_screen)


if __name__ in {"__main__", "__mp_main__"}:
    # 127.0.0.1 only. Caddy terminates TLS for LIVE_HOST and is the only thing
    # that should ever talk to this port — the same rule the app follows, and
    # for the same reason. ⚠ Never widen this to 0.0.0.0.
    ui.run(host="127.0.0.1", port=NICEGUI_LIVE_PORT,
           title=f"{theme.BRAND_NAME} Live ({LIVE_HOST})",
           favicon=_STATIC_DIR / "img" / "favicon.ico",
           dark=True, reload=False, show=False)
