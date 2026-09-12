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
# CONCENTRATION (per name / per expiry)
#############################################
# The rung that was missing between MAX_RISK_PER_TRADE (one trade) and
# MAX_SESSION_DRAWDOWN (the whole account). Without it a book could be entirely
# one name and still clear both ends: on 2026-09-08 all fourteen open positions
# were ORCL spreads expiring 2026-09-11 -- $2,829, 11.6% of a $24,490 account,
# one name, one direction, one expiry, over a report scheduled for 09-10.
#
# Enforced by paper_concentration.concentration_reject at the entry path. A
# breach SKIPS the signal for this cycle rather than recording a rejected order:
# the condition is transient, and an order row would blacklist the signal for
# good (see that module's header).
MAX_POSITIONS_PER_SYMBOL = 3       # open positions in one underlying
MAX_RISK_PER_SYMBOL      = 750.0   # summed max loss in one underlying (~3% of account)
MAX_POSITIONS_PER_EXPIRY = 5       # open positions sharing one expiration, book-wide

# --- SECTOR (gap assessment B4) ---
# The rung between the per-symbol caps and the book-wide deployment cap: four
# DIFFERENT semiconductors at the full symbol cap breach nothing above, and that
# is the correlated book the playbook warns about. Enforced by
# paper_concentration.concentration_reject, grouped by config/sectors.toml.
#
# Measured on the real books before choosing these (not hypothetical):
#   * manual  - worst simultaneous Information Technology exposure $3,569 across
#     19 positions; Industrials $20,312 across 107 (SPCX stacking, which
#     MAX_POSITIONS_PER_SYMBOL now stops on its own);
#   * driver  - worst IT exposure $21,531 across 15 positions, 86% of a $25,000
#     account in ONE sector, and $15,018 across 9 INDEX positions;
#   * a $1,500 cap would have bound on 20 of 46 trading days in the manual book
#     and 35 of 40 in the driver's.
#
# And the GROUPING's premise was measured too, because two of this audit's
# rationales have already failed that way: across six months of daily returns on
# the tradeable watchlist, mean pairwise correlation WITHIN a sector is 0.250
# against 0.017 ACROSS sectors, and 14 of the 15 most-correlated pairs in the
# universe share one. Sector is a real proxy for correlation in this universe.
#
# $1,500 is two symbols at the full MAX_RISK_PER_SYMBOL and ~31% of the $4,837
# deployment ceiling on the live book, so filling the book needs at least four
# sectors. 5 positions matches MAX_POSITIONS_PER_EXPIRY for the reason that
# number was chosen: five positions on one thing is a bet on that thing.
MAX_POSITIONS_PER_SECTOR = 5        # open positions in one sector, book-wide
MAX_RISK_PER_SECTOR      = 1_500.0  # summed max loss in one sector (~6% of account)

# Book-wide DEPLOYMENT cap: total open max loss as a fraction of session-start
# equity (gap assessment B3). The three caps above are per symbol and per expiry;
# nothing capped the book as a whole, so three symbols at the $750 symbol cap is
# $2,250 and clears every one of them.
#
# 0.20 is the TIGHT end of the published range on purpose - theoptionpremium caps
# open risk at 20-25%, Option Alpha keeps 40-50% in cash (i.e. 50-60% deployed).
# Measured on the live book 2026-09-11: $1,933 committed against $24,184 equity,
# 8.0%, so 20% is a real ceiling with ~11 more $250 spreads of headroom rather
# than something that bites on day one. Raise it here to loosen.
MAX_DEPLOYED_RISK_PCT = 0.20

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
