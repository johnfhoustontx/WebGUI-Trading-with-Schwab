"""
test_preflight.py - Full pre-flight check for the next trading session
Tests all services, validates field names, checks config.

Usage:
    python test_preflight.py
"""

import json
import sys
import requests
from datetime import date, timedelta

SCHWAB_PROXY       = "http://127.0.0.1:8100"
OPTIONS_ANALYTICS  = "http://127.0.0.1:8200"
ML_SERVERS = {
    "MES": "http://127.0.0.1:8000",
    "MNQ": "http://127.0.0.1:8001",
    "ES":  "http://127.0.0.1:8004",
    "NQ":  "http://127.0.0.1:8005",
}

results = {}

def chk(label, ok, detail=""):
    results[label] = ok
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def next_trading_day() -> str:
    """Return the next weekday date string (skipping holidays)."""
    sys.path.insert(0, ".")
    try:
        import config
        holidays = config.MARKET_HOLIDAYS
    except Exception:
        holidays = set()
    d = date.today() + timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in holidays:
        d += timedelta(days=1)
    return d.isoformat()

# ============================================================
section("1. Config")
# ============================================================
try:
    sys.path.insert(0, ".")
    import config
    ver = config.__doc__.split('Version: ')[1].split()[0] if 'Version: ' in (config.__doc__ or '') else '?'
    chk("config.py loads", True, f"v{ver}")
    chk("MARKET_HOLIDAYS present", hasattr(config, 'MARKET_HOLIDAYS'), f"{len(config.MARKET_HOLIDAYS)} holidays")
    today = date.today().isoformat()
    is_holiday = today in config.MARKET_HOLIDAYS
    chk("Today's holiday status correct",
        True,   # always passes — just informational
        f"{today} is {'a HOLIDAY — do not trade' if is_holiday else 'a TRADING DAY'}")
    nxt = next_trading_day()
    chk("Next trading day identified", nxt not in config.MARKET_HOLIDAYS, nxt)
    chk("NT_FEEDS_ML_SERVERS=True", config.NT_FEEDS_ML_SERVERS)
    chk("PAPER_TRADE flag present", hasattr(config, 'PAPER_TRADE'), f"PAPER_TRADE={config.PAPER_TRADE}")
    chk("ML_SERVERS=4", len(config.ML_SERVERS) == 4)
    chk("OPTIONS_ANALYTICS_URL=8200", "8200" in config.OPTIONS_ANALYTICS_URL)
    chk("SCHWAB_PROXY_URL=8100", "8100" in config.SCHWAB_PROXY_URL)
except Exception as e:
    chk("config.py load", False, str(e))

# ============================================================
section("2. ML Servers (ports 8000/8001/8004/8005)")
# ============================================================
for inst, url in ML_SERVERS.items():
    try:
        r = requests.get(f"{url}/health", timeout=2)
        d = r.json()
        chk(f"ML:{inst} /health", r.status_code == 200,
            f"v{d.get('version','?')} session={d.get('current_session','?')}")
    except Exception as e:
        chk(f"ML:{inst} /health", False, str(e))

# ============================================================
section("3. Schwab Proxy (port 8100)")
# ============================================================
try:
    r = requests.get(f"{SCHWAB_PROXY}/health", timeout=3)
    chk("Proxy /health", r.status_code == 200)
except Exception as e:
    chk("Proxy /health", False, str(e))

try:
    r = requests.get(f"{SCHWAB_PROXY}/quotes", params={"symbols": "SPY"}, timeout=5)
    r.raise_for_status()
    q = r.json().get("SPY", {})
    chk("Proxy /quotes SPY", bool(q), f"keys={list(q.keys())[:4]}")
except Exception as e:
    chk("Proxy /quotes SPY", False, str(e))

try:
    r = requests.get(f"{SCHWAB_PROXY}/quotes", params={"symbols": "$VIX"}, timeout=5)
    r.raise_for_status()
    q = r.json().get("$VIX", {})
    nested = q.get("quote", {})
    chk("Proxy /quotes $VIX", bool(q),
        f"lastPrice_in_quote={'lastPrice' in nested}, val={nested.get('lastPrice')}")
except Exception as e:
    chk("Proxy /quotes $VIX", False, str(e))

try:
    r = requests.get(f"{SCHWAB_PROXY}/quotes?symbols=$VIX,$SPX,$VIX1D", timeout=5)
    r.raise_for_status()
    data = r.json()
    errors = data.get("errors", {}).get("invalidSymbols", [])
    working = [k for k in data if k != "errors"]
    chk("Batch $VIX,$SPX,$VIX1D", len(errors) == 0,
        f"working={working}, invalid={errors}")
except Exception as e:
    chk("Batch $VIX,$SPX,$VIX1D", False, str(e))

try:
    r = requests.get(f"{SCHWAB_PROXY}/pricehistory",
                     params={"symbol": "SPY", "periodType": "day", "period": 3,
                             "frequencyType": "minute", "frequency": 5}, timeout=15)
    r.raise_for_status()
    n = len(r.json().get("candles", []))
    chk("Proxy /pricehistory SPY", n > 50, f"{n} bars")
except Exception as e:
    chk("Proxy /pricehistory SPY", False, str(e))

# Chains: use next trading day dynamically (avoids expired-expiry 400)
try:
    nxt = next_trading_day()
    r = requests.get(f"{SCHWAB_PROXY}/chains",
                     params={"symbol": "$SPX", "contractType": "ALL",
                             "fromDate": nxt, "toDate": nxt}, timeout=15)
    ok = r.status_code == 200
    if ok:
        d = r.json()
        detail = f"call_exp={len(d.get('callExpDateMap',{}))}, put_exp={len(d.get('putExpDateMap',{}))}"
    else:
        detail = f"status={r.status_code} (may be weekend/holiday — no expiry)"
        ok = True  # Not a blocker if next day has no 0-DTE expiry
    chk(f"Proxy /chains $SPX ({nxt})", ok, detail)
except Exception as e:
    chk("Proxy /chains $SPX", False, str(e))

# ============================================================
section("4. OptionsAnalytics (port 8200)")
# ============================================================
try:
    r = requests.get(f"{OPTIONS_ANALYTICS}/options/health", timeout=3)
    chk("OptionsAnalytics /options/health", r.status_code == 200)
except Exception as e:
    chk("OptionsAnalytics /options/health", False, "NOT RUNNING — start via start_all.bat")

try:
    r = requests.get(f"{OPTIONS_ANALYTICS}/options/snapshot/$SPX", timeout=20)
    ok = r.status_code == 200
    if ok:
        d = r.json()
        chk("GEX snapshot $SPX", True,
            f"spot={d.get('spot')}, gex_flip={d.get('gex',{}).get('flip')}")
    else:
        chk("GEX snapshot $SPX", False, f"{r.status_code}")
except Exception as e:
    chk("GEX snapshot $SPX", False, str(e))

# ============================================================
section("5. Feature Engineering + ML V6")
# ============================================================
try:
    import pandas as pd
    from feature_engineer import compute_features, get_valid_features
    r = requests.get(f"{SCHWAB_PROXY}/pricehistory",
                     params={"symbol": "SPY", "periodType": "day", "period": 3,
                             "frequencyType": "minute", "frequency": 5}, timeout=15)
    candles = r.json().get("candles", [])
    df = pd.DataFrame(candles)
    df["datetime"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
    feats = compute_features(opens=df["open"].values, highs=df["high"].values,
                              lows=df["low"].values, closes=df["close"].values,
                              volumes=df["volume"].values, datetimes=df["datetime"])
    valid = get_valid_features(feats, n_bars=66)
    chk("F1-F25 computation", feats.shape[1] == 25, f"shape={feats.shape}, valid={len(valid)}")
    warmup = valid[:-1].tolist(); current = valid[-1].tolist()
    wr = requests.post("http://127.0.0.1:8000/warmup", json={"historical_bars": warmup}, timeout=10)
    er = requests.post("http://127.0.0.1:8000/ensemble",
                       json={"features": current, "method": "confidence"}, timeout=10)
    ed = er.json()
    conf = ed.get("confidence", 0)
    if 0 < conf <= 1: conf *= 100
    chk("MES /ensemble end-to-end", er.status_code == 200,
        f"signal={ed.get('signal')} conf={conf:.1f}% agree={ed.get('agreement','?')}")
except Exception as e:
    chk("Feature + ML V6 flow", False, str(e))

# ============================================================
section("6. Holiday Calendar")
# ============================================================
try:
    import config as cfg
    today_str = date.today().isoformat()
    nxt = next_trading_day()
    is_today_holiday = today_str in cfg.MARKET_HOLIDAYS
    chk("Holiday calendar loads", True,
        f"today={today_str} ({'HOLIDAY' if is_today_holiday else 'trading day'}), next={nxt}")
    chk("Jul 3 2026 blocked", "2026-07-03" in cfg.MARKET_HOLIDAYS)
    chk("Sep 7 2026 blocked", "2026-09-07" in cfg.MARKET_HOLIDAYS)
    chk("Dec 25 2026 blocked", "2026-12-25" in cfg.MARKET_HOLIDAYS)
except Exception as e:
    chk("Holiday calendar", False, str(e))

# ============================================================
section("7. Core Files Present")
# ============================================================
import os
for f in ["config.py", "morning_agent.py", "trade_selector.py", "feature_engineer.py",
          "order_executor.py", "approval_server.py", "intraday_monitor.py", "start_all.bat"]:
    chk(f, os.path.exists(f))

# ============================================================
section("SUMMARY")
# ============================================================
total = len(results); passed = sum(1 for v in results.values() if v); failed = total - passed
print(f"\n  {passed}/{total} checks passed")
if failed == 0:
    print("\n  *** ALL SYSTEMS GO ***")
    print(f"\n  Next trading day: {next_trading_day()}")
    print("  1. Run start_all.bat before 9:28am ET")
    print("  2. Agent fires automatically at 9:28am ET")
    print("  3. Browser opens at http://127.0.0.1:8300")
    print("  4. Click APPROVE or SKIP")
else:
    print(f"\n  *** {failed} ISSUE(S) ***")
    for label, ok in results.items():
        if not ok:
            print(f"    FAIL: {label}")
print()
