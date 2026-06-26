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


class DriverControl(_Base):
    """cache:driver:control — the autonomous master switch + kill-switch.

    ``enabled`` is the user's master toggle (default OFF). ``halted`` latches
    within a day when a halt condition trips (banked $500 / loss cap / VIX) or
    the STOP button is hit; ``reason`` explains. The scheduler runs the decision
    loop only when ``enabled and not halted``. ``halted_date`` records the ISO
    date the latch was set so the scheduler can re-arm (clear) it the next
    trading day.
    """

    enabled: bool = False
    halted: bool = False
    reason: str | None = None
    halted_date: str | None = None     # ISO date the latch was set (re-arm next day)
    timestamp: str | None = None


class AutonomousState(_Base):
    """cache:driver:autonomous — the live monitor view for the /driver page.

    The repurposed Driver page renders the autonomous loop from this view:
    today's day P&L vs the target, the open driver positions, ``decisions``
    — the per-checkpoint audit log (newest first) of each cycle's thesis plus the
    chosen / clamped / rejected trades — and ``perf``, the driver paper account's
    performance scorecard (win rate / profit factor / P&L by symbol & strategy,
    sourced from ``cache:options:driver_paper_perf``). Loose dicts — the page
    tolerates sparse rows, like ``ApprovalState`` leaves its proposed trades loose.
    """

    date: str = ""
    enabled: bool = False
    halted: bool = False
    halt_reason: str | None = None
    day_pnl: float | None = None
    target: float = 500.0
    positions: list[dict] = []         # open driver positions w/ live P&L
    decisions: list[dict] = []         # newest-first checkpoint log
    perf: dict = {}                    # driver-account performance scorecard (additive)
    last_cycle_ts: str | None = None
    error: str | None = None
    timestamp: str | None = None
