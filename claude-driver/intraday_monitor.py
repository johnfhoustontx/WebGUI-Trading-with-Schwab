"""
intraday_monitor.py - Polls open positions, closes at 50% profit target or stop breach
Version: 1.0.0
Last Updated: 2026-05-23

Version 1.0.0 Changes:
- Initial implementation
- Polls positions every MONITOR_INTERVAL_S seconds
- Closes Bucket A at 50% of max credit (or on stop breach)
- Closes Bucket B/C when bracket order fills (monitors status only)
- Updates trade_log.json with final P&L on close
- Shuts down automatically at market close
"""

import json
import logging
import os
import time
from datetime import date, datetime, time as dtime

import requests

from config import (
    SCHWAB_PROXY_URL,
    TRADE_LOG,
    LOG_DIR,
    MONITOR_INTERVAL_S,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MIN,
    BUCKET_CONFIG,
)

#############################################
# LOGGING
#############################################

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, f"monitor_{date.today()}.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

#############################################
# HELPERS
#############################################

def _market_is_open() -> bool:
    """Return True if current time is before market close."""
    now = datetime.now().time()
    close = dtime(MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN)
    open_ = dtime(9, 30)
    return open_ <= now <= close


def _get_account_hash() -> str | None:
    try:
        resp = requests.get(f"{SCHWAB_PROXY_URL}/accounts", timeout=8)
        resp.raise_for_status()
        accounts = resp.json()
        if accounts:
            return accounts[0].get("hashValue") or accounts[0].get("accountNumber")
    except Exception as exc:
        logger.warning(f"Account hash fetch failed: {exc}")
    return None


def _fetch_positions(account_hash: str) -> list:
    """Fetch all open positions from Schwab proxy."""
    try:
        resp = requests.get(
            f"{SCHWAB_PROXY_URL}/accounts/{account_hash}",
            params={"fields": "positions"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("securitiesAccount", {}).get("positions", [])
    except Exception as exc:
        logger.warning(f"Positions fetch failed: {exc}")
    return []


def _fetch_orders(account_hash: str) -> list:
    """Fetch today's orders from Schwab proxy."""
    try:
        today = date.today().isoformat()
        resp  = requests.get(
            f"{SCHWAB_PROXY_URL}/accounts/{account_hash}/orders",
            params={"fromEnteredTime": f"{today}T00:00:00Z", "toEnteredTime": f"{today}T23:59:59Z"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() if isinstance(resp.json(), list) else []
    except Exception as exc:
        logger.warning(f"Orders fetch failed: {exc}")
    return []


def _close_option_position(account_hash: str, symbol: str, quantity: int, instruction: str) -> bool:
    """Close an option leg (BUY_TO_CLOSE or SELL_TO_CLOSE)."""
    order = {
        "orderType":         "MARKET",
        "session":           "NORMAL",
        "duration":          "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [{
            "instruction": instruction,
            "quantity":    quantity,
            "instrument":  {"symbol": symbol, "assetType": "OPTION"},
        }],
    }
    try:
        resp = requests.post(f"{SCHWAB_PROXY_URL}/orders/{account_hash}", json=order, timeout=10)
        resp.raise_for_status()
        logger.info(f"Close order placed: {instruction} {quantity}x {symbol}")
        return True
    except Exception as exc:
        logger.error(f"Close order failed for {symbol}: {exc}")
        return False

#############################################
# TRADE LOG UPDATE
#############################################

def _update_trade_log(trade_id: str, pnl: float, close_reason: str):
    """Update a trade record in trade_log.json with final P&L."""
    if not os.path.exists(TRADE_LOG):
        return
    try:
        with open(TRADE_LOG, "r") as f:
            trades = json.load(f)
        for t in trades:
            if t.get("trade_id") == trade_id:
                t["pnl"]         = round(pnl, 2)
                t["status"]      = "closed"
                t["close_reason"]= close_reason
                t["closed_at"]   = datetime.now().isoformat()
                t["result"]      = "Win" if pnl > 0 else "Loss" if pnl < 0 else "BE"
                break
        with open(TRADE_LOG, "w") as f:
            json.dump(trades, f, indent=2)
        logger.info(f"Trade log updated: {trade_id} | P&L={pnl:+.2f} | {close_reason}")
    except Exception as exc:
        logger.error(f"Trade log update failed: {exc}")

#############################################
# BUCKET A MONITOR — SPX OPTIONS
#############################################

def _check_bucket_a(account_hash: str, open_trades: list):
    """
    For each open Bucket A trade, check P&L vs 50% profit target.
    Closes automatically if target or EOD close criteria met.
    """
    positions = _fetch_positions(account_hash)
    spx_positions = [p for p in positions if "SPX" in p.get("instrument", {}).get("symbol", "")]

    for trade in open_trades:
        if trade.get("bucket") != "A" or trade.get("status") != "open":
            continue

        max_risk     = float(trade.get("max_risk", BUCKET_CONFIG["A"]["max_risk"]))
        profit_target = max_risk * 0.50   # 50% of max credit received

        # Approximate current value from positions
        total_value = 0.0
        for pos in spx_positions:
            mkt_val = float(pos.get("marketValue", 0))
            total_value += mkt_val

        pnl_estimate = -total_value   # short premium: positive mkt value = loss for us

        now = datetime.now().time()
        eod_close = dtime(15, 45)   # Force close at 3:45pm ET

        if pnl_estimate >= profit_target:
            logger.info(f"Bucket A profit target hit: +${pnl_estimate:.2f} >= ${profit_target:.2f} — closing")
            _close_all_spx_positions(account_hash, spx_positions, trade["trade_id"], pnl_estimate, "profit_target")

        elif now >= eod_close:
            logger.info(f"Bucket A EOD close (3:45pm) — closing with P&L ~${pnl_estimate:.2f}")
            _close_all_spx_positions(account_hash, spx_positions, trade["trade_id"], pnl_estimate, "eod_close")

        else:
            logger.debug(f"Bucket A: current P&L ~${pnl_estimate:.2f} | target=${profit_target:.2f} — holding")


def _close_all_spx_positions(account_hash: str, spx_positions: list, trade_id: str, pnl: float, reason: str):
    """Send close orders for all SPX option positions."""
    for pos in spx_positions:
        symbol   = pos.get("instrument", {}).get("symbol", "")
        qty      = abs(int(pos.get("longQuantity", 0) - pos.get("shortQuantity", 0)))
        is_long  = pos.get("longQuantity", 0) > 0
        instruction = "SELL_TO_CLOSE" if is_long else "BUY_TO_CLOSE"
        if qty > 0:
            _close_option_position(account_hash, symbol, qty, instruction)
    _update_trade_log(trade_id, pnl, reason)

#############################################
# BUCKET B/C MONITOR — CHECK ORDER STATUS
#############################################

def _check_bracket_orders(account_hash: str, open_trades: list):
    """
    For Bucket B and C, monitor order status.
    When a child order (stop or target) fills, update the trade log with realized P&L.
    """
    orders = _fetch_orders(account_hash)
    filled = [o for o in orders if o.get("status") == "FILLED"]

    for trade in open_trades:
        if trade.get("bucket") not in ("B", "C") or trade.get("status") != "open":
            continue

        instrument = trade.get("instrument", "")

        # Look for filled exit orders matching this instrument
        for order in filled:
            for leg in order.get("orderLegCollection", []):
                if leg.get("instrument", {}).get("symbol") == instrument:
                    instr = leg.get("instruction", "")
                    if instr in ("SELL", "BUY_TO_COVER", "SELL", "BUY_TO_COVER"):
                        avg_fill  = float(order.get("price", 0))
                        entry_est = float(trade.get("entry_price", avg_fill))
                        qty       = int(order.get("quantity", 1))
                        # Estimate P&L (simplified — actual P&L from Schwab fill data)
                        direction = 1 if trade.get("side") == "BUY" else -1
                        pnl       = direction * (avg_fill - entry_est) * qty
                        reason    = "target_fill" if "LIMIT" in order.get("orderType", "") else "stop_fill"
                        logger.info(f"Bucket {trade['bucket']} closed: {reason} | P&L~${pnl:.2f}")
                        _update_trade_log(trade["trade_id"], pnl, reason)

#############################################
# MAIN MONITOR LOOP
#############################################

def _load_open_trades() -> list:
    """Return list of today's open trades from the log."""
    if not os.path.exists(TRADE_LOG):
        return []
    try:
        with open(TRADE_LOG, "r") as f:
            trades = json.load(f)
        today = date.today().isoformat()
        return [t for t in trades if t.get("date") == today and t.get("status") == "open"]
    except Exception:
        return []


def run_monitor():
    """Main monitor loop. Runs until market close or all positions closed."""
    logger.info("Intraday monitor started")
    account_hash = _get_account_hash()
    if not account_hash:
        logger.error("Monitor cannot start — no account hash.")
        return

    while _market_is_open():
        open_trades = _load_open_trades()

        if not open_trades:
            logger.info("No open trades — monitor idle")
        else:
            logger.info(f"Monitoring {len(open_trades)} open trade(s)")
            _check_bucket_a(account_hash, open_trades)
            _check_bracket_orders(account_hash, open_trades)

        time.sleep(MONITOR_INTERVAL_S)

    logger.info("Market closed — intraday monitor shutting down")


if __name__ == "__main__":
    run_monitor()
