"""
Configuration - the constants the analysis_lib package still carries.

What is left of the Blueprint Trading System's config after the Tk app was
deleted (2026-08-20) and its unused constants were pruned (2026-09-19): the
timeframe weights ``technical.calculate_ema_alignment`` scores with, and the
GICS sector-ETF table.
"""

#############################################
# SECTOR CONFIGURATION
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

#############################################
# TECHNICAL PARAMETERS
#############################################

# Timeframe weights for alignment scoring
TIMEFRAME_WEIGHTS = {
    'daily': 3.0,
    '60min': 2.0,
    '15min': 1.5,
    '5min': 1.0,
    '1min': 0.5,
}
