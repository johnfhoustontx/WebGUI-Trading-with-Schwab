"""End-to-end autonomous cycle test (Phase 8.1) — the REAL pipeline seam.

Unlike ``test_handlers_autonomous.py`` (which stubs ``compute.run_cycle`` to focus
on the bus plumbing), this drives the WHOLE pipeline through the genuine
``handlers.run_autonomous_cycle`` → ``compute.run_cycle`` → ``compute.build_packet``
→ ``guardrails.apply_guardrails`` path. Only the two true externalities are
stubbed:

* the LLM (``services.driver_svc.decider.decide`` — patched as a module attribute
  because ``compute.run_cycle`` imports ``decider`` *inside* its try), and
* the market fetch (``compute.fetch_market_context`` — no proxy in tests).

Everything else is real, so the headline assertion — the model asks for **3**
contracts but a ``paper_create`` lands with ``qty == 1`` — is proof the REAL
guardrail math ran (``per_trade_max_risk $3000 / $2000 per-contract = 1``, i.e.
the per-share ``max_loss 20`` x100), not a stub.
The signal is seeded with the REAL ``cache:options:scan`` field names
(``type``/``expiration``/``pop_pct``) verified against
``options-scanner/scanner_engine.py``.
"""
import json

from services.driver_svc import handlers


# A real-shaped allowed signal: structure in ``type``, expiry in ``expiration``,
# PoP in ``pop_pct`` (the scanner_engine.build_iron_condors / screen_spreads keys).
_QQQ_PCS = {
    "symbol": "QQQ",
    "type": "PCS",
    "max_loss": 20.0,        # per-SHARE; x100 = $2,000/contract (clamps qty 3 -> 1 at the $3,000 cap)
    "credit": 60.0,
    "pop_pct": 0.85,
    "composite_score": 80,
    "expiration": "2026-06-24",
}


def _seed_scan(fake_bus, signal):
    fake_bus.cache_set("cache:options:scan",
                       {"signals_0dte": [signal], "signals_swing": []})


def _seed_paper(fake_bus, session_pnl):
    # The autonomous cycle reads the ISOLATED driver paper book.
    fake_bus.cache_set("cache:options:driver_paper_account",
                       {"snapshot": {"session_pnl": session_pnl}, "positions": []})


def test_autonomous_e2e_clamps_and_executes(fake_bus, monkeypatch):
    """Happy path through the REAL run_cycle + guardrails.

    The model requests qty=3; the guardrails clamp it to 1 ($3000 per-trade /
    $2000 per-contract) and a ``driver_paper_create`` lands on ``cmd:options`` tagged
    ``source="driver"``; the monitor view records the executed QQQ trade.
    """
    handlers.set_control(fake_bus, enabled=True)
    _seed_scan(fake_bus, _QQQ_PCS)
    _seed_paper(fake_bus, session_pnl=0.0)

    monkeypatch.setattr(handlers.compute, "fetch_market_context",
                        lambda: {"vix": 14})
    # Patch the MODULE attribute (compute.run_cycle does `from ... import decider`
    # inside its try, then calls decider.decide) — the model "asks" for 3.
    monkeypatch.setattr(
        "services.driver_svc.decider.decide",
        lambda packet, **kw: {"stand_down": False, "day_thesis": "e2e",
                              "confidence": 0.7,
                              "trades": [{"id": "m0", "quantity": 3}]})

    handlers.run_autonomous_cycle(fake_bus)

    # (a) a driver_paper_create landed on cmd:options, CLAMPED to qty 1 (real guardrail math).
    entries = fake_bus._r.xrange("cmd:options")
    assert len(entries) == 1
    _msg_id, fields = entries[0]
    payload = json.loads(fields["data"])
    assert payload["type"] == "driver_paper_create"
    assert payload["args"]["qty"] == 1               # 3 → clamped to 1, not a stub
    assert payload["args"]["signal"]["source"] == "driver"
    assert payload["args"]["signal"]["symbol"] == "QQQ"

    # (b) the monitor view reflects the executed trade + day P&L.
    env = fake_bus.cache_get("cache:driver:autonomous")
    assert env is not None
    state = env.payload
    assert state["day_pnl"] == 0.0
    executed = state["decisions"][0]["executed"]
    assert len(executed) == 1
    assert executed[0]["symbol"] == "QQQ"
    assert executed[0]["qty"] == 1
    # Not halted on a fresh $0 day.
    assert state["halted"] is False
    assert handlers.read_control(fake_bus)["halted"] is False


def test_autonomous_e2e_banked_target_halts(fake_bus, monkeypatch):
    """Banked-target halt through the REAL pipeline.

    A session already +$600 (≥ $500 target) → ``halt_state`` trips inside the real
    guardrails, so NOTHING is enqueued, the kill-switch latches ``halted=True`` with
    the banked reason + today's date, and the monitor view shows the halt.
    """
    handlers.set_control(fake_bus, enabled=True)
    _seed_scan(fake_bus, _QQQ_PCS)
    _seed_paper(fake_bus, session_pnl=600.0)        # already banked the day

    monkeypatch.setattr(handlers.compute, "fetch_market_context",
                        lambda: {"vix": 14})
    # Even though the model proposes a trade, the halt blocks everything upstream.
    monkeypatch.setattr(
        "services.driver_svc.decider.decide",
        lambda packet, **kw: {"stand_down": False, "day_thesis": "greedy",
                              "confidence": 0.9,
                              "trades": [{"id": "m0", "quantity": 1}]})

    handlers.run_autonomous_cycle(fake_bus)

    # Nothing enqueued — the halt short-circuits before any paper_create.
    assert fake_bus._r.xrange("cmd:options") == []

    # The kill-switch latched, stamped with a halt reason + today's date.
    control = handlers.read_control(fake_bus)
    assert control["halted"] is True
    assert control["reason"] and "target" in control["reason"].lower()
    assert control["halted_date"]

    # The monitor view reflects the halt + the $600 day.
    state = fake_bus.cache_get("cache:driver:autonomous").payload
    assert state["halted"] is True
    assert state["halt_reason"] and "target" in state["halt_reason"].lower()
    assert state["day_pnl"] == 600.0


def test_autonomous_e2e_packet_carries_market_read(fake_bus, monkeypatch):
    """Through the REAL build_packet: seeded market-read sources reach the model-facing
    packet, and the /driver log row shows the summary."""
    import datetime as _dt

    handlers.set_control(fake_bus, enabled=True)
    _seed_scan(fake_bus, _QQQ_PCS)
    _seed_paper(fake_bus, session_pnl=0.0)
    fake_bus.cache_set("cache:market:dashboard", {"categories": [{"category": "B", "tiles": [
        {"display": "$ADVN-$DECN", "last": -620.0, "color_state": "risk_off_mild"}]}]})
    fake_bus.cache_set("cache:options:gamma_analyze_midday", {
        "slot": "midday", "generated_at": _dt.date.today().isoformat() + "T12:30:00-05:00",
        "analysis": {"bias": -35, "regime": "neg gamma", "indices": [
            {"symbol": "$SPX", "gamma_flip": 6005, "put_wall": 5900, "call_wall": 6050}]}})
    monkeypatch.setattr(handlers.compute, "fetch_market_context",
                        lambda: {"vix": 14, "spx_spot": 5980.0})
    seen = {}
    monkeypatch.setattr(
        "services.driver_svc.decider.decide",
        lambda packet, **kw: seen.setdefault("pkt", packet) or
        {"stand_down": True, "trades": []})

    handlers.run_autonomous_cycle(fake_bus)

    # (a) the REAL build_packet folded the market read into the model-facing packet.
    mr = seen["pkt"]["market_read"]
    assert mr["risk"] == "risk_off" and mr["breadth_spread"] == -620.0
    assert mr["indices"][0]["symbol"] == "$SPX"
    assert mr["indices"][0]["spot"] == 5980.0          # live spot, not the briefing's
    # (b) the model NEVER sees menu_by_id (raw signals).
    assert "menu_by_id" not in seen["pkt"]
    # (c) the /driver decision log row shows the one-line summary.
    row = fake_bus.cache_get("cache:driver:autonomous").payload["decisions"][0]
    assert row["market_read"] and "risk_off" in row["market_read"]
