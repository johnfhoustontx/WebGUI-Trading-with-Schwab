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


def _num(value):
    """A finite number, or None. Rejects bools (``True`` is an int in Python)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _project(rows):
    """``[(ts, spot, cvol, pvol, cprem, pprem), …]`` → ``[[ts, call, put], …]``.

    Rows carrying NO premium on either side are skipped (premium is forward-only
    — legacy rows predate the columns), so a series simply starts where premium
    began collecting. One missing side counts as 0.0. Junk rows are skipped, not
    raised on."""
    out = []
    for row in rows or []:
        try:
            ts, call, put = _num(row[0]), _num(row[4]), _num(row[5])
        except (TypeError, IndexError):
            continue
        if ts is None or (call is None and put is None):
            continue
        out.append([ts, call or 0.0, put or 0.0])
    out.sort(key=lambda r: r[0])
    return out


def build_series(flow_by_symbol) -> dict:
    """``{symbol: [flow rows]}`` → ``{symbol: [[ts, call_prem, put_prem], …]}``.

    Symbols with no usable rows are OMITTED (the page names them as
    "no data yet" rather than drawing an empty line). Baskets are summed from
    their members aligned on ``ts``; a member missing a snapshot contributes
    nothing at that timestamp rather than dropping the column. Dollar sum is the
    only sensible aggregation for money, so a basket's skew is
    ``Σnet ÷ Σ(call+put)`` — the same dollar-weighted convention
    ``market_svc.symbol_premium_skew`` uses."""
    flow_by_symbol = flow_by_symbol or {}
    out = {}
    for sym, rows in flow_by_symbol.items():
        projected = _project(rows)
        if projected:
            out[sym] = projected

    for basket, members in BASKETS.items():
        totals: dict = {}
        for member in members:
            for ts, call, put in out.get(member, ()):
                acc = totals.setdefault(ts, [0.0, 0.0])
                acc[0] += call
                acc[1] += put
        if totals:
            out[basket] = [[ts, c, p] for ts, (c, p) in sorted(totals.items())]
    return out
