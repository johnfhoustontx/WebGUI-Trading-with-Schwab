#!/usr/bin/env python
"""One-time migration for GEX rows written before the × 0.01 fix landed.

Before commit 7a8d218 ("fix: GEX x0.01 normalization..."), gamma_tool's
GEX formula omitted the `* 0.01` factor the spec requires for "per 1% move
in underlying." Every GEX snapshot written by gex_collector.py pre-fix is
therefore 100x too large.

This script covers two recovery modes:

  --purge      delete all GEX rows (view='gex'); the collector repopulates
               on its next poll with the corrected values. Charm and DEX
               rows are UNTOUCHED because the bug only affected GEX.
               Recommended when you don't need the buggy intraday history.

  --rescale    divide net_total, top_pos_strike (value, not key), the
               per-strike gex_json call/put/net values, and any other GEX
               numeric columns by 100. Preserves the history but changes
               magnitudes. Idempotent via a PRAGMA user_version marker
               (will refuse to re-run against an already-migrated DB).

Run:
    python scripts/fix_gex_history_scale.py --purge
    python scripts/fix_gex_history_scale.py --rescale

The script is safe to run while the collector is idle (e.g. overnight).
Do not run while a poll is writing; coordinate via the Windows Task
Scheduler or stop the task first.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# user_version marker semantics:
#   0 — DB has never been migrated. Buggy data may be present.
#   1 — Migration has run (either purge or rescale).
MIGRATED_USER_VERSION = 1


def _resolve_db_path(cli_path: str | None) -> Path:
    if cli_path:
        return Path(cli_path).resolve()
    # Default: the gex_history.db next to the project root.
    return (Path(__file__).resolve().parent.parent / "gex_history.db").resolve()


def _current_user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_user_version(conn: sqlite3.Connection, n: int) -> None:
    conn.execute(f"PRAGMA user_version = {n}")


def _count(conn: sqlite3.Connection, where: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM snapshots WHERE {where}").fetchone()
    return int(row[0]) if row else 0


def run_purge(conn: sqlite3.Connection) -> int:
    """Delete all GEX snapshots. Collector will backfill on next poll."""
    cur = conn.execute("DELETE FROM snapshots WHERE view = 'gex'")
    return cur.rowcount


def _rescale_grid(raw_json: str | None, factor: float) -> str | None:
    if not raw_json:
        return raw_json
    grid = json.loads(raw_json)
    scaled = {}
    for strike, lane in grid.items():
        if isinstance(lane, dict):
            scaled[strike] = {k: (v * factor if isinstance(v, (int, float)) else v)
                              for k, v in lane.items()}
        elif isinstance(lane, (int, float)):
            scaled[strike] = lane * factor
        else:
            scaled[strike] = lane
    return json.dumps(scaled)


def run_rescale(conn: sqlite3.Connection, factor: float = 0.01) -> int:
    """Multiply GEX magnitudes by `factor` in place. Columns that hold
    strike prices (top_pos_strike, top_neg_strike) are left unchanged —
    they are strike prices, not GEX magnitudes.
    """
    rows = conn.execute(
        "SELECT rowid, net_total, gex_json FROM snapshots WHERE view = 'gex'"
    ).fetchall()
    scaled = 0
    for rowid, net_total, gex_json in rows:
        new_total = (net_total * factor) if net_total is not None else None
        new_json = _rescale_grid(gex_json, factor)
        conn.execute(
            "UPDATE snapshots SET net_total = ?, gex_json = ? WHERE rowid = ?",
            (new_total, new_json, rowid),
        )
        scaled += 1
    return scaled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--purge", action="store_true", help="DELETE all GEX rows (view='gex').")
    mode.add_argument("--rescale", action="store_true", help="Multiply GEX magnitudes by 0.01 in place.")
    parser.add_argument("--db", help="Path to gex_history.db (default: next to project root).")
    parser.add_argument("--force", action="store_true",
                        help="Run even if the user_version marker says migration already ran.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change; don't write.")
    args = parser.parse_args(argv)

    db = _resolve_db_path(args.db)
    if not db.exists():
        print(f"No DB found at {db}; nothing to migrate.")
        return 0

    conn = sqlite3.connect(str(db))
    try:
        before = _count(conn, "view='gex'")
        print(f"DB: {db}")
        print(f"GEX rows before: {before}")

        version = _current_user_version(conn)
        if version >= MIGRATED_USER_VERSION and not args.force:
            print(f"PRAGMA user_version = {version} — migration already ran. "
                  f"Use --force to override.")
            return 0

        if args.dry_run:
            print("--dry-run: no changes written.")
            return 0

        if args.purge:
            deleted = run_purge(conn)
            print(f"Deleted {deleted} GEX row(s). Collector will repopulate on next poll.")
        else:
            scaled = run_rescale(conn, factor=0.01)
            print(f"Rescaled {scaled} GEX row(s) by 0.01 (net_total + gex_json values).")

        _set_user_version(conn, MIGRATED_USER_VERSION)
        conn.commit()

        after = _count(conn, "view='gex'")
        print(f"GEX rows after: {after}")
        print("Done.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
