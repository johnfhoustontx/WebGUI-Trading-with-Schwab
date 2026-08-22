import pandas as pd
import numpy as np
import pytest
from dataclasses import replace
from src.analysis.recommendation import PositionVerdict, PositionInputs
from src.analysis.sector_strength import SectorStrength


def _trending_up_history(days=260, drift=0.0015, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    closes = [start]
    for _ in range(days - 1):
        closes.append(closes[-1] * (1 + drift + rng.normal(0, 0.005)))
    idx = pd.date_range(end="2026-04-25", periods=days, freq="B")
    return pd.DataFrame({"close": closes, "high": closes, "low": closes, "volume": [1e6]*days}, index=idx)


@pytest.fixture
def strong_uptrend_inputs():
    daily = _trending_up_history(seed=1)
    spy = _trending_up_history(seed=2, drift=0.0003)  # weaker
    sector = SectorStrength(score=70, in_confirmed_downtrend=False, sector_above_50ema=True, rs_3m_percentile=0.8)
    last = daily["close"].iloc[-1]
    return PositionInputs(
        daily=daily, hourly=daily, spy_history=spy,
        ema_alignment_pct=85.0,
        rsi=58.0, adx=28.0,
        macd_hist=0.5, macd_hist_prev=0.3,
        relative_volume=1.6,
        vwap=last * 0.99,
        volume_profile={"poc": last * 0.97, "vah": last * 1.02, "val": last * 0.95},
        sector_strength=sector,
        days_to_earnings=120,
    )


class TestPositionVerdictHappyPath:
    def test_strong_uptrend_yields_buy(self, strong_uptrend_inputs):
        v = PositionVerdict().score(strong_uptrend_inputs)
        assert v["verdict"] == "BUY"
        assert v["score"] >= 40
        assert len(v["top_reasons"]) == 3
        assert v["gates_triggered"] == []
        assert all(k in v for k in ("verdict", "score", "breakdown", "top_reasons", "gates_triggered"))

    def test_breakdown_has_all_factors(self, strong_uptrend_inputs):
        v = PositionVerdict().score(strong_uptrend_inputs)
        factors = {b["factor"] for b in v["breakdown"]}
        assert factors == {"ema_alignment", "adx", "rsi", "macd", "rel_volume",
                           "vwap", "volume_profile", "rs_3m", "rs_6m", "dist_52wk", "sector"}

    def test_weights_sum_to_100(self, strong_uptrend_inputs):
        v = PositionVerdict().score(strong_uptrend_inputs)
        assert sum(b["weight"] for b in v["breakdown"]) == 100


class TestPositionVerdictGates:
    def test_low_adx_caps_at_hold(self, strong_uptrend_inputs):
        inp = replace(strong_uptrend_inputs, adx=10.0)
        v = PositionVerdict().score(inp)
        assert v["verdict"] == "HOLD"
        assert any("ADX<15" in g for g in v["gates_triggered"])

    def test_earnings_within_8wks_caps_at_hold(self, strong_uptrend_inputs):
        inp = replace(strong_uptrend_inputs, days_to_earnings=14)
        v = PositionVerdict().score(inp)
        assert v["verdict"] == "HOLD"
        assert any("Earnings in 14 days" in g for g in v["gates_triggered"])

    def test_earnings_outside_window_does_not_gate(self, strong_uptrend_inputs):
        inp = replace(strong_uptrend_inputs, days_to_earnings=90)
        v = PositionVerdict().score(inp)
        assert all("Earnings" not in g for g in v["gates_triggered"])

    def test_sector_downtrend_caps_at_hold(self, strong_uptrend_inputs):
        bear_sector = SectorStrength(score=-80, in_confirmed_downtrend=True,
                                     sector_above_50ema=False, rs_3m_percentile=0.05)
        inp = replace(strong_uptrend_inputs, sector_strength=bear_sector)
        v = PositionVerdict().score(inp)
        assert v["verdict"] == "HOLD"
        assert any("Sector in confirmed downtrend" in g for g in v["gates_triggered"])

    def test_below_200ema_cannot_be_buy(self):
        # Build inputs where price sits below the 200EMA
        rng = np.random.default_rng(99)
        days = 260
        closes = [200.0]
        for i in range(days - 1):
            drift = 0.001 if i < days * 0.6 else -0.003   # rallies, then steep drop
            closes.append(closes[-1] * (1 + drift + rng.normal(0, 0.004)))
        idx = pd.date_range(end="2026-04-25", periods=days, freq="B")
        daily = pd.DataFrame({"close": closes, "high": closes, "low": closes, "volume": [1e6]*days}, index=idx)
        spy = _trending_up_history(seed=2, drift=0.0003)
        sector = SectorStrength(score=70, in_confirmed_downtrend=False, sector_above_50ema=True, rs_3m_percentile=0.8)
        last = daily["close"].iloc[-1]
        inp = PositionInputs(
            daily=daily, hourly=daily, spy_history=spy,
            ema_alignment_pct=85.0, rsi=58.0, adx=28.0,
            macd_hist=0.5, macd_hist_prev=0.3, relative_volume=1.6,
            vwap=last * 0.99,
            volume_profile={"poc": last * 0.97, "vah": last * 1.02, "val": last * 0.95},
            sector_strength=sector,
            days_to_earnings=120,
        )
        # confirm setup: price actually below 200EMA
        ema200 = daily["close"].ewm(span=200, adjust=False).mean().iloc[-1]
        assert daily["close"].iloc[-1] < ema200
        v = PositionVerdict().score(inp)
        assert v["verdict"] != "BUY"
        assert any("Below 200EMA" in g for g in v["gates_triggered"])


def _trending_down_history(days=260, drift=-0.0015, seed=7, start=200.0):
    rng = np.random.default_rng(seed)
    closes = [start]
    for _ in range(days - 1):
        closes.append(closes[-1] * (1 + drift + rng.normal(0, 0.005)))
    idx = pd.date_range(end="2026-04-25", periods=days, freq="B")
    return pd.DataFrame({"close": closes, "high": closes, "low": closes,
                         "volume": [1e6] * days}, index=idx)


@pytest.fixture
def strong_downtrend_inputs():
    """A clean SELL: falling price, bearish alignment, weak vs SPY."""
    daily = _trending_down_history(seed=7)
    spy = _trending_up_history(seed=2, drift=0.0003)
    sector = SectorStrength(score=-70, in_confirmed_downtrend=True,
                            sector_above_50ema=False, rs_3m_percentile=0.1)
    last = daily["close"].iloc[-1]
    return PositionInputs(
        daily=daily, hourly=daily, spy_history=spy,
        ema_alignment_pct=-85.0,
        rsi=34.0, adx=28.0,
        macd_hist=-0.5, macd_hist_prev=-0.3,
        relative_volume=1.6,
        vwap=last * 1.01,
        volume_profile={"poc": last * 1.03, "vah": last * 1.05, "val": last * 1.01},
        sector_strength=sector,
        days_to_earnings=120,
    )


class TestShortSideGatesMirrorTheLongOnes:
    """The existing gate set is long-biased: below-200-EMA and sector-downtrend
    cap only BUY. A short book needs the mirror images, or the model will
    happily recommend shorting a name in a healthy uptrend."""

    def test_a_clean_downtrend_is_a_sell_when_nothing_gates_it(self, strong_downtrend_inputs):
        v = PositionVerdict().score(strong_downtrend_inputs)
        assert v["verdict"] == "SELL"
        assert v["score"] <= -40

    def test_above_a_RISING_200ema_caps_sell(self, strong_downtrend_inputs):
        """Shorting into a rising long-term trend is the mirror of the existing
        'no BUY below the 200-EMA' rule, and the more expensive mistake."""
        inp = replace(strong_downtrend_inputs, daily=_trending_up_history(seed=1))
        v = PositionVerdict().score(inp)
        assert v["verdict"] != "SELL"
        assert any("200-EMA" in g for g in v["short_gates"])

    def test_above_a_FALLING_200ema_does_not_cap_sell(self):
        """The slope is load-bearing, not decoration. Price bouncing back above
        a still-FALLING 200-EMA is a rally in a downtrend — the textbook short
        entry — so a bare 'price above the 200' test would gate away exactly
        the setup the short side wants."""
        daily = _trending_down_history(seed=7)
        # Lift the last few closes above a 200-EMA that is still falling hard.
        daily = daily.copy()
        ema200 = daily["close"].ewm(span=200, adjust=False).mean().iloc[-1]
        daily.iloc[-1, daily.columns.get_loc("close")] = ema200 * 1.02
        spy = _trending_up_history(seed=2, drift=0.0003)
        sector = SectorStrength(score=-70, in_confirmed_downtrend=True,
                                sector_above_50ema=False, rs_3m_percentile=0.1)
        last = daily["close"].iloc[-1]
        inp = PositionInputs(
            daily=daily, hourly=daily, spy_history=spy,
            ema_alignment_pct=-85.0, rsi=34.0, adx=28.0,
            macd_hist=-0.5, macd_hist_prev=-0.3, relative_volume=1.6,
            vwap=last * 1.01,
            volume_profile={"poc": last * 1.03, "vah": last * 1.05, "val": last * 1.01},
            sector_strength=sector, days_to_earnings=120,
        )
        v = PositionVerdict().score(inp)
        assert not any("200-EMA" in g for g in v["short_gates"])

    def test_sector_in_confirmed_uptrend_caps_sell(self, strong_downtrend_inputs):
        sector = SectorStrength(score=80, in_confirmed_downtrend=False,
                                in_confirmed_uptrend=True,
                                sector_above_50ema=True, rs_3m_percentile=0.9)
        inp = replace(strong_downtrend_inputs, sector_strength=sector)
        v = PositionVerdict().score(inp)
        assert v["verdict"] != "SELL"
        assert any("uptrend" in g.lower() for g in v["short_gates"])

    def test_the_mirrors_never_touch_a_buy(self, strong_uptrend_inputs):
        """A BUY sits above a rising 200-EMA in a strong sector by definition —
        if the mirrors were written carelessly they would gate every BUY."""
        sector = SectorStrength(score=80, in_confirmed_downtrend=False,
                                in_confirmed_uptrend=True,
                                sector_above_50ema=True, rs_3m_percentile=0.9)
        inp = replace(strong_uptrend_inputs, sector_strength=sector)
        v = PositionVerdict().score(inp)
        assert v["verdict"] == "BUY"


class TestSqueezeGate:
    """The short-only gate. Its input is computed upstream (the engine cannot
    reach FINRA), so the verdict gates on the REASON being present — which
    keeps this module pure and the threshold policy in one place."""

    def test_a_squeeze_reason_caps_sell_and_is_surfaced(self, strong_downtrend_inputs):
        inp = replace(strong_downtrend_inputs,
                      squeeze_reason="17.1 days to cover")
        v = PositionVerdict().score(inp)
        assert v["verdict"] != "SELL"
        gate = next(g for g in v["short_gates"] if "squeeze" in g.lower())
        assert "17.1 days to cover" in gate

    def test_no_squeeze_reason_does_not_gate(self, strong_downtrend_inputs):
        v = PositionVerdict().score(replace(strong_downtrend_inputs, squeeze_reason=None))
        assert not any("squeeze" in g.lower() for g in v["short_gates"])
        assert v["verdict"] == "SELL"

    def test_a_squeeze_never_blocks_a_buy(self, strong_uptrend_inputs):
        """Crowded shorts are a reason not to SHORT. Being heavily shorted is
        not a reason not to be long — if anything it is fuel."""
        inp = replace(strong_uptrend_inputs, squeeze_reason="31.0% of float short")
        assert PositionVerdict().score(inp)["verdict"] == "BUY"
