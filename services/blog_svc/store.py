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
The folder and file names are ``shared.blog_inbox``'s (``STAGING_DIR`` ...),
because the private preview reads this folder without importing the service.

A document says ``../fonts/<name>``. On this disk that is ``staging/fonts/``,
which does not exist, and that is intended: the private preview route and the
site writer each map that address onto the one pool themselves. There is never
a second copy of a typeface per draft.

One writer, any number of readers
---------------------------------
**ONE process may write here: the service.** Every ``Store`` in that process,
on one data folder, shares one lock (``_lock_for``), held around each public
call - the file steps included - so the service's command stream and its
scheduler branch are safe together, with one ``Store`` or one each.

A second process may READ, through ``blog_inbox.read_document`` and
``blog_inbox.read_font`` and nothing else (see the next section for why not by
opening ``entry.html``).

A second WRITER process is not supported. ``BEGIN IMMEDIATE`` would keep its
row changes apart from this one's, but the steps that come AFTER a commit - the
rename of a replacement into place, the removal of a discarded folder - run
under the in-process lock only. Another process could start its own write
between this one's commit and its rename, and the two would be moving the same
files.

A document is the one its row describes, or it is not there
-----------------------------------------------------------
Each row carries ``digest``, the SHA-256 of its document's bytes, and a read
returns only bytes that match it (``blog_inbox.read_document``, which this
module uses for every document read, so it and the readers outside cannot
disagree). This is what lets the writes below be exact - a row and a file
cannot be changed in one step, so for a moment they disagree, and the digest
says which file belongs to the row - and it means the operator previews what
was cleaned and the site serves what was previewed: a file altered on disk
reads as missing. A typeface likewise: ``font_bytes`` returns one only when its
content is what its name says.

``fonts``, ``bytes``, ``digest`` and ``revises_published_at`` are worked out
HERE from what is stored. Whatever a caller's dict says for them is ignored.

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

On top of the gates:

* An address must already be SPELLED as its gate spells it. ``existing_slug``
  lower-cases and trims, so "A" and "abc " both come back valid; neither is the
  string that was handed in, and a path is built only from that string.
* The reserved words (``fonts``, and the names Windows gives to devices) are
  the gates' to refuse, and are checked again here against the same set.
* A name is joined to a folder only if it is ONE path component
  (``_component``). That holds whatever the gates let through, and it reads the
  name, never the disk.

A name that fails:

* in a WRITE (``add_draft``, ``replace_draft``, ``publish``) is a
  ``StoreRefusal("bad_name")``, raised before the disk is touched - not even to
  make a folder;
* in a LOOKUP (``draft``, ``draft_html``, ``entry``, ``entry_html``,
  ``font_bytes``) reads as "not there": ``None``. ``discard`` and ``unpublish``
  answer False. A double-clicked Discard is not an error to report.

Refusals
--------
Everything this module will not do is a ``StoreRefusal``, a ``ValueError``
whose text is one of ``REFUSAL_CODES`` and nothing else - never an id, an
address or any part of a document.

The order of every write
------------------------
All of it happens under the lock and inside one ``BEGIN IMMEDIATE``
transaction, so the COMMIT is the moment a change has happened, and a file
step sits on one side of it or the other:

* **A new document in a new folder** (``add_draft``; ``publish`` of a new
  entry). Typefaces, then the folder and ``entry.html`` written whole, then the
  row, then COMMIT. Nothing was there before, so the file can take its final
  name at once. Stopped before the commit: a folder no row names. An ordinary
  failure removes it again; after a crash ``repair`` does.
* **A document that replaces one** (``replace_draft``; ``publish`` of a
  revision). The new document is written BESIDE the old as ``entry.html.next``,
  then the row (with the new digest), then COMMIT, then the rename over
  ``entry.html``. The old file is not touched until the rows say it is
  replaced. Stopped before the commit: the old document, the old row, and a
  ``.next`` that matches nothing, which is deleted. Stopped after it: the row
  describes the ``.next``; reads already return it (the digest picks it), and
  the rename is finished by the next read, the next replacement or ``repair``.
  Either other order leaves a row beside the wrong document with nothing to
  tell them apart - and the row is what names the typefaces a prune must keep.

  ⚠ So a ``.next`` can be the ONLY copy of a document that is committed: after
  the commit the rename can be refused (on Windows, by anyone holding
  ``entry.html`` open), and ``_finish`` lets the call succeed all the same. A
  later replacement must therefore never write its own ``.next`` over one that
  matches the row, and never delete a ``.next`` it did not write. ``_land``
  finishes the waiting rename first and refuses (``busy``) if it cannot;
  ``_undo`` runs only after ``_land`` has returned.
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
    a rename that is still refused             recorded; nothing is missing
    a ``.next`` beside a document that matches deleted
    a half-written temporary file              deleted
    a typeface a row names that is not there   reported

Each of those is a step of its own: one that fails is recorded by KIND and the
rest still run, and a row is listed as removed only after the transaction that
removed it has committed. It deletes only what this module could have made: a
real folder named like an id under ``staging/``, like an address under
``published/``, a temporary file of its own pattern. A link, and anything else,
is listed and left.

``prune_fonts`` runs under the same lock and transaction as ``add_draft``, and
``add_draft`` holds them from before its first typeface is written until its
row is committed. So a prune can never see a typeface that is on disk and not
yet named: the check and the delete are one step with respect to every write.

Each file is written to a temporary name in its own folder, flushed to the
disk, then renamed, so a name never holds half a file. The FOLDER entry is not
flushed: after a power cut (not a crash of the process) a rename may be undone,
which is one of the states above and is put right the same way.

Times are the caller's. This module reads no clock. Each one must be an ISO
string that carries its timezone; it is kept as UTC with microseconds always
written, so that text order is time order.

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
import stat
import threading

from services import _degrade
from shared import blog_inbox

log = logging.getLogger("blog_svc.store")

DB_NAME = "blog.db"
# The layout is ``blog_inbox``'s, because the private preview reads these
# folders without importing this module. The short names are this module's own.
STAGING, PUBLISHED, FONTS = (blog_inbox.STAGING_DIR, blog_inbox.PUBLISHED_DIR,
                             blog_inbox.FONTS_DIR)
# The document, and the name its replacement waits under until the rows say it
# has replaced it (see "The order of every write").
DOC, NEXT = blog_inbox.DOC_NAME, blog_inbox.NEXT_NAME

# Why a call was refused, in one word. The service turns each into the
# operator's sentence; nothing else about a refusal leaves this module.
#   no_draft          there is no draft with that id
#   no_title          an entry needs a title
#   slug_taken        another entry already has that address
#   slug_changed      a revision must keep the address of the entry it revises
#   document_missing  the draft's document is not on disk as its row describes
#   bad_name          an id, an address or a typeface name that is not one
#   bad_input         anything else unusable about what was handed in
#   busy              a document is still waiting to be moved into place, and
#                     writing the next one now would destroy it. Try again.
REFUSAL_CODES = ("no_draft", "no_title", "slug_taken", "slug_changed",
                 "document_missing", "bad_name", "bad_input", "busy")


class StoreRefusal(ValueError):
    """The store will not do that, and ``code`` (one of ``REFUSAL_CODES``) is why.

    Its text is the code and nothing else - never an id, an address or any
    part of a document - so it can go into a log line or an answer as it is.
    A ``ValueError``, because that is what a refusal was before it had a code.
    A code that is not one of the listed ones is this module's own mistake and
    reads as ``bad_input`` rather than carrying whatever it was handed."""

    def __init__(self, code):
        code = code if isinstance(code, str) and code in REFUSAL_CODES else "bad_input"
        super().__init__(code)
        self.code = code


SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, revises TEXT, slug TEXT NOT NULL,
    title TEXT NOT NULL, summary TEXT NOT NULL, tags TEXT NOT NULL, removed TEXT NOT NULL,
    fonts TEXT NOT NULL, font_links TEXT NOT NULL, font_note TEXT NOT NULL,
    bytes INTEGER NOT NULL, digest TEXT NOT NULL, received_at TEXT NOT NULL,
    revises_published_at TEXT
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
        raise StoreRefusal("bad_name")
    return raw


def _address(raw, gate) -> str:
    """``raw`` if ``gate`` accepts it exactly as written; else a ``bad_name``
    refusal (see "What is a name" in the module docstring).

    The reserved words - ``fonts`` and the names Windows gives to devices - are
    refused by the gates themselves. They are checked AGAIN here, against the
    same set, so that this module does not depend on the gate being right for
    the two names that would overwrite the typeface folder or open a device."""
    slug = gate(raw)
    if slug is None or slug != raw or slug in blog_inbox.RESERVED_SLUGS:
        raise StoreRefusal("bad_name")
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
        raise StoreRefusal("bad_name")
    return raw


def _component(name) -> str:
    """``name`` if it is ONE path component, else a ``bad_name`` refusal.

    The last check before a name is joined to a folder, and it holds whatever
    the gates let through: no separator, no drive or stream colon, no NUL, not
    a dot name. It reads the NAME and never the disk - see ``Store._inside``
    for why nothing here may resolve a path."""
    if (not isinstance(name, str) or name in ("", ".", "..")
            or any(mark in name for mark in ("/", "\\", ":", chr(0)))
            or pathlib.PurePath(name).name != name):
        raise StoreRefusal("bad_name")
    return name


def _utf8(text):
    """``text`` as UTF-8, or ``None`` when it cannot be written as that (a lone
    surrogate). Returned, not raised: the encoder's own error carries the whole
    text it was given, and a document must never ride out on an exception."""
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        return None


def _text(raw) -> str:
    """``raw`` if it is text UTF-8 can hold; else a ``bad_input`` refusal."""
    if not isinstance(raw, str) or _utf8(raw) is None:
        raise StoreRefusal("bad_input")
    return raw


def _texts(raw) -> list:
    if not isinstance(raw, (list, tuple)):
        raise StoreRefusal("bad_input")
    return [_text(item) for item in raw]


def _counts(raw) -> dict:
    """What the cleaner removed: a name to how many. A bool is refused (``True``
    is an int and would be stored as 1)."""
    if not isinstance(raw, dict):
        raise StoreRefusal("bad_input")
    for name, count in raw.items():
        _text(name)
        if isinstance(count, bool) or not isinstance(count, int):
            raise StoreRefusal("bad_input")
    return dict(raw)


def _moment(raw) -> dt.datetime:
    """``raw`` as an aware UTC moment. A time with no timezone is refused: it
    would be read as this box's local time on one machine and as UTC on
    another, and this module has no clock of its own to settle which."""
    try:
        when = dt.datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        when = None
    if when is None or when.utcoffset() is None:
        raise StoreRefusal("bad_input")
    try:
        return when.astimezone(dt.timezone.utc)
    except (OverflowError, ValueError):
        raise StoreRefusal("bad_input") from None


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
        raise StoreRefusal("bad_input")
    return data


def _typefaces(font_files) -> dict:
    """``{name: bytes}`` with every name the hash of its own bytes.

    A file already in the pool is never written again, so a file stored once
    under a name that is not its hash would be served to every later entry
    that asks for the real one. ``fonts.py`` names them this way; this is the
    store not taking that on trust."""
    if not isinstance(font_files, dict):
        raise StoreRefusal("bad_input")
    out = {}
    for name, data in font_files.items():
        _font_name(name)
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise StoreRefusal("bad_input")
        if blog_inbox.font_name_for(data) != name:
            raise StoreRefusal("bad_input")
        out[name] = bytes(data)
    return out


def _described(draft) -> dict:
    """The fields of a draft that a replacement replaces, each checked. The
    address is checked by the caller, which knows which gate it must pass."""
    return {"slug": draft.get("slug"),
            "title": _text(draft.get("title")),
            "summary": _text(draft.get("summary")),
            "tags": _texts(draft.get("tags")),
            "removed": _counts(draft.get("removed")),
            "font_links": _texts(draft.get("font_links")),
            "font_note": _text(draft.get("font_note"))}


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
    same folder, flushed to the disk, then renamed over the real one. Whatever
    goes wrong, ``path`` is either as it was or holds all of ``data``."""
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


def _digest_at(path, strict=False):
    """The SHA-256 of the file at ``path``; ``None`` when there is no file.

    ``strict`` is for ``repair``, which DELETES a draft whose document is not
    there: a file that exists and could not be read this time (locked, a disk
    error) is then an error to raise, not an absence to act on."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        if not strict or path.is_dir():     # a folder in the file's place is "no file"
            return None
        raise


def _is_link(path) -> bool:
    """Whether ``path`` is itself a link: a symbolic link, or a Windows
    junction (which ``Path.is_symlink`` does not report before Python 3.12).
    Asked before anything is deleted or written under a name, so that nothing
    here follows a link out of the data folder."""
    try:
        seen = os.lstat(path)
    except (OSError, ValueError):
        return False
    if stat.S_ISLNK(seen.st_mode):
        return True
    junction = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
    return junction is not None and getattr(seen, "st_reparse_tag", 0) == junction


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
        self._c = _open(folder / DB_NAME if db_path is None else pathlib.Path(db_path))
        try:
            # Resolved ONCE, here. No later call resolves anything (``_inside``).
            self._dir = folder.resolve()
            self._lock = _lock_for(self._dir)
            self._c.row_factory = sqlite3.Row
            with self._lock:
                self._c.execute("PRAGMA journal_mode=WAL")
                self._c.executescript(SCHEMA)
                self._migrate()
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

    def _migrate(self) -> None:
        """Bring a database made by the first version to the current shape:
        ``drafts.revises_published_at``, added as NULL. A draft already waiting
        therefore reads as written against no entry, which is the safe
        reading: it can be published as a new entry and can never replace one.
        Checked inside the transaction, so a second opener finds it done."""
        with self._write():
            columns = {row[1] for row in self._c.execute("PRAGMA table_info(drafts)").fetchall()}
            if "revises_published_at" not in columns:
                self._c.execute("ALTER TABLE drafts ADD COLUMN revises_published_at TEXT")

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
        """``<data>/<kind>/<name>``, for a ``name`` that is one path component.

        ⚠ It reads the name and NEVER the disk. The first version resolved the
        finished path and compared its parent; on Windows, resolving a folder
        that another thread is in the middle of removing fails or answers with
        something else, and four lookups in ten of a draft being discarded were
        refused as "outside the data folder". The data folder itself is
        resolved once, when the ``Store`` is made. What this gives up - noticing
        that ``<name>`` is a LINK to somewhere else - is asked where it matters,
        with ``_is_link``: before a delete and before a write."""
        return self._dir / kind / _component(name)

    def _staging(self, draft_id) -> pathlib.Path:
        return self._inside(STAGING, _draft_id(draft_id))

    def _published(self, slug) -> pathlib.Path:
        return self._inside(PUBLISHED, _old_slug(slug))

    def _font(self, name) -> pathlib.Path:
        return self._inside(FONTS, _font_name(name))

    # ── file steps (each called with the lock held) ────────────────────────
    def _drop(self, folder) -> bool:
        """Remove ``folder`` and what is in it. True when it is gone (or was
        never there). A link is removed as a link: what it points at is not
        this module's. A failure is counted, not raised: every caller has
        already committed the row change this tidies up after, and what is left
        is a folder no row names - ``repair`` removes those."""
        try:
            if _is_link(folder):
                try:
                    folder.unlink()
                except OSError:
                    os.rmdir(folder)        # Windows: a link to a folder
            elif folder.is_file():
                folder.unlink()
            elif folder.exists():
                shutil.rmtree(folder)
            return True
        except OSError:
            _degrade.degraded("blog.store", detail="a folder could not be removed")
            return False

    def _put_fonts(self, files) -> None:
        """Add ``files`` to the pool. One already there under the same name
        WITH the content the name promises is left alone; anything else under
        that name (cut short, altered) is replaced."""
        pool = self._dir / FONTS
        for name, data in files.items():
            if blog_inbox.read_font(pool, name) is None:
                path = self._font(name)
                if _is_link(path):
                    raise StoreRefusal("busy")
                pool.mkdir(parents=True, exist_ok=True)
                _put(path, data)

    def _catch_up(self, folder, digest) -> bool:
        """Finish a rename that an earlier, COMMITTED replacement could not.

        False only when the document a row with ``digest`` describes is still
        waiting under ``NEXT`` after this - the one state in which ``NEXT``
        must not be written over. True when there is nothing waiting: no
        ``NEXT``, or one that is not the row's document (left by a write that
        was never committed), or the rename has now been done."""
        final, waiting = folder / DOC, folder / NEXT
        if _digest_at(waiting) != digest:
            return True
        if _digest_at(final) == digest:
            # The same bytes under both names: the final one is enough, so the
            # other may be written over whether or not it can be removed.
            with contextlib.suppress(OSError):
                waiting.unlink()
            return True
        try:
            os.replace(waiting, final)
        except OSError:
            return False
        return True

    def _land(self, folder, data, fresh, current=None) -> None:
        """Put a document where the row about to be committed will look for it.

        ``fresh`` - no row names ``folder`` yet: clear whatever an interrupted
        write left under the name, and write the document under its final
        name. If that fails the folder is taken away again, here.

        Otherwise the row that IS there describes a document with digest
        ``current``, and the new one is written beside it (``NEXT``) for
        ``_settle`` to rename after the commit. ⚠ ``NEXT`` may already hold
        that current document: a rename refused after an earlier commit
        (``_finish``) leaves it there, as the ONLY copy. So the rename is
        finished first, and if it still cannot be, the write is refused
        (``busy``) rather than made over it. When this raises, ``NEXT`` has not
        been touched by this call - ``_put`` renames over it or does nothing -
        which is what lets the caller undo only what it knows was written."""
        if fresh:
            if not self._drop(folder):
                raise StoreRefusal("busy")
            try:
                folder.mkdir(parents=True)
                _put(folder / DOC, data)
            except BaseException:
                self._drop(folder)
                raise
            return
        if _is_link(folder):
            raise StoreRefusal("busy")      # never write through a link
        folder.mkdir(parents=True, exist_ok=True)
        if not self._catch_up(folder, current):
            raise StoreRefusal("busy")
        _put(folder / NEXT, data)

    def _settle(self, folder) -> None:
        """The rename that follows the commit of a replacement."""
        os.replace(folder / NEXT, folder / DOC)

    def _finish(self, folder) -> None:
        """``_settle``, without letting a refused rename undo a change that has
        already been committed. The document stays under ``NEXT``, where
        ``blog_inbox.read_document`` finds it by its digest, until the next
        read, the next replacement or ``repair`` moves it."""
        try:
            self._settle(folder)
        except OSError:
            _degrade.degraded("blog.store", detail="a document is waiting to be moved into place")

    def _undo(self, folder, fresh) -> None:
        """Take back what ``_land`` did, after the rows were NOT committed.

        Called ONLY when ``_land`` returned, so that for a replacement the
        ``NEXT`` being removed is the one this call wrote. A ``NEXT`` that was
        already there is never this method's to remove: it may be the only
        copy of the document the row describes."""
        if fresh:
            self._drop(folder)
        else:
            with contextlib.suppress(OSError):
                (folder / NEXT).unlink()

    def _document_of(self, folder, digest):
        """The bytes of the document a row with ``digest`` describes, or
        ``None`` - through the one reader everything outside this module uses,
        so the two cannot disagree. A rename left waiting is finished on the
        way if it can be; if not, the read still returns the right document."""
        self._catch_up(folder, digest)
        return blog_inbox.read_document(folder, digest)

    # ── drafts ─────────────────────────────────────────────────────────────
    def add_draft(self, draft, html, font_files) -> None:
        """File a new draft: its typefaces, its document, then its row.

        ``draft`` carries ``id``, ``source``, ``revises`` (an entry's address
        or ``None``), ``slug``, ``title``, ``summary``, ``tags``, ``removed``,
        ``font_links``, ``font_note`` and ``received_at``. A ``StoreRefusal``
        (``bad_name`` for an id, an address or a typeface name that is not one;
        ``bad_input`` for anything else unusable, and for an id that is already
        stored, whose draft is left exactly as it was) - raised BEFORE the disk
        is touched.

        For a revision, the entry's ``published_at`` as it is NOW is recorded
        on the row (``revises_published_at``; NULL when there is no such
        entry). ``publish`` uses it to tell the entry this draft was written
        against from another that later took the same address."""
        if not isinstance(draft, dict):
            raise StoreRefusal("bad_input")
        draft_id = _draft_id(draft.get("id"))
        revises = None if draft.get("revises") is None else _old_slug(draft.get("revises"))
        slug = _draft_slug(draft.get("slug"), revises)
        new = _described(draft)
        if draft.get("source") not in blog_inbox.SOURCES:
            raise StoreRefusal("bad_input")
        received = _utc(draft.get("received_at"))
        data, files = _document(html), _typefaces(font_files)
        # Everything above is a check on the arguments alone. The first look at
        # the disk is below, after all of them.
        folder = self._staging(draft_id)
        landed = False
        with self._lock:
            try:
                with self._write():
                    if self._c.execute("SELECT 1 FROM drafts WHERE id=?",
                                       (draft_id,)).fetchone():
                        raise StoreRefusal("bad_input")
                    against = None if revises is None else self._c.execute(
                        "SELECT published_at FROM entries WHERE slug=?", (revises,)).fetchone()
                    self._put_fonts(files)
                    self._land(folder, data, True)
                    landed = True
                    self._c.execute(
                        "INSERT INTO drafts (id, source, revises, slug, title, summary, tags, "
                        "removed, fonts, font_links, font_note, bytes, digest, received_at, "
                        "revises_published_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (draft_id, draft["source"], revises, slug, new["title"],
                         new["summary"], json.dumps(new["tags"]), json.dumps(new["removed"]),
                         json.dumps(sorted(files)), json.dumps(new["font_links"]),
                         new["font_note"], len(data), hashlib.sha256(data).hexdigest(),
                         received, None if against is None else against["published_at"]))
            except BaseException:
                if landed:
                    self._undo(folder, True)
                raise

    def replace_draft(self, draft_id, draft, html, font_files) -> bool:
        """Replace a draft that is still waiting. Its ``id``, ``source``,
        ``revises``, ``revises_published_at`` and ``received_at`` are KEPT,
        whatever ``draft`` says for them; everything else is replaced. False,
        and nothing written, for an id that is not stored. A ``StoreRefusal``
        as for ``add_draft``, and ``busy`` when the draft's current document is
        still waiting to be moved into place and cannot be (see ``_land``)."""
        _draft_id(draft_id)
        if not isinstance(draft, dict):
            raise StoreRefusal("bad_input")
        # Which gate the address must pass depends on the stored row (a
        # revision keeps its entry's address). This is the part that needs no
        # row: no address of any kind is past the ceiling, or reserved.
        _old_slug(draft.get("slug"))
        new = _described(draft)
        data, files = _document(html), _typefaces(font_files)
        folder = self._staging(draft_id)
        landed = False
        with self._lock:
            try:
                with self._write():
                    old = self._c.execute("SELECT revises, digest FROM drafts WHERE id=?",
                                          (draft_id,)).fetchone()
                    if old is None:
                        return False
                    slug = _draft_slug(new["slug"], old["revises"])
                    self._put_fonts(files)
                    self._land(folder, data, False, old["digest"])
                    landed = True
                    self._c.execute(
                        "UPDATE drafts SET slug=?, title=?, summary=?, tags=?, removed=?, "
                        "fonts=?, font_links=?, font_note=?, bytes=?, digest=? WHERE id=?",
                        (slug, new["title"], new["summary"], json.dumps(new["tags"]),
                         json.dumps(new["removed"]), json.dumps(sorted(files)),
                         json.dumps(new["font_links"]), new["font_note"], len(data),
                         hashlib.sha256(data).hexdigest(), draft_id))
            except BaseException:
                if landed:
                    self._undo(folder, False)
                raise
            self._finish(folder)
            return True

    def draft(self, draft_id) -> dict | None:
        """The draft, or ``None`` - also for an id that is not one."""
        if not blog_inbox.is_id(draft_id):
            return None
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
        """A draft's document. ``None`` when there is no such draft, when its
        document is not on disk as its row describes it, and for an id that is
        not one."""
        try:
            folder = self._staging(draft_id)
        except StoreRefusal:
            return None
        with self._lock:
            row = self._c.execute("SELECT digest FROM drafts WHERE id=?",
                                  (draft_id,)).fetchone()
            data = None if row is None else self._document_of(folder, row["digest"])
        return None if data is None else data.decode("utf-8")

    def discard(self, draft_id) -> bool:
        """Remove a draft: its row, then its staged folder. Its typefaces stay
        in the pool until ``prune_fonts``. False when there was no such draft -
        a second Discard of the same one, or an id that is not one."""
        try:
            folder = self._staging(draft_id)
        except StoreRefusal:
            return False
        with self._lock:
            with self._write():
                gone = self._c.execute("DELETE FROM drafts WHERE id=?",
                                       (draft_id,)).rowcount == 1
            if gone:
                self._drop(folder)
            return gone

    # ── entries ────────────────────────────────────────────────────────────
    def _revised(self, draft):
        """The entry ``draft`` is a revision OF, or ``None``.

        Only the entry it was written against: the one at ``revises`` whose
        ``published_at`` is what was recorded when the draft was filed. When
        that entry has been unpublished and another now has the address, this
        is ``None`` - the draft is a new entry, and at that address
        ``slug_taken`` - so a stale revision cannot overwrite something it was
        never a revision of. (A revision does not change ``published_at``, so
        two drafts written against one entry both still match it.)"""
        if draft["revises"] is None:
            return None
        entry = self._c.execute("SELECT slug, published_at, digest FROM entries WHERE slug=?",
                                (draft["revises"],)).fetchone()
        if entry is None or entry["published_at"] != draft["revises_published_at"]:
            return None
        return entry

    def publish(self, draft_id, fields, now) -> dict:
        """Make a draft an entry, or bring an entry up to a revision of it.

        ``fields`` is ``{"title", "summary", "tags", "slug"}`` as the operator
        left them (through ``blog_inbox.clean_fields``; the address is checked
        again here). Returns the entry. Otherwise raises ``StoreRefusal`` with
        NOTHING changed, and ``code`` says why:

        * ``bad_name``: the id or the address is not one - including an
          address too long for a NEW entry under the current limit;
        * ``bad_input``: the fields or the time are not usable;
        * ``no_title``: the title is blank;
        * ``no_draft``: there is no such draft;
        * ``slug_taken``: the address is already an entry's, and this draft is
          not a revision of THAT entry (see ``_revised``);
        * ``slug_changed``: the draft revises an entry and the address is not
          that entry's - a revision cannot rename what it revises;
        * ``document_missing``: the draft's document is not on disk as its row
          describes it;
        * ``busy``: the entry's current document is still waiting to be moved
          into place and cannot be (see ``_land``). Nothing is lost; try again.

        Checked in that order: the names, then the rest of what was handed in,
        and only then what the rows say.

        Whether a draft revises is decided NOW, against the entries that
        exist: one whose entry has since been unpublished is a new entry.
        A revision keeps the first ``published_at``; ``updated_at`` is ``now``."""
        _draft_id(draft_id)
        if not isinstance(fields, dict):
            raise StoreRefusal("bad_input")
        slug = _old_slug(fields.get("slug"))
        title, summary = _text(fields.get("title")), _text(fields.get("summary"))
        tags, when = _texts(fields.get("tags")), _utc(now)
        if not title.strip():
            raise StoreRefusal("no_title")
        staged, target = self._staging(draft_id), self._published(slug)
        landed = fresh = False
        with self._lock:
            try:
                with self._write():
                    draft = self._c.execute("SELECT * FROM drafts WHERE id=?",
                                            (draft_id,)).fetchone()
                    if draft is None:
                        raise StoreRefusal("no_draft")
                    revised = self._revised(draft)
                    if revised is None:
                        _new_slug(slug)
                        if self._c.execute("SELECT 1 FROM entries WHERE slug=?",
                                           (slug,)).fetchone():
                            raise StoreRefusal("slug_taken")
                        first = when
                    elif slug != revised["slug"]:
                        raise StoreRefusal("slug_changed")
                    else:
                        first = revised["published_at"]
                    data = self._document_of(staged, draft["digest"])
                    if data is None:
                        raise StoreRefusal("document_missing")
                    fresh = revised is None
                    self._land(target, data, fresh, None if revised is None else revised["digest"])
                    landed = True
                    self._c.execute(
                        "INSERT OR REPLACE INTO entries (slug, title, summary, tags, fonts, "
                        "font_links, digest, published_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (slug, title, summary, json.dumps(tags), draft["fonts"],
                         draft["font_links"], draft["digest"], first, when))
                    self._c.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
            except BaseException:
                if landed:
                    self._undo(target, fresh)
                raise
            if not fresh:
                self._finish(target)
            self._drop(staged)
            published = self.entry(slug)
            assert published is not None    # committed above, and the lock is still held
            return published

    def entry(self, slug) -> dict | None:
        """The entry, or ``None`` - also for an address that is not one."""
        try:
            _old_slug(slug)
        except StoreRefusal:
            return None
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
        """An entry's document. ``None`` when there is no such entry, when its
        document is not on disk as its row describes it, and for an address
        that is not one."""
        try:
            folder = self._published(slug)
        except StoreRefusal:
            return None
        with self._lock:
            row = self._c.execute("SELECT digest FROM entries WHERE slug=?",
                                  (slug,)).fetchone()
            data = None if row is None else self._document_of(folder, row["digest"])
        return None if data is None else data.decode("utf-8")

    def unpublish(self, slug) -> bool:
        """Remove an entry: its row, then its folder. False when there was no
        such entry, or the address is not one."""
        try:
            folder = self._published(slug)
        except StoreRefusal:
            return False
        with self._lock:
            with self._write():
                gone = self._c.execute("DELETE FROM entries WHERE slug=?",
                                       (slug,)).rowcount == 1
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
        """One typeface from the pool. ``None`` when it is not there, when the
        name is not a typeface's, and when the file is not what its name says
        (``blog_inbox.read_font``): a typeface altered on disk is not served."""
        with self._lock:
            return blog_inbox.read_font(self._dir / FONTS, name)

    def prune_fonts(self) -> int:
        """Delete every typeface in the pool that nothing names; how many went.

        Only a FILE named like a typeface is ever deleted: a link of that name
        is skipped, and so is one that will not go (logged). Safe beside
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
                path = self._font(name)
                if _is_link(path):
                    continue
                try:
                    path.unlink()
                    gone += 1
                except OSError:
                    log.warning("blog store: an unused typeface could not be removed")
        return gone

    # ── putting an interrupted write right ─────────────────────────────────
    def repair(self) -> dict:
        """Make the files agree with the rows (see the module docstring for
        each state). Run at service start. NEVER raises.

        Each item is its own guarded step: one that fails is recorded and the
        rest still run, so one document that cannot be moved yet does not
        leave every orphan in place. And each row it removes is removed in a
        transaction of its own, committed before it is listed - the report
        describes what is TRUE after the call, whatever else went wrong.

        Returns ``ok`` (False when anything failed, and that is counted for
        /health); ``failed``, the KINDS of step that did (``draft``,
        ``staging``, ``entry``, ``published``, ``fonts``, ``rename``, or
        ``repair`` for the run itself - never a name); the ids in
        ``staging_removed`` and ``drafts_removed``; the addresses in
        ``published_removed`` and ``entries_missing`` (rows that were KEPT and
        have no document); the names in ``fonts_missing``; what it would not
        touch in ``left_alone``; and two counts, ``settled`` (renames finished)
        and ``temp_removed``."""
        report = {"ok": True, "staging_removed": [], "drafts_removed": [],
                  "published_removed": [], "entries_missing": [], "fonts_missing": [],
                  "left_alone": [], "failed": [], "settled": 0, "temp_removed": 0}
        try:
            with self._lock:
                self._mend(report)
        except Exception:          # noqa: BLE001 - the service must still start
            report["failed"].append("repair")
            _degrade.degraded("blog.store", detail="repair did not finish")
        else:
            if report["failed"]:
                _degrade.degraded("blog.store", exc_info=False,
                                  detail="repair left: " + ", ".join(sorted(set(report["failed"]))))
        report["failed"] = sorted(set(report["failed"]))
        report["left_alone"].sort()
        report["ok"] = not report["failed"]
        return report

    def _step(self, report, kind, work, *args) -> None:
        """Run one piece of ``repair``. A failure is logged with its traceback
        (a path at most, never content), recorded by ``kind``, and does not
        stop the pieces after it."""
        try:
            work(report, *args)
        except Exception:          # noqa: BLE001 - recorded; the run goes on
            log.warning("blog store: repair could not finish a step (%s)", kind, exc_info=True)
            report["failed"].append(kind)

    def _heal(self, folder, digest, report) -> str:
        """Where the document a row with ``digest`` describes is:

        * ``"there"`` - under its final name (an interrupted rename is finished
          to get it there, and a ``NEXT`` nothing committed is cleared);
        * ``"waiting"`` - whole, under ``NEXT``, and the rename is still
          refused. Nothing is missing; it is recorded and tried again later;
        * ``"missing"`` - nowhere.

        A file that exists and cannot be READ raises: "missing" gets a draft
        deleted, and a locked file is not a missing one."""
        final, waiting = folder / DOC, folder / NEXT
        if _digest_at(final, strict=True) == digest:
            if os.path.lexists(waiting):
                with contextlib.suppress(OSError):
                    waiting.unlink()
                    report["temp_removed"] += 1
            return "there"
        if _digest_at(waiting, strict=True) != digest:
            return "missing"
        try:
            os.replace(waiting, final)
        except OSError:
            report["failed"].append("rename")
            return "waiting"
        report["settled"] += 1
        return "there"

    def _sweep(self, folder, report) -> None:
        """Delete this module's own half-written files in ``folder``."""
        for child in _listing(folder):
            if _TMP_RE.match(child.name) and not _is_link(child):
                with contextlib.suppress(OSError):
                    child.unlink()
                    report["temp_removed"] += 1

    def _mend_draft(self, report, draft_id, digest, kept) -> None:
        if not blog_inbox.is_id(draft_id):
            # Not a name this module would write, so no path is built from it.
            log.warning("blog store: a draft row has an id that is not one; left alone")
            return
        folder = self._staging(draft_id)
        if self._heal(folder, digest, report) != "missing":
            self._sweep(folder, report)
            return
        with self._write():
            self._c.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
        # Committed. Only now is it true that the draft is gone.
        kept.discard(draft_id)
        report["drafts_removed"].append(draft_id)
        # By id only. What the draft said is not for a log.
        log.warning("blog store: draft %s has no document on disk and was removed", draft_id)
        self._drop(folder)

    def _mend_entry(self, report, slug, digest) -> None:
        try:
            folder = self._published(slug)
        except StoreRefusal:
            log.warning("blog store: an entry row has an address that is not one; left alone")
            return
        state = self._heal(folder, digest, report)
        if state == "missing":
            log.warning("blog store: entry %s has no document on disk; its row is kept", slug)
            report["entries_missing"].append(slug)
        else:
            self._sweep(folder, report)

    def _mend_folder(self, report, child, kind, named, listed, kept) -> None:
        """One thing found under ``staging/`` or ``published/``. Removed only
        when it is a real folder (or file) whose name ``named`` accepts and no
        row has; a link, and anything not named like this module's own, is
        listed and left."""
        if not named(child.name) or _is_link(child):
            report["left_alone"].append(f"{kind}/{child.name}")
        elif child.name not in kept:
            if not self._drop(self._inside(kind, child.name)):
                raise OSError("a folder no row names could not be removed")
            listed.append(child.name)

    def _mend_fonts(self, report) -> None:
        pool = self._dir / FONTS
        self._sweep(pool, report)
        on_disk = {child.name for child in _listing(pool)}
        report["fonts_missing"] = sorted(self._named_fonts() - on_disk)

    def _mend(self, report) -> None:
        drafts = {row["id"]: row["digest"] for row in self._c.execute(
            "SELECT id, digest FROM drafts").fetchall()}
        kept = set(drafts)
        for draft_id, digest in sorted(drafts.items()):
            self._step(report, "draft", self._mend_draft, draft_id, digest, kept)
        for child in _listing(self._dir / STAGING):
            self._step(report, "staging", self._mend_folder, child, STAGING,
                       blog_inbox.is_id, report["staging_removed"], kept)

        entries = {row["slug"]: row["digest"] for row in self._c.execute(
            "SELECT slug, digest FROM entries").fetchall()}
        for slug, digest in sorted(entries.items()):
            self._step(report, "entry", self._mend_entry, slug, digest)

        def an_address(name) -> bool:
            try:
                return _old_slug(name) == name
            except StoreRefusal:
                return False
        for child in _listing(self._dir / PUBLISHED):
            self._step(report, "published", self._mend_folder, child, PUBLISHED,
                       an_address, report["published_removed"], set(entries))
        self._step(report, "fonts", self._mend_fonts)

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
            raise StoreRefusal("bad_name")
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
            raise StoreRefusal("bad_name")
        try:
            # ASCII out: a lone surrogate inside a value is written as an
            # escape and comes back as it went in, where the raw character
            # would fail at the database.
            text = json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            raise StoreRefusal("bad_input") from None
        with self._write():
            self._c.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, text))
