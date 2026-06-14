"""
Schwab Index Symbol Test - Market Internals
Tests which symbols return data via Schwab REST API (proxy).
Run:  python test_add_symbol.py

Results (2026-04-07):
  $ADD       Empty   — Use $ADVN/$DECN instead
  $ADVN      OK      — NYSE Advancing Issues (e.g., 1483)
  $DECN      OK      — NYSE Declining Issues (e.g., 1294)
  $TICK      OK      — NYSE Tick Index (e.g., -24)
  $TRIN      OK      — Arms Index (e.g., 0.82)
  $UVOL/$DVOL OVERFLOW — Returns garbage (92233720368), unusable
  $VIX       OK      — CBOE Volatility Index
  $VVIX      OK      — CBOE VIX of VIX
  $SPX       OK      — S&P 500 Index
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import SCHWAB_PROXY, SHARED  # noqa: E402
sys.path.insert(0, str(SCHWAB_PROXY))
sys.path.insert(0, str(SHARED / "analysis_lib"))

from proxy_client import SchwabProxyClient, proxy_available

if not proxy_available():
    print("ERROR: Schwab Proxy not running on port 8100")
    sys.exit(1)

client = SchwabProxyClient()

symbols = [
    # Market Internals
    ('$ADVN',  'NYSE Advancing Issues'),
    ('$DECN',  'NYSE Declining Issues'),
    ('$TICK',  'NYSE Tick Index'),
    ('$TRIN',  'Arms Index (TRIN)'),
    # Known working indices
    ('$VIX',   'CBOE VIX'),
    ('$VVIX',  'CBOE VVIX'),
    ('$SPX',   'S&P 500'),
    # Known NOT working (empty/overflow)
    ('$ADD',   'NYSE A/D (not available)'),
]

print(f"{'Symbol':<10} {'Last':>10} {'Change':>10}  {'Description'}")
print("-" * 65)

for sym, desc in symbols:
    try:
        q = client.get_quote(sym)
        if q and q.get('last', 0) != 0:
            last = q['last']
            chg = q.get('change', 0)
            # Flag overflow values
            if abs(last) > 1e10:
                print(f"{sym:<10} {'OVERFLOW':>10} {'':>10}  {desc}")
            else:
                print(f"{sym:<10} {last:>10.2f} {chg:>+10.2f}  {desc}")
        else:
            print(f"{sym:<10} {'—':>10} {'':>10}  {desc} (empty)")
    except Exception as e:
        print(f"{sym:<10} {'ERROR':>10} {'':>10}  {desc}: {e}")

# Compute derived values
print("\n--- Derived ---")
try:
    advn = client.get_quote('$ADVN')
    decn = client.get_quote('$DECN')
    if advn and decn:
        a = advn['last']
        d = decn['last']
        net = a - d
        ratio = a / d if d > 0 else 0
        print(f"NYSE Net A/D:  {net:+.0f}  ({'Advancing' if net > 0 else 'Declining'})")
        print(f"NYSE A/D Ratio: {ratio:.2f}")
except Exception as e:
    print(f"Error: {e}")
