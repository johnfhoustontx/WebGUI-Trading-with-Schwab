"""Single source of truth for the small CANONICAL index-symbol sets used across
apps (the parts that were drifting). Deliberately TIERED: this holds only the
benchmark-index sets + index-root classification. The LIVE scan universe stays
file-sourced (options-scanner/data/Top 20.xlsx via watchlist.py), and the large
backtest / sector universes stay in their own modules — they are different
concerns, not duplication.
"""
from __future__ import annotations

# Benchmark index tickers (Schwab uses the leading '$' for cash indices).
SPX = "$SPX"
VIX = "$VIX"
SPY = "SPY"
QQQ = "QQQ"

# Scan base — the three benchmark indices the scanner always covers (no $VIX,
# which is a context quote, not a scan target). Order-preserving tuple.
SCAN_BASE: tuple[str, ...] = (SPX, SPY, QQQ)

# GEX collection base — the scan base plus $VIX (listed 2nd so it's always present
# even when absent from the watchlist). Order matters for collection dedup.
COLLECTION_BASE: tuple[str, ...] = (SPX, VIX, SPY, QQQ)

# Underlyings treated as "index" for the directional-signal dedup gate.
INDEX_SYMBOLS: frozenset[str] = frozenset({SPX, SPY, QQQ})

# Roots that carry the index OPTION commission rate (+ exchange passthrough).
INDEX_ROOTS: frozenset[str] = frozenset({"SPX", "VIX", "OEX", "NDX", "RUT", "XSP", "DJX"})


def is_index_symbol(symbol: str | None) -> bool:
    """True if ``symbol`` (with or without a leading '$', any case) is an index
    root — used for commission routing."""
    if not symbol:
        return False
    return str(symbol).lstrip("$").upper() in INDEX_ROOTS
