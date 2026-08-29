#!/usr/bin/env python
"""Nightly backup of everything this checkout cannot re-create.

WHY THIS EXISTS. The E:-drive robocopy routine died with the Windows box. After
the 2026-08-29 cutover the VPS is the ONLY live copy of the trading record:
``paper_account.db`` and ``paper_account_driver.db`` are the books,
``signals.db`` is what the model said and when, and ``gex_history.db`` is ~1.5 GB
of intraday dealer positioning that cannot be re-fetched at any price -- Schwab
serves no history for it. Losing them is not a restore-from-upstream situation;
it is data that stops existing.

WHAT IT COPIES
  * every ``*.db`` in the checkout, via SQLite's ONLINE BACKUP API so the stack
    keeps running throughout. ⚠ Never ``cp`` a live SQLite file: a copy taken
    mid-write is a torn database that passes a file-size check and fails
    ``PRAGMA integrity_check``, usually months later.
  * Redis, via ``--rdb`` (a real point-in-time RDB, not a key scan).
  * the gitignored DATA TREES, swept whole -- EOD report archives, the portfolio
    ledger, app settings, generated reports, the watchlist workbook, the swing
    model artifact -- plus the loose secret files.

    ⚠ A SWEEP, not a list. The first version copied *.db plus a named list, and
    that shape silently lost the EOD archives, entries.json and settings.json
    during the migration. A list must be remembered whenever a feature starts
    writing somewhere new; a sweep must not.

WHAT IT DELIBERATELY DOES NOT DO
  * It does not run ``VACUUM``. A backup job is the wrong place to mutate the
    source.
  * It does not stop the stack. The whole point of the online API is that it
    does not have to, and a backup that requires downtime is a backup that gets
    skipped.
  * It keeps only ``KEEP`` dated generations locally. Local copies protect
    against corruption and mistakes; they do NOT protect against losing the
    instance. That is what the offsite pull is for -- see tools/pull_backups.ps1.

Usage:
    .venv/bin/python tools/backup_local.py            # into ~/backups
    .venv/bin/python tools/backup_local.py --dest /mnt/x --keep 7
"""
import argparse
import datetime as dt
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from repo_paths import ENV_NAME, MEMURAI_PORT, REDIS_DB, REPO_ROOT  # noqa: E402

KEEP = 3

# Loose gitignored files that live OUTSIDE the data trees below.
EXTRA_FILES = (
    "shared/appsettings.json",
    "shared/tokens.json",
    "shared/notifications.json",
    "shared/anthropic_key.txt",
    "shared/sentiment_bridge.json",
    "schwab-proxy/proxy_tokens.json",
    "config/env.local.toml",
)

# Gitignored data trees, swept WHOLE.
#
# ⚠ This is a SWEEP and not a list on purpose. The first version of this script
# backed up *.db plus a named list, which is exactly the shape that lost the EOD
# archives, portfolio-analyzer/data/entries.json (86 real trades -- the ledger)
# and webgui/data/settings.json during the 2026-08-29 migration: anything living
# one directory down fell straight through, silently, and the gap only surfaced
# because someone went looking for a report.
#
# A named list has to be remembered every time a feature starts writing
# somewhere new. A sweep does not.
DATA_TREES = (
    "webgui/data",
    "options-scanner/data",
    "sentiment-dashboard/data",
    "trade-analyzer/data",
    "portfolio-analyzer/data",
    "shared/data",
    "services/trade_svc/data",
    "schwab-proxy/data",
)

# Excluded from the sweep, each for a stated reason -- never "it looked big".
SWEEP_EXCLUDE = (
    # Regenerated on demand by edge-tts from the phrase text. Restoring these
    # buys nothing a first playback would not rebuild.
    "webgui/data/voice",
)


def db_paths(root):
    """Every non-empty SQLite store in the checkout."""
    return sorted(p for p in root.rglob("*.db")
                  if ".git" not in p.parts and p.is_file() and p.stat().st_size > 0)


def backup_db(src, dst):
    """Online-backup `src` to `dst`. Returns the destination size in bytes.

    Uses sqlite3's backup API, which takes a consistent snapshot of a database
    that is actively being written. The alternative -- copying the file -- can
    capture a write in progress and produce something that only fails later.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    return dst.stat().st_size


def verify(path):
    """PRAGMA integrity_check on a finished copy. 'ok' or the failure text.

    A backup nobody has verified is a hypothesis. Checking at write time is what
    makes the difference between finding corruption now and finding it during a
    restore, which is the worst possible moment.
    """
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return con.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        return f"unreadable: {exc}"


def backup_redis(dst):
    """Point-in-time RDB of the bus. Returns (ok, detail)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["redis-cli", "-p", str(MEMURAI_PORT), "-n", str(REDIS_DB),
           "--rdb", str(dst)]
    env = dict(os.environ)
    pw = env.get("MEMURAI_PASSWORD")
    if pw:
        env["REDISCLI_AUTH"] = pw          # never on the command line
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if dst.exists() and dst.stat().st_size > 0:
        return True, f"{dst.stat().st_size:,} bytes"
    return False, (p.stderr or p.stdout or "no output").strip()[:200]


def prune(root, keep):
    """Drop all but the newest `keep` dated generations. Returns names removed."""
    gens = sorted((d for d in root.iterdir() if d.is_dir()), reverse=True)
    dropped = []
    for d in gens[keep:]:
        shutil.rmtree(d, ignore_errors=True)
        dropped.append(d.name)
    return dropped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", default=str(pathlib.Path.home() / "backups"))
    ap.add_argument("--keep", type=int, default=KEEP)
    args = ap.parse_args(argv)

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    dest_root = pathlib.Path(args.dest)
    out = dest_root / f"{ENV_NAME}_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"backup -> {out}")

    failures = []
    total = 0

    for src in db_paths(REPO_ROOT):
        rel = src.relative_to(REPO_ROOT)
        dst = out / rel
        try:
            size = backup_db(src, dst)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{rel}: {exc}")
            print(f"  FAIL  {rel}: {exc}")
            continue
        state = verify(dst)
        total += size
        flag = "ok  " if state == "ok" else "BAD "
        if state != "ok":
            failures.append(f"{rel}: integrity_check={state}")
        print(f"  {flag}{rel}  {size:,} bytes")

    ok, detail = backup_redis(out / "redis" / f"db{REDIS_DB}.rdb")
    print(f"  {'ok  ' if ok else 'FAIL'}redis db{REDIS_DB}  {detail}")
    if not ok:
        failures.append(f"redis: {detail}")

    for rel in EXTRA_FILES:
        src = REPO_ROOT / rel
        if not src.is_file():
            print(f"  --  {rel} (absent)")
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o600)
        print(f"  ok  {rel}")

    swept = swept_bytes = 0
    for tree in DATA_TREES:
        root = REPO_ROOT / tree
        if not root.is_dir():
            continue
        for src in sorted(root.rglob("*")):
            if not src.is_file() or src.suffix in (".db", ".db-wal", ".db-shm"):
                continue          # databases went through the online backup API
            rel = src.relative_to(REPO_ROOT)
            if any(str(rel).replace("\\", "/").startswith(x) for x in SWEEP_EXCLUDE):
                continue
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            swept += 1
            swept_bytes += src.stat().st_size
    print(f"  ok  swept {swept} files from the data trees "
          f"({swept_bytes / 1e6:.1f} MB)")

    dropped = prune(dest_root, args.keep)
    if dropped:
        print(f"pruned {len(dropped)} old generation(s): {', '.join(dropped)}")

    print(f"\n{total / 1e9:.2f} GB of databases, {len(failures)} failure(s)")
    if failures:
        for f in failures:
            print(f"  ! {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
