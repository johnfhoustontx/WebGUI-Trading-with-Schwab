"""Sector strength module.

Computes a tailwind/headwind score (-100..+100) from a sector ETF's history
versus SPY, plus a sector-name -> ETF lookup.
"""
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


SECTOR_TO_ETF = {
    "Technology": "XLK", "Healthcare": "XLV", "Financials": "XLF",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP",
    "Energy": "XLE", "Industrials": "XLI", "Materials": "XLB",
    "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC",
}


@dataclass
class SectorStrength:
    score: int
    in_confirmed_downtrend: bool
    sector_above_50ema: bool
    rs_3m_percentile: float
    # The mirror of ``in_confirmed_downtrend``, for the short side: a sector
    # both above its 50-EMA and leading the market is a headwind to shorting
    # anything inside it. Defaulted so every existing construction site (and
    # every fixture) keeps working unchanged.
    in_confirmed_uptrend: bool = False


def _three_month_return(history: pd.DataFrame, lookback: int = 63) -> float:
    if history is None or "close" not in history.columns:
        return 0.0
    closes = history["close"].dropna()
    if len(closes) <= lookback:
        return 0.0
    last = float(closes.iloc[-1])
    prior = float(closes.iloc[-1 - lookback])
    if prior == 0:
        return 0.0
    return (last / prior) - 1.0


def compute_sector_strength(
    sector_history: pd.DataFrame,
    spy_history: pd.DataFrame,
) -> SectorStrength:
    sector_3m = _three_month_return(sector_history)
    spy_3m = _three_month_return(spy_history)

    excess = sector_3m - spy_3m
    rs_pct = float(np.clip(0.5 + excess / 0.20, 0.0, 1.0))

    closes = sector_history["close"].dropna()
    ema50 = closes.ewm(span=50, adjust=False).mean()
    above_50ema = bool(float(closes.iloc[-1]) > float(ema50.iloc[-1]))

    trend_score = 50 if above_50ema else -50
    rs_score = round((rs_pct - 0.5) * 100)
    score = int(np.clip(trend_score + rs_score, -100, 100))

    in_confirmed_downtrend = (not above_50ema) and (rs_pct < 0.20)
    in_confirmed_uptrend = above_50ema and (rs_pct > 0.80)

    return SectorStrength(
        score=score,
        in_confirmed_downtrend=in_confirmed_downtrend,
        sector_above_50ema=above_50ema,
        rs_3m_percentile=rs_pct,
        in_confirmed_uptrend=in_confirmed_uptrend,
    )


def sector_etf_for(sector_name: Optional[str]) -> Optional[str]:
    if not sector_name:
        return None
    return SECTOR_TO_ETF.get(sector_name)
