"""xfeed.db - accounts, posts, gaps and settings, ported from the Node feed's ``src/db.js``.

The schema text is the Node app's, verbatim: production opens a database that
``node:sqlite`` created, so the two must agree on every column. JSON columns are
text; writes use ``json.dumps`` with JSON.stringify's compact separators so a
row reads back identically through either runtime.

``db_path=None`` resolves ``repo_paths.XFEED_DB`` at CALL time (so the pytest
connect guard can redirect it). ``":memory:"`` is allowed.

Every method holds one RLock (a single shared connection,
``check_same_thread=False``); every write is an explicit ``BEGIN IMMEDIATE`` ...
``COMMIT``.
"""
import contextlib
import json
import pathlib
import sqlite3
import threading
from functools import cmp_to_key

_BUSY_TIMEOUT_MS = 30000

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  handle TEXT PRIMARY KEY,
  display_name TEXT,
  avatar_url TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  added_at INTEGER NOT NULL,
  baseline_at INTEGER NOT NULL,
  last_checked_at INTEGER,
  last_ok_at INTEGER,
  last_error TEXT,
  fail_count INTEGER NOT NULL DEFAULT 0,
  not_found_streak INTEGER NOT NULL DEFAULT 0,
  next_check_at INTEGER NOT NULL DEFAULT 0,
  group_name TEXT
);
CREATE TABLE IF NOT EXISTS posts (
  account_handle TEXT NOT NULL,
  status_id TEXT NOT NULL,
  first_seen_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  sort_at INTEGER NOT NULL,
  origin TEXT NOT NULL,
  type TEXT NOT NULL,
  text TEXT,
  author_name TEXT,
  author_handle TEXT,
  author_avatar TEXT,
  url TEXT,
  cashtags TEXT,
  media TEXT,
  quote TEXT,
  counts TEXT,
  raw TEXT,
  PRIMARY KEY (account_handle, status_id)
);
CREATE INDEX IF NOT EXISTS posts_sort ON posts(sort_at);
CREATE INDEX IF NOT EXISTS posts_account ON posts(account_handle);
CREATE TABLE IF NOT EXISTS gaps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_handle TEXT NOT NULL,
  from_at INTEGER NOT NULL,
  to_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

_INSERT_POST = """INSERT OR IGNORE INTO posts
    (account_handle, status_id, first_seen_at, created_at, sort_at, origin, type, text, author_name,
     author_handle, author_avatar, url, cashtags, media, quote, counts, raw)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""


def _dumps(v) -> str:
    """JSON.stringify-compatible text (compact, no ASCII escaping)."""
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def _to_account(r):
    if r is None:
        return None
    return {
        "handle": r["handle"],
        "display_name": r["display_name"],
        "avatar_url": r["avatar_url"],
        "active": bool(r["active"]),
        "added_at": r["added_at"],
        "baseline_at": r["baseline_at"],
        "last_checked_at": r["last_checked_at"],
        "last_ok_at": r["last_ok_at"],
        "last_error": r["last_error"],
        "fail_count": r["fail_count"],
        "not_found_streak": r["not_found_streak"],
        "next_check_at": r["next_check_at"],
        "group_name": r["group_name"],
    }


def _to_feed_post(r) -> dict:
    return {
        "kind": "post",
        "account_handle": r["account_handle"],
        "status_id": r["status_id"],
        "first_seen_at": r["first_seen_at"],
        "created_at": r["created_at"],
        "sort_at": r["sort_at"],
        "origin": r["origin"],
        "type": r["type"],
        "text": r["text"],
        "author_name": r["author_name"],
        "author_handle": r["author_handle"],
        "author_avatar": r["author_avatar"],
        "url": r["url"],
        "cashtags": json.loads(r["cashtags"] or "[]"),
        "media": json.loads(r["media"] or "[]"),
        "quote": json.loads(r["quote"] or "null"),
        "counts": json.loads(r["counts"] or "{}"),
    }


def _to_gap(r) -> dict:
    return {"kind": "gap", "id": r["id"], "account_handle": r["account_handle"],
            "from_at": r["from_at"], "to_at": r["to_at"], "sort_at": r["to_at"]}


def _feed_order(x, y):
    # Newest first; on a tie a post sorts before a gap (the Node comparator).
    d = y["sort_at"] - x["sort_at"]
    if d:
        return d
    return 1 if x["kind"] == "gap" else -1


class Store:
    def __init__(self, db_path=None):
        if db_path is None:
            from repo_paths import XFEED_DB
            db_path = XFEED_DB
        memory = str(db_path) == ":memory:"
        if not memory:
            db_path = pathlib.Path(db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # isolation_level=None: no implicit transactions; every write is the
        # explicit BEGIN IMMEDIATE in _write().
        self._c = sqlite3.connect(str(db_path), check_same_thread=False,
                                  timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        self._c.row_factory = sqlite3.Row
        self._c.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        if not memory:
            self._c.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        """Create anything missing. Every statement is IF NOT EXISTS, so a
        database the Node app wrote is left as it is."""
        with self._lock:
            self._c.executescript(SCHEMA)

    @contextlib.contextmanager
    def _write(self):
        with self._lock:
            self._c.execute("BEGIN IMMEDIATE")
            try:
                yield self._c
                self._c.execute("COMMIT")
            except BaseException:
                # COMMIT is inside the guard: a failed commit can leave the
                # transaction open, and every later BEGIN would then fail.
                with contextlib.suppress(sqlite3.Error):
                    self._c.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock:
            self._c.close()

    # -- accounts ---------------------------------------------------------

    def upsert_account(self, handle, *, display_name, avatar_url, now, group_name=None) -> None:
        """Insert or reactivate. A missing group_name keeps the existing group."""
        with self._write() as c:
            c.execute(
                """INSERT INTO accounts (handle, display_name, avatar_url, active, added_at, baseline_at, group_name)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(handle) DO UPDATE SET display_name = excluded.display_name,
                  avatar_url = excluded.avatar_url, active = 1, baseline_at = excluded.baseline_at,
                  group_name = COALESCE(excluded.group_name, group_name),
                  last_error = NULL, fail_count = 0, not_found_streak = 0, next_check_at = 0""",
                (handle, display_name, avatar_url, now, now, group_name))

    def set_account_group(self, handle, group_name) -> None:
        with self._write() as c:
            c.execute("UPDATE accounts SET group_name = ? WHERE handle = ?", (group_name, handle))

    def get_account(self, handle):
        with self._lock:
            return _to_account(self._c.execute(
                "SELECT * FROM accounts WHERE handle = ?", (handle,)).fetchone())

    def list_accounts(self, *, active_only=False) -> list:
        sql = f"SELECT * FROM accounts {'WHERE active = 1' if active_only else ''} ORDER BY added_at, handle"
        with self._lock:
            return [_to_account(r) for r in self._c.execute(sql).fetchall()]

    def deactivate_account(self, handle) -> None:
        with self._write() as c:
            c.execute("UPDATE accounts SET active = 0 WHERE handle = ?", (handle,))

    def record_check(self, handle, *, now, ok, error=None, not_found_streak=0, next_check_at=0) -> None:
        with self._write() as c:
            if ok:
                c.execute("""UPDATE accounts SET last_checked_at = ?, last_ok_at = ?, last_error = NULL,
                    fail_count = 0, not_found_streak = 0, next_check_at = ? WHERE handle = ?""",
                          (now, now, next_check_at, handle))
            else:
                c.execute("""UPDATE accounts SET last_checked_at = ?, last_error = ?, fail_count = fail_count + 1,
                    not_found_streak = ?, next_check_at = ? WHERE handle = ?""",
                          (now, error, not_found_streak, next_check_at, handle))

    # -- posts ------------------------------------------------------------

    def seen_ids(self, handle, ids) -> set:
        ids = list(ids)
        if not ids:
            return set()
        with self._lock:
            rows = self._c.execute(
                f"SELECT status_id FROM posts WHERE account_handle = ? AND status_id IN ({','.join('?' * len(ids))})",
                (handle, *ids)).fetchall()
        return {r["status_id"] for r in rows}

    def insert_posts(self, posts) -> int:
        with self._write() as c:
            n = 0
            for p in posts:
                cur = c.execute(_INSERT_POST, (
                    p["account_handle"], p["status_id"], p["first_seen_at"], p["created_at"], p["sort_at"],
                    p["origin"], p["type"], p.get("text"), p.get("author_name"), p.get("author_handle"),
                    p.get("author_avatar"), p.get("url"),
                    _dumps(p.get("cashtags") or []), _dumps(p.get("media") or []),
                    _dumps(p.get("quote")), _dumps(p.get("counts") or {}), _dumps(p.get("raw"))))
                n += cur.rowcount
            return n

    def get_posts(self, handle, ids) -> list:
        """Specific feed rows (used to broadcast freshly inserted posts)."""
        ids = list(ids)
        if not ids:
            return []
        with self._lock:
            rows = self._c.execute(
                f"""SELECT * FROM posts WHERE account_handle = ? AND status_id IN ({','.join('?' * len(ids))})
                ORDER BY sort_at DESC, status_id DESC""", (handle, *ids)).fetchall()
        return [_to_feed_post(r) for r in rows]

    def insert_gap(self, *, handle, from_at, to_at, now) -> dict:
        with self._write() as c:
            cur = c.execute(
                "INSERT INTO gaps (account_handle, from_at, to_at, created_at) VALUES (?, ?, ?, ?)",
                (handle, from_at, to_at, now))
            return _to_gap({"id": cur.lastrowid, "account_handle": handle, "from_at": from_at, "to_at": to_at})

    def query_feed(self, *, account=None, group=None, ungrouped=False, type=None, q=None,
                   ticker=None, before=None, before_key=None, limit=300) -> list:
        """Newest-first feed. Paginate with before = last row's sort_at and
        before_key = "status_id:account_handle", so rows sharing a sort_at are
        never skipped."""
        where = ["p.origin != 'baseline'", "a.active = 1"]
        args = []
        group_where = []
        group_args = []
        if group:
            group_where.append("a.group_name = ? COLLATE NOCASE")
            group_args.append(group)
        elif ungrouped:
            group_where.append("a.group_name IS NULL")
        where += group_where
        args += group_args
        if account:
            where.append("p.account_handle = ?")
            args.append(account)
        if type:
            where.append("p.type = ?")
            args.append(type)
        if q:
            where.append("(p.text LIKE ? OR p.author_handle LIKE ? OR p.author_name LIKE ?)")
            like = f"%{q}%"
            args += [like, like, like]
        if ticker:
            t = str(ticker)
            if t.startswith("$"):
                t = t[1:]
            where.append("p.cashtags LIKE ?")
            args.append(f'%"{t.upper()}"%')
        if before is not None and before_key:
            bk = str(before_key)
            sep = bk.rfind(":")
            sid = bk[:sep]
            acct = bk[sep + 1:]
            where.append("(p.sort_at < ? OR (p.sort_at = ? AND (p.status_id < ? OR (p.status_id = ? AND p.account_handle < ?))))")
            args += [int(before), int(before), sid, sid, acct]
        elif before is not None:
            where.append("p.sort_at < ?")
            args.append(int(before))
        with self._lock:
            rows = [_to_feed_post(r) for r in self._c.execute(
                f"""SELECT p.* FROM posts p JOIN accounts a ON a.handle = p.account_handle
                WHERE {' AND '.join(where)} ORDER BY p.sort_at DESC, p.status_id DESC, p.account_handle DESC LIMIT ?""",
                (*args, int(limit))).fetchall()]

            # Gap markers only make sense in the unfiltered timeline (or one account's).
            if type or q or ticker:
                return rows
            g_where = ["a.active = 1", *group_where]
            g_args = list(group_args)
            if account:
                g_where.append("g.account_handle = ?")
                g_args.append(account)
            if before is not None:
                g_where.append("g.to_at < ?")
                g_args.append(int(before))
            if len(rows) >= limit:
                g_where.append("g.to_at >= ?")
                g_args.append(rows[-1]["sort_at"])
            gaps = [_to_gap(r) for r in self._c.execute(
                f"SELECT g.* FROM gaps g JOIN accounts a ON a.handle = g.account_handle WHERE {' AND '.join(g_where)}",
                g_args).fetchall()]
        if not gaps:
            return rows
        return sorted([*rows, *gaps], key=cmp_to_key(_feed_order))

    def account_counts(self) -> dict:
        with self._lock:
            rows = self._c.execute(
                """SELECT p.account_handle AS h, COUNT(*) AS n FROM posts p JOIN accounts a ON a.handle = p.account_handle
                WHERE a.active = 1 AND p.origin != 'baseline' GROUP BY p.account_handle""").fetchall()
        return {r["h"]: r["n"] for r in rows}

    # -- settings ---------------------------------------------------------

    def get_setting(self, key, fallback=None):
        with self._lock:
            r = self._c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(r["value"]) if r else fallback

    def set_setting(self, key, value) -> None:
        with self._write() as c:
            c.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, _dumps(value)))
