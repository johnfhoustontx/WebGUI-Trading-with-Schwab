"""One-time bootstrap import of historical trades from a broker CSV export.

This is a *pure-logic* module (no network). Its output rows match the SAME
contract as the proxy ``/transactions`` endpoint so CSV-imported trades and
proxy-synced trades merge uniformly. Each row has exactly these 8 keys::

    {"trade_id", "symbol", "asset_type", "underlying", "quantity", "price",
     "instruction", "trade_date"}

- ``instruction`` is ``"BUY"`` or ``"SELL"``
- ``quantity`` is a positive float, ``price`` is a float
- ``trade_date`` is ISO ``YYYY-MM-DD``
- ``trade_id`` is deterministic for CSV rows so re-importing dedupes:
  ``f"csv:{trade_date}:{symbol}:{idx}"`` (idx = source row index).

The real broker CSV header names are NOT yet known, so headers are resolved
through a flexible, case-insensitive ``HEADER_ALIASES`` mapping that is easy to
extend once a real export is in hand.
"""
from __future__ import annotations

import csv
from datetime import datetime

# Internal field -> accepted header names (matched case-insensitively, stripped).
# Extend these sets when the real broker CSV format is known.
HEADER_ALIASES: dict[str, set[str]] = {
    "date": {"Date", "Trade Date", "Run Date", "Date Acquired", "Settlement Date"},
    "action": {"Action", "Side", "Transaction", "Buy/Sell", "Instruction", "Type"},
    "symbol": {"Symbol", "Ticker"},
    "quantity": {"Quantity", "Qty", "Shares", "Amount"},
    "price": {"Price", "Trade Price", "Price (USD)", "Purchase Price"},
}

# Accepted date input formats; output is always ISO YYYY-MM-DD.
_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d")

# The 8-key contract, in canonical order.
_CONTRACT_KEYS = (
    "trade_id", "symbol", "asset_type", "underlying",
    "quantity", "price", "instruction", "trade_date",
)


def _build_header_lookup(fieldnames: list[str]) -> dict[str, str]:
    """Map each internal field to the actual CSV header that supplies it.

    Returns ``{internal_field: actual_header}``. Missing fields are simply
    absent from the result; callers decide whether that is fatal.
    """
    # normalized accepted-name -> internal field
    accepted: dict[str, str] = {}
    for field, names in HEADER_ALIASES.items():
        for name in names:
            accepted[name.strip().lower()] = field

    lookup: dict[str, str] = {}
    for header in fieldnames or []:
        if header is None:
            continue
        field = accepted.get(header.strip().lower())
        if field and field not in lookup:
            lookup[field] = header
    return lookup


def _parse_date(raw: str) -> str:
    """Parse a date string into ISO ``YYYY-MM-DD``; raise on unparseable input."""
    value = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {raw!r}")


# Meaningful action tokens -> instruction. Matched case-insensitively after
# normalizing (lowercase, strip, collapse internal whitespace). Bare "b"/"s"
# are intentionally NOT recognized so tokens like "Split"/"Sweep"/"Balance"
# fail loudly instead of being mis-classified.
_BUY_TOKENS = ("buy", "bought", "bto", "btc")
_SELL_TOKENS = ("sell", "sold", "sto", "stc", "short")


def _parse_action(raw: str) -> str:
    """Map a broker action string to ``"BUY"`` or ``"SELL"``; raise if unclear."""
    value = " ".join((raw or "").strip().lower().split())
    # Leading token (e.g. "buy" in "buy to open", "stc" in "stc 100").
    head = value.split(" ", 1)[0]
    if head in _BUY_TOKENS:
        return "BUY"
    if head in _SELL_TOKENS:
        return "SELL"
    raise ValueError(f"Unrecognized action: {raw!r}")


def parse_trades_csv(path: str) -> list[dict]:
    """Parse a broker trades CSV into normalized trade rows (8-key contract).

    Rows whose required cells are missing/unparseable raise ``ValueError`` so
    bootstrap problems surface loudly rather than silently corrupting history.
    """
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        lookup = _build_header_lookup(reader.fieldnames or [])

        missing = [k for k in ("date", "action", "symbol", "quantity", "price")
                   if k not in lookup]
        if missing:
            raise ValueError(
                f"CSV is missing required column(s) {missing}; "
                f"headers were {reader.fieldnames}. "
                f"Add the real header name(s) to HEADER_ALIASES."
            )

        for idx, record in enumerate(reader):
            symbol = (record.get(lookup["symbol"]) or "").strip().upper()
            if not symbol:
                # Skip blank trailing lines / empty symbol rows.
                continue
            trade_date = _parse_date(record.get(lookup["date"]))
            instruction = _parse_action(record.get(lookup["action"]))
            try:
                quantity = abs(float((record.get(lookup["quantity"]) or "").strip()))
            except ValueError:
                raise ValueError(
                    f"Invalid quantity {record.get(lookup['quantity'])!r} "
                    f"for {symbol} at row {idx}"
                )
            try:
                price = float((record.get(lookup["price"]) or "").strip())
            except ValueError:
                raise ValueError(
                    f"Invalid price {record.get(lookup['price'])!r} "
                    f"for {symbol} at row {idx}"
                )

            # TODO: refine option/futures detection once the real CSV format is
            # known. For now every imported row is treated as an equity; the
            # underlying defaults to the symbol itself.
            asset_type = "EQUITY"
            underlying = symbol

            rows.append({
                "trade_id": f"csv:{trade_date}:{symbol}:{idx}",
                "symbol": symbol,
                "asset_type": asset_type,
                "underlying": underlying,
                "quantity": quantity,
                "price": price,
                "instruction": instruction,
                "trade_date": trade_date,
            })
    return rows
