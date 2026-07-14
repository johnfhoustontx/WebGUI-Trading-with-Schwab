from .envelope import _Base


class ScanResult(_Base):
    """Options-scan payload produced by ``scanner_engine.run_full_scan``.

    Signals are heterogeneous, sparse dicts that vary by trade type
    (PCS / CCS / IC), so the lists are modelled loosely as ``list[dict]``.
    This contract validates the *envelope* shape (the lists/dicts exist and
    have the right container types) as a gate against gross drift — it does
    not over-specify each signal (the GUI's ``signal_rows`` already tolerates
    sparse fields).
    """

    signals_0dte: list[dict] = []
    signals_swing: list[dict] = []
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
