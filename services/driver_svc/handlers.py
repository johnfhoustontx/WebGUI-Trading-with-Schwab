"""Driver service handlers (Tier-2 → Tier-3 write path).

The order-approval queue reified over Redis. Three command-driven transitions
plus a read-only performance refresh:

* ``run`` — run the morning pipeline (``compute.run_morning``) and cache the
  resulting **pending** approval at ``cache:driver:approvals`` (also fired by the
  9:28 ET scheduler).
* ``approve`` — if the cached approval is still ``pending``, execute its proposed
  trades (``compute.execute`` → ``order_executor``, PAPER_TRADE simulates),
  re-cache it as ``approved`` with the results.
* ``skip`` — mark the cached approval ``skipped``.
* ``perf`` — recompute + cache the performance report at
  ``cache:driver:performance`` (also refreshed periodically by the scheduler).

Each write validates the payload against its contract (``ApprovalState`` /
``PerfReport``) as a gate against gross drift BEFORE caching, then publishes a
change event so the GUI version-poll repaints. Kept synchronous — the scaffold's
consumer loop handles sync handlers.
"""
from datetime import datetime, timezone

from services.driver_svc import compute
from shared.contracts.driver import ApprovalState, PerfReport

CACHE_APPROVALS = "cache:driver:approvals"
EVENT_APPROVALS = "events:driver:approvals"
CACHE_PERF = "cache:driver:performance"
EVENT_PERF = "events:driver:performance"

# Fields we project the compute dict onto (dropping extras like ml_signals /
# gex_snapshot the GUI ignores). ``.get`` with the field default keeps a
# partial/error result from crashing while construction validates the types.
_APPROVAL_FIELDS = {
    "date": "", "grade": "", "grade_reasons": [], "conditions": {},
    "pnl_today": None, "pnl_week": None, "proposed_trades": [], "status": "",
    "decision": None, "results": [], "reasons": [], "error": None,
    "timestamp": None,
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _cache_approval(bus, result) -> int:
    """Validate ``result`` against ``ApprovalState``, cache it, publish an event."""
    st = ApprovalState(**{k: result.get(k, default)
                          for k, default in _APPROVAL_FIELDS.items()})
    version = bus.cache_set(CACHE_APPROVALS, st.model_dump())
    bus.publish(EVENT_APPROVALS, {"version": version})
    return version


def run_morning(bus) -> None:
    """Run the morning pipeline and cache the pending approval (+ publish)."""
    _cache_approval(bus, compute.run_morning())


def approve(bus) -> None:
    """Execute the pending approval's trades and mark it approved.

    No-op unless a ``pending`` approval is currently cached (already-decided or
    no-trade states must not re-fire orders).
    """
    env = bus.cache_get(CACHE_APPROVALS)
    payload = env.payload if env else None
    if not payload or payload.get("status") != "pending":
        return
    results = compute.execute(payload.get("proposed_trades") or [])
    _cache_approval(bus, {**payload, "decision": "approved",
                          "status": "approved", "results": results})


def skip(bus) -> None:
    """Mark the cached approval skipped (no-op if nothing is cached)."""
    env = bus.cache_get(CACHE_APPROVALS)
    payload = env.payload if env else None
    if not payload:
        return
    _cache_approval(bus, {**payload, "decision": "skipped", "status": "skipped"})


def refresh_perf(bus) -> None:
    """Recompute the performance report, cache it, publish an event."""
    rep = compute.build_perf_report()
    pr = PerfReport(summary=rep.get("summary", {}),
                    trades=rep.get("trades", []), timestamp=_now_iso())
    version = bus.cache_set(CACHE_PERF, pr.model_dump())
    bus.publish(EVENT_PERF, {"version": version})


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:driver`` command; unknown types are a no-op."""
    if command.type == "run":
        run_morning(bus)
    elif command.type == "approve":
        approve(bus)
    elif command.type == "skip":
        skip(bus)
    elif command.type == "perf":
        refresh_perf(bus)
