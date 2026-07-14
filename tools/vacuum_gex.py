"""One-time (or occasional) offline VACUUM of the GEX history DB to reclaim disk.

``gex_history.db`` grows to multiple GB. The options service already PURGES old rows
daily (``gex_history_db.purge_keep_sessions``), which stops unbounded growth and frees
pages for REUSE — but a plain DELETE does NOT shrink the file on disk. This tool runs
``PRAGMA auto_vacuum=INCREMENTAL; VACUUM;`` to actually shrink it (and enable incremental
reclaim going forward), reporting the before/after size.

**Run it OFFLINE.** A full VACUUM takes an EXCLUSIVE lock and rewrites the whole file for
minutes; doing that while the 1-min GEX collector is writing would fail collection. So this
refuses to run during market hours or while the collector lock looks active, unless you pass
``--force``. It is NOT wired into any scheduler — you run it (e.g. on a weekend).

Usage:
    python tools/vacuum_gex.py            # safe: vacuum only if off-hours + collector idle
    python tools/vacuum_gex.py --purge    # also run retention (keep last 5 sessions) first
    python tools/vacuum_gex.py --force     # override the off-hours / collector guard
    python tools/vacuum_gex.py --dry-run   # report size + what it would do, no changes
"""
import argparse
import datetime as dt
import pathlib
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import OPTIONS_SCANNER

_TZ = ZoneInfo("America/Chicago")
DB_PATH = pathlib.Path(OPTIONS_SCANNER) / "gex_history.db"
LOCK_PATH = pathlib.Path(OPTIONS_SCANNER) / "data" / "gex_collector.lock"
# The collector runs ~08:30–15:20 CT on trading days (a 1-min write cadence). Guard a
# little wider so a VACUUM never overlaps a live write.
_COLLECT_START = dt.time(8, 0)
_COLLECT_END = dt.time(15, 30)


def _fmt_size(n_bytes) -> str:
    return f"{n_bytes / (1024 ** 3):.2f} GB" if n_bytes >= 1024 ** 3 else f"{n_bytes / (1024 ** 2):.1f} MB"


def is_collection_window(now=None) -> bool:
    """True if the GEX collector is likely writing now (trading day, ~08:00–15:30 CT)."""
    now = now or dt.datetime.now(_TZ)
    return now.weekday() < 5 and _COLLECT_START <= now.time() <= _COLLECT_END


def lock_looks_active(*, now_ts=None, max_age_sec=180) -> bool:
    """True if the collector advisory lock was touched within the last few minutes."""
    try:
        if not LOCK_PATH.exists():
            return False
        age = (now_ts or time.time()) - LOCK_PATH.stat().st_mtime
        return age < max_age_sec
    except Exception:
        return False


def main(argv=None) -> int:
    # Windows: stdout defaults to cp1252 when piped (e.g. run from the Settings
    # page's subprocess), which can't encode the summary's unicode arrows — the
    # VACUUM then SUCCEEDS but the process dies printing its result. Re-encode
    # defensively; errors="replace" keeps output flowing no matter the console.
    try:
        import sys as _sys
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — cosmetics must not block maintenance
        pass
    ap = argparse.ArgumentParser(description="Offline VACUUM of gex_history.db.")
    ap.add_argument("--purge", action="store_true",
                    help="run retention (keep last 5 sessions) before vacuuming")
    ap.add_argument("--force", action="store_true",
                    help="override the off-hours / collector-idle guard")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not DB_PATH.exists():
        print(f"No DB at {DB_PATH} — nothing to do.")
        return 0
    before = DB_PATH.stat().st_size
    print(f"gex_history.db: {_fmt_size(before)}  ({DB_PATH})")

    busy = is_collection_window() or lock_looks_active()
    if busy and not args.force:
        print("REFUSING: the GEX collector looks active (market hours or a fresh lock).\n"
              "  VACUUM locks the DB for minutes and would break collection.\n"
              "  Run this off-hours, or pass --force if you know collection is stopped.")
        return 2

    if args.dry_run:
        print("[dry-run] would" + (" purge (keep 5 sessions) then" if args.purge else "")
              + " PRAGMA auto_vacuum=INCREMENTAL; VACUUM;")
        return 0

    if args.purge:
        sys.path.insert(0, str(pathlib.Path(OPTIONS_SCANNER)))
        import gex_history_db as gdb
        conn = gdb.connect() if hasattr(gdb, "connect") else sqlite3.connect(DB_PATH)
        try:
            n = gdb.purge_keep_sessions(conn, keep_sessions=5)
            conn.commit()
            print(f"purged {n} old rows (kept last 5 sessions)")
        finally:
            conn.close()

    print("running PRAGMA auto_vacuum=INCREMENTAL; VACUUM; … (this can take minutes)")
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()
    after = DB_PATH.stat().st_size
    print(f"done in {time.time() - t0:.0f}s — {_fmt_size(before)} → {_fmt_size(after)} "
          f"(reclaimed {_fmt_size(max(0, before - after))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
