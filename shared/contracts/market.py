from .envelope import _Base


class MarketDashboard(_Base):
    """Market-dashboard payload (cache:market:dashboard).

    The single view market_svc publishes each poll tick. ``categories`` is an
    ORDERED list (frame layout order) of ``{"category": str, "tiles": [tile, …]}``
    where each tile is a display-ready dict:
    ``{display, description, category, last, change, change_pct, value_only,
       color_state, polarity}``. Like the other domain contracts this
    validates the envelope container shape, not each sparse tile.

    Staleness is not carried per-tile: a proxy-down / missing quote renders as a
    grey ``no_data`` tile, and overall publish freshness is surfaced by the
    ``/status`` data-freshness table (via the bus envelope version/ts).
    """

    categories: list[dict] = []
    proxy_up: bool = False
    errors: list = []


class MarketSummary(_Base):
    """Market summary narrative payload (cache:market:summary).

    A short Claude-written verdict market_svc publishes on a schedule. The webgui
    ticker leads its scroll with this, followed by live rule-based data items.
    Defensive: an empty ``narrative`` (no key / API error) means the ticker shows
    live items only.
    """

    narrative: str = ""
    generated_at: str | None = None
