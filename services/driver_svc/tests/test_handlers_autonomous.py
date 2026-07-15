"""Tests for the autonomous bus I/O handlers (Phase 5).

The bus I/O wrapper around the bus-free ``compute.run_cycle`` brain: the control
key read/write helpers (5.1), ``run_autonomous_cycle`` (5.2 — gate → cycle →
``paper_create`` enqueue → halt-latch → publish), and the autonomous command
dispatch branches (5.3 — ``cycle``/``enable``/``disable``/``stop``).

``compute.run_cycle`` / ``compute.fetch_market_context`` are monkeypatched so no
network/engine work happens; the focus here is the Redis read/execute/publish
plumbing and the kill-switch latching. Uses the ``fake_bus`` fixture (conftest).
"""
import json

from services.driver_svc import handlers


def _cmd(t, **args):
    return type("C", (), {"type": t, "args": args})()


# ── 5.1: control read/write helpers ──────────────────────────────────────────


def test_control_roundtrip(fake_bus):
    handlers.set_control(fake_bus, enabled=True)
    c = handlers.read_control(fake_bus)
    assert c["enabled"] is True and c["halted"] is False


def test_read_control_default_when_unset(fake_bus):
    c = handlers.read_control(fake_bus)
    assert c["enabled"] is False and c["halted"] is False and c["reason"] is None


def test_stop_sets_halted(fake_bus):
    handlers.set_control(fake_bus, enabled=True)
    handlers.set_control(fake_bus, halted=True, reason="manual STOP")
    c = handlers.read_control(fake_bus)
    assert c["halted"] is True and c["reason"] == "manual STOP" and c["enabled"] is True


def test_set_control_publishes_event(fake_bus):
    sub = fake_bus.subscribe(handlers.EVENT_CONTROL)
    handlers.set_control(fake_bus, enabled=True)
    msg = sub.get_message(timeout=1.0)
    sub.close()
    assert msg is not None and "version" in msg


# ── 5.2: run_autonomous_cycle (gate → cycle → execute → publish) ─────────────


def _seed_caches(fake_bus, *, day_pnl=0.0):
    fake_bus.cache_set("cache:options:scan",
                       {"signals_0dte": [], "signals_swing": []})
    # The autonomous cycle now reads the ISOLATED driver paper book, not the
    # user's manual account — seed session_pnl (the real account_snapshot field).
    fake_bus.cache_set("cache:options:driver_paper_account",
                       {"snapshot": {"session_pnl": day_pnl}, "positions": []})


def _stub_cycle(monkeypatch, out):
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", lambda *a, **k: out)


def test_cycle_disabled_is_noop(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=False)
    called = []
    monkeypatch.setattr(handlers.compute, "run_cycle",
                        lambda *a, **k: called.append(1) or {})
    monkeypatch.setattr(handlers.compute, "fetch_market_context",
                        lambda: called.append("mkt") or {})
    enq = []
    monkeypatch.setattr(fake_bus, "enqueue_command",
                        lambda stream, cmd: enq.append((stream, cmd)))
    handlers.run_autonomous_cycle(fake_bus)
    assert called == []  # gated off — no cycle, no market fetch
    assert enq == []      # and nothing enqueued
    # And no monitor view was published (genuine no-op).
    assert fake_bus.cache_get("cache:driver:autonomous") is None


def test_cycle_halted_is_noop(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    handlers.set_control(fake_bus, halted=True, reason="banked")
    called = []
    monkeypatch.setattr(handlers.compute, "run_cycle",
                        lambda *a, **k: called.append(1) or {})
    handlers.run_autonomous_cycle(fake_bus)
    assert called == []  # halted → gated off


def test_cycle_enqueues_driver_paper_create(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus, day_pnl=0.0)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": False, "day_thesis": "t",
                     "trades": [{"id": "m0", "quantity": 2}]},
        "executable": [{"id": "m0", "signal": {"symbol": "QQQ", "structure": "PCS"},
                        "qty": 2, "rationale": "r"}],
        "rejected": [], "halted": False, "halt_reason": None,
        "shadow_gate": {"posture": "up", "would_block": [], "n": 0, "enabled": False},
        "market_read": {"summary": "gamma bullish · breadth +"},
        "day_pnl": 0.0, "open_positions": []})
    enq = []
    monkeypatch.setattr(fake_bus, "enqueue_command",
                        lambda stream, cmd: enq.append((stream, cmd)))
    handlers.run_autonomous_cycle(fake_bus)
    assert len(enq) == 1
    stream, cmd = enq[0]
    assert stream == "cmd:options"
    # The driver trades into its OWN isolated paper book, not the manual account.
    assert cmd["type"] == "driver_paper_create"
    assert cmd["args"]["qty"] == 2
    assert cmd["args"]["signal"]["source"] == "driver"
    assert cmd["args"]["signal"]["symbol"] == "QQQ"
    # Entry context stamped for the post-mortem: the posture + market read + would_block.
    ctx = cmd["args"]["context"]
    assert ctx["posture"] == "up" and ctx["gate_enabled"] is False
    assert ctx["would_block"] is False and ctx["market_read"] == "gamma bullish · breadth +"


def test_cycle_context_flags_would_block_trade(fake_bus, monkeypatch):
    """A fired trade the shadow gate flagged is stamped would_block=True in its context."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": False, "day_thesis": "t",
                     "trades": [{"id": "m0", "quantity": 1}]},
        "executable": [{"id": "m0", "signal": {"symbol": "SPY", "structure": "CCS"},
                        "qty": 1, "rationale": "r"}],
        "rejected": [], "halted": False, "halt_reason": None,
        "shadow_gate": {"posture": "up", "n": 1, "enabled": False,
                        "would_block": [{"id": "m0", "symbol": "SPY", "structure": "CCS"}]},
        "day_pnl": 0.0, "open_positions": []})
    enq = []
    monkeypatch.setattr(fake_bus, "enqueue_command",
                        lambda stream, cmd: enq.append((stream, cmd)))
    handlers.run_autonomous_cycle(fake_bus)
    assert enq[0][1]["args"]["context"]["would_block"] is True


def test_cycle_publishes_stand_down_reason_on_log_row(fake_bus, monkeypatch):
    """R7 end-to-end: the decider's stand-down ``reason`` (e.g. a broken key →
    'no_key') must be carried onto the published decision-log row so the /driver UI
    can flag an ops incident vs a genuine model stand-down."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": True, "day_thesis": "", "trades": [],
                     "reason": "no_key"},
        "executable": [], "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    handlers.run_autonomous_cycle(fake_bus)
    state = fake_bus.cache_get("cache:driver:autonomous").payload
    assert state["decisions"][0]["reason"] == "no_key"
    assert state["decisions"][0]["stand_down"] is True


def test_cycle_publishes_shadow_gate_on_log_row(fake_bus, monkeypatch):
    """The shadow directional gate (log-only) rides onto the published decision-log row
    so /driver accrues would-have-blocked evidence while the gate is inert."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": False, "day_thesis": "t",
                     "trades": [{"id": "m0", "quantity": 1}]},
        "executable": [{"id": "m0", "signal": {"symbol": "SPY", "structure": "CCS"},
                        "qty": 1, "rationale": "r"}],
        "rejected": [], "halted": False, "halt_reason": None,
        "shadow_gate": {"posture": "up", "would_block": [
            {"id": "m0", "symbol": "SPY", "structure": "CCS"}], "n": 1, "enabled": False},
        "day_pnl": 0.0, "open_positions": []})
    handlers.run_autonomous_cycle(fake_bus)
    state = fake_bus.cache_get("cache:driver:autonomous").payload
    sg = state["decisions"][0]["shadow_gate"]
    assert sg["n"] == 1 and sg["posture"] == "up" and sg["enabled"] is False


def test_cycle_log_row_reason_absent_is_none(fake_bus, monkeypatch):
    """A decision with no reason (legacy/back-compat) publishes reason=None."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": True, "day_thesis": "", "trades": []},
        "executable": [], "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    handlers.run_autonomous_cycle(fake_bus)
    state = fake_bus.cache_get("cache:driver:autonomous").payload
    assert state["decisions"][0]["reason"] is None


def test_cycle_enqueue_lands_on_cmd_options_stream(fake_bus, monkeypatch):
    """Read the stream back (no enqueue_command monkeypatch) → real round-trip."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": False, "day_thesis": "bull", "trades": []},
        "executable": [{"id": "m0", "signal": {"symbol": "SPX", "structure": "IC"},
                        "qty": 1, "rationale": ""}],
        "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    handlers.run_autonomous_cycle(fake_bus)
    entries = fake_bus._r.xrange("cmd:options")
    assert len(entries) == 1
    _msg_id, fields = entries[0]
    payload = json.loads(fields["data"])
    assert payload["type"] == "driver_paper_create"
    assert payload["args"]["qty"] == 1
    assert payload["args"]["signal"]["source"] == "driver"


def test_cycle_reads_driver_account_for_day_pnl(fake_bus, monkeypatch):
    """Day-P&L is sourced from the DRIVER paper book (cache:options:driver_paper_account),
    NOT the manual account — the cycle passes the driver view to run_cycle."""
    handlers.set_control(fake_bus, enabled=True)
    fake_bus.cache_set("cache:options:scan",
                       {"signals_0dte": [], "signals_swing": []})
    # A manual account with a DIFFERENT pnl that must be ignored.
    fake_bus.cache_set("cache:options:paper_account",
                       {"snapshot": {"session_pnl": 999.0}, "positions": [{"symbol": "MANUAL"}]})
    fake_bus.cache_set("cache:options:driver_paper_account",
                       {"snapshot": {"session_pnl": 250.0}, "positions": [{"symbol": "DRV"}]})
    seen = {}

    def _capture_cycle(scan_view, paper_view, **k):
        seen["paper_view"] = paper_view
        from services.driver_svc import compute as _c
        return {"decision": {"stand_down": True, "day_thesis": "t", "trades": []},
                "executable": [], "rejected": [], "halted": False, "halt_reason": None,
                "day_pnl": _c._day_pnl(paper_view),
                "open_positions": (paper_view or {}).get("positions", [])}

    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", _capture_cycle)
    handlers.run_autonomous_cycle(fake_bus)
    # run_cycle received the DRIVER account view (session_pnl 250, DRV position).
    assert seen["paper_view"]["snapshot"]["session_pnl"] == 250.0
    assert seen["paper_view"]["positions"][0]["symbol"] == "DRV"
    env = fake_bus.cache_get("cache:driver:autonomous")
    assert env.payload["day_pnl"] == 250.0          # driver book, not the manual 999


def test_cycle_attaches_driver_perf_to_monitor(fake_bus, monkeypatch):
    """The driver-account performance scorecard (cache:options:driver_paper_perf) is
    attached to the published AutonomousState.perf."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    fake_bus.cache_set("cache:options:driver_paper_perf",
                       {"total_trades": 4, "win_rate": 0.5, "profit_factor": 2.5})
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": True, "day_thesis": "", "trades": []},
        "executable": [], "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    handlers.run_autonomous_cycle(fake_bus)
    env = fake_bus.cache_get("cache:driver:autonomous")
    assert env.payload["perf"]["total_trades"] == 4
    assert env.payload["perf"]["win_rate"] == 0.5


def test_cycle_perf_defaults_empty_when_unpublished(fake_bus, monkeypatch):
    """A driver-perf view that has never been published → perf == {} (None-guard)."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": True, "day_thesis": "", "trades": []},
        "executable": [], "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    handlers.run_autonomous_cycle(fake_bus)
    env = fake_bus.cache_get("cache:driver:autonomous")
    assert env.payload["perf"] == {}


def test_cycle_does_not_mutate_raw_menu_signal(fake_bus, monkeypatch):
    """The cached menu signal must NOT gain ``source`` — only the enqueued COPY."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    raw_signal = {"symbol": "QQQ", "structure": "PCS", "max_loss": 200}
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": False, "day_thesis": "t", "trades": []},
        "executable": [{"id": "m0", "signal": raw_signal, "qty": 1, "rationale": ""}],
        "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    enq = []
    monkeypatch.setattr(fake_bus, "enqueue_command",
                        lambda stream, cmd: enq.append((stream, cmd)))
    handlers.run_autonomous_cycle(fake_bus)
    # The original signal object is untouched.
    assert "source" not in raw_signal
    # The enqueued one carries the tag (a distinct dict).
    enqueued_sig = enq[0][1]["args"]["signal"]
    assert enqueued_sig["source"] == "driver"
    assert enqueued_sig is not raw_signal


def test_cycle_partial_enqueue_failure_still_publishes(fake_bus, monkeypatch):
    """A mid-loop enqueue failure must NOT skip the publish, and the audit log must
    record only what ACTUALLY fired (M1 — no silent partial execution)."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": False, "day_thesis": "two", "trades": []},
        "executable": [
            {"id": "m0", "signal": {"symbol": "QQQ", "structure": "PCS"}, "qty": 1, "rationale": ""},
            {"id": "m1", "signal": {"symbol": "SPX", "structure": "IC"}, "qty": 1, "rationale": ""}],
        "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    calls = {"n": 0}

    def _flaky(stream, cmd):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("bus down")

    monkeypatch.setattr(fake_bus, "enqueue_command", _flaky)
    handlers.run_autonomous_cycle(fake_bus)   # must not raise
    env = fake_bus.cache_get("cache:driver:autonomous")
    assert env is not None                                   # published despite the failure
    executed = env.payload["decisions"][0]["executed"]
    assert len(executed) == 1 and executed[0]["symbol"] == "QQQ"  # only what fired


def test_cycle_honors_stop_landed_mid_cycle(fake_bus, monkeypatch):
    """A STOP that lands DURING the (slow) cycle is honored — the already-decided
    trades are NOT fired (kill-switch tightening; the top gate only catches a STOP
    from before the cycle)."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})

    def _slow_cycle(*a, **k):
        # Simulate the user hitting STOP during the cycle's Claude call.
        handlers.set_control(fake_bus, halted=True, reason="manual STOP")
        return {"decision": {"stand_down": False, "day_thesis": "t", "trades": []},
                "executable": [{"id": "m0", "signal": {"symbol": "QQQ", "structure": "PCS"},
                                "qty": 1, "rationale": ""}],
                "rejected": [], "halted": False, "halt_reason": None,
                "day_pnl": 0.0, "open_positions": []}

    monkeypatch.setattr(handlers.compute, "run_cycle", _slow_cycle)
    enq = []
    monkeypatch.setattr(fake_bus, "enqueue_command", lambda s, c: enq.append((s, c)))
    handlers.run_autonomous_cycle(fake_bus)
    assert enq == []   # STOP mid-cycle → nothing fired
    # The published view records nothing executed.
    env = fake_bus.cache_get("cache:driver:autonomous")
    assert env is not None and env.payload["decisions"][0]["executed"] == []


def test_cycle_stand_down_enqueues_nothing_but_publishes(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus, day_pnl=120.0)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": True, "day_thesis": "no edge", "trades": []},
        "executable": [], "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 120.0, "open_positions": []})
    enq = []
    monkeypatch.setattr(fake_bus, "enqueue_command",
                        lambda stream, cmd: enq.append((stream, cmd)))
    handlers.run_autonomous_cycle(fake_bus)
    assert enq == []
    env = fake_bus.cache_get("cache:driver:autonomous")
    assert env is not None
    assert env.payload["day_pnl"] == 120.0
    assert env.payload["decisions"][0]["thesis"] == "no edge"


def test_cycle_halt_latches_control(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus, day_pnl=600.0)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": True, "trades": []},
        "executable": [], "rejected": [],
        "halted": True, "halt_reason": "Target reached",
        "day_pnl": 600.0, "open_positions": []})
    handlers.run_autonomous_cycle(fake_bus)
    control = handlers.read_control(fake_bus)
    assert control["halted"] is True
    assert control["reason"] == "Target reached"
    assert control["halted_date"]  # stamped with today
    # The monitor view reflects the halt too.
    env = fake_bus.cache_get("cache:driver:autonomous")
    assert env.payload["halted"] is True
    assert env.payload["halt_reason"] == "Target reached"


def test_cycle_publishes_autonomous_event(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    _stub_cycle(monkeypatch, {
        "decision": {"stand_down": True, "day_thesis": "", "trades": []},
        "executable": [], "rejected": [], "halted": False, "halt_reason": None,
        "day_pnl": 0.0, "open_positions": []})
    sub = fake_bus.subscribe("events:driver:autonomous")
    handlers.run_autonomous_cycle(fake_bus)
    msg = sub.get_message(timeout=1.0)
    sub.close()
    assert msg is not None and "version" in msg


def test_decision_log_is_newest_first_and_capped(fake_bus, monkeypatch):
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    # Run 55 cycles with a distinguishable thesis each; log must cap at 50, newest first.
    for i in range(55):
        _stub_cycle(monkeypatch, {
            "decision": {"stand_down": True, "day_thesis": f"cycle-{i}", "trades": []},
            "executable": [], "rejected": [], "halted": False, "halt_reason": None,
            "day_pnl": 0.0, "open_positions": []})
        handlers.run_autonomous_cycle(fake_bus)
    log = fake_bus.cache_get("cache:driver:autonomous").payload["decisions"]
    assert len(log) == 50
    assert log[0]["thesis"] == "cycle-54"   # newest first
    assert log[-1]["thesis"] == "cycle-5"   # oldest retained (55 total, cap 50)


# ── Task 3: market_state merged from cache:sentiment:composite (context only) ─


def _capture_market_cycle(seen):
    def _capture(scan_view, paper_view, *, target, limits, market, **k):
        seen["market"] = market
        return {"decision": {"stand_down": True, "day_thesis": "", "trades": []},
                "executable": [], "rejected": [], "halted": False, "halt_reason": None,
                "day_pnl": 0.0, "open_positions": []}
    return _capture


def test_cycle_merges_market_state_from_composite(fake_bus, monkeypatch):
    """The handler reads cache:sentiment:composite derived.trend and merges the
    five-state {state,label,evidence} into the market context passed to run_cycle."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    fake_bus.cache_set("cache:sentiment:composite", {"derived": {"trend": {
        "state": "lack_of_bearishness", "label": "Lack of Bearishness",
        "description": "resilient, puts undefended",
        "evidence": ["put-skew Δ -1.2", "aggression +0.30"]}}})
    seen = {}
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", _capture_market_cycle(seen))
    handlers.run_autonomous_cycle(fake_bus)
    ms = seen["market"]["market_state"]
    assert ms["label"] == "Lack of Bearishness"
    assert ms["state"] == "lack_of_bearishness"
    assert "put-skew Δ -1.2" in ms["evidence"]
    # The existing market context (vix) is preserved alongside.
    assert seen["market"]["vix"] == 14


def test_cycle_market_state_absent_when_no_composite(fake_bus, monkeypatch):
    """No composite published → market carries no market_state (graceful, no crash)."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)   # composite deliberately NOT seeded
    seen = {}
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", _capture_market_cycle(seen))
    handlers.run_autonomous_cycle(fake_bus)
    assert "market_state" not in seen["market"]


def test_cycle_market_state_absent_when_trend_blank(fake_bus, monkeypatch):
    """A composite whose derived.trend has no state/label → no market_state (defensive)."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    fake_bus.cache_set("cache:sentiment:composite", {"derived": {"trend": {}}})
    seen = {}
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {})
    monkeypatch.setattr(handlers.compute, "run_cycle", _capture_market_cycle(seen))
    handlers.run_autonomous_cycle(fake_bus)
    assert "market_state" not in seen["market"]


# ── market-read: handler folds briefing + dashboard + sentiment into market ───
def _today_iso():
    import datetime as _dt
    return _dt.date.today().isoformat()


def test_cycle_folds_market_read_sources(fake_bus, monkeypatch):
    """The handler reads the freshest gamma briefing + market dashboard + sentiment
    magnitude and merges them into the market context passed to run_cycle."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    fake_bus.cache_set("cache:options:gamma_analyze_midday", {
        "slot": "midday", "generated_at": _today_iso() + "T12:30:00-05:00",
        "analysis": {"bias": -35, "regime": "neg gamma", "indices": [
            {"symbol": "$SPX", "gamma_flip": 6005, "put_wall": 5900, "call_wall": 6050}]}})
    fake_bus.cache_set("cache:market:dashboard", {"categories": [{"category": "B", "tiles": [
        {"display": "$ADVN-$DECN", "last": -620.0, "color_state": "risk_off_mild"}]}]})
    fake_bus.cache_set("cache:sentiment:composite",
                       {"live": {"composite": {"total_score": "4.1", "bias": "bearish"}},
                        "derived": {"trend": {}}})
    seen = {}
    monkeypatch.setattr(handlers.compute, "fetch_market_context",
                        lambda: {"vix": 14, "spx_spot": 5980.0})
    monkeypatch.setattr(handlers.compute, "run_cycle", _capture_market_cycle(seen))
    handlers.run_autonomous_cycle(fake_bus)
    market = seen["market"]
    assert market["briefing"]["_slot"] == "midday" and market["briefing"]["bias"] == -35
    assert market["dashboard"]["categories"][0]["tiles"][0]["last"] == -620.0
    assert market["sentiment"] == {"score": 4.1, "bias": "bearish"}
    assert market["vix"] == 14 and market["spx_spot"] == 5980.0   # preserved


def test_cycle_market_read_sources_absent_graceful(fake_bus, monkeypatch):
    """None of the market-read caches published → market carries none of them (no crash)."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)   # no briefing / dashboard / composite seeded
    seen = {}
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", _capture_market_cycle(seen))
    handlers.run_autonomous_cycle(fake_bus)
    for k in ("briefing", "dashboard", "sentiment"):
        assert k not in seen["market"]


def test_cycle_drops_prior_day_briefing(fake_bus, monkeypatch):
    """A yesterday-only gamma briefing is NOT folded in (stale walls mislead)."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    fake_bus.cache_set("cache:options:gamma_analyze_close", {
        "slot": "close", "generated_at": "2020-01-02T14:58:00-05:00",   # long ago
        "analysis": {"bias": -10, "indices": []}})
    seen = {}
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", _capture_market_cycle(seen))
    handlers.run_autonomous_cycle(fake_bus)
    assert "briefing" not in seen["market"]


# ── cumulative MTD banking target ────────────────────────────────────────────
def _capture_target_cycle(seen):
    def _capture(scan_view, paper_view, *, target, limits, market, **k):
        seen["target"] = target
        seen["limit_target"] = limits["daily_target"]
        return {"decision": {"stand_down": True, "day_thesis": "", "trades": []},
                "executable": [], "rejected": [], "halted": False, "halt_reason": None,
                "day_pnl": 0.0, "open_positions": []}
    return _capture


def test_cycle_uses_cumulative_mtd_target(fake_bus, monkeypatch):
    import datetime as _dt
    handlers.set_control(fake_bus, enabled=True)
    fake_bus.cache_set("cache:options:scan", {"signals_0dte": [], "signals_swing": []})
    # Driver book: behind the MTD pace (a loss earlier this month) -> ratchet toward cap.
    m = _dt.date.today().strftime("%Y-%m")
    fake_bus.cache_set("cache:options:driver_paper_account", {
        "snapshot": {"session_pnl": 0.0}, "positions": [],
        "closed_positions": [{"realized_pnl": -300.0, "exit_ts": f"{m}-01T15:00:00-05:00"}]})
    seen = {}
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr(handlers.compute, "run_cycle", _capture_target_cycle(seen))
    handlers.run_autonomous_cycle(fake_bus)
    # behind pace -> target > base, clamped <= cap; halt_state uses the SAME value.
    assert 500 < seen["target"] <= 1000
    assert seen["limit_target"] == seen["target"]
    # ...and the monitor view's target reflects the dynamic value.
    assert fake_bus.cache_get("cache:driver:autonomous").payload["target"] == seen["target"]


def test_cycle_target_falls_back_to_base_on_error(fake_bus, monkeypatch):
    """A failure computing the MTD target must degrade to the flat base (never block)."""
    handlers.set_control(fake_bus, enabled=True)
    fake_bus.cache_set("cache:options:scan", {"signals_0dte": [], "signals_swing": []})
    fake_bus.cache_set("cache:options:driver_paper_account", {"snapshot": {}, "positions": []})
    monkeypatch.setattr(handlers.compute, "_mtd_trading_days",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    seen = {}
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {})
    monkeypatch.setattr(handlers.compute, "run_cycle", _capture_target_cycle(seen))
    handlers.run_autonomous_cycle(fake_bus)
    assert seen["target"] == handlers.settings.DAILY_TARGET   # 500 fallback


def test_publish_autonomous_stamps_market_read_summary(fake_bus):
    """The market_read one-line summary lands on the newest decision-log row (/driver
    observability) so the log shows what the model saw."""
    handlers._publish_autonomous(
        fake_bus, day_pnl=0.0, positions=[],
        decision={"day_thesis": "t", "stand_down": True},
        guarded={"rejected": [], "halted": False, "halt_reason": None}, executed=[],
        control={"enabled": True, "halted": False},
        market_read={"summary": "neg gamma · bias -35 · breadth -620 risk_off · sent 4.1"})
    row = fake_bus.cache_get("cache:driver:autonomous").payload["decisions"][0]
    assert "bias -35" in row["market_read"]


def test_publish_autonomous_market_read_absent_is_none(fake_bus):
    """No market_read passed → the log row's market_read is None (back-compat)."""
    handlers._publish_autonomous(
        fake_bus, day_pnl=0.0, positions=[],
        decision={"day_thesis": "t", "stand_down": True},
        guarded={"rejected": [], "halted": False, "halt_reason": None}, executed=[],
        control={"enabled": True, "halted": False})
    row = fake_bus.cache_get("cache:driver:autonomous").payload["decisions"][0]
    assert row["market_read"] is None


def test_cycle_end_to_end_stamps_market_read_on_log(fake_bus, monkeypatch):
    """Through the REAL run_cycle: a seeded dashboard yields a market_read whose summary
    is published on the decision-log row."""
    handlers.set_control(fake_bus, enabled=True)
    _seed_caches(fake_bus)
    fake_bus.cache_set("cache:market:dashboard", {"categories": [{"category": "B", "tiles": [
        {"display": "$ADVN-$DECN", "last": -620.0, "color_state": "risk_off_mild"}]}]})
    monkeypatch.setattr(handlers.compute, "fetch_market_context", lambda: {"vix": 14})
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda packet, **kw: {"stand_down": True, "trades": []})
    handlers.run_autonomous_cycle(fake_bus)
    row = fake_bus.cache_get("cache:driver:autonomous").payload["decisions"][0]
    assert row["market_read"] and "risk_off" in row["market_read"]


# ── 5.3: autonomous command dispatch (cycle/enable/disable/stop) ─────────────


def test_handle_command_enable_disable_stop(fake_bus):
    handlers.handle_command(fake_bus, _cmd("enable"))
    assert handlers.read_control(fake_bus)["enabled"] is True
    handlers.handle_command(fake_bus, _cmd("stop"))
    assert handlers.read_control(fake_bus)["halted"] is True
    handlers.handle_command(fake_bus, _cmd("disable"))
    assert handlers.read_control(fake_bus)["enabled"] is False


def test_handle_command_stop_sets_reason_and_date(fake_bus):
    handlers.handle_command(fake_bus, _cmd("enable"))
    handlers.handle_command(fake_bus, _cmd("stop"))
    c = handlers.read_control(fake_bus)
    assert c["halted"] is True
    assert c["reason"] == "manual STOP"
    assert c["halted_date"]  # ISO date stamped


def test_handle_command_enable_clears_prior_halt(fake_bus):
    # A halted-but-enabled control re-arms on a fresh enable.
    handlers.set_control(fake_bus, enabled=True)
    handlers.set_control(fake_bus, halted=True, reason="banked")
    handlers.handle_command(fake_bus, _cmd("enable"))
    c = handlers.read_control(fake_bus)
    assert c["enabled"] is True and c["halted"] is False and c["reason"] is None


def test_handle_command_cycle_runs_autonomous(fake_bus, monkeypatch):
    called = []
    monkeypatch.setattr(handlers, "run_autonomous_cycle",
                        lambda bus: called.append(bus))
    handlers.handle_command(fake_bus, _cmd("cycle"))
    assert called == [fake_bus]


def test_handle_command_unknown_type_is_noop(fake_bus):
    """An unknown command type (incl. the removed legacy run/approve/skip/perf) is
    a silent no-op — nothing published, no crash."""
    for t in ("run", "approve", "skip", "perf", "bogus"):
        handlers.handle_command(fake_bus, _cmd(t))
    assert fake_bus.cache_get("cache:driver:autonomous") is None
    assert fake_bus.cache_get("cache:driver:control") is None
