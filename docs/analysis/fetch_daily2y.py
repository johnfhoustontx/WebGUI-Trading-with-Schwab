"""Fetch ~1yr daily history for the same universe via schwab-proxy -> daily2y.pkl."""
import time, pickle, requests
import pandas as pd

BASE = "http://127.0.0.1:8100/pricehistory"
HERE = r"C:/Users/john_/AppData/Local/Temp/claude/D--WebGUI-Trading-with-Schwab/9a392989-bcd2-4e24-937f-2bd010329c64/scratchpad"

with open(f"{HERE}/prices.pkl","rb") as f:
    G = pickle.load(f)["groups"]
ALL = G["STOCKS"]+G["BROAD_ETF"]+G["INDEX"]+G["VOL"]+G["BREADTH"]+G["SECTORS"]

def fetch(sym, retries=3):
    params = {"symbol": sym, "periodType": "year", "period": 2,
              "frequencyType": "daily", "frequency": 1, "needExtendedHoursData": "false"}
    for a in range(retries):
        try:
            r = requests.get(BASE, params=params, timeout=30)
            if r.status_code == 200:
                c = r.json().get("candles", [])
                if not c: return None
                df = pd.DataFrame(c)
                df["dt"] = pd.to_datetime(df["datetime"], unit="ms", utc=True).dt.tz_convert("US/Eastern").dt.normalize()
                return df.set_index("dt").sort_index()[["open","high","low","close","volume"]]
        except Exception as e:
            print(f"  {sym} try{a+1} {e}", flush=True)
        time.sleep(1.0)
    return None

out={}
for i,s in enumerate(ALL,1):
    df=fetch(s); n=0 if df is None else len(df)
    rng="" if df is None else f"{df.index[0].date()}..{df.index[-1].date()}"
    print(f"[{i:2d}/{len(ALL)}] {s:8s} bars={n:4d} {rng}", flush=True)
    if df is not None: out[s]=df
    time.sleep(0.12)
with open(f"{HERE}/daily2y.pkl","wb") as f:
    pickle.dump({"prices":out,"groups":G}, f)
print(f"\nSaved {len(out)}/{len(ALL)} -> daily2y.pkl")
short=[(s,len(out[s])) for s in out if len(out[s])<126]
if short: print("Fewer than 126 daily bars (young listings):", short)
