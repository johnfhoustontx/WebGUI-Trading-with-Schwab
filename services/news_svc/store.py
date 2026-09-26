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

Times: every adapter hands over UTC ISO strings. Ordering and pruning compare
``julianday(...)`` rather than the raw strings, so a fractional-seconds stamp
sorts by its instant. An unparseable ``published_at`` sorts last and is pruned by
``first_seen`` - our own clock - so it can never be kept forever.
"""
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
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
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
_ORDER = "ORDER BY julianday(published_at) DESC, published_at DESC, first_seen DESC"


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
        self._c = sqlite3.connect(str(db_path), check_same_thread=False,
                                  timeout=_BUSY_TIMEOUT_MS / 1000)
        self._c.row_factory = sqlite3.Row
        self._c.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.executescript(SCHEMA)

    # ── items ──────────────────────────────────────────────────────────────
    def _merge_into(self, row, it) -> None:
        """Union ``it``'s tickers and source into the stored ``row``."""
        tickers = json.loads(row["tickers"])
        sources = json.loads(row["sources"])
        new_tickers = _union(tickers, it.get("tickers") or [])
        new_sources = _union(sources, [it["source"]])
        if new_tickers != tickers or new_sources != sources:
            self._c.execute("UPDATE items SET tickers=?, sources=? WHERE id=?",
                            (_dumps(new_tickers), _dumps(new_sources), row["id"]))

    def _title_match(self, it, key):
        """The row this item is the same story as, from ANOTHER feed, or None."""
        if key is None:
            return None
        candidates = self._c.execute(
            "SELECT id, sources, tickers FROM items WHERE title_key=? AND "
            "abs(julianday(published_at)-julianday(?)) < ? "
            "ORDER BY abs(julianday(published_at)-julianday(?))",
            (key, it["published_at"], _TITLE_MERGE_DAYS, it["published_at"])).fetchall()
        for row in candidates:
            if it["source"] not in json.loads(row["sources"]):
                return row
        return None

    def insert_many(self, rows) -> int:
        """Insert new items, merging duplicates (see the module docstring).
        Returns the count of NEW rows only."""
        n = 0
        with self._lock:
            for it in rows:
                same_id = self._c.execute(
                    "SELECT id, sources, tickers FROM items WHERE id=? OR id="
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
            self._c.commit()
        return n

    def _select(self, where, params, limit, *, public_only, sources) -> list:
        if sources is not None:
            sources = list(sources)
            if not sources:
                return []
            where.append(f"source IN ({','.join('?' * len(sources))})")
            params.extend(sources)
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
            out.append(d)
        return out

    def newest(self, limit, *, public_only=False, sources=None) -> list:
        """The newest ``limit`` items. ``sources`` (a collection of feed names)
        keeps only rows whose ``source`` is in it; an EMPTY collection returns
        nothing. The limit applies after every filter."""
        return self._select([], [], limit, public_only=public_only, sources=sources)

    def newest_for_ticker(self, symbol, limit, *, public_only=False, sources=None) -> list:
        """As ``newest``, restricted to items tagged with exactly ``symbol``."""
        return self._select(
            ["EXISTS (SELECT 1 FROM json_each(items.tickers) WHERE value=?)"], [symbol],
            limit, public_only=public_only, sources=sources)

    def prune(self, *, keep_days, now) -> int:
        """Delete items published OR first seen before the cutoff (``first_seen``
        is our own UTC clock, so an unparseable ``published_at`` still ages out),
        and accessions seen before it. Returns the count of items deleted."""
        cutoff = (dt.datetime.fromisoformat(now) - dt.timedelta(days=keep_days)).isoformat()
        with self._lock:
            cur = self._c.execute(
                "DELETE FROM items WHERE julianday(published_at) < julianday(?) "
                "OR julianday(first_seen) < julianday(?)", (cutoff, cutoff))
            self._c.execute("DELETE FROM aliases WHERE item_id NOT IN (SELECT id FROM items)")
            self._c.execute("DELETE FROM seen_accessions WHERE julianday(seen) < julianday(?)",
                            (cutoff,))
            self._c.commit()
        return cur.rowcount

    # ── feed state ────────────────────────────────────────────────────────
    def feed_state(self, name) -> dict:
        with self._lock:
            r = self._c.execute("SELECT * FROM feed_state WHERE name=?", (name,)).fetchone()
        return dict(r) if r else {"name": name, "etag": None, "last_modified": None,
                                  "last_ok": None, "last_poll": None, "error": None}

    def set_feed_state(self, name, **fields):
        with self._lock:
            st = self.feed_state(name)
            st.update(fields)
            self._c.execute(
                "INSERT OR REPLACE INTO feed_state VALUES (?,?,?,?,?,?)",
                (name, st["etag"], st["last_modified"], st["last_ok"], st["last_poll"],
                 st["error"]))
            self._c.commit()

    def all_feed_states(self) -> list:
        with self._lock:
            return [dict(r) for r in self._c.execute("SELECT * FROM feed_state").fetchall()]

    # ── EDGAR ─────────────────────────────────────────────────────────────
    def unseen_accessions(self, accessions) -> list:
        with self._lock:
            seen = {r[0] for r in
                    self._c.execute("SELECT accession FROM seen_accessions").fetchall()}
        return [a for a in accessions if a not in seen]

    def mark_accessions(self, accessions, now=None):
        now = now or dt.datetime.now(dt.timezone.utc).isoformat()
        with self._lock:
            self._c.executemany("INSERT OR IGNORE INTO seen_accessions VALUES (?,?)",
                                [(a, now) for a in accessions])
            self._c.commit()
