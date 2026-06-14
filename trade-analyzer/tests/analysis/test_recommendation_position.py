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
        assert any("Earnings in 14d" in g for g in v["gates_triggered"])

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
