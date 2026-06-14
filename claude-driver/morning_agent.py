"""
morning_agent.py - Orchestrator: pulls all data, grades the day, fires trade selector
Version: 1.9.1
Last Updated: 2026-05-25

Version 1.9.1 Changes:
- Fixed fetch_schwab_signal net_pct extraction: added "netPercentChange"
  Index quotes ($SPX) use netPercentChange not netPercentChangeInDouble
  Confirmed from $VIX quote sub-fields in test_preflight.py

Version 1.9.0 Changes:
- Fixed fetch_market_conditions: symbols VIX/SPX -> $VIX/$SPX (confirmed via test_vix_symbols.py)
- Fixed index quote structure: Schwab returns values nested under "quote" sub-key for indices
  Added _index_price() helper that checks both flat (ETF) and nested (index) response formats
- Fixed fetch_schwab_signal: SPX signal now uses $SPX API symbol via _SCHWAB_API_SYMBOL map
  Signal dict key unchanged ("SPX") for trade_selector compatibility

Version 1.8.0 Changes:
- Added is_market_holiday() check — agent exits immediately on NYSE holidays

Version 1.7.9 Changes:
- Fixed _check_nt_buffer: reads NT_FEEDS_ML_SERVERS config flag

Version 1.7.8 Changes:
- Added NinjaTrader-aware warmup: NT warm -> skip /warmup, use real futures data

Version 1.7.7 Changes:
- Fixed OptionsAnalytics health check path

Version 1.7.6 Changes:
- Added _parse_gex_snapshot(): translates raw OptionsAnalytics response

Version 1.7.5 Changes:
- Fixed GEX endpoint: /options/snapshot/$SPX

Version 1.7.4 Changes:
- Reverted OPTIONS_ANALYTICS_URL to 8200

Version 1.7.3 Changes:
- Bar cache: SPY/QQQ fetched once each

Version 1.7.2 Changes:
- Fixed signal parsing: integer (1=Long, -1=Short, 0=Neutral)

Version 1.7.1 Changes:
- Fixed n_days: 7->5

Version 1.7.0 Changes:
- Added fetch_bars_from_schwab() and fetch_ml_signal_v6()

Version 1.0.0 Changes:
- Initial implementation
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, date

import requests
from apscheduler.schedulers.blocking import BlockingScheduler

from config import (
    ML_SERVERS,
    ML_FALLBACK_SIGNALS,
    NT_FEEDS_ML_SERVERS,
    MARKET_HOLIDAYS,
    OPTIONS_ANALYTICS_URL,
    OPTIONS_ANALYTICS_SNAPSHOT,
    SCHWAB_PROXY_URL,
    VIX_MAX_TRADE,
    VIX_MAX_A,
    ML_MIN_CONFIDENCE,
    AGENT_RUN_HOUR,
    AGENT_RUN_MIN,
    TRADE_LOG,
    PENDING_TRADE,
    LOG_DIR,
    RISK_LIMITS,
)

#############################################
# LOGGING
#############################################

os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, f"morning_agent_{date.today()}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

#############################################
# DATA COLLECTION
#############################################

def check_service_health() -> dict:
    services = {}
    for inst, url in ML_SERVERS.items():
        up = False
        for endpoint in ("/health", "/predict"):
            try:
                requests.get(f"{url}{endpoint}", timeout=0.5)
                up = True; break
            except Exception:
                pass
        services[f"ML:{inst}"] = up
    try:
        requests.get(f"{OPTIONS_ANALYTICS_URL}/options/health", timeout=0.5)
        services["OptionsAnalytics"] = True
    except Exception:
        services["OptionsAnalytics"] = False
    try:
        requests.get(f"{SCHWAB_PROXY_URL}/health", timeout=0.5)
        services["SchwabProxy"] = True
    except Exception:
        try:
            requests.get(f"{SCHWAB_PROXY_URL}/quotes", params={"symbols": "SPY"}, timeout=0.5)
            services["SchwabProxy"] = True
        except Exception:
            services["SchwabProxy"] = False
    up   = [k for k, v in services.items() if v]
    down = [k for k, v in services.items() if not v]
    if up:   logger.info(f"Services UP:   {', '.join(up)}")
    if down: logger.info(f"Services DOWN: {', '.join(down)}")
    return services


ML_BAR_PROXY  = {"MES": "SPY", "ES": "SPY", "MNQ": "QQQ", "NQ": "QQQ"}
WARMUP_BARS   = 65
FEATURE_COUNT = 25


def is_market_holiday() -> bool:
    """Return True if today is a US equity market holiday."""
    today = date.today().isoformat()
    if today in MARKET_HOLIDAYS:
        logger.info(f"Market holiday: {today} — skipping run")
        return True
    return False


def fetch_bars_from_schwab(symbol: str, n_days: int = 5, freq_min: int = 5, timeout: int = 15):
    """Fetch intraday OHLCV bars. Valid period values: [1, 2, 3, 4, 5, 10]."""
    import pandas as pd
    try:
        resp = requests.get(
            f"{SCHWAB_PROXY_URL}/pricehistory",
            params={"symbol": symbol, "periodType": "day", "period": n_days,
                    "frequencyType": "minute", "frequency": freq_min},
            timeout=timeout,
        )
        resp.raise_for_status()
        candles = resp.json().get("candles", [])
        if not candles:
            logger.warning(f"fetch_bars: no candles for {symbol}"); return None
        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        df = df.sort_values("datetime").reset_index(drop=True)
        logger.info(f"fetch_bars [{symbol}]: {len(df)} bars "
                    f"({df['datetime'].iloc[0]} -> {df['datetime'].iloc[-1]})")
        return df[["open", "high", "low", "close", "volume", "datetime"]]
    except Exception as exc:
        logger.warning(f"fetch_bars failed for {symbol}: {exc}"); return None


def _check_nt_buffer(url: str = None, timeout: float = 1.0) -> bool:
    """
    Return True if NinjaTrader is feeding ML servers with live futures bars.
    Reads NT_FEEDS_ML_SERVERS config flag — /session endpoint does not expose buffer state.
    """
    if NT_FEEDS_ML_SERVERS:
        logger.info("  NT_FEEDS_ML_SERVERS=True — NT buffer warm, skipping /warmup")
        return True
    return False


def fetch_ml_signal_v6(instrument: str, available: bool = True, timeout: int = 10,
                        cached_bars=None) -> dict:
    """
    Compute current F1-F25 features and call V6 ML server.
    NT warm  -> skip /warmup, only compute current feature vector (real futures data).
    NT cold  -> full warmup cycle using SPY/QQQ proxy bars.
    Signal:  1=Long/Bullish, -1=Short/Bearish, 0=Neutral
    """
    from feature_engineer import compute_features, get_valid_features
    base = {"instrument": instrument, "signal": "Neutral", "confidence": 0.0,
            "direction": "flat", "source": "ml_unavailable", "error": None}
    if not available: base["error"] = "server down"; return base
    url = ML_SERVERS.get(instrument)
    if not url: base["error"] = "not configured"; return base

    proxy_symbol = ML_BAR_PROXY.get(instrument, "SPY")
    nt_warm      = _check_nt_buffer(url)
    n_days       = 3 if nt_warm else 5
    min_bars     = 210 if nt_warm else WARMUP_BARS + 50

    df = cached_bars if cached_bars is not None else fetch_bars_from_schwab(proxy_symbol, n_days=n_days)
    if df is None or len(df) < min_bars:
        logger.warning(f"[{instrument}] insufficient bars — falling back to Schwab signal")
        base["error"] = "insufficient bars"; return base

    try:
        features = compute_features(
            opens=df["open"].values, highs=df["high"].values, lows=df["low"].values,
            closes=df["close"].values, volumes=df["volume"].values, datetimes=df["datetime"]
        )
        n_rows = 1 if nt_warm else WARMUP_BARS + 1
        valid  = get_valid_features(features, n_bars=n_rows)
        if len(valid) < 1: base["error"] = "no valid feature rows"; return base
    except Exception as exc:
        logger.error(f"[{instrument}] feature computation failed: {exc}", exc_info=True)
        base["error"] = str(exc); return base

    current_feat = valid[-1].tolist()

    if not nt_warm:
        warmup_rows = valid[:-1].tolist()
        try:
            wr = requests.post(f"{url}/warmup", json={"historical_bars": warmup_rows}, timeout=timeout)
            if wr.status_code not in (200, 204):
                logger.warning(f"[{instrument}] /warmup returned {wr.status_code}")
        except Exception as exc: logger.warning(f"[{instrument}] /warmup failed: {exc}")
    else:
        logger.info(f"  [{instrument}] NT buffer warm — skipping /warmup")

    try:
        er = requests.post(f"{url}/ensemble",
                           json={"features": current_feat, "method": "confidence"}, timeout=timeout)
        er.raise_for_status(); data = er.json()
    except Exception as exc:
        logger.warning(f"[{instrument}] /ensemble failed: {exc}"); base["error"] = str(exc); return base

    raw_signal = data.get("signal", 0)
    confidence = float(data.get("confidence", data.get("probability", 0.0)))
    agreement  = float(data.get("agreement", 0.0))
    if 0.0 < confidence <= 1.0: confidence *= 100.0
    try:   raw_signal = int(raw_signal)
    except (TypeError, ValueError): raw_signal = 0

    if raw_signal == 1:    signal_str, direction = "Bullish", "bullish"
    elif raw_signal == -1: signal_str, direction = "Bearish", "bearish"
    else:                  signal_str, direction = "Neutral",  "flat"

    sig = {"instrument": instrument, "signal": signal_str, "confidence": confidence,
           "direction": direction, "agreement": agreement, "raw_signal": raw_signal,
           "source": "ml_server_v6", "proxy": proxy_symbol, "error": None}
    logger.info(f"ML V6 signal [{instrument} via {proxy_symbol}]: "
                f"{signal_str} {confidence:.1f}% (dir={direction}, agree={agreement:.2f})")
    return sig


def fetch_ml_signal(instrument: str, available: bool = True, timeout: int = 1) -> dict:
    """Legacy /predict endpoint fallback."""
    if not available:
        return {"instrument": instrument, "signal": "Neutral", "confidence": 0.0,
                "direction": "flat", "source": "ml_unavailable", "error": "server down"}
    url = ML_SERVERS.get(instrument)
    if not url:
        return {"instrument": instrument, "signal": "Neutral", "confidence": 0.0,
                "direction": "flat", "source": "ml_unavailable", "error": "not configured"}
    try:
        resp = requests.get(f"{url}/predict", timeout=timeout); resp.raise_for_status()
        data = resp.json()
        return {"instrument": instrument, "signal": data.get("signal", "Neutral"),
                "confidence": float(data.get("confidence", 0.0)),
                "direction": data.get("direction", "flat"), "source": "ml_server", "error": None}
    except Exception as exc:
        logger.debug(f"ML signal unavailable for {instrument}: {exc}")
        return {"instrument": instrument, "signal": "Neutral", "confidence": 0.0,
                "direction": "flat", "source": "ml_unavailable", "error": str(exc)}


def _index_price(quote_dict: dict, *fields) -> float | None:
    """
    Extract price from a Schwab quote response.

    Indices ($VIX, $SPX, $VIX1D) nest values under a "quote" sub-key:
      {"assetMainType": "INDEX", "quote": {"lastPrice": 20.3, ...}}
    ETFs (SPY, QQQ) have values flat at the top level:
      {"lastPrice": 590.1, ...}

    Checks both levels. Confirmed correct via test_vix_symbols.py on 2026-05-25.
    """
    if not quote_dict:
        return None
    search_dicts   = [quote_dict, quote_dict.get("quote", {})]
    default_fields = fields or ("lastPrice", "mark", "closePrice", "close")
    for d in search_dicts:
        for field in default_fields:
            val = d.get(field)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    return None


# Schwab API symbol map: indices need $ prefix — keep original as signal dict key
_SCHWAB_API_SYMBOL = {"SPX": "$SPX", "VIX": "$VIX", "VIX1D": "$VIX1D"}


def fetch_schwab_signal(instrument: str, timeout: int = 8) -> dict:
    """Derive directional signal for equities/indices from Schwab quote data."""
    from config import SCHWAB_SIGNAL_THRESHOLDS
    thresholds = SCHWAB_SIGNAL_THRESHOLDS
    api_symbol = _SCHWAB_API_SYMBOL.get(instrument, instrument)
    base = {"instrument": instrument, "signal": "Neutral", "confidence": 0.0,
            "direction": "flat", "source": "schwab_quote", "error": None}
    try:
        resp = requests.get(f"{SCHWAB_PROXY_URL}/quotes", params={"symbols": api_symbol}, timeout=timeout)
        resp.raise_for_status()
        q = resp.json().get(api_symbol, {})
        if not q: base["error"] = f"No quote data for {api_symbol}"; return base
        last_price  = _index_price(q, "lastPrice", "mark") or 0.0
        close_price = _index_price(q, "closePrice", "regularMarketLastPrice") or 0.0
        net_pct     = _index_price(q, "netPercentChangeInDouble",
                                   "regularMarketPercentChangeInDouble",
                                   "netPercentChange") or 0.0
        if net_pct == 0 and close_price > 0 and last_price > 0:
            net_pct = ((last_price - close_price) / close_price) * 100
        pre_vol   = float(q.get("preMarketVolume")     or 0)
        reg_vol   = float(q.get("regularMarketVolume") or 1)
        vol_ratio = (pre_vol / reg_vol) if reg_vol > 0 else 1.0
        if   net_pct >= thresholds["strong_bullish_pct"]: direction, signal, base_conf = "bullish", "Bullish", 82.0
        elif net_pct >= thresholds["bullish_pct"]:        direction, signal, base_conf = "bullish", "Bullish", 70.0
        elif net_pct <= thresholds["strong_bearish_pct"]: direction, signal, base_conf = "bearish", "Bearish", 82.0
        elif net_pct <= thresholds["bearish_pct"]:        direction, signal, base_conf = "bearish", "Bearish", 70.0
        else:                                             direction, signal, base_conf = "flat",    "Neutral", 55.0
        vol_boost  = 5.0 if vol_ratio >= thresholds["volume_surge_ratio"] else 0.0
        confidence = min(base_conf + vol_boost, 95.0)
        if direction == "flat" or confidence < thresholds["min_confidence"]:
            direction, signal, confidence = "flat", "Neutral", 0.0
        logger.info(f"Schwab signal [{instrument}]: {signal} {confidence:.1f}% "
                    f"(chg={net_pct:+.2f}%, vol_ratio={vol_ratio:.2f})")
        return {"instrument": instrument, "signal": signal, "confidence": confidence,
                "direction": direction, "net_pct": round(net_pct, 3),
                "vol_ratio": round(vol_ratio, 2), "last_price": last_price,
                "source": "schwab_quote", "error": None}
    except Exception as exc:
        logger.warning(f"Schwab signal fetch failed for {instrument}: {exc}")
        base["error"] = str(exc); return base


def fetch_all_ml_signals(health: dict | None = None) -> dict:
    """ML-first / Schwab-fallback. Bar cache: SPY once for MES+ES; QQQ once for MNQ+NQ."""
    from config import SCHWAB_SIGNAL_INSTRUMENTS
    health = health or {}; signals = {}
    for instrument in SCHWAB_SIGNAL_INSTRUMENTS:
        signals[instrument] = fetch_schwab_signal(instrument)

    first_url   = next(iter(ML_SERVERS.values()), None)
    nt_feeding  = _check_nt_buffer(first_url) if first_url else False
    n_days_bars = 3 if nt_feeding else 5

    bar_cache = {}
    for instrument in ML_SERVERS:
        proxy = ML_BAR_PROXY.get(instrument, "SPY")
        if proxy not in bar_cache:
            bar_cache[proxy] = fetch_bars_from_schwab(proxy, n_days=n_days_bars)
            n = len(bar_cache[proxy]) if bar_cache[proxy] is not None else 0
            logger.info(f"  bar_cache: fetched {proxy} ({n} bars, NT={'warm' if nt_feeding else 'cold'})")

    for instrument in ML_SERVERS:
        available    = health.get(f"ML:{instrument}", True)
        ml_sig       = fetch_ml_signal_v6(instrument, available=available,
                                           cached_bars=bar_cache.get(ML_BAR_PROXY.get(instrument, "SPY")))
        fallback_key = ML_FALLBACK_SIGNALS.get(instrument)
        if ml_sig["source"] == "ml_server_v6" and ml_sig["confidence"] > 0:
            signals[instrument] = ml_sig
        elif fallback_key and fallback_key in signals:
            fallback = dict(signals[fallback_key])
            fallback["instrument"] = instrument
            fallback["source"]     = f"schwab_fallback({fallback_key})"
            signals[instrument]    = fallback
            logger.info(f"  [{instrument}] ML failed -> fallback ({fallback_key}): "
                        f"{fallback['signal']} {fallback['confidence']:.1f}%")
        else:
            signals[instrument] = ml_sig

    logger.info(f"All signals: { {k: v['signal']+' '+str(round(v['confidence'],1))+'%' for k,v in signals.items()} }")
    return signals


def _parse_gex_snapshot(raw: dict) -> dict:
    """Translate raw OptionsAnalytics snapshot into trade_selector-compatible fields."""
    spot  = float(raw.get("spot", 0) or 0)
    gex   = raw.get("gex",   {}) or {}
    charm = raw.get("charm", {}) or {}
    by_strike = gex.get("by_strike_cp", {}) or {}
    net_gex   = sum((v.get("CALL", 0) or 0) + (v.get("PUT", 0) or 0)
                    for v in by_strike.values() if isinstance(v, dict))
    top_pos   = gex.get("top_pos", []) or []
    top_neg   = gex.get("top_neg", []) or []
    bilateral = len(top_pos) > 0 and len(top_neg) > 0
    charm_flip = charm.get("flip"); gex_flip = gex.get("flip")
    if charm_flip is None or spot == 0:    charm_bias = "neutral"
    elif float(charm_flip) > spot:         charm_bias = "bullish"
    elif float(charm_flip) < spot:         charm_bias = "bearish"
    else:                                  charm_bias = "neutral"
    return {
        "symbol": raw.get("symbol"), "spot": spot,
        "net_gex": net_gex, "gex_flip": gex_flip,
        "charm_flip_level": charm_flip, "charm_bias": charm_bias, "bilateral": bilateral,
        "top_pos_gex": [s.get("strike") for s in top_pos[:3]],
        "top_neg_gex": [s.get("strike") for s in top_neg[:3]],
        "expected_move": raw.get("expected_move", {}),
        "dte": raw.get("dte"), "timestamp": raw.get("timestamp"),
    }


def fetch_gex_snapshot() -> dict:
    """Fetch GEX/DEX/Charm from OptionsAnalytics (port 8200), translate to trade_selector fields."""
    try:
        resp = requests.get(f"{OPTIONS_ANALYTICS_URL}{OPTIONS_ANALYTICS_SNAPSHOT}/$SPX", timeout=10)
        resp.raise_for_status()
        gex = _parse_gex_snapshot(resp.json())
        logger.info(
            f"GEX snapshot: spot={gex.get('spot')}, net_gex={gex.get('net_gex', 0):.0f}, "
            f"gex_flip={gex.get('gex_flip')}, charm_flip={gex.get('charm_flip_level')}, "
            f"charm_bias={gex.get('charm_bias')}, bilateral={gex.get('bilateral')}"
        )
        return gex
    except Exception as exc:
        logger.warning(f"GEX snapshot fetch failed: {exc}"); return {}


def fetch_market_conditions() -> dict:
    """
    Fetch VIX, SPX spot and VIX1D from Schwab proxy.
    Correct symbols confirmed via test_vix_symbols.py: $VIX, $SPX, $VIX1D.
    Index quotes nest values under "quote" sub-key; _index_price() handles both.
    """
    conditions = {"vix": None, "spx_spot": None, "pc_ratio": None, "vix1d": None, "error": None}
    try:
        resp   = requests.get(f"{SCHWAB_PROXY_URL}/quotes?symbols=$VIX,$SPX,$VIX1D", timeout=10)
        resp.raise_for_status(); quotes = resp.json()
        conditions["vix"]      = _index_price(quotes.get("$VIX",   {}))
        conditions["spx_spot"] = _index_price(quotes.get("$SPX",   {}))
        conditions["vix1d"]    = _index_price(quotes.get("$VIX1D", {}))
        logger.info(f"Market conditions: VIX={conditions['vix']}, "
                    f"SPX={conditions['spx_spot']}, VIX1D={conditions['vix1d']}")
    except Exception as exc:
        logger.warning(f"Market conditions fetch failed: {exc}"); conditions["error"] = str(exc)
    return conditions


def fetch_current_pnl() -> dict:
    """Read today's, weekly, and monthly P&L from the trade log."""
    pnl = {"today": 0.0, "week": 0.0, "month": 0.0}
    if not os.path.exists(TRADE_LOG): return pnl
    try:
        with open(TRADE_LOG, "r") as f: trades = json.load(f)
        today_str  = date.today().isoformat(); today_dt = date.today()
        week_start = (today_dt - __import__("datetime").timedelta(days=today_dt.weekday())).isoformat()
        month_str  = today_str[:7]
        for t in trades:
            p = float(t.get("pnl", 0))
            if t.get("date") == today_str:              pnl["today"] += p
            if t.get("date", "") >= week_start:         pnl["week"]  += p
            if t.get("date", "").startswith(month_str): pnl["month"] += p
    except Exception as exc: logger.warning(f"P&L fetch failed: {exc}")
    return pnl

#############################################
# DAY GRADING
#############################################

def grade_day(conditions: dict, pnl: dict) -> tuple[str, list[str]]:
    reasons = []; failures = []
    vix = conditions.get("vix"); vix1d = conditions.get("vix1d")
    if vix and vix > VIX_MAX_TRADE:           failures.append(f"VIX={vix:.1f} > {VIX_MAX_TRADE} hard limit")
    if pnl["today"] <= -RISK_LIMITS["daily_max_loss"]:   failures.append(f"Daily loss limit hit (${pnl['today']:.2f})")
    if pnl["week"]  <= -RISK_LIMITS["weekly_max_loss"]:  failures.append(f"Weekly loss limit hit (${pnl['week']:.2f})")
    if pnl["month"] <= -RISK_LIMITS["monthly_max_loss"]: failures.append(f"Monthly drawdown limit hit (${pnl['month']:.2f})")
    if failures: return "X", [f"BLOCKED: {r}" for r in failures]
    score = 10; data_warnings = []
    if vix is None:
        score -= 2; data_warnings.append("VIX unavailable - defaulting conservative")
    elif vix < 15:  score += 1; reasons.append(f"VIX={vix:.1f} - very low, ideal")
    elif vix < 20:  reasons.append(f"VIX={vix:.1f} - normal range")
    elif vix < 25:  score -= 2; reasons.append(f"VIX={vix:.1f} - elevated, reduce size")
    else:           score -= 4; reasons.append(f"VIX={vix:.1f} - high, Bucket A skipped")
    if vix1d and vix and (vix1d / vix) < 0.85:
        score -= 1; reasons.append(f"VIX1D/VIX={vix1d/vix:.2f} - short-term premium compressed")
    if data_warnings: reasons.extend(data_warnings)
    grade = "A" if score >= 10 else "B" if score >= 8 else "C" if score >= 6 else "X"
    reasons.insert(0, f"Grade {grade} - score {score}/11")
    return grade, reasons

#############################################
# MAIN AGENT RUN
#############################################

def run_morning_agent():
    logger.info("=" * 60); logger.info("MORNING AGENT - starting run"); logger.info("=" * 60)
    if is_market_holiday():
        logger.info("No action taken — market holiday.")
        return
    health     = check_service_health()
    ml_signals = fetch_all_ml_signals(health=health)
    gex        = fetch_gex_snapshot()
    conditions = fetch_market_conditions()
    pnl        = fetch_current_pnl()
    grade, reasons = grade_day(conditions, pnl)
    logger.info(f"Day grade: {grade}")
    for r in reasons: logger.info(f"  {r}")
    if grade == "X": _notify_skip(grade, reasons, conditions, pnl); return
    try:
        from trade_selector import select_trades
        proposed_trades = select_trades(grade=grade, ml_signals=ml_signals,
                                        gex=gex, conditions=conditions, pnl=pnl)
    except Exception as exc:
        logger.error(f"Trade selector failed: {exc}", exc_info=True); _notify_error(str(exc)); return
    if not proposed_trades:
        logger.info("Trade selector returned no trades - skipping.")
        _notify_skip(grade, ["No qualifying setups."], conditions, pnl); return
    payload = {
        "timestamp": datetime.now().isoformat(), "date": date.today().isoformat(),
        "grade": grade, "grade_reasons": reasons, "conditions": conditions,
        "ml_signals": ml_signals, "gex_snapshot": gex,
        "pnl_today": pnl["today"], "pnl_week": pnl["week"],
        "proposed_trades": proposed_trades, "status": "pending",
    }
    os.makedirs(os.path.dirname(PENDING_TRADE), exist_ok=True)
    with open(PENDING_TRADE, "w") as f: json.dump(payload, f, indent=2)
    logger.info(f"Pending trade written: {PENDING_TRADE}")
    _send_approval_request(payload)


def _notify_skip(grade, reasons, conditions, pnl):
    try:
        requests.post(
            f"http://{__import__('config').APPROVAL_HOST}:{__import__('config').APPROVAL_PORT}/notify_skip",
            json={"timestamp": datetime.now().isoformat(), "date": date.today().isoformat(),
                  "grade": grade, "status": "no_trade", "reasons": reasons,
                  "conditions": conditions, "pnl_today": pnl.get("today", 0),
                  "pnl_week": pnl.get("week", 0)}, timeout=5)
    except Exception: logger.warning("Approval server not reachable.")


def _send_approval_request(payload):
    try:
        requests.post(
            f"http://{__import__('config').APPROVAL_HOST}:{__import__('config').APPROVAL_PORT}/pending",
            json=payload, timeout=5).raise_for_status()
        logger.info("Approval request sent.")
    except Exception as exc: logger.error(f"Could not reach approval server: {exc}")


def _notify_error(message):
    try:
        requests.post(
            f"http://{__import__('config').APPROVAL_HOST}:{__import__('config').APPROVAL_PORT}/notify_error",
            json={"error": message, "timestamp": datetime.now().isoformat()}, timeout=5)
    except Exception: pass

#############################################
# ENTRY POINT
#############################################

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Claude is the Driver - Morning Agent")
    parser.add_argument("--now", action="store_true", help="Run immediately")
    args = parser.parse_args()
    if args.now:
        logger.info("Running immediately (--now flag)"); run_morning_agent()
    else:
        logger.info(f"Scheduler started - will run at {AGENT_RUN_HOUR:02d}:{AGENT_RUN_MIN:02d} ET")
        scheduler = BlockingScheduler(timezone="America/New_York")
        scheduler.add_job(run_morning_agent, trigger="cron",
                          day_of_week="mon-fri", hour=AGENT_RUN_HOUR, minute=AGENT_RUN_MIN)
        try: scheduler.start()
        except KeyboardInterrupt: logger.info("Scheduler stopped.")
