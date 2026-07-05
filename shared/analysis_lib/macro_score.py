"""
Macro Score Calculator - Weighted Macro Scoring System
Version: 1.0.0
Last Updated: 2025-01-01

Implements Blueprint Section 4.6 Weighted Macro Score System.

Version 1.0.0 Changes:
- Initial implementation
- SPY vs 200 DMA analysis
- VIX sentiment scoring
- Yield curve monitoring
- Deployment level calculation
"""

import logging
from typing import Optional, Dict
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from config import (
    MACRO_WEIGHTS, MACRO_DEPLOYMENT, VIX_LOW, VIX_NORMAL, VIX_HIGH, VIX_EXTREME
)
from technical import calculate_sma

logger = logging.getLogger(__name__)

#############################################
# DATA CLASSES
#############################################

@dataclass
class MacroIndicator:
    """Individual macro indicator with score"""
    name: str
    value: float
    score: float  # 0-10
    weight: float
    weighted_score: float
    status: str  # BULLISH, NEUTRAL, BEARISH
    description: str


@dataclass
class MacroScore:
    """Complete macro score assessment"""
    total_score: float  # 0-10 weighted
    deployment_pct: float  # 0-100
    regime: str  # FULL, DEFENSIVE, PRESERVATION
    indicators: Dict[str, MacroIndicator] = field(default_factory=dict)
    zbt_active: bool = False
    yield_curve_warning: bool = False
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            'total_score': self.total_score,
            'deployment_pct': self.deployment_pct,
            'regime': self.regime,
            'zbt_active': self.zbt_active,
            'yield_curve_warning': self.yield_curve_warning,
            'indicators': {
                k: {
                    'value': v.value,
                    'score': v.score,
                    'status': v.status,
                    'description': v.description
                }
                for k, v in self.indicators.items()
            }
        }


#############################################
# MACRO SCORE CALCULATOR
#############################################

class MacroScoreCalculator:
    """Calculate weighted macro score per Blueprint Section 4.6"""

    # Shared MarketDataProvider instance (lazy initialized)
    _market_data_provider = None

    def __init__(self, market_client):
        """
        Args:
            market_client: MarketDataClient instance
        """
        self.client = market_client
        self._last_score: Optional[MacroScore] = None

        # Manual inputs (can be updated)
        self.fed_policy = 'NEUTRAL'  # CUTTING, HOLDING_DOVISH, NEUTRAL, HOLDING_HAWKISH, HIKING
        self.event_risk_high = False

    def _get_market_data_provider(self):
        """Get or create shared MarketDataProvider instance"""
        # Use class-level cache but pass instance's Schwab client
        if MacroScoreCalculator._market_data_provider is None:
            try:
                from market_data import MarketDataProvider
                # Pass Schwab client for fast breadth data
                MacroScoreCalculator._market_data_provider = MarketDataProvider(
                    schwab_client=self.client
                )
            except Exception as e:
                logger.debug(f"Could not create MarketDataProvider: {e}")
        return MacroScoreCalculator._market_data_provider
    
    def calculate(
        self,
        spy_df: pd.DataFrame = None,
        vix_price: float = None
    ) -> MacroScore:
        """Calculate full macro score
        
        Args:
            spy_df: SPY daily DataFrame (fetched if None)
            vix_price: Current VIX level (fetched if None)
            
        Returns:
            MacroScore with all indicators
        """
        indicators = {}
        
        # Fetch data if not provided
        if spy_df is None:
            spy_df = self.client.get_daily('SPY', months=12)
        
        if vix_price is None:
            vix_quote = self.client.get_quote('VIX')
            vix_price = vix_quote['last'] if vix_quote else 20.0
        
        # 1. SPY vs 200 DMA (30% weight)
        spy_indicator = self._score_spy_vs_200dma(spy_df)
        indicators['spy_vs_200dma'] = spy_indicator
        
        # 2. Fed Policy (15% weight)
        fed_indicator = self._score_fed_policy()
        indicators['fed_policy'] = fed_indicator
        
        # 3. Yield Curve (15% weight)
        yield_indicator = self._score_yield_curve()
        indicators['yield_curve'] = yield_indicator
        
        # 4. Breadth - % above 50 DMA (10% weight) - Placeholder
        breadth_indicator = self._score_breadth()
        indicators['breadth_50dma'] = breadth_indicator
        
        # 5. A/D Line (10% weight) - Placeholder
        ad_indicator = self._score_ad_line()
        indicators['ad_line_trend'] = ad_indicator
        
        # 6. Economic Data (10% weight) - Placeholder
        econ_indicator = self._score_economic_data()
        indicators['economic_data'] = econ_indicator
        
        # 7. VIX Sentiment (5% weight)
        vix_indicator = self._score_vix(vix_price)
        indicators['vix_sentiment'] = vix_indicator
        
        # 8. Event Calendar (5% weight)
        event_indicator = self._score_event_calendar()
        indicators['event_calendar'] = event_indicator
        
        # Calculate total weighted score
        total_score = sum(ind.weighted_score for ind in indicators.values())
        
        # Check for ZBT (would need breadth data)
        zbt_active = self._check_zbt()
        
        # Check yield curve warning
        yield_warning = yield_indicator.status == 'BEARISH' and 're-steep' in yield_indicator.description.lower()
        
        # Determine deployment level
        if zbt_active:
            deployment_pct = 100.0
            regime = 'ZBT - Rare bullish signal, full deployment'
        else:
            deployment_pct = self._get_deployment_level(total_score)
            if deployment_pct >= 80:
                regime = 'Favorable - Normal position sizes'
            elif deployment_pct >= 50:
                regime = 'Caution - Tighter stops, defensive sectors'
            else:
                regime = 'Poor - Minimal new positions, protect capital'
        
        score = MacroScore(
            total_score=round(total_score, 2),
            deployment_pct=deployment_pct,
            regime=regime,
            indicators=indicators,
            zbt_active=zbt_active,
            yield_curve_warning=yield_warning
        )
        
        self._last_score = score
        return score
    
    def _score_spy_vs_200dma(self, spy_df: pd.DataFrame) -> MacroIndicator:
        """Score SPY position relative to 200 DMA (30% weight)"""
        weight = MACRO_WEIGHTS['spy_vs_200dma']
        
        if spy_df is None or len(spy_df) < 200:
            return MacroIndicator(
                name='SPY vs 200 DMA',
                value=0,
                score=5.0,
                weight=weight,
                weighted_score=5.0 * weight,
                status='NEUTRAL',
                description='Insufficient data'
            )
        
        current_price = float(spy_df['close'].iloc[-1])
        sma_200 = calculate_sma(spy_df, 200)
        sma_50 = calculate_sma(spy_df, 50)
        
        sma_200_val = float(sma_200.iloc[-1])
        sma_50_val = float(sma_50.iloc[-1]) if sma_50 is not None else sma_200_val
        
        # Calculate % above/below 200 DMA
        pct_vs_200 = (current_price / sma_200_val - 1) * 100
        
        # Check if 200 DMA is rising
        sma_200_prev = float(sma_200.iloc[-20])
        sma_200_rising = sma_200_val > sma_200_prev
        
        # Scoring logic per Blueprint Section 4.6
        if current_price > sma_50_val > sma_200_val and sma_200_rising:
            score = 10.0
            status = 'BULLISH'
            desc = f'Above rising 200 DMA (+{pct_vs_200:.1f}%), 50>200'
        elif current_price > sma_200_val and sma_50_val > sma_200_val:
            score = 8.0
            status = 'BULLISH'
            desc = f'Above 200 DMA (+{pct_vs_200:.1f}%), 50>200'
        elif current_price > sma_200_val:
            score = 7.0
            status = 'NEUTRAL'
            desc = f'Above 200 DMA (+{pct_vs_200:.1f}%), 50 rolling'
        elif abs(pct_vs_200) < 2:
            score = 5.0
            status = 'NEUTRAL'
            desc = f'Near 200 DMA ({pct_vs_200:+.1f}%)'
        elif current_price < sma_200_val and current_price > sma_200_val * 0.95:
            score = 3.0
            status = 'BEARISH'
            desc = f'Below 200 DMA ({pct_vs_200:.1f}%), testing'
        else:
            # Below falling 200, death cross potential
            if sma_50_val < sma_200_val:
                score = 0.0
                status = 'BEARISH'
                desc = f'Below falling 200 DMA ({pct_vs_200:.1f}%), DEATH CROSS'
            else:
                score = 2.0
                status = 'BEARISH'
                desc = f'Below 200 DMA ({pct_vs_200:.1f}%)'
        
        return MacroIndicator(
            name='SPY vs 200 DMA',
            value=round(pct_vs_200, 2),
            score=score,
            weight=weight,
            weighted_score=score * weight,
            status=status,
            description=desc
        )
    
    def _score_fed_policy(self) -> MacroIndicator:
        """Score Fed policy stance (15% weight)"""
        weight = MACRO_WEIGHTS['fed_policy']
        
        scores = {
            'CUTTING': (10.0, 'BULLISH', 'Fed actively cutting rates'),
            'HOLDING_DOVISH': (7.0, 'BULLISH', 'Fed paused, dovish guidance'),
            'NEUTRAL': (5.0, 'NEUTRAL', 'Fed data-dependent'),
            'HOLDING_HAWKISH': (3.0, 'BEARISH', 'Fed paused, hawkish guidance'),
            'HIKING': (0.0, 'BEARISH', 'Fed actively hiking rates'),
        }
        
        score, status, desc = scores.get(self.fed_policy, (5.0, 'NEUTRAL', 'Unknown'))
        
        return MacroIndicator(
            name='Fed Policy',
            value=0,
            score=score,
            weight=weight,
            weighted_score=score * weight,
            status=status,
            description=desc
        )
    
    def _score_yield_curve(self, yield_data=None) -> MacroIndicator:
        """Score yield curve status (15% weight)

        Uses FRED data when available via MarketDataProvider.
        """
        weight = MACRO_WEIGHTS['yield_curve']

        # Try to get real yield curve data (using cached provider)
        if yield_data is None:
            try:
                provider = self._get_market_data_provider()
                if provider:
                    yield_data = provider.get_yield_curve(use_cache=True)
            except Exception as e:
                logger.debug(f"Could not fetch yield curve: {e}")
                yield_data = None

        if yield_data:
            spread_bps = int(yield_data.spread_10y_2y * 100)
            curve_status = yield_data.status
        else:
            spread_bps = 0
            curve_status = 'FLAT'

        if spread_bps > 50:
            score = 10.0
            status = 'BULLISH'
            desc = f'Normal curve ({spread_bps:+d} bps), 2Y:{yield_data.yield_2y:.2f}% 10Y:{yield_data.yield_10y:.2f}%' if yield_data else f'Normal curve ({spread_bps:+d} bps)'
        elif spread_bps > 0:
            score = 7.0
            status = 'NEUTRAL'
            desc = f'Flat curve ({spread_bps:+d} bps), 2Y:{yield_data.yield_2y:.2f}% 10Y:{yield_data.yield_10y:.2f}%' if yield_data else f'Flat curve ({spread_bps:+d} bps)'
        elif spread_bps > -25:
            score = 5.0
            status = 'NEUTRAL'
            desc = f'Slightly inverted ({spread_bps:+d} bps)'
        elif spread_bps > -75:
            score = 3.0
            status = 'BEARISH'
            desc = f'Inverted curve ({spread_bps:+d} bps) - caution'
        else:
            score = 1.0
            status = 'BEARISH'
            desc = f'Deeply inverted ({spread_bps:+d} bps) - recession risk'

        return MacroIndicator(
            name='Yield Curve',
            value=spread_bps,
            score=score,
            weight=weight,
            weighted_score=score * weight,
            status=status,
            description=desc
        )
    
    def _score_breadth(self, breadth_data=None) -> MacroIndicator:
        """Score market breadth (10% weight)

        Uses yfinance-calculated breadth when available.
        """
        weight = MACRO_WEIGHTS['breadth_50dma']

        # Try to get real breadth data (using cached provider)
        if breadth_data is None:
            try:
                provider = self._get_market_data_provider()
                if provider:
                    breadth_data = provider.get_breadth_data(use_cache=True)
            except Exception as e:
                logger.debug(f"Could not fetch breadth data: {e}")
                breadth_data = None

        if breadth_data:
            pct_above_50 = breadth_data.pct_above_50dma
            pct_above_200 = breadth_data.pct_above_200dma
            breadth_status = breadth_data.status
        else:
            pct_above_50 = 55
            pct_above_200 = 50
            breadth_status = 'NEUTRAL'

        if pct_above_50 > 70:
            score = 10.0
            status = 'BULLISH'
            desc = f'{pct_above_50:.0f}% > 50 DMA, {pct_above_200:.0f}% > 200 DMA - strong breadth'
        elif pct_above_50 > 60:
            score = 8.0
            status = 'BULLISH'
            desc = f'{pct_above_50:.0f}% > 50 DMA, {pct_above_200:.0f}% > 200 DMA - healthy breadth'
        elif pct_above_50 > 50:
            score = 6.0
            status = 'NEUTRAL'
            desc = f'{pct_above_50:.0f}% > 50 DMA, {pct_above_200:.0f}% > 200 DMA - neutral'
        elif pct_above_50 > 40:
            score = 4.0
            status = 'NEUTRAL'
            desc = f'{pct_above_50:.0f}% > 50 DMA - weakening breadth'
        elif pct_above_50 > 25:
            score = 2.0
            status = 'BEARISH'
            desc = f'{pct_above_50:.0f}% > 50 DMA - weak breadth'
        else:
            score = 1.0
            status = 'BEARISH'
            desc = f'{pct_above_50:.0f}% > 50 DMA - extreme weakness (potential bottom)'

        return MacroIndicator(
            name='Market Breadth',
            value=pct_above_50,
            score=score,
            weight=weight,
            weighted_score=score * weight,
            status=status,
            description=desc
        )
    
    def _score_ad_line(self) -> MacroIndicator:
        """Score Advance/Decline line trend (10% weight)"""
        weight = MACRO_WEIGHTS['ad_line_trend']
        
        # Placeholder - assume neutral
        score = 6.0
        status = 'NEUTRAL'
        desc = 'A/D line data not available'
        
        return MacroIndicator(
            name='A/D Line Trend',
            value=0,
            score=score,
            weight=weight,
            weighted_score=score * weight,
            status=status,
            description=desc
        )
    
    def _score_economic_data(self) -> MacroIndicator:
        """Score economic data composite (10% weight)"""
        weight = MACRO_WEIGHTS['economic_data']
        
        # Placeholder - would aggregate ISM, Claims, etc.
        score = 6.0
        status = 'NEUTRAL'
        desc = 'Economic data composite: Neutral'
        
        return MacroIndicator(
            name='Economic Data',
            value=0,
            score=score,
            weight=weight,
            weighted_score=score * weight,
            status=status,
            description=desc
        )
    
    def _score_vix(self, vix_level: float) -> MacroIndicator:
        """Score VIX/sentiment (5% weight)"""
        weight = MACRO_WEIGHTS['vix_sentiment']
        
        if vix_level < VIX_LOW:
            score = 8.0  # Complacency can be risky
            status = 'NEUTRAL'
            desc = f'VIX {vix_level:.1f} - Low volatility (complacent)'
        elif vix_level < VIX_NORMAL:
            score = 10.0
            status = 'BULLISH'
            desc = f'VIX {vix_level:.1f} - Normal range'
        elif vix_level < VIX_HIGH:
            score = 5.0
            status = 'NEUTRAL'
            desc = f'VIX {vix_level:.1f} - Elevated'
        elif vix_level < VIX_EXTREME:
            score = 3.0
            status = 'BEARISH'
            desc = f'VIX {vix_level:.1f} - High fear'
        else:
            score = 1.0
            status = 'BEARISH'
            desc = f'VIX {vix_level:.1f} - EXTREME fear'
        
        return MacroIndicator(
            name='VIX Sentiment',
            value=vix_level,
            score=score,
            weight=weight,
            weighted_score=score * weight,
            status=status,
            description=desc
        )
    
    def _score_event_calendar(self) -> MacroIndicator:
        """Score event calendar risk (5% weight)"""
        weight = MACRO_WEIGHTS['event_calendar']
        
        if self.event_risk_high:
            score = 3.0
            status = 'BEARISH'
            desc = 'High event risk (Fed, CPI, etc.)'
        else:
            score = 8.0
            status = 'BULLISH'
            desc = 'No major events imminent'
        
        return MacroIndicator(
            name='Event Calendar',
            value=0,
            score=score,
            weight=weight,
            weighted_score=score * weight,
            status=status,
            description=desc
        )
    
    def _check_zbt(self) -> bool:
        """Check for Zweig Breadth Thrust
        
        Note: Would need NYSE breadth data for accurate detection.
        """
        # Placeholder - ZBT is rare, default to False
        return False
    
    def _get_deployment_level(self, score: float) -> float:
        """Get deployment percentage based on macro score"""
        for (low, high), pct in MACRO_DEPLOYMENT.items():
            if low <= score < high:
                return pct * 100
        return 20.0  # Default to preservation
    
    def set_fed_policy(self, policy: str):
        """Update Fed policy assessment
        
        Args:
            policy: One of CUTTING, HOLDING_DOVISH, NEUTRAL, HOLDING_HAWKISH, HIKING
        """
        valid = ['CUTTING', 'HOLDING_DOVISH', 'NEUTRAL', 'HOLDING_HAWKISH', 'HIKING']
        if policy.upper() in valid:
            self.fed_policy = policy.upper()
            logger.info(f"Fed policy updated to: {self.fed_policy}")
        else:
            logger.warning(f"Invalid fed policy: {policy}. Use one of {valid}")
    
    def set_event_risk(self, high_risk: bool):
        """Set event risk flag"""
        self.event_risk_high = high_risk
    
    def get_last_score(self) -> Optional[MacroScore]:
        """Get most recent macro score"""
        return self._last_score
    
    def format_report(self, score: MacroScore = None) -> str:
        """Format macro score as text report"""
        if score is None:
            score = self._last_score
        
        if score is None:
            return "No macro score available. Run calculate() first."
        
        lines = [
            "=" * 70,
            "MACRO SCORE REPORT",
            "=" * 70,
            f"Timestamp: {score.timestamp.strftime('%Y-%m-%d %H:%M')}",
            "",
            f"TOTAL SCORE: {score.total_score:.2f} / 10.00",
            f"DEPLOYMENT:  {score.deployment_pct:.0f}%",
            f"REGIME:      {score.regime}",
            "",
        ]
        
        if score.zbt_active:
            lines.append("🚀 ZWEIG BREADTH THRUST ACTIVE - OVERRIDE TO 100% DEPLOYMENT")
            lines.append("")
        
        if score.yield_curve_warning:
            lines.append("⚠️  YIELD CURVE RE-STEEPENING WARNING - MAX DEFENSIVE")
            lines.append("")
        
        lines.append("-" * 70)
        lines.append(f"{'Indicator':<25} {'Value':<10} {'Score':<8} {'Weight':<8} {'Status':<10}")
        lines.append("-" * 70)
        
        for name, ind in score.indicators.items():
            val_str = f"{ind.value:.1f}" if isinstance(ind.value, float) else str(ind.value)
            lines.append(
                f"{ind.name:<25} {val_str:<10} {ind.score:<8.1f} {ind.weight*100:<7.0f}% {ind.status:<10}"
            )
        
        lines.append("-" * 70)
        lines.append("")
        lines.append("DETAILS:")
        for name, ind in score.indicators.items():
            lines.append(f"  • {ind.name}: {ind.description}")
        
        lines.append("")
        lines.append("=" * 70)
        
        # Deployment guidance
        if score.deployment_pct >= 80:
            lines.append("GUIDANCE: Full deployment allowed. Normal position sizes.")
        elif score.deployment_pct >= 50:
            lines.append("GUIDANCE: Reduced deployment. Tighter stops, defensive sectors.")
        else:
            lines.append("GUIDANCE: Capital preservation mode. Minimal new positions.")
        
        lines.append("=" * 70)
        
        return "\n".join(lines)
