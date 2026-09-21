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
    """Market summary payload (cache:market:summary).

    The highlights of the latest published NeuralStrike market report, which
    market_svc reads off ``deploy/site/reports/latest.html`` whenever the
    report is replaced (``services/market_svc/report_summary.py``) — no Claude
    call of its own. It feeds the Desk's MARKET SUMMARY frame and is the whole
    of the webgui ticker (headline, highlights, then the report's stamp). Defensive: no report yet means an empty
    payload, and both surfaces stay quiet rather than inventing a line.
    """

    # The report's verdict title.
    headline: str = ""
    # Its section headlines, in report order — at most five.
    highlights: list[str] = []
    slot: str = ""           # premarket / open / first_hour / midday / close
    slot_label: str = ""     # the report's own name for the slot, e.g. "Market close"
    report_date: str = ""    # YYYY-MM-DD the report was written for
    as_of: str = ""          # the report's own time stamp, e.g. "16:20 CT"
    report_url: str = ""     # the full report on the public site
