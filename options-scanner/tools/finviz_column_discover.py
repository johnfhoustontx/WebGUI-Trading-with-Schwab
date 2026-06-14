"""Print all Finviz Elite screener columns and their IDs.

Run once after each Finviz Elite version update or when adding new
columns to the export. Output is a numbered list suitable for
hardcoding into FINVIZ_*_COL_ID constants in event_calendar.py.

Reads the auth token from ../../appsettings.json (relative to this file)
— the same file that holds Schwab AppKey/AppSecret.

Usage:
    python tools/finviz_column_discover.py             # uses AAPL by default
    python tools/finviz_column_discover.py MSFT        # any liquid US ticker
"""
import csv
import io
import json
import sys
from pathlib import Path

import requests

APPSETTINGS = Path(__file__).resolve().parent.parent.parent / "appsettings.json"


def main(ticker: str = "AAPL") -> int:
    if not APPSETTINGS.exists():
        print(f"ERROR: appsettings.json not found at {APPSETTINGS}", file=sys.stderr)
        return 2
    cfg = json.loads(APPSETTINGS.read_text(encoding="utf-8"))
    auth = cfg.get("FinvizEliteAuth")
    if not auth:
        print("ERROR: FinvizEliteAuth not in appsettings.json", file=sys.stderr)
        return 2

    r = requests.get(
        "https://elite.finviz.com/export.ashx",
        params={
            "v": "152",
            "t": ticker,
            "c": ",".join(str(i) for i in range(1, 200)),
            "auth": auth,
        },
        timeout=15,
    )
    if r.status_code == 401:
        print("ERROR: Finviz auth invalid (401) — rotate FinvizEliteAuth", file=sys.stderr)
        return 1
    r.raise_for_status()

    rows = list(csv.reader(io.StringIO(r.text)))
    if not rows:
        print("ERROR: empty CSV response", file=sys.stderr)
        return 1

    header = rows[0]
    print(f"Finviz Elite returned {len(header)} columns for ticker {ticker}:\n")
    for idx, name in enumerate(header, start=1):
        print(f"  {idx:3d}: {name}")
    print()
    print("To find Earnings Date, look for 'Earnings' or 'Earnings Date' in the list above.")
    print("Update FINVIZ_EARNINGS_COL_ID in event_calendar.py to that index.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "AAPL"))
