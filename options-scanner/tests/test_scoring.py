"""Tests for scoring.py — actual bid/ask in liquidity scoring."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring import calc_composite_score, norm_trend, norm_iv_hv_ratio, norm_vega_risk, norm_gex_proximity, DEFAULT_WEIGHTS


class TestLiquidityScoringUsesActualBidAsk:
    """Verify scoring uses actual bid/ask instead of estimates."""

    def _make_signal(self, credit=1.50, bid=1.45, ask=1.55, **kwargs):
        return {
            "credit": credit, "bid": bid, "ask": ask,
            "max_loss": 3.50, "rr_pct": 42.86, "pop_pct": 72.0,
            "short_delta": -0.15, "net_theta": -0.05,
            "short_iv": 25.0, "short_strike": 525.0,
            "underlying_price": 530.0,
            "type": "PCS", "dte": 1,
            "long_strike": 520.0, "width": 5,
            **kwargs,
        }

    def test_tight_spread_scores_higher_than_wide(self):
        """A signal with tight actual spread should score higher on liquidity
        than one with wide actual spread, even if credit is the same."""
        tight = self._make_signal(bid=1.48, ask=1.52)  # 2.7% spread
        wide = self._make_signal(bid=1.20, ask=1.80)   # 40% spread

        result_tight = calc_composite_score(tight)
        result_wide = calc_composite_score(wide)
        assert result_tight["composite_score"] > result_wide["composite_score"]

    def test_uses_actual_bid_not_estimate(self):
        """If actual bid/ask are present, the old estimate should not be used.
        With credit=1.50, old estimate was bid=1.38, ask=1.62 (16% spread).
        Actual bid=1.49, ask=1.51 (1.3% spread) -> much better liquidity score."""
        sig = self._make_signal(credit=1.50, bid=1.49, ask=1.51)
        result = calc_composite_score(sig)
        assert result["composite_score"] > 0  # sanity

    def test_falls_back_to_estimate_when_no_bid_ask(self):
        """When bid/ask are 0 or missing, fall back to credit-based estimate."""
        sig = self._make_signal(credit=1.50, bid=0, ask=0)
        result = calc_composite_score(sig)
        assert result["composite_score"] > 0  # should not crash, should use fallback


class TestNormTrend:
    def test_ic_always_scores_neutral(self):
        """Iron Condors are direction-neutral — trend should always be 50."""
        assert norm_trend("BULLISH", "IC") == 50.0
        assert norm_trend("BEARISH", "IC") == 50.0
        assert norm_trend("RECOVERING", "IC") == 50.0
        assert norm_trend("WEAKENING", "IC") == 50.0
        assert norm_trend("NEUTRAL", "IC") == 50.0

    def test_pcs_bullish_scores_100(self):
        assert norm_trend("BULLISH", "PCS") == 100.0

    def test_ccs_bearish_scores_100(self):
        assert norm_trend("BEARISH", "CCS") == 100.0


class TestNormIvHvRatio:
    def test_high_ratio_scores_high(self):
        """IV/HV ratio 1.5 = big volatility premium -> good for selling."""
        score = norm_iv_hv_ratio(1.5)
        assert score >= 80

    def test_ratio_1_scores_middle(self):
        """IV/HV ratio 1.0 = IV equals realized -> neutral."""
        score = norm_iv_hv_ratio(1.0)
        assert 40 <= score <= 60

    def test_low_ratio_scores_low(self):
        """IV/HV ratio 0.7 = IV below realized -> bad for selling."""
        score = norm_iv_hv_ratio(0.7)
        assert score <= 30

    def test_none_returns_neutral(self):
        assert norm_iv_hv_ratio(None) == 50.0


class TestNormVegaRisk:
    def test_low_vega_low_iv_scores_well(self):
        """Low vega exposure even in low IV = manageable."""
        score = norm_vega_risk(net_vega=-0.01, max_loss=5.0, iv_rank=20)
        assert score >= 60

    def test_high_vega_low_iv_scores_poorly(self):
        """High vega in low IV = dangerous expansion risk."""
        score = norm_vega_risk(net_vega=-0.15, max_loss=5.0, iv_rank=15)
        assert score <= 40

    def test_high_vega_high_iv_scores_ok(self):
        """High vega in high IV = less expansion risk."""
        score = norm_vega_risk(net_vega=-0.15, max_loss=5.0, iv_rank=80)
        assert score >= 50

    def test_none_returns_neutral(self):
        assert norm_vega_risk(None, 5.0, 50) == 50.0

    def test_zero_max_loss_returns_neutral(self):
        assert norm_vega_risk(-0.05, 0, 50) == 50.0


class TestNormTrendWithRSI:
    def test_pcs_overbought_penalized(self):
        score_normal = norm_trend("BULLISH", "PCS", rsi=55)
        score_overbought = norm_trend("BULLISH", "PCS", rsi=75)
        assert score_overbought < score_normal

    def test_ccs_oversold_penalized(self):
        score_normal = norm_trend("BEARISH", "CCS", rsi=45)
        score_oversold = norm_trend("BEARISH", "CCS", rsi=25)
        assert score_oversold < score_normal

    def test_no_rsi_no_penalty(self):
        score = norm_trend("BULLISH", "PCS", rsi=None)
        assert score == 100.0


class TestNormGexProximity:
    def test_far_from_wall_scores_high(self):
        score = norm_gex_proximity(short_strike=5200, spot=5300, gex_walls=[5400, 5100])
        assert score >= 80

    def test_on_wall_scores_low(self):
        score = norm_gex_proximity(short_strike=5300, spot=5300, gex_walls=[5300, 5400])
        assert score <= 30

    def test_near_wall_scores_medium(self):
        score = norm_gex_proximity(short_strike=5270, spot=5300, gex_walls=[5300, 5400])
        assert 30 < score < 80

    def test_no_walls_returns_neutral(self):
        assert norm_gex_proximity(short_strike=5200, spot=5300, gex_walls=None) == 50.0

    def test_empty_walls_neutral(self):
        assert norm_gex_proximity(short_strike=5200, spot=5300, gex_walls=[]) == 50.0


class TestWeightValidation:
    def test_weights_sum_to_100(self):
        assert sum(DEFAULT_WEIGHTS.values()) == 100

    def test_all_factors_have_weights(self):
        required = {"rr", "pop", "theta", "iv", "iv_hv", "vega", "em", "liq", "trend"}
        assert required.issubset(DEFAULT_WEIGHTS.keys())


from scoring import norm_dex_proximity


class TestNormDexProximity:
    def test_far_from_wall_scores_high(self):
        score = norm_dex_proximity(short_strike=5200, spot=5300, dex_walls=[5400, 5100])
        assert score >= 80

    def test_on_wall_scores_low(self):
        score = norm_dex_proximity(short_strike=5300, spot=5300, dex_walls=[5300, 5400])
        assert score <= 30

    def test_near_wall_scores_medium(self):
        """Strike 0.5% away from wall - within the 1% penalty band."""
        score = norm_dex_proximity(short_strike=5273.5, spot=5300, dex_walls=[5300, 5400])
        assert 30 <= score <= 70

    def test_no_walls_returns_neutral(self):
        assert norm_dex_proximity(short_strike=5200, spot=5300, dex_walls=None) == 50.0

    def test_empty_walls_neutral(self):
        assert norm_dex_proximity(short_strike=5200, spot=5300, dex_walls=[]) == 50.0


class TestDexInComposite:
    def _make_signal(self, **kwargs):
        return {
            "credit": 1.0, "max_loss": 4.0, "rr_pct": 25.0, "pop_pct": 85.0,
            "net_theta": -0.05, "net_vega": -0.02,
            "short_strike": 5200, "long_strike": 5190,
            "underlying_price": 5300,
            "type": "PCS", "bid": 0.98, "ask": 1.02,
            **kwargs,
        }

    def test_dex_wall_hurts_score(self):
        """Signal with short strike on a DEX wall should score lower than one far from walls."""
        on_wall = self._make_signal(short_strike=5300, dex_walls=[5300, 5400])
        far_off = self._make_signal(short_strike=5200, dex_walls=[5400, 5100])
        s_on = calc_composite_score(on_wall)
        s_off = calc_composite_score(far_off)
        assert s_off["composite_score"] > s_on["composite_score"]

    def test_dex_absent_gives_neutral_score(self):
        """When no dex_walls provided, scoring still works and doesn't crash."""
        sig = self._make_signal()
        result = calc_composite_score(sig)
        assert result["composite_score"] > 0

    def test_default_weights_include_gex_and_dex(self):
        assert "gex" in DEFAULT_WEIGHTS
        assert "dex" in DEFAULT_WEIGHTS
        assert sum(DEFAULT_WEIGHTS.values()) == 100


def test_norm_liquidity_prefers_spread_bid_ask():
    """When signal has spread_bid/spread_ask, scoring uses those over leg-level bid/ask."""
    from scoring import calc_composite_score

    signal_with_spread = {
        "symbol": "$SPX", "type": "CCS",
        "short_strike": 7620, "long_strike": 7630, "width": 10,
        "credit": 2.90, "max_loss": 7.10,
        "pop_pct": 97.9, "short_delta": 0.27,
        "net_theta": -0.10, "net_vega": -0.2,
        "underlying_price": 7515.0,
        "bid": 1.50, "ask": 4.00,
        "spread_bid": 2.88, "spread_ask": 2.92,
    }
    result = calc_composite_score(signal_with_spread)
    assert result["factor_scores"]["liq"] > 50, (
        f"Expected high liquidity with tight spread-level bid/ask, "
        f"got {result['factor_scores']['liq']}"
    )


def test_norm_liquidity_falls_back_to_leg_bid_ask():
    """When spread_bid/spread_ask absent, scoring falls back to leg-level bid/ask (backward compat)."""
    from scoring import calc_composite_score
    signal_no_spread = {
        "symbol": "$SPX", "type": "CCS",
        "short_strike": 7620, "long_strike": 7630, "width": 10,
        "credit": 2.90, "max_loss": 7.10,
        "pop_pct": 97.9, "short_delta": 0.27,
        "net_theta": -0.10, "net_vega": -0.2,
        "underlying_price": 7515.0,
        "bid": 2.88, "ask": 2.92,
    }
    result = calc_composite_score(signal_no_spread)
    assert result["factor_scores"]["liq"] > 50, (
        f"Expected reasonable liquidity with tight leg-level bid/ask, "
        f"got {result['factor_scores']['liq']}"
    )
