"""news.db — items, per-feed HTTP state, and the EDGAR accessions already seen.

``db_path=None`` resolves ``repo_paths.NEWS_DB`` at CALL time (the shape CLAUDE.md
prefers, so the pytest connect guard can redirect it).

Merging, which is the part worth reading before changing anything here:

* An incoming item whose **id** is already stored is not a new row. Its tickers
  are unioned into the stored row (the same Yahoo story arrives on the NVDA and
  AMD feeds under one URL, and must end up tagged with both), and its source is
  added to ``sources`` when new.
* Otherwise an item whose **title key** matches a row published within a day is
  the same story under another URL - but only ACROSS feeds. A feed whose name is
  already in that row's ``sources`` never merges into it, because two stories
  from one feed with one title are two stories (a daily "Morning Bid" column).
* A title-merged item's id is kept in ``aliases``, pointing at the row it
  merged into, so the same feed re-serving that story on the next poll is an
  id match - not, since its feed is now in ``sources``, a brand-new row.
* ``items.title_key`` returns ``None`` for a title too short to identify a story;
  such an item is never title-merged and is stored as its own row, which is why
  ``title_key`` is NULLABLE (``INSERT OR IGNORE`` silently drops a NOT NULL
  violation - the item would simply vanish).
* A merge sets the ``public`` column to (stored OR incoming). The column is the
  ingest-time record only: the PUBLIC VIEW is decided by ``public_sources`` - the
  feeds public NOW - at every read, so a feed turned private disappears from it at
  once, and a merged row names only its public feeds there.

Writes: every write opens ``BEGIN IMMEDIATE`` and either commits or rolls back
whole. IMMEDIATE takes the write lock BEFORE the id / title lookups, so two Store
instances on one file (the scheduler and a ``news_refresh`` command) serialise -
neither can miss the other's uncommitted row and store the story twice. A failure
mid-batch leaves nothing of that batch and no open transaction behind it.

Times: every adapter hands over UTC ISO strings. Ordering and pruning compare
``julianday(...)`` rather than the raw strings, so a fractional-seconds stamp
sorts by its instant. An unparseable ``published_at`` sorts last and is pruned by
``first_seen`` - our own clock - so it can never be kept forever.
"""
import contextlib
import datetime as dt
import json
import pathlib
import sqlite3
import threading

from services.news_svc import items

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, sources TEXT NOT NULL,
    original_source TEXT, title TEXT NOT NULL, title_key TEXT,
    teaser TEXT, url TEXT NOT NULL, published_at TEXT NOT NULL, first_seen TEXT NOT NULL,
    tickers TEXT NOT NULL, kind TEXT NOT NULL, topics TEXT NOT NULL,
    detail TEXT NOT NULL, public INTEGER NOT NULL
);
DROP INDEX IF EXISTS idx_items_published;
CREATE INDEX IF NOT EXISTS idx_items_published_jd ON items(julianday(published_at));
CREATE INDEX IF NOT EXISTS idx_items_title_key ON items(title_key);
CREATE TABLE IF NOT EXISTS feed_state (
    name TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, last_ok TEXT,
    last_poll TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS aliases (id TEXT PRIMARY KEY, item_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seen_accessions (accession TEXT PRIMARY KEY, seen TEXT NOT NULL);
"""

_JSON_COLS = ("sources", "tickers", "topics", "detail")
_BUSY_TIMEOUT_MS = 10_000
_TITLE_MERGE_DAYS = 1.0
# One instant written as ``Z`` or ``+00:00`` must tie on julianday and fall to
# first_seen, so there is deliberately no raw-string key between the two.
_ORDER = "ORDER BY julianday(published_at) DESC, first_seen DESC"
_IN_CHUNK = 500


def _names(value):
    """A collection of feed names, or None. A bare str is ONE name - never its
    characters."""
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return list(value)


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _union(existing, incoming) -> list:
    out = list(existing)
    for v in incoming:
        if v not in out:
            out.append(v)
    return out


class Store:
    def __init__(self, db_path=None):
        if db_path is None:
            from repo_paths import NEWS_DB
            db_path = NEWS_DB
        db_path = pathlib.Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # isolation_level=None: no implicit transactions, so every write is the
        # explicit BEGIN IMMEDIATE in _write(). ``timeout`` is the ONE busy wait.
        self._c = sqlite3.connect(str(db_path), check_same_thread=False,
                                  timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    @contextlib.contextmanager
    def _write(self):
        """One atomic write: BEGIN IMMEDIATE, then COMMIT, or ROLLBACK and re-raise."""
        with self._lock:
            self._c.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._c.rollback()
                raise
            self._c.commit()

    # ── items ──────────────────────────────────────────────────────────────
    def _merge_into(self, row, it) -> None:
        """Union ``it``'s tickers and source into the stored ``row``; ``public``
        becomes stored OR incoming."""
        tickers = json.loads(row["tickers"])
        sources = json.loads(row["sources"])
        new_tickers = _union(tickers, it.get("tickers") or [])
        new_sources = _union(sources, [it["source"]])
        public = int(bool(row["public"]) or bool(it.get("public")))
        if new_tickers != tickers or new_sources != sources or public != row["public"]:
            self._c.execute("UPDATE items SET tickers=?, sources=?, public=? WHERE id=?",
                            (_dumps(new_tickers), _dumps(new_sources), public, row["id"]))

    def _title_match(self, it, key):
        """The row this item is the same story as, from ANOTHER feed, or None."""
        if key is None:
            return None
        candidates = self._c.execute(
            "SELECT id, sources, tickers, public FROM items WHERE title_key=? AND "
            "abs(julianday(published_at)-julianday(?)) < ? "
            "ORDER BY abs(julianday(published_at)-julianday(?))",
            (key, it["published_at"], _TITLE_MERGE_DAYS, it["published_at"])).fetchall()
        for row in candidates:
            if it["source"] not in json.loads(row["sources"]):
                return row
        return None

    def _stale(self, it, min_published) -> bool:
        """Published before the cutoff. An unparseable date is NOT stale here -
        ``prune`` ages it out by ``first_seen``."""
        if min_published is None:
            return False
        return self._c.execute("SELECT julianday(?) < julianday(?)",
                               (it["published_at"], min_published)).fetchone()[0] == 1

    def insert_many(self, rows, *, min_published=None) -> int:
        """Insert new items, merging duplicates (see the module docstring).
        Returns the count of NEW rows only. An item published before
        ``min_published`` (an ISO cutoff - pass the prune cutoff) is skipped and
        not counted: it would only be pruned again at the end of the cycle.
        Atomic: a failure mid-batch writes nothing of the batch."""
        n = 0
        with self._write():
            for it in rows:
                if self._stale(it, min_published):
                    continue
                same_id = self._c.execute(
                    "SELECT id, sources, tickers, public FROM items WHERE id=? OR id="
                    "(SELECT item_id FROM aliases WHERE id=?)", (it["id"], it["id"])).fetchone()
                if same_id is not None:
                    self._merge_into(same_id, it)
                    continue
                key = items.title_key(it["title"])
                dup = self._title_match(it, key)
                if dup is not None:
                    self._merge_into(dup, it)
                    self._c.execute("INSERT OR IGNORE INTO aliases VALUES (?,?)",
                                    (it["id"], dup["id"]))
                    continue
                cur = self._c.execute(
                    "INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (it["id"], it["source"], _dumps([it["source"]]), it["original_source"],
                     it["title"], key, it["teaser"], it["url"], it["published_at"],
                     it["first_seen"], _dumps(list(it["tickers"])), it["kind"],
                     _dumps(list(it["topics"])), _dumps(it["detail"]), int(bool(it["public"]))))
                n += cur.rowcount
        return n

    def _select(self, where, params, limit, *, public_only, sources, public_sources) -> list:
        sources = _names(sources)
        if sources is not None:
            if not sources:
                return []
            where.append(f"source IN ({','.join('?' * len(sources))})")
            params.extend(sources)
        public_sources = _names(public_sources)
        if public_sources is not None:
            if not public_sources:
                return []
            where.append("EXISTS (SELECT 1 FROM json_each(items.sources) WHERE value IN "
                         f"({','.join('?' * len(public_sources))}))")
            params.extend(public_sources)
        if public_only:
            where.append("public=1")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._lock:
            rows = self._c.execute(f"SELECT * FROM items {clause} {_ORDER} LIMIT ?",
                                   (*params, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for col in _JSON_COLS:
                d[col] = json.loads(d[col])
            d["public"] = bool(d["public"])
            d.pop("title_key", None)
            if public_sources is not None:
                # Only public names reach the public view - a non-public feed's
                # name must never leak through a merged row's ``sources``.
                allowed = set(public_sources)
                d["sources"] = [s for s in d["sources"] if s in allowed]
                d["source"] = d["sources"][0]
                d["public"] = True
            out.append(d)
        return out

    def newest(self, limit, *, public_only=False, sources=None, public_sources=None) -> list:
        """The newest ``limit`` items.

        ``public_sources`` - the feeds public NOW - is THE public view: a row is
        returned iff any feed in its ``sources`` is in it, and the row comes back
        with ``sources`` cut to those public names and ``source`` the first of
        them. ``sources`` keeps only rows whose primary ``source`` is in it;
        ``public_only`` keeps rows whose ingest-time ``public`` column is set.
        A bare str is one name; an EMPTY collection returns nothing. The limit
        applies after every filter."""
        return self._select([], [], limit, public_only=public_only, sources=sources,
                            public_sources=public_sources)

    def newest_for_ticker(self, symbol, limit, *, public_only=False, sources=None,
                          public_sources=None) -> list:
        """As ``newest``, restricted to items tagged with exactly ``symbol``."""
        return self._select(
            ["EXISTS (SELECT 1 FROM json_each(items.tickers) WHERE value=?)"], [symbol],
            limit, public_only=public_only, sources=sources, public_sources=public_sources)

    def prune(self, *, keep_days, now) -> int:
        """Delete items published OR first seen before the cutoff (``first_seen``
        is our own UTC clock, so an unparseable ``published_at`` still ages out),
        and accessions seen before it. Returns the count of items deleted."""
        cutoff = (dt.datetime.fromisoformat(now) - dt.timedelta(days=keep_days)).isoformat()
        with self._write():
            cur = self._c.execute(
                "DELETE FROM items WHERE julianday(published_at) < julianday(?) "
                "OR julianday(first_seen) < julianday(?)", (cutoff, cutoff))
            deleted = cur.rowcount
            self._c.execute("DELETE FROM aliases WHERE item_id NOT IN (SELECT id FROM items)")
            self._c.execute("DELETE FROM seen_accessions WHERE julianday(seen) < julianday(?)",
                            (cutoff,))
        return deleted

    # ── feed state ────────────────────────────────────────────────────────
    def feed_state(self, name) -> dict:
        with self._lock:
            r = self._c.execute("SELECT * FROM feed_state WHERE name=?", (name,)).fetchone()
        return dict(r) if r else {"name": name, "etag": None, "last_modified": None,
                                  "last_ok": None, "last_poll": None, "error": None}

    def set_feed_state(self, name, **fields):
        with self._write():
            st = self.feed_state(name)
            st.update(fields)
            self._c.execute(
                "INSERT OR REPLACE INTO feed_state VALUES (?,?,?,?,?,?)",
                (name, st["etag"], st["last_modified"], st["last_ok"], st["last_poll"],
                 st["error"]))

    def all_feed_states(self) -> list:
        with self._lock:
            return [dict(r) for r in self._c.execute("SELECT * FROM feed_state").fetchall()]

    # ── EDGAR ─────────────────────────────────────────────────────────────
    def unseen_accessions(self, accessions) -> list:
        """The incoming accessions not yet seen, in their order. Looks up only
        the batch (``IN`` over at most ``_IN_CHUNK`` names a query)."""
        accessions = list(accessions)
        wanted = list(dict.fromkeys(accessions))
        seen = set()
        with self._lock:
            for i in range(0, len(wanted), _IN_CHUNK):
                chunk = wanted[i:i + _IN_CHUNK]
                seen.update(r[0] for r in self._c.execute(
                    "SELECT accession FROM seen_accessions WHERE accession IN "
                    f"({','.join('?' * len(chunk))})", chunk).fetchall())
        return [a for a in accessions if a not in seen]

    def mark_accessions(self, accessions, now=None):
        now = now or dt.datetime.now(dt.timezone.utc).isoformat()
        with self._write():
            self._c.executemany("INSERT OR IGNORE INTO seen_accessions VALUES (?,?)",
                                [(a, now) for a in accessions])
