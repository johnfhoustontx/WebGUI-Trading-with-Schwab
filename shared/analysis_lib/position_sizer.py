"""
Position Sizer - Blueprint Position Sizing Calculator
Version: 1.0.0
Last Updated: 2025-01-01

Implements Blueprint Section 8 Position Sizing Framework.

Version 1.0.0 Changes:
- Initial implementation
- Style-specific risk percentages
- ATR volatility adjustment
- Correlation adjustment
- Maximum position limits
"""

import logging
from typing import Dict
from dataclasses import dataclass
from enum import Enum

from config import PORTFOLIO, RISK

logger = logging.getLogger(__name__)

#############################################
# DATA CLASSES
#############################################

class SizingStyle(Enum):
    SWING = "swing"
    MOMENTUM = "momentum"
    SPECULATIVE = "speculative"
    BUY_HOLD = "buy_hold"


@dataclass
class PositionSize:
    """Position sizing calculation result"""
    shares: int
    position_value: float
    dollar_risk: float
    risk_pct_portfolio: float
    pct_of_allocation: float
    pct_of_total: float
    atr_multiplier: float
    correlation_multiplier: float
    warnings: list
    
    def to_dict(self) -> Dict:
        return {
            'shares': self.shares,
            'position_value': self.position_value,
            'dollar_risk': self.dollar_risk,
            'risk_pct_portfolio': self.risk_pct_portfolio,
            'pct_of_allocation': self.pct_of_allocation,
            'pct_of_total': self.pct_of_total,
            'atr_multiplier': self.atr_multiplier,
            'correlation_multiplier': self.correlation_multiplier,
            'warnings': self.warnings
        }


#############################################
# POSITION SIZER
#############################################

class PositionSizer:
    """Calculate position sizes per Blueprint rules"""
    
    def __init__(
        self,
        total_capital: float = None,
        swing_capital: float = None,
        momentum_capital: float = None,
        speculative_capital: float = None,
        buy_hold_capital: float = None
    ):
        """Initialize with portfolio capital
        
        Args:
            total_capital: Total portfolio value (default from config)
            swing_capital: Swing trading allocation
            momentum_capital: Momentum trading allocation
            speculative_capital: Speculative trading allocation
            buy_hold_capital: Buy & hold allocation
        """
        self.total_capital = total_capital or PORTFOLIO.total_capital
        self.swing_capital = swing_capital or PORTFOLIO.swing_capital
        self.momentum_capital = momentum_capital or PORTFOLIO.momentum_capital
        self.speculative_capital = speculative_capital or PORTFOLIO.speculative_capital
        self.buy_hold_capital = buy_hold_capital or PORTFOLIO.buy_hold_capital
        
        # Risk parameters
        self.swing_risk_pct = RISK.swing_risk_pct
        self.momentum_risk_pct = RISK.momentum_risk_pct
        self.speculative_risk_pct = RISK.speculative_risk_pct
        
        # Max position limits
        self.swing_max = RISK.swing_max_position
        self.momentum_max = RISK.momentum_max_position
        self.speculative_max = RISK.speculative_max_position
        self.single_stock_max_pct = RISK.single_stock_max_pct
    
    def calculate(
        self,
        style: SizingStyle,
        entry_price: float,
        stop_price: float,
        atr_percent: float = None,
        same_sector_positions: int = 0,
        same_subindustry: bool = False,
        deployment_pct: float = 100.0
    ) -> PositionSize:
        """Calculate position size
        
        Args:
            style: Trading style
            entry_price: Planned entry price
            stop_price: Stop loss price
            atr_percent: ATR as percentage of price (for volatility adjustment)
            same_sector_positions: Number of existing positions in same sector
            same_subindustry: True if same sub-industry as existing position
            deployment_pct: Current deployment level from macro score
            
        Returns:
            PositionSize with all calculations
        """
        warnings = []
        
        # Step 1: Get base risk amount
        base_risk = self._get_base_risk(style)
        
        # Adjust for deployment level
        adjusted_risk = base_risk * (deployment_pct / 100.0)
        
        # Step 2: Calculate dollar risk per share
        dollar_risk_per_share = abs(entry_price - stop_price)
        
        if dollar_risk_per_share <= 0:
            warnings.append("Invalid stop price - must be different from entry")
            return PositionSize(
                shares=0, position_value=0, dollar_risk=0,
                risk_pct_portfolio=0, pct_of_allocation=0, pct_of_total=0,
                atr_multiplier=1.0, correlation_multiplier=1.0,
                warnings=warnings
            )
        
        # Step 3: Calculate raw shares
        raw_shares = adjusted_risk / dollar_risk_per_share
        
        # Step 4: Apply ATR volatility adjustment
        atr_mult = self._get_atr_multiplier(atr_percent) if atr_percent else 1.0
        adjusted_shares = raw_shares * atr_mult
        
        # Step 5: Apply correlation adjustment
        corr_mult = self._get_correlation_multiplier(
            same_sector_positions, same_subindustry
        )
        adjusted_shares = adjusted_shares * corr_mult
        
        if corr_mult < 1.0:
            warnings.append(f"Correlation adjustment: {corr_mult:.2f}x due to sector overlap")
        
        # Step 6: Check maximum position limits
        position_value = adjusted_shares * entry_price
        max_position = self._get_max_position(style)
        
        if position_value > max_position:
            adjusted_shares = max_position / entry_price
            position_value = max_position
            warnings.append(f"Position capped at ${max_position:,.0f} max for {style.value}")
        
        # Check single stock max (10% of total)
        single_max = self.total_capital * self.single_stock_max_pct
        if position_value > single_max:
            adjusted_shares = single_max / entry_price
            position_value = single_max
            warnings.append(f"Position capped at ${single_max:,.0f} (10% total portfolio)")
        
        # Final calculations
        final_shares = int(adjusted_shares)
        final_value = final_shares * entry_price
        final_risk = final_shares * dollar_risk_per_share
        
        # Percentages
        allocation = self._get_style_capital(style)
        pct_of_allocation = (final_value / allocation * 100) if allocation > 0 else 0
        pct_of_total = (final_value / self.total_capital * 100)
        risk_pct = (final_risk / self.total_capital * 100)
        
        return PositionSize(
            shares=final_shares,
            position_value=round(final_value, 2),
            dollar_risk=round(final_risk, 2),
            risk_pct_portfolio=round(risk_pct, 3),
            pct_of_allocation=round(pct_of_allocation, 1),
            pct_of_total=round(pct_of_total, 1),
            atr_multiplier=atr_mult,
            correlation_multiplier=corr_mult,
            warnings=warnings
        )
    
    def _get_base_risk(self, style: SizingStyle) -> float:
        """Get base risk amount for style"""
        if style == SizingStyle.SWING:
            return self.swing_capital * self.swing_risk_pct
        elif style == SizingStyle.MOMENTUM:
            return self.momentum_capital * self.momentum_risk_pct
        elif style == SizingStyle.SPECULATIVE:
            # Speculative uses total portfolio percentage
            return self.total_capital * self.speculative_risk_pct
        else:  # BUY_HOLD
            # B&H uses position-based sizing, not risk-based
            # Return a default for calculation purposes
            return self.buy_hold_capital * 0.05  # 5% of B&H allocation
    
    def _get_style_capital(self, style: SizingStyle) -> float:
        """Get capital allocation for style"""
        if style == SizingStyle.SWING:
            return self.swing_capital
        elif style == SizingStyle.MOMENTUM:
            return self.momentum_capital
        elif style == SizingStyle.SPECULATIVE:
            return self.speculative_capital
        else:
            return self.buy_hold_capital
    
    def _get_max_position(self, style: SizingStyle) -> float:
        """Get maximum position value for style"""
        if style == SizingStyle.SWING:
            return self.swing_max
        elif style == SizingStyle.MOMENTUM:
            return self.momentum_max
        elif style == SizingStyle.SPECULATIVE:
            return self.speculative_max
        else:
            return self.buy_hold_capital * 0.15  # 15% of B&H
    
    def _get_atr_multiplier(self, atr_pct: float) -> float:
        """Get position size multiplier based on ATR%
        
        Per Blueprint Section 8:
        - ATR% < 2%: 1.25x (low vol, size up)
        - ATR% 2-4%: 1.00x (normal)
        - ATR% 4-6%: 0.75x (high vol, size down)
        - ATR% > 6%: 0.50x (very high vol)
        """
        if atr_pct is None:
            return 1.0
        
        if atr_pct < 0.02:
            return RISK.atr_multipliers['very_low']
        elif atr_pct < 0.04:
            return RISK.atr_multipliers['normal']
        elif atr_pct < 0.06:
            return RISK.atr_multipliers['high']
        else:
            return RISK.atr_multipliers['very_high']
    
    def _get_correlation_multiplier(
        self, 
        same_sector_positions: int,
        same_subindustry: bool
    ) -> float:
        """Get position size multiplier based on correlation
        
        Per Blueprint Section 8:
        - Unrelated sector: 1.0x
        - Same sector, different sub-industry: 0.75x
        - Same sub-industry: 0.50x
        - Direct competitor: 0.25x (or skip)
        """
        if same_subindustry:
            return 0.50
        elif same_sector_positions > 0:
            return 0.75
        else:
            return 1.0
    
    def format_calculation(
        self,
        symbol: str,
        style: SizingStyle,
        entry_price: float,
        stop_price: float,
        result: PositionSize
    ) -> str:
        """Format position size calculation as text"""
        
        risk_per_share = abs(entry_price - stop_price)
        stop_pct = abs(entry_price - stop_price) / entry_price * 100
        
        lines = [
            "=" * 60,
            f"POSITION SIZE: {symbol}",
            "=" * 60,
            f"Style: {style.value.upper()}",
            "",
            "TRADE PARAMETERS:",
            f"  Entry Price:     ${entry_price:,.2f}",
            f"  Stop Price:      ${stop_price:,.2f}",
            f"  Risk/Share:      ${risk_per_share:,.2f} ({stop_pct:.1f}%)",
            "",
            "ADJUSTMENTS:",
            f"  ATR Multiplier:  {result.atr_multiplier:.2f}x",
            f"  Corr Multiplier: {result.correlation_multiplier:.2f}x",
            "",
            "-" * 60,
            "RESULT:",
            f"  Shares:          {result.shares:,}",
            f"  Position Value:  ${result.position_value:,.2f}",
            f"  Dollar Risk:     ${result.dollar_risk:,.2f}",
            f"  Risk % Portfolio: {result.risk_pct_portfolio:.2f}%",
            f"  % of Allocation: {result.pct_of_allocation:.1f}%",
            f"  % of Total:      {result.pct_of_total:.1f}%",
            "-" * 60,
        ]
        
        if result.warnings:
            lines.append("WARNINGS:")
            for w in result.warnings:
                lines.append(f"  ⚠️  {w}")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)


#############################################
# QUICK CALCULATION FUNCTION
#############################################

def quick_size(
    style: str,
    entry: float,
    stop: float,
    atr_pct: float = None,
    total_capital: float = 300_000
) -> Dict:
    """Quick position size calculation
    
    Args:
        style: 'swing', 'momentum', 'speculative', or 'buy_hold'
        entry: Entry price
        stop: Stop price
        atr_pct: ATR as decimal (e.g., 0.03 for 3%)
        total_capital: Total portfolio value
        
    Returns:
        Dict with shares, value, risk
    """
    try:
        sizing_style = SizingStyle(style.lower())
    except ValueError:
        return {'error': f'Invalid style: {style}'}
    
    sizer = PositionSizer(total_capital=total_capital)
    result = sizer.calculate(
        style=sizing_style,
        entry_price=entry,
        stop_price=stop,
        atr_percent=atr_pct
    )
    
    return {
        'shares': result.shares,
        'position_value': result.position_value,
        'dollar_risk': result.dollar_risk,
        'risk_pct': result.risk_pct_portfolio,
        'warnings': result.warnings
    }
