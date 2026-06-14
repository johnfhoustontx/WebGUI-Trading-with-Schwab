r"""db_admin.py - maintenance CLI for the option-trade SQLite databases.

Targets the three DBs in options-scanner/data/:
    trades.db             paper-trade lifecycle (trades + trade_events)
    trade_performance.db  stream-derived perf_events + perf_iv_snapshots
    signals.db            captured scanner signals + marks/outcomes

Subcommands:
    status     row counts, file/WAL size, and date range per table (read-only)
    reset      full wipe + reinit empty schema (auto-backup + safety rails)
    backup     timestamped copy of each DB
    vacuum     VACUUM each DB to reclaim space
    integrity  PRAGMA integrity_check per DB

Schema is never duplicated here — reset reinitialises each DB by calling the
owning app's own init function, so this tool can never drift from the apps.

Paths and ports come from repo_paths.py — no hard-coded D:\ paths.
"""
import argparse
import datetime as _dt
import shutil
import sqlite3
import sys
import pathlib
from dataclasses import dataclass, field
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import OPTIONS_SCANNER  # noqa: E402

DATA_DIR = OPTIONS_SCANNER / "data"
BACKUP_DIR = DATA_DIR / "backups"
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


@dataclass
class DbTarget:
    name: str
    path: pathlib.Path
    init: Callable[[], None]
    # table -> timestamp column used to report a date range (omit if none)
    ts_columns: dict = field(default_factory=dict)


#############################################
# TARGET REGISTRY
#############################################

def default_targets():
    """Build the three option-trade DB targets, wiring each app's own init.

    Imports happen lazily and with options-scanner on sys.path so the schema
    stays single-sourced in the owning modules.
    """
    sys.path.insert(0, str(OPTIONS_SCANNER))
    import trades_db
    import trade_performance_db
    import signal_db

    return [
        DbTarget(
            name="trades.db",
            path=DATA_DIR / "trades.db",
            init=lambda: trades_db.init_db(DATA_DIR / "trades.db"),
            ts_columns={"trades": "entry_time", "trade_events": "timestamp"},
        ),
        DbTarget(
            name="trade_performance.db",
            path=DATA_DIR / "trade_performance.db",
            init=lambda: trade_performance_db.init_schema(
                DATA_DIR / "trade_performance.db"),
            ts_columns={"perf_events": "ts", "perf_iv_snapshots": "ts"},
        ),
        DbTarget(
            name="signals.db",
            path=DATA_DIR / "signals.db",
            init=lambda: signal_db.init_db(DATA_DIR / "signals.db"),
            ts_columns={"signals": "first_seen_ts", "signal_marks": "mark_ts"},
        ),
    ]


def select_targets(targets, spec):
    """Filter targets by a comma-separated spec of bare names (no .db needed)."""
    if not spec:
        return targets
    wanted = {s.strip().removesuffix(".db") for s in spec.split(",") if s.strip()}
    by_key = {t.name.removesuffix(".db"): t for t in targets}
    unknown = wanted - by_key.keys()
    if unknown:
        raise ValueError(
            f"unknown db name(s): {', '.join(sorted(unknown))}. "
            f"choices: {', '.join(sorted(by_key))}")
    return [by_key[k] for k in wanted]


#############################################
# STATUS
#############################################

def _table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'").fetchall()
    return [r[0] for r in rows]


def db_status(target: DbTarget):
    info = {"name": target.name, "path": str(target.path), "exists": False,
            "size_bytes": 0, "wal_bytes": 0, "tables": {}}
    if not target.path.exists():
        return info
    info["exists"] = True
    info["size_bytes"] = target.path.stat().st_size
    wal = target.path.with_name(target.path.name + "-wal")
    if wal.exists():
        info["wal_bytes"] = wal.stat().st_size

    conn = sqlite3.connect(target.path)
    try:
        for table in _table_names(conn):
            rows = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            entry = {"rows": rows, "min_ts": None, "max_ts": None}
            ts_col = target.ts_columns.get(table)
            if ts_col and rows:
                lo, hi = conn.execute(
                    f"SELECT MIN({ts_col}), MAX({ts_col}) FROM {table}"
                ).fetchone()
                entry["min_ts"], entry["max_ts"] = lo, hi
            info["tables"][table] = entry
    finally:
        conn.close()
    return info


#############################################
# BACKUP / RESET / VACUUM / INTEGRITY
#############################################

def _utc_stamp():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_db(target: DbTarget, backup_dir: pathlib.Path,
              stamp: Optional[str] = None) -> Optional[pathlib.Path]:
    """Copy the DB to backup_dir/<name>.<stamp>.db. Returns None if no DB."""
    if not target.path.exists():
        return None
    backup_dir = pathlib.Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or _utc_stamp()
    dest = backup_dir / f"{target.path.stem}.{stamp}.db"
    # sqlite backup API flushes WAL into the copy → consistent snapshot
    src = sqlite3.connect(target.path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def _remove_db_files(path: pathlib.Path):
    if path.exists():
        path.unlink()
    for suffix in SIDECAR_SUFFIXES:
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def reset_db(target: DbTarget, backup: bool = True,
             backup_dir: pathlib.Path = BACKUP_DIR,
             stamp: Optional[str] = None) -> Optional[pathlib.Path]:
    """Full wipe + reinit. Optionally back up first. Returns backup path."""
    backup_path = backup_db(target, backup_dir, stamp) if backup else None
    _remove_db_files(target.path)
    target.init()
    return backup_path


def vacuum_db(target: DbTarget):
    if not target.path.exists():
        return
    conn = sqlite3.connect(target.path)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


def integrity_db(target: DbTarget) -> str:
    if not target.path.exists():
        return "missing"
    conn = sqlite3.connect(target.path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


#############################################
# CLI
#############################################

def _print_status(targets):
    for t in targets:
        info = db_status(t)
        if not info["exists"]:
            print(f"\n{t.name}: (does not exist)")
            continue
        size = info["size_bytes"] / 1024
        wal = info["wal_bytes"] / 1024
        print(f"\n{t.name}  -  {size:.1f} KB  (WAL {wal:.1f} KB)")
        for table, e in info["tables"].items():
            span = ""
            if e["min_ts"]:
                span = f"   [{e['min_ts']} .. {e['max_ts']}]"
            print(f"    {table:<20} {e['rows']:>8} rows{span}")


def _running_processes_warning():
    """Return a human message if scanner/proxy processes look alive, else ''."""
    try:
        import check_env  # sibling module
    except Exception:
        return ""
    scripts = ("dashboard.py", "schwab_proxy.py")
    alive = []
    try:
        import psutil
        for proc in psutil.process_iter(["cmdline"]):
            cmd = proc.info.get("cmdline") or []
            for s in scripts:
                if check_env.cmdline_matches(cmd, s):
                    alive.append(s)
    except Exception:
        return ""
    if alive:
        return ("processes still running: " + ", ".join(sorted(set(alive))))
    return ""


def _cmd_status(args, targets):
    _print_status(targets)
    return 0


def _cmd_reset(args, targets):
    warning = _running_processes_warning()
    if warning and not args.force:
        print(f"REFUSING to reset - {warning}.")
        print("Stop those apps first, or re-run with --force.")
        return 2

    print("About to WIPE and reinitialise (empty schema):")
    for t in targets:
        print(f"    {t.name}")
    if not args.no_backup:
        print(f"A backup will be written to {BACKUP_DIR} first.")
    else:
        print("WARNING: --no-backup given; no backup will be taken.")

    if not args.yes:
        reply = input("Type 'reset' to confirm: ").strip().lower()
        if reply != "reset":
            print("Aborted.")
            return 1

    stamp = _utc_stamp()
    for t in targets:
        backup_path = reset_db(t, backup=not args.no_backup, stamp=stamp)
        msg = f"  reset {t.name}"
        if backup_path:
            msg += f"  (backed up -> {backup_path.name})"
        print(msg)
    print("Done.")
    return 0


def _cmd_backup(args, targets):
    stamp = _utc_stamp()
    for t in targets:
        path = backup_db(t, BACKUP_DIR, stamp)
        print(f"  {t.name}: " + (f"-> {path}" if path else "(no DB, skipped)"))
    return 0


def _cmd_vacuum(args, targets):
    for t in targets:
        vacuum_db(t)
        print(f"  vacuumed {t.name}")
    return 0


def _cmd_integrity(args, targets):
    rc = 0
    for t in targets:
        result = integrity_db(t)
        print(f"  {t.name}: {result}")
        if result not in ("ok", "missing"):
            rc = 1
    return rc


# Ordered actions for the interactive menu: (key, label).
MENU_ACTIONS = [
    ("status", "Status      - row counts, sizes, date ranges (read-only)"),
    ("integrity", "Integrity   - PRAGMA integrity_check per DB"),
    ("backup", "Backup      - timestamped copy of each DB"),
    ("vacuum", "Vacuum      - reclaim space after deletes"),
    ("reset", "Reset       - FULL WIPE + reinit (auto-backup, confirm)"),
]


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default="",
                   help="comma-separated subset, e.g. 'trades,signals' "
                        "(default: all three)")
    # command is optional: with none, an interactive menu opens.
    sub = p.add_subparsers(dest="command", required=False)

    sub.add_parser("menu", help="interactive menu (default if no command)")
    sub.add_parser("status", help="row counts, sizes, date ranges")

    r = sub.add_parser("reset", help="full wipe + reinit empty schema")
    r.add_argument("--no-backup", action="store_true",
                   help="skip the pre-wipe backup (dangerous)")
    r.add_argument("--yes", action="store_true",
                   help="skip the typed confirmation prompt")
    r.add_argument("--force", action="store_true",
                   help="reset even if scanner/proxy processes are running")

    sub.add_parser("backup", help="timestamped copy of each DB")
    sub.add_parser("vacuum", help="VACUUM each DB to reclaim space")
    sub.add_parser("integrity", help="PRAGMA integrity_check per DB")
    return p


_DISPATCH = {
    "status": _cmd_status,
    "reset": _cmd_reset,
    "backup": _cmd_backup,
    "vacuum": _cmd_vacuum,
    "integrity": _cmd_integrity,
}


def _reset_args_interactive():
    """Prompt for the reset flags and return a matching argparse-style ns."""
    no_backup = input("  Skip pre-wipe backup? [y/N]: ").strip().lower() == "y"
    force = input("  Force even if apps are running? [y/N]: ").strip().lower() == "y"
    # The typed 'reset' confirmation in _cmd_reset still applies (yes=False).
    return argparse.Namespace(no_backup=no_backup, force=force, yes=False)


def run_menu(all_targets):
    """Interactive loop. `all_targets` is the full target list (DB filtering
    happens per-action inside the menu)."""
    db_spec = ""  # empty = all DBs
    while True:
        targets = select_targets(all_targets, db_spec)
        scope = db_spec or "all (" + ", ".join(t.name for t in all_targets) + ")"
        print("\n=== Option-Trade DB Admin ===")
        print(f"  scope: {scope}")
        for i, (_key, label) in enumerate(MENU_ACTIONS, start=1):
            print(f"  {i}. {label}")
        print("  d. Change DB scope")
        print("  q. Quit")
        choice = input("Select: ").strip().lower()

        if choice in ("q", "quit", "0", ""):
            print("Bye.")
            return 0
        if choice == "d":
            raw = input("  DBs (comma-separated, blank=all): ").strip()
            try:
                select_targets(all_targets, raw)  # validate
                db_spec = raw
            except ValueError as e:
                print(f"  {e}")
            continue
        if not choice.isdigit() or not (1 <= int(choice) <= len(MENU_ACTIONS)):
            print("  Unknown choice.")
            continue

        key = MENU_ACTIONS[int(choice) - 1][0]
        args = (_reset_args_interactive() if key == "reset"
                else argparse.Namespace())
        _DISPATCH[key](args, targets)


def main(argv=None):
    args = build_parser().parse_args(argv)
    all_targets = default_targets()

    if args.command in (None, "menu"):
        return run_menu(all_targets)

    try:
        targets = select_targets(all_targets, args.db)
    except ValueError as e:
        print(f"error: {e}")
        return 2
    return _DISPATCH[args.command](args, targets)


if __name__ == "__main__":
    raise SystemExit(main())
