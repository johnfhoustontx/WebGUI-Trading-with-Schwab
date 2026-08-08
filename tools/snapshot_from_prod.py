"""Snapshot prod's live data into the dev checkout.

Run FROM THE DEV CHECKOUT (the destination). Dev's collectors are off by
default, so this is what makes a dev stack show anything at all: it brings over
every published Redis view and every SQLite store, after which dev renders
exactly what prod renders.

    python tools\\snapshot_from_prod.py [--dry-run] [--redis-only] [--skip-gex]
                                        [--peer PATH]

PROD KEEPS RUNNING throughout. SQLite is copied with the online-backup API
rather than a file copy: a plain copy of a live WAL database is not consistent,
and ``gex_history.db`` is ~1.4 GB with a writer appending to it every minute
during market hours.

Three things could do real damage here, so each has a structural guard rather
than a warning in the docs:

1. **Running in the wrong direction.** The tool only ever writes into the
   checkout it is *run from*, and :func:`assert_destination_is_dev` refuses
   unless that checkout resolves to ``dev``. There is no flag that makes prod a
   destination — ``--peer`` names the SOURCE only.
2. **Flushing the wrong Redis DB.** Both environments share one Memurai server
   and are separated only by logical index, so a wrong index would destroy
   prod's entire published cache. Prod's index is read from PROD's own profile
   (never assumed to be 0) and :func:`assert_distinct_redis_dbs` refuses to run
   when the two resolve to the same number.
3. **Copying a torn database.** See :func:`copy_sqlite`.

Design: docs/plans/2026-08-08-dev-prod-environments-design.md
"""
import argparse
import dataclasses
import json
import pathlib
import shutil
import socket
import sqlite3
import sys
import time
import tomllib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import (  # noqa: E402
    ENV_NAME, MEMURAI_PORT, NICEGUI_PORT, PEER_ROOT, REDIS_DB, REPO_ROOT,
    SERVICE_PORTS,
)

# Repo-relative stores, all gitignored, so a fresh checkout has none of them and
# a missing one is a skip rather than an error.
#
# gex_history.db lives at the APP ROOT, not under data/ — gex_history_db.py sets
# ``DB_PATH = Path(__file__).parent / "gex_history.db"``. There IS a stale 0-byte
# options-scanner/data/gex_history.db on disk; it is not the store and is not
# copied.
GEX_STORE = "options-scanner/gex_history.db"
SQLITE_STORES = [
    GEX_STORE,
    "options-scanner/data/trades.db",
    "options-scanner/data/signals.db",
    "options-scanner/data/paper_account.db",
    "options-scanner/data/paper_account_driver.db",
    "options-scanner/data/gamma_briefings.db",
    "options-scanner/data/daily_trade_log.db",
    "sentiment-dashboard/data/sentiment_intraday.db",
    "sentiment-dashboard/data/sector_pcr.db",
    "sentiment-dashboard/data/momentum.db",
    "sentiment-dashboard/data/market_state.db",
    "services/trade_svc/data/iv_history.db",
]
# Plain files: the scanner watchlist (without it the scanner degrades to base
# symbols and the Net Prem view's SPDR-sector group empties) and the sentiment
# bridge regime_filter reads.
FILE_STORES = [
    "options-scanner/data/Top 20.xlsx",
    "shared/sentiment_bridge.json",
]

# ``cmd:`` covers both the command STREAMS and the ``cmd:*:dead`` dead-letter
# lists. Neither is a published view: a stream is a queue dev would drain and
# EXECUTE on startup (a stranded driver_paper_create or rescue_apply would
# double-open a position), and the dead-letter list is prod's ops backlog, which
# Bus.drain_pending surfaces for review — inheriting it would put prod's
# incidents in front of a dev reader as if they were dev's.
REDIS_EXCLUDE_PREFIXES = ("cmd:",)

DRIVER_CONTROL_KEY = "cache:driver:control"


@dataclasses.dataclass(frozen=True)
class Item:
    src: pathlib.Path
    dst: pathlib.Path
    kind: str          # "sqlite" | "file"


def snapshot_plan(src_root, dst_root, *, skip_gex=False):
    """Every file the snapshot copies, as ``Item``s. PURE — no filesystem access.

    Kept pure and separate from the copying so the decision of WHAT moves is
    unit-testable without a 1.4 GB database on disk. Every item maps the same
    relative path under both roots, which is what makes "did this land where it
    came from" checkable in one assertion.
    """
    src_root, dst_root = pathlib.Path(src_root), pathlib.Path(dst_root)
    plan = []
    for rel in SQLITE_STORES:
        if skip_gex and rel == GEX_STORE:
            continue
        plan.append(Item(src_root / rel, dst_root / rel, "sqlite"))
    for rel in FILE_STORES:
        plan.append(Item(src_root / rel, dst_root / rel, "file"))
    return plan


# --------------------------------------------------------------------- guards

def assert_destination_is_dev():
    """Exit unless THIS checkout is dev. The tool can never overwrite prod.

    Note for tests: under pytest ``repo_paths`` pins ``ENV_NAME`` to ``"prod"``
    regardless of the marker, so this always refuses during a test run — which
    is the correct behavior, and means the allowed branch has to be exercised by
    monkeypatching ``ENV_NAME`` on THIS module.
    """
    if ENV_NAME != "dev":
        sys.exit(
            f"refusing to run: this checkout resolves to '{ENV_NAME}', not 'dev'.\n"
            "Run it from the DEV checkout (the destination). Its config/env.local.toml\n"
            'must contain:  name = "dev"'
        )


def assert_peer_is_usable(peer):
    """Exit unless ``peer`` is a real, DIFFERENT checkout.

    Same-root would back a database up onto itself; a path that is merely a
    directory (an easy typo — a parent, or the data folder) would produce a run
    of "absent in prod" skips that reads like success.
    """
    peer = pathlib.Path(peer)
    if peer.resolve() == pathlib.Path(REPO_ROOT).resolve():
        sys.exit(f"refusing to run: --peer is this same checkout ({peer}).\n"
                 "The peer must be the OTHER (prod) checkout.")
    if not (peer / "repo_paths.py").exists():
        sys.exit(f"refusing to run: {peer} does not look like a checkout "
                 "(no repo_paths.py).")


def assert_distinct_redis_dbs(src_db, dst_db):
    """Exit if source and destination are the same logical Redis DB.

    The copy FLUSHDBs the destination first, so equal indices would wipe the
    source — prod's entire published cache — before reading a byte of it.
    """
    if int(src_db) == int(dst_db):
        sys.exit(
            f"refusing to run: source and destination are both Redis db {src_db}.\n"
            "The copy flushes the destination first, so this would wipe prod's cache.\n"
            "Check redis_db in config/environments.toml in BOTH checkouts."
        )


def assert_dev_stack_is_down():
    """Exit if dev's services are listening — they hold DB handles and would
    write over the restore mid-copy.

    Dev's proxy port is deliberately NOT probed: dev borrows prod's proxy on
    :8100, so a listener there is prod doing its job, not dev being up.
    """
    busy = []
    for label, port in list(SERVICE_PORTS.items()) + [("webgui", NICEGUI_PORT)]:
        if not port:
            continue
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", int(port))) == 0:
                busy.append(f"{label}:{port}")
    if busy:
        sys.exit("refusing to run: dev is still up on " + ", ".join(busy)
                 + "\nStop it first (stop_all.bat), then re-run.")


# ---------------------------------------------------------------------- copies

def copy_sqlite(src, dst):
    """Online backup — safe while the source is being written to.

    The source is opened READ-ONLY, and that is load-bearing rather than
    decorative. Measured on this repo's stores: a read-WRITE open of a WAL
    database checkpoints it and deletes the ``-wal``/``-shm`` sidecars on close,
    i.e. it mutates prod's WAL state. ``mode=ro`` leaves them alone. (It may
    CREATE a ``-shm`` beside prod's database when none exists — a read-only
    connection needs the shared-memory index to see WAL frames — but it never
    touches the data, and prod's own services keep a ``-shm`` there anyway.)

    ``backup()`` runs in a single step (``pages=-1``) on purpose. An incremental
    backup restarts whenever an external writer modifies the source, which
    against a database written every minute could livelock; and in WAL mode a
    reader does not block the writer, so the single step costs prod nothing.
    """
    dst = pathlib.Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{pathlib.Path(src).as_posix()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target, pages=-1)
        finally:
            target.close()
    finally:
        source.close()


def copy_file(src, dst):
    dst = pathlib.Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _disabled_control_envelope(raw):
    """Prod's control value with ``enabled`` forced false, envelope intact.

    ``cache:driver:control`` is stored as a ``CacheEnvelope``
    (``{version, ts, payload}``), NOT the bare control dict — writing the bare
    shape would make ``CacheEnvelope.from_json`` raise inside
    ``driver_svc.handlers.read_control``. The envelope's ``version`` is preserved
    so it stays in lockstep with the ``:ver`` side key copied alongside it.
    Anything unparseable degrades to a minimal valid disabled envelope.
    """
    disabled = {"enabled": False, "halted": False, "reason": None,
                "halted_date": None, "timestamp": None}
    try:
        env = json.loads(raw) if raw else None
        if not isinstance(env, dict) or not isinstance(env.get("payload"), dict):
            raise ValueError("not a cache envelope")
        payload = dict(env["payload"])
        payload["enabled"] = False
        env = dict(env)
        env["payload"] = payload
        env.setdefault("version", 1)
        env.setdefault("ts", "1970-01-01T00:00:00+00:00")
    except Exception:  # noqa: BLE001 — absent, non-JSON, or a foreign shape.
        env = {"version": 1, "ts": "1970-01-01T00:00:00+00:00", "payload": disabled}
    return json.dumps(env).encode()


def copy_redis(src, dst):
    """DUMP/RESTORE every key from prod's DB into dev's, preserving type + TTL.

    DUMP/RESTORE rather than GET/SET so a non-string value (a stream, hash or
    list) survives as itself. Two deliberate departures from a straight copy:

    * ``cmd:*`` is skipped — see ``REDIS_EXCLUDE_PREFIXES``;
    * ``cache:driver:control`` is rewritten disabled, so a snapshot taken while
      the autonomous driver was armed cannot arm it in dev. (Task 6 added an
      independent guard in ``driver_svc``; two defences is the right number for
      something that trades.) It is written by hand rather than restored and
      then overwritten, so prod's armed value is never even transiently present.

    The caller is responsible for having checked that ``src`` and ``dst`` are
    different logical DBs — this flushes ``dst`` first.
    """
    dst.flushdb()
    control_raw = None
    for key in src.scan_iter(match="*", count=500):
        name = key.decode() if isinstance(key, bytes) else str(key)
        if name.startswith(REDIS_EXCLUDE_PREFIXES):
            continue
        if name == DRIVER_CONTROL_KEY:
            control_raw = src.get(key)
            continue
        payload = src.dump(key)
        if payload is None:                      # expired between scan and dump
            continue
        pttl = src.pttl(key)
        dst.restore(key, pttl if pttl and pttl > 0 else 0, payload, replace=True)
    dst.set(DRIVER_CONTROL_KEY, _disabled_control_envelope(control_raw))


# ------------------------------------------------------------------- peer read

def peer_redis_db(peer):
    """PROD's Redis DB index, read from ITS ``environments.toml`` + marker.

    Never assumed to be 0: the peer is whichever checkout ``--peer``/``peer_root``
    names, and reading its own profile is what makes the
    :func:`assert_distinct_redis_dbs` comparison meaningful rather than circular.
    Mirrors ``repo_paths._read_env_marker``: ``utf-8-sig`` (PowerShell's ``>``
    writes UTF-8 *with BOM*), a name absent from the profile table falls back to
    ``prod``, and anything unreadable falls back to 0 — the source-side default,
    and the safe direction, since a wrong SOURCE reads the wrong data whereas a
    wrong DESTINATION would destroy it.
    """
    peer = pathlib.Path(peer)

    def _load(name):
        try:
            return tomllib.loads(
                (peer / "config" / name).read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001 — missing file, bad TOML, encoding.
            return {}

    env_name = str(_load("env.local.toml").get("name") or "prod").strip().lower()
    profiles = _load("environments.toml")
    if env_name not in profiles:
        env_name = "prod"
    try:
        return int((profiles.get(env_name) or {}).get("redis_db", 0) or 0)
    except (TypeError, ValueError):
        return 0


# ----------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Copy prod's Redis views and SQLite stores into this (dev) checkout.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the plan and sizes; write nothing")
    ap.add_argument("--redis-only", action="store_true",
                    help="skip the file/SQLite copies")
    ap.add_argument("--skip-gex", action="store_true",
                    help="skip gex_history.db (the ~1.4 GB one)")
    ap.add_argument("--peer", default=None,
                    help="prod checkout root (else peer_root from config/env.local.toml)")
    args = ap.parse_args(argv)

    # FIRST, before anything reads or writes: this checkout must be dev.
    assert_destination_is_dev()

    peer = pathlib.Path(args.peer) if args.peer else PEER_ROOT
    if not peer or not pathlib.Path(peer).exists():
        sys.exit("no prod checkout: set peer_root in config/env.local.toml, "
                 "or pass --peer")
    peer = pathlib.Path(peer)
    assert_peer_is_usable(peer)

    src_db, dst_db = peer_redis_db(peer), int(REDIS_DB)
    assert_distinct_redis_dbs(src_db, dst_db)

    if not args.dry_run:
        assert_dev_stack_is_down()

    print(f"snapshot  {peer}  ->  {REPO_ROOT}"
          # ASCII only in printed output: the launchers redirect stdout to
          # logs/<name>.out.log under a cp1252 console, where a non-ASCII
          # character mojibakes at best and raises UnicodeEncodeError at worst.
          + ("   (dry run - nothing is written)" if args.dry_run else ""))

    if not args.redis_only:
        total = 0.0
        for item in snapshot_plan(peer, REPO_ROOT, skip_gex=args.skip_gex):
            if not item.src.exists():
                print(f"  skip        {item.src.name} (absent in prod)")
                continue
            mb = item.src.stat().st_size / 1e6
            total += mb
            if args.dry_run:
                print(f"  would copy  {item.src.name}  {mb:,.1f} MB")
                continue
            t0 = time.monotonic()
            (copy_sqlite if item.kind == "sqlite" else copy_file)(item.src, item.dst)
            print(f"  copied      {item.src.name}  {mb:,.1f} MB  "
                  f"{time.monotonic() - t0:.1f}s")
        print(f"  {'would copy' if args.dry_run else 'copied'} {total:,.1f} MB of stores")

    print(f"  redis       db {src_db} -> db {dst_db} on :{MEMURAI_PORT}"
          + ("  (dry run)" if args.dry_run else ""))
    if not args.dry_run:
        import redis
        t0 = time.monotonic()
        copy_redis(redis.Redis(port=MEMURAI_PORT, db=src_db),
                   redis.Redis(port=MEMURAI_PORT, db=dst_db))
        print(f"              done in {time.monotonic() - t0:.1f}s")
    print("done.")


if __name__ == "__main__":
    main()
