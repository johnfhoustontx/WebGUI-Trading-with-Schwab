"""
config_paper.py - Paper Trading Configuration
Version: 1.0.0
Last Updated: 2026-06-03

Single source of truth for the PAPER_MODE master gate and every tunable used
by the Portfolio paper-trading system. Nothing in the order path submits unless
PAPER_MODE is True.

Version 1.0.0 Changes:
- Initial implementation
"""

#############################################
# MASTER GATE
#############################################

PAPER_MODE = True   # gates ALL order submission; flip False to disarm entirely

#############################################
# ACCOUNT / RISK
#############################################

STARTING_BALANCE     = 25_000.0
MAX_RISK_PER_TRADE   = 250.0
MAX_SESSION_DRAWDOWN = 2_500.0

#############################################
# ENTRY QUALITY BAR
#############################################

MIN_ENTRY_SCORE = 60   # entry_score must be >= this AND rec must not be CUT
# NOTE: lowered 70 -> 60 on 2026-06-03 after live data showed the scanner's
# open-signal scores cluster 50-63; a 70 bar essentially never fired.

#############################################
# FILL SIMULATION
#############################################

SLIPPAGE_TICKS = 1      # ticks of slippage against the trader (1-2)
OPTION_TICK    = 0.05   # net-spread tick size

# Opening-auction protection: quotes are unreliable in the first minutes after
# the 08:30 CT open, producing garbage (near-zero / negative) credit fills.
OPEN_BUFFER_MIN = 5     # no NEW entries until this many minutes after the open
MIN_FILL_CREDIT = 0.10  # reject a fill whose net credit is below this (bad quote)

#############################################
# ENGINE CADENCE (minutes; RTH only)
#############################################

ENTRY_CYCLE_MIN  = 2    # entry-scan cadence (2-5)
MANAGE_CYCLE_MIN = 15   # re-price / exit cadence (matches auto-remark loop)
