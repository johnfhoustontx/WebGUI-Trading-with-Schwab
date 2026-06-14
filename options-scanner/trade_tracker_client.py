"""Best-effort client that registers/unregisters paper trades with the
SchwabProxy stream tracker. Every call is fire-and-forget: failures are logged
and swallowed so the paper-trade path never blocks on streaming. The proxy's
30s reconcile loop heals anything dropped here.
"""
import logging
import sys
from pathlib import Path

import requests

import signal_recommender

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import PROXY_URL

log = logging.getLogger("trade_tracker_client")

PROXY_BASE = PROXY_URL
TIMEOUT = 1.5  # seconds; short — never hold up a trade


def _payload(trade):
    th = signal_recommender.track_thresholds(trade["entry_credit"])
    return {
        "trade_id": trade["trade_id"],
        "symbol": trade["symbol"],
        "strategy": trade["strategy"],
        "expiration": trade["expiration"],
        "quantity": trade.get("quantity", 1),
        "entry_credit": trade["entry_credit"],
        "short_strike": trade["short_strike"],
        "long_strike": trade["long_strike"],
        "call_short": trade.get("call_short"),
        "call_long": trade.get("call_long"),
        "target_mid": th["target_mid"],
        "stop_mid": th["stop_mid"],
    }


def track(trade):
    try:
        r = requests.post(f"{PROXY_BASE}/track", json=_payload(trade), timeout=TIMEOUT)
        if r.status_code == 200:
            return True
        log.warning("track %s -> %s", trade["trade_id"], r.status_code)
    except requests.exceptions.RequestException as e:
        log.warning("track %s failed (proxy down?): %s", trade.get("trade_id"), e)
    return False


def untrack(trade_id):
    try:
        r = requests.post(f"{PROXY_BASE}/untrack", json={"trade_id": trade_id}, timeout=TIMEOUT)
        if r.status_code == 200:
            return True
        log.warning("untrack %s -> %s", trade_id, r.status_code)
    except requests.exceptions.RequestException as e:
        log.warning("untrack %s failed (proxy down?): %s", trade_id, e)
    return False
