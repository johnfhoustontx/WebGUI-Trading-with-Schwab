"""Tests for driver_svc.guardrails — the code-authoritative safety core (PURE).

These are plain pytest functions: the module performs no I/O, no network, and no
clock reads, so there is nothing to mock. The model PROPOSES trades; this module
DECIDES what executes — every test here pins a safety invariant (allowlist,
quantity clamp, halt condition, orchestration) so a regression in the risk
layer surfaces immediately, not in a live cycle.

Grows per task (2.1 → 2.4).
"""
from services.driver_svc import guardrails as g


# ---------------------------------------------------------------------------
# Task 2.1 — normalize_structure + is_allowed
# ---------------------------------------------------------------------------
def test_normalize_structure_canonicalizes():
    assert g.normalize_structure("put_credit_spread") == "PCS"
    assert g.normalize_structure("CALL_CREDIT_SPREAD") == "CCS"
    assert g.normalize_structure("iron_condor") == "IC"
    assert g.normalize_structure("PCS") == "PCS"


def test_is_allowed_only_defined_risk_spreads():
    assert g.is_allowed({"structure": "put_credit_spread", "max_loss": 250}) is True
    assert g.is_allowed({"structure": "naked_put", "max_loss": None}) is False
    assert g.is_allowed({"structure": "PCS", "max_loss": 0}) is False  # no real risk/credit


def test_signal_structure_public_reads_real_scanner_keys():
    """The public ``signal_structure`` reads ``structure`` → ``type`` → ``trade_type``.

    A REAL ``cache:options:scan`` signal stores the structure code in ``type``
    (``PCS``/``CCS``/``IC``) and uses ``trade_type`` for the DTE bucket — there is
    NO ``structure`` key. ``compute.build_packet`` reuses this to project the
    model-facing ``structure`` field, so it must classify the real-shaped signal.
    """
    # Real scanner signal: structure in ``type``, NO ``structure`` key.
    assert g.signal_structure({"type": "PCS", "max_loss": 200.0}) == "PCS"
    assert g.signal_structure({"type": "IC", "trade_type": "0-DTE"}) == "IC"
    # The projected menu item uses ``structure`` and still wins (read first).
    assert g.signal_structure({"structure": "call_credit_spread"}) == "CCS"
    # The DTE bucket in ``trade_type`` must NOT be mistaken for the structure.
    assert g.signal_structure({"trade_type": "0-DTE"}) not in g.ALLOWED
    assert g.signal_structure({}) == ""


def test_is_allowed_real_scanner_signal_shape():
    """A real ``type``-keyed PCS signal (no ``structure`` key) is allowed."""
    assert g.is_allowed({"type": "PCS", "max_loss": 200.0}) is True
    assert g.is_allowed({"type": "IC", "max_loss": 300.0}) is True
    # ``trade_type`` alone (DTE bucket, no structure) is not a tradeable structure.
    assert g.is_allowed({"trade_type": "0-DTE", "max_loss": 200.0}) is False


# ---------------------------------------------------------------------------
# Task 2.2 — clamp_quantity
#
# NOTE: a signal's ``max_loss`` is PER-SHARE (the scanner's units). clamp_quantity
# converts it to per-CONTRACT dollars (``* CONTRACT_MULTIPLIER`` = ``* 100``) before
# comparing to the dollar caps, so ``max_loss: 2.0`` below is a $200/contract spread.
# ---------------------------------------------------------------------------
def test_clamp_quantity_respects_per_trade_and_budget():
    sig = {"structure": "PCS", "max_loss": 2.0}   # $2/share x 100 = $200 risk per spread
    # requested 5, per-trade cap $300 → 1 spread; budget $900 → 4; → min(5,1,4)=1
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=300, remaining_budget=900) == 1
    # bigger per-trade cap: per-trade $1000 → 5; budget $900 → 4; req 5 → 4
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=1000, remaining_budget=900) == 4


def test_clamp_quantity_uses_per_contract_dollars():
    """REGRESSION: a $SPX-shaped spread (per-share max_loss 7.05 = $705/contract) is
    evaluated in per-contract DOLLARS. Under the old per-share bug clamp returned
    floor(300/7.05)=42 (capped to the request), so $SPX/MU slipped the driver only to
    be rejected RISK_TOO_HIGH by the paper account's per-contract sizer."""
    spx = {"structure": "CCS", "max_loss": 7.05}                  # $705 per contract
    assert g.clamp_quantity(spx, 2, per_trade_max_risk=300, remaining_budget=900) == 0
    # With a per-trade cap that actually covers it, contracts fit: floor(1500/705)=2.
    assert g.clamp_quantity(spx, 2, per_trade_max_risk=1500, remaining_budget=4500) == 2
    assert g.clamp_quantity(spx, 5, per_trade_max_risk=1500, remaining_budget=4500) == 2


def test_clamp_quantity_zero_when_unaffordable():
    sig = {"structure": "PCS", "max_loss": 10.0}   # $1000 per contract
    assert g.clamp_quantity(sig, 1, per_trade_max_risk=300, remaining_budget=900) == 0


def test_clamp_quantity_zero_on_bad_maxloss():
    assert g.clamp_quantity({"structure": "PCS", "max_loss": None}, 3, 300, 900) == 0


def test_clamp_quantity_hardening_bad_requested_qty():
    """A None / non-numeric / negative requested qty is a no-trade, not a crash."""
    sig = {"structure": "PCS", "max_loss": 2.0}   # $200 per contract
    assert g.clamp_quantity(sig, None, 1000, 900) == 0
    assert g.clamp_quantity(sig, "x", 1000, 900) == 0
    assert g.clamp_quantity(sig, -3, 1000, 900) == 0
    # A float request truncates toward zero (2.9 -> 2), then caps apply.
    assert g.clamp_quantity(sig, 2.9, 1000, 900) == 2


def test_clamp_quantity_budget_exactly_one_spread():
    """Remaining budget equal to exactly one spread's max-loss yields 1."""
    sig = {"structure": "PCS", "max_loss": 2.0}   # $200 per contract
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=1000, remaining_budget=200) == 1
    # A penny short of one spread → 0 (floor, never over-commit the budget).
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=1000, remaining_budget=199.99) == 0


def test_clamp_quantity_negative_max_loss():
    assert g.clamp_quantity({"structure": "PCS", "max_loss": -50.0}, 3, 300, 900) == 0


def test_clamp_quantity_nonfinite_inputs_never_raise():
    """NaN/inf in max_loss OR the budget args → 0 (a malformed chain can't crash)."""
    nan, inf = float("nan"), float("inf")
    assert g.clamp_quantity({"structure": "PCS", "max_loss": nan}, 5, 1000, 900) == 0
    assert g.clamp_quantity({"structure": "PCS", "max_loss": inf}, 5, 1000, 900) == 0
    sig = {"structure": "PCS", "max_loss": 2.0}   # $200 per contract
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=nan, remaining_budget=900) == 0
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=1000, remaining_budget=inf) == 0


def test_is_allowed_rejects_nonfinite_max_loss():
    """NaN/inf max_loss must fail the allowlist (NaN would otherwise slip `> 0`)."""
    assert g.is_allowed({"structure": "PCS", "max_loss": float("nan")}) is False
    assert g.is_allowed({"structure": "PCS", "max_loss": float("inf")}) is False


# ---------------------------------------------------------------------------
# Task 2.3 — halt_state
# ---------------------------------------------------------------------------
def test_halt_banked_at_target():
    halted, reason = g.halt_state(day_pnl=520, target=500, daily_max_loss=250, vix=14)
    assert halted and "target" in reason.lower()


def test_halt_loss_cap():
    halted, reason = g.halt_state(day_pnl=-260, target=500, daily_max_loss=250, vix=14)
    assert halted and "loss" in reason.lower()


def test_halt_vix():
    halted, reason = g.halt_state(day_pnl=0, target=500, daily_max_loss=250, vix=26)
    assert halted and "vix" in reason.lower()


def test_no_halt_in_normal_range():
    assert g.halt_state(day_pnl=120, target=500, daily_max_loss=250, vix=15) == (False, None)


# ---------------------------------------------------------------------------
# Task 2.4 — apply_guardrails (the orchestrator)
# ---------------------------------------------------------------------------
def _menu():
    return {
        "m0": {"id": "m0", "structure": "PCS", "max_loss": 2.0, "symbol": "QQQ"},   # $200/contract
        "m1": {"id": "m1", "structure": "naked_put", "max_loss": None, "symbol": "X"},
        "m2": {"id": "m2", "structure": "IC", "max_loss": 3.0, "symbol": "SPX"},     # $300/contract
    }


def g_limits():
    return {"daily_target": 500.0, "per_trade_max_risk": 300.0, "daily_risk_budget": 900.0,
            "max_concurrent": 6, "max_trades_per_cycle": 3, "vix_max": 25.0}


def test_apply_stand_down():
    out = g.apply_guardrails({"stand_down": True, "trades": []}, _menu(),
                             g_limits(), open_count=0, day_pnl=0)
    assert out["executable"] == [] and out["halted"] is False


def test_apply_halts_block_everything():
    out = g.apply_guardrails({"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]},
                             _menu(), g_limits(), open_count=0, day_pnl=600)  # banked
    assert out["halted"] is True and out["executable"] == []


def test_apply_rejects_offmenu_and_disallowed_and_clamps():
    decision = {"stand_down": False, "trades": [
        {"id": "m9", "quantity": 1},          # off-menu → reject
        {"id": "m1", "quantity": 1},          # disallowed structure → reject
        {"id": "m0", "quantity": 99},         # clamp to budget/per-trade
    ]}
    out = g.apply_guardrails(decision, _menu(), g_limits(), open_count=0, day_pnl=0)
    ids = [t["id"] for t in out["executable"]]
    assert "m0" in ids and "m1" not in ids and "m9" not in ids
    m0 = next(t for t in out["executable"] if t["id"] == "m0")
    assert m0["qty"] >= 1
    assert any(r["id"] == "m9" for r in out["rejected"])


def test_apply_respects_max_trades_and_concurrent():
    decision = {"stand_down": False, "trades": [
        {"id": "m0", "quantity": 1}, {"id": "m2", "quantity": 1}, {"id": "m0", "quantity": 1},
    ]}
    lim = g_limits(); lim["max_trades_per_cycle"] = 1
    out = g.apply_guardrails(decision, _menu(), lim, open_count=0, day_pnl=0)
    assert len(out["executable"]) == 1


def test_apply_no_slots_when_concurrent_already_full():
    """open_count >= max_concurrent leaves zero slots → everything rejected."""
    decision = {"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]}
    out = g.apply_guardrails(decision, _menu(), g_limits(), open_count=6, day_pnl=0)
    assert out["executable"] == []
    assert out["rejected"][0]["reason"] == "max trades/concurrent reached"
    # And open_count OVER the cap must not produce negative slots / crash.
    out2 = g.apply_guardrails(decision, _menu(), g_limits(), open_count=10, day_pnl=0)
    assert out2["executable"] == []


def test_apply_tracks_budget_across_trades():
    """The remaining risk budget is decremented across surviving trades."""
    lim = g_limits(); lim["daily_risk_budget"] = 350.0  # room for one $200 spread, not two
    decision = {"stand_down": False, "trades": [
        {"id": "m0", "quantity": 1},   # takes $200 → leaves $150
        {"id": "m0", "quantity": 1},   # floor(150/200)=0 → unaffordable
    ]}
    out = g.apply_guardrails(decision, _menu(), lim, open_count=0, day_pnl=0)
    assert len(out["executable"]) == 1 and out["executable"][0]["qty"] == 1
    assert any(r["reason"] == "unaffordable within remaining budget" for r in out["rejected"])


def test_apply_empty_or_missing_trades_is_safe():
    """No trades (empty list or absent key) → empty executable, never raises."""
    base = g_limits()
    assert g.apply_guardrails({"stand_down": False, "trades": []}, _menu(), base,
                              open_count=0, day_pnl=0)["executable"] == []
    out = g.apply_guardrails({"stand_down": False}, _menu(), base, open_count=0, day_pnl=0)
    assert out["executable"] == [] and out["halted"] is False


def test_apply_idless_trade_rejected_offmenu():
    """A trade dict with no id can't resolve → off-menu reject (never executes)."""
    decision = {"stand_down": False, "trades": [{"quantity": 2}]}
    out = g.apply_guardrails(decision, _menu(), g_limits(), open_count=0, day_pnl=0)
    assert out["executable"] == []
    assert out["rejected"][0]["reason"] == "off-menu (no matching signal)"


def test_apply_executable_carries_full_signal_and_rationale():
    """Survivors carry the FULL scanner signal (for verbatim enqueue) + rationale."""
    menu = _menu()
    menu["m2"]["extra_field"] = "must-survive"   # any signal field must pass through
    decision = {"stand_down": False,
                "trades": [{"id": "m2", "quantity": 1, "rationale": "high pop IC"}]}
    out = g.apply_guardrails(decision, menu, g_limits(), open_count=0, day_pnl=0)
    t = out["executable"][0]
    assert t["signal"] is menu["m2"]                      # same object, enqueued verbatim
    assert t["signal"]["extra_field"] == "must-survive"   # no field dropped
    assert t["rationale"] == "high pop IC" and t["qty"] >= 1


# ── directional wrong-side gate (2026-07-09) ─────────────────────────────────
def _gdec(mid):
    return {"stand_down": False, "trades": [{"id": mid, "quantity": 1}]}


def test_side_blocked_matrix():
    ccs = {"type": "CCS", "max_loss": 2.0}
    pcs = {"type": "PCS", "max_loss": 2.0}
    ic = {"type": "IC", "max_loss": 2.0}
    assert g._side_blocked(ccs, "up") and not g._side_blocked(ccs, "down")
    assert g._side_blocked(pcs, "down") and not g._side_blocked(pcs, "up")
    assert not g._side_blocked(ic, "up") and not g._side_blocked(ic, "down")   # IC exempt
    for s in (ccs, pcs, ic):
        assert not g._side_blocked(s, "neutral")                              # neutral → nothing


def test_apply_guardrails_blocks_wrong_side_ccs_when_up():
    menu = {"m0": {"type": "CCS", "max_loss": 2.0}}
    out = g.apply_guardrails(_gdec("m0"), menu, g_limits(), open_count=0, day_pnl=0.0,
                             vix=14, posture="up")
    assert out["executable"] == []
    assert out["rejected"][0]["reason"] == g.WRONG_SIDE_REGIME


def test_apply_guardrails_blocks_wrong_side_pcs_when_down():
    menu = {"m0": {"type": "PCS", "max_loss": 2.0}}
    out = g.apply_guardrails(_gdec("m0"), menu, g_limits(), open_count=0, day_pnl=0.0,
                             vix=14, posture="down")
    assert out["executable"] == [] and out["rejected"][0]["reason"] == g.WRONG_SIDE_REGIME


def test_apply_guardrails_allows_right_side_pcs_when_up():
    """A PCS (short puts, bullish) is the RIGHT side in an up tape — it still trades."""
    menu = {"m0": {"type": "PCS", "max_loss": 2.0}}
    out = g.apply_guardrails(_gdec("m0"), menu, g_limits(), open_count=0, day_pnl=0.0,
                             vix=14, posture="up")
    assert len(out["executable"]) == 1 and out["executable"][0]["qty"] == 1


def test_apply_guardrails_neutral_and_default_are_backcompat():
    """Default posture (neutral) → the gate is inert; a CCS trades exactly as before."""
    menu = {"m0": {"type": "CCS", "max_loss": 2.0}}
    for kw in ({}, {"posture": "neutral"}):
        out = g.apply_guardrails(_gdec("m0"), menu, g_limits(), open_count=0, day_pnl=0.0,
                                 vix=14, **kw)
        assert len(out["executable"]) == 1


# ── shadow directional gate (log-only evidence, 2026-07-13) ──────────────────
def test_shadow_gate_flags_wrong_side_survivors():
    """A CCS that FIRED (inert gate) is flagged as would-block when posture is up."""
    executable = [{"id": "m0", "signal": {"symbol": "SPY", "type": "CCS", "max_loss": 2.0},
                   "qty": 1}]
    sg = g.shadow_gate(executable, "up")
    assert sg["n"] == 1 and sg["posture"] == "up"
    assert sg["would_block"][0] == {"id": "m0", "symbol": "SPY", "structure": "CCS"}


def test_shadow_gate_empty_when_right_side_or_neutral():
    executable = [{"id": "m0", "signal": {"symbol": "SPY", "type": "PCS", "max_loss": 2.0},
                   "qty": 1}]
    assert g.shadow_gate(executable, "up")["n"] == 0       # PCS right-side in up tape
    assert g.shadow_gate(executable, "neutral")["n"] == 0  # indecisive → flags nothing


def test_shadow_gate_tolerates_sparse_rows():
    """Never raises on None / non-dict rows / missing signal (defensive)."""
    for bad in (None, [], [None, "x", {}, {"signal": None}, {"signal": "x"}]):
        sg = g.shadow_gate(bad, "up")
        assert sg["n"] == 0 and sg["would_block"] == []


def test_wrong_side_block_does_not_consume_slot_or_budget():
    """A blocked wrong-side trade must not eat a concurrent slot — a following right-side
    trade in the SAME cycle still executes."""
    menu = {"m0": {"type": "CCS", "max_loss": 2.0}, "m1": {"type": "PCS", "max_loss": 2.0}}
    dec = {"stand_down": False, "trades": [{"id": "m0", "quantity": 1},
                                           {"id": "m1", "quantity": 1}]}
    out = g.apply_guardrails(dec, menu, g_limits(), open_count=0, day_pnl=0.0,
                             vix=14, posture="up")
    assert [t["id"] for t in out["executable"]] == ["m1"]          # CCS blocked, PCS runs
    assert out["rejected"][0]["reason"] == g.WRONG_SIDE_REGIME
