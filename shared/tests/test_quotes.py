"""shared.quotes: the ONE mid price, and the callers bound to it.

Measured 2026-11-28 over one quote battery: several callers took a crossed
quote as a price, three passed a NaN or an infinity through, one read 0 x 0 as
a mid of 0.00 and two read a bid with no offer as half the bid.
"""
import ast
import math
import pathlib

import pytest

from shared.quotes import contract_mid, mark_or_mid, mid

REPO = pathlib.Path(__file__).resolve().parents[2]
NAN, INF = float("nan"), float("inf")


class TestMid:
    def test_a_normal_quote(self):
        assert mid(1.00, 1.10) == pytest.approx(1.05)

    def test_a_locked_quote_is_a_market(self):
        assert mid(1.00, 1.00) == 1.00

    def test_returns_a_float(self):
        assert isinstance(mid(1, 2), float)

    @pytest.mark.parametrize("bid,ask", [
        (1.20, 1.00),          # crossed
        (1.00, 0.0),           # bid with no offer
        (0.0, 0.0),            # no market at all
        (-0.05, 1.10),         # negative bid
        (None, 1.10), (1.00, None), (None, None),
        (NAN, 1.10), (1.00, NAN), (1.00, INF), (INF, INF),
        (True, 1.10), (1.00, True),
        ("1.00", "1.10"),      # text is not a quote here
    ])
    def test_never_a_market(self, bid, ask):
        assert mid(bid, ask, zero_bid_max_ask=math.inf) is None

    def test_zero_bid_refused_by_default(self):
        assert mid(0.0, 0.05) is None

    def test_zero_bid_under_the_limit_is_a_market(self):
        assert mid(0.0, 0.05, zero_bid_max_ask=0.05) == pytest.approx(0.025)
        assert mid(0.0, 0.06, zero_bid_max_ask=0.05) is None

    def test_zero_bid_with_infinite_limit_takes_any_offer(self):
        assert mid(0.0, 1.20, zero_bid_max_ask=math.inf) == pytest.approx(0.60)


class TestContract:
    def test_contract_mid(self):
        assert contract_mid({"bid": 1.0, "ask": 1.1}) == pytest.approx(1.05)

    @pytest.mark.parametrize("c", [None, [], "x", {}])
    def test_not_a_contract(self, c):
        assert contract_mid(c) is None
        assert mark_or_mid(c) is None

    def test_mark_is_preferred(self):
        assert mark_or_mid({"bid": 1.0, "ask": 1.1, "mark": 1.07}) == 1.07

    @pytest.mark.parametrize("bad", [0, -1.0, NAN, INF, True, None])
    def test_an_unusable_mark_falls_back_to_the_mid(self, bad):
        assert mark_or_mid({"bid": 1.0, "ask": 1.1, "mark": bad}) == pytest.approx(1.05)

    def test_an_unusable_mark_and_a_crossed_quote_is_nothing(self):
        assert mark_or_mid({"bid": 1.2, "ask": 1.0, "mark": NAN}) is None

    def test_the_mark_survives_a_crossed_quote(self):
        assert mark_or_mid({"bid": 1.2, "ask": 1.0, "mark": 1.07}) == 1.07


# Every caller moved on 2026-11-28 reaches the one module. A new private
# (bid + ask) / 2 is how the five ideas of a market came about.
BOUND = {
    "services/options_svc/compute.py",
    "services/options_svc/flow_alerts.py",
    "services/options_svc/scalp_call.py",
    "services/options_svc/tools_public.py",
    "shared/live_order.py",
    "webgui/pages/options/chain_grid.py",
    "options-scanner/signal_repricer.py",
    "options-scanner/strategy_scanner.py",
    "options-scanner/scanner_engine.py",
    "options-scanner/trade_analyzer.py",
    "schwab-proxy/trade_detector.py",
}


@pytest.mark.parametrize("rel", sorted(BOUND))
def test_caller_imports_shared_quotes(rel):
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    found = any(
        isinstance(n, ast.ImportFrom) and (
            n.module == "shared.quotes"
            or (n.module == "shared" and any(a.name == "quotes" for a in n.names)))
        for n in ast.walk(tree))
    assert found, f"{rel} no longer imports shared.quotes"
