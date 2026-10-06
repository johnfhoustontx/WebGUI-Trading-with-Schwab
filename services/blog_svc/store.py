"""blog.db and the files beside it: drafts, entries, their documents, their typefaces.

This is the source of truth for the site Blog. The public folder
(``deploy/site/blog/``) is NOT: the site writer rebuilds it whole from what is
here, so everything a rebuild needs is here.

    <data>/blog.db                         the rows
    <data>/staging/<draft id>/entry.html   a draft's cleaned document, typefaces applied
    <data>/published/<slug>/entry.html     an entry's document
    <data>/fonts/<20 hex>.woff2            ONE pool of typefaces, named by content

``<data>`` is ``repo_paths.BLOG_DATA``, read when a ``Store`` is MADE (never at
import, never as a ``def`` default), so the suite can point it somewhere else.

A document says ``../fonts/<name>``. On this disk that is ``staging/fonts/``,
which does not exist, and that is intended: the private preview route and the
site writer each map that address onto the one pool themselves. There is never
a second copy of a typeface per draft.

What is a name, and who may spell one
-------------------------------------
Three kinds of string become a file or folder name, and each has ONE gate:

* a draft id        ``blog_inbox.is_id``
* an address (slug) ``blog_inbox.clean_slug`` for a NEW one (a draft's own, a
                    publish), ``blog_inbox.existing_slug`` to find, read or
                    remove an entry that already exists. The second is held to
                    the CEILING of ``[limits] slug_chars``, so lowering the
                    setting cannot strand an entry published under the old one.
* a typeface name   ``blog_inbox.FONT_NAME_RE``, and it must be the hash of the
                    bytes stored under it.

Anything else raises ``ValueError`` before the disk is touched, not even to make
a folder. On top of the gates:

* An address must already be SPELLED as its gate spells it. ``existing_slug``
  lower-cases and trims, so "A" and "abc " both come back valid; neither is the
  string that was handed in, and a path is built only from that string.
* An address that Windows reads as a device (``con``, ``nul``, ``com1`` ...) is
  refused on every platform. It has the shape of an address, and prod is Linux
  where it would simply work - which is how an entry no Windows box can restore
  would get published.
* Every finished path's resolved parent must be exactly the folder it was built
  in. That holds whatever the gates let through.

Reading from a row that does not pass its gate is not attempted either: every
public method that takes an id or an address raises on a bad one, including the
ones that only look a row up. Check ``blog_inbox.is_id`` first on a path that
must not raise.

A document is the one its row describes, or it is not there
-----------------------------------------------------------
Each row carries ``digest``, the SHA-256 of its document's bytes, and a read
returns only bytes that match it. This is what lets the writes below be exact
(a row and a file cannot be changed in one step, so for a moment they
disagree, and the digest says which file belongs to the row), and it means the
operator previews what was cleaned and the site serves what was previewed: a
file altered on disk reads as missing.

``fonts``, ``bytes`` and ``digest`` are worked out HERE from what is stored.
Whatever a caller's dict says for them is ignored.

The order of every write
------------------------
All of it happens under one lock and inside one ``BEGIN IMMEDIATE``
transaction, so the COMMIT is the moment a change has happened, and a file
step sits on one side of it or the other:

* **A new document in a new folder** (``add_draft``; ``publish`` of a new
  entry). Typefaces, then the folder and ``entry.html`` written whole, then the
  row, then COMMIT. Nothing was there before, so the file can take its final
  name at once, and "the row exists" implies "its document is in place" for a
  reader that only looks at the disk (the private preview does). Stopped before
  the commit: a folder no row names. An ordinary failure removes it again;
  after a crash ``repair`` does.
* **A document that replaces one** (``replace_draft``; ``publish`` of a
  revision). The new document is written BESIDE the old as ``entry.html.next``,
  then the row (with the new digest), then COMMIT, then the rename over
  ``entry.html``. The old file is not touched until the rows say it is
  replaced. Stopped before the commit: the old document, the old row, and a
  ``.next`` that matches nothing, which is deleted. Stopped after it: the row
  describes the ``.next``; reads already return it (the digest picks it), and
  ``repair`` finishes the rename. Either other order leaves a row beside the
  wrong document with nothing to tell them apart - and the row is what names
  the typefaces a prune must keep.
* **publish** copies; it does not move. The draft's row and staged folder stay
  whole until the commit that creates the entry also deletes the draft, and
  only then is the staged folder removed. A crash at any point loses nothing:
  before the commit the draft is still waiting, after it the entry is
  published and a staged folder no row names is left for ``repair``.
* **discard, unpublish**: the row first, then the folder. A folder no row
  names is the one leftover ``repair`` may delete without asking anything.

``repair()`` - run at service start - for each interrupted state:

    a staged folder no draft names             removed
    a draft whose document is not there        the rename finished if its
                                               ``.next`` matches; else the draft
                                               is removed and logged by id
    a published folder no entry names          removed
    an entry whose document is not there       the rename finished if its
                                               ``.next`` matches; else KEPT and
                                               reported (losing a published
                                               entry in silence is worse than a
                                               broken page)
    a ``.next`` beside a document that matches deleted
    a half-written temporary file              deleted
    a typeface a row names that is not there   reported

It deletes only what this module could have made: a folder named like an id
under ``staging/``, like an address under ``published/``, a temporary file of
its own pattern. Anything else is listed and left.

⚠ A reader that opens the files itself (the private preview, the connector's
``get_entry``) sees ``entry.html`` and nothing else: no digest, no ``.next``.
For a NEW document that is exact. For a replacement it shows the previous
document from the commit until the rename - normally no time at all, and until
the next ``repair`` if the rename was refused or the process died between the
two. ``draft_html`` / ``entry_html`` are the exact reads.

Each file is written to a temporary name in its own folder, flushed to the
disk, then renamed, so a name never holds half a file. The FOLDER entry is not
flushed: after a power cut (not a crash of the process) a rename may be undone,
which is one of the states above and is put right the same way.

More than one thread, more than one Store
-----------------------------------------
The service calls this from two command streams and a scheduler branch. Every
``Store`` on one data folder, in one process, shares one lock (``_lock_for``),
held around each public call - the file steps included. ``BEGIN IMMEDIATE``
takes SQLite's write lock as well, which is what would hold off a second
PROCESS; nothing but ``blog_svc`` writes here today.

``prune_fonts`` runs under the same lock and transaction as ``add_draft``, and
``add_draft`` holds them from before its first typeface is written until its
row is committed. So a prune can never see a typeface that is on disk and not
yet named: the check and the delete are one step with respect to every write.

Times are the caller's. This module reads no clock. Each one must be an ISO
string that carries its timezone; it is kept as UTC with microseconds always
written, so that text order is time order.

No error raised here quotes a document or a field: each ``ValueError`` is one of
this module's own sentences.

Design: docs/plans/2026-10-06-site-blog-design.md ("Shape", "What lands on the
site", "Rules that keep it safe").
"""
import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import pathlib
import re
import secrets
import shutil
import sqlite3
import threading

from services import _degrade
from shared import blog_inbox

log = logging.getLogger("blog_svc.store")

DB_NAME = "blog.db"
STAGING, PUBLISHED, FONTS = "staging", "published", "fonts"
# The document, and the name its replacement waits under until the rows say it
# has replaced it (see "The order of every write").
DOC = "entry.html"
NEXT = "entry.html.next"

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, revises TEXT, slug TEXT NOT NULL,
    title TEXT NOT NULL, summary TEXT NOT NULL, tags TEXT NOT NULL, removed TEXT NOT NULL,
    fonts TEXT NOT NULL, font_links TEXT NOT NULL, font_note TEXT NOT NULL,
    bytes INTEGER NOT NULL, digest TEXT NOT NULL, received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    slug TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL, tags TEXT NOT NULL,
    fonts TEXT NOT NULL, font_links TEXT NOT NULL, digest TEXT NOT NULL,
    published_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_submissions_at ON submissions(at);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# How long a row of the submission log is kept. The log backs an HOURLY limit,
# so anything past an hour is already unread; two days leaves room to look at
# what happened yesterday. Not a config key: no setting of it changes anything
# an operator can see, and one below the limit's own window would break the limit.
SUBMISSIONS_KEEP_HOURS = 48

# A key of the ``kv`` table: a short plain name, so none can be mistaken for SQL
# or for another key written with different capitals.
KV_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}\Z")

# Names Windows gives to devices, in any folder and whatever follows a dot. A
# folder of that name cannot be made there, and opening one opens the device.
DEVICE_NAMES = frozenset({"con", "prn", "aux", "nul"}
                         | {f"com{n}" for n in range(10)}
                         | {f"lpt{n}" for n in range(10)})

# A file this module was part-way through writing: ``.<final name>.<12 hex>.tmp``.
# ``repair`` deletes these and nothing else that merely ends in ``.tmp``.
_TMP_RE = re.compile(r"^\.[a-z0-9.]{1,48}\.[0-9a-f]{12}\.tmp\Z")

_BUSY_TIMEOUT_MS = 10_000

_DRAFT_SHAPES = {"tags": list, "removed": dict, "fonts": list, "font_links": list}
_ENTRY_SHAPES = {"tags": list, "fonts": list, "font_links": list}


# ── one lock per data folder ─────────────────────────────────────────────────

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(folder) -> threading.RLock:
    """The lock every ``Store`` on ``folder`` shares, in this process.

    A lock on the instance would serialise one ``Store`` and nothing else, and
    a ``Store`` per thread is an ordinary way to use SQLite: two of them would
    each hold "the" lock while one pruned the typefaces the other had just
    written. Entries are never removed - there is one data folder per process
    outside the test suite."""
    key = os.path.normcase(str(pathlib.Path(folder).resolve()))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


# ── what may be a name, a time, a field ──────────────────────────────────────

def _draft_id(raw) -> str:
    if not blog_inbox.is_id(raw):
        raise ValueError("not a draft id")
    return raw


def _address(raw, gate) -> str:
    """``raw`` if ``gate`` accepts it exactly as written and it is not a device
    name; else ``ValueError`` (see "What is a name" in the module docstring)."""
    slug = gate(raw)
    if slug is None or slug != raw:
        raise ValueError("not an entry address")
    if slug in DEVICE_NAMES:
        raise ValueError("an address that is a device name on Windows")
    return slug


def _old_slug(raw) -> str:
    """The address of an entry that may already exist."""
    return _address(raw, blog_inbox.existing_slug)


def _new_slug(raw) -> str:
    """An address something NEW may take."""
    return _address(raw, blog_inbox.clean_slug)


def _draft_slug(raw, revises) -> str:
    """The address a draft may carry. A revision carries its entry's own, which
    may be longer than a new address is now allowed to be."""
    if revises is not None and raw == revises:
        return _old_slug(raw)
    return _new_slug(raw)


def _font_name(raw) -> str:
    if not isinstance(raw, str) or not blog_inbox.FONT_NAME_RE.match(raw):
        raise ValueError("not a typeface name")
    return raw


def _utf8(text):
    """``text`` as UTF-8, or ``None`` when it cannot be written as that (a lone
    surrogate). Returned, not raised: the encoder's own error carries the whole
    text it was given, and a document must never ride out on an exception."""
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        return None


def _text(raw, what) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{what} must be text")
    if _utf8(raw) is None:
        raise ValueError(f"{what} cannot be stored as UTF-8")
    return raw


def _texts(raw, what) -> list:
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{what} must be a list")
    return [_text(item, what) for item in raw]


def _counts(raw) -> dict:
    """What the cleaner removed: a name to how many. A bool is refused (``True``
    is an int and would be stored as 1)."""
    if not isinstance(raw, dict):
        raise ValueError("what was removed must be a mapping")
    for name, count in raw.items():
        _text(name, "a removed item's name")
        if isinstance(count, bool) or not isinstance(count, int):
            raise ValueError("a removed item's count must be a whole number")
    return dict(raw)


def _moment(raw) -> dt.datetime:
    """``raw`` as an aware UTC moment. A time with no timezone is refused: it
    would be read as this box's local time on one machine and as UTC on
    another, and this module has no clock of its own to settle which."""
    if not isinstance(raw, str):
        raise ValueError("a time must be an ISO string")
    try:
        when = dt.datetime.fromisoformat(raw)
    except ValueError:
        when = None
    if when is None or when.utcoffset() is None:
        raise ValueError("a time must be an ISO string that carries its timezone")
    try:
        return when.astimezone(dt.timezone.utc)
    except (OverflowError, ValueError):
        raise ValueError("a time outside the calendar") from None


def _iso(when) -> str:
    """One spelling for every stored time, microseconds ALWAYS written, so that
    comparing two of them as text compares the moments."""
    return when.isoformat(timespec="microseconds")


def _utc(raw) -> str:
    return _iso(_moment(raw))


def _document(html) -> bytes:
    """The bytes to store for ``html``. Encoded here and nowhere else: the same
    bytes are hashed, counted and written, and read back without any newline
    being translated."""
    data = _utf8(html) if isinstance(html, str) else None
    if data is None:
        raise ValueError("a document must be text that can be stored as UTF-8")
    return data


def _typefaces(font_files) -> dict:
    """``{name: bytes}`` with every name the hash of its own bytes.

    A file already in the pool is never written again, so a file stored once
    under a name that is not its hash would be served to every later entry
    that asks for the real one. ``fonts.py`` names them this way; this is the
    store not taking that on trust."""
    if not isinstance(font_files, dict):
        raise ValueError("typefaces must be a mapping of name to bytes")
    out = {}
    for name, data in font_files.items():
        _font_name(name)
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("a typeface must be bytes")
        if hashlib.sha256(data).hexdigest()[:20] + ".woff2" != name:
            raise ValueError("a typeface must be named by a hash of its content")
        out[name] = bytes(data)
    return out


def _described(draft) -> dict:
    """The fields of a draft that a replacement replaces, each checked."""
    if not isinstance(draft, dict):
        raise ValueError("a draft must be a mapping")
    return {"slug": draft.get("slug"),
            "title": _text(draft.get("title"), "a title"),
            "summary": _text(draft.get("summary"), "a summary"),
            "tags": _texts(draft.get("tags"), "tags"),
            "removed": _counts(draft.get("removed")),
            "font_links": _texts(draft.get("font_links"), "typeface links"),
            "font_note": _text(draft.get("font_note"), "a typeface note")}


def _loads(raw, shape):
    """A JSON column as ``shape`` (``list`` or ``dict``); an empty one when the
    text is not that."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return shape()
    return value if isinstance(value, shape) else shape()


def _as_dict(row, shapes):
    if row is None:
        return None
    out = dict(row)
    for column, shape in shapes.items():
        out[column] = _loads(out[column], shape)
    return out


# ── files ────────────────────────────────────────────────────────────────────

def _put(path, data) -> None:
    """Write ``data`` at ``path`` whole or not at all: a temporary name in the
    same folder, flushed to the disk, then renamed over the real one."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _digest_at(path):
    """The SHA-256 of the file at ``path``, ``None`` when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _listing(folder) -> list:
    """What is in ``folder``, by name; nothing when it is not there."""
    try:
        return sorted(folder.iterdir(), key=lambda child: child.name)
    except OSError:
        return []


def _open(db):
    """A connection to ``db``, making its folder only if the first try needed it.

    ⚠ The connect comes FIRST, before any ``mkdir``. Under pytest the repo-root
    guard refuses ``sqlite3.connect`` on a live path; with the folder made
    first, a test that reached the live path would have created the production
    data folder on its way to being refused."""
    def connect():
        # isolation_level=None: no implicit transactions, so every write is the
        # explicit one in ``Store._write``. ``timeout`` is the ONE busy wait.
        return sqlite3.connect(str(db), check_same_thread=False,
                               timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    try:
        return connect()
    except sqlite3.OperationalError:
        db.parent.mkdir(parents=True, exist_ok=True)
        return connect()


class Store:
    def __init__(self, db_path=None, data_dir=None):
        if data_dir is not None:
            folder = pathlib.Path(data_dir)
        elif db_path is not None:
            # Never the caller's database with the FILES from repo_paths: the
            # pytest guard watches sqlite3.connect and would not see documents
            # landing in the live folder.
            folder = pathlib.Path(db_path).parent
        else:
            import repo_paths
            folder, db_path = pathlib.Path(repo_paths.BLOG_DATA), repo_paths.BLOG_DB
        self._dir = folder
        self._c = _open(folder / DB_NAME if db_path is None else pathlib.Path(db_path))
        try:
            self._lock = _lock_for(self._dir)
            self._c.row_factory = sqlite3.Row
            with self._lock:
                self._c.execute("PRAGMA journal_mode=WAL")
                self._c.executescript(SCHEMA)
        except BaseException:
            self._c.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ── transactions ───────────────────────────────────────────────────────
    def _commit(self) -> None:
        """Its own method so a test can fail or stop a write exactly here."""
        self._c.commit()

    def _rollback(self) -> None:
        """Best effort: a failed rollback must not mask the error that caused
        it, so it is swallowed - but never silently, since a transaction left
        open wedges every later write until a restart."""
        try:
            self._c.rollback()
        except Exception:          # noqa: BLE001 - the original error is re-raised
            log.warning("blog store: rollback failed", exc_info=True)
        try:
            wedged = self._c.in_transaction
        except Exception:          # noqa: BLE001 - e.g. a closed connection
            wedged = False
        if wedged:
            log.warning("blog store: a transaction is still open after the rollback; "
                        "later writes will fail until it closes")

    @contextlib.contextmanager
    def _write(self):
        """One atomic write: the lock, BEGIN IMMEDIATE, then COMMIT - or
        ROLLBACK and re-raise. The COMMIT is inside the guard: a failed commit
        can leave the transaction open, and then every later one fails."""
        with self._lock:
            self._c.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._commit()
            except BaseException:
                self._rollback()
                raise

    # ── paths ──────────────────────────────────────────────────────────────
    def _inside(self, kind, name) -> pathlib.Path:
        """``<data>/<kind>/<name>``, refused unless its resolved parent is
        exactly ``<data>/<kind>``. Every caller hands in a name that passed its
        gate; this is the check that holds if a gate is ever wrong."""
        base = self._dir / kind
        path = base / name
        if path.resolve().parent != base.resolve():
            raise ValueError("a path outside the blog data folder")
        return path

    def _staging(self, draft_id) -> pathlib.Path:
        return self._inside(STAGING, _draft_id(draft_id))

    def _published(self, slug) -> pathlib.Path:
        return self._inside(PUBLISHED, _old_slug(slug))

    def _font(self, name) -> pathlib.Path:
        return self._inside(FONTS, _font_name(name))

    # ── file steps (each called with the lock held) ────────────────────────
    def _drop(self, folder) -> bool:
        """Remove ``folder`` and what is in it. True when it is gone (or was
        never there). A failure is counted, not raised: every caller has
        already committed the row change this tidies up after, and what is left
        is a folder no row names - ``repair`` removes those."""
        try:
            if folder.is_symlink() or folder.is_file():
                folder.unlink()
            elif folder.exists():
                shutil.rmtree(folder)
            return True
        except OSError:
            _degrade.degraded("blog.store", detail="a folder could not be removed")
            return False

    def _put_fonts(self, files) -> None:
        """Add ``files`` to the pool. One already there under the same name and
        of the same size is left alone: the name is its content."""
        for name, data in files.items():
            path = self._font(name)
            try:
                there = path.stat().st_size == len(data)
            except OSError:
                there = False
            if not there:
                path.parent.mkdir(parents=True, exist_ok=True)
                _put(path, data)

    def _land(self, folder, data, fresh) -> None:
        """Put a document where the row about to be committed will look for it.

        ``fresh`` - no row names ``folder`` yet: clear whatever an interrupted
        write left under the name, and write the document under its final name.
        Otherwise a document may be there that the current row describes: write
        beside it (``NEXT``) and let ``_settle`` rename after the commit."""
        if fresh:
            if not self._drop(folder):
                raise OSError("a leftover folder is in the way and could not be cleared")
            folder.mkdir(parents=True)
            _put(folder / DOC, data)
        else:
            folder.mkdir(parents=True, exist_ok=True)
            _put(folder / NEXT, data)

    def _settle(self, folder) -> None:
        """The rename that follows the commit of a replacement."""
        os.replace(folder / NEXT, folder / DOC)

    def _finish(self, folder) -> None:
        """``_settle``, without letting a refused rename undo a change that has
        already been committed. Reads find the document under ``NEXT`` until
        ``repair`` (or the next replacement) moves it."""
        try:
            self._settle(folder)
        except OSError:
            _degrade.degraded("blog.store", detail="a document is waiting to be moved into place")

    def _undo(self, folder, fresh) -> None:
        """Take back what ``_land`` did, after the rows were NOT committed."""
        if fresh:
            self._drop(folder)
        else:
            with contextlib.suppress(OSError):
                (folder / NEXT).unlink()

    def _read(self, folder, digest):
        """The bytes of the document a row with ``digest`` describes, or
        ``None``. ``NEXT`` is the document only when the rename after a commit
        has not happened yet; the digest is what says so."""
        for name in (DOC, NEXT):
            try:
                data = (folder / name).read_bytes()
            except OSError:
                continue
            if hashlib.sha256(data).hexdigest() == digest:
                return data
        return None

    # ── drafts ─────────────────────────────────────────────────────────────
    def add_draft(self, draft, html, font_files) -> None:
        """File a new draft: its typefaces, its document, then its row.

        ``draft`` carries ``id``, ``source``, ``revises`` (an entry's address
        or ``None``), ``slug``, ``title``, ``summary``, ``tags``, ``removed``,
        ``font_links``, ``font_note`` and ``received_at``. ``ValueError`` for
        anything unusable - and for an id that is already stored, whose draft
        is left exactly as it was."""
        new = _described(draft)
        draft_id = _draft_id(draft.get("id"))
        if draft.get("source") not in blog_inbox.SOURCES:
            raise ValueError("a draft comes from the chat or from an upload")
        revises = None if draft.get("revises") is None else _old_slug(draft.get("revises"))
        slug = _draft_slug(new["slug"], revises)
        received = _utc(draft.get("received_at"))
        data, files = _document(html), _typefaces(font_files)
        # Everything above is a check on the arguments alone. The first look at
        # the disk is here, after all of them.
        folder = self._staging(draft_id)
        landing = False
        with self._lock:
            try:
                with self._write():
                    if self._c.execute("SELECT 1 FROM drafts WHERE id=?",
                                       (folder.name,)).fetchone():
                        raise ValueError("that draft id is already stored")
                    self._put_fonts(files)
                    landing = True
                    self._land(folder, data, True)
                    self._c.execute(
                        "INSERT INTO drafts (id, source, revises, slug, title, summary, tags, "
                        "removed, fonts, font_links, font_note, bytes, digest, received_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (folder.name, draft["source"], revises, slug, new["title"],
                         new["summary"], json.dumps(new["tags"]), json.dumps(new["removed"]),
                         json.dumps(sorted(files)), json.dumps(new["font_links"]),
                         new["font_note"], len(data), hashlib.sha256(data).hexdigest(),
                         received))
            except BaseException:
                if landing:
                    self._undo(folder, True)
                raise

    def replace_draft(self, draft_id, draft, html, font_files) -> bool:
        """Replace a draft that is still waiting. Its ``id``, ``source``,
        ``revises`` and ``received_at`` are KEPT, whatever ``draft`` says for
        them; everything else is replaced. False, and nothing written, for an
        id that is not stored."""
        _draft_id(draft_id)
        new = _described(draft)
        # Which gate the address must pass depends on the stored row (a
        # revision keeps its entry's address). This is the part that needs no
        # row: no address of any kind is past the ceiling, or a device.
        _old_slug(new["slug"])
        data, files = _document(html), _typefaces(font_files)
        folder = self._staging(draft_id)
        landing = False
        with self._lock:
            try:
                with self._write():
                    old = self._c.execute("SELECT revises FROM drafts WHERE id=?",
                                          (folder.name,)).fetchone()
                    if old is None:
                        return False
                    slug = _draft_slug(new["slug"], old["revises"])
                    self._put_fonts(files)
                    landing = True
                    self._land(folder, data, False)
                    self._c.execute(
                        "UPDATE drafts SET slug=?, title=?, summary=?, tags=?, removed=?, "
                        "fonts=?, font_links=?, font_note=?, bytes=?, digest=? WHERE id=?",
                        (slug, new["title"], new["summary"], json.dumps(new["tags"]),
                         json.dumps(new["removed"]), json.dumps(sorted(files)),
                         json.dumps(new["font_links"]), new["font_note"], len(data),
                         hashlib.sha256(data).hexdigest(), folder.name))
            except BaseException:
                if landing:
                    self._undo(folder, False)
                raise
            self._finish(folder)
            return True

    def draft(self, draft_id) -> dict | None:
        _draft_id(draft_id)
        with self._lock:
            row = self._c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return _as_dict(row, _DRAFT_SHAPES)

    def drafts(self) -> list:
        """Every draft waiting, newest first (the later filed first among any
        received at one instant)."""
        with self._lock:
            found = self._c.execute(
                "SELECT * FROM drafts ORDER BY received_at DESC, rowid DESC").fetchall()
        return [_as_dict(row, _DRAFT_SHAPES) for row in found]

    def draft_html(self, draft_id) -> str | None:
        """A draft's document, or ``None`` when there is no such draft or its
        document is not on disk as its row describes it."""
        folder = self._staging(draft_id)
        with self._lock:
            row = self._c.execute("SELECT digest FROM drafts WHERE id=?",
                                  (folder.name,)).fetchone()
            data = None if row is None else self._read(folder, row["digest"])
        return None if data is None else data.decode("utf-8")

    def discard(self, draft_id) -> bool:
        """Remove a draft: its row, then its staged folder. Its typefaces stay
        in the pool until ``prune_fonts``."""
        folder = self._staging(draft_id)
        with self._lock:
            with self._write():
                gone = self._c.execute("DELETE FROM drafts WHERE id=?",
                                       (folder.name,)).rowcount == 1
            if gone:
                self._drop(folder)
            return gone

    # ── entries ────────────────────────────────────────────────────────────
    def publish(self, draft_id, fields, now) -> dict | None:
        """Make a draft an entry, or bring an entry up to a revision of it.

        ``fields`` is ``{"title", "summary", "tags", "slug"}`` as the operator
        left them (through ``blog_inbox.clean_fields``; the address is checked
        again here). Returns the entry, or ``None`` with NOTHING changed when:

        * there is no such draft, or its document is not on disk as described;
        * the title is blank;
        * the draft is new and the address is already an entry's. Only a draft
          filed as a revision (``revises``) may replace an entry;
        * the draft revises an entry and the address is not that entry's: a
          revision cannot rename what it revises.

        Whether a draft revises is decided NOW, against the entries that
        exist: one whose entry has since been unpublished is a new entry.
        A revision keeps the first ``published_at``; ``updated_at`` is ``now``.

        ``ValueError`` for an id, an address, a time or a field that is not
        usable at all - including an address too long for a NEW entry under the
        current limit."""
        _draft_id(draft_id)
        if not isinstance(fields, dict):
            raise ValueError("the fields must be a mapping")
        title = _text(fields.get("title"), "a title")
        summary = _text(fields.get("summary"), "a summary")
        tags = _texts(fields.get("tags"), "tags")
        slug, when = _old_slug(fields.get("slug")), _utc(now)
        if not title.strip():
            return None
        staged, target = self._staging(draft_id), self._published(slug)
        landing = fresh = False
        with self._lock:
            try:
                with self._write():
                    draft = self._c.execute("SELECT * FROM drafts WHERE id=?",
                                            (staged.name,)).fetchone()
                    if draft is None:
                        return None
                    revised = None
                    if draft["revises"] is not None:
                        revised = self._c.execute(
                            "SELECT slug, published_at FROM entries WHERE slug=?",
                            (draft["revises"],)).fetchone()
                    if revised is None:
                        _new_slug(slug)
                        if self._c.execute("SELECT 1 FROM entries WHERE slug=?",
                                           (slug,)).fetchone():
                            return None
                        first = when
                    elif slug != revised["slug"]:
                        return None
                    else:
                        first = revised["published_at"]
                    data = self._read(staged, draft["digest"])
                    if data is None:
                        return None
                    fresh, landing = revised is None, True
                    self._land(target, data, fresh)
                    self._c.execute(
                        "INSERT OR REPLACE INTO entries (slug, title, summary, tags, fonts, "
                        "font_links, digest, published_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (slug, title, summary, json.dumps(tags), draft["fonts"],
                         draft["font_links"], draft["digest"], first, when))
                    self._c.execute("DELETE FROM drafts WHERE id=?", (staged.name,))
            except BaseException:
                if landing:
                    self._undo(target, fresh)
                raise
            if not fresh:
                self._finish(target)
            self._drop(staged)
            return self.entry(slug)

    def entry(self, slug) -> dict | None:
        _old_slug(slug)
        with self._lock:
            row = self._c.execute("SELECT * FROM entries WHERE slug=?", (slug,)).fetchone()
        return _as_dict(row, _ENTRY_SHAPES)

    def entries(self) -> list:
        """Every entry, newest FIRST publication first. A revision does not
        move an entry up the list."""
        with self._lock:
            found = self._c.execute(
                "SELECT * FROM entries ORDER BY published_at DESC, slug ASC").fetchall()
        return [_as_dict(row, _ENTRY_SHAPES) for row in found]

    def entry_html(self, slug) -> str | None:
        """An entry's document, or ``None`` when there is no such entry or its
        document is not on disk as its row describes it."""
        folder = self._published(slug)
        with self._lock:
            row = self._c.execute("SELECT digest FROM entries WHERE slug=?",
                                  (folder.name,)).fetchone()
            data = None if row is None else self._read(folder, row["digest"])
        return None if data is None else data.decode("utf-8")

    def unpublish(self, slug) -> bool:
        """Remove an entry: its row, then its folder."""
        folder = self._published(slug)
        with self._lock:
            with self._write():
                gone = self._c.execute("DELETE FROM entries WHERE slug=?",
                                       (folder.name,)).rowcount == 1
            if gone:
                self._drop(folder)
            return gone

    # ── typefaces ──────────────────────────────────────────────────────────
    def _named_fonts(self) -> set:
        used = set()
        for table in ("drafts", "entries"):
            for row in self._c.execute(f"SELECT fonts FROM {table}").fetchall():
                used.update(name for name in _loads(row["fonts"], list)
                            if isinstance(name, str) and blog_inbox.FONT_NAME_RE.match(name))
        return used

    def fonts_in_use(self) -> set:
        """Every typeface a draft or an entry names."""
        with self._lock:
            return self._named_fonts()

    def font_bytes(self, name):
        """One typeface from the pool, or ``None`` when it is not there."""
        path = self._font(name)
        with self._lock:
            try:
                return path.read_bytes()
            except OSError:
                return None

    def prune_fonts(self) -> int:
        """Delete every typeface in the pool that nothing names; how many went.

        Only a file named like a typeface is ever deleted. Safe beside
        ``add_draft`` (see the module docstring): both run under the one lock
        and transaction, so a typeface written for a row not yet committed
        cannot be seen here as unused."""
        gone = 0
        with self._write():
            used = self._named_fonts()
            for child in _listing(self._dir / FONTS):
                name = child.name
                if not blog_inbox.FONT_NAME_RE.match(name) or name in used:
                    continue
                try:
                    self._font(name).unlink()
                    gone += 1
                except OSError:
                    log.warning("blog store: an unused typeface could not be removed")
        return gone

    # ── putting an interrupted write right ─────────────────────────────────
    def repair(self) -> dict:
        """Make the files agree with the rows (see the module docstring for
        each state). Run at service start. NEVER raises: ``ok`` is False when
        it could not finish, and that is counted for /health.

        Returns ``ok``, the ids in ``staging_removed`` and ``drafts_removed``,
        the addresses in ``published_removed`` and ``entries_missing`` (rows
        that were KEPT and have no document), the names in ``fonts_missing``,
        what it would not touch in ``left_alone``, and two counts: ``settled``
        (renames finished) and ``temp_removed``."""
        report = {"ok": True, "staging_removed": [], "drafts_removed": [],
                  "published_removed": [], "entries_missing": [], "fonts_missing": [],
                  "left_alone": [], "settled": 0, "temp_removed": 0}
        try:
            with self._write():
                self._mend(report)
        except Exception:          # noqa: BLE001 - the service must still start
            _degrade.degraded("blog.store", detail="repair did not finish")
            report["ok"] = False
        return report

    def _heal(self, folder, digest, report) -> bool:
        """Whether ``folder`` holds, under its final name, the document a row
        with ``digest`` describes - finishing an interrupted rename if that is
        all that is missing, and clearing a ``NEXT`` nothing committed."""
        final, waiting = folder / DOC, folder / NEXT
        if _digest_at(final) == digest:
            with contextlib.suppress(OSError):
                waiting.unlink()
                report["temp_removed"] += 1
            return True
        if _digest_at(waiting) == digest:
            os.replace(waiting, final)
            report["settled"] += 1
            return True
        return False

    def _sweep(self, folder, report) -> None:
        """Delete this module's own half-written files in ``folder``."""
        for child in _listing(folder):
            if _TMP_RE.match(child.name):
                with contextlib.suppress(OSError):
                    (folder / child.name).unlink()
                    report["temp_removed"] += 1

    def _mend(self, report) -> None:
        drafts = {row["id"]: row["digest"] for row in self._c.execute(
            "SELECT id, digest FROM drafts").fetchall()}
        for draft_id, digest in sorted(drafts.items()):
            try:
                folder = self._staging(draft_id)
            except ValueError:
                # Not a name this module would write, so no path is built from
                # it. One such row must not stop every other repair.
                log.warning("blog store: a draft row has an id that is not one; left alone")
                continue
            if self._heal(folder, digest, report):
                self._sweep(folder, report)
                continue
            # By id only. What the draft said is not for a log.
            log.warning("blog store: draft %s has no document on disk and was removed",
                        draft_id)
            self._c.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
            self._drop(folder)
            report["drafts_removed"].append(draft_id)
            del drafts[draft_id]
        for child in _listing(self._dir / STAGING):
            if not blog_inbox.is_id(child.name):
                report["left_alone"].append(f"{STAGING}/{child.name}")
            elif child.name not in drafts and self._drop(self._staging(child.name)):
                report["staging_removed"].append(child.name)

        entries = {row["slug"]: row["digest"] for row in self._c.execute(
            "SELECT slug, digest FROM entries").fetchall()}
        for slug, digest in sorted(entries.items()):
            try:
                folder = self._published(slug)
            except ValueError:
                log.warning("blog store: an entry row has an address that is not one; left alone")
                continue
            if self._heal(folder, digest, report):
                self._sweep(folder, report)
                continue
            log.warning("blog store: entry %s has no document on disk; its row is kept", slug)
            report["entries_missing"].append(slug)
        for child in _listing(self._dir / PUBLISHED):
            try:
                folder = self._published(child.name)
            except ValueError:
                report["left_alone"].append(f"{PUBLISHED}/{child.name}")
                continue
            if child.name not in entries and self._drop(folder):
                report["published_removed"].append(child.name)

        self._sweep(self._dir / FONTS, report)
        on_disk = {child.name for child in _listing(self._dir / FONTS)}
        report["fonts_missing"] = sorted(self._named_fonts() - on_disk)
        report["left_alone"].sort()

    # ── the submission log ─────────────────────────────────────────────────
    def note_submission(self, iso) -> None:
        """Record one draft filed by the connector at ``iso``, and forget every
        one more than ``SUBMISSIONS_KEEP_HOURS`` before it."""
        when = _moment(iso)
        cutoff = _iso(when - dt.timedelta(hours=SUBMISSIONS_KEEP_HOURS))
        with self._write():
            self._c.execute("INSERT INTO submissions (at) VALUES (?)", (_iso(when),))
            self._forget_submissions(cutoff)

    def _forget_submissions(self, before) -> int:
        """The one delete behind ``prune_submissions`` and the tidy-up in
        ``note_submission``. ``before`` is a stored spelling; the caller holds
        the transaction."""
        return self._c.execute("DELETE FROM submissions WHERE at < ?", (before,)).rowcount

    def count_submissions_since(self, iso) -> int:
        """How many were noted at ``iso`` or later."""
        since = _utc(iso)
        with self._lock:
            return self._c.execute("SELECT COUNT(*) FROM submissions WHERE at >= ?",
                                   (since,)).fetchone()[0]

    def prune_submissions(self, before_iso) -> int:
        """Forget every submission noted before ``before_iso``; how many went."""
        before = _utc(before_iso)
        with self._write():
            return self._forget_submissions(before)

    # ── the key-value table ────────────────────────────────────────────────
    def get_kv(self, key, default=None):
        """The JSON value stored under ``key``; ``default`` when there is none
        or what is stored cannot be read."""
        if not isinstance(key, str) or not KV_KEY_RE.match(key):
            raise ValueError("not a key")
        with self._lock:
            row = self._c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    def set_kv(self, key, value) -> None:
        if not isinstance(key, str) or not KV_KEY_RE.match(key):
            raise ValueError("not a key")
        try:
            # ASCII out: a lone surrogate inside a value is written as an
            # escape and comes back as it went in, where the raw character
            # would fail at the database.
            text = json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("a value JSON cannot hold") from None
        with self._write():
            self._c.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, text))
