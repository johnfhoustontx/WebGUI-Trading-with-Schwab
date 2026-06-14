"""
test_es_mes_compare.py - Compare ES vs MES ensemble vote breakdown
Diagnoses why ES returns 0.0% confidence while MES returns 41.5%

Usage:
    python test_es_mes_compare.py
"""
import json, sys, requests
import pandas as pd
sys.path.insert(0, ".")
from feature_engineer import compute_features, get_valid_features

SCHWAB_PROXY = "http://127.0.0.1:8100"
SERVERS = {
    "MES": "http://127.0.0.1:8000",
    "ES":  "http://127.0.0.1:8004",
}
WARMUP_BARS = 65

# ---- Fetch SPY bars (proxy for both MES and ES) ----
print("Fetching SPY bars...")
r = requests.get(f"{SCHWAB_PROXY}/pricehistory",
                 params={"symbol": "SPY", "periodType": "day", "period": 5,
                         "frequencyType": "minute", "frequency": 5}, timeout=15)
candles = r.json().get("candles", [])
df = pd.DataFrame(candles)
df["datetime"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
df = df.sort_values("datetime").reset_index(drop=True)
print(f"  {len(df)} bars fetched\n")

# ---- Compute features ----
features = compute_features(
    opens=df["open"].values, highs=df["high"].values, lows=df["low"].values,
    closes=df["close"].values, volumes=df["volume"].values, datetimes=df["datetime"]
)
valid        = get_valid_features(features, n_bars=WARMUP_BARS + 1)
warmup_rows  = valid[:-1].tolist()
current_feat = valid[-1].tolist()
print(f"Features: {valid.shape[1]} cols, {len(valid)} valid rows")
print(f"Current feature vector: {[round(x,3) for x in current_feat]}\n")

# ---- Warm up and call ensemble on each server ----
print("=" * 65)
for inst, url in SERVERS.items():
    print(f"\n[{inst}]  {url}")
    print("-" * 50)

    # /config
    cfg = requests.get(f"{url}/config", timeout=5).json()
    print(f"  Config version  : {cfg.get('version')}")
    print(f"  Active models   : {cfg.get('active_models')}")
    print(f"  Current session : {cfg.get('current_session')}")

    cconf = cfg.get("file_config", {}).get("confidence", {})
    print(f"  minConfidence   : {cconf.get('minConfidence')}")
    print(f"  longSignalMin   : {cconf.get('longSignalMinConfidence')}")
    print(f"  shortSignalMin  : {cconf.get('shortSignalMinConfidence')}")

    # /warmup
    wr = requests.post(f"{url}/warmup", json={"historical_bars": warmup_rows}, timeout=15)
    print(f"\n  /warmup status  : {wr.status_code} | {wr.json()}")

    # /ensemble
    er = requests.post(f"{url}/ensemble",
                       json={"features": current_feat, "method": "confidence"}, timeout=10)
    data = er.json()
    print(f"\n  /ensemble result:")
    print(f"    signal     = {data.get('signal')}   (1=Long, -1=Short, 0=Neutral)")
    print(f"    confidence = {data.get('confidence'):.4f}  ({data.get('confidence',0)*100:.1f}%)")
    print(f"    agreement  = {data.get('agreement'):.2f}   ({data.get('agreement',0)*4:.0f}/4 models agree)")
    print(f"\n  Per-model votes:")
    for model, vote in (data.get("votes") or {}).items():
        sig   = vote.get("signal")
        conf  = vote.get("confidence", 0)
        probs = [round(p, 3) for p in vote.get("probs", [])]
        print(f"    {model:<20} signal={sig}  conf={conf:.3f}  probs={probs}")

print(f"\n{'='*65}")
print("Key:")
print("  agreement=0.25 -> only 1/4 models agree on same signal (ES issue)")
print("  agreement=0.50 -> 2/4 models agree (MES result)")
print("  Look for: split votes between Long/Short, or one model returning outlier")
