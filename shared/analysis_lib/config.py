"""
Configuration - Blueprint Trading System Constants and Settings
Version: 1.0.0
Last Updated: 2025-01-01

Version 1.0.0 Changes:
- Initial implementation
- Portfolio allocation constants
- Risk parameters per style
- Sector/Industry mappings
"""

from pathlib import Path
from dataclasses import dataclass
from typing import Dict

#############################################
# PATH CONFIGURATION
#############################################

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
CACHE_DIR = PROJECT_ROOT / "cache"

# Schwab API config location. PROJECT_ROOT resolves to the monorepo's shared/
# folder, so this points at shared/appsettings.json (the consolidated location).
SCHWAB_CONFIG_PATHS = [
    PROJECT_ROOT / "appsettings.json",
]

#############################################
# PORTFOLIO CONFIGURATION
#############################################

@dataclass
class PortfolioConfig:
    """Portfolio allocation settings from Blueprint v2.0.0"""
    total_capital: float = 300_000.0
    
    # Style allocations (Section 1)
    swing_pct: float = 0.40
    buy_hold_pct: float = 0.40
    momentum_pct: float = 0.10
    speculative_pct: float = 0.10
    
    @property
    def swing_capital(self) -> float:
        return self.total_capital * self.swing_pct
    
    @property
    def buy_hold_capital(self) -> float:
        return self.total_capital * self.buy_hold_pct
    
    @property
    def momentum_capital(self) -> float:
        return self.total_capital * self.momentum_pct
    
    @property
    def speculative_capital(self) -> float:
        return self.total_capital * self.speculative_pct


# Default portfolio config
PORTFOLIO = PortfolioConfig()

#############################################
# RISK PARAMETERS (Section 8 & 9)
#############################################

@dataclass
class RiskConfig:
    """Risk parameters per trading style"""
    # Risk per trade as percentage of style capital
    swing_risk_pct: float = 0.01       # 1.0% = $1,200 on $120k
    momentum_risk_pct: float = 0.005   # 0.5% = $300 on $60k  
    speculative_risk_pct: float = 0.0025  # 0.25% of total = $750
    
    # Maximum position sizes
    swing_max_position: float = 30_000      # 25% of swing capital
    momentum_max_position: float = 15_000   # 25% of momentum capital
    speculative_max_position: float = 10_000  # 33% of spec capital
    single_stock_max_pct: float = 0.10      # 10% of total portfolio
    
    # Maximum positions per style
    swing_max_positions: int = 6
    momentum_max_positions: int = 4
    speculative_max_positions: int = 3
    buy_hold_max_positions: int = 15
    
    # Daily/Weekly limits
    daily_loss_limit: float = 6_000   # 2% of total
    weekly_loss_limit: float = 12_000  # 4% of total
    
    # ATR% position size multipliers (Section 8)
    atr_multipliers: Dict[str, float] = None
    
    def __post_init__(self):
        if self.atr_multipliers is None:
            self.atr_multipliers = {
                'very_low': 1.25,   # ATR% < 2%
                'normal': 1.00,     # ATR% 2-4%
                'high': 0.75,       # ATR% 4-6%
                'very_high': 0.50,  # ATR% > 6%
            }


RISK = RiskConfig()

#############################################
# MACRO SCORING WEIGHTS (Section 4.6)
#############################################

MACRO_WEIGHTS = {
    'spy_vs_200dma': 0.30,      # 30% - Most important
    'fed_policy': 0.15,          # 15%
    'yield_curve': 0.15,         # 15%
    'breadth_50dma': 0.10,       # 10%
    'ad_line_trend': 0.10,       # 10%
    'economic_data': 0.10,       # 10%
    'vix_sentiment': 0.05,       # 5%
    'event_calendar': 0.05,      # 5%
}

# Deployment levels based on macro score
MACRO_DEPLOYMENT = {
    (8.0, 10.0): 1.00,   # 100% deployment
    (6.5, 8.0): 0.80,    # 80% deployment
    (5.0, 6.5): 0.60,    # 60% deployment
    (3.5, 5.0): 0.40,    # 40% deployment
    (0.0, 3.5): 0.20,    # 20% deployment
}

#############################################
# BLUEPRINT SCORING WEIGHTS (Section 10.8)
#############################################

BLUEPRINT_SCORE_WEIGHTS = {
    'macro_alignment': 0.15,
    'sector_strength': 0.15,
    'industry_strength': 0.10,
    'trend_structure': 0.15,
    'relative_strength': 0.15,
    'setup_quality': 0.15,
    'risk_reward': 0.10,
    'volume_pattern': 0.05,
}

# Score interpretation thresholds
SCORE_THRESHOLDS = {
    'strong_buy': 8.5,
    'buy': 7.5,
    'buy_reduced': 6.5,
    'speculative': 5.5,
    'avoid': 0.0,
}

#############################################
# SECTOR CONFIGURATION (Section 3 & 10.1)
#############################################

# 11 GICS Sectors with ETFs
SECTORS = {
    'XLK': {'name': 'Technology', 'cycle': 'mid'},
    'XLC': {'name': 'Communication Services', 'cycle': 'mid'},
    'XLY': {'name': 'Consumer Discretionary', 'cycle': 'early'},
    'XLF': {'name': 'Financials', 'cycle': 'early'},
    'XLI': {'name': 'Industrials', 'cycle': 'early-mid'},
    'XLB': {'name': 'Materials', 'cycle': 'early'},
    'XLE': {'name': 'Energy', 'cycle': 'late'},
    'XLRE': {'name': 'Real Estate', 'cycle': 'late'},
    'XLP': {'name': 'Consumer Staples', 'cycle': 'defensive'},
    'XLV': {'name': 'Healthcare', 'cycle': 'defensive'},
    'XLU': {'name': 'Utilities', 'cycle': 'defensive'},
}

SECTOR_ETFS = list(SECTORS.keys())

# Sector exposure limits
SECTOR_MAX_ALLOCATION = {
    'Technology': 0.30,
    'Healthcare': 0.20,
    'Financials': 0.20,
    'Consumer Discretionary': 0.15,
    'Industrials': 0.15,
    'Energy': 0.15,
    'default': 0.15,
}

#############################################
# UNIVERSE FILTERS (Section 3)
#############################################

UNIVERSE_FILTERS = {
    'tier1_core': {
        'min_volume': 500_000,
        'min_price': 10.0,
        'min_market_cap': 2_000_000_000,
    },
    'tier2_swing': {
        'min_atr': 1.50,
        'beta_range': (0.8, 2.0),
    },
    'tier3_buy_hold': {
        'min_market_cap': 10_000_000_000,
        'min_roe': 0.10,
        'max_debt_equity': 1.5,
    },
    'tier4_momentum': {
        'min_volume': 1_000_000,
        'min_atr_pct': 0.03,
        'price_range': (15, 150),
    },
}

#############################################
# TECHNICAL PARAMETERS
#############################################

# EMA periods used throughout blueprint
EMA_PERIODS = [12, 21, 50, 200]

# Timeframes for multi-timeframe analysis
TIMEFRAMES = ['1min', '5min', '15min', '60min', 'daily']

# Timeframe weights for alignment scoring
TIMEFRAME_WEIGHTS = {
    'daily': 3.0,
    '60min': 2.0,
    '15min': 1.5,
    '5min': 1.0,
    '1min': 0.5,
}

# RSI thresholds
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_NEUTRAL_LOW = 40
RSI_NEUTRAL_HIGH = 60

# ADX thresholds
ADX_TRENDING = 25
ADX_STRONG_TREND = 30

# VIX thresholds
VIX_LOW = 15
VIX_NORMAL = 25
VIX_HIGH = 30
VIX_EXTREME = 40

#############################################
# TREND STRUCTURE DEFINITIONS (Section 6)
#############################################

TREND_STATES = {
    'STRONG_UPTREND': 10,    # Price > 50 > 200, all rising
    'UPTREND': 8,            # Price > 50 > 200
    'WEAKENING': 6,          # Price > 200, 50 rolling over
    'NEUTRAL': 5,            # At 200 DMA
    'DOWNTREND': 2,          # Price < 50 < 200
    'DOUBLE_DEATH': 0,       # Price < 200 AND 50 < 200 (death cross)
}

#############################################
# SETUP TYPES (Section 5 & 7)
#############################################

SWING_SETUPS = [
    'trend_continuation_pullback',
    'breakout',
    'mean_reversion',
]

MOMENTUM_SETUPS = [
    'gap_and_go',
    'relative_strength_breakout',
    'oversold_bounce',
    'gap_fill_reversal',
]

SPECULATIVE_SETUPS = [
    'earnings_explosion',
    'short_squeeze',
    'parabolic_breakout',
    'news_catalyst',
    'volatility_squeeze',
    'sector_rotation_surge',
    'sympathy_play',
]

#############################################
# MINIMUM R:R REQUIREMENTS
#############################################

MIN_RR_RATIOS = {
    'swing': 2.0,
    'momentum': 1.5,
    'speculative': 3.0,
    'buy_hold': None,  # Thesis-driven, not R:R
}

#############################################
# STYLE-SPECIFIC SECTOR REQUIREMENTS
#############################################

STYLE_SECTOR_RULES = {
    'momentum': {
        'max_sector_rank': 3,      # Must be top 3 sectors
        'description': 'Trade ONLY in top 3 ranked sectors',
    },
    'swing': {
        'max_sector_rank': 5,      # Must be top 5 sectors
        'avoid_bottom': 3,         # Avoid bottom 3
        'description': 'Trade in top 5 ranked sectors; avoid bottom 3',
    },
    'buy_hold': {
        'max_sector_rank': None,   # Less restrictive
        'description': 'Less restrictive; consider cycle position',
    },
    'speculative': {
        'max_sector_rank': 5,
        'description': 'Prefer top sectors but catalyst-driven',
    },
}

#############################################
# OUTPUT FORMATTING
#############################################

REPORT_TEMPLATES = {
    'quick_scan': 'quick_scan.txt',
    'standard_analysis': 'standard_analysis.txt',
    'watchlist_review': 'watchlist_review.txt',
}

# Colors for terminal output (ANSI codes)
COLORS = {
    'green': '\033[92m',
    'red': '\033[91m',
    'yellow': '\033[93m',
    'blue': '\033[94m',
    'cyan': '\033[96m',
    'white': '\033[97m',
    'bold': '\033[1m',
    'reset': '\033[0m',
}

#############################################
# API KEYS
#############################################

# FRED API key for economic data (Treasury yields, VIX, etc.)
# Get your free key at: https://fred.stlouisfed.org/docs/api/api_key.html
FRED_API_KEY = "5cf066909e3fa22065663b8fbcb3e533"
