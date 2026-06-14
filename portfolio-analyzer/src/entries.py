"""Cost-weighted entry (price + date) per symbol from BUY trades.

This is a *pure-logic* module (no network, no filesystem). It consumes the
8-key trade contract produced by the proxy ``/transactions`` endpoint and the
CSV bootstrap importer::

    {"trade_id", "symbol", "asset_type", "underlying", "quantity", "price",
     "instruction", "trade_date"}

Comparison #3 ("since-purchase vs sector") needs a single representative entry
per symbol. V1 keeps it deliberately simple:

- Only ``instruction == "BUY"`` trades contribute. SELLs are ignored entirely
  (full lot / FIFO accounting is out of scope by design — the cost-weighted
  average was the chosen approach).
- ``avg_price`` is the cost-weighted average price: ``sum(qty*price)/sum(qty)``.
- ``entry_date`` is the cost-weighted average date: each ``trade_date`` is
  converted to its proleptic-Gregorian ordinal, weighted by quantity, the
  weighted mean is rounded to the nearest day, and converted back to ISO
  ``YYYY-MM-DD``.

Symbols with no BUY trades do not appear in the result.
"""
from __future__ import annotations

from datetime import date


def weighted_avg_entry(trades: list[dict]) -> dict[str, dict]:
    """Compute a cost-weighted entry price and date per symbol from BUYs.

    Args:
        trades: 8-key contract trade rows. Only ``instruction == "BUY"`` rows
            are considered; ``quantity`` and ``price`` may be int or float.

    Returns:
        ``{symbol: {"avg_price": float, "entry_date": "YYYY-MM-DD",
        "total_quantity": float}}``. Symbols with no BUY trades are omitted;
        empty input yields an empty dict.
    """
    # symbol -> [sum(qty*price), sum(qty*ordinal), sum(qty)]
    acc: dict[str, list[float]] = {}

    for trade in trades:
        if trade.get("instruction") != "BUY":
            continue
        symbol = trade["symbol"]
        qty = float(trade["quantity"])
        price = float(trade["price"])
        ordinal = date.fromisoformat(trade["trade_date"]).toordinal()

        bucket = acc.setdefault(symbol, [0.0, 0.0, 0.0])
        bucket[0] += qty * price
        bucket[1] += qty * ordinal
        bucket[2] += qty

    result: dict[str, dict] = {}
    for symbol, (cost, date_weight, total_qty) in acc.items():
        if total_qty == 0:
            # Defensive: a symbol made entirely of zero-qty BUYs carries no
            # weight and can't form a meaningful entry.
            continue
        entry_ordinal = round(date_weight / total_qty)
        result[symbol] = {
            "avg_price": cost / total_qty,
            "entry_date": date.fromordinal(entry_ordinal).isoformat(),
            "total_quantity": total_qty,
        }
    return result
