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
    kwargs: dict = field(default_factory=dict)   # pins passed to render()
    settings: dict = field(default_factory=dict)  # app_settings pins for this screen


_NETPREM = {"gamma_netprem_group": "indices",
            "gamma_netprem_symbols": ["SPY", "QQQ", "BIG10"],
            "gamma_netprem_mode": "dollars"}

SCREENS = (
    Screen("desk", "/desk", "The Desk", "desk"),
    Screen("opportunity", "/opportunity", "Opportunity Board", "options.matrix"),
    Screen("flow", "/flow", "Flow Alerts", "options.flow"),
    # Skin B is the Heat Lattice. The page reads it from app_settings, so this
    # is a settings pin rather than a render kwarg.
    Screen("macro", "/macro", "Macro Board", "market", settings={"macro_skin": "B"}),
    Screen("sentiment", "/sentiment", "Sentiment", "sentiment"),
    Screen("bullbear", "/bullbear", "Bull / Bear Map", "sentiment_bullbear"),
    # Collapsed is already the page's build state (state["expanded"] = set()).
    Screen("sectors", "/sectors", "Sector & Industry", "sentiment_sectors"),
    Screen("rotation", "/rotation", "Sector Rotation", "sentiment_rotation"),
    Screen("rrg", "/rrg", "RRG", "sentiment_rrg"),
    Screen("momentum", "/momentum", "Momentum", "sentiment_momentum",
           kwargs={"level": "industry"}),
    Screen("gamma", "/gamma", "Gamma", "options.gamma",
           kwargs={"symbol": "$SPX", "view": "GEX"}),
    Screen("net-premium", "/net-premium", "Net Prem", "options.gamma",
           kwargs={"view": "Net Prem"}, settings=_NETPREM),
    Screen("premium-divergence-spy", "/premium-divergence/spy",
           "Premium Divergence · SPY", "options.gamma",
           kwargs={"symbol": "SPY", "view": "Flow"}),
    Screen("premium-divergence-qqq", "/premium-divergence/qqq",
           "Premium Divergence · QQQ", "options.gamma",
           kwargs={"symbol": "QQQ", "view": "Flow"}),
)

# The union of every screen's settings pins, which is what the live entrypoint
# freezes. ⚠ Pins are process-wide, not per-request: app_settings is a module
# singleton. That is fine only because no two screens pin the SAME key to
# DIFFERENT values -- asserted in tests/test_live_screens.py.
SETTINGS_PINS = {k: v for s in SCREENS for k, v in s.settings.items()}
