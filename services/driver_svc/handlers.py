"""Driver service handlers (Tier-2 → Tier-3 write path).

The autonomous decision layer's bus I/O, command-driven over ``cmd:driver``:

* ``cycle`` — run one autonomous decision checkpoint now
  (``run_autonomous_cycle``: gate → ``compute.run_cycle`` → enqueue the clamped
  survivors as ``driver_paper_create`` on ``cmd:options`` → latch the kill-switch
  on halt → publish the monitor view at ``cache:driver:autonomous``).
* ``enable`` / ``disable`` — flip the ``cache:driver:control`` master switch
  (``enable`` also clears any stale halt so re-enabling re-arms).
* ``stop`` — the kill-switch (latch ``halted``).

Each write validates the payload against its contract (``DriverControl`` /
``AutonomousState``) as a gate against gross drift BEFORE caching, then publishes a
change event so the GUI version-poll repaints. Kept synchronous — the scaffold's
consumer loop handles sync handlers.
"""
from datetime import date, datetime, timezone

from services.driver_svc import compute, settings
from shared.contracts.driver import (
    AutonomousState,
    DriverControl,
)

# Autonomous decision layer (Phase 5) — the master-switch/kill-switch control key
# and the live monitor view. The option caches the cycle reads + the command
# stream it executes survivors on (the EXISTING options ``paper_create`` path).
CACHE_CONTROL = "cache:driver:control"
EVENT_CONTROL = "events:driver:control"
CACHE_AUTONOMOUS = "cache:driver:autonomous"
EVENT_AUTONOMOUS = "events:driver:autonomous"
CACHE_OPT_SCAN = "cache:options:scan"
# The autonomous cycle trades into + reads P&L from the driver's OWN isolated
# paper book (a dedicated DB published by options_svc), NOT the user's manual
# paper_account — so the $500/halt logic measures the driver's own equity and the
# trades don't commingle. ``driver_paper_perf`` is the matching scorecard view.
CACHE_OPT_DRIVER_PAPER = "cache:options:driver_paper_account"
CACHE_OPT_DRIVER_PERF = "cache:options:driver_paper_perf"
CMD_OPTIONS = "cmd:options"
# The sentiment composite carries the five-state market state under
# ``derived.trend`` — surfaced to the decider as REASONING CONTEXT only (the scanner
# menu is ALREADY hard-gated by regime_filter, so this adds no hard rule; guardrails
# are untouched).
CACHE_SENTIMENT_COMPOSITE = "cache:sentiment:composite"

# Cap on the newest-first per-checkpoint decision log carried in the monitor view.
_DECISION_LOG_CAP = 50


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


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


def _read_market_state(bus):
    """The five-state market state from ``cache:sentiment:composite`` ``derived.trend``.

    Returns ``{state, label, evidence}`` (additive REASONING CONTEXT for the
    decider — ``regime_filter`` already hard-gates the scanner menu the driver
    reads, so this changes NO hard rule and the guardrails never see it). Read
    DEFENSIVELY → ``None`` on any failure / missing composite / blank trend, so a
    down/slow sentiment service never blocks a cycle.
    """
    try:
        trend = ((_read_payload(bus, CACHE_SENTIMENT_COMPOSITE) or {})
                 .get("derived") or {}).get("trend") or {}
        state, label = trend.get("state"), trend.get("label")
        if not (state or label):
            return None
        ev = trend.get("evidence")
        return {"state": state, "label": label,
                "evidence": list(ev) if isinstance(ev, list) else []}
    except Exception:  # noqa: BLE001 — context is best-effort; never block the cycle.
        return None


def _publish_autonomous(bus, *, day_pnl, positions, decision, guarded, executed,
                        control, perf=None) -> int:
    """Cache + publish the monitor view, prepending this cycle to the decision log.

    Reads the prior ``cache:driver:autonomous`` to grow a NEWEST-FIRST audit log
    (``log.insert(0, …)``) capped at ``_DECISION_LOG_CAP`` so the page never balloons.
    Each log row carries the thesis + the trades that were ACTUALLY ENQUEUED this
    cycle (``executed`` — what truly fired, which equals the clamped executable list
    on the happy path but is shorter if an enqueue failed mid-loop) + the rejected
    ones + the halt flag/reason. ``perf`` is the driver-account performance scorecard
    (``cache:options:driver_paper_perf``), attached so the page can render it without
    a second poll. Validated through ``AutonomousState`` before caching.
    """
    prev = _read_payload(bus, CACHE_AUTONOMOUS) or {}
    log = list(prev.get("decisions", []))
    log.insert(0, {
        "ts": _now_iso(),
        "thesis": decision.get("day_thesis", ""),
        "stand_down": decision.get("stand_down", True),
        # R7: carry the stand-down REASON onto the log row so the /driver UI can
        # distinguish an ops incident (no_key/api_error/parse_error) from a genuine
        # model stand-down. Missing/legacy → None → renders as today.
        "reason": decision.get("reason"),
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
        perf=perf if isinstance(perf, dict) else {},   # bulletproof vs a malformed payload
        last_cycle_ts=_now_iso(),
        timestamp=_now_iso(),
    )
    return bus.cache_set(CACHE_AUTONOMOUS, st.model_dump(), event=EVENT_AUTONOMOUS)


def run_autonomous_cycle(bus) -> None:
    """One autonomous decision checkpoint (gate → cycle → execute → publish).

    A genuine no-op unless the control key is ``enabled and not halted`` — when
    gated off, NOTHING happens (no market fetch, no cycle, no enqueue, no publish).
    Otherwise: read the option scan + the DRIVER paper-account cache (the driver's
    OWN isolated book, so day-P&L + open positions + the $500/halt logic measure the
    driver's equity, not the user's manual account), ask the bus-free
    ``compute.run_cycle`` brain (which itself never raises), then for each survivor
    enqueue a ``driver_paper_create`` command on ``cmd:options`` — opening into the
    driver DB (NOT the manual ``paper_create``) — tagging a COPY of the raw signal
    ``source="driver"`` (the raw menu signal is the same object the cached scan view
    holds, so it must NOT be mutated) with the guardrail-CLAMPED ``qty`` (never the
    model's requested quantity). If the cycle reports a halt, latch the kill-switch
    (``set_control(halted=True)``) so no further checkpoints run until the next-day
    re-arm. Finally publish the monitor view, attaching the driver-account
    performance scorecard (``cache:options:driver_paper_perf``).
    """
    control = read_control(bus)
    if not control.get("enabled") or control.get("halted"):
        return
    scan = _read_payload(bus, CACHE_OPT_SCAN) or {}
    paper = _read_payload(bus, CACHE_OPT_DRIVER_PAPER) or {}
    market = compute.fetch_market_context()
    # Additive: fold the five-state market state into the decider's market context
    # (context only — no new hard rule; the menu is already regime-gated upstream).
    ms = _read_market_state(bus)
    if ms:
        market["market_state"] = ms
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
            bus.enqueue_command(CMD_OPTIONS, {"type": "driver_paper_create",
                                              "args": {"signal": signal, "qty": t.get("qty")}})
            executed.append(t)
        except Exception:  # noqa: BLE001 — stop firing, but still latch/publish what fired.
            break
    if out.get("halted"):
        control = set_control(bus, halted=True, reason=out.get("halt_reason"),
                              halted_date=date.today().isoformat())
    perf = _read_payload(bus, CACHE_OPT_DRIVER_PERF) or {}
    _publish_autonomous(bus, day_pnl=out.get("day_pnl"),
                        positions=out.get("open_positions", []),
                        decision=out.get("decision", {}), guarded=out,
                        executed=executed, control=control, perf=perf)


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:driver`` command; unknown types are a no-op.

    The autonomous controls: ``cycle`` runs one decision checkpoint now,
    ``enable``/``disable`` flip the master switch (``enable`` also clears any stale
    halt so re-enabling re-arms), and ``stop`` is the kill-switch (latch
    ``halted``).
    """
    if command.type == "cycle":
        run_autonomous_cycle(bus)
    elif command.type == "enable":
        set_control(bus, enabled=True, halted=False, reason=None)
    elif command.type == "disable":
        set_control(bus, enabled=False)
    elif command.type == "stop":
        set_control(bus, halted=True, reason="manual STOP",
                    halted_date=date.today().isoformat())
