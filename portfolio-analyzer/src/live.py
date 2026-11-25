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

    A tick whose ``last`` is 0 or below is no reading and changes nothing.
    Holdings not matching the tick symbol pass through unchanged. After updating
    holdings, sector weights are recomputed from the (updated) ``market_value``s
    via :func:`src.sectors.sector_weights`. ``benchmark_delta``
    on each sector row is left untouched (they refresh on a full rebuild); a
    sector with no weight after the regroup defaults to ``0.0``.

    Returns a new ``{"holdings": [...], "sectors": [...]}`` dict, plus
    ``accounts`` (each account's ``positions`` ticked the same way) when the
    model carries them.
    """
    symbol = tick.get("symbol")
    last = tick.get("last")
    net_change = tick.get("net_change")

    # ⚠ A price of 0 or below is NO reading, and neither is the net change that
    # comes with it. Schwab's equity stream answers a symbol it cannot quote
    # with zeros, and applying them wrote every such row a market value of 0
    # and a P/L of minus its whole cost (seen on prod 2026-11-24 on option
    # legs: Mark 0.00, Market Value $0.00, Total P/L -$1,560.00 on a 7.80 x 2
    # spread). The model is returned unchanged.
    if last is not None and not last > 0:
        return model

    new_holdings = _tick_rows(model.get("holdings") or [], symbol, last, net_change)

    # Recompute sector weights from the updated holdings. sector_weights wants
    # rows carrying ``sector`` + ``market_value`` (holdings already have both).
    weights = sector_weights(new_holdings)
    new_sectors = []
    for sector_row in model.get("sectors") or []:
        new_row = dict(sector_row)
        new_row["weight"] = weights.get(new_row.get("sector"), 0.0)
        new_sectors.append(new_row)

    out = {"holdings": new_holdings, "sectors": new_sectors}
    # The per-account positions (the Holdings tab, by account) take the same
    # tick by the same rule, so an account's rows and the whole-book rows
    # cannot disagree between rebuilds. Carried only when the model has them.
    if "accounts" in model:
        out["accounts"] = [
            {**acct, "positions": _tick_rows(acct.get("positions") or [],
                                             symbol, last, net_change)}
            for acct in model.get("accounts") or []]
    return out


def _tick_rows(rows, symbol, last, net_change) -> list:
    """``rows`` with the tick applied to each row of ``symbol`` (copies only;
    a row of another symbol is passed through as the same object)."""
    out = []
    for holding in rows:
        if holding.get("symbol") != symbol:
            out.append(holding)  # unchanged: keep reference
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

        out.append(row)
    return out


def stream_symbols(model: dict, extra=("SPY",)) -> list[str]:
    """Symbols to subscribe to for a portfolio ``model``.

    Collects, in order, each holding's ``symbol`` (never an option's: the
    equity stream cannot quote one) and its ``sector_etf`` (when truthy), then
    appends the ``extra`` symbols (default ``("SPY",)``). The result is order-preserving deduplicated with all falsy/None entries dropped,
    so a symbol/ETF/extra that repeats appears once and futures (whose
    ``sector_etf`` is ``None``) contribute only their own symbol.

    Pure: no network or GUI — the GUI calls this to build its subscription list.
    """
    symbols: list[str] = []
    for holding in model.get("holdings") or []:
        # The stream is Schwab's EQUITY stream (LEVELONE_EQUITIES): it cannot
        # quote an option contract and answers one with zeros. An option leg
        # keeps the value Schwab's positions gave it until the next rebuild;
        # its underlying's sector ETF is still streamed.
        if holding.get("asset_type") != "OPTION":
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
