"""
order_executor.py - Places bracket orders via Schwab API for all three buckets
Version: 1.0.1
Last Updated: 2026-05-23

Version 1.0.1 Changes:
- Added PAPER_TRADE mode (config.PAPER_TRADE=True)
  When True: logs all orders without submitting to Schwab
  Set False when ready for live execution

Version 1.0.0 Changes:
- Initial implementation
- Handles Bucket A: SPX 0-DTE options (IC, PCS, CCS)
- Handles Bucket B: Equity market orders with stop + target
- Handles Bucket C: MES micro futures bracket
- Writes execution results to trade_log.json
- Fires intraday_monitor after successful fills
"""

import json
import logging
import os
import threading
from datetime import date, datetime
from typing import Optional

import requests

from config import (
    SCHWAB_PROXY_URL,
    TRADE_LOG,
    LOG_DIR,
    PAPER_TRADE,
)

#############################################
# LOGGING
#############################################

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, f"executor_{date.today()}.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

#############################################
# SCHWAB ORDER HELPERS
#############################################

def _post_order(account_hash: str, order_body: dict) -> dict:
    """POST an order to the Schwab proxy (or simulate if PAPER_TRADE=True)."""
    if PAPER_TRADE:
        logger.info(f"  [PAPER TRADE] Simulated order — no real order sent")
        logger.info(f"  [PAPER TRADE] Order body: {json.dumps(order_body, indent=2)}")
        return {"success": True, "paper": True, "order_id": f"PAPER-{datetime.now().strftime('%H%M%S')}",
                "message": "Paper trade — order logged, not submitted"}
    try:
        resp = requests.post(
            f"{SCHWAB_PROXY_URL}/orders/{account_hash}",
            json=order_body,
            timeout=15,
        )
        resp.raise_for_status()
        return {"success": True, "status_code": resp.status_code,
                "order_id": resp.headers.get("Location", "").split("/")[-1]}
    except Exception as exc:
        logger.error(f"Order POST failed: {exc}")
        return {"success": False, "error": str(exc)}


def _get_account_hash() -> Optional[str]:
    """Fetch the first linked Schwab account hash from the proxy."""
    try:
        resp = requests.get(f"{SCHWAB_PROXY_URL}/accounts", timeout=10)
        resp.raise_for_status()
        accounts = resp.json()
        if accounts:
            return accounts[0].get("hashValue") or accounts[0].get("accountNumber")
        logger.error("No accounts returned from proxy")
        return None
    except Exception as exc:
        logger.error(f"Failed to fetch account hash: {exc}")
        return None


#############################################
# BUCKET A — SPX 0-DTE OPTIONS
#############################################

def _execute_iron_condor(account_hash: str, trade: dict) -> dict:
    strikes = trade.get("strikes", {})
    contracts = trade.get("contracts", 1)

    legs = [
        {"instruction": "SELL_TO_OPEN", "quantity": contracts,
         "instrument": {"symbol": f"SPX_{strikes.get('put_short')}_P", "assetType": "OPTION"}},
        {"instruction": "BUY_TO_OPEN",  "quantity": contracts,
         "instrument": {"symbol": f"SPX_{strikes.get('put_long')}_P",  "assetType": "OPTION"}},
        {"instruction": "SELL_TO_OPEN", "quantity": contracts,
         "instrument": {"symbol": f"SPX_{strikes.get('call_short')}_C", "assetType": "OPTION"}},
        {"instruction": "BUY_TO_OPEN",  "quantity": contracts,
         "instrument": {"symbol": f"SPX_{strikes.get('call_long')}_C",  "assetType": "OPTION"}},
    ]

    order = {
        "orderType":         "NET_CREDIT",
        "session":           "NORMAL",
        "duration":          "DAY",
        "orderStrategyType": "SINGLE",
        "complexOrderStrategyType": "IRON_CONDOR",
        "orderLegCollection": legs,
    }
    return _post_order(account_hash, order)


def _execute_vertical_spread(account_hash: str, trade: dict) -> dict:
    strikes  = trade.get("strikes", {})
    struct   = trade.get("structure", "")
    contracts = trade.get("contracts", 1)
    is_put   = "put" in struct.lower()

    if is_put:
        legs = [
            {"instruction": "SELL_TO_OPEN", "quantity": contracts,
             "instrument": {"symbol": f"SPX_{strikes.get('short')}_P", "assetType": "OPTION"}},
            {"instruction": "BUY_TO_OPEN",  "quantity": contracts,
             "instrument": {"symbol": f"SPX_{strikes.get('long')}_P",  "assetType": "OPTION"}},
        ]
        strategy = "VERTICAL"
    else:
        legs = [
            {"instruction": "SELL_TO_OPEN", "quantity": contracts,
             "instrument": {"symbol": f"SPX_{strikes.get('short')}_C", "assetType": "OPTION"}},
            {"instruction": "BUY_TO_OPEN",  "quantity": contracts,
             "instrument": {"symbol": f"SPX_{strikes.get('long')}_C",  "assetType": "OPTION"}},
        ]
        strategy = "VERTICAL"

    order = {
        "orderType":         "NET_CREDIT",
        "session":           "NORMAL",
        "duration":          "DAY",
        "orderStrategyType": "SINGLE",
        "complexOrderStrategyType": strategy,
        "orderLegCollection": legs,
    }
    return _post_order(account_hash, order)


def execute_bucket_a(account_hash: str, trade: dict) -> dict:
    structure = trade.get("structure", "")
    if structure == "iron_condor":
        return _execute_iron_condor(account_hash, trade)
    else:
        return _execute_vertical_spread(account_hash, trade)


#############################################
# BUCKET B — EQUITY DAY TRADES
#############################################

def execute_bucket_b(account_hash: str, trade: dict) -> dict:
    instrument = trade.get("instrument", "QQQ")
    side       = trade.get("side", "BUY")
    max_risk   = trade.get("max_risk", 150.0)

    # Fetch current price to size the order
    try:
        resp = requests.get(f"{SCHWAB_PROXY_URL}/quotes",
                            params={"symbols": instrument}, timeout=5)
        resp.raise_for_status()
        q = resp.json().get(instrument, {})
        quote = q.get("quote", {})
        price = float(quote.get("lastPrice") or quote.get("mark")
                      or q.get("lastPrice") or q.get("mark") or 0)
    except Exception:
        price = 0

    if price <= 0:
        return {"success": False, "error": f"Cannot get price for {instrument}"}

    from order_preview import preview_bucket_b
    pv = preview_bucket_b(trade, price)
    if pv["quantity"] is None:
        return {"success": False, "error": f"Cannot size {instrument} at price {price}"}
    shares, stop_price, target_price = pv["quantity"], pv["stop"], pv["target"]

    logger.info(f"  Bucket B: {instrument} {side} {shares} shares @ ~${price:.2f} "
                f"stop=${stop_price} target=${target_price}")

    entry = {
        "orderType":         "MARKET",
        "session":           "NORMAL",
        "duration":          "DAY",
        "orderStrategyType": "TRIGGER",
        "orderLegCollection": [
            {"instruction": side, "quantity": shares,
             "instrument": {"symbol": instrument, "assetType": "EQUITY"}}
        ],
        "childOrderStrategies": [
            {
                "orderStrategyType": "OCO",
                "childOrderStrategies": [
                    {
                        "orderType": "LIMIT", "session": "NORMAL", "duration": "DAY",
                        "price": target_price,
                        "orderLegCollection": [
                            {"instruction": "SELL" if side == "BUY" else "BUY_TO_COVER",
                             "quantity": shares,
                             "instrument": {"symbol": instrument, "assetType": "EQUITY"}}
                        ]
                    },
                    {
                        "orderType": "STOP", "session": "NORMAL", "duration": "DAY",
                        "stopPrice": stop_price,
                        "orderLegCollection": [
                            {"instruction": "SELL" if side == "BUY" else "BUY_TO_COVER",
                             "quantity": shares,
                             "instrument": {"symbol": instrument, "assetType": "EQUITY"}}
                        ]
                    }
                ]
            }
        ]
    }
    return _post_order(account_hash, entry)


#############################################
# BUCKET C — MES MICRO FUTURES
#############################################

def execute_bucket_c(account_hash: str, trade: dict) -> dict:
    side        = trade.get("side", "BUY")
    contracts   = trade.get("contracts", 2)
    instrument  = trade.get("instrument", "/MESO")

    # Fetch current MES price via SPX proxy
    try:
        resp = requests.get(f"{SCHWAB_PROXY_URL}/quotes",
                            params={"symbols": "$SPX"}, timeout=5)
        resp.raise_for_status()
        q = resp.json().get("$SPX", {})
        spx_price = float(q.get("quote", {}).get("lastPrice") or q.get("lastPrice") or 0)
    except Exception:
        spx_price = 0

    tick_value = 12.50  # MES 1 tick = $12.50 (0.25 point × $5/point)
    from order_preview import preview_bucket_c
    pv = preview_bucket_c(trade, spx_price)
    stop_price, target_price = pv["stop"], pv["target"]
    if stop_price is None:
        stop_price = target_price = 0

    logger.info(f"  Bucket C: {instrument} {side} {contracts} contracts "
                f"stop={stop_price} target={target_price}")

    order = {
        "orderType":         "MARKET",
        "session":           "NORMAL",
        "duration":          "DAY",
        "orderStrategyType": "TRIGGER",
        "orderLegCollection": [
            {"instruction": side, "quantity": contracts,
             "instrument": {"symbol": instrument, "assetType": "FUTURE"}}
        ],
        "childOrderStrategies": [
            {
                "orderStrategyType": "OCO",
                "childOrderStrategies": [
                    {
                        "orderType": "LIMIT", "session": "NORMAL", "duration": "DAY",
                        "price": target_price,
                        "orderLegCollection": [
                            {"instruction": "SELL" if side == "BUY" else "BUY",
                             "quantity": contracts,
                             "instrument": {"symbol": instrument, "assetType": "FUTURE"}}
                        ]
                    },
                    {
                        "orderType": "STOP", "session": "NORMAL", "duration": "DAY",
                        "stopPrice": stop_price,
                        "orderLegCollection": [
                            {"instruction": "SELL" if side == "BUY" else "BUY",
                             "quantity": contracts,
                             "instrument": {"symbol": instrument, "assetType": "FUTURE"}}
                        ]
                    }
                ]
            }
        ]
    }
    return _post_order(account_hash, order)


#############################################
# TRADE LOGGING
#############################################

def _log_trade(trade: dict, result: dict) -> str:
    trade_id = f"{date.today().isoformat()}-{trade.get('bucket','X')}-{datetime.now().strftime('%H%M%S')}"
    entry = {
        "trade_id":  trade_id,
        "date":      date.today().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "bucket":    trade.get("bucket"),
        "instrument": trade.get("instrument"),
        "side":      trade.get("side"),
        "status":    "open",
        "notes":     trade.get("notes", ""),
        "paper":     result.get("paper", False),
        "success":   result.get("success", False),
        "order_id":  result.get("order_id"),
        "error":     result.get("error"),
        "pnl":       0.0,
    }
    os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
    trades = []
    if os.path.exists(TRADE_LOG):
        try:
            with open(TRADE_LOG) as f:
                trades = json.load(f)
        except Exception:
            trades = []
    trades.append(entry)
    with open(TRADE_LOG, "w") as f:
        json.dump(trades, f, indent=2)
    return trade_id


# Map claude-driver Bucket A `structure` names to the proxy's resolve_legs
# strategy codes (trade_registry.resolve_legs only accepts PCS/CCS/IC).
_STRUCTURE_TO_STRATEGY = {
    "iron_condor":        "IC",
    "put_credit_spread":  "PCS",
    "call_credit_spread": "CCS",
}


def _track_bucket_a(trade: dict, trade_id: str) -> None:
    """Register a Bucket A options trade with the proxy stream tracker so it
    records live events/P&L to trade_performance.db. Best-effort; never raises.

    The proxy's /track endpoint (schwab_proxy._track) requires: trade_id,
    symbol, strategy, expiration, quantity, entry_credit, short_strike,
    long_strike (+ call_short/call_long for ICs). We adapt the claude-driver
    trade dict to that shape:
      - structure -> strategy code (iron_condor->IC, put/call_credit_spread->PCS/CCS),
        since resolve_legs raises KeyError on any other strategy name.
      - IC strikes nest as put_short/put_long/call_short/call_long; verticals
        nest as short/long. Map both to short_strike/long_strike.
      - SPX Bucket A is 0-DTE, so expiration defaults to today.
      - entry_credit defaults to 0.0 when not supplied (the proxy reads
        body["entry_credit"] unconditionally and would KeyError otherwise).
    """
    strikes = trade.get("strikes", {})
    structure = trade.get("structure", "")
    if structure not in _STRUCTURE_TO_STRATEGY:
        # The proxy's resolve_legs only knows IC/PCS/CCS; anything else would
        # be rejected server-side with an HTTP 200 {"status":"error"} body,
        # which our raise_for_status would NOT catch. Skip rather than mislog.
        logger.warning(f"  Not tracking {trade_id}: unmapped structure '{structure}'")
        return
    body = {
        "trade_id":     trade_id,
        "symbol":       trade.get("instrument", "SPX"),
        "strategy":     _STRUCTURE_TO_STRATEGY.get(structure, structure),
        "expiration":   trade.get("expiration") or date.today().isoformat(),
        "quantity":     trade.get("contracts", 1),
        "entry_credit": trade.get("entry_credit", trade.get("credit", 0.0)),
        "short_strike": strikes.get("short") or strikes.get("put_short"),
        "long_strike":  strikes.get("long") or strikes.get("put_long"),
        "call_short":   strikes.get("call_short"),
        "call_long":    strikes.get("call_long"),
    }
    try:
        resp = requests.post(f"{SCHWAB_PROXY_URL}/track", json=body, timeout=5)
        resp.raise_for_status()
        # The proxy returns HTTP 200 even on internal resolution failure, with a
        # {"status": "error"|"skipped", ...} body — inspect it so the log is truthful.
        status = (resp.json() or {}).get("status")
        if status == "ok":
            logger.info(f"  Tracking {trade_id} via proxy stream")
        else:
            logger.warning(f"  /track did not register {trade_id}: {resp.json()}")
    except Exception as exc:
        logger.warning(f"  Could not register {trade_id} with /track: {exc}")


#############################################
# MAIN ENTRY POINT
#############################################

def execute_trades(proposed_trades: list) -> list:
    """
    Main entry point. Execute all proposed trades and log results.

    When PAPER_TRADE=True (config): logs trades without placing real orders.
    When PAPER_TRADE=False: submits orders via Schwab Trader API.
    """
    if PAPER_TRADE:
        logger.info("PAPER_TRADE=True — simulating execution, no real orders placed")
        account_hash = "PAPER-ACCOUNT"
    else:
        account_hash = _get_account_hash()
        if not account_hash:
            logger.error("Cannot execute — no account hash available.")
            return [{"success": False, "error": "No account hash"}]

    results = []
    for trade in proposed_trades:
        bucket = trade.get("bucket")
        logger.info(f"Executing Bucket {bucket} trade: {trade.get('notes','')}")

        if bucket == "A":
            result = execute_bucket_a(account_hash, trade)
        elif bucket == "B":
            result = execute_bucket_b(account_hash, trade)
        elif bucket == "C":
            result = execute_bucket_c(account_hash, trade)
        else:
            result = {"success": False, "error": f"Unknown bucket: {bucket}"}

        trade_id = _log_trade(trade, result)
        result["trade_id"] = trade_id
        if bucket == "A" and result.get("success") and not PAPER_TRADE:
            _track_bucket_a(trade, trade_id)
        results.append(result)

        status = "OK (paper)" if result.get("paper") else ("OK" if result.get("success") else "FAILED")
        logger.info(f"Bucket {bucket} execution: {status} — trade_id={trade_id}")

    # Fire intraday monitor in background only for real trades
    if not PAPER_TRADE and any(r.get("success") for r in results):
        threading.Thread(target=_start_monitor, daemon=True).start()

    return results


def _start_monitor():
    """Start the intraday monitor after a short delay to allow fills."""
    import time
    time.sleep(10)
    try:
        import intraday_monitor
        intraday_monitor.run()
    except Exception as exc:
        logger.error(f"Intraday monitor failed to start: {exc}")
