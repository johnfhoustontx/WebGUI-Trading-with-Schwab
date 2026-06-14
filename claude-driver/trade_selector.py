"""
trade_selector.py - Decision engine: day grade -> structure -> strikes -> risk check
Version: 1.3.0
Last Updated: 2026-05-24

Version 1.3.0 Changes:
- Bucket C: prefers MES ML signal when available; falls back to SPX proxy
- Bucket A: incorporates ES signal alongside SPX for structure confidence
- Bucket B: incorporates NQ/MNQ signals alongside QQQ/SPY

Version 1.2.0 Changes:
- Bucket C used SPX as MES proxy (superseded by ML signal in 1.3.0)

Version 1.1.0 Changes:
- Replaced unicode arrow characters with ASCII (->)

Version 1.0.0 Changes:
- Initial implementation
"""

import logging
import math
from typing import Optional

import requests

from config import (
    BUCKET_CONFIG,
    ML_MIN_CONFIDENCE,
    SCHWAB_PROXY_URL,
    TARGET_DELTA_SHORT,
    TARGET_DELTA_LONG,
    VIX_MAX_A,
)

logger = logging.getLogger(__name__)

#############################################
# STRUCTURE SELECTION LOGIC
#############################################

def _select_bucket_a_structure(gex: dict, ml_signals: dict, conditions: dict) -> Optional[str]:
    """
    Choose SPX 0-DTE structure based on GEX/Charm/ML signals.

    Direction priority:
      ES ML server (port 8004) — futures-based, preferred when live
      SPX Schwab signal        — index fallback

    Returns: 'iron_condor' | 'put_credit_spread' | 'call_credit_spread' | None
    """
    net_gex       = gex.get("net_gex", 0)
    charm_bias    = gex.get("charm_bias", "neutral")
    gex_bilateral = gex.get("bilateral", False)

    spx_signal = ml_signals.get("SPX", {})
    spy_signal = ml_signals.get("SPY", {})
    es_signal  = ml_signals.get("ES",  {})

    # Prefer ES ML signal (futures-based) when live and confident
    if es_signal.get("source") == "ml_server" and es_signal.get("confidence", 0) >= ML_MIN_CONFIDENCE:
        ml_direction = es_signal.get("direction", "flat")
    else:
        ml_direction = spx_signal.get("direction", "flat")

    ml_confidence = max(
        spx_signal.get("confidence", 0),
        spy_signal.get("confidence", 0),
        es_signal.get("confidence",  0),
    )

    vix = conditions.get("vix", 20)

    logger.info(f"  Structure selection: net_gex={net_gex}, charm_bias={charm_bias}, "
                f"ml_dir={ml_direction}, ml_conf={ml_confidence:.1f}%, bilateral={gex_bilateral}")

    if gex_bilateral and net_gex > 0:
        logger.info("  -> Iron Condor (bilateral GEX, pin regime)")
        return "iron_condor"

    if vix and vix > VIX_MAX_A:
        logger.info("  -> Skipping Bucket A (VIX too high)")
        return None

    if charm_bias == "bullish" and ml_direction in ("long", "bullish") and ml_confidence >= ML_MIN_CONFIDENCE:
        logger.info("  -> Put Credit Spread (bullish Charm + ML agreement)")
        return "put_credit_spread"

    if charm_bias == "bearish" and ml_direction in ("short", "bearish") and ml_confidence >= ML_MIN_CONFIDENCE:
        logger.info("  -> Call Credit Spread (bearish Charm + ML agreement)")
        return "call_credit_spread"

    if net_gex > 0 and charm_bias == "neutral":
        logger.info("  -> Iron Condor (positive GEX, neutral Charm)")
        return "iron_condor"

    logger.info("  -> No clean Bucket A structure - skipping")
    return None


def _fetch_spx_strikes(structure: str, spx_spot: float, width: int = 25) -> Optional[dict]:
    """
    Fetch SPX 0-DTE options chain and find strikes nearest to TARGET_DELTA_SHORT.
    """
    try:
        today_str = __import__("datetime").date.today().strftime("%Y-%m-%d")
        resp = requests.get(
            f"{SCHWAB_PROXY_URL}/chains",
            params={
                "symbol":        "SPX",
                "contractType":  "ALL",
                "toDate":        today_str,
                "fromDate":      today_str,
                "strikeCount":   60,
                "includeQuotes": True,
                "optionType":    "S",
            },
            timeout=15,
        )
        resp.raise_for_status()
        chain = resp.json()
    except Exception as exc:
        logger.error(f"Options chain fetch failed: {exc}")
        return None

    puts  = chain.get("putExpDateMap",  {})
    calls = chain.get("callExpDateMap", {})

    def find_strike_by_delta(exp_map: dict, target_delta: float, above_spot: bool) -> Optional[float]:
        best_strike = None
        best_diff   = float("inf")
        for _exp, strikes in exp_map.items():
            for strike_str, contracts in strikes.items():
                strike = float(strike_str)
                if above_spot and strike <= spx_spot:
                    continue
                if not above_spot and strike >= spx_spot:
                    continue
                for contract in contracts:
                    delta = abs(float(contract.get("delta", 0)))
                    diff  = abs(delta - target_delta)
                    if diff < best_diff:
                        best_diff   = diff
                        best_strike = strike
        return best_strike

    if structure == "iron_condor":
        put_short  = find_strike_by_delta(puts,  TARGET_DELTA_SHORT, above_spot=False)
        put_long   = find_strike_by_delta(puts,  TARGET_DELTA_LONG,  above_spot=False)
        call_short = find_strike_by_delta(calls, TARGET_DELTA_SHORT, above_spot=True)
        call_long  = find_strike_by_delta(calls, TARGET_DELTA_LONG,  above_spot=True)
        if not all([put_short, put_long, call_short, call_long]):
            return None
        return {
            "structure":  "iron_condor",
            "put_short":  put_short,  "put_long":  put_long,
            "call_short": call_short, "call_long": call_long,
            "wing_width": width,
        }
    elif structure == "put_credit_spread":
        short = find_strike_by_delta(puts, TARGET_DELTA_SHORT, above_spot=False)
        return {"structure": "put_credit_spread", "short": short, "long": short - width, "wing_width": width} if short else None
    elif structure == "call_credit_spread":
        short = find_strike_by_delta(calls, TARGET_DELTA_SHORT, above_spot=True)
        return {"structure": "call_credit_spread", "short": short, "long": short + width, "wing_width": width} if short else None

    return None


def _build_bucket_b_trade(ml_signals: dict, conditions: dict) -> Optional[dict]:
    """
    Select equity day trade from Bucket B.

    Instrument priority:
      NQ/MNQ ML (ports 8005/8001) -> QQQ/SPY Schwab -> SPX fallback
    NQ and MNQ ML signals preferred when live — trained on futures data.
    """
    for inst in ["NQ", "MNQ", "QQQ", "SPY", "SPX"]:
        sig = ml_signals.get(inst, {})
        if sig.get("error") or sig.get("confidence", 0) < ML_MIN_CONFIDENCE:
            continue
        direction = sig.get("direction", "flat")
        if direction not in ("long", "bullish", "short", "bearish"):
            continue
        # Map NQ/MNQ ML signal to QQQ for execution (liquid ETF)
        exec_instrument = "QQQ" if inst in ("NQ", "MNQ") else inst
        side = "BUY" if direction in ("long", "bullish") else "SELL_SHORT"
        logger.info(f"  Bucket B: {exec_instrument} {side} via {inst} signal - conf {sig['confidence']:.1f}%")
        return {
            "bucket":        "B",
            "instrument":    exec_instrument,
            "signal_source": inst,
            "side":          side,
            "order_type":    "MARKET",
            "max_risk":      BUCKET_CONFIG["B"]["max_risk"],
            "ml_signal":     sig.get("signal"),
            "ml_confidence": sig.get("confidence"),
            "notes":         f"{exec_instrument} via {inst} signal - {direction} {sig['confidence']:.1f}%",
        }

    logger.info("  Bucket B: no qualifying signal - skipping")
    return None


def _build_bucket_c_trade(ml_signals: dict, conditions: dict) -> Optional[dict]:
    """
    Select MES futures trade from Bucket C.
    ORB bias only — requires clear directional signal.

    Signal priority:
      1. MES ML server (port 8000) — trained directly on MES, preferred
      2. ES  ML server (port 8004) — full-size same underlying
      3. SPX Schwab signal         — index proxy, always available as fallback
    """
    sig          = None
    source_label = ""
    for candidate, label in [("MES", "MES ML"), ("ES", "ES ML"), ("SPX", "SPX proxy")]:
        s = ml_signals.get(candidate, {})
        if s.get("confidence", 0) >= ML_MIN_CONFIDENCE and s.get("direction", "flat") != "flat":
            sig          = s
            source_label = label
            break

    if not sig:
        logger.info("  Bucket C: no qualifying signal for MES entry - skipping")
        return None

    direction = sig.get("direction", "flat")
    side = "BUY" if direction in ("long", "bullish") else "SELL_SHORT"
    logger.info(f"  Bucket C: MES {side} via {source_label} - conf {sig['confidence']:.1f}%")
    return {
        "bucket":        "C",
        "instrument":    "/MESO",
        "side":          side,
        "contracts":     2,
        "order_type":    "MARKET",
        "max_risk":      BUCKET_CONFIG["C"]["max_risk"],
        "stop_ticks":    10,
        "target_ticks":  30,
        "ml_signal":     sig.get("signal"),
        "ml_confidence": sig.get("confidence"),
        "signal_source": source_label,
        "notes":         f"MES ORB via {source_label} - {direction} {sig['confidence']:.1f}%",
    }

#############################################
# ORDER PREVIEW ATTACHMENT
#############################################

def _preview_price(instrument: str, spx_spot: float) -> float:
    """One quote fetch for preview sizing. Bucket C sizes off SPX spot."""
    try:
        resp = requests.get(f"{SCHWAB_PROXY_URL}/quotes",
                            params={"symbols": instrument}, timeout=5)
        resp.raise_for_status()
        q = resp.json().get(instrument, {})
        # Schwab /quotes nests price under a per-symbol "quote" key; fall back
        # to a flat shape just in case. Mirrors order_executor's Bucket C read.
        quote = q.get("quote", {})
        return float(quote.get("lastPrice") or quote.get("mark")
                     or q.get("lastPrice") or q.get("mark") or 0)
    except Exception:
        return 0.0


def attach_previews(trades: list[dict], spx_spot: float) -> list[dict]:
    from order_preview import preview_bucket_a, preview_bucket_b, preview_bucket_c
    for t in trades:
        b = t.get("bucket")
        if b == "A":
            t["preview"] = preview_bucket_a(t)
        elif b == "B":
            price = _preview_price(t.get("instrument", "QQQ"), spx_spot)
            t["preview"] = preview_bucket_b(t, price)
        elif b == "C":
            t["preview"] = preview_bucket_c(t, spx_spot)
    return trades

#############################################
# MAIN SELECTOR ENTRY POINT
#############################################

def select_trades(
    grade:      str,
    ml_signals: dict,
    gex:        dict,
    conditions: dict,
    pnl:        dict,
) -> list[dict]:
    """
    Grade A -> Buckets A + B + C (if signals qualify)
    Grade B -> Buckets A + B
    Grade C -> Bucket A only
    Grade X -> [] (no trades)
    """
    logger.info(f"Trade selector running - grade {grade}")
    trades   = []
    spx_spot = conditions.get("spx_spot", 5000.0)

    if grade in ("A", "B", "C"):
        structure = _select_bucket_a_structure(gex, ml_signals, conditions)
        if structure:
            strikes = _fetch_spx_strikes(structure, spx_spot)
            if strikes:
                trades.append({
                    "bucket":        "A",
                    "instrument":    "SPX",
                    "asset_class":   "OPTION",
                    "structure":     structure,
                    "strikes":       strikes,
                    "contracts":     1,
                    "max_risk":      BUCKET_CONFIG["A"]["max_risk"],
                    "profit_target": 0.50,
                    "ml_signal":     ml_signals.get("SPX", {}).get("signal"),
                    "ml_confidence": ml_signals.get("SPX", {}).get("confidence"),
                    "gex_net":       gex.get("net_gex"),
                    "charm_bias":    gex.get("charm_bias"),
                    "notes":         f"SPX 0-DTE {structure.replace('_',' ')} - GEX={gex.get('net_gex',0):.0f}",
                })
                logger.info(f"  Bucket A: {structure} strikes={strikes}")
            else:
                logger.warning("  Bucket A: strike selection failed - chain data unavailable")

    if grade in ("A", "B"):
        trade_b = _build_bucket_b_trade(ml_signals, conditions)
        if trade_b:
            trades.append(trade_b)

    if grade == "A":
        trade_c = _build_bucket_c_trade(ml_signals, conditions)
        if trade_c:
            trades.append(trade_c)

    logger.info(f"Trade selector complete - {len(trades)} trade(s) proposed")
    return attach_previews(trades, spx_spot=spx_spot)
