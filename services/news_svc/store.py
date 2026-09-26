"""news.db — items, per-feed HTTP state, and the EDGAR accessions already seen.

``db_path=None`` resolves ``repo_paths.NEWS_DB`` at CALL time (the shape CLAUDE.md
prefers, so the pytest connect guard can redirect it).

Merging, which is the part worth reading before changing anything here:

* An incoming item whose **id** is already stored is not a new row. Its tickers
  are unioned into the stored row (the same Yahoo story arrives on the NVDA and
  AMD feeds under one URL, and must end up tagged with both), and its source is
  added to ``sources`` when new.
* Otherwise an item whose **title key** matches a row published within a day is
  the same story under another URL - ACROSS feeds. A feed whose name is already
  in that row's ``sources`` merges into it only when the two are published within
  ``same_feed_merge_h`` hours (``insert_many``'s argument, from ``[dedupe]`` in
  config/news.toml): one feed repeating a headline minutes apart is one story
  under two URLs (WSJ via Google News publishes one piece under two redirect
  URLs), while the same title ~24 h apart is two stories (a daily "Morning Bid"
  column). ``0`` - the default here - turns the same-feed rule off. It never
  applies to an ``_UNDATED`` item or row: with no date there is no gap to
  measure (``julianday`` reads the sentinel as NULL, so such an item finds no
  title candidate at all).
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
  once, and a merged row names only its public feeds there. A row is public only
  while its PRIMARY source is: its stored url / title / teaser are that feed's, so
  a story first stored by a non-public feed stays out even once a public feed
  carries it (fail closed; an accepted loss).
* ``ticker_sources`` records WHICH feeds tagged each ticker (``{ticker: [feed,
  ...]}``), maintained on every insert and merge. The public view keeps only the
  tickers some CURRENTLY public feed contributed, so a private feed merging into
  a public row adds its tickers to the owner's view and never to the public one.
  ``newest_for_ticker`` applies the same rule to the MATCH: under
  ``public_sources`` a row is found by a ticker only when some currently public
  feed contributed that ticker, so a public per-ticker read never lists a story
  under a tag only a private feed gave it.

Writes: every write opens ``BEGIN IMMEDIATE`` and either commits or rolls back
whole. IMMEDIATE takes the write lock BEFORE the id / title lookups, so two Store
instances on one file (the scheduler and a ``news_refresh`` command) serialise -
neither can miss the other's uncommitted row and store the story twice. A failure
mid-batch leaves nothing of that batch and no open transaction behind it.

Times: every adapter hands over UTC ISO strings. Ordering and pruning compare
``julianday(...)`` rather than the raw strings, so a fractional-seconds stamp
sorts by its instant.

Every date is NORMALISED before it is stored, because SQLite reads some strings
as the clock: ``julianday('now')`` - and, from 3.42, ``'subsec'`` /
``'subsecond'`` in any case - are the current time, which the expression index
refuses as non-deterministic (one such item rolled back its whole batch, every
cycle), and a ``first_seen`` read as the clock is never older than any cutoff,
so its row could never be pruned. A date is USABLE only if it is a str that
``datetime.fromisoformat`` parses, is timezone-aware, and ``julianday()`` can
read (Python accepts ISO basic and week forms SQLite does not). Otherwise:

* ``first_seen`` becomes the current UTC time - it is our own clock, the moment
  we saw the item - so the row ages out like any other.
* ``published_at`` becomes the item's ``first_seen`` when the INCOMING
  ``first_seen`` is usable, else ``_UNDATED``: an unparseable sentinel that
  sorts last, is never stale, and is pruned by ``first_seen``.

A usable date is stored verbatim.

Seen EDGAR accessions are per FEED (``(feed, accession)``): two feeds reading one
Atom must each decide what the accession means to them. An accession marked with
``feed=""`` is seen by EVERY feed.

Migrations (run once at open, inside ``BEGIN IMMEDIATE`` so two openers cannot
race, each a no-op on a database that already has the shape):

* ``items.ticker_sources`` is added and back-filled. The old shape never
  recorded which feed tagged a ticker, so a row's tickers are credited to its
  PRIMARY source only when ``sources`` is exactly ``[primary]`` - then no other
  feed can have contributed one. A row another feed had merged into (``sources``
  names more) gets ``{}``: any of its tickers may be a private feed's, so none is
  public until that row ages out (fail closed; the owner's view still has them,
  and a public feed tagging the story after the upgrade is credited as usual).
* ``feed_state.url`` is added as NULL. A NULL url never matches the feed's
  current url, so the first poll after the upgrade sends no validators (one full
  fetch per feed) and stores the url.
* ``seen_accessions`` is rebuilt keyed by ``(feed, accession)``; the old rows
  are kept under ``feed=""`` - seen by every feed - until ``prune`` ages them
  out. Dropping them instead would refetch up to 100 Form 4s (200 SEC requests)
  per feed on the first poll and re-log every poison filing.
"""
import contextlib
import datetime as dt
import json
import logging
import pathlib
import sqlite3
import threading

from services.news_svc import items

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, sources TEXT NOT NULL,
    original_source TEXT, title TEXT NOT NULL, title_key TEXT,
    teaser TEXT, url TEXT NOT NULL, published_at TEXT NOT NULL, first_seen TEXT NOT NULL,
    tickers TEXT NOT NULL, kind TEXT NOT NULL, topics TEXT NOT NULL,
    detail TEXT NOT NULL, public INTEGER NOT NULL, ticker_sources TEXT
);
DROP INDEX IF EXISTS idx_items_published;
CREATE INDEX IF NOT EXISTS idx_items_published_jd ON items(julianday(published_at));
CREATE INDEX IF NOT EXISTS idx_items_title_key ON items(title_key);
CREATE TABLE IF NOT EXISTS feed_state (
    name TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, last_ok TEXT,
    last_poll TEXT, error TEXT, url TEXT
);
CREATE TABLE IF NOT EXISTS aliases (id TEXT PRIMARY KEY, item_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seen_accessions (
    feed TEXT NOT NULL, accession TEXT NOT NULL, seen TEXT NOT NULL,
    PRIMARY KEY (feed, accession)
);
CREATE INDEX IF NOT EXISTS idx_seen_accession ON seen_accessions(accession);
"""

_JSON_COLS = ("sources", "tickers", "topics", "detail")
_BUSY_TIMEOUT_MS = 10_000
_TITLE_MERGE_DAYS = 1.0
# One instant written as ``Z`` or ``+00:00`` must tie on julianday and fall to
# first_seen, so there is deliberately no raw-string key between the two.
_ORDER = "ORDER BY julianday(published_at) DESC, first_seen DESC"
_IN_CHUNK = 500
_UNDATED = "undated"          # unparseable on purpose: julianday() -> NULL, sorts last
_ROW_COLS = "id, source, sources, tickers, ticker_sources, public"
_ITEM_COLS = ("id, source, sources, original_source, title, title_key, teaser, url, "
              "published_at, first_seen, tickers, kind, topics, detail, public, "
              "ticker_sources")
_STATE_COLS = ("name", "etag", "last_modified", "last_ok", "last_poll", "error", "url")


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


def _utcnow() -> str:
    """Our own clock, as an aware UTC ISO string (a seam for tests)."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _aware_iso(value) -> bool:
    """A str ``datetime.fromisoformat`` parses to a timezone-AWARE datetime."""
    if not isinstance(value, str):
        return False
    try:
        return dt.datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _ticker_sources(row) -> dict:
    """``{ticker: [feed, ...]}`` for a stored row. A row with none recorded
    (unreadable, or written before the column existed) attributes every ticker
    to its PRIMARY source."""
    raw = row["ticker_sources"]
    try:
        got = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        got = None
    if not isinstance(got, dict):
        return {t: [row["source"]] for t in json.loads(row["tickers"])}
    return {t: list(v) if isinstance(v, list) else [] for t, v in got.items()}


def _backfilled_ticker_sources(row) -> dict:
    """The migration's attribution for a row stored before ``ticker_sources``:
    every ticker is the primary's when the primary is the row's ONLY source,
    else nobody's (see the module docstring). An unreadable ``sources`` or
    ``tickers`` credits nobody."""
    try:
        sources = json.loads(row["sources"])
        tickers = json.loads(row["tickers"])
    except (TypeError, ValueError):
        return {}
    if sources != [row["source"]] or not isinstance(tickers, list):
        return {}
    return {t: [row["source"]] for t in tickers if isinstance(t, str)}


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
        self._migrate()
        self._c.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _columns(self, table) -> set:
        return {r[1] for r in self._c.execute(f"PRAGMA table_info({table})").fetchall()}

    def _migrate(self) -> None:
        """Bring an older database to the current shape (see the module
        docstring). Checked inside the transaction, so a second opener that
        waited on the lock finds the work done."""
        with self._write():
            cols = self._columns("items")
            if cols and "ticker_sources" not in cols:
                self._c.execute("ALTER TABLE items ADD COLUMN ticker_sources TEXT")
            if cols:
                for r in self._c.execute("SELECT id, source, sources, tickers FROM items "
                                         "WHERE ticker_sources IS NULL").fetchall():
                    self._c.execute("UPDATE items SET ticker_sources=? WHERE id=?",
                                    (_dumps(_backfilled_ticker_sources(r)), r["id"]))
            cols = self._columns("feed_state")
            if cols and "url" not in cols:
                self._c.execute("ALTER TABLE feed_state ADD COLUMN url TEXT")
            cols = self._columns("seen_accessions")
            if cols and "feed" not in cols:
                self._c.execute("ALTER TABLE seen_accessions RENAME TO seen_accessions_v1")
                self._c.execute(
                    "CREATE TABLE seen_accessions (feed TEXT NOT NULL, accession TEXT NOT NULL, "
                    "seen TEXT NOT NULL, PRIMARY KEY (feed, accession))")
                self._c.execute("INSERT OR IGNORE INTO seen_accessions (feed, accession, seen) "
                                "SELECT '', accession, seen FROM seen_accessions_v1")
                self._c.execute("DROP TABLE seen_accessions_v1")

    def _rollback(self) -> None:
        """Best effort: a failed rollback must not mask the error that caused it,
        so it is swallowed - but never silently, since a transaction left open
        wedges every later BEGIN IMMEDIATE until a restart."""
        try:
            self._c.rollback()
        except Exception:          # noqa: BLE001 - the original error is re-raised
            log.warning("news store: rollback failed", exc_info=True)
        try:
            wedged = self._c.in_transaction
        except Exception:          # noqa: BLE001 - e.g. a closed connection
            wedged = False
        if wedged:
            log.warning("news store: a transaction is still open after the rollback; "
                        "later writes will fail until it closes")

    def _usable(self, value) -> bool:
        """See the module docstring: aware ISO that julianday() can read."""
        return _aware_iso(value) and self._c.execute(
            "SELECT julianday(?) IS NOT NULL", (value,)).fetchone()[0] == 1

    def _dated(self, it) -> dict:
        """``it`` with both dates normalised (see the module docstring). A copy
        when anything changes - the caller's dict is never changed."""
        published, first_seen = it.get("published_at"), it.get("first_seen")
        seen_ok = self._usable(first_seen)
        fixed = {}
        if not self._usable(published):
            fixed["published_at"] = first_seen if seen_ok else _UNDATED
        if not seen_ok:
            fixed["first_seen"] = _utcnow()
        return dict(it, **fixed) if fixed else it

    @contextlib.contextmanager
    def _write(self):
        """One atomic write: BEGIN IMMEDIATE, then COMMIT, or ROLLBACK and re-raise.
        The COMMIT is inside the guard: a failed commit can leave the transaction
        open, and then every later BEGIN IMMEDIATE fails until a restart."""
        with self._lock:
            self._c.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._c.commit()
            except BaseException:
                self._rollback()
                raise

    # ── items ──────────────────────────────────────────────────────────────
    def _merge_into(self, row, it) -> None:
        """Union ``it``'s tickers and source into the stored ``row``, recording
        ``it``'s source against each of its tickers; ``public`` becomes stored
        OR incoming."""
        tickers = json.loads(row["tickers"])
        sources = json.loads(row["sources"])
        incoming = list(it.get("tickers") or [])
        new_tickers = _union(tickers, incoming)
        new_sources = _union(sources, [it["source"]])
        by_ticker = _ticker_sources(row)
        new_by_ticker = {t: list(v) for t, v in by_ticker.items()}
        for t in incoming:
            feeds = new_by_ticker.setdefault(t, [])
            if it["source"] not in feeds:
                feeds.append(it["source"])
        public = int(bool(row["public"]) or bool(it.get("public")))
        if (new_tickers != tickers or new_sources != sources or public != row["public"]
                or new_by_ticker != by_ticker or not row["ticker_sources"]):
            self._c.execute(
                "UPDATE items SET tickers=?, sources=?, public=?, ticker_sources=? WHERE id=?",
                (_dumps(new_tickers), _dumps(new_sources), public, _dumps(new_by_ticker),
                 row["id"]))

    def _title_match(self, it, key, same_feed_merge_h=0):
        """The row this item is the same story as, or None: the nearest row with
        its title key published within a day, from ANOTHER feed - or from the
        same feed when the gap is at most ``same_feed_merge_h`` hours (> 0)."""
        if key is None:
            return None
        candidates = self._c.execute(
            f"SELECT {_ROW_COLS}, published_at, "
            "abs(julianday(published_at)-julianday(?)) * 24 AS gap_h "
            "FROM items WHERE title_key=? AND "
            "abs(julianday(published_at)-julianday(?)) < ? "
            "ORDER BY abs(julianday(published_at)-julianday(?))",
            (it["published_at"], key, it["published_at"], _TITLE_MERGE_DAYS,
             it["published_at"])).fetchall()
        for row in candidates:
            if it["source"] not in json.loads(row["sources"]):
                return row
            if self._same_feed_close(it, row, same_feed_merge_h):
                return row
        return None

    @staticmethod
    def _same_feed_close(it, row, window_h) -> bool:
        """The same-feed rule: a real date on both sides and a gap within
        ``window_h`` hours. ``0`` (or less) is off - a gap of 0 must not merge."""
        if not window_h or window_h <= 0:
            return False
        if _UNDATED in (it["published_at"], row["published_at"]):
            return False
        gap = row["gap_h"]
        return gap is not None and gap <= window_h

    def _stale(self, it, min_published) -> bool:
        """Published before the cutoff. ``_UNDATED`` is NOT stale here -
        ``prune`` ages it out by ``first_seen``."""
        if min_published is None:
            return False
        return self._c.execute("SELECT julianday(?) < julianday(?)",
                               (it["published_at"], min_published)).fetchone()[0] == 1

    def insert_many(self, rows, *, min_published=None, same_feed_merge_h=0) -> int:
        """Insert new items, merging duplicates (see the module docstring).
        Returns the count of NEW rows only. An item published before
        ``min_published`` (an ISO cutoff - pass the prune cutoff) is skipped and
        not counted: it would only be pruned again at the end of the cycle.
        ``same_feed_merge_h`` is the same-feed title-merge window in hours; the
        default ``0`` is off, so the store alone never folds one feed's items -
        the poll cycle passes the configured window.
        Atomic: a failure mid-batch writes nothing of the batch."""
        n = 0
        with self._write():
            for it in rows:
                it = self._dated(it)
                if self._stale(it, min_published):
                    continue
                same_id = self._c.execute(
                    f"SELECT {_ROW_COLS} FROM items WHERE id=? OR id="
                    "(SELECT item_id FROM aliases WHERE id=?)", (it["id"], it["id"])).fetchone()
                if same_id is not None:
                    self._merge_into(same_id, it)
                    continue
                key = items.title_key(it["title"])
                dup = self._title_match(it, key, same_feed_merge_h)
                if dup is not None:
                    self._merge_into(dup, it)
                    self._c.execute("INSERT OR IGNORE INTO aliases VALUES (?,?)",
                                    (it["id"], dup["id"]))
                    continue
                tickers = list(it["tickers"])
                cur = self._c.execute(
                    f"INSERT OR IGNORE INTO items ({_ITEM_COLS}) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (it["id"], it["source"], _dumps([it["source"]]), it["original_source"],
                     it["title"], key, it["teaser"], it["url"], it["published_at"],
                     it["first_seen"], _dumps(tickers), it["kind"],
                     _dumps(list(it["topics"])), _dumps(it["detail"]), int(bool(it["public"])),
                     _dumps({t: [it["source"]] for t in tickers})))
                n += cur.rowcount
        return n

    def _select(self, where, params, limit, *, public_only, sources, public_sources) -> list:
        # SQLite reads a negative LIMIT as "no limit"; a limit that is not a
        # positive int (bool included) returns nothing.
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            return []
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
            # The PRIMARY source must be public: the stored url / title / teaser /
            # original_source are that feed's, and must never reach the public
            # view from a non-public feed (fail closed - a story first stored by
            # a non-public feed stays out even once a public feed carries it).
            where.append(f"source IN ({','.join('?' * len(public_sources))})")
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
            by_ticker = _ticker_sources(r)
            d.pop("ticker_sources", None)
            for col in _JSON_COLS:
                d[col] = json.loads(d[col])
            d["public"] = bool(d["public"])
            d.pop("title_key", None)
            if public_sources is not None:
                # Only public names reach the public view - a non-public feed's
                # name must never leak through a merged row's ``sources``.
                allowed = set(public_sources)
                d["sources"] = [d["source"]] + [s for s in d["sources"]
                                                if s in allowed and s != d["source"]]
                # ...and only the tickers some public feed tagged: a private
                # feed merged into this row adds nothing here.
                d["tickers"] = [t for t in d["tickers"]
                                if allowed.intersection(by_ticker.get(t, ()))]
                d["public"] = True
            out.append(d)
        return out

    def newest(self, limit, *, public_only=False, sources=None, public_sources=None) -> list:
        """The newest ``limit`` items.

        ``public_sources`` - the feeds public NOW - is THE public view: a row is
        returned iff its PRIMARY ``source`` (the feed whose url / title / teaser
        are stored) is in it, and comes back with ``sources`` cut to the public
        names, primary first. ``sources`` keeps only rows whose primary ``source`` is in it;
        ``public_only`` keeps rows whose ingest-time ``public`` column is set.
        A bare str is one name; an EMPTY collection returns nothing. The limit
        applies after every filter; a ``limit`` that is not a positive int
        returns nothing."""
        return self._select([], [], limit, public_only=public_only, sources=sources,
                            public_sources=public_sources)

    def newest_for_ticker(self, symbol, limit, *, public_only=False, sources=None,
                          public_sources=None) -> list:
        """As ``newest``, restricted to items tagged with exactly ``symbol``.

        Under ``public_sources`` the tag itself must be PUBLIC: a row matches
        only when some public feed contributed ``symbol`` (per
        ``ticker_sources``), the same rule that cuts the returned ``tickers``.
        Fail closed - otherwise a public per-ticker page would list a story
        under AAPL while showing it untagged, because only a private feed said
        AAPL. A row whose ``ticker_sources`` is unreadable attributes every
        ticker to its primary source (as ``_ticker_sources`` does), which the
        public view already requires to be public. The filter is in SQL so the
        limit applies after it."""
        where = ["EXISTS (SELECT 1 FROM json_each(items.tickers) WHERE value=?)"]
        params = [symbol]
        names = _names(public_sources)
        if names:
            marks = ",".join("?" * len(names))
            where.append(
                "(CASE WHEN json_valid(items.ticker_sources) "
                "AND json_type(items.ticker_sources)='object' "
                "THEN EXISTS (SELECT 1 FROM json_each(items.ticker_sources) AS ts "
                "WHERE ts.key=? AND ts.type='array' AND EXISTS "
                f"(SELECT 1 FROM json_each(ts.value) AS f WHERE f.value IN ({marks}))) "
                "ELSE 1 END)")
            params.extend([symbol, *names])
        return self._select(
            where, params,
            limit, public_only=public_only, sources=sources, public_sources=public_sources)

    def prune(self, *, keep_days, now) -> int:
        """Delete items published OR first seen before the cutoff (``first_seen``
        is always a usable UTC stamp - our own clock when the feed's was not - so
        an ``_UNDATED`` ``published_at`` still ages out),
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
        if r:
            return dict(r)
        return dict.fromkeys(_STATE_COLS, None) | {"name": name}

    def set_feed_state(self, name, **fields):
        """Update the named fields; the rest keep their stored values. ``url`` is
        the URL ``etag`` / ``last_modified`` were answered for."""
        with self._write():
            st = self.feed_state(name)
            st.update(fields)
            self._c.execute(
                f"INSERT OR REPLACE INTO feed_state ({', '.join(_STATE_COLS)}) "
                f"VALUES ({','.join('?' * len(_STATE_COLS))})",
                (name, *(st[c] for c in _STATE_COLS[1:])))

    def all_feed_states(self) -> list:
        with self._lock:
            return [dict(r) for r in self._c.execute("SELECT * FROM feed_state").fetchall()]

    # ── EDGAR ─────────────────────────────────────────────────────────────
    def unseen_accessions(self, accessions, *, feed=None) -> list:
        """The incoming accessions ``feed`` has not seen, in their order - one
        marked for ``feed`` or for every feed (``""``). ``feed=None`` asks
        whether ANY feed has seen it. Looks up only the batch (``IN`` over at
        most ``_IN_CHUNK`` names a query)."""
        accessions = list(accessions)
        wanted = list(dict.fromkeys(accessions))
        seen = set()
        scope, extra = ("", []) if feed is None else ("feed IN (?, '') AND ", [str(feed)])
        with self._lock:
            for i in range(0, len(wanted), _IN_CHUNK):
                chunk = wanted[i:i + _IN_CHUNK]
                seen.update(r[0] for r in self._c.execute(
                    f"SELECT accession FROM seen_accessions WHERE {scope}accession IN "
                    f"({','.join('?' * len(chunk))})", [*extra, *chunk]).fetchall())
        return [a for a in accessions if a not in seen]

    def mark_accessions(self, accessions, now=None, *, feed=""):
        """Mark ``accessions`` seen by ``feed`` (``""``: by every feed)."""
        now = now or dt.datetime.now(dt.timezone.utc).isoformat()
        with self._write():
            self._c.executemany(
                "INSERT OR IGNORE INTO seen_accessions (feed, accession, seen) VALUES (?,?,?)",
                [(str(feed), a, now) for a in accessions])
