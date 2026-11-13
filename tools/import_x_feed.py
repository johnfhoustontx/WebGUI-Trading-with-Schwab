"""One-shot import of the standalone Node X-feed's SQLite file into ``xfeed_svc``.

    python tools/import_x_feed.py --source ~/x-news-feed/data/feed.db [--target PATH] [--force]

The source is NEVER written: it is opened with ``mode=ro`` and a consistent copy
(WAL content included) is taken with ``Connection.backup`` into a temp file; all
reads come from that copy. The target is refused (exit 2) if it already has any
``accounts`` row, unless ``--force``, which empties the four tables inside the
same transaction as the copy (so a failure leaves the old data in place).
"""
import argparse
import pathlib
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

TABLES = ("accounts", "posts", "gaps", "settings")


class ImportRefused(Exception):
    """A precondition failed; nothing was changed."""


def _snapshot(source: pathlib.Path, dest: pathlib.Path) -> None:
    ro = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(dest)
        try:
            ro.backup(copy)
        finally:
            copy.close()
    finally:
        ro.close()


def _columns(c, table):
    return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]


def import_db(source: pathlib.Path, target: pathlib.Path, *, force: bool = False) -> dict[str, int]:
    from services.xfeed_svc.store import Store

    source = pathlib.Path(source).expanduser()
    target = pathlib.Path(target).expanduser()
    if not source.is_file():
        raise ImportRefused(f"source database not found: {source}")
    if source.resolve() == target.resolve():
        raise ImportRefused("source and target are the same file")

    with tempfile.TemporaryDirectory(prefix="import_x_feed_") as tmp:
        snap = pathlib.Path(tmp) / "snapshot.db"
        try:
            _snapshot(source, snap)
        except sqlite3.Error as exc:
            raise ImportRefused(f"cannot read source database {source}: {exc}") from exc

        Store(target).close()  # creates the schema (and parent dir) if missing
        src = sqlite3.connect(snap)
        dst = sqlite3.connect(target, timeout=30, isolation_level=None)
        try:
            have = dst.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            if have and not force:
                raise ImportRefused(
                    f"target {target} already has {have} account(s); use --force to replace")
            plan = {}
            for t in TABLES:
                cols = _columns(src, t)
                if cols != _columns(dst, t):
                    raise ImportRefused(f"table {t!r} has different columns in source and target")
                plan[t] = (cols, src.execute(f"SELECT {', '.join(cols)} FROM {t}").fetchall())
            dst.execute("BEGIN IMMEDIATE")
            try:
                for t in TABLES:
                    dst.execute(f"DELETE FROM {t}")  # a no-op unless --force
                counts = {}
                for t in TABLES:
                    cols, rows = plan[t]
                    dst.executemany(
                        f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
                    counts[t] = dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    if counts[t] != len(rows):
                        raise ImportRefused(f"{t}: copied {counts[t]} rows, source has {len(rows)}")
                dst.execute("COMMIT")
            except BaseException:
                try:
                    dst.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            return counts
        finally:
            dst.close()
            src.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True, help="the Node feed's feed.db (read-only)")
    ap.add_argument("--target", help="xfeed_svc database (default: repo_paths.XFEED_DB)")
    ap.add_argument("--force", action="store_true", help="replace a non-empty target")
    args = ap.parse_args(argv)
    if args.target:
        target = pathlib.Path(args.target)
    else:
        from repo_paths import XFEED_DB as target
    try:
        counts = import_db(pathlib.Path(args.source), target, force=args.force)
    except ImportRefused as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    print(" ".join(f"{t}={counts[t]}" for t in TABLES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
