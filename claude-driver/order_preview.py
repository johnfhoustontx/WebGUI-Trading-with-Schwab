"""order_preview.py — single source of truth for order sizing / stop / target.

Pure functions: the caller passes in the live price. Both the pre-approval
preview (trade_selector) and order_executor use these so the numbers shown to
the user match what is sent (modulo a fresh re-price at fill).
"""


def preview_bucket_b(trade: dict, price: float) -> dict:
    """Equity day trade. shares=max(1,int(max_risk/(price*0.02)));
    stop/target are +/-2% / +/-4% depending on side."""
    side = trade.get("side", "BUY")
    max_risk = float(trade.get("max_risk", 150.0))
    if price and price > 0:
        shares = max(1, int(max_risk / (price * 0.02)))
        stop = round(price * (0.98 if side == "BUY" else 1.02), 2)
        target = round(price * (1.04 if side == "BUY" else 0.96), 2)
    else:
        shares = stop = target = None
    return {
        "side": side,
        "quantity": shares,
        "quantity_unit": "shares",
        "est_entry": round(price, 2) if price else None,
        "limit": None,
        "stop": stop,
        "target": target,
        "max_risk": max_risk,
    }


def preview_bucket_c(trade: dict, spx_price: float) -> dict:
    """MES micro futures bracket, stop/target in points off SPX spot."""
    side = trade.get("side", "BUY")
    contracts = trade.get("contracts", 2)
    stop_ticks = trade.get("stop_ticks", 10)
    target_ticks = trade.get("target_ticks", 30)
    if spx_price and spx_price > 0:
        stop = round(spx_price - stop_ticks * 0.25 if side == "BUY"
                     else spx_price + stop_ticks * 0.25, 2)
        target = round(spx_price + target_ticks * 0.25 if side == "BUY"
                       else spx_price - target_ticks * 0.25, 2)
    else:
        stop = target = None
    return {
        "side": side,
        "quantity": contracts,
        "quantity_unit": "contracts",
        "est_entry": round(spx_price, 2) if spx_price else None,
        "limit": None,
        "stop": stop,
        "target": target,
        "max_risk": float(trade.get("max_risk", 0.0)),
    }


def preview_bucket_a(trade: dict) -> dict:
    """Options spread. Entry is a NET_CREDIT market order, so no single entry
    price; report contracts, max risk, and the dollar profit target."""
    contracts = trade.get("contracts", 1)
    max_risk = float(trade.get("max_risk", 0.0))
    pt_frac = float(trade.get("profit_target", 0.0) or 0.0)
    return {
        "side": "SELL_TO_OPEN",
        "structure": trade.get("structure", ""),
        "strikes": trade.get("strikes", {}),
        "quantity": contracts,
        "quantity_unit": "contracts",
        "est_entry": None,
        "limit": None,
        "stop": None,
        "target": None,
        "max_risk": max_risk,
        "profit_target_dollars": round(max_risk * pt_frac, 2),
    }
