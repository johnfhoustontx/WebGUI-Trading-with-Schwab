"""
paper_broker.py - Paper order gateway + fill simulation
Version: 1.0.0
Last Updated: 2026-06-03

Intercepts orders while PAPER_MODE is on: simulates a fill against a FRESH live
quote and returns a Schwab-order-shaped response. NEVER calls the Schwab trader
endpoint. The [PAPER] log prefix keeps paper and live activity distinct.

Version 1.0.0 Changes:
- Initial implementation
"""
import itertools
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import config_paper
import fill_model
import signal_repricer

log = logging.getLogger("paper_engine")   # shared logger -> logs/paper_engine.log
PREFIX = "[PAPER]"
TZ = ZoneInfo("America/Chicago")

# Unique Schwab-shaped orderId per FILLED response (distinct from the DB row id
# the engine assigns later). REJECTED responses keep orderId=0.
_order_seq = itertools.count(100001)


class FillError(Exception):
    """Raised when a leg is unquoted / one-sided and no fill is possible."""


#############################################
# FILL PRICE (pure)
#############################################

def _vertical_credit(leg_map, short_k, long_k):
    """Return (short_bid, short_ask, long_bid, long_ask). Raise FillError if any
    leg is unquoted / one-sided."""
    sb, sa, _ = signal_repricer._leg_bid_ask(leg_map, short_k)
    lb, la, _ = signal_repricer._leg_bid_ask(leg_map, long_k)
    if None in (sb, sa, lb, la):
        raise FillError(f"unquoted legs {short_k}/{long_k}")
    return sb, sa, lb, la


def simulate_fill_price(chain, side, strategy, short_strike, long_strike,
                        call_short=None, call_long=None):
    """Realistic limit fill against the LIVE quote (mimics how the trader
    actually executes): a limit worked fill_model.FILL_FRAC into the net
    spread market from the natural side. Raises FillError if any leg is
    unquoted. IC sums put + call verticals."""
    pm = chain.get("putExpDateMap", {})
    cm = chain.get("callExpDateMap", {})

    def leg_price(leg_map, sk, lk):
        sb, sa, lb, la = _vertical_credit(leg_map, sk, lk)
        return fill_model.realistic_vertical_fill(sb, sa, lb, la, side)

    if strategy == "PCS":
        raw = leg_price(pm, short_strike, long_strike)
    elif strategy == "CCS":
        raw = leg_price(cm, short_strike, long_strike)
    elif strategy == "IC":
        raw = leg_price(pm, short_strike, long_strike) + \
              leg_price(cm, call_short, call_long)
    else:
        raise FillError(f"unknown strategy {strategy}")

    return round(raw, 2)


COMPLEX = {"IC": "IRON_CONDOR"}


#############################################
# ORDER RESPONSE (Schwab-shaped)
#############################################

def build_order_response(order_id, status, side, strategy, quantity, price,
                         legs, entered_time, reason):
    """Mirror the Schwab order-response schema so calling code is identical for
    paper vs (future) live."""
    return {
        "orderId": order_id,
        "status": status,
        "orderType": "NET_CREDIT" if side == "SELL_TO_OPEN" else "NET_DEBIT",
        "quantity": quantity,
        "filledQuantity": quantity if status == "FILLED" else 0,
        "price": price,
        "enteredTime": entered_time,
        "closeTime": entered_time if status == "FILLED" else None,
        "orderStrategyType": "SINGLE",
        "complexOrderStrategyType": COMPLEX.get(strategy, "VERTICAL"),
        "orderLegCollection": legs,
        "statusDescription": reason,
    }


#############################################
# GATEWAY (intercepts before any trader endpoint)
#############################################

def submit_order(order, client):
    """PAPER_MODE gateway. Returns a Schwab-shaped response. NEVER routes to the
    Schwab trader endpoint — only reads a fresh live quote to simulate the fill."""
    now_iso = datetime.now(TZ).isoformat()
    side = order["side"]
    strategy = order["strategy"]
    qty = order.get("quantity", 0)

    if not config_paper.PAPER_MODE:
        log.warning("%s submit blocked: PAPER_MODE off", PREFIX)
        return build_order_response(0, "REJECTED", side, strategy, qty, None,
                                    [], now_iso, "PAPER_MODE_OFF")
    try:
        # The engine clears signal_repricer's per-(symbol,expiration) chain cache at
        # cycle start (clear_chain_cache()) so fills use fresh quotes. If submit_order
        # is ever called outside a cycle, the chain may be a stale snapshot.
        chain = signal_repricer._fetch_chain(client, order["symbol"], order["expiration"])
        if chain is None:
            raise FillError("chain unavailable")
        if not isinstance(chain, dict):
            raise FillError("bad chain")
        price = simulate_fill_price(
            chain, side=side, strategy=strategy,
            short_strike=order["short_strike"], long_strike=order["long_strike"],
            call_short=order.get("call_short"), call_long=order.get("call_long"))
    except FillError as e:
        log.warning("%s REJECTED %s %s x%s: %s", PREFIX, side, order.get("symbol"), qty, e)
        return build_order_response(0, "REJECTED", side, strategy, qty, None,
                                    [], now_iso, "UNQUOTED_LEGS")
    except Exception as e:
        log.exception("%s order error %s x%s", PREFIX, order.get("symbol"), qty)
        return build_order_response(0, "REJECTED", side, strategy, qty, None,
                                    [], now_iso, "FILL_ERROR")

    legs = order.get("legs", [])
    resp = build_order_response(next(_order_seq), "FILLED", side, strategy, qty,
                                price, legs, now_iso, None)
    log.info("%s FILLED %s %s x%s @ %.2f (%s)", PREFIX, side, order.get("symbol"),
             qty, price, resp["orderType"])
    return resp
