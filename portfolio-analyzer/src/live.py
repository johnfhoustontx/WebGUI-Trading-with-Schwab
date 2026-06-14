"""Pure live-update logic for streamed quotes.

This module is deliberately free of any network or GUI imports so it can be
unit-tested in isolation. The SSE consumer thread lives in ``src.data`` and the
Tk refresh wiring lives in ``portfolio_analyzer.py``; both call into the pure
functions here.

A "tick" is the normalized quote dict the proxy emits over
``GET /stream/quotes`` — ``{"symbol", "last", "net_change"}`` — where ``last``
and/or ``net_change`` may be ``None`` when a field was absent on that update.
"""
from __future__ import annotations

import json

from src.sectors import sector_weights


def MULTIPLIER(asset_type) -> int:
    """Contract multiplier for a holding's market value / P&L math.

    Options are quoted per-share but trade in 100-share contracts, so their
    notional is ``quantity * price * 100``. Everything else (equities) is 1.

    # TODO: futures point-value multiplier — futures use 1 for now (their real
    # point value is instrument-specific and not yet wired in).
    """
    return 100 if asset_type == "OPTION" else 1


def apply_tick(model: dict, tick: dict) -> dict:
    """Apply one streamed quote ``tick`` to ``model``, returning a new model.

    Operates on a shallow copy of ``model`` and copies of each affected holding
    row, so the caller's holding dicts are never mutated. For every holding
    whose ``symbol`` matches ``tick["symbol"]``:

    - if ``tick["last"]`` is not None: set ``last``, recompute ``market_value``
      (= ``quantity * last * mult``) and ``total_pl`` (= ``market_value -
      quantity * avg_price * mult``).
    - if ``tick["net_change"]`` is not None: set ``day_pl`` (= ``quantity *
      net_change * mult``).
    - ``vs_sector_rs`` and ``since_purchase_excess`` are left untouched — they
      are historical-window values that only refresh on a full rebuild.

    Holdings not matching the tick symbol pass through unchanged. After updating
    holdings, sector weights are recomputed from the (updated) ``market_value``s
    via :func:`src.sectors.sector_weights`. ``benchmark_delta`` and ``tailwind``
    on each sector row are left untouched (they refresh on a full rebuild); a
    sector with no weight after the regroup defaults to ``0.0``.

    Returns a new ``{"holdings": [...], "sectors": [...]}`` dict.
    """
    symbol = tick.get("symbol")
    last = tick.get("last")
    net_change = tick.get("net_change")

    new_holdings = []
    for holding in model.get("holdings") or []:
        if holding.get("symbol") != symbol:
            new_holdings.append(holding)  # unchanged: keep reference
            continue

        row = dict(holding)  # copy before mutating — never touch the caller's dict
        mult = MULTIPLIER(row.get("asset_type"))
        quantity = row.get("quantity") or 0
        avg_price = row.get("avg_price") or 0

        if last is not None:
            row["last"] = last
            row["market_value"] = quantity * last * mult
            row["total_pl"] = row["market_value"] - quantity * avg_price * mult

        if net_change is not None:
            row["day_pl"] = quantity * net_change * mult

        new_holdings.append(row)

    # Recompute sector weights from the updated holdings. sector_weights wants
    # rows carrying ``sector`` + ``market_value`` (holdings already have both).
    weights = sector_weights(new_holdings)
    new_sectors = []
    for sector_row in model.get("sectors") or []:
        new_row = dict(sector_row)
        new_row["weight"] = weights.get(new_row.get("sector"), 0.0)
        new_sectors.append(new_row)

    return {"holdings": new_holdings, "sectors": new_sectors}


def stream_symbols(model: dict, extra=("SPY",)) -> list[str]:
    """Symbols to subscribe to for a portfolio ``model``.

    Collects, in order, each holding's ``symbol`` and its ``sector_etf`` (when
    truthy), then appends the ``extra`` symbols (default ``("SPY",)``). The
    result is order-preserving deduplicated with all falsy/None entries dropped,
    so a symbol/ETF/extra that repeats appears once and futures (whose
    ``sector_etf`` is ``None``) contribute only their own symbol.

    Pure: no network or GUI — the GUI calls this to build its subscription list.
    """
    symbols: list[str] = []
    for holding in model.get("holdings") or []:
        symbols.append(holding.get("symbol"))
        symbols.append(holding.get("sector_etf"))
    symbols.extend(extra)

    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def parse_sse_line(line: str):
    """Parse one SSE line into a tick dict, or ``None`` if it carries no data.

    Returns the decoded JSON object for a ``data: {...}`` line; returns ``None``
    for blank lines, comment lines (``: ...``), non-``data:`` fields, or a
    ``data:`` payload that is not valid JSON.
    """
    if line is None:
        return None
    line = line.strip()
    if not line or line.startswith(":"):
        return None
    if not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    try:
        return json.loads(payload)
    except (ValueError, TypeError):
        return None
