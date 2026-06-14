"""
Blueprint Analyzer - Stock Trading Analysis System
Version: 1.0.0

Implements Stock Trading Blueprint v2.0.0 methodology.
"""

__version__ = '1.0.0'
__author__ = 'Blueprint Trading System'

from .config import PORTFOLIO, RISK, SECTORS
from .schwab_client import SchwabClient, MarketDataClient
from .technical import (
    calculate_ema, calculate_sma, calculate_rsi, calculate_adx,
    calculate_atr, calculate_atr_percent, calculate_vwap,
    detect_trend_structure, calculate_relative_strength
)
from .sector_analysis import SectorRanker, get_sector_info
from .macro_score import MacroScoreCalculator, MacroScore
from .blueprint_scorer import BlueprintScorer, BlueprintScore, TradingStyle, Recommendation
from .position_sizer import PositionSizer, SizingStyle, quick_size

__all__ = [
    # Config
    'PORTFOLIO', 'RISK', 'SECTORS',
    
    # Data clients
    'SchwabClient', 'MarketDataClient',
    
    # Technical analysis
    'calculate_ema', 'calculate_sma', 'calculate_rsi', 'calculate_adx',
    'calculate_atr', 'calculate_atr_percent', 'calculate_vwap',
    'detect_trend_structure', 'calculate_relative_strength',
    
    # Sector analysis
    'SectorRanker', 'get_sector_info',
    
    # Macro scoring
    'MacroScoreCalculator', 'MacroScore',
    
    # Blueprint scoring
    'BlueprintScorer', 'BlueprintScore', 'TradingStyle', 'Recommendation',
    
    # Position sizing
    'PositionSizer', 'SizingStyle', 'quick_size',
]
