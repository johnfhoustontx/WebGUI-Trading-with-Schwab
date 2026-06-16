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
    price: float | None = None
    volume: int | None = None
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
