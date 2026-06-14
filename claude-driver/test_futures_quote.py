"""
test_futures_quote.py - Verify Schwab proxy supports futures symbols
Run this from claude-driver to test before going live.

Usage:
    python test_futures_quote.py
"""

import json
import requests

SCHWAB_PROXY_URL = "http://127.0.0.1:8100"

# Symbol formats to test — Schwab may accept one or more of these
SYMBOLS_TO_TEST = [
    "/MES",        # Standard continuous front month (what we use)
    "/MESU25",     # Explicit September 2025 contract
    "/ES",         # Full-size E-mini (to confirm futures work at all)
    "/ESU25",      # Explicit ES contract
    "SPY",         # Equity control — should definitely work
]

def test_quote(symbol: str) -> dict:
    try:
        resp = requests.get(
            f"{SCHWAB_PROXY_URL}/quotes",
            params={"symbols": symbol},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            quote = data.get(symbol, {})
            if quote:
                return {
                    "symbol":     symbol,
                    "status":     "OK",
                    "lastPrice":  quote.get("lastPrice") or quote.get("mark"),
                    "closePrice": quote.get("closePrice") or quote.get("settlePrice"),
                    "netPctChg":  quote.get("netPercentChangeInDouble"),
                    "assetType":  quote.get("assetMainType") or quote.get("assetType"),
                    "description":quote.get("description", ""),
                }
            else:
                return {"symbol": symbol, "status": "EMPTY — symbol not found in response",
                        "raw_keys": list(data.keys())}
        else:
            return {"symbol": symbol, "status": f"HTTP {resp.status_code}", "body": resp.text[:200]}
    except Exception as exc:
        return {"symbol": symbol, "status": f"ERROR: {exc}"}


if __name__ == "__main__":
    print(f"\nTesting Schwab proxy at {SCHWAB_PROXY_URL}\n")
    print(f"{'Symbol':<12} {'Status':<8} {'Last':>10} {'Close':>10} {'Chg%':>8}  Asset / Description")
    print("-" * 90)

    results = []
    for sym in SYMBOLS_TO_TEST:
        r = test_quote(sym)
        results.append(r)
        if r["status"] == "OK":
            print(f"{r['symbol']:<12} {'OK':<8} "
                  f"{str(r['lastPrice'] or '—'):>10} "
                  f"{str(r['closePrice'] or '—'):>10} "
                  f"{str(r['netPctChg'] or '—'):>8}  "
                  f"{r['assetType'] or ''} {r['description'][:40]}")
        else:
            print(f"{r['symbol']:<12} {r['status']}")

    print("\n--- Full JSON for futures symbols ---")
    for r in results:
        if r["symbol"] not in ("SPY",) and r["status"] == "OK":
            try:
                resp = requests.get(f"{SCHWAB_PROXY_URL}/quotes",
                                    params={"symbols": r["symbol"]}, timeout=10)
                raw = resp.json().get(r["symbol"], {})
                print(f"\n{r['symbol']}:")
                print(json.dumps(raw, indent=2))
            except Exception as e:
                print(f"  {e}")

    print("\nDone. Share output above and I'll update config accordingly.")
