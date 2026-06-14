"""
fill_model.py - Shared spread fill pricing (natural + realistic limit)
Version: 1.1.0
Last Updated: 2026-06-10

Single source of truth for how a spread fills against a LIVE quote, used by the
Portfolio broker (paper_broker), the Captured Signals re-pricer
(signal_repricer), and scanner candidate pricing (scanner_engine) so they can
never diverge.

Two models:

  Natural (worst case, vertical_fill): SELL legs at the bid, BUY legs at the
  ask — embeds the full bid/ask cost on every transaction.

    SELL_TO_OPEN credit = short_bid - long_ask
    BUY_TO_CLOSE debit  = short_ask - long_bid

  Realistic (default for simulated fills, realistic_vertical_fill): a limit
  order worked FILL_FRAC into the net spread market from the natural side —
  how the trader actually executes (matches the scanner's per-leg
  suggested_limit convention of bid + 40% of the spread).

Iron condors sum two verticals (the caller does the summing and final rounding).

Version 1.1.0 Changes:
- Add realistic_vertical_fill (limit worked 40% into the net market) and
  is_tradeable gate. See docs/plans/2026-06-10-realistic-fill-model-design.md.

Version 1.0.0 Changes:
- Initial implementation
"""

#############################################
# NATURAL VERTICAL FILL
#############################################

def vertical_fill(short_bid, short_ask, long_bid, long_ask, side):
    """Return the natural net price for ONE vertical (unrounded; caller rounds).

    side == "SELL_TO_OPEN": net credit  = short_bid - long_ask
    side == "BUY_TO_CLOSE": net debit    = short_ask - long_bid
    """
    if side == "SELL_TO_OPEN":
        return short_bid - long_ask
    return short_ask - long_bid


#############################################
# REALISTIC LIMIT FILL
#############################################

# Fraction of the net spread market a worked limit concedes from the natural
# side. 0.40 matches the scanner's long-standing per-leg suggested_limit
# convention (bid + 40% of the bid/ask spread): each side fills 10% of the
# market width worse than mid. Single calibration knob — tune against actual
# paper/live fills, never inline the number at call sites.
FILL_FRAC = 0.40


def realistic_vertical_fill(short_bid, short_ask, long_bid, long_ask, side,
                            frac=FILL_FRAC):
    """Net price for ONE vertical assuming a limit worked `frac` into the net
    spread market from the natural side (unrounded; caller rounds).

    side == "SELL_TO_OPEN": credit = net_bid + frac * (net_ask - net_bid)
    side == "BUY_TO_CLOSE": debit  = net_ask - frac * (net_ask - net_bid)
    """
    net_bid = short_bid - long_ask
    net_ask = short_ask - long_bid
    if side == "SELL_TO_OPEN":
        return net_bid + frac * (net_ask - net_bid)
    return net_ask - frac * (net_ask - net_bid)


#############################################
# TRADEABILITY GATE
#############################################

# A net spread market wider than this fraction of the spread width is not a
# real trading opportunity — the modeled fill price would be fiction.
MAX_MARKET_WIDTH_FRAC = 0.30


def is_tradeable(short_bid, short_ask, long_bid, long_ask, width,
                 max_market_frac=MAX_MARKET_WIDTH_FRAC):
    """A spread is tradeable when you could be filled for a positive credit at
    the natural price AND the net market is no wider than max_market_frac of
    the spread width."""
    net_bid = short_bid - long_ask
    net_ask = short_ask - long_bid
    return net_bid > 0 and (net_ask - net_bid) <= max_market_frac * width
