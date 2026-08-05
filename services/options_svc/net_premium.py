"""Group model + intraday net-premium series for the Dealer Positioning
"Net Prem" view.

PURE — stdlib only, no I/O, no engine imports. ``compute.build_net_premium``
does the DB reading and hands the raw rows here.

Net premium = cumulative call premium ($) − cumulative put premium ($) for a
symbol, per intraday snapshot. Because Schwab serves no time-&-sales tape the
premium is UNSIGNED cumulative traded dollars, so this is a money-weighted
put/call read, NOT net buying. The UI must say so.
"""
from __future__ import annotations

# The three selectable groups, in display order. This is the single source of
# truth for membership + ordering — the page builds its tabs from it.
GROUPS = (
    {"key": "indices", "label": "Indices & Broad",
     "symbols": ("$SPX", "$NDX", "BIG10", "SPY", "QQQ", "IWM", "DIA")},
    {"key": "sectors", "label": "SPDR Sectors",
     "symbols": ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                 "XLP", "XLRE", "XLU", "XLV", "XLY")},
    {"key": "megacaps", "label": "Mega-caps",
     "symbols": ("NVDA", "AVGO", "AAPL", "META", "MSFT",
                 "TSLA", "PLTR", "AMZN", "GOOGL", "AMD")},
)

# Composite pseudo-symbols: summed server-side from real tickers. BIG10 holds
# the same set as the Mega-caps group above, in a different order (that one is
# display order; this one mirrors market_svc/symbols.py's BIG10 basket, whose
# membership this must match by design). Tests pin both relationships.
BASKETS = {
    "BIG10": ("NVDA", "MSFT", "GOOGL", "AMZN", "META",
              "AAPL", "TSLA", "AVGO", "PLTR", "AMD"),
}


def display_symbols() -> list:
    """Every symbol the view can plot, in group order (includes baskets)."""
    out: list = []
    for group in GROUPS:
        for sym in group["symbols"]:
            if sym not in out:
                out.append(sym)
    return out


def source_symbols() -> list:
    """The REAL tickers to read from gex_history: display symbols minus the
    baskets, plus every basket's members (deduped, order-preserving)."""
    out: list = []
    for sym in display_symbols():
        members = BASKETS.get(sym)
        for real in (members if members else (sym,)):
            if real not in out:
                out.append(real)
    return out
