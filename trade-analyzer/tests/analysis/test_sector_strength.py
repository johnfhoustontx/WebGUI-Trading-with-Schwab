import pandas as pd
import numpy as np
from src.analysis.sector_strength import compute_sector_strength, SectorStrength, sector_etf_for

def _synthetic_history(start: float, drift: float, days: int = 260, seed: int = 0):
    rng = np.random.default_rng(seed)
    closes = [start]
    for _ in range(days - 1):
        closes.append(closes[-1] * (1 + drift + rng.normal(0, 0.005)))
    idx = pd.date_range(end="2026-04-25", periods=days, freq="B")
    return pd.DataFrame({"close": closes, "high": closes, "low": closes, "volume": [1e6]*days}, index=idx)


class TestComputeSectorStrength:
    def test_strong_uptrending_sector_outperforming_spy(self):
        sector = _synthetic_history(100, 0.002)
        spy    = _synthetic_history(400, 0.0005)
        result = compute_sector_strength(sector_history=sector, spy_history=spy)
        assert isinstance(result, SectorStrength)
        assert result.score > 50
        assert result.in_confirmed_downtrend is False
        assert result.sector_above_50ema is True

    def test_downtrending_sector_underperforming_spy(self):
        sector = _synthetic_history(100, -0.002)
        spy    = _synthetic_history(400, 0.0005)
        result = compute_sector_strength(sector_history=sector, spy_history=spy)
        assert result.score < -50
        assert result.in_confirmed_downtrend is True
        assert result.sector_above_50ema is False

    def test_score_clamped_to_range(self):
        sector = _synthetic_history(100, 0.005)   # very strong
        spy    = _synthetic_history(400, -0.005)  # very weak
        result = compute_sector_strength(sector_history=sector, spy_history=spy)
        assert -100 <= result.score <= 100

    def test_rs_percentile_in_unit_range(self):
        sector = _synthetic_history(100, 0.001)
        spy    = _synthetic_history(400, 0.001)
        result = compute_sector_strength(sector_history=sector, spy_history=spy)
        assert 0.0 <= result.rs_3m_percentile <= 1.0


class TestSectorEtfFor:
    def test_known_sectors(self):
        assert sector_etf_for("Technology") == "XLK"
        assert sector_etf_for("Healthcare") == "XLV"
        assert sector_etf_for("Financials") == "XLF"
        assert sector_etf_for("Real Estate") == "XLRE"

    def test_unknown_returns_none(self):
        assert sector_etf_for("Cryptopunks") is None

    def test_none_returns_none(self):
        assert sector_etf_for(None) is None

    def test_empty_returns_none(self):
        assert sector_etf_for("") is None
