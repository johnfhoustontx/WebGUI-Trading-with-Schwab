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

from math import floor, isfinite

import config_paper

#############################################
# CONSTANTS
#############################################

MULTIPLIER = 100

#############################################
# SIZING
#############################################


def _strike(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if isfinite(v) else None


def risk_width(sig):
    """The width a position's max loss is measured across (PURE).

    A vertical: its own ``width``. An iron condor: the WIDER of its two wings,
    read off the four strikes themselves. Only one side of a condor can finish
    in the money, so its max loss is ``wider wing - credit`` - and the scanner's
    IC row carries ``width`` = the PUT wing (``build_iron_condors``), so a
    condor with a wider call wing was sized and reserved off the narrow one:
    put 5 wide, call 10 wide, 2.20 credit booked $560 against a true $1,560
    (audit AC-04). The stored ``width`` is left alone - Rescue's roll builders
    read it as the put wing.

    A condor missing a strike falls back to the stored ``width``.
    """
    stored = sig.get("width")
    if str(sig.get("strategy") or "").strip().upper() != "IC":
        return stored
    ps, pl = _strike(sig.get("short_strike")), _strike(sig.get("long_strike"))
    cs, cl = _strike(sig.get("call_short")), _strike(sig.get("call_long"))
    if None in (ps, pl, cs, cl):
        return stored
    return max(abs(ps - pl), abs(cl - cs))


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
