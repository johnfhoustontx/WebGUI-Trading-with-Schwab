"""
OptionsScanner - Automated SPX/SPY/QQQ Credit Spread Scanner
Version: 1.0.0
Last Updated: 2026-04-03

Scans the market every 15 minutes on trading days (8 AM CT - 3:15 PM CT)
and suggests 0-DTE and swing credit spread trades.

Usage:
    python scanner.py              # Run scanner (auto-schedules)
    python scanner.py --once       # Single scan, no loop
    python scanner.py --test       # Test connection only

Version 1.0.0 Changes:
- Initial implementation
- 0-DTE PCS/CCS/Iron Condor scanner for SPX, SPY, QQQ
- Swing trade scanner (1-15 DTE)
- VIX regime + IV skew analysis
- Technical levels (20/50/200 SMA)
- Position sizing
- Console + log file output
- Trading day / market hours scheduling
"""

import os
import sys
import json
import time
import logging
import subprocess
import math
from pathlib import Path
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from watchlist import get_scan_symbols
import daily_trade_log

import pathlib as _pathlib
sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root
from shared.market_calendar import HOLIDAYS as _SHARED_HOLIDAYS  # noqa: E402
from shared.symbols import SCAN_BASE as _SCAN_BASE, VIX as _VIX  # noqa: E402

try:
    import schwab
    from schwab.auth import client_from_token_file
except ImportError:
    print("[!] Installing schwab-py...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "schwab-py", "-q"])
    import schwab
    from schwab.auth import client_from_token_file

try:
    import httpx
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx", "-q"])
    import httpx

#############################################
# CONFIGURATION
#############################################

class Config:
    # Schwab credentials
    APPSETTINGS = Path(__file__).parent.parent / "appsettings.json"
    TOKEN_FILE = Path.home() / ".schwab-mcp" / "token.json"

    # Symbols to scan
    SYMBOLS = list(_SCAN_BASE)
    VIX_SYMBOL = _VIX

    # Timezone
    TZ = ZoneInfo("America/Chicago")

    # Market hours (CT)
    SCAN_START_HOUR = 8
    SCAN_START_MIN = 0
    SCAN_END_HOUR = 15
    SCAN_END_MIN = 15
    SCAN_INTERVAL_MINUTES = 15

    # Account sizing
    ACCOUNT_SIZE = 100000
    MAX_RISK_PCT = 0.05  # 5% max risk per trade
    MAX_DAILY_RISK_PCT = 0.15  # 15% max daily risk

    # 0-DTE settings
    ZERO_DTE_PUT_DELTA_MIN = -0.25
    ZERO_DTE_PUT_DELTA_MAX = -0.08
    ZERO_DTE_CALL_DELTA_MIN = 0.08
    ZERO_DTE_CALL_DELTA_MAX = 0.25
    ZERO_DTE_MIN_CREDIT_PCT = 0.15  # Min 15% of width as credit

    # Swing settings (1-15 DTE)
    SWING_DTE_MIN = 1
    SWING_DTE_MAX = 15
    SWING_PUT_DELTA_MIN = -0.22
    SWING_PUT_DELTA_MAX = -0.08
    SWING_CALL_DELTA_MIN = 0.08
    SWING_CALL_DELTA_MAX = 0.22
    SWING_MIN_CREDIT_PCT = 0.12

    # Spread widths by symbol
    SPREAD_WIDTHS = {
        "$SPX": [25, 50],
        "SPY": [2, 3, 5],
        "QQQ": [2, 3, 5],
    }

    # Logging
    LOG_DIR = Path(__file__).parent / "logs"
    LOG_DIR.mkdir(exist_ok=True)

    # Sourced from shared/market_calendar.py (single source; update yearly there).
    HOLIDAYS = _SHARED_HOLIDAYS

#############################################
# LOGGING SETUP
#############################################

def setup_logging():
    today_str = datetime.now(Config.TZ).strftime("%Y-%m-%d")
    log_file = Config.LOG_DIR / f"scanner_{today_str}.log"

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    logger = logging.getLogger("scanner")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

log = setup_logging()

#############################################
# SCHWAB CLIENT
#############################################

def get_schwab_client():
    """Initialize schwab-py client from token file."""
    with open(Config.APPSETTINGS) as f:
        cfg = json.load(f)["Schwab"]
    api_key = cfg["AppKey"]
    app_secret = cfg["AppSecret"]

    try:
        client = client_from_token_file(str(Config.TOKEN_FILE), api_key, app_secret)
        return client
    except Exception as e:
        log.error(f"Failed to create Schwab client: {e}")
        log.error("Run schwab_oauth_setup.py to refresh your token.")
        return None

#############################################
# MARKET HOURS / TRADING DAY CHECK
#############################################

def is_trading_day(dt=None):
    """Check if the given date is a trading day (weekday, not holiday)."""
    if dt is None:
        dt = datetime.now(Config.TZ)
    d = dt.date() if hasattr(dt, "date") else dt
    if d.weekday() >= 5:
        return False
    if d in Config.HOLIDAYS:
        return False
    return True


def is_market_hours(dt=None):
    """Check if current time is within scan window (8:00 AM - 3:15 PM CT)."""
    if dt is None:
        dt = datetime.now(Config.TZ)
    if not is_trading_day(dt):
        return False
    t = dt.time()
    start = datetime.now(Config.TZ).replace(hour=Config.SCAN_START_HOUR, minute=Config.SCAN_START_MIN, second=0).time()
    end = datetime.now(Config.TZ).replace(hour=Config.SCAN_END_HOUR, minute=Config.SCAN_END_MIN, second=0).time()
    return start <= t <= end


def next_scan_time():
    """Calculate the next scan time."""
    now = datetime.now(Config.TZ)
    interval = timedelta(minutes=Config.SCAN_INTERVAL_MINUTES)
    next_t = now + interval
    # Round up to next interval boundary
    minutes = next_t.minute
    rounded = math.ceil(minutes / Config.SCAN_INTERVAL_MINUTES) * Config.SCAN_INTERVAL_MINUTES
    if rounded >= 60:
        next_t = next_t.replace(minute=0, second=0) + timedelta(hours=1)
    else:
        next_t = next_t.replace(minute=rounded, second=0)
    return next_t

#############################################
# DATA FETCHING
#############################################

def fetch_quotes(client, symbols):
    """Fetch real-time quotes for multiple symbols."""
    try:
        resp = client.get_quotes(symbols)
        if resp.status_code == 200:
            return resp.json()
        log.warning(f"Quote fetch returned {resp.status_code}")
        return {}
    except Exception as e:
        log.error(f"Quote fetch failed: {e}")
        return {}


def fetch_option_chain(client, symbol, from_date=None, to_date=None, contract_type="ALL"):
    """Fetch option chain for a symbol."""
    try:
        kwargs = {"contract_type": getattr(client.Options.ContractType, contract_type)}
        if from_date:
            kwargs["from_date"] = from_date
        if to_date:
            kwargs["to_date"] = to_date

        resp = client.get_option_chain(symbol, **kwargs)
        if resp.status_code == 200:
            return resp.json()
        log.warning(f"Option chain for {symbol} returned {resp.status_code}")
        return None
    except Exception as e:
        log.error(f"Option chain fetch for {symbol} failed: {e}")
        return None


def fetch_price_history(client, symbol, period_type="month", period=3, frequency_type="daily"):
    """Fetch price history for technical analysis."""
    try:
        pt = getattr(client.PriceHistory.PeriodType, period_type.upper())
        ft = getattr(client.PriceHistory.FrequencyType, frequency_type.upper())
        if period_type.upper() == "MONTH":
            p = getattr(client.PriceHistory.Period, f"THREE_MONTHS") if period == 3 else getattr(client.PriceHistory.Period, f"ONE_MONTH")
        else:
            p = getattr(client.PriceHistory.Period, f"ONE_YEAR")
        f = getattr(client.PriceHistory.Frequency, "DAILY")

        resp = client.get_price_history_every_day(symbol)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        log.error(f"Price history for {symbol} failed: {e}")
        return None

#############################################
# TECHNICAL ANALYSIS
#############################################

def calculate_technicals(price_data):
    """Calculate SMAs and key levels from price history."""
    if not price_data or "candles" not in price_data:
        return None

    candles = price_data["candles"]
    if len(candles) < 50:
        return None

    closes = [c["close"] for c in candles]

    def sma(data, period):
        if len(data) < period:
            return None
        return sum(data[-period:]) / period

    current = closes[-1]
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200) if len(closes) >= 200 else None

    # Recent support/resistance (20-day high/low)
    recent_high = max(c["high"] for c in candles[-20:])
    recent_low = min(c["low"] for c in candles[-20:])

    # Average True Range (14-day)
    trs = []
    for i in range(1, min(15, len(candles))):
        c = candles[-i]
        p = candles[-i - 1]
        tr = max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"]))
        trs.append(tr)
    atr14 = sum(trs) / len(trs) if trs else 0

    # Trend assessment
    trend = "NEUTRAL"
    if sma20 and sma50:
        if current > sma20 > sma50:
            trend = "BULLISH"
        elif current < sma20 < sma50:
            trend = "BEARISH"
        elif current > sma20 and sma20 < sma50:
            trend = "RECOVERING"
        elif current < sma20 and sma20 > sma50:
            trend = "WEAKENING"

    return {
        "current": round(current, 2),
        "sma20": round(sma20, 2) if sma20 else None,
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "recent_high": round(recent_high, 2),
        "recent_low": round(recent_low, 2),
        "atr14": round(atr14, 2),
        "trend": trend,
    }

#############################################
# VIX REGIME ANALYSIS
#############################################

def assess_vix_regime(vix_price):
    """Determine VIX regime and recommended adjustments."""
    if vix_price < 15:
        return {"regime": "LOW", "label": "Low vol — thin premiums, tighten widths",
                "size_mult": 1.2, "delta_bias": "neutral"}
    elif vix_price < 20:
        return {"regime": "NORMAL", "label": "Normal — standard credit spread environment",
                "size_mult": 1.0, "delta_bias": "neutral"}
    elif vix_price < 30:
        return {"regime": "ELEVATED", "label": "Elevated — rich premiums, widen for safety",
                "size_mult": 0.75, "delta_bias": "wider_otm"}
    else:
        return {"regime": "HIGH", "label": "High vol — extreme premiums, reduce size significantly",
                "size_mult": 0.5, "delta_bias": "far_otm"}

#############################################
# OPTION CHAIN PARSING
#############################################

def parse_chain_to_spreads(chain_data, symbol, target_dte_min, target_dte_max,
                           put_delta_min, put_delta_max, call_delta_min, call_delta_max,
                           min_credit_pct, widths, trade_type="0-DTE"):
    """Parse option chain and find credit spread candidates."""
    if not chain_data:
        return []

    underlying_price = chain_data.get("underlyingPrice", 0)
    if underlying_price == 0:
        return []

    put_map = chain_data.get("putExpDateMap", {})
    call_map = chain_data.get("callExpDateMap", {})

    suggestions = []

    # Process PUTS for PCS
    for exp_key, strikes_data in put_map.items():
        exp_date_str, dte_str = exp_key.split(":")
        dte = int(float(dte_str))
        if not (target_dte_min <= dte <= target_dte_max):
            continue

        # Build strike -> option data map
        put_strikes = {}
        for strike_str, contracts in strikes_data.items():
            strike = float(strike_str)
            if contracts:
                c = contracts[0]
                delta = c.get("delta", 0)
                if delta is None:
                    continue
                put_strikes[strike] = {
                    "strike": strike, "delta": delta, "mark": c.get("mark", 0),
                    "bid": c.get("bid", 0), "ask": c.get("ask", 0),
                    "theta": c.get("theta", 0), "vega": c.get("vega", 0),
                    "gamma": c.get("gamma", 0), "iv": c.get("volatility", 0),
                    "volume": c.get("totalVolume", 0), "oi": c.get("openInterest", 0),
                }

        # Screen PCS candidates
        for strike_val, short_opt in put_strikes.items():
            d = short_opt["delta"]
            if not (put_delta_min <= d <= put_delta_max):
                continue
            if short_opt["mark"] <= 0:
                continue

            for width in widths:
                long_strike = strike_val - width
                if long_strike not in put_strikes:
                    continue
                long_opt = put_strikes[long_strike]
                credit = short_opt["mark"] - long_opt["mark"]
                if credit <= 0:
                    continue
                max_loss = width - credit
                if max_loss <= 0:
                    continue
                credit_pct = credit / width
                if credit_pct < min_credit_pct:
                    continue

                rr = credit / max_loss * 100
                pop = (1 + d) * 100  # approx PoP from delta
                net_theta = (short_opt["theta"] or 0) - (long_opt["theta"] or 0)
                net_vega = (short_opt["vega"] or 0) - (long_opt["vega"] or 0)
                breakeven = strike_val - credit

                suggestions.append({
                    "symbol": symbol,
                    "type": "PCS",
                    "trade_type": trade_type,
                    "expiration": exp_date_str,
                    "dte": dte,
                    "short_strike": strike_val,
                    "long_strike": long_strike,
                    "width": width,
                    "credit": round(credit, 2),
                    "max_loss": round(max_loss, 2),
                    "rr_pct": round(rr, 1),
                    "pop_pct": round(pop, 1),
                    "short_delta": round(d, 4),
                    "net_theta": round(net_theta, 3),
                    "net_vega": round(net_vega, 3),
                    "short_iv": round(short_opt["iv"], 1),
                    "breakeven": round(breakeven, 2),
                    "short_volume": short_opt["volume"],
                    "underlying_price": underlying_price,
                })

    # Process CALLS for CCS
    for exp_key, strikes_data in call_map.items():
        exp_date_str, dte_str = exp_key.split(":")
        dte = int(float(dte_str))
        if not (target_dte_min <= dte <= target_dte_max):
            continue

        call_strikes = {}
        for strike_str, contracts in strikes_data.items():
            strike = float(strike_str)
            if contracts:
                c = contracts[0]
                delta = c.get("delta", 0)
                if delta is None:
                    continue
                call_strikes[strike] = {
                    "strike": strike, "delta": delta, "mark": c.get("mark", 0),
                    "bid": c.get("bid", 0), "ask": c.get("ask", 0),
                    "theta": c.get("theta", 0), "vega": c.get("vega", 0),
                    "gamma": c.get("gamma", 0), "iv": c.get("volatility", 0),
                    "volume": c.get("totalVolume", 0), "oi": c.get("openInterest", 0),
                }

        for strike_val, short_opt in call_strikes.items():
            d = short_opt["delta"]
            if not (call_delta_min <= d <= call_delta_max):
                continue
            if short_opt["mark"] <= 0:
                continue

            for width in widths:
                long_strike = strike_val + width
                if long_strike not in call_strikes:
                    continue
                long_opt = call_strikes[long_strike]
                credit = short_opt["mark"] - long_opt["mark"]
                if credit <= 0:
                    continue
                max_loss = width - credit
                if max_loss <= 0:
                    continue
                credit_pct = credit / width
                if credit_pct < min_credit_pct:
                    continue

                rr = credit / max_loss * 100
                pop = (1 - d) * 100
                net_theta = (short_opt["theta"] or 0) - (long_opt["theta"] or 0)
                net_vega = (short_opt["vega"] or 0) - (long_opt["vega"] or 0)
                breakeven = strike_val + credit

                suggestions.append({
                    "symbol": symbol,
                    "type": "CCS",
                    "trade_type": trade_type,
                    "expiration": exp_date_str,
                    "dte": dte,
                    "short_strike": strike_val,
                    "long_strike": long_strike,
                    "width": width,
                    "credit": round(credit, 2),
                    "max_loss": round(max_loss, 2),
                    "rr_pct": round(rr, 1),
                    "pop_pct": round(pop, 1),
                    "short_delta": round(d, 4),
                    "net_theta": round(net_theta, 3),
                    "net_vega": round(net_vega, 3),
                    "short_iv": round(short_opt["iv"], 1),
                    "breakeven": round(breakeven, 2),
                    "short_volume": short_opt["volume"],
                    "underlying_price": underlying_price,
                })

    return suggestions


def build_iron_condors(pcs_list, ccs_list, max_results=3):
    """Combine best PCS and CCS into Iron Condor suggestions."""
    ics = []
    # Match by symbol, expiration, width
    for pcs in pcs_list[:5]:
        for ccs in ccs_list[:5]:
            if (pcs["symbol"] == ccs["symbol"] and
                pcs["expiration"] == ccs["expiration"] and
                pcs["width"] == ccs["width"]):

                ic_credit = pcs["credit"] + ccs["credit"]
                ic_max_loss = max(pcs["width"], ccs["width"]) - ic_credit
                if ic_max_loss <= 0:
                    continue

                ics.append({
                    "symbol": pcs["symbol"],
                    "type": "IC",
                    "trade_type": pcs["trade_type"],
                    "expiration": pcs["expiration"],
                    "dte": pcs["dte"],
                    "put_short": pcs["short_strike"],
                    "put_long": pcs["long_strike"],
                    "call_short": ccs["short_strike"],
                    "call_long": ccs["long_strike"],
                    "width": pcs["width"],
                    "credit": round(ic_credit, 2),
                    "max_loss": round(ic_max_loss, 2),
                    "rr_pct": round(ic_credit / ic_max_loss * 100, 1),
                    "put_breakeven": round(pcs["short_strike"] - pcs["credit"], 2),
                    "call_breakeven": round(ccs["short_strike"] + ccs["credit"], 2),
                    "net_theta": round(pcs["net_theta"] + ccs["net_theta"], 3),
                    "net_delta": round(pcs["short_delta"] + ccs["short_delta"], 4),
                    "underlying_price": pcs["underlying_price"],
                })

    ics.sort(key=lambda x: x["rr_pct"], reverse=True)
    return ics[:max_results]

#############################################
# POSITION SIZING
#############################################

def calculate_position_size(max_loss_per_contract, multiplier=100):
    """Calculate max contracts based on account risk limits."""
    max_risk = Config.ACCOUNT_SIZE * Config.MAX_RISK_PCT
    loss_per = max_loss_per_contract * multiplier
    if loss_per <= 0:
        return 0
    return int(max_risk / loss_per)

#############################################
# OUTPUT FORMATTING
#############################################

def format_separator(char="=", length=72):
    return char * length


def format_scan_header(scan_time, vix_price, vix_regime):
    lines = [
        "",
        format_separator(),
        f"  OPTIONS SCANNER — {scan_time.strftime('%Y-%m-%d %H:%M:%S CT')}",
        format_separator(),
        f"  VIX: {vix_price:.2f}  |  Regime: {vix_regime['label']}",
        format_separator("-"),
    ]
    return "\n".join(lines)


def format_symbol_section(symbol, price, technicals):
    lines = [
        f"\n  {symbol}  ${price:,.2f}",
    ]
    if technicals:
        t = technicals
        sma_line = f"    SMA20: ${t['sma20']:,.2f}" if t['sma20'] else ""
        if t['sma50']:
            sma_line += f"  |  SMA50: ${t['sma50']:,.2f}"
        if t['sma200']:
            sma_line += f"  |  SMA200: ${t['sma200']:,.2f}"
        lines.append(sma_line)
        lines.append(f"    Trend: {t['trend']}  |  ATR14: ${t['atr14']:,.2f}  |  20d Range: ${t['recent_low']:,.2f} - ${t['recent_high']:,.2f}")
    return "\n".join(lines)


def format_spread_suggestion(s, rank):
    """Format a single spread suggestion."""
    mult = 100
    if s["type"] == "IC":
        return (
            f"    #{rank}  {s['type']}  {s['put_short']}/{s['put_long']}p — {s['call_short']}/{s['call_long']}c"
            f"  |  Exp: {s['expiration']} ({s['dte']}d)"
            f"\n         Credit: ${s['credit']:.2f} (${s['credit']*mult:,.0f}/ct)"
            f"  |  MaxLoss: ${s['max_loss']:.2f} (${s['max_loss']*mult:,.0f}/ct)"
            f"  |  R:R: {s['rr_pct']:.1f}%"
            f"\n         Bkeven: ${s['put_breakeven']:,.2f} / ${s['call_breakeven']:,.2f}"
            f"  |  Theta: ${s['net_theta']:.3f}/day"
            f"  |  Max Cts: {calculate_position_size(s['max_loss'])}"
        )
    else:
        side = "PUT" if s["type"] == "PCS" else "CALL"
        return (
            f"    #{rank}  {s['type']}  {s['short_strike']}/{s['long_strike']} ({s['width']}-wide {side})"
            f"  |  Exp: {s['expiration']} ({s['dte']}d)"
            f"\n         Credit: ${s['credit']:.2f} (${s['credit']*mult:,.0f}/ct)"
            f"  |  MaxLoss: ${s['max_loss']:.2f} (${s['max_loss']*mult:,.0f}/ct)"
            f"  |  R:R: {s['rr_pct']:.1f}%"
            f"  |  PoP: {s['pop_pct']:.1f}%"
            f"\n         Delta: {s['short_delta']:.4f}"
            f"  |  Theta: ${s['net_theta']:.3f}/day"
            f"  |  IV: {s['short_iv']:.1f}%"
            f"  |  Bkeven: ${s['breakeven']:,.2f}"
            f"  |  Max Cts: {calculate_position_size(s['max_loss'])}"
        )

#############################################
# MAIN SCAN LOGIC
#############################################

def run_scan(client):
    """Execute a single market scan cycle."""
    # Live-read the watchlist each cycle so spreadsheet edits are picked up.
    Config.SYMBOLS = get_scan_symbols()
    scan_time = datetime.now(Config.TZ)
    today = scan_time.date()
    today_str = today.strftime("%Y-%m-%d")

    # Pull VIX
    quotes = fetch_quotes(client, Config.SYMBOLS + [Config.VIX_SYMBOL])
    if not quotes:
        log.error("Failed to fetch quotes. Skipping scan.")
        return

    vix_price = 0
    symbol_prices = {}
    for sym, data in quotes.items():
        q = data.get("quote", data.get("reference", {}))
        last = q.get("lastPrice", q.get("lastPrice", 0))
        if sym == Config.VIX_SYMBOL:
            vix_price = last
        else:
            symbol_prices[sym] = last

    if vix_price == 0:
        vix_price = 20.0  # default
        log.warning("Could not fetch VIX, defaulting to 20")

    vix_regime = assess_vix_regime(vix_price)

    # Print header
    header = format_scan_header(scan_time, vix_price, vix_regime)
    log.info(header)

    all_suggestions = {"0-DTE": [], "SWING": []}

    for symbol in Config.SYMBOLS:
        price = symbol_prices.get(symbol, 0)
        if price == 0:
            log.warning(f"  No price for {symbol}, skipping")
            continue

        # Technical analysis
        hist = fetch_price_history(client, symbol)
        technicals = calculate_technicals(hist) if hist else None

        section = format_symbol_section(symbol, price, technicals)
        log.info(section)

        widths = Config.SPREAD_WIDTHS.get(symbol, [5])

        # 0-DTE scan — today's expiration
        log.info(f"\n    --- 0-DTE Scan ---")
        chain_0dte = fetch_option_chain(client, symbol, from_date=today, to_date=today)

        if chain_0dte and chain_0dte.get("status") != "FAILED":
            zero_dte = parse_chain_to_spreads(
                chain_0dte, symbol, 0, 0,
                Config.ZERO_DTE_PUT_DELTA_MIN, Config.ZERO_DTE_PUT_DELTA_MAX,
                Config.ZERO_DTE_CALL_DELTA_MIN, Config.ZERO_DTE_CALL_DELTA_MAX,
                Config.ZERO_DTE_MIN_CREDIT_PCT, widths, "0-DTE"
            )

            pcs_0 = sorted([s for s in zero_dte if s["type"] == "PCS"], key=lambda x: x["rr_pct"], reverse=True)[:3]
            ccs_0 = sorted([s for s in zero_dte if s["type"] == "CCS"], key=lambda x: x["rr_pct"], reverse=True)[:3]
            ic_0 = build_iron_condors(pcs_0, ccs_0, max_results=2)

            if pcs_0:
                log.info("    Put credit spreads (0-DTE):")
                for i, s in enumerate(pcs_0, 1):
                    log.info(format_spread_suggestion(s, i))
            if ccs_0:
                log.info("    Call credit spreads (0-DTE):")
                for i, s in enumerate(ccs_0, 1):
                    log.info(format_spread_suggestion(s, i))
            if ic_0:
                log.info("    Iron condors (0-DTE):")
                for i, s in enumerate(ic_0, 1):
                    log.info(format_spread_suggestion(s, i))

            if not pcs_0 and not ccs_0:
                log.info("    No 0-DTE candidates meeting criteria.")

            all_suggestions["0-DTE"].extend(pcs_0 + ccs_0 + ic_0)
        else:
            log.info("    No 0-DTE expiration available for this symbol today.")

        # Swing scan — 1-15 DTE
        log.info(f"\n    --- Swing Scan (1-15 DTE) ---")
        swing_from = today + timedelta(days=Config.SWING_DTE_MIN)
        swing_to = today + timedelta(days=Config.SWING_DTE_MAX)
        chain_swing = fetch_option_chain(client, symbol, from_date=swing_from, to_date=swing_to)

        if chain_swing and chain_swing.get("status") != "FAILED":
            swing = parse_chain_to_spreads(
                chain_swing, symbol, Config.SWING_DTE_MIN, Config.SWING_DTE_MAX,
                Config.SWING_PUT_DELTA_MIN, Config.SWING_PUT_DELTA_MAX,
                Config.SWING_CALL_DELTA_MIN, Config.SWING_CALL_DELTA_MAX,
                Config.SWING_MIN_CREDIT_PCT, widths, "SWING"
            )

            pcs_s = sorted([s for s in swing if s["type"] == "PCS"], key=lambda x: x["rr_pct"], reverse=True)[:3]
            ccs_s = sorted([s for s in swing if s["type"] == "CCS"], key=lambda x: x["rr_pct"], reverse=True)[:3]
            ic_s = build_iron_condors(pcs_s, ccs_s, max_results=2)

            if pcs_s:
                log.info("    Put credit spreads (swing):")
                for i, s in enumerate(pcs_s, 1):
                    log.info(format_spread_suggestion(s, i))
            if ccs_s:
                log.info("    Call credit spreads (swing):")
                for i, s in enumerate(ccs_s, 1):
                    log.info(format_spread_suggestion(s, i))
            if ic_s:
                log.info("    Iron condors (swing):")
                for i, s in enumerate(ic_s, 1):
                    log.info(format_spread_suggestion(s, i))

            if not pcs_s and not ccs_s:
                log.info("    No swing candidates meeting criteria.")

            all_suggestions["SWING"].extend(pcs_s + ccs_s + ic_s)
        else:
            log.info("    No swing expirations available.")

    # Summary
    total_0dte = len(all_suggestions["0-DTE"])
    total_swing = len(all_suggestions["SWING"])
    log.info(f"\n{format_separator('-')}")
    log.info(f"  SCAN COMPLETE  |  0-DTE: {total_0dte} suggestions  |  Swing: {total_swing} suggestions")
    log.info(format_separator())

    # Daily potential-trade journal: fires once per day inside the ~13:00 CT
    # entry window; a no-op otherwise. Never let it break the scan.
    try:
        written = daily_trade_log.capture_if_due(scan_time)
        if written:
            log.info(f"  Potential-trade journal: captured {len(written)} candidate(s).")
    except Exception as e:
        log.error(f"  daily_trade_log.capture_if_due failed (non-fatal): {e}")

    return all_suggestions

#############################################
# MAIN LOOP
#############################################

def main():
    single_run = "--once" in sys.argv
    test_only = "--test" in sys.argv

    log.info("=" * 72)
    log.info("  OPTIONS SCANNER v1.0.0")
    log.info(f"  Symbols: {', '.join(Config.SYMBOLS)}")
    log.info(f"  Scan interval: {Config.SCAN_INTERVAL_MINUTES} min")
    log.info(f"  Market hours: {Config.SCAN_START_HOUR}:{Config.SCAN_START_MIN:02d} - {Config.SCAN_END_HOUR}:{Config.SCAN_END_MIN:02d} CT")
    log.info(f"  Account size: ${Config.ACCOUNT_SIZE:,.0f}  |  Max risk/trade: {Config.MAX_RISK_PCT*100:.0f}%")
    log.info("=" * 72)

    # Initialize Schwab client
    client = get_schwab_client()
    if not client:
        sys.exit(1)

    # Test connection
    log.info("  Testing Schwab connection...")
    try:
        r = client.get_quote("SPY")
        if r.status_code == 200:
            d = r.json()
            price = d.get("SPY", {}).get("quote", {}).get("lastPrice", "?")
            log.info(f"  Connection OK — SPY=${price}")
        else:
            log.error(f"  Connection test failed: {r.status_code}")
            sys.exit(1)
    except Exception as e:
        log.error(f"  Connection test failed: {e}")
        sys.exit(1)

    if test_only:
        log.info("  Test complete.")
        return

    if single_run:
        log.info("\n  Running single scan...")
        run_scan(client)
        return

    # Continuous loop
    log.info(f"\n  Scanner running. Press Ctrl+C to stop.\n")

    while True:
        try:
            now = datetime.now(Config.TZ)

            if is_market_hours(now):
                log.info(f"\n  Initiating scan at {now.strftime('%H:%M:%S CT')}...")
                try:
                    run_scan(client)
                except Exception as e:
                    log.error(f"  Scan failed: {e}")
                    # Try to refresh client
                    client = get_schwab_client()
                    if not client:
                        log.error("  Could not refresh client. Waiting for next scan.")

                # Wait until next scan interval
                next_t = next_scan_time()
                wait_secs = max(0, (next_t - datetime.now(Config.TZ)).total_seconds())
                log.info(f"  Next scan at {next_t.strftime('%H:%M CT')} (waiting {wait_secs:.0f}s)")
                time.sleep(wait_secs)
            else:
                if not is_trading_day(now):
                    log.info(f"  Not a trading day. Sleeping until tomorrow...")
                    # Sleep until tomorrow 7:55 AM CT
                    tomorrow = now + timedelta(days=1)
                    wake = tomorrow.replace(hour=7, minute=55, second=0)
                    sleep_secs = (wake - now).total_seconds()
                    if sleep_secs > 0:
                        time.sleep(min(sleep_secs, 3600))  # Check every hour max
                elif now.hour < Config.SCAN_START_HOUR:
                    open_time = now.replace(hour=Config.SCAN_START_HOUR, minute=0, second=0)
                    wait = (open_time - now).total_seconds()
                    log.info(f"  Pre-market. Scan starts at {Config.SCAN_START_HOUR}:00 CT ({wait/60:.0f} min)")
                    time.sleep(min(wait, 300))
                else:
                    log.info(f"  After hours. Done for today.")
                    # Sleep until tomorrow
                    tomorrow = now + timedelta(days=1)
                    wake = tomorrow.replace(hour=7, minute=55, second=0)
                    sleep_secs = (wake - now).total_seconds()
                    if sleep_secs > 0:
                        time.sleep(min(sleep_secs, 3600))

        except KeyboardInterrupt:
            log.info("\n  Scanner stopped by user.")
            break
        except Exception as e:
            log.error(f"  Unexpected error: {e}")
            time.sleep(60)  # Wait 1 min before retry


if __name__ == "__main__":
    main()
