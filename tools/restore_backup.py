#!/usr/bin/env python
"""Restore one backup generation into a checkout (or a scratch directory).

The other half of ``tools/backup_local.py``. Until 2026-10-03 there was a
backup and no restore: nothing documented how to put a generation back and
nobody had ever tried, so the first restore would also have been the first test
(audit AR-04).

    # 1. If the generation is only offsite, fetch and decrypt it first:
    rclone copy gdrive:TradingBackups/prod_2026-10-02_2004.tar.age /tmp/restore/
    age -d -i ~/.config/age/backup-key.txt /tmp/restore/prod_2026-10-02_2004.tar.age \\
        | tar xf - -C /tmp/restore

    # 2. Look before writing anything:
    .venv/bin/python tools/restore_backup.py /tmp/restore/prod_2026-10-02_2004 \\
        --into /home/administrator/dev --dry-run

    # 3. Restore (the stack must be STOPPED when --into is a live checkout):
    .venv/bin/python tools/restore_backup.py /tmp/restore/prod_2026-10-02_2004 \\
        --into /home/administrator/dev

    # A drill, any time, touching nothing live:
    .venv/bin/python tools/restore_backup.py ~/backups/prod_2026-10-02_2004 \\
        --into /tmp/restore-drill

WHAT IT DOES. Copies every file of the generation to the same relative path
under ``--into``, then runs ``PRAGMA integrity_check`` on every restored
database. A file that already exists is LEFT ALONE and reported unless
``--force`` is given: a restore must never silently replace newer data.

WHAT IT DOES NOT DO. It does not restore Redis. The generation holds an RDB
dump (``redis/db<N>.rdb``), and putting it back means stopping the system
``redis-server`` and replacing its dump file as root - see the runbook. The
stack runs without it: every cache view is rebuilt by its service.

No imports from this repository, on purpose: it has to run on a box whose
checkout is the thing being rebuilt.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sqlite3
import sys

OK_MARKER = "BACKUP_OK"
REDIS_DIR = "redis"


def files_in(generation: pathlib.Path) -> list:
    """Every restorable file of a generation, as paths relative to it. The Redis
    dump and the marker are not files of the checkout."""
    out = []
    for p in sorted(generation.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(generation)
        if rel.parts[0] == REDIS_DIR or rel.name == OK_MARKER and len(rel.parts) == 1:
            continue
        out.append(rel)
    return out


def integrity(path: pathlib.Path) -> str:
    """``ok``, or what is wrong with the database at ``path``."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return con.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        return f"unreadable: {exc}"


def restore(generation, into, *, dry_run=False, force=False, out=print) -> dict:
    """Restore ``generation`` under ``into``. Returns a summary dict:
    ``{"restored": [...], "kept": [...], "bad": [...], "marked_ok": bool}``."""
    generation, into = pathlib.Path(generation), pathlib.Path(into)
    summary = {"restored": [], "kept": [], "bad": [],
               "marked_ok": (generation / OK_MARKER).is_file()}
    if not summary["marked_ok"]:
        out(f"  !  {generation.name} has no {OK_MARKER} marker: the backup run "
            "that made it reported a failure (or predates the marker)")
    for rel in files_in(generation):
        src, dst = generation / rel, into / rel
        if dst.exists() and not force:
            summary["kept"].append(str(rel))
            out(f"  --  {rel} (already there; left alone)")
            continue
        if dry_run:
            summary["restored"].append(str(rel))
            out(f"  would restore  {rel}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        try:
            os.chmod(dst, 0o600)          # credentials and the trading record
        except OSError:
            pass
        summary["restored"].append(str(rel))
        if dst.suffix == ".db":
            state = integrity(dst)
            if state != "ok":
                summary["bad"].append(f"{rel}: {state}")
            out(f"  {'ok  ' if state == 'ok' else 'BAD '}{rel}")
        else:
            out(f"  ok  {rel}")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("generation", help="a generation directory, e.g. ~/backups/prod_2026-10-02_2004")
    ap.add_argument("--into", required=True, help="the checkout (or scratch directory) to restore under")
    ap.add_argument("--dry-run", action="store_true", help="list what would be restored; write nothing")
    ap.add_argument("--force", action="store_true", help="replace files that already exist")
    args = ap.parse_args(argv)

    generation = pathlib.Path(args.generation).expanduser()
    if not generation.is_dir():
        print(f"not a generation directory: {generation}", file=sys.stderr)
        return 2
    print(f"restore {generation} -> {args.into}{'  (dry run)' if args.dry_run else ''}")
    s = restore(generation, pathlib.Path(args.into).expanduser(),
                dry_run=args.dry_run, force=args.force)
    verb = "would restore" if args.dry_run else "restored"
    print(f"\n{verb} {len(s['restored'])} file(s); left {len(s['kept'])} existing "
          f"file(s) alone; {len(s['bad'])} database(s) failed their integrity check")
    for line in s["bad"]:
        print(f"  ! {line}")
    if (generation / REDIS_DIR).is_dir():
        print(f"Redis was NOT restored. Its dump is {generation / REDIS_DIR}; see "
              "docs/dev-prod-environments.md, \"Restoring from a backup\".")
    return 1 if s["bad"] else 0


if __name__ == "__main__":
    sys.exit(main())
