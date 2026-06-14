import pytest
from dataclasses import replace
from src.analysis.recommendation import InvestorVerdict, InvestorInputs
from src.analysis.fundamentals import Fundamentals
from src.analysis.sector_strength import SectorStrength


def _strong_fundamentals():
    return Fundamentals(
        pe_ratio=18, peg_ratio=0.9,
        rev_growth_ttm=0.20, eps_growth_ttm=0.25,
        roe=0.22, margin_expanding=True, fcf=1e9,
        eps_surprises=[0.06, 0.04, 0.05, 0.08],
        last_eps_surprise=0.08, guidance="RAISED", days_to_earnings=120,
    )


def _bullish_sector():
    return SectorStrength(score=60, in_confirmed_downtrend=False, sector_above_50ema=True, rs_3m_percentile=0.8)


@pytest.fixture
def strong_inputs():
    return InvestorInputs(
        fundamentals=_strong_fundamentals(),
        sector_pe_median=22,
        rs_vs_spy_3m=0.8, rs_vs_spy_6m=0.75, rs_vs_spy_12m=0.7,
        rs_vs_sector_3m=0.7, rs_vs_sector_6m=0.65, rs_vs_sector_12m=0.6,
        sector_strength=_bullish_sector(),
    )


class TestInvestorVerdictHappyPath:
    def test_strong_fundamentals_yield_buy(self, strong_inputs):
        v = InvestorVerdict().score(strong_inputs)
        assert v["verdict"] == "BUY"
        assert v["score"] >= 40
        assert len(v["top_reasons"]) == 3
        assert v["gates_triggered"] == []

    def test_breakdown_factors(self, strong_inputs):
        v = InvestorVerdict().score(strong_inputs)
        factors = {b["factor"] for b in v["breakdown"]}
        assert factors == {"valuation", "growth_quality", "earnings_traj", "rs_vs_spy", "rs_vs_sector", "sector"}

    def test_weights_sum_to_100(self, strong_inputs):
        v = InvestorVerdict().score(strong_inputs)
        assert sum(b["weight"] for b in v["breakdown"]) == 100


class TestInvestorVerdictGates:
    def test_insufficient_fundamentals_returns_hold_short_circuit(self):
        empty = Fundamentals()
        inp = InvestorInputs(
            fundamentals=empty,
            sector_pe_median=22,
            rs_vs_spy_3m=0.9, rs_vs_spy_6m=0.9, rs_vs_spy_12m=0.9,
            rs_vs_sector_3m=0.9, rs_vs_sector_6m=0.9, rs_vs_sector_12m=0.9,
            sector_strength=_bullish_sector(),
        )
        v = InvestorVerdict().score(inp)
        assert v["verdict"] == "HOLD"
        assert v["score"] == 0
        assert v["breakdown"] == []
        assert "Insufficient fundamental data" in v["top_reasons"]
        assert "No fundamentals" in v["gates_triggered"]

    def test_negative_fcf_plus_miss_caps_at_hold(self, strong_inputs):
        bad = replace(_strong_fundamentals(), fcf=-1e8, last_eps_surprise=-0.05,
                      eps_surprises=[0.04, 0.03, 0.02, -0.05])
        inp = replace(strong_inputs, fundamentals=bad)
        v = InvestorVerdict().score(inp)
        assert v["verdict"] == "HOLD"
        assert any("Negative FCF" in g for g in v["gates_triggered"])

    def test_negative_fcf_alone_does_not_gate(self, strong_inputs):
        # FCF negative but earnings beat — gate requires both
        bad = replace(_strong_fundamentals(), fcf=-1e8, last_eps_surprise=0.08)
        inp = replace(strong_inputs, fundamentals=bad)
        v = InvestorVerdict().score(inp)
        assert all("Negative FCF" not in g for g in v["gates_triggered"])

    def test_sector_downtrend_caps_at_hold(self, strong_inputs):
        bear = SectorStrength(score=-80, in_confirmed_downtrend=True, sector_above_50ema=False, rs_3m_percentile=0.05)
        inp = replace(strong_inputs, sector_strength=bear)
        v = InvestorVerdict().score(inp)
        assert v["verdict"] == "HOLD"
        assert any("Sector in confirmed downtrend" in g for g in v["gates_triggered"])
