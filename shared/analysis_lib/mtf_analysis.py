"""
Multi-Timeframe Analysis - Weekly and Monthly trend confirmation
Version: 1.0.0

Analyzes multiple timeframes to confirm trend alignment and identify
support/resistance levels across daily, weekly, and monthly charts.
"""

import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from enum import Enum

import pandas as pd
import numpy as np

from technical import calculate_sma, calculate_ema, calculate_rsi, calculate_atr

logger = logging.getLogger(__name__)


class TrendDirection(Enum):
    """Trend direction"""
    STRONG_UP = "strong_uptrend"
    UP = "uptrend"
    NEUTRAL = "neutral"
    DOWN = "downtrend"
    STRONG_DOWN = "strong_downtrend"


class TimeframeAlignment(Enum):
    """Multi-timeframe alignment status"""
    FULLY_ALIGNED_BULL = "fully_aligned_bullish"
    MOSTLY_ALIGNED_BULL = "mostly_aligned_bullish"
    MIXED = "mixed"
    MOSTLY_ALIGNED_BEAR = "mostly_aligned_bearish"
    FULLY_ALIGNED_BEAR = "fully_aligned_bearish"


@dataclass
class TimeframeTrend:
    """Trend analysis for a single timeframe"""
    timeframe: str  # daily, weekly, monthly
    trend: TrendDirection
    price: float
    sma_20: float
    sma_50: float
    sma_200: float
    above_20: bool
    above_50: bool
    above_200: bool
    rsi: float
    atr_pct: float
    trend_score: int  # -10 to +10


@dataclass
class MultiTimeframeAnalysis:
    """Complete multi-timeframe analysis"""
    symbol: str
    daily: TimeframeTrend
    weekly: Optional[TimeframeTrend]
    monthly: Optional[TimeframeTrend]
    alignment: TimeframeAlignment
    alignment_score: float  # 0-100
    key_levels: Dict[str, List[float]]  # support/resistance by timeframe
    recommendation: str
    notes: List[str]


class MTFAnalyzer:
    """Multi-Timeframe Analyzer"""

    def __init__(self, market_data_provider=None):
        """Initialize analyzer

        Args:
            market_data_provider: MarketDataProvider instance for fetching data
        """
        self.provider = market_data_provider

    def analyze(
        self,
        symbol: str,
        daily_df: pd.DataFrame = None,
        weekly_df: pd.DataFrame = None,
        monthly_df: pd.DataFrame = None
    ) -> MultiTimeframeAnalysis:
        """Perform multi-timeframe analysis

        Args:
            symbol: Stock symbol
            daily_df: Daily price DataFrame
            weekly_df: Weekly price DataFrame (fetched if None)
            monthly_df: Monthly price DataFrame (fetched if None)

        Returns:
            MultiTimeframeAnalysis result
        """
        # Fetch data if not provided and provider available
        if self.provider and (weekly_df is None or monthly_df is None):
            mtf_data = self.provider.get_multi_timeframe_data(symbol)
            if weekly_df is None:
                weekly_df = mtf_data.get('weekly')
            if monthly_df is None:
                monthly_df = mtf_data.get('monthly')

        # Analyze each timeframe
        daily_trend = self._analyze_timeframe(daily_df, 'daily') if daily_df is not None else None
        weekly_trend = self._analyze_timeframe(weekly_df, 'weekly') if weekly_df is not None else None
        monthly_trend = self._analyze_timeframe(monthly_df, 'monthly') if monthly_df is not None else None

        if daily_trend is None:
            raise ValueError("Daily data is required for MTF analysis")

        # Calculate alignment
        alignment, alignment_score = self._calculate_alignment(
            daily_trend, weekly_trend, monthly_trend
        )

        # Identify key levels
        key_levels = self._identify_key_levels(daily_df, weekly_df, monthly_df)

        # Generate recommendation
        recommendation, notes = self._generate_recommendation(
            daily_trend, weekly_trend, monthly_trend, alignment
        )

        return MultiTimeframeAnalysis(
            symbol=symbol,
            daily=daily_trend,
            weekly=weekly_trend,
            monthly=monthly_trend,
            alignment=alignment,
            alignment_score=alignment_score,
            key_levels=key_levels,
            recommendation=recommendation,
            notes=notes
        )

    def _analyze_timeframe(self, df: pd.DataFrame, timeframe: str) -> Optional[TimeframeTrend]:
        """Analyze a single timeframe"""
        if df is None or len(df) < 50:
            return None

        try:
            # Get close price column
            close_col = 'close' if 'close' in df.columns else 'Close'
            close = df[close_col]
            current_price = float(close.iloc[-1])

            # Calculate moving averages
            sma_20 = float(close.rolling(20).mean().iloc[-1])
            sma_50 = float(close.rolling(50).mean().iloc[-1]) if len(df) >= 50 else sma_20
            sma_200 = float(close.rolling(200).mean().iloc[-1]) if len(df) >= 200 else sma_50

            # Position relative to MAs
            above_20 = current_price > sma_20
            above_50 = current_price > sma_50
            above_200 = current_price > sma_200

            # Calculate RSI
            rsi = calculate_rsi(df) if len(df) >= 14 else 50.0

            # Calculate ATR%
            atr = calculate_atr(df) if len(df) >= 14 else 0
            atr_pct = (atr / current_price * 100) if current_price > 0 else 0

            # Determine trend and score
            trend, trend_score = self._determine_trend(
                current_price, sma_20, sma_50, sma_200, rsi, df
            )

            return TimeframeTrend(
                timeframe=timeframe,
                trend=trend,
                price=current_price,
                sma_20=sma_20,
                sma_50=sma_50,
                sma_200=sma_200,
                above_20=above_20,
                above_50=above_50,
                above_200=above_200,
                rsi=rsi,
                atr_pct=atr_pct,
                trend_score=trend_score
            )

        except Exception as e:
            logger.error(f"Error analyzing {timeframe}: {e}")
            return None

    def _determine_trend(
        self,
        price: float,
        sma_20: float,
        sma_50: float,
        sma_200: float,
        rsi: float,
        df: pd.DataFrame
    ) -> Tuple[TrendDirection, int]:
        """Determine trend direction and score"""
        score = 0

        # Price vs MAs (max 6 points)
        if price > sma_20:
            score += 2
        else:
            score -= 2

        if price > sma_50:
            score += 2
        else:
            score -= 2

        if price > sma_200:
            score += 2
        else:
            score -= 2

        # MA alignment (max 4 points)
        if sma_20 > sma_50 > sma_200:
            score += 4  # Perfect bullish alignment
        elif sma_20 < sma_50 < sma_200:
            score -= 4  # Perfect bearish alignment
        elif sma_20 > sma_50:
            score += 2
        elif sma_20 < sma_50:
            score -= 2

        # MA direction (max 2 points)
        close_col = 'close' if 'close' in df.columns else 'Close'
        if len(df) >= 25:
            sma_20_prev = float(df[close_col].rolling(20).mean().iloc[-5])
            if sma_20 > sma_20_prev:
                score += 1
            else:
                score -= 1

        # RSI confirmation
        if rsi > 60:
            score += 1
        elif rsi < 40:
            score -= 1

        # Determine trend category
        if score >= 8:
            trend = TrendDirection.STRONG_UP
        elif score >= 4:
            trend = TrendDirection.UP
        elif score <= -8:
            trend = TrendDirection.STRONG_DOWN
        elif score <= -4:
            trend = TrendDirection.DOWN
        else:
            trend = TrendDirection.NEUTRAL

        return trend, score

    def _calculate_alignment(
        self,
        daily: TimeframeTrend,
        weekly: Optional[TimeframeTrend],
        monthly: Optional[TimeframeTrend]
    ) -> Tuple[TimeframeAlignment, float]:
        """Calculate timeframe alignment"""
        scores = [daily.trend_score]

        if weekly:
            scores.append(weekly.trend_score)
        if monthly:
            scores.append(monthly.trend_score)

        avg_score = sum(scores) / len(scores)

        # Check if all timeframes agree
        all_bullish = all(s > 0 for s in scores)
        all_bearish = all(s < 0 for s in scores)
        mostly_bullish = sum(1 for s in scores if s > 0) >= len(scores) - 1
        mostly_bearish = sum(1 for s in scores if s < 0) >= len(scores) - 1

        if all_bullish and avg_score >= 5:
            alignment = TimeframeAlignment.FULLY_ALIGNED_BULL
            alignment_score = 90 + min(avg_score, 10)
        elif all_bullish or mostly_bullish:
            alignment = TimeframeAlignment.MOSTLY_ALIGNED_BULL
            alignment_score = 70 + min(avg_score * 2, 20)
        elif all_bearish and avg_score <= -5:
            alignment = TimeframeAlignment.FULLY_ALIGNED_BEAR
            alignment_score = 10 - min(abs(avg_score), 10)
        elif all_bearish or mostly_bearish:
            alignment = TimeframeAlignment.MOSTLY_ALIGNED_BEAR
            alignment_score = 30 - min(abs(avg_score) * 2, 20)
        else:
            alignment = TimeframeAlignment.MIXED
            alignment_score = 50

        return alignment, alignment_score

    def _identify_key_levels(
        self,
        daily_df: pd.DataFrame,
        weekly_df: pd.DataFrame,
        monthly_df: pd.DataFrame
    ) -> Dict[str, List[float]]:
        """Identify key support/resistance levels from each timeframe"""
        levels = {
            'daily_support': [],
            'daily_resistance': [],
            'weekly_support': [],
            'weekly_resistance': [],
            'monthly_support': [],
            'monthly_resistance': [],
        }

        for df, prefix in [(daily_df, 'daily'), (weekly_df, 'weekly'), (monthly_df, 'monthly')]:
            if df is None or len(df) < 20:
                continue

            close_col = 'close' if 'close' in df.columns else 'Close'
            high_col = 'high' if 'high' in df.columns else 'High'
            low_col = 'low' if 'low' in df.columns else 'Low'

            close = df[close_col]
            high = df[high_col] if high_col in df.columns else close
            low = df[low_col] if low_col in df.columns else close

            current = float(close.iloc[-1])

            # Moving average levels
            sma_20 = float(close.rolling(20).mean().iloc[-1])
            sma_50 = float(close.rolling(50).mean().iloc[-1]) if len(df) >= 50 else None
            sma_200 = float(close.rolling(200).mean().iloc[-1]) if len(df) >= 200 else None

            # Recent swing highs/lows
            recent_high = float(high.tail(20).max())
            recent_low = float(low.tail(20).min())

            # Classify as support or resistance
            for level in [sma_20, sma_50, sma_200, recent_high, recent_low]:
                if level is None:
                    continue
                if level < current:
                    levels[f'{prefix}_support'].append(round(level, 2))
                else:
                    levels[f'{prefix}_resistance'].append(round(level, 2))

            # Sort and deduplicate
            levels[f'{prefix}_support'] = sorted(set(levels[f'{prefix}_support']), reverse=True)[:3]
            levels[f'{prefix}_resistance'] = sorted(set(levels[f'{prefix}_resistance']))[:3]

        return levels

    def _generate_recommendation(
        self,
        daily: TimeframeTrend,
        weekly: Optional[TimeframeTrend],
        monthly: Optional[TimeframeTrend],
        alignment: TimeframeAlignment
    ) -> Tuple[str, List[str]]:
        """Generate trading recommendation based on MTF analysis"""
        notes = []

        # Daily trend note
        notes.append(f"Daily: {daily.trend.value} (score: {daily.trend_score:+d})")

        if weekly:
            notes.append(f"Weekly: {weekly.trend.value} (score: {weekly.trend_score:+d})")
        if monthly:
            notes.append(f"Monthly: {monthly.trend.value} (score: {monthly.trend_score:+d})")

        # Generate recommendation
        if alignment == TimeframeAlignment.FULLY_ALIGNED_BULL:
            recommendation = "STRONG BUY - All timeframes aligned bullish"
            notes.append("Ideal conditions for swing/position trades")
            if daily.rsi > 70:
                notes.append("Caution: Daily RSI overbought, wait for pullback")

        elif alignment == TimeframeAlignment.MOSTLY_ALIGNED_BULL:
            recommendation = "BUY - Mostly bullish across timeframes"
            notes.append("Good setup, monitor lower timeframe for entry")

        elif alignment == TimeframeAlignment.FULLY_ALIGNED_BEAR:
            recommendation = "AVOID/SHORT - All timeframes aligned bearish"
            notes.append("Not suitable for long positions")
            if daily.rsi < 30:
                notes.append("Note: Daily RSI oversold, bounce possible")

        elif alignment == TimeframeAlignment.MOSTLY_ALIGNED_BEAR:
            recommendation = "AVOID - Mostly bearish across timeframes"
            notes.append("Wait for trend change before entering long")

        else:  # MIXED
            recommendation = "NEUTRAL - Mixed signals across timeframes"
            notes.append("Wait for clearer alignment before trading")

            if daily.trend_score > 0 and (weekly is None or weekly.trend_score < 0):
                notes.append("Daily bullish but weekly bearish - potential counter-trend")

        return recommendation, notes

    def get_trend_summary(self, analysis: MultiTimeframeAnalysis) -> str:
        """Get formatted trend summary"""
        lines = [
            f"=== Multi-Timeframe Analysis: {analysis.symbol} ===",
            f"",
            f"Alignment: {analysis.alignment.value} ({analysis.alignment_score:.0f}%)",
            f"Recommendation: {analysis.recommendation}",
            f"",
            f"--- Trend by Timeframe ---"
        ]

        for tf in [analysis.daily, analysis.weekly, analysis.monthly]:
            if tf:
                ma_status = []
                if tf.above_20:
                    ma_status.append(">20")
                if tf.above_50:
                    ma_status.append(">50")
                if tf.above_200:
                    ma_status.append(">200")

                lines.append(
                    f"{tf.timeframe.upper():8} | {tf.trend.value:20} | "
                    f"RSI: {tf.rsi:.0f} | {', '.join(ma_status) or 'Below all MAs'}"
                )

        lines.append("")
        lines.append("--- Key Levels ---")

        for level_type in ['support', 'resistance']:
            for tf in ['daily', 'weekly', 'monthly']:
                key = f'{tf}_{level_type}'
                if analysis.key_levels.get(key):
                    levels_str = ', '.join(f'${l:.2f}' for l in analysis.key_levels[key])
                    lines.append(f"{tf.capitalize()} {level_type}: {levels_str}")

        lines.append("")
        lines.append("--- Notes ---")
        for note in analysis.notes:
            lines.append(f"  - {note}")

        return '\n'.join(lines)


def analyze_mtf(
    symbol: str,
    daily_df: pd.DataFrame,
    weekly_df: pd.DataFrame = None,
    monthly_df: pd.DataFrame = None,
    provider=None
) -> MultiTimeframeAnalysis:
    """Convenience function for MTF analysis"""
    analyzer = MTFAnalyzer(provider)
    return analyzer.analyze(symbol, daily_df, weekly_df, monthly_df)
