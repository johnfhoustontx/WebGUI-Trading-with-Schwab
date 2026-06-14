#!/usr/bin/env python
"""One-shot dev helper: fire poll_term_once() once against the live Schwab
client and print what was written to gex_term_snapshots.

Useful for manually validating the term-heatmap data path outside of the
5-min collector cadence (e.g. for a fresh "No data" screen to populate
immediately during UI smoke testing).

Usage:
    python tools/gex_term_one_shot.py

Exit codes:
    0 = success (rows written)
    1 = runtime error (caught and logged)
    2 = no rows written (chain empty or term-poll suppressed)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Path setup so this script runs from the project root or from tools/.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gex_collector
import gex_history_db as db
from gamma_tool import GammaEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("gex_term_one_shot")


def _build_client():
    """Build a Schwab client using the same path the collector uses.

    Mirrors gex_collector._build_live_deps() — both import init_client from
    dashboard.
    """
    from dashboard import init_client
    return init_client()


def main() -> int:
    log.info("Building Schwab client...")
    try:
        client = _build_client()
    except Exception as e:
        log.error("Client build failed: %s", e)
        return 1
    if client is None:
        log.error("init_client() returned None. Authenticate first.")
        return 1

    log.info("Opening gex_history.db (read-write)...")
    conn = db.connect()
    db.init_schema(conn)  # init_schema now also calls init_term_schema

    log.info("Firing poll_term_once...")
    try:
        gex_collector.poll_term_once(client, GammaEngine(), conn)
    except Exception as e:
        log.error("poll_term_once raised: %s", e)
        conn.close()
        return 1

    # Find what we just wrote (latest snapshot)
    from datetime import datetime as _dt
    import zoneinfo
    TZ = zoneinfo.ZoneInfo("America/Chicago")
    today = _dt.now(TZ).strftime("%Y-%m-%d")
    timestamps = db.list_term_timestamps_for_date(conn, today, "SPX")
    if not timestamps:
        log.warning("No SPX term snapshots for today found in db.")
        conn.close()
        return 2

    latest_ts = timestamps[-1]
    rows = db.load_term_snapshot(conn, latest_ts, "SPX")
    conn.close()

    if not rows:
        log.warning("No rows for latest snapshot %s.", latest_ts)
        return 2

    expirations = sorted({r["expiration_date"] for r in rows})
    nets = [r["net_gex_usd"] for r in rows]

    print("=" * 60)
    print(f"Snapshot: {latest_ts}")
    print(f"Rows:     {len(rows)}")
    print(f"Expirations ({len(expirations)}): {', '.join(expirations)}")
    print(f"Net GEX range: {min(nets)/1e6:>10,.2f}M .. {max(nets)/1e6:>10,.2f}M")
    if all(n == 0 for n in nets):
        print()
        print("  !! ALL cells are $0 -- Schwab returned OI=0 for every contract.")
        print("  !! This is the typical off-hours behavior of the Schwab feed.")
        print("  !! Re-run during market hours for real numbers.")
        print()
    print("-" * 60)
    print("Sample of 5 rows (largest |net_gex| first):")
    for r in sorted(rows, key=lambda x: abs(x["net_gex_usd"]), reverse=True)[:5]:
        print(f"  K {int(r['strike']):>5} | Exp {r['expiration_date']} | "
              f"Net {r['net_gex_usd']/1e6:>12,.2f}M  "
              f"Call {r['call_gex_usd']/1e6:>10,.2f}M  "
              f"Put {r['put_gex_usd']/1e6:>10,.2f}M")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
