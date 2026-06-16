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

    ``fundamentals_available`` is False in the MVP: this repo has no
    fundamentals source wired (the Schwab proxy exposes none and finvizfinance
    is not installed), so ``InvestorVerdict`` degrades to an
    "Insufficient fundamental data" HOLD. Flipping this flag is a clean
    follow-up once a fundamentals feed lands.
    """

    symbol: str
    description: str = ""
    price: float | None = None
    volume: int | None = None
    bias: str = ""
    ema_alignment: dict = {}
    momentum: dict = {}
    volume_profile: dict = {}
    sector: dict = {}
    position_verdict: dict = {}
    investor_verdict: dict = {}
    fundamentals_available: bool = False
    timestamp: str | None = None
    errors: list = []
