"""
validate_new_symbols - Pre-implementation symbol availability check
Version: 1.0.0
Last Updated: 2026-05-15

Version 1.0.0 Changes:
- Validate $VIX1D, $VIX9D, HYG, IEI via Schwab proxy
- Try alternate variants for VIX1D / VIX9D if primary fails
- PASS/FAIL report with the working symbol per metric

Usage:
    py -3.11 validate_new_symbols.py
    Exit code 0 = all required symbols available.
    Exit code 1 = at least one required symbol unavailable.
"""

import sys
import pathlib
import urllib.parse
import urllib.request
import json

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import PROXY_URL  # noqa: E402

PROXY = PROXY_URL

# Each metric maps to an ordered list of variants to try. First positive
# quote wins; the symbol is reported for use in the dashboard.
SYMBOL_GROUPS = {
    "VIX1D":  ["$VIX1D", "VIX1D", "VIX1D.X", "$VIX1D.X"],
    "VIX9D":  ["$VIX9D", "VIX9D", "VIX9D.X", "$VIX9D.X"],
    "HYG":    ["HYG"],
    "IEI":    ["IEI"],
}

# Required: failure to find any of these means stop and report.
REQUIRED = {"VIX1D", "VIX9D", "HYG", "IEI"}


def fetch_quote(symbol: str):
    """Hit /quote?symbol=… and return the parsed dict (or None)."""
    url = f"{PROXY}/quote?symbol={urllib.parse.quote(symbol)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            if resp.status != 200:
                return None, f"HTTP {resp.status}"
            data = json.loads(resp.read())
            return data, None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def extract_last(quote_blob) -> float:
    """The proxy returns either {SYMBOL: {quote: {...}}} or {last:...}.
    Walk both shapes and return the last price (or 0)."""
    if not isinstance(quote_blob, dict):
        return 0
    if "last" in quote_blob:
        return float(quote_blob.get("last") or 0)
    for _sym, info in quote_blob.items():
        if isinstance(info, dict):
            q = info.get("quote", info.get("reference", info))
            for key in ("lastPrice", "mark", "closePrice",
                        "regularMarketLastPrice"):
                v = q.get(key)
                if v:
                    return float(v)
    return 0


def validate_group(metric: str, variants):
    """Try variants in order. Return (working_symbol, last_value, error)."""
    last_err = None
    for sym in variants:
        data, err = fetch_quote(sym)
        if err:
            last_err = err
            continue
        last = extract_last(data)
        if last > 0:
            return sym, last, None
        last_err = f"empty response (last=0)"
    return None, 0, last_err


def main():
    print(f"Validating new symbols against proxy: {PROXY}")
    print("=" * 70)
    results = {}
    all_pass = True
    for metric, variants in SYMBOL_GROUPS.items():
        sym, last, err = validate_group(metric, variants)
        status = "PASS" if sym else "FAIL"
        results[metric] = {"symbol": sym, "last": last, "error": err}
        line = f"  {status:4s}  {metric:6s}"
        if sym:
            line += f"  via '{sym}'   last={last}"
        else:
            line += f"  no variant returned data   (last err: {err})"
        print(line)
        if metric in REQUIRED and not sym:
            all_pass = False
    print("=" * 70)
    if all_pass:
        print("RESULT: PASS — all required symbols available.")
        print("Resolved symbol map:")
        for m, r in results.items():
            print(f"  {m}: {r['symbol']}")
        return 0
    print("RESULT: FAIL — at least one required symbol unavailable.")
    print(f"If auth errors, re-auth at {PROXY}/auth")
    print("Do NOT silently fall back — report and stop.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
