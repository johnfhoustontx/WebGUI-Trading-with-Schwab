from .envelope import _Base


class ApprovalState(_Base):
    """Driver order-approval payload (cache:driver:approvals).

    The morning-agent pipeline produces a *pending* approval (a graded day + a
    list of proposed trades); the GUI's APPROVE/SKIP buttons enqueue commands
    that transition it to ``approved`` (carrying ``order_executor`` results) or
    ``skipped``. This contract validates the *envelope* shape — proposed trades
    are a list, conditions/results are the right container types — as a gate
    against gross drift BEFORE caching; it does NOT over-specify each proposed
    trade (the dicts vary by bucket A/B/C, mirroring how ``ScanResult`` leaves
    its signals loose).

    ``status`` is the lifecycle marker the page renders on:
    ``pending`` (awaiting decision) · ``no_trade`` (graded X / no qualifying
    setups / market holiday) · ``error`` (pipeline failure) · ``approved``
    (orders sent — ``results`` populated) · ``skipped`` (declined).
    """

    date: str = ""
    grade: str = ""
    grade_reasons: list = []
    conditions: dict = {}
    pnl_today: float | None = None
    pnl_week: float | None = None
    proposed_trades: list[dict] = []
    status: str = ""
    decision: str | None = None  # None | "approved" | "skipped"
    results: list = []           # order_executor results once approved
    reasons: list = []           # no_trade / skip rationale
    error: str | None = None
    timestamp: str | None = None


class PerfReport(_Base):
    """Read-only performance aggregation (cache:driver:performance).

    Mirrors ``claude-driver/perf_report.build_report`` output: a ``summary``
    dict (totals / win rate / realized P&L / P&L-by-bucket) and a per-trade
    ``trades`` list. Loose dicts/lists — the GUI display tolerates sparse rows.
    """

    summary: dict = {}
    trades: list[dict] = []
    timestamp: str | None = None
