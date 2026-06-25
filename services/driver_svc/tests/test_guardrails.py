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


# ---------------------------------------------------------------------------
# Task 2.2 — clamp_quantity
# ---------------------------------------------------------------------------
def test_clamp_quantity_respects_per_trade_and_budget():
    sig = {"structure": "PCS", "max_loss": 200.0}   # $200 risk per spread
    # requested 5, per-trade cap $300 → 1 spread; budget $900 → 4; → min(5,1,4)=1
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=300, remaining_budget=900) == 1
    # bigger per-trade cap: per-trade $1000 → 5; budget $900 → 4; req 5 → 4
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=1000, remaining_budget=900) == 4


def test_clamp_quantity_zero_when_unaffordable():
    sig = {"structure": "PCS", "max_loss": 1000.0}
    assert g.clamp_quantity(sig, 1, per_trade_max_risk=300, remaining_budget=900) == 0


def test_clamp_quantity_zero_on_bad_maxloss():
    assert g.clamp_quantity({"structure": "PCS", "max_loss": None}, 3, 300, 900) == 0


def test_clamp_quantity_hardening_bad_requested_qty():
    """A None / non-numeric / negative requested qty is a no-trade, not a crash."""
    sig = {"structure": "PCS", "max_loss": 200.0}
    assert g.clamp_quantity(sig, None, 1000, 900) == 0
    assert g.clamp_quantity(sig, "x", 1000, 900) == 0
    assert g.clamp_quantity(sig, -3, 1000, 900) == 0
    # A float request truncates toward zero (2.9 -> 2), then caps apply.
    assert g.clamp_quantity(sig, 2.9, 1000, 900) == 2


def test_clamp_quantity_budget_exactly_one_spread():
    """Remaining budget equal to exactly one spread's max-loss yields 1."""
    sig = {"structure": "PCS", "max_loss": 200.0}
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=1000, remaining_budget=200) == 1
    # A penny short of one spread → 0 (floor, never over-commit the budget).
    assert g.clamp_quantity(sig, 5, per_trade_max_risk=1000, remaining_budget=199.99) == 0


def test_clamp_quantity_negative_max_loss():
    assert g.clamp_quantity({"structure": "PCS", "max_loss": -50.0}, 3, 300, 900) == 0
