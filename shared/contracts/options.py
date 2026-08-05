from .envelope import _Base


class ScanResult(_Base):
    """Options-scan payload produced by ``scanner_engine.run_full_scan``.

    Signals are heterogeneous, sparse dicts that vary by trade type
    (PCS / CCS / IC), so the lists are modelled loosely as ``list[dict]``.
    This contract validates the *envelope* shape (the lists/dicts exist and
    have the right container types) as a gate against gross drift — it does
    not over-specify each signal (the GUI's ``signal_rows`` already tolerates
    sparse fields).

    ``signals_directional`` (single-leg LONG_CALL / LONG_PUT / SHORT_CALL /
    SHORT_PUT) is a THIRD list, not more of the same — do not merge it into the
    other two. Two reasons:

    * **Different score.** options-scanner's ``scoring.py`` is a premium
      seller's model and structurally cannot score a long call, so these are
      scored by ``strategy_scoring`` (Fit+Quality). Keeping them separate means
      their scores are never ranked against the premium composites; it also
      leaves the autonomous driver (which reads ``signals_0dte +
      signals_swing``) blind to them by construction.
    * **Different SHAPE.** These carry the ``strategy_scanner`` normalized
      shape: a ``legs`` list (not flat ``short_strike``/``long_strike``),
      per-contract dollars (×100, not per-share), ``rr`` (a ratio, not
      ``rr_pct``, a percent), and ``breakevens`` (a list, not the scalar
      ``breakeven``). Code reading these lists must not assume one shape.
    """

    signals_0dte: list[dict] = []
    signals_swing: list[dict] = []
    # Additive with a default: payloads cached before this field existed (Redis
    # persists cache:options:scan across restarts) must still validate.
    signals_directional: list[dict] = []
    vix_term_structure: dict = {}
    timestamp: str | None = None
    errors: list = []
    warnings: list = []


class RescueLeg(_Base):
    side: str = ""        # "BUY" | "SELL"
    right: str = ""       # "PUT" | "CALL"
    strike: float = 0.0
    expiry: str | None = None
    qty: int = 0
    price: float = 0.0


class RescueCandidate(_Base):
    """One ranked rescue action with full economics (commission-inclusive)."""
    action: str                     # close | partial_close | narrow | convert_ic |
                                    # convert_butterfly | broken_wing | roll_down |
                                    # roll_out | roll_down_out | inverted | futures_hedge
    label: str
    applies: bool = True
    apply_kind: str = "execute"     # "execute" | "advisory"
    gross_cash: float = 0.0         # credit (+) / debit (-) before fees
    commission: float = 0.0
    net_cash: float = 0.0           # gross_cash - commission
    realized_pnl: float | None = None   # P&L LOCKED IN by the action (close/partial)
    new_max_loss: float | None = None
    new_breakeven: float | None = None
    new_short_delta: float | None = None
    new_width: float | None = None
    new_expiry: str | None = None
    dte_after: int | None = None
    est_fill_legs: list[RescueLeg] = []
    rationale: list[str] = []
    context: list[str] = []
    warnings: list[str] = []
    score: float = 0.0


class RescueMark(_Base):
    underlying: float | None = None
    current_value: float | None = None
    unrealized_pnl: float | None = None
    short_delta: float | None = None
    dte: int | None = None


class RescueAdvisory(_Base):
    """cache:options:rescue:<position_id> — ranked rescue menu for one position."""
    position_id: int | str          # paper id (int) | captured signal_id (str)
    source: str = "paper"           # "paper" | "captured" (captured = advisory-only)
    symbol: str
    strategy: str
    state: str = "ok"               # ok | watch | tested | critical
    heat: float = 0.0
    mark: RescueMark = RescueMark()
    context: list[str] = []
    candidates: list[RescueCandidate] = []
    priced_from_version: int | None = None
    ts: str | None = None
    error: str | None = None


class MatrixSnapshot(_Base):
    """cache:options:matrix — one row per watchlist symbol for the Matrix Display tab.

    Rows are heterogeneous per-symbol dicts (see ``matrix.build_rows``); like the
    other view models this contract validates only the envelope shape. Every field
    carries a default so a payload cached before a field existed still validates
    (Redis persists cache:options:matrix across a service restart).
    """
    date: str | None = None            # CT date the row counts are scoped to
    session_date: str | None = None    # gex session date used for series/flip
    ts: str | None = None
    rows: list[dict] = []              # heterogeneous per-symbol row dicts (see matrix.build_rows)
    premium: dict | None = None        # dollar-weighted net-premium skew (see matrix.market_premium_aggregate)
    error: str | None = None


class NetPremiumSnapshot(_Base):
    """cache:options:net_premium — intraday net-premium series per symbol for the
    Dealer Positioning "Net Prem" view.

    ``series`` maps a display symbol (including the composite ``BIG10``) to
    ``[[ts, call_prem, put_prem], …]`` for ``session_date``, as produced by
    ``compute.build_net_premium``, which crops to RTH. Like the other view models
    this validates only the envelope shape. Every field carries a default so a
    payload cached before a field existed still validates.
    """
    session_date: str | None = None    # gex session date the series cover
    ts: str | None = None              # publish time (NOT the per-row ts inside series)
    series: dict = {}                  # {symbol: [[ts, call_prem, put_prem], …]}
    error: str | None = None
