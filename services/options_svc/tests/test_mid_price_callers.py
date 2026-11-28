"""The options-service callers of shared.quotes (2026-11-28)."""
import math

import pytest

from services.options_svc import compute, flow_alerts, tools_public

NAN, INF = float("nan"), float("inf")


def _chain(c):
    return {"putExpDateMap": {"2026-12-18:21": {"500.0": [c]}}}


class TestLegMidFromChain:
    def test_a_normal_quote(self):
        assert compute._leg_mid_from_chain(
            _chain({"bid": 1.0, "ask": 1.1}), "PUT", 500.0) == pytest.approx(1.05)

    @pytest.mark.parametrize("c", [
        {"bid": 1.2, "ask": 1.0},
        {"bid": NAN, "ask": 1.1},
        {"bid": 1.0, "ask": INF},
        {"bid": True, "ask": 1.1},
    ])
    def test_no_market_is_none(self, c):
        assert compute._leg_mid_from_chain(_chain(c), "PUT", 500.0) is None


class TestFlowAlertMark:
    def test_a_zero_bid_under_any_offer_is_a_market(self):
        assert flow_alerts._mark({"bid": 0.0, "ask": 1.2}) == pytest.approx(0.6)

    @pytest.mark.parametrize("c", [
        {"bid": 1.0, "ask": 0.0},
        {"bid": 1.2, "ask": 1.0},
        {"bid": -0.05, "ask": 1.1},
        {"bid": 1.0, "ask": INF},
    ])
    def test_no_market_is_none(self, c):
        assert flow_alerts._mark(c) is None


def test_tools_public_refuses_a_negative_bid():
    assert tools_public.mark_from_chain(
        _chain({"bid": -0.05, "ask": 1.1}), "put", "2026-12-18", 500.0) is None


def test_tools_public_prefers_a_finite_mark():
    m = tools_public.mark_from_chain(
        _chain({"bid": 1.0, "ask": 1.1, "mark": 1.07}), "put", "2026-12-18", 500.0)
    assert m == 1.07 and math.isfinite(m)
