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
import json
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

# Offsite: encrypt, then upload the CIPHERTEXT. Drive never sees plaintext.
#
# The archive carries live Schwab OAuth tokens, the Schwab API keys, the
# Anthropic key and the notification credentials. Those must not sit in a
# third-party service in the clear, where they can be cached, indexed, synced to
# other devices and remain recoverable after deletion.
AGE_IDENTITY = pathlib.Path.home() / ".config" / "age" / "backup-key.txt"
RCLONE_REMOTE = "gdrive:TradingBackups"
KEEP_REMOTE = 3

# Loose gitignored files that live OUTSIDE the data trees below.
EXTRA_FILES = (
    "shared/appsettings.json",
    "shared/tokens.json",
    "shared/notifications.json",
    "shared/anthropic_key.txt",
    "shared/sentiment_bridge.json",
    "schwab-proxy/proxy_tokens.json",
    "config/env.local.toml",
    # The units' EnvironmentFile -- MEMURAI_PASSWORD, ALPHAVANTAGE_API_KEY,
    # EDGAR_USER_AGENT, anything else read from the process environment. It is
    # loaded with NO leading dash, so a missing one does not degrade: the unit
    # fails to start. It was omitted here while its sibling config/env.local.toml
    # was carried, which meant a restore produced a stack that would not come up
    # and nothing to fix it with.
    ".env",
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


def _md5(path, chunk=1 << 20):
    """Streaming md5 of a local file. Only ever called on the duplicate path,
    where 1.5 GB of hashing buys the one fact that resolves it."""
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _remote_objects(remote):
    """``[{name, size, md5, id}]`` for every object on the remote.

    ``lsjson``, not ``lsf``. ``lsf`` returns a bare list of NAMES, in which two
    objects sharing one name are indistinguishable from two generations -- which
    is exactly the state that cost a restore point. Degrades to ``[]`` rather
    than raising: a listing failure must not lose the upload that preceded it.
    """
    r = subprocess.run(["rclone", "lsjson", remote + "/", "--hash"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return []
    try:
        rows = json.loads(r.stdout or "[]")
    except ValueError:
        return []
    return [{"name": o.get("Name", ""), "size": o.get("Size"),
             "md5": (o.get("Hashes") or {}).get("md5"), "id": o.get("ID")}
            for o in rows]


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
        objects = _remote_objects(remote)

        # ⚠ DUPLICATE NAMES, checked BEFORE the checksum. The destination is
        # Google Drive, where two objects may share one name -- and on
        # 2026-09-04 two did, 30 seconds apart, differing by 92 KB. The cause of
        # the second object was never established; what matters is that the code
        # below assumed a name identifies one object, and both things it does
        # with a name break when it does not:
        #
        #   * ``rclone check`` picks one of them and reports "sizes differ" --
        #     about an upload that was in fact PERFECT. The good object was
        #     hash-verified against the local file afterwards, by hand.
        #   * the retention counted the duplicate as a generation and evicted a
        #     real one offsite, silently, leaving two restore points where the
        #     policy says three.
        #
        # So a duplicate is now reported, loudly and with the object IDs and
        # hashes needed to tell which is which. It is NOT auto-deleted: this is
        # the only offsite copy of the trading record, and "newest" and
        # "largest" -- the obvious rclone dedupe modes -- would BOTH have kept
        # the wrong object that night. Only the local hash can say.
        same_name = [o for o in objects if o["name"] == path.name]
        if len(same_name) > 1:
            mine = _md5(path)
            lines = "; ".join(
                f"id={o['id']} {o['size']}B md5={o['md5']}"
                f"{' <- MATCHES LOCAL' if o['md5'] == mine else ''}"
                for o in same_name)
            return False, (f"DUPLICATE NAME on the remote: {len(same_name)} objects "
                           f"named {path.name}. Local md5={mine}. {lines}. "
                           f"Retention skipped; delete the non-matching object(s) "
                           f"by id, never by newest/largest.")

        c = subprocess.run(["rclone", "check", str(path.parent), remote + "/",
                            "--include", path.name, "--one-way"],
                           capture_output=True, text=True, timeout=3600)
        if c.returncode != 0:
            return False, "uploaded but CHECKSUM MISMATCH: " + (c.stderr or "").strip()[:200]

        # DISTINCT names, not objects: a generation is a name, and counting rows
        # is what let one duplicate evict a real restore point.
        names = sorted({o["name"] for o in objects if o["name"].endswith(".tar.age")})
        for old in names[:-keep] if len(names) > keep else []:
            subprocess.run(["rclone", "deletefile", f"{remote}/{old}"],
                           capture_output=True, text=True, timeout=300)
        return True, f"verified; {min(len(names), keep)} generation(s) offsite"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def prune(root, keep):
    """Drop all but the newest `keep` dated generations. Returns names removed.

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
    dropped = []
    for d in gens[keep:]:
        shutil.rmtree(d, ignore_errors=True)
        (root / f"{d.name}.tar.age").unlink(missing_ok=True)
        dropped.append(d.name)
    return dropped


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

    dropped = prune(dest_root, args.keep)
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
