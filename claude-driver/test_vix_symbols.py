"""
test_vix_symbols.py - Find correct VIX/SPX symbol format for Schwab proxy
Tests multiple symbol variations to find which ones return data.

Usage: python test_vix_symbols.py
"""
import requests, json

PROXY = "http://127.0.0.1:8100"

candidates = [
    # VIX variations
    "VIX", "$VIX", "$VIX.X", "VIX.X",
    # SPX variations
    "SPX", "$SPX", "$SPX.X", "SPX.X",
    # VIX1D variations
    "$VIX1D", "VIX1D", "$VIX1D.X",
]

print("\nTesting symbol formats against Schwab proxy /quotes\n" + "="*55)

# Test each individually to get clean response per symbol
working = {}
for sym in candidates:
    try:
        r = requests.get(f"{PROXY}/quotes", params={"symbols": sym}, timeout=5)
        data = r.json()
        result = data.get(sym, {})
        has_data = bool(result)
        last = result.get("lastPrice") or result.get("mark") or result.get("closePrice")
        keys = list(result.keys())[:5] if result else []
        status = "DATA" if has_data else "EMPTY"
        print(f"  {sym:<15} {status}  lastPrice={last}  keys={keys}")
        if has_data:
            working[sym] = result
    except Exception as e:
        print(f"  {sym:<15} ERROR: {e}")

# Also try batch to see exact response keys
print("\n" + "="*55)
print("Batch test (what the morning agent actually calls):")
try:
    r = requests.get(f"{PROXY}/quotes", params={"symbols": "VIX,SPX,$VIX1D"}, timeout=5)
    data = r.json()
    print(f"  Response keys: {list(data.keys())}")
    for k, v in data.items():
        print(f"  {k}: {json.dumps(v)[:120] if v else 'empty'}")
except Exception as e:
    print(f"  ERROR: {e}")

print("\n" + "="*55)
if working:
    print(f"Working symbols: {list(working.keys())}")
    print("\nFor morning_agent.py fetch_market_conditions(), use:")
    for sym in working:
        if 'vix' in sym.lower() and '1d' not in sym.lower():
            print(f"  VIX symbol:  \"{sym}\"")
        if 'spx' in sym.lower():
            print(f"  SPX symbol:  \"{sym}\"")
        if '1d' in sym.lower():
            print(f"  VIX1D symbol: \"{sym}\"")
else:
    print("No symbols returned data (market closed - expected on Memorial Day)")
    print("Run this again Tuesday morning to confirm working symbols")
