"""The fourteen screens published on the public live origin.

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
    # ⚠ THE FOUR GAMMA SCREENS ALL NAME /options/gamma, AND THIS ONE IS FIRST.
    # ``PUBLIC_ROUTES`` keeps the first, so a Dealer Positioning click-through
    # lands here rather than on Net Prem or a Premium Divergence board — see
    # the note on ``_public_routes`` below.
    Screen("gamma", "/gamma", "Gamma", "options.gamma", "/options/gamma",
           kwargs={"symbol": "$SPX", "view": "GEX"}),
    Screen("net-premium", "/net-premium", "Net Prem", "options.gamma",
           "/options/gamma", kwargs={"view": "Net Prem"}, settings=_NETPREM),
    Screen("premium-divergence-spy", "/premium-divergence/spy",
           "Premium Divergence · SPY", "options.gamma", "/options/gamma",
           kwargs={"symbol": "SPY", "view": "Flow"}),
    Screen("premium-divergence-qqq", "/premium-divergence/qqq",
           "Premium Divergence · QQQ", "options.gamma", "/options/gamma",
           kwargs={"symbol": "QQQ", "view": "Flow"}),
)


def _public_routes() -> dict:
    """``{private route: the route THIS origin serves that page at}``.

    DERIVED, so there is no second table to keep in step: every Screen already
    names both ends, and ``tests/test_live_navigation.py`` reads ``main.py`` to
    check that ``private_route`` really is where the private app renders that
    module — the one field with no consequence on the private app, and so the
    one a typo would hide in until a visitor hit a 404.

    A private route that no screen names is simply ABSENT, which is the answer
    a caller needs: ``/options/paper``, ``/driver`` and ``/options/captured``
    are the owner's positions and are deliberately unpublished, so a control
    pointing at one must not be drawn as a link rather than be given a
    stand-in. Nothing here ever resolves to the private app's own host.

    ⚠ FIRST WINS, and that is the one place this table's ORDER matters: four
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
