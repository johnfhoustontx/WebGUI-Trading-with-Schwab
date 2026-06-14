"""
config.py - Central configuration for Claude_is_the_Driver
Version: 1.9.1
Last Updated: 2026-05-25

Version 1.9.1 Changes:
- Added PAPER_TRADE flag (True = simulate orders, no real Schwab submission)

Version 1.9.0 Changes:
- Added MARKET_HOLIDAYS calendar (2026-2027)
  Morning agent will skip on market holidays — no wasted API calls or approval prompts

Version 1.8.0 Changes:
- Added NT_FEEDS_ML_SERVERS flag: True = NinjaTrader is running and feeding
  ML servers with real futures bars. Skip /warmup, use 3-day bar lookback.
  Set False only if NT is not running when morning agent fires.

Version 1.7.0 Changes:
- Reverted OPTIONS_ANALYTICS_URL back to 8200 (OptionsAnalytics is a SEPARATE
  service from the Schwab proxy — schwab_proxy.py has no GEX endpoints)

Version 1.6.0 Changes:
- Fixed OPTIONS_ANALYTICS_URL: was 8200, corrected to 8100 (now reverted in 1.7.0)

Version 1.5.0 Changes:
- Restored ML_SERVERS with updated ports (MES:8000, MNQ:8001, ES:8004, NQ:8005)
- Added ML_FALLBACK_SIGNALS

Version 1.4.0 Changes:
- Removed FUTURES_INSTRUMENTS / FUTURES_SIGNAL_THRESHOLDS

Version 1.0.0 Changes:
- Initial implementation
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import ML_SERVER_URLS, ANALYTICS_URL, PROXY_URL, APPROVAL_PORT as _APPROVAL_PORT

#############################################
# CAPITAL & RISK PARAMETERS
#############################################

STARTING_CAPITAL = 10_000.00

RISK_LIMITS = {
    "daily_max_loss":   250.00,
    "weekly_max_loss":  600.00,
    "monthly_max_loss": 1500.00,
}

BUCKET_CONFIG = {
    "A": {
        "label":       "SPX 0-DTE Options",
        "allocation":  4000.00,
        "max_risk":    300.00,
        "max_trades":  1,
    },
    "B": {
        "label":       "Equity Day Trades",
        "allocation":  3500.00,
        "max_risk":    150.00,
        "max_trades":  3,
    },
    "C": {
        "label":       "Micro Futures (MES)",
        "allocation":  1500.00,
        "max_risk":    75.00,
        "max_trades":  1,
    },
}

#############################################
# SERVICE ENDPOINTS
#############################################

ML_SERVERS = ML_SERVER_URLS

ML_FALLBACK_SIGNALS = {
    "MES": "SPX",
    "MNQ": "QQQ",
    "ES":  "SPX",
    "NQ":  "QQQ",
}

SCHWAB_SIGNAL_INSTRUMENTS = ["SPY", "QQQ", "SPX"]

SCHWAB_SIGNAL_THRESHOLDS = {
    "strong_bullish_pct":  0.40,
    "bullish_pct":         0.15,
    "bearish_pct":        -0.15,
    "strong_bearish_pct": -0.40,
    "volume_surge_ratio":  1.30,
    "min_confidence":      60.0,
}

OPTIONS_ANALYTICS_URL      = ANALYTICS_URL
OPTIONS_ANALYTICS_SNAPSHOT = "/options/snapshot"

# When True: morning agent skips /warmup (NT already filled CNN buffers with real futures data)
#            and uses only 3 days of Schwab bars for current feature computation.
# When False: full warmup cycle using SPY/QQQ proxy bars.
NT_FEEDS_ML_SERVERS = True

# Paper trading — set True to log orders without submitting to Schwab.
# Set False when ready for live execution with real capital.
PAPER_TRADE = True

SCHWAB_PROXY_URL = PROXY_URL

#############################################
# MARKET HOLIDAY CALENDAR
#############################################

# US equity market full-close holidays (NYSE schedule).
# Agent exits immediately on these dates — no API calls, no approval prompts.
# Update annually. Dates as "YYYY-MM-DD" strings.
MARKET_HOLIDAYS = {
    # 2026
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed — Jul 4 is Saturday)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
    # 2027
    "2027-01-01",  # New Year's Day
    "2027-01-18",  # Martin Luther King Jr. Day
    "2027-02-15",  # Presidents' Day
    "2027-03-26",  # Good Friday
    "2027-05-31",  # Memorial Day
    "2027-06-18",  # Juneteenth (observed — Jun 19 is Saturday)
    "2027-07-05",  # Independence Day (observed — Jul 4 is Sunday)
    "2027-09-06",  # Labor Day
    "2027-11-25",  # Thanksgiving
    "2027-12-24",  # Christmas (observed — Dec 25 is Saturday)
}

#############################################
# TRADE SELECTION THRESHOLDS
#############################################

VIX_MAX_TRADE     = 25.0
VIX_MAX_A         = 20.0
ML_MIN_CONFIDENCE = 65.0

TARGET_DELTA_SHORT = 0.11
TARGET_DELTA_LONG  = 0.05

PROFIT_TARGET_PCT  = 0.50
MONITOR_INTERVAL_S = 300

#############################################
# APPROVAL SERVER
#############################################

APPROVAL_HOST        = "127.0.0.1"
APPROVAL_PORT        = _APPROVAL_PORT
APPROVAL_TIMEOUT_MIN = 15

#############################################
# SCHEDULER
#############################################

MARKET_OPEN_HOUR  = 9
MARKET_OPEN_MIN   = 30
AGENT_RUN_HOUR    = 9
AGENT_RUN_MIN     = 28

MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN  = 0

#############################################
# PATHS
#############################################

import os

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
TRADE_LOG     = os.path.join(BASE_DIR, "data", "trade_log.json")
PENDING_TRADE = os.path.join(BASE_DIR, "data", "pending_trade.json")
LOG_DIR       = os.path.join(BASE_DIR, "logs")
