from .envelope import _Base


class TradeAnalysis(_Base):
    """Single-symbol analysis payload produced by ``trade_svc.compute.analyze``.

    The verdict/momentum/sector sub-objects are heterogeneous, sparse dicts
    (the verdict ``breakdown`` lists vary by factor, momentum keys come and go
    off-hours), so they are modelled loosely as ``dict``. This contract
    validates the *envelope* shape (a symbol exists; the sub-objects are the
    right container types) as a gate against gross drift BEFORE caching — it
    does not over-specify each sub-object (the GUI's display builders already
    tolerate sparse fields).

    ``fundamentals`` is the small display view (P/E, growth, ROE, margin trend,
    days-to-earnings) the page surfaces; ``fundamentals_available`` is True when
    the Schwab fundamentals were sufficient for the ``InvestorVerdict`` to run on
    real data (else it degrades to an "Insufficient fundamental data" HOLD).
    """

    symbol: str
    description: str = ""
    # The real company name, from Schwab's symbol-search projection.
    # `description` is the TICKER; the fundamental projection has no name.
    company_name: str | None = None
    price: float | None = None
    volume: int | None = None
    change: float | None = None
    change_pct: float | None = None
    bias: str = ""
    ema_alignment: dict = {}
    momentum: dict = {}
    volume_profile: dict = {}
    sector: dict = {}
    position_verdict: dict = {}
    investor_verdict: dict = {}
    fundamentals: dict = {}
    fundamentals_available: bool = False
    timestamp: str | None = None
    errors: list = []
    markov: dict | None = None
    swing_model: dict | None = None

    # ── two-sided reads (2026-08-22) ────────────────────────────────────────
    # All ADDITIVE and all optional: a payload written before they existed
    # validates unchanged, and the page's builders no-op when they are absent.
    #
    # ``direction_clearance`` — what the tape permits per side (cleared /
    # relative_only / blocked, each with reasons). The swing model predicts
    # excess return vs SPY, so a bottom-band name is predicted to LAG, not to
    # fall; this is what stops that being read as a directional short.
    #
    # ``dealer_context`` — the symbol's row from the options matrix (gamma
    # regime, flip, walls, ATM IV), joined for context only. It reaches no
    # verdict: positioning gates and informs in this codebase, and only the
    # IC-tested harness grants weight.
    #
    # ``peers`` — where the symbol sits among its SECTOR peers in today's
    # cross-section, which is the question single-stock research should end on:
    # is this the best vehicle for the thesis?
    direction_clearance: dict | None = None
    dealer_context: dict | None = None
    peers: dict | None = None
    # "upcoming" / "none_scheduled" / "not_listed". Kept separate from
    # fundamentals.days_to_earnings because the last two BOTH leave that
    # None, and conflating them lets the earnings gate fail open silently.
    earnings_coverage: str | None = None
    # The verdict rendered as a falsifiable plan: structure, entry zone,
    # stop, target, and a TIME STOP at the model's own 20-day horizon —
    # past which the read is unmodelled and nothing else says so.
    trade_plan: dict | None = None
    # Phase 6. The live-IC monitor's reading over the recommendation journal:
    # is the edge holding? Carries its own `status`, because with a young
    # journal the honest answer is "not enough data yet" and that must not
    # render as a thin edge.
    live_ic: dict | None = None
    # This name's recent journal reads — rows, not a statistic.
    symbol_history: list[dict] = []


class RankBoard(_Base):
    """Today's whole cross-section, ranked — ``trade_svc.compute.build_rank_board``.

    Rows are modelled as ``list[dict]`` for the same reason ``TradeAnalysis``'s
    sub-objects are loose: the per-row gate list and score fields are sparse and
    move with the model. This validates the ENVELOPE — that rows are a list and
    the pools are lists of symbols — as a gate against gross drift before
    caching.

    ⚠ ``risk_share`` is not decoration. Phase 4 measured this composite at
    cross-sectional IC +0.16 when the market's forward 20 days were up and −0.11
    when they were down, with the asymmetry carried entirely by the volatility
    factors. On a RANKED board that means the top decile skews to the
    highest-beta names, which is the single most important thing to know about
    the ordering — so it travels with the payload rather than being something
    the page could forget to ask for.

    ``gates_evaluated`` names the SUBSET of the card's gates the board can
    check from the daily snapshot plus two local stores. Without it, a row
    showing no gates would read as "cleared everything the card checks".

    ``short_expression`` is ``"relative"`` whenever the tape has not cleared the
    short side: the model predicts excess return vs SPY, so a bottom-decile name
    in an uptrend is predicted to LAG, not to fall.
    """

    # WHY the board is empty, when it is: "ok" | "no_snapshot" |
    # "legacy_snapshot" | "no_artifact" | "unscoreable". Only one of those is
    # about the market, and on screen they are indistinguishable without it.
    status: str = "ok"
    as_of: str | None = None
    model_version: str | None = None
    regime_key: str | None = None
    risk_share: float | None = None
    horizon_days: int = 20
    n: int = 0
    thin_cross_section: bool = True
    rows: list[dict] = []
    long_pool: list[str] = []
    short_pool: list[str] = []
    market_filter: dict = {}
    short_expression: str = "relative"
    gates_evaluated: list[str] = []
    # Joined per row from the options matrix + short-interest store:
    # dealer regime, ATM IV and its state, and days-to-cover. A symbol
    # either source lacks reads ABSENT rather than neutral.


class ModelBook(_Base):
    """The model's own paper book — ``trade_svc.compute.run_model_book``.

    Paper only, and isolated from the driver's book: this one exists to give the
    swing model a track record without anyone placing its trades.

    ⚠ It trades the UNDERLYING rather than the Trade Plan's options structure, a
    deliberate deviation. The model predicts a 20-day excess return on the
    stock; wrapping that in a spread would add theta and vega P&L that says
    nothing about whether the ranking works, so a book that lost money on
    correct calls would be indistinguishable from one whose calls were wrong.

    ``summary`` splits long from short, because a book carried entirely by its
    longs is a different product from one that works on both sides — and this
    model's short side is usually expressed RELATIVE to SPY, since that is
    literally what it predicts.
    """

    as_of: str | None = None
    positions: list[dict] = []
    summary: dict = {}
