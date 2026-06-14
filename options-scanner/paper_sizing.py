"""
paper_sizing.py - Risk-based contract sizing
Version: 1.0.0
Last Updated: 2026-06-03

Pure helper: number of spread contracts that keeps max loss within the
per-trade risk budget. qty == 0 means the spread is too rich for even one
contract (caller treats as RISK_TOO_HIGH).

Version 1.0.0 Changes:
- Initial implementation
"""

from math import floor

import config_paper

#############################################
# CONSTANTS
#############################################

MULTIPLIER = 100

#############################################
# SIZING
#############################################


def size_contracts(credit, width, max_risk=None):
    """Return (qty, max_loss_per_contract).

    max_loss_per_contract = (width - credit) * 100.
    qty = floor(max_risk / max_loss_per_contract), or 0 if one contract already
    exceeds max_risk or the spread is degenerate (credit >= width).
    """
    if max_risk is None:
        max_risk = config_paper.MAX_RISK_PER_TRADE
    max_loss_per = round((width - credit) * MULTIPLIER, 2)
    if max_loss_per <= 0:
        return 0, max_loss_per
    return floor(max_risk / max_loss_per), max_loss_per
