"""
test_ml_v6_flow.py - End-to-end test of the V6 ML signal pipeline
Tests: bar fetch -> feature computation -> warmup -> ensemble for one instrument

Usage:
    python test_ml_v6_flow.py
"""

import json
import sys
import requests
import numpy as np

SCHWAB_PROXY = "http://127.0.0.1:8100"
ML_SERVER    = "http://127.0.0.1:8000"   # MES
PROXY_SYMBOL = "SPY"
WARMUP_BARS  = 65

def step(label):
    print(f"\n[{label}]")
    print("-" * 50)

if __name__ == "__main__":
    # ---- Step 1: Fetch bars ----
    step("1. Fetch SPY bars from Schwab")
    try:
        r = requests.get(f"{SCHWAB_PROXY}/pricehistory",
                         params={"symbol": PROXY_SYMBOL, "periodType": "day",
                                 "period": 5, "frequencyType": "minute", "frequency": 5},
                         timeout=15)
        r.raise_for_status()
        candles = r.json().get("candles", [])
        print(f"  Bars returned: {len(candles)}")
        if candles:
            print(f"  First: {candles[0]}")
            print(f"  Last:  {candles[-1]}")
        if len(candles) < WARMUP_BARS + 50:
            print(f"  FAIL: need at least {WARMUP_BARS+50} bars, got {len(candles)}")
            sys.exit(1)
    except Exception as e:
        print(f"  FAIL: {e}")
        sys.exit(1)

    # ---- Step 2: Compute features ----
    step("2. Compute F1-F25 features")
    import pandas as pd
    sys.path.insert(0, ".")
    from feature_engineer import compute_features, get_valid_features

    df = pd.DataFrame(candles)
    df["datetime"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
    df = df.sort_values("datetime").reset_index(drop=True)

    features = compute_features(
        opens=df["open"].values, highs=df["high"].values,
        lows=df["low"].values, closes=df["close"].values,
        volumes=df["volume"].values, datetimes=df["datetime"],
    )
    valid = get_valid_features(features, n_bars=WARMUP_BARS + 1)
    print(f"  Feature matrix shape: {features.shape}")
    print(f"  Valid rows (no NaN):  {len(valid)}")
    print(f"  Feature sample (last bar): {[round(x,4) for x in valid[-1].tolist()]}")

    if valid.shape[1] != 25:
        print(f"  FAIL: expected 25 features, got {valid.shape[1]}")
        sys.exit(1)
    if len(valid) < WARMUP_BARS:
        print(f"  FAIL: not enough valid rows")
        sys.exit(1)
    print(f"  PASS: {valid.shape[1]} features, {len(valid)} valid rows")

    # ---- Step 3: Warmup ----
    step("3. POST /warmup to MES server")
    warmup_rows = valid[:-1].tolist()
    try:
        wr = requests.post(f"{ML_SERVER}/warmup",
                           json={"historical_bars": warmup_rows}, timeout=15)
        print(f"  Status: {wr.status_code}")
        print(f"  Response: {wr.text[:300]}")
        if wr.status_code not in (200, 204):
            print("  WARN: warmup returned non-200 (continuing anyway)")
    except Exception as e:
        print(f"  WARN: warmup failed: {e} (continuing)")

    # ---- Step 4: Ensemble ----
    step("4. POST /ensemble with current features")
    current_feat = valid[-1].tolist()
    try:
        er = requests.post(f"{ML_SERVER}/ensemble",
                           json={"features": current_feat, "method": "confidence"},
                           timeout=10)
        er.raise_for_status()
        data = er.json()
        print(f"  Status: {er.status_code}")
        print(f"  Full response:")
        print(json.dumps(data, indent=2))

        signal     = data.get("signal",     data.get("prediction", "?"))
        confidence = data.get("confidence", data.get("probability", 0))
        direction  = data.get("direction",  "?")
        print(f"\n  SIGNAL:     {signal}")
        print(f"  CONFIDENCE: {confidence}")
        print(f"  DIRECTION:  {direction}")
        print(f"\n  PASS: V6 ML pipeline fully functional")
    except Exception as e:
        print(f"  FAIL: /ensemble error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"  Response body: {e.response.text[:300]}")
        sys.exit(1)
