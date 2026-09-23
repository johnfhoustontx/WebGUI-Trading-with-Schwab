"""The sixteen screens published on the public live origin.

PURE DATA -- no NiceGUI import -- so the route registration, the thumbnail
capture script and the static grid on neuralstrike.co all read one source and
cannot disagree about what is published.

⚠ Every route here is served UNAUTHENTICATED. Adding one is publishing it.
``tests/test_live_screens.py`` refuses the known control surfaces, but that list
cannot be exhaustive: the question to ask of a new entry is not "is it on the
forbidden list" but "would I put this on a billboard".
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Screen:
    """One published screen.

    ⚠ ``frozen=True`` stops the ATTRIBUTES being rebound; it does not make the
    two dicts immutable. That is deliberate and sufficient here -- every field
    is read at startup and never written, and ``SETTINGS_PINS`` below builds a
    NEW dict rather than aliasing one -- but it means a caller that mutated
    ``s.settings`` would edit the table for the whole process. Nothing does; a
    ``MappingProxyType`` would buy immutability at the cost of making the
    literals below unreadable.
    """

    slug: str            # URL segment AND capture filename stem
    route: str           # the public route
    title: str           # the tile caption and the browser title
    module: str          # dotted path under `pages`
    private_route: str   # where the PRIVATE app serves this same page
    kwargs: dict = field(default_factory=dict)   # pins passed to render()
    settings: dict = field(default_factory=dict)  # app_settings pins for this screen
    # Drawn as a picture tile on neuralstrike.co/live.html (and so captured by
    # tools/capture_live_shots.py). False for the interactive tools: a capture
    # of an empty form sells nothing, so they are reached from the site's
    # Tools menu only (removed from the grid 2026-09-21 at the owner's ask).
    tile: bool = True
    # The slug of the tile this screen is linked from instead, as a small
    # text link under that tile's caption (implies ``tile=False``). The four
    # extra $SPX Dealer Positioning views hang off the $SPX Gamma tile.
    parent: str = ""


_NETPREM = {"gamma_netprem_group": "indices",
            "gamma_netprem_symbols": ["SPY", "QQQ", "BIG10"],
            "gamma_netprem_mode": "dollars"}

SCREENS = (
    Screen("desk", "/desk", "The Desk", "desk", "/desk"),
    Screen("opportunity", "/opportunity", "Opportunity Board", "options.matrix",
           "/options/matrix"),
    Screen("flow", "/flow", "Flow Alerts", "options.flow", "/options/flow"),
    # Skin B is the Heat Lattice. The page reads it from app_settings, so this
    # is a settings pin rather than a render kwarg.
    Screen("macro", "/macro", "Macro Board", "market", "/market",
           settings={"macro_skin": "B"}),
    Screen("sentiment", "/sentiment", "Sentiment", "sentiment", "/sentiment"),
    Screen("bullbear", "/bullbear", "Bull / Bear Map", "sentiment_bullbear",
           "/sentiment/bullbear"),
    # Collapsed is already the page's build state (state["expanded"] = set()).
    Screen("sectors", "/sectors", "Sector & Industry", "sentiment_sectors",
           "/sentiment/sectors"),
    Screen("rotation", "/rotation", "Sector Rotation", "sentiment_rotation",
           "/sentiment/rotation"),
    Screen("rrg", "/rrg", "RRG", "sentiment_rrg", "/sentiment/rrg"),
    Screen("momentum", "/momentum", "Momentum", "sentiment_momentum",
           "/sentiment/momentum", kwargs={"level": "industry"}),
    # ⚠ THE TWO GAMMA SCREENS BOTH NAME /options/gamma, AND THIS ONE IS FIRST.
    # ``PUBLIC_ROUTES`` keeps the first, so a Dealer Positioning click-through
    # (the Flow Alerts tape) lands here rather than on Net Prem -- see the note
    # on ``_public_routes`` below.
    #
    # The PUBLIC GAMMA PAGE (2026-09-22): the app's dropdown over every symbol
    # options_svc collects, and its subtabs less Term and Net Prem. It replaced
    # nine pinned screens ($SPX GEX, SPY and QQQ GEX, $SPX Charm, DEX, Vanna and
    # Term, Premium Divergence on SPY and QQQ); their routes REDIRECT here
    # (``RETIRED_ROUTES``). It WRITES: a pick asks options_svc to keep the
    # symbol live (``bus_client.request_public_gamma`` on cmd:gamma_public, the
    # live ACL user's fifth write selector), which costs no Schwab call. Roadmap:
    # docs/plans/2026-09-21-public-gamma-any-symbol-roadmap.md.
    Screen("gamma", "/gamma", "Gamma", "options.gamma", "/options/gamma",
           kwargs={"public": True}),
    # Net Prem stays a screen of its own (decision D3): symbol-INDEPENDENT, so
    # the dropdown would mean nothing on it.
    Screen("net-premium", "/net-premium", "Net Prem", "options.gamma",
           "/options/gamma", kwargs={"view": "Net Prem"}, settings=_NETPREM),
    # ⚠ THE ONE SCREEN THAT WRITES. A visitor's Scan puts one validated symbol
    # on cmd:finder_public (bus_client.request_public_scan; the live ACL user's
    # only write selector) and options_svc answers it. ``public=True`` hands off
    # to pages/options/finder_live.py before the private Finder builds anything.
    # Roadmap: docs/plans/2026-09-21-public-strategy-finder-roadmap.md.
    Screen("finder", "/finder", "Strategy Finder", "options.swing",
           "/options/swing", kwargs={"public": True}, tile=False),
    # The SECOND screen that writes: a visitor's strikes loads and rescue
    # requests go on cmd:rescue_public (bus_client.request_public_ladder /
    # request_public_rescue; the live ACL user's second write selector).
    # ``public=True`` hands off to pages/options/rescue_live.py before the
    # private page builds its at-risk board, which reads the owner's paper book.
    # Blueprint: docs/plans/2026-09-21-public-rescue-adhoc-roadmap.md.
    Screen("rescue", "/rescue", "Rescue my Sh*tty trade", "options.rescue",
           "/options/rescue", kwargs={"public": True}, tile=False),
    # The THIRD and FOURTH screens that write, and the two share their streams:
    # a visitor's chain and expiration loads go on cmd:tools_public, and the
    # pricing (the Calculator's P&L and implied IV, the Simulator's sweep) on
    # cmd:tools_public_math (bus_client.request_public_tool / request_public_math;
    # the live ACL user's tools and math write selectors). ``public=True``
    # hands off to pages/options/calc_live.py / sim_live.py before the private
    # page builds anything, so the owner's calc_* / sim_* commands and the
    # single-user shared_position / page_state stores never exist here; the
    # two hand a position across through TAB storage (public_handoff), one
    # visitor per browser tab.
    # Blueprint: docs/plans/2026-09-21-public-calculator-simulator-design.md.
    Screen("calculator", "/calculator", "Calculator", "options.calculator",
           "/options/calculator", kwargs={"public": True}, tile=False),
    Screen("simulator", "/simulator", "Simulator", "options.simulator",
           "/options/simulator", kwargs={"public": True}, tile=False),
)


# Routes an earlier screen table published, and where each now REDIRECTS (a 308,
# registered by ``live_main``). Links to them were shared -- the tiles, the
# YouTube wall description, anyone's bookmarks -- so they must not 404. No state
# rides the redirect: carrying the old route's symbol or view would be a deep
# link, which the public Gamma page does not take (decision D4), so each opens
# on $SPX GEX. Pure data, like the table above; ``tests/test_live_main.py``
# pins the route set to screens + these + the static mount.
RETIRED_ROUTES = {
    "/gamma/spy": "/gamma",
    "/gamma/qqq": "/gamma",
    "/charm": "/gamma",
    "/dex": "/gamma",
    "/vanna": "/gamma",
    "/term": "/gamma",
    "/premium-divergence/spy": "/gamma",
    "/premium-divergence/qqq": "/gamma",
}

def _public_routes() -> dict:
    """``{private route: the route THIS origin serves that page at}``.

    DERIVED, so there is no second table to keep in step: every Screen already
    names both ends, and ``tests/test_live_navigation.py`` reads ``main.py`` to
    check that ``private_route`` really is where the private app renders that
    module — the one field with no consequence on the private app, and so the
    one a typo would hide in until a visitor hit a 404.

    A private route that no screen names is simply ABSENT, which is the answer
    a caller needs: ``/options/paper`` and ``/options/captured``
    are the owner's positions and are deliberately unpublished, so a control
    pointing at one must not be drawn as a link rather than be given a
    stand-in. Nothing here ever resolves to the private app's own host.

    ⚠ FIRST WINS, and that is the one place this table's ORDER matters: ten
    screens render ``options.gamma`` under different pins, and a click asking
    for Dealer Positioning means the plain Gamma board, not Net Prem. Asserted
    in the tests rather than left to be discovered."""
    out: dict = {}
    for s in SCREENS:
        out.setdefault(s.private_route, s.route)
    return out


PUBLIC_ROUTES = _public_routes()


# Pins that belong to the ORIGIN rather than to any one screen.
#
# ⚠ ``voice_enabled`` DEFAULTS TRUE, and the Desk's spoken alerts are not a
# bundled sound file: ``webgui/voice.py`` synthesizes each phrase through
# ``edge_tts``, which is a NETWORK call to a Microsoft endpoint, and writes the
# mp3 into ``webgui/data/voice/``. Unpinned, this origin would drive outbound
# calls on anonymous traffic -- one per new flow alert per visitor on the live
# path (``desk.speak_phrases``), plus up to 32 on the FIRST Desk build
# (``desk._prewarm_clips``, which unlike the live path has no market-hours
# gate). ``/voice`` is not mounted in this process, so not one of those clips
# could ever be played.
#
# Here rather than in the Desk screen's own ``settings`` because the reason is
# the ORIGIN, not the screen: public, so nothing that spends money, calls out,
# or writes. A Desk-attached pin would read as "the Desk publishes voice off",
# implying some other screen could publish it on. These are applied LAST so an
# origin rule wins, and ``tests/test_live_screens.py`` refuses a screen that
# names one of these keys at all, so the two dicts cannot silently disagree.
PUBLIC_PINS = {"voice_enabled": False}

# The union of every screen's settings pins plus the origin's, which is what the
# live entrypoint freezes. ⚠ Pins are process-wide, not per-request:
# app_settings is a module singleton. That is fine only because no two screens
# pin the SAME key to DIFFERENT values -- asserted in tests/test_live_screens.py.
SETTINGS_PINS = {**{k: v for s in SCREENS for k, v in s.settings.items()},
                 **PUBLIC_PINS}
