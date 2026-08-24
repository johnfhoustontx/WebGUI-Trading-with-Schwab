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


def _valuation_raw(verdict):
    return next(b["raw_score"] for b in verdict["breakdown"] if b["factor"] == "valuation")


class TestValuationAveragesOnlyAvailableInputs:
    """`valuation` is the mean of {P/E-vs-sector, PEG}. When a sub-score's INPUT
    is missing its primitive returns 0, and averaging that structural 0 in HALVES
    the surviving sub-score — so a symbol with an excellent PEG scored +20 where
    it should score +40, purely because no sector median was supplied. Live
    `analyze()` passes `sector_pe_median=None` unconditionally, so this halving
    applied to EVERY symbol, always.

    The availability test has to be on the INPUTS, not the outputs: `score_peg`
    legitimately returns 0 for a PEG between 1 and 2, so "score == 0" cannot mean
    "missing"."""

    def test_missing_sector_median_does_not_halve_the_peg_score(self, strong_inputs):
        no_median = replace(strong_inputs, sector_pe_median=None)
        v = InvestorVerdict().score(no_median)
        # PEG 0.9 -> score_peg == 40. Averaging in a structural 0 would give 20.
        assert _valuation_raw(v) == 40

    def test_both_inputs_present_still_averages_both(self, strong_inputs):
        v = InvestorVerdict().score(strong_inputs)
        # P/E 18 vs median 22 -> ratio 0.82 -> 30;  PEG 0.9 -> 40;  mean -> 35.
        assert _valuation_raw(v) == 35

    def test_a_legitimate_zero_peg_score_is_not_treated_as_missing(self, strong_inputs):
        # PEG 1.5 scores exactly 0 — a real reading, not an absence. With no
        # sector median it must stand alone as 0, not vanish.
        f = replace(strong_inputs.fundamentals, peg_ratio=1.5)
        inp = replace(strong_inputs, fundamentals=f, sector_pe_median=None)
        assert _valuation_raw(InvestorVerdict().score(inp)) == 0

    def test_neither_input_available_scores_zero(self, strong_inputs):
        f = replace(strong_inputs.fundamentals, peg_ratio=None)
        inp = replace(strong_inputs, fundamentals=f, sector_pe_median=None)
        assert _valuation_raw(InvestorVerdict().score(inp)) == 0


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


class TestEarningsTrajectoryAveragesOnlyWhatArrived:
    """The same bug `valuation` already carries a comment about, one component
    over: averaging a structurally-absent sub-score HALVES the one that did
    arrive.

    `earnings_traj` is the mean of a surprise-streak score and a guidance
    score. No vendor here publishes forward guidance — Schwab does not, and
    Alpha Vantage's EARNINGS feed is historical — so the guidance half is
    always 0. Averaging it in turns a perfect four-quarter beat streak (80)
    into 40, and a fresh miss (-60) into -30. The availability test is on the
    INPUT, not the output: `score_earnings_surprise_streak` legitimately
    returns 0 for a mixed record, so a 0 cannot stand for "missing"."""

    def _f(self, **kw):
        from src.analysis.fundamentals import Fundamentals
        base = dict(pe_ratio=20.0, peg_ratio=1.5, rev_growth_ttm=0.10,
                    eps_growth_ttm=0.10, roe=0.20, margin_expanding=True)
        base.update(kw)
        return Fundamentals(**base)

    def _traj(self, f):
        from src.analysis.recommendation import InvestorInputs, InvestorVerdict
        from src.analysis.sector_strength import SectorStrength
        inp = InvestorInputs(
            fundamentals=f, sector_pe_median=18.0,
            rs_vs_spy_3m=0.5, rs_vs_spy_6m=0.5, rs_vs_spy_12m=0.5,
            rs_vs_sector_3m=0.5, rs_vs_sector_6m=0.5, rs_vs_sector_12m=0.5,
            sector_strength=SectorStrength(score=0, rs_3m_percentile=0.5,
                                           sector_above_50ema=True,
                                           in_confirmed_downtrend=False))
        out = InvestorVerdict().score(inp)
        return next(b for b in out["breakdown"]
                    if b["factor"] == "earnings_traj")["raw_score"]

    def test_a_four_quarter_beat_streak_is_not_halved_by_absent_guidance(self):
        f = self._f(eps_surprises=[0.10, 0.08, 0.07, 0.06], guidance=None)
        assert self._traj(f) == 80

    def test_a_recent_miss_is_not_halved_either(self):
        f = self._f(eps_surprises=[0.10, 0.08, 0.07, -0.02], guidance=None)
        assert self._traj(f) == -60

    def test_guidance_still_counts_when_a_source_supplies_it(self):
        f = self._f(eps_surprises=[0.10, 0.08, 0.07, 0.06], guidance="RAISED")
        assert self._traj(f) == 60          # mean(80, 40)

    def test_no_surprise_history_and_no_guidance_scores_zero(self):
        assert self._traj(self._f(eps_surprises=None, guidance=None)) == 0
