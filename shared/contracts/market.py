from .envelope import _Base


class MarketDashboard(_Base):
    """Market-dashboard payload (cache:market:dashboard).

    The single view market_svc publishes each poll tick. ``categories`` is an
    ORDERED list (frame layout order) of ``{"category": str, "tiles": [tile, …]}``
    where each tile is a display-ready dict:
    ``{display, description, category, last, change, change_pct, value_only,
       color_state, polarity, stale}``. Like the other domain contracts this
    validates the envelope container shape, not each sparse tile.
    """

    categories: list[dict] = []
    proxy_up: bool = False
    errors: list = []
    timestamp: str | None = None
