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
from datetime import date, datetime, timezone

from services.driver_svc import compute, settings
from shared.contracts.driver import (
    ApprovalState,
    AutonomousState,
    DriverControl,
    PerfReport,
)

CACHE_APPROVALS = "cache:driver:approvals"
EVENT_APPROVALS = "events:driver:approvals"
CACHE_PERF = "cache:driver:performance"
EVENT_PERF = "events:driver:performance"

# Autonomous decision layer (Phase 5) — the master-switch/kill-switch control key
# and the live monitor view. The option caches the cycle reads + the command
# stream it executes survivors on (the EXISTING options ``paper_create`` path).
CACHE_CONTROL = "cache:driver:control"
EVENT_CONTROL = "events:driver:control"
CACHE_AUTONOMOUS = "cache:driver:autonomous"
EVENT_AUTONOMOUS = "events:driver:autonomous"
CACHE_OPT_SCAN = "cache:options:scan"
CACHE_OPT_PAPER = "cache:options:paper_account"
CMD_OPTIONS = "cmd:options"

# Cap on the newest-first per-checkpoint decision log carried in the monitor view.
_DECISION_LOG_CAP = 50

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


# ── autonomous control key (Phase 5.1) ───────────────────────────────────────


def read_control(bus) -> dict:
    """The autonomous control state (``cache:driver:control``) as a plain dict.

    Returns the ``DriverControl`` defaults (disabled, not halted) when the key
    has never been written — so the very first read is a safe "off" rather than
    a missing-key crash.
    """
    env = bus.cache_get(CACHE_CONTROL)
    return env.payload if env else DriverControl().model_dump()


def set_control(bus, *, enabled=None, halted=None, reason=None, halted_date=None) -> dict:
    """Mutate + persist the control key, returning the new state dict.

    A read-modify-write of ``cache:driver:control``: each kwarg left ``None`` is
    untouched, so ``enable``/``disable`` flip only ``enabled`` and the kill-switch
    flips only ``halted``/``reason``/``halted_date`` without clobbering the master
    toggle. Validates through ``DriverControl`` before caching and publishes a
    change event so the GUI version-poll repaints.
    """
    cur = read_control(bus)
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    if halted is not None:
        cur["halted"] = bool(halted)
        cur["reason"] = reason
        cur["halted_date"] = halted_date
    cur["timestamp"] = _now_iso()
    st = DriverControl(**cur)
    bus.cache_set(CACHE_CONTROL, st.model_dump(), event=EVENT_CONTROL)
    return st.model_dump()


# ── autonomous decision cycle (Phase 5.2) ────────────────────────────────────


def _read_payload(bus, key):
    """The payload dict cached under ``key`` (None-safe)."""
    env = bus.cache_get(key)
    return env.payload if env else None


def _publish_autonomous(bus, *, day_pnl, positions, decision, guarded, executed, control) -> int:
    """Cache + publish the monitor view, prepending this cycle to the decision log.

    Reads the prior ``cache:driver:autonomous`` to grow a NEWEST-FIRST audit log
    (``log.insert(0, …)``) capped at ``_DECISION_LOG_CAP`` so the page never balloons.
    Each log row carries the thesis + the trades that were ACTUALLY ENQUEUED this
    cycle (``executed`` — what truly fired, which equals the clamped executable list
    on the happy path but is shorter if an enqueue failed mid-loop) + the rejected
    ones + the halt flag/reason. Validated through ``AutonomousState`` before caching.
    """
    prev = _read_payload(bus, CACHE_AUTONOMOUS) or {}
    log = list(prev.get("decisions", []))
    log.insert(0, {
        "ts": _now_iso(),
        "thesis": decision.get("day_thesis", ""),
        "stand_down": decision.get("stand_down", True),
        "executed": [{"id": t.get("id"), "symbol": (t.get("signal") or {}).get("symbol"),
                      "qty": t.get("qty"), "rationale": t.get("rationale", "")}
                     for t in executed],
        "rejected": guarded.get("rejected", []),
        "halted": guarded.get("halted", False),
        "halt_reason": guarded.get("halt_reason"),
    })
    st = AutonomousState(
        date=date.today().isoformat(),
        enabled=control.get("enabled", False),
        halted=control.get("halted", False),
        halt_reason=control.get("reason"),
        day_pnl=day_pnl,
        target=settings.DAILY_TARGET,
        positions=positions,
        decisions=log[:_DECISION_LOG_CAP],
        last_cycle_ts=_now_iso(),
        timestamp=_now_iso(),
    )
    return bus.cache_set(CACHE_AUTONOMOUS, st.model_dump(), event=EVENT_AUTONOMOUS)


def run_autonomous_cycle(bus) -> None:
    """One autonomous decision checkpoint (gate → cycle → execute → publish).

    A genuine no-op unless the control key is ``enabled and not halted`` — when
    gated off, NOTHING happens (no market fetch, no cycle, no enqueue, no publish).
    Otherwise: read the option scan + paper-account caches, ask the bus-free
    ``compute.run_cycle`` brain (which itself never raises), then for each
    survivor enqueue the EXISTING ``paper_create`` command on ``cmd:options`` —
    tagging a COPY of the raw signal ``source="driver"`` (the raw menu signal is
    the same object the cached scan view holds, so it must NOT be mutated) with
    the guardrail-CLAMPED ``qty`` (never the model's requested quantity). If the
    cycle reports a halt, latch the kill-switch (``set_control(halted=True)``) so
    no further checkpoints run until the next-day re-arm. Finally publish the
    monitor view.
    """
    control = read_control(bus)
    if not control.get("enabled") or control.get("halted"):
        return
    scan = _read_payload(bus, CACHE_OPT_SCAN) or {}
    paper = _read_payload(bus, CACHE_OPT_PAPER) or {}
    market = compute.fetch_market_context()
    out = compute.run_cycle(scan, paper, target=settings.DAILY_TARGET,
                            limits=settings.limits(), market=market)
    # Kill-switch tightening: re-read control RIGHT BEFORE firing. The top-of-fn gate
    # only catches a STOP/disable from before the cycle; the cycle itself is slow (a
    # proxy fetch + the Claude call), so a STOP that lands DURING it must still be
    # honored — don't fire this checkpoint's already-decided trades.
    control = read_control(bus)
    stop_now = not control.get("enabled") or control.get("halted")
    # Enqueue each survivor, isolated: a single bad enqueue (transient bus error /
    # malformed row) must NOT skip the halt-latch + publish below, and the audit log
    # must record only what ACTUALLY fired. ``executed`` accumulates the enqueued rows.
    executed = []
    for t in ([] if stop_now else out.get("executable", [])):
        sig = t.get("signal")
        if not isinstance(sig, dict):
            continue
        signal = {**sig, "source": "driver"}  # shallow COPY — we add only the top-level source tag
        try:
            bus.enqueue_command(CMD_OPTIONS, {"type": "paper_create",
                                              "args": {"signal": signal, "qty": t.get("qty")}})
            executed.append(t)
        except Exception:  # noqa: BLE001 — stop firing, but still latch/publish what fired.
            break
    if out.get("halted"):
        control = set_control(bus, halted=True, reason=out.get("halt_reason"),
                              halted_date=date.today().isoformat())
    _publish_autonomous(bus, day_pnl=out.get("day_pnl"),
                        positions=out.get("open_positions", []),
                        decision=out.get("decision", {}), guarded=out,
                        executed=executed, control=control)


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:driver`` command; unknown types are a no-op.

    Legacy approval-queue commands (``run``/``approve``/``skip``/``perf``) plus
    the autonomous controls: ``cycle`` runs one decision checkpoint now, ``enable``
    /``disable`` flip the master switch (``enable`` also clears any stale halt so
    re-enabling re-arms), and ``stop`` is the kill-switch (latch ``halted``).
    """
    if command.type == "run":
        run_morning(bus)
    elif command.type == "approve":
        approve(bus)
    elif command.type == "skip":
        skip(bus)
    elif command.type == "perf":
        refresh_perf(bus)
    elif command.type == "cycle":
        run_autonomous_cycle(bus)
    elif command.type == "enable":
        set_control(bus, enabled=True, halted=False, reason=None)
    elif command.type == "disable":
        set_control(bus, enabled=False)
    elif command.type == "stop":
        set_control(bus, halted=True, reason="manual STOP",
                    halted_date=date.today().isoformat())
