"""Buy/Hold/Sell verdict engines."""
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.analysis.fundamentals import Fundamentals
from src.analysis.scoring import (
    score_adx_directional,
    score_distance_from_52wk_high,
    score_earnings_surprise_streak,
    score_growth_metric,
    score_guidance_direction,
    score_macd,
    score_margin_trend,
    score_pe_vs_sector,
    score_peg,
    score_relative_strength_percentile,
    score_relative_volume,
    score_roe,
    score_rsi,
    score_volume_profile_location,
    score_vwap,
)
from src.analysis.sector_strength import SectorStrength


WEIGHTS_POSITION = {
    "ema_alignment": 20, "adx": 10, "rsi": 10, "macd": 10,
    "rel_volume": 5, "vwap": 5, "volume_profile": 5,
    "rs_3m": 10, "rs_6m": 10, "dist_52wk": 5, "sector": 10,
}


@dataclass
class PositionInputs:
    daily: pd.DataFrame
    hourly: pd.DataFrame
    spy_history: pd.DataFrame
    ema_alignment_pct: float
    rsi: float
    adx: float
    macd_hist: float
    macd_hist_prev: float
    relative_volume: float
    vwap: float
    volume_profile: Dict[str, float]
    sector_strength: SectorStrength
    days_to_earnings: Optional[int] = None
    sector_history: Optional[pd.DataFrame] = None


def _rs_percentile(symbol_close: pd.Series, spy_close: pd.Series, lookback: int) -> float:
    if len(symbol_close) < lookback + 1 or len(spy_close) < lookback + 1:
        return 0.5
    sym_ret = symbol_close.iloc[-1] / symbol_close.iloc[-lookback - 1] - 1
    spy_ret = spy_close.iloc[-1] / spy_close.iloc[-lookback - 1] - 1
    excess = sym_ret - spy_ret
    return float(np.clip(0.5 + excess / 0.40, 0.0, 1.0))


def _dist_from_52wk_high_pct(daily: pd.DataFrame) -> float:
    high_252 = daily["close"].tail(252).max()
    last = daily["close"].iloc[-1]
    if high_252 <= 0:
        return 0.0
    return float((high_252 - last) / high_252)


def _verdict_from_score(score: float) -> str:
    if score >= 40:
        return "BUY"
    if score <= -40:
        return "SELL"
    return "HOLD"


class PositionVerdict:
    """Buy/Hold/Sell verdict for a 1-8 week horizon."""

    def score(self, inp: PositionInputs) -> dict:
        ema_slope = 1 if inp.ema_alignment_pct >= 0 else -1
        last_close = inp.daily["close"].iloc[-1]

        raw_scores = {
            "ema_alignment": int(np.clip(inp.ema_alignment_pct, -100, 100)),
            "adx": score_adx_directional(inp.adx, ema_slope),
            "rsi": score_rsi(inp.rsi),
            "macd": score_macd(inp.macd_hist, inp.macd_hist_prev),
            "rel_volume": score_relative_volume(inp.relative_volume, ema_slope),
            "vwap": score_vwap(last_close, inp.vwap),
            "volume_profile": score_volume_profile_location(last_close, inp.volume_profile),
            "rs_3m": score_relative_strength_percentile(
                _rs_percentile(inp.daily["close"], inp.spy_history["close"], 63)
            ),
            "rs_6m": score_relative_strength_percentile(
                _rs_percentile(inp.daily["close"], inp.spy_history["close"], 126)
            ),
            "dist_52wk": score_distance_from_52wk_high(_dist_from_52wk_high_pct(inp.daily)),
            "sector": inp.sector_strength.score,
        }

        breakdown: List[dict] = []
        for factor, weight in WEIGHTS_POSITION.items():
            raw = raw_scores[factor]
            breakdown.append({
                "factor": factor,
                "weight": weight,
                "raw_score": raw,
                "contribution": raw * weight / 100,
            })

        composite = sum(b["contribution"] for b in breakdown)
        verdict = _verdict_from_score(composite)

        # Hard gates
        gates: List[str] = []

        if inp.adx < 15:
            gates.append("ADX<15: no trend, capped at HOLD")
            if verdict in ("BUY", "SELL"):
                verdict = "HOLD"

        ema200 = inp.daily["close"].ewm(span=200, adjust=False).mean().iloc[-1]
        if last_close < ema200:
            gates.append("Below 200EMA: cannot be BUY")
            if verdict == "BUY":
                verdict = "HOLD"

        if inp.days_to_earnings is not None and 0 <= inp.days_to_earnings <= 56:
            gates.append(f"Earnings in {inp.days_to_earnings} days: capped at HOLD")
            if verdict in ("BUY", "SELL"):
                verdict = "HOLD"

        if inp.sector_strength.in_confirmed_downtrend:
            gates.append("Sector in confirmed downtrend: capped at HOLD")
            if verdict == "BUY":
                verdict = "HOLD"

        top = sorted(breakdown, key=lambda b: abs(b["contribution"]), reverse=True)[:3]
        reasons = [f"{b['factor']} ({b['contribution']:+.0f})" for b in top]

        return {
            "verdict": verdict,
            "score": int(round(composite)),
            "breakdown": breakdown,
            "top_reasons": reasons,
            "gates_triggered": gates,
        }


WEIGHTS_INVESTOR = {
    "valuation": 20,
    "growth_quality": 25,
    "earnings_traj": 15,
    "rs_vs_spy": 15,
    "rs_vs_sector": 10,
    "sector": 15,
}


@dataclass
class InvestorInputs:
    fundamentals: Fundamentals
    sector_pe_median: Optional[float]
    rs_vs_spy_3m: float
    rs_vs_spy_6m: float
    rs_vs_spy_12m: float
    rs_vs_sector_3m: float
    rs_vs_sector_6m: float
    rs_vs_sector_12m: float
    sector_strength: SectorStrength


def _mean_int(values: List[float]) -> int:
    return int(round(sum(values) / len(values)))


class InvestorVerdict:
    """Buy/Hold/Sell verdict for a months+ investing horizon."""

    def score(self, inp: InvestorInputs) -> dict:
        f = inp.fundamentals

        if not f.is_sufficient():
            return {
                "verdict": "HOLD",
                "score": 0,
                "breakdown": [],
                "top_reasons": ["Insufficient fundamental data"],
                "gates_triggered": ["No fundamentals"],
            }

        # Valuation averages only the sub-scores whose INPUTS are present. A
        # missing input makes its primitive return 0, and averaging that
        # structural 0 in HALVES the surviving sub-score — so an excellent PEG
        # scored +20 instead of +40 whenever no sector median was supplied,
        # which live ``analyze()`` never supplied. The availability test is on
        # the inputs, not the outputs: ``score_peg`` legitimately returns 0 for
        # a PEG between 1 and 2, so a 0 score cannot stand for "missing".
        valuation_parts = []
        if f.pe_ratio is not None and inp.sector_pe_median:
            valuation_parts.append(score_pe_vs_sector(f.pe_ratio, inp.sector_pe_median))
        if f.peg_ratio is not None:
            valuation_parts.append(score_peg(f.peg_ratio))

        raw_scores = {
            "valuation": _mean_int(valuation_parts) if valuation_parts else 0,
            "growth_quality": _mean_int([
                score_growth_metric(f.rev_growth_ttm),
                score_growth_metric(f.eps_growth_ttm),
                score_roe(f.roe),
                score_margin_trend(f.margin_expanding),
            ]),
            "earnings_traj": _mean_int([
                score_earnings_surprise_streak(f.eps_surprises),
                score_guidance_direction(f.guidance),
            ]),
            "rs_vs_spy": _mean_int([
                score_relative_strength_percentile(inp.rs_vs_spy_3m),
                score_relative_strength_percentile(inp.rs_vs_spy_6m),
                score_relative_strength_percentile(inp.rs_vs_spy_12m),
            ]),
            "rs_vs_sector": _mean_int([
                score_relative_strength_percentile(inp.rs_vs_sector_3m),
                score_relative_strength_percentile(inp.rs_vs_sector_6m),
                score_relative_strength_percentile(inp.rs_vs_sector_12m),
            ]),
            "sector": inp.sector_strength.score,
        }

        breakdown: List[dict] = []
        for factor, weight in WEIGHTS_INVESTOR.items():
            raw = raw_scores[factor]
            breakdown.append({
                "factor": factor,
                "weight": weight,
                "raw_score": raw,
                "contribution": raw * weight / 100,
            })

        composite = sum(b["contribution"] for b in breakdown)
        verdict = _verdict_from_score(composite)

        gates: List[str] = []

        if (f.fcf is not None and f.fcf < 0
                and f.last_eps_surprise is not None and f.last_eps_surprise < 0):
            gates.append("Negative FCF + missed last quarter: capped at HOLD")
            if verdict in ("BUY", "SELL"):
                verdict = "HOLD"

        if inp.sector_strength.in_confirmed_downtrend:
            gates.append("Sector in confirmed downtrend: capped at HOLD")
            if verdict == "BUY":
                verdict = "HOLD"

        top = sorted(breakdown, key=lambda b: abs(b["contribution"]), reverse=True)[:3]
        reasons = [f"{b['factor']} ({b['contribution']:+.0f})" for b in top]

        return {
            "verdict": verdict,
            "score": int(round(composite)),
            "breakdown": breakdown,
            "top_reasons": reasons,
            "gates_triggered": gates,
        }
