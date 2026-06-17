from .envelope import _Base


class PortfolioModel(_Base):
    """Portfolio payload (cache:portfolio:positions).

    The single cache view the portfolio service publishes. Tier 2 (the service)
    builds the model from ``portfolio-analyzer/src`` engines and formats it with
    that app's ``view_model`` into **display-ready rows**, so the GUI tier stays
    a thin renderer (no engine imports). On each streamed quote tick the service
    re-formats and re-publishes; the GUI version-poll repaints.

    * ``holdings_rows`` — per-position display dicts (``view_model``'s
      ``HOLDINGS_COLUMNS`` keys: symbol/sector/quantity/market_value/day_pl/
      total_pl/vs_sector/since_purchase).
    * ``sector_rows`` — per-sector display dicts (sector/weight/benchmark_delta/
      tailwind).
    * ``performance_rows`` — per-position scorecard display dicts (symbol + grade
      letters + composite + ann_return + ... + top_action), worst-composite first.
    * ``suggestions`` — ``{symbol: [{"action", "severity", "reason", ...}]}``
      advisory rules (shown in the detail pane when a perf row is selected).
    * ``proxy_up`` / ``streaming`` — live-status meta for the status bar.

    Like the other domain contracts this validates the *envelope* shape (the
    lists/dicts are the right container types) as a gate against gross drift; it
    does not over-specify each sparse display row.
    """

    holdings_rows: list[dict] = []
    sector_rows: list[dict] = []
    performance_rows: list[dict] = []
    suggestions: dict = {}
    proxy_up: bool = False
    streaming: bool = False
    errors: list = []
    timestamp: str | None = None
