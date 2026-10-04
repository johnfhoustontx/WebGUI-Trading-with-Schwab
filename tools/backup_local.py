#!/usr/bin/env python
"""Nightly backup of everything this checkout cannot re-create.

WHY THIS EXISTS. The E:-drive robocopy routine died with the Windows box. After
the 2026-08-29 cutover the VPS is the ONLY live copy of the trading record:
``paper_account.db`` is the book (and ``paper_account_driver.db`` the removed
autonomous driver's history),
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

OFFSITE. Once the local generation is complete it is tarred, age-encrypted to
the public key in ``~/.config/age/backup-key.txt``, uploaded to Google Drive and
verified with ``rclone check`` (hashes, not just size). Drive never sees
plaintext: the archive carries live Schwab OAuth tokens, the Schwab API keys, the
Anthropic key and the notification credentials.

⚠ THE PRIVATE KEY IS THE BACKUP. Lose every copy of
``~/.config/age/backup-key.txt`` and the Drive archive is permanently
unreadable -- 1.5 GB of noise. It is escrowed on the Windows workstation and
belongs in a password manager too, and it must NEVER be uploaded to Drive: a key
stored beside its ciphertext is not encryption.

Usage:
    .venv/bin/python tools/backup_local.py               # local + offsite
    .venv/bin/python tools/backup_local.py --no-offsite  # local only
    .venv/bin/python tools/backup_local.py --dest /mnt/x --keep 7
"""
import argparse
import datetime as dt
import os
import pathlib
import shlex
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from repo_paths import ENV_NAME, MEMURAI_PORT, REDIS_DB, REPO_ROOT  # noqa: E402

KEEP = 3
# One generation from each of this many EARLIER weeks is kept as well. Three
# dailies on weeknights only meant the oldest copy was three trading days old:
# a problem noticed on a Monday had nothing from before the previous Wednesday.
# ~1.6 GB each; the box had 119 GB free when this was set (2026-10-03).
KEEP_WEEKLY = 4
# Written into a generation whose LOCAL part finished with no failure. prune()
# never removes the newest generation carrying it, however many damaged nights
# follow.
OK_MARKER = "BACKUP_OK"

# Offsite: encrypt, then upload the CIPHERTEXT. Drive never sees plaintext.
#
# The archive carries live Schwab OAuth tokens, the Schwab API keys, the
# Anthropic key and the notification credentials. Those must not sit in a
# third-party service in the clear, where they can be cached, indexed, synced to
# other devices and remain recoverable after deletion.
AGE_IDENTITY = pathlib.Path.home() / ".config" / "age" / "backup-key.txt"
RCLONE_REMOTE = "gdrive:TradingBackups"
KEEP_REMOTE = 3
# Offsite: ONE earlier week on top of the three dailies. Each archive is ~1.6 GB
# of a shared Drive quota (23 GiB free, 2026-10-03), so the weekly depth lives
# on the server's disk and only one week of it is shipped.
KEEP_REMOTE_WEEKLY = 1

# Loose gitignored files that live OUTSIDE the data trees below.
EXTRA_FILES = (
    "shared/appsettings.json",
    "shared/tokens.json",
    "shared/notifications.json",
    "shared/anthropic_key.txt",
    "shared/sentiment_bridge.json",
    "schwab-proxy/proxy_tokens.json",
    "config/env.local.toml",
    # THE LOGIN STORE: the password hash, the second-factor secret, the session
    # secret and the last accepted code counter. Omitted until 2026-10-03, so a
    # restore produced an app its owner could not sign in to (audit AR-04).
    "shared/webgui_auth.json",
    # Vendor and integration credentials, each gitignored and each absent from
    # this list until the same date. tests/test_backup_local.py now derives the
    # expected set from .gitignore, so the next one cannot be forgotten.
    "shared/google_calendar_sa.json",
    "shared/alphavantage_key.txt",
    "shared/massive_key.txt",
    "shared/proxy_secret.txt",
    # The public site's manifest of published trade ideas (the cards themselves
    # are swept from deploy/site/ideas below).
    "deploy/site/ideas.json",
    # The two operator switches options_svc keeps on disk (auto-close, the
    # manual book's lifecycle), so they outlive a Redis flush.
    "options-scanner/data/operator_toggles.json",
    # The units' EnvironmentFile -- MEMURAI_PASSWORD, ALPHAVANTAGE_API_KEY,
    # EDGAR_USER_AGENT, anything else read from the process environment. It is
    # loaded with NO leading dash, so a missing one does not degrade: the unit
    # fails to start. It was omitted here while its sibling config/env.local.toml
    # was carried, which meant a restore produced a stack that would not come up
    # and nothing to fix it with.
    ".env",
    # The PUBLIC live unit's own EnvironmentFile -- REDIS_LIVE_URL and
    # MEMURAI_PASSWORD, and deliberately NOT the rest of .env. Same argument as
    # its sibling above: it is loaded with no leading dash, so a restore missing
    # it produces a live unit that will not start, from an archive that looks
    # complete.
    ".env.live",
)

# Gitignored ``shared/`` files deliberately NOT carried, each with its reason.
# The guard test fails on a gitignored shared/ file that is in neither list.
NOT_BACKED_UP = {
    "shared/driver_model.txt": "belonged to the autonomous driver, removed "
                               "2026-09-22; nothing reads it",
}

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
    "services/news_svc/data",
    "schwab-proxy/data",
    # The public site's generated state: every posted trade idea's card, and the
    # market reports the site frames. Both are gitignored, so a rebuilt box
    # serves an empty site until they are put back.
    "deploy/site/ideas",
    "deploy/site/reports",
    # The operator's Settings -> Configuration overrides (gitignored). Losing
    # them silently reverts every tuned threshold to the shipped value.
    "config/local",
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


def age_recipient(identity=AGE_IDENTITY):
    """The PUBLIC key from the age identity file, or None.

    Read from the identity rather than hardcoded, so there is one source. A
    second copy of the public key could drift from the private one, and the
    failure would be an archive nobody can decrypt -- discovered during a
    restore, which is the worst possible moment.
    """
    try:
        for line in identity.read_text(encoding="utf-8").splitlines():
            if line.startswith("# public key:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def encrypt_generation(gen_dir, out_path, recipient):
    """tar the generation and age-encrypt it. Returns (ok, detail).

    NOT compressed: gex_history.db is ~95% of the bytes and its grids are
    already zlib-compressed, so gzip would burn CPU for almost nothing.
    """
    cmd = (f"tar cf - -C {shlex.quote(str(gen_dir.parent))} "
           f"{shlex.quote(gen_dir.name)} | age -r {shlex.quote(recipient)} "
           f"> {shlex.quote(str(out_path))}")
    try:
        p = subprocess.run(["bash", "-o", "pipefail", "-c", cmd],
                           capture_output=True, text=True, timeout=3600)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if p.returncode != 0:
        return False, (p.stderr or "tar|age failed").strip()[:300]
    if not out_path.exists() or out_path.stat().st_size == 0:
        return False, "produced no output"
    return True, f"{out_path.stat().st_size:,} bytes"


def upload(path, remote=RCLONE_REMOTE, keep=KEEP_REMOTE):
    """rclone copy `path` to `remote`, verify by checksum, prune old. (ok, detail).

    ⚠ Verified with `rclone check`, not by exit code alone. A transfer can
    report success and leave a short object; the check compares hashes.
    """
    try:
        p = subprocess.run(["rclone", "copy", str(path), remote + "/", "--transfers", "1"],
                           capture_output=True, text=True, timeout=7200)
        if p.returncode != 0:
            return False, (p.stderr or "rclone copy failed").strip()[:300]
        c = subprocess.run(["rclone", "check", str(path.parent), remote + "/",
                            "--include", path.name, "--one-way"],
                           capture_output=True, text=True, timeout=3600)
        if c.returncode != 0:
            return False, "uploaded but CHECKSUM MISMATCH: " + (c.stderr or "").strip()[:200]
        listing = subprocess.run(["rclone", "lsf", remote + "/"],
                                 capture_output=True, text=True, timeout=300)
        names = sorted(n for n in listing.stdout.split() if n.endswith(".tar.age"))
        drop = remote_to_delete(names, keep=keep, keep_weekly=KEEP_REMOTE_WEEKLY)
        for old in drop:
            subprocess.run(["rclone", "deletefile", f"{remote}/{old}"],
                           capture_output=True, text=True, timeout=300)
        return True, f"verified; {len(names) - len(drop)} generation(s) offsite"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _generation_date(name):
    """The date in a generation name (``<env>_YYYY-MM-DD_HHMM``), or None."""
    try:
        return dt.date.fromisoformat(name.rsplit("_", 2)[-2])
    except (ValueError, IndexError):
        return None


def select_keep(names, keep, keep_weekly=0):
    """The generation names to KEEP (PURE).

    * the newest ``keep`` dated generations;
    * then, working back through the older ones, the newest generation of each
      of the next ``keep_weekly`` ISO weeks that has none kept already;
    * every name with no readable date: not this tool's to delete, and it uses
      no slot.
    """
    dated = sorted((n for n in names if _generation_date(n)), reverse=True)
    kept = set(n for n in names if not _generation_date(n))
    kept.update(dated[:max(keep, 0)])
    weeks = {_generation_date(n).isocalendar()[:2] for n in dated[:max(keep, 0)]}
    taken = 0
    for n in dated[max(keep, 0):]:
        if taken >= keep_weekly:
            break
        week = _generation_date(n).isocalendar()[:2]
        if week not in weeks:
            kept.add(n)
            weeks.add(week)
            taken += 1
    return kept


def remote_to_delete(archives, keep, keep_weekly=0):
    """Which offsite ``.tar.age`` names to delete, oldest first (PURE)."""
    stems = {a[:-len(".tar.age")]: a for a in archives if a.endswith(".tar.age")}
    kept = select_keep(list(stems), keep, keep_weekly)
    return sorted(a for stem, a in stems.items() if stem not in kept)


def prune(root, keep, keep_weekly=0):
    """Drop the generations ``select_keep`` does not keep. Returns names removed.

    Kept: the newest ``keep``, one per earlier week for ``keep_weekly`` weeks,
    and - whatever else is true - the newest generation carrying ``OK_MARKER``.
    A run of damaged nights must not push out the last copy that finished clean.

    Takes each dropped generation's ``.tar.age`` with it. That archive is the
    retry cache for ITS generation, kept when an upload fails so a retry need
    not re-encrypt 1.5 GB -- but once the generation is gone no retry can reach
    it, and it is pure disk cost.

    ⚠ This mattered more than it sounds. Only directories were considered here,
    so a RUN of failed uploads left ~1.6 GB behind every night and nothing ever
    collected it -- on the box that runs the trading stack, where a full disk is
    an outage. The trigger is exactly the state this is normally in while the
    offsite remote is being set up, which is when nobody is watching disk.
    """
    gens = sorted((d for d in root.iterdir() if d.is_dir()), reverse=True)
    kept = select_keep([d.name for d in gens], keep, keep_weekly)
    last_good = next((d.name for d in gens if (d / OK_MARKER).is_file()), None)
    if last_good:
        kept.add(last_good)
    dropped = []
    for d in gens:
        if d.name in kept:
            continue
        shutil.rmtree(d, ignore_errors=True)
        (root / f"{d.name}.tar.age").unlink(missing_ok=True)
        dropped.append(d.name)
    return sorted(dropped)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", default=str(pathlib.Path.home() / "backups"))
    ap.add_argument("--keep", type=int, default=KEEP)
    ap.add_argument("--no-offsite", action="store_true",
                    help="skip encrypt + upload (local generation only)")
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

    # Mark, THEN prune. The marker is what prune protects, so a generation with
    # a failure is never the one that survives as "the last good copy" - and the
    # last good copy is never dropped to make room for a damaged one. This used
    # to prune before the failure check.
    if not failures:
        (out / OK_MARKER).write_text(
            dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    dropped = prune(dest_root, args.keep, KEEP_WEEKLY)
    if dropped:
        print(f"pruned {len(dropped)} old generation(s): {', '.join(dropped)}")

    print(f"\n{total / 1e9:.2f} GB of databases, {len(failures)} failure(s)")
    # ── offsite: encrypt, upload, verify ────────────────────────────────────
    #
    # Deliberately AFTER the local generation is complete and pruned. A Drive
    # outage must not cost you the local backup, which is the copy you reach for
    # first. It is still reported as a FAILURE (non-zero exit, so systemd marks
    # the unit failed), because an offsite backup that quietly stopped happening
    # is indistinguishable from one that is working until you need it.
    if not args.no_offsite and not failures:
        recipient = age_recipient()
        if not recipient:
            failures.append(f"offsite: no age identity at {AGE_IDENTITY}")
            print(f"  FAIL offsite: no age identity at {AGE_IDENTITY}")
        else:
            enc = out.parent / (out.name + ".tar.age")
            ok, detail = encrypt_generation(out, enc, recipient)
            print(f"  {'ok  ' if ok else 'FAIL'}encrypted  {detail}")
            if not ok:
                failures.append(f"encrypt: {detail}")
            else:
                ok, detail = upload(enc)
                print(f"  {'ok  ' if ok else 'FAIL'}uploaded   {detail}")
                if not ok:
                    failures.append(f"upload: {detail}")
                else:
                    # Redundant once Drive holds a checksum-verified copy, and it
                    # doubles the disk cost of every generation. Kept on failure
                    # so a retry need not re-encrypt 1.5 GB.
                    enc.unlink(missing_ok=True)
    elif failures:
        print("  --  offsite SKIPPED: the local backup had failures, so there is "
              "nothing worth shipping")

    if failures:
        for f in failures:
            print(f"  ! {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
