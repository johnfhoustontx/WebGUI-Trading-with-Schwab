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
