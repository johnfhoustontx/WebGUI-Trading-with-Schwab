"""Tests for driver_svc.settings — the autonomous-driver tunables.

``limits()`` is the risk envelope the guardrails enforce; assert its shape and a
few sanity invariants (the daily risk budget must cover at least one max-risk
trade; the caps are positive) so a typo in a default surfaces here, not in a
live cycle.
"""
from services.driver_svc import settings


def test_limits_dict_shape():
    lim = settings.limits()
    assert lim["daily_target"] == 500.0
    assert lim["per_trade_max_risk"] > 0
    assert lim["daily_risk_budget"] >= lim["per_trade_max_risk"]
    assert lim["vix_max"] == 25.0
    assert lim["max_concurrent"] >= 1
    assert lim["max_trades_per_cycle"] >= 1
