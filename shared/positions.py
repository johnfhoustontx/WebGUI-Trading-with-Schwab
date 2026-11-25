"""Folding brokerage positions held in several accounts into one book.

Pure (no imports beyond the language), so the proxy's ``/positions`` route and
the portfolio engine fold the SAME way. It was the proxy's private
``_merge_positions`` until 2026-11-24, when the Portfolio page began fetching
each account on its own (to show the positions BY account) and still needed the
whole-book view its sector and performance tabs are built on. Two copies of
this would drift on the one rule that matters - how ``avg_price`` re-weights.
"""


def merge_positions(positions: list) -> list:
    """Fold same-``symbol`` positions (across accounts) into one holding.

    A user with multiple linked Schwab accounts can hold the same instrument in
    more than one of them. The portfolio model downstream is keyed by symbol
    (entries, baselines), so the aggregate view wants a single row per symbol:
    quantity, market value and P&L are summed, and ``avg_price`` is re-weighted
    by absolute quantity. Rows for a symbol that appears only once pass through
    untouched (as a copy); first-seen order is preserved.
    """
    groups: dict = {}
    order: list = []
    for p in positions:
        sym = p.get("symbol", "")
        if sym not in groups:
            groups[sym] = []
            order.append(sym)
        groups[sym].append(p)

    out: list = []
    for sym in order:
        rows = groups[sym]
        if len(rows) == 1:
            out.append(dict(rows[0]))
            continue
        merged = dict(rows[0])
        merged["quantity"] = sum(float(r.get("quantity", 0)) for r in rows)
        merged["market_value"] = sum(float(r.get("market_value", 0)) for r in rows)
        merged["day_pl"] = sum(float(r.get("day_pl", 0)) for r in rows)
        merged["total_pl"] = sum(float(r.get("total_pl", 0)) for r in rows)
        abs_qty = sum(abs(float(r.get("quantity", 0))) for r in rows)
        cost = sum(float(r.get("avg_price", 0)) * abs(float(r.get("quantity", 0)))
                   for r in rows)
        merged["avg_price"] = (cost / abs_qty) if abs_qty else 0.0
        out.append(merged)
    return out
