"""Copy each closed session of gex_history.db into the Gamma Archive.

Run nightly by ``trading-<env>-gex-archive.timer`` ([slots.gex_archive]). Safe to
run at any time: it takes only closed sessions, and skips any it already holds.

    .venv/bin/python -X utf8 tools/archive_gex.py
    .venv/bin/python -X utf8 tools/archive_gex.py --db /path/to/gex_history.db --archive /path/to/out.db

``--db`` reads a COPY of the store, immutably: a backup, to save sessions the live
store has already purged. Design: docs/plans/2026-10-31-gamma-archive-design.md
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sqlite3
import subprocess
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import gex_archive  # noqa: E402
import gex_history_db as gh  # noqa: E402


def code_version():
    """The commit doing the archiving, so a study can tell which formula a session
    was computed under. ``None`` when git cannot say; that is not worth failing a
    night's archive over."""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def open_source(db=None):
    """The store to read, read-only. A named copy opens immutably: nothing writes it."""
    if db is None:
        return sqlite3.connect(f"file:{gh.DB_PATH}?mode=ro", uri=True)
    resolved = pathlib.Path(db).resolve().as_posix()
    return sqlite3.connect(f"file:{resolved}?immutable=1", uri=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Archive closed GEX sessions.")
    ap.add_argument("--db", help="read this COPY of gex_history.db, immutably")
    ap.add_argument("--archive", help="write this file instead of the app's archive")
    args = ap.parse_args(argv)

    source = open_source(args.db)
    archive = gex_archive.connect_archive(args.archive)
    try:
        done = gex_archive.archive_new_sessions(source, archive, _dt.datetime.now(),
                                                code_version())
        total = len(gex_archive.archived_dates(archive))
    finally:
        source.close()
        archive.close()
    for day, snapshot_rows, term_rows in done:
        print(f"archived {day}: {snapshot_rows} snapshot rows, {term_rows} term rows")
    print(f"{len(done)} new, {total} sessions held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
