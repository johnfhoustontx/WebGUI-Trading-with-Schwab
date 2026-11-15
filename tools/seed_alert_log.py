"""Copy the contract-level alerts still held in gex_history.db into the alert record.

``flow_contract_days`` keeps about 20 sessions of the unusual activity and big delta
alerts; the record (``services/options_svc/alert_log.py``) starts empty. Run once,
by hand, so Model Health's Alert quality has those sessions from its first night.
Safe to rerun: an alert already held is skipped.

    .venv/bin/python -X utf8 tools/seed_alert_log.py
    .venv/bin/python -X utf8 tools/seed_alert_log.py --db /path/to/backup/gex_history.db

``--db`` reads a COPY immutably (a backup can hold sessions the live store has
purged). Design: docs/plans/2026-11-14-alert-quality-design.md
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import gex_history_db as gh  # noqa: E402
from services.options_svc import alert_log  # noqa: E402

# Carried into the record's detail, beside its own columns.
_DETAIL = ("osi", "strike", "expiry", "volume")


def _alert(row):
    a = {"id": row.get("alert_id"), "type": row.get("alert_type"),
         "side": row.get("side"), "symbol": row.get("symbol"), "ts": row.get("fired_ts")}
    a.update({k: row.get(k) for k in _DETAIL})
    return a


def seed(source_conn, db_path=None) -> int:
    """Copy every held contract-day alert; returns the rows added."""
    days = [r[0] for r in source_conn.execute(
        "SELECT DISTINCT session_date FROM flow_contract_days ORDER BY session_date")]
    added = 0
    for day in days:
        added += alert_log.record([_alert(r) for r in gh.load_flow_contract_days(
            source_conn, day)], source="seed", db_path=db_path)
    return added


def open_source(db=None):
    if db is None:
        return sqlite3.connect(f"file:{gh.DB_PATH.as_posix()}?mode=ro", uri=True)
    resolved = pathlib.Path(db).resolve().as_posix()
    return sqlite3.connect(f"file:{resolved}?immutable=1", uri=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seed the alert record.")
    ap.add_argument("--db", help="read this COPY of gex_history.db, immutably")
    ap.add_argument("--log", help="write this file instead of the app's alert record")
    args = ap.parse_args(argv)
    source = open_source(args.db)
    try:
        added = seed(source, db_path=args.log)
    finally:
        source.close()
    print(f"{added} alerts added")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
