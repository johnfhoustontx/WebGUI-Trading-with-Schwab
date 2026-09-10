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

    A short Claude-written verdict market_svc writes when the market readings it
    consolidates change. It feeds both the webgui ticker (which leads its scroll
    with this, followed by live rule-based data items) and the Desk's MARKET
    SUMMARY frame. Defensive: an empty ``narrative`` (no key / API error) means
    the ticker shows live items only and the Desk frame stays quiet.
    """

    narrative: str = ""
    # The six readings the sentence was written from (market_svc's summary
    # packet). The Desk compares them with the live readings to say when the
    # sentence has been overtaken. Empty on an older writer.
    inputs: dict = {}
    # When the sentence was written (UTC ISO). The Desk prints it as "as of".
    as_of: str = ""
