"""The engine callers of shared.quotes (2026-11-28): what each now refuses.

Before, a crossed quote (bid above the offer) marked at its mid in every one,
and a NaN or an infinity passed through as a price.
"""
import math

import pytest

import signal_repricer
import strategy_scanner
import trade_analyzer

NAN, INF = float("nan"), float("inf")


class TestRepricerMarket:
    def test_a_normal_quote_is_a_market(self):
        assert signal_repricer._market({"bid": 1.0, "ask": 1.1}) == (1.0, 1.1)

    @pytest.mark.parametrize("c", [
        {"bid": 1.20, "ask": 1.00},
        {"bid": True, "ask": 1.10},
        {"bid": NAN, "ask": 1.10},
        {"bid": 1.0, "ask": INF},
    ])
    def test_no_market_holds_the_last_mark(self, c):
        assert signal_repricer._market(c) == (None, None)

    def test_an_absent_bid_is_a_zero_bid(self):
        # 0 x 0.05: the long leg of a spread that has won is still a market.
        assert signal_repricer._market({"ask": 0.05}) == (0.0, 0.05)


class TestStrategyScannerMark:
    def test_crossed_without_a_mark_is_no_price(self):
        assert strategy_scanner._norm_mark({"bid": 1.2, "ask": 1.0}) == 0

    def test_a_nan_mark_falls_back_to_the_mid(self):
        assert strategy_scanner._norm_mark(
            {"bid": 1.0, "ask": 1.1, "mark": NAN}) == pytest.approx(1.05)

    def test_an_infinite_offer_is_no_price(self):
        assert strategy_scanner._norm_mark({"bid": 1.0, "ask": INF}) == 0


class TestTradeAnalyzerMark:
    def _chain(self, c):
        return {"putExpDateMap": {"e": {"500.0": [c]}}}

    @pytest.mark.parametrize("c", [
        {"bid": 1.2, "ask": 1.0},
        {"bid": 1.0, "ask": INF},
        {"bid": 1.2, "ask": 1.0, "mark": NAN},
    ])
    def test_no_usable_price_is_zero(self, c):
        row = trade_analyzer._find_contract_in_chain(self._chain(c), 500.0, "put")
        assert row["mark"] == 0 and math.isfinite(row["mark"])
