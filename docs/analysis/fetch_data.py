"""Fetch 10-day, 5-min RTH intraday candles for the watchlist + reference universe
via the local schwab-proxy (:8100). Cache to prices.pkl for the analysis step."""
import sys, time, pickle, requests
import pandas as pd

BASE = "http://127.0.0.1:8100/pricehistory"
HERE = r"C:/Users/john_/AppData/Local/Temp/claude/D--WebGUI-Trading-with-Schwab/9a392989-bcd2-4e24-937f-2bd010329c64/scratchpad"

# ---- universe -------------------------------------------------------------
STOCKS = ['AAL','AAPL','ABBV','AMD','AMZN','AVGO','BAC','CMCSA','CMG','CRWV','DELL',
          'GOOGL','HOOD','INTC','IONQ','IREN','JPM','META','MRVL','MU','NFLX','NVDA',
          'PFE','PLTR','QCOM','RGTI','RKLB','SMCI','SOFI','SPCX','T','TSLA','UBER',
          'WMT','WULF','XOM','ALAB','NBIS','MSFT']
BROAD_ETF = ['SPY','QQQ','IWM','DIA']          # in watchlist; analysed AND references
INDEX     = ['$SPX','$NDX']
VOL       = ['$VIX']
BREADTH   = ['$ADVN','$DECN']
SECTORS   = ['XLK','XLF','XLE','XLV','XLY','XLP','XLI','XLU','XLB','XLRE','XLC']

ALL = STOCKS + BROAD_ETF + INDEX + VOL + BREADTH + SECTORS

def fetch(sym, retries=3):
    params = {"symbol": sym, "periodType": "day", "period": 10,
              "frequencyType": "minute", "frequency": 5, "needExtendedHoursData": "false"}
    for a in range(retries):
        try:
            r = requests.get(BASE, params=params, timeout=30)
            if r.status_code == 200:
                c = r.json().get("candles", [])
                if not c:
                    return None
                df = pd.DataFrame(c)
                df["dt"] = pd.to_datetime(df["datetime"], unit="ms", utc=True).dt.tz_convert("US/Eastern")
                df = df.set_index("dt").sort_index()
                # RTH only (defensive; proxy already excludes ext hours)
                df = df.between_time("09:30", "16:00")
                return df[["open","high","low","close","volume"]]
        except Exception as e:
            print(f"  {sym} attempt {a+1} err {e}", flush=True)
        time.sleep(1.0)
    return None

def main():
    out = {}
    for i, s in enumerate(ALL, 1):
        df = fetch(s)
        n = 0 if df is None else len(df)
        rng = "" if df is None else f"{df.index[0].date()}..{df.index[-1].date()}"
        print(f"[{i:2d}/{len(ALL)}] {s:8s} bars={n:4d} {rng}", flush=True)
        if df is not None:
            out[s] = df
        time.sleep(0.15)
    with open(f"{HERE}/prices.pkl", "wb") as f:
        pickle.dump({"prices": out,
                     "groups": {"STOCKS":STOCKS,"BROAD_ETF":BROAD_ETF,"INDEX":INDEX,
                                "VOL":VOL,"BREADTH":BREADTH,"SECTORS":SECTORS}}, f)
    print(f"\nSaved {len(out)}/{len(ALL)} symbols -> prices.pkl")
    missing = [s for s in ALL if s not in out]
    if missing: print("MISSING:", missing)

if __name__ == "__main__":
    main()
