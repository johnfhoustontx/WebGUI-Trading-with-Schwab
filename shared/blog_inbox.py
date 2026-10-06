"""The site Blog's requests, their validators, the frame policy and its config.

An entry on neuralstrike.co/blog is a self-contained HTML document written in
Claude Chat. It arrives as a DRAFT - through the connector gate
(``services/blog_gate``, phase 2) or an upload on the private Blog page - and
reaches the public site only when the operator presses Publish. Three
processes take part, and all three import this module, so the stream names, the
command shapes, the validators and the limits cannot drift between the one that
asks and the one that answers:

* ``blog_svc`` owns drafts and entries and is the only writer of the site's
  blog files. It runs every validator here AGAIN on what it reads: a command
  on a stream proves nothing about who built it.
* the private app's ``/blog`` page enqueues on ``cmd:blog`` (``OWNER_DOMAIN``).
* the gate, the one internet-facing piece, may append to ``INBOX_STREAM`` and
  nowhere else.

Design: docs/plans/2026-10-06-site-blog-design.md.

⚠ **Nothing publishes without the operator.** ``submit_command`` and
``revise_command`` are the only builders whose output belongs on
``INBOX_STREAM``, and neither can produce a publish, discard or unpublish:
those come from ``owner_command`` and travel on ``cmd:blog``. The service
enforces the same split with two handler tables; this pins the builders.

⚠ ``INBOX_STREAM`` will also be named in the gate's Redis ACL write selector
(phase 2). Renaming it here without the ACL makes every connector call fail
with NOPERM.

⚠ ``cache:blog:*`` is readable by the public live process. No view named here
may ever carry a document, a connection code or a token.

On the Tier-1 allow-list for the same reason as ``shared.public_scan``: it
holds validators and config and imports stdlib, ``repo_paths`` and
``shared.config_toml`` only - no engine, no bus, no service, nothing that
parses HTML. ``shared/tests/test_blog_inbox.py`` pins the import set.

Missing file / bad TOML / bad value -> the built-in defaults, never a raise.
"""
import math
import re
import secrets
import unicodedata

from repo_paths import BLOG_TOML
from shared.config_toml import toml_loader

# ── the streams and the views ────────────────────────────────────────────────

# The gate's ONLY write. Deliberately not ``cmd:blog``: that stream carries the
# operator's Publish, and the gate's Redis user must be unable to reach it.
INBOX_STREAM = "cmd:blog_inbox"
# The private app's commands go on ``cmd:<OWNER_DOMAIN>``, the spelling
# ``bus_client.request(domain, command)`` takes.
OWNER_DOMAIN = "blog"

# Cache VIEWS (Tier-1 spelling; the key is ``cache:<view>``). Drafts waiting,
# published entries, and how the last command ended. Metadata only.
VIEW_DRAFTS, VIEW_POSTS, VIEW_RESULT = "blog:drafts", "blog:posts", "blog:result"

# ⚠ Every pattern ends in ``\Z``, not ``$``. ``$`` also matches BEFORE a final
# newline, so ``^[a-z]+$`` with ``.match`` accepts "abc\n" - and each of these
# strings becomes a name: a Redis key (an id), a folder under the served root (a
# slug), a file the private preview opens (a font). With ``\Z`` a caller is safe
# whichever of ``match`` / ``fullmatch`` it reaches for.

# Draft ids and request ids: ``new_id()`` makes them, nothing else does.
ID_RE = re.compile(r"^[0-9a-f]{16}\Z")
# An entry's address. An allow-list, because a slug is a FOLDER NAME under the
# public file server's root: lower-case words joined by single hyphens, so no
# dot, no slash, no leading or doubled hyphen can be spelled at all.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")
# ``blog/fonts/`` is the typeface folder. An entry with that address would be
# written over it, and unpublishing the entry would delete every typeface.
RESERVED_SLUGS = frozenset({"fonts"})
# A typeface on disk is named by a hash of its content (``blog_svc/fonts.py``).
FONT_NAME_RE = re.compile(r"^[0-9a-f]{20}\.woff2\Z")


def is_id(raw) -> bool:
    """Whether ``raw`` is an id ``new_id`` could have made."""
    return isinstance(raw, str) and ID_RE.match(raw) is not None


def new_id() -> str:
    """A fresh draft or request id: 64 random bits, 16 hex characters."""
    return secrets.token_hex(8)


def answer_view(request_id) -> str:
    """The cache VIEW holding how ONE connector request ended.

    Every inbox request gets one, a refusal as much as a result, and it expires
    (``[limits] answer_keep_sec``): the gate polls its own answer and never a
    map of everyone's.

    Raises ``ValueError`` for anything that is not an id. The id becomes part of
    a Redis KEY NAME, so a string this module did not make is never spliced into
    one - and a raise, unlike an empty string, cannot be written to by mistake.
    Check ``is_id`` first on a path that must not raise."""
    if not is_id(request_id):
        raise ValueError("not a blog request id")
    return f"blog:answer:{request_id}"


# ── how an entry is isolated ─────────────────────────────────────────────────

# One definition for the three places that must agree: the frame the service
# writes, the header the edge sends, the private preview.
#
# ⚠ ``allow-same-origin`` is there for the typefaces, and ``allow-scripts`` must
# NEVER join it. Without it the framed document has an opaque origin and its
# font requests are cross-origin: the site would need CORS headers on
# /blog/fonts/, and the private preview's font requests would carry no session
# cookie and be refused by the login. With it and no ``allow-scripts`` nothing
# runs, so the shared origin gives the document nothing to use. The two together
# would let a framed document remove its own sandbox.
ENTRY_SANDBOX = "allow-same-origin allow-popups allow-popups-to-escape-sandbox"
# Sent on the entry document itself, so the same holds when it is opened outside
# its frame. No ``script-src``: ``default-src 'none'`` governs it.
ENTRY_CSP = ("default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; "
             "img-src 'self' data:; base-uri 'none'; form-action 'none'; "
             f"frame-ancestors 'self'; sandbox {ENTRY_SANDBOX}")


# ── the config ───────────────────────────────────────────────────────────────

DEFAULTS = {
    "site": {
        # Off = drafts still arrive and can be previewed; nothing is written
        # under deploy/site/blog/.
        "enabled": True,
        # Minutes between re-publishing the views. They are published on every
        # change; this heals a flushed Redis.
        "republish_min": 30,
    },
    "limits": {
        # The largest document accepted, in KB of UTF-8, BEFORE cleaning. The
        # example entry is 35 KB.
        "max_html_kb": 512,
        # Drafts waiting at once; a new one is refused past this.
        "max_drafts": 20,
        # Drafts the connector may file in an hour.
        "submissions_per_hour": 12,
        # The longest title, summary, tag and address kept, in characters, and
        # how many tags. A longer title, summary or tag is CUT; a longer address
        # is REFUSED (half an address is a different address).
        "title_chars": 140,
        "summary_chars": 300,
        "max_tags": 6,
        "tag_chars": 24,
        "slug_chars": 80,
        # A connector request older than this is answered "expired" unprocessed:
        # it covers a replayed backlog and a queue that has fallen behind.
        "max_wait_sec": 120,
        # How long a connector answer key lives. Every one must expire or their
        # count grows without limit.
        "answer_keep_sec": 120,
    },
    "fonts": {
        # Copy the typefaces an entry asks Google Fonts for onto this box.
        "enabled": True,
        # The character sets copied; each is a separate file per weight.
        "subsets": ["latin", "latin-ext"],
        "max_files": 24,
        "max_file_kb": 400,
        "timeout_sec": 10,
    },
}

# ``(lowest, highest)`` each number may be. Outside them - or not a number at
# all - a value reads as the shipped one. Data rather than arguments scattered
# through the accessors, so the Settings catalogue (webgui/config_schema.py) can
# offer exactly the range that is enforced here.
#
# The ceilings are not decoration. A document travels inside ONE stream entry
# and the command streams keep about a thousand entries, so ``max_html_kb``
# bounds Redis memory as well as a request. ``slug_chars`` has a FLOOR because
# ``slugify`` must always have room for its fallback.
BOUNDS = {
    ("site", "republish_min"): (1, 1440),
    ("limits", "max_html_kb"): (1, 4096),
    ("limits", "max_drafts"): (1, 200),
    ("limits", "submissions_per_hour"): (1, 600),
    ("limits", "title_chars"): (1, 300),
    ("limits", "summary_chars"): (1, 1000),
    ("limits", "max_tags"): (1, 24),
    ("limits", "tag_chars"): (1, 64),
    ("limits", "slug_chars"): (16, 120),
    ("limits", "max_wait_sec"): (1, 3600),
    ("limits", "answer_keep_sec"): (1, 3600),
    ("fonts", "max_files"): (1, 200),
    ("fonts", "max_file_kb"): (1, 4096),
    ("fonts", "timeout_sec"): (1, 60),
}

# A subset as Google's stylesheet names one ("latin-ext", "cyrillic"). Never a
# path, but still held to the characters a name has.
_SUBSET_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_SUBSET_CHARS = 32
_MAX_SUBSETS = 16

load, reset = toml_loader(BLOG_TOML, DEFAULTS, label="blog.toml")


def _section(name) -> dict:
    """One config table. ``{}`` when the file put something that is not a table
    under that name (``limits = 5``): the merge lets a scalar replace a table,
    and ``.get`` on an int would be the raise this module promises not to make."""
    table = load().get(name)
    return table if isinstance(table, dict) else {}


def _bounded(section, key):
    """A config number of the default's own type inside its ``BOUNDS``, or the
    default. A bool is refused (``True`` is an int and would read as 1), and so
    are nan and inf, which TOML accepts and ``int()`` raises on."""
    default = DEFAULTS[section][key]
    low, high = BOUNDS[(section, key)]
    raw = _section(section).get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    if not math.isfinite(raw):
        return default
    value = type(default)(raw)
    return value if low <= value <= high else default


def _flag(section, key) -> bool:
    default = DEFAULTS[section][key]
    raw = _section(section).get(key, default)
    return raw if isinstance(raw, bool) else default


def limits() -> dict:
    """The validated ``[limits]`` table. A fresh dict on every call."""
    return {key: _bounded("limits", key) for key in DEFAULTS["limits"]}


def site() -> dict:
    """The validated ``[site]`` table."""
    return {"enabled": _flag("site", "enabled"),
            "republish_min": _bounded("site", "republish_min")}


def fonts() -> dict:
    """The validated ``[fonts]`` table.

    ``subsets`` keeps each usable name once, in the file's order, and drops the
    rest. A list with NOTHING usable in it - empty included - reads as the
    shipped list: copying no typefaces is what ``enabled = false`` is for, and
    it says so on the page."""
    raw = _section("fonts").get("subsets")
    subsets = []
    for item in raw if isinstance(raw, list) else ():
        if (isinstance(item, str) and len(item) <= _SUBSET_CHARS
                and _SUBSET_RE.match(item) and item not in subsets):
            subsets.append(item)
        if len(subsets) == _MAX_SUBSETS:
            break
    return {"enabled": _flag("fonts", "enabled"),
            "subsets": subsets or list(DEFAULTS["fonts"]["subsets"]),
            "max_files": _bounded("fonts", "max_files"),
            "max_file_kb": _bounded("fonts", "max_file_kb"),
            "timeout_sec": _bounded("fonts", "timeout_sec")}


# ── validation ───────────────────────────────────────────────────────────────

def clean_slug(raw) -> str | None:
    """The address ``raw`` spells, lower-cased and trimmed, or ``None``.

    ``None`` means refuse. A slug becomes a folder name under the served root,
    so this is the gate every one passes: the allow-list pattern, at most
    ``[limits] slug_chars`` long, and not a reserved word. It never repairs a
    string into something usable; ``slugify`` is what makes a new slug."""
    if not isinstance(raw, str):
        return None
    slug = raw.strip().lower()
    if len(slug) > limits()["slug_chars"] or not SLUG_RE.match(slug):
        return None
    return None if slug in RESERVED_SLUGS else slug


# What ``slugify`` returns when a title has nothing an address can be made of.
FALLBACK_SLUG = "entry"
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title) -> str:
    """An address made from a title. ALWAYS one ``clean_slug`` accepts.

    Accents are folded to ASCII, everything else that is not a letter or a
    digit becomes one hyphen, and the result is cut to ``[limits] slug_chars``.
    A title with nothing left ("  ", one written in a script with no ASCII
    form, something that is not text) gives ``FALLBACK_SLUG``; a reserved word
    gets it as a prefix. Uniqueness is the store's business, not this one's."""
    cap = limits()["slug_chars"]
    text = unicodedata.normalize("NFKD", title if isinstance(title, str) else "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NOT_SLUG.sub("-", text).strip("-")[:cap].strip("-")
    if slug in RESERVED_SLUGS:
        slug = f"{FALLBACK_SLUG}-{slug}"[:cap].strip("-")
    return slug if clean_slug(slug) == slug else FALLBACK_SLUG


def _text(raw, cap) -> str:
    """One line of plain text, at most ``cap`` characters, or ``""``.

    Anything that is not a ``str`` is ``""``. Whitespace runs (newlines
    included) collapse to one space, control characters count as whitespace,
    and lone surrogates are dropped: JSON can carry one, and it cannot be
    encoded, so it would raise at the first write to Redis or to disk."""
    if not isinstance(raw, str):
        return ""
    # Bounds the work on a hostile megabyte "title". Collapsing can only shrink
    # the text, so a few times the cap is all that could ever survive the cut -
    # short of a field that is nearly all whitespace, which is not a title.
    raw = raw[:cap * 4 + 64]
    kept = "".join(" " if unicodedata.category(ch) == "Cc" else ch
                   for ch in raw if unicodedata.category(ch) != "Cs")
    return " ".join(kept.split())[:cap].rstrip()


def clean_fields(raw) -> dict:
    """``{"title", "summary", "tags", "slug"}`` from whatever ``raw`` is.

    Always exactly those four keys. Each is text cut to its limit; anything
    unusable is ``""`` (or ``[]`` for tags), never a guess and never a raise.
    Every other key is dropped - the document does not travel in here.

    An empty field means "not given": the service then falls back to the
    document's own title and first paragraph, and to ``slugify`` of the title.
    ⚠ That includes a slug that fails ``clean_slug``. A page that wants to tell
    the operator their address was refused must call ``clean_slug`` itself.

    Tags are compared without case, first spelling kept. Stable on its own
    output, so the gate cleaning and the service cleaning again is one clean."""
    lim = limits()
    src = raw if isinstance(raw, dict) else {}
    tags, seen = [], set()
    listed = src.get("tags")
    for item in listed if isinstance(listed, list) else ():
        if len(tags) == lim["max_tags"]:
            break
        tag = _text(item, lim["tag_chars"])
        if tag and tag.casefold() not in seen:
            seen.add(tag.casefold())
            tags.append(tag)
    return {"title": _text(src.get("title"), lim["title_chars"]),
            "summary": _text(src.get("summary"), lim["summary_chars"]),
            "tags": tags,
            "slug": clean_slug(src.get("slug")) or ""}


def html_ok(html) -> bool:
    """Whether ``html`` is a document worth cleaning: a ``str`` that is not
    blank and is at most ``[limits] max_html_kb`` in UTF-8 BYTES.

    Bytes, because that is what Redis and the disk hold; an accented document
    is larger than its character count. A string that cannot be encoded at all
    (a lone surrogate) is refused here rather than raising at the write."""
    if not isinstance(html, str) or not html.strip():
        return False
    cap = limits()["max_html_kb"] * 1024
    if len(html) > cap:              # every character is at least one byte
        return False
    try:
        return len(html.encode("utf-8")) <= cap
    except UnicodeEncodeError:
        return False


# ── the commands ─────────────────────────────────────────────────────────────

# Where a draft came from. The service does not believe it: it stamps "chat" on
# everything read from INBOX_STREAM and "upload" on everything from cmd:blog.
SOURCES = ("chat", "upload")

SUBMIT_TYPE = "draft_submit"
REVISE_TYPE = "draft_revise"
# Everything that belongs on INBOX_STREAM. No member may ever change what the
# public sees.
INBOX_TYPES = (SUBMIT_TYPE, REVISE_TYPE)


def submit_command(html, fields, *, source, request_id) -> dict | None:
    """The command that files ``html`` as a new draft, or ``None``.

    ``None`` means refuse, before anything is written: a document ``html_ok``
    rejects, a ``source`` that is not one of ``SOURCES``, or a ``request_id``
    that is not an id. ``fields`` goes through ``clean_fields``, so what is
    enqueued is never the caller's dict itself.

    Valid on both streams: the gate puts it on ``INBOX_STREAM``, the private
    page's upload on ``cmd:blog``. Either way it makes a DRAFT."""
    if source not in SOURCES or not is_id(request_id) or not html_ok(html):
        return None
    return {"type": SUBMIT_TYPE,
            "args": {"request_id": request_id, "source": source, "html": html,
                     "fields": clean_fields(fields)}}


def revise_command(draft_id, html, fields, *, request_id) -> dict | None:
    """The command that replaces a draft still waiting, or ``None``. Refuses
    what ``submit_command`` refuses, and a ``draft_id`` that is not an id."""
    if not is_id(draft_id) or not is_id(request_id) or not html_ok(html):
        return None
    return {"type": REVISE_TYPE,
            "args": {"request_id": request_id, "draft_id": draft_id,
                     "html": html, "fields": clean_fields(fields)}}


# The operator's commands, and the ONE target each takes. They travel on
# cmd:blog only; ``owner_command`` is the only thing that builds them.
#   publish    draft_id (+ fields: the title, summary, address and tags as edited)
#   discard    draft_id
#   unpublish  slug
OWNER_KINDS = ("publish", "discard", "unpublish")
_OWNER_ARGS = {"publish": {"draft_id", "fields"},
               "discard": {"draft_id"},
               "unpublish": {"slug"}}


def owner_command(kind, request_id, **args) -> dict | None:
    """One of the operator's commands, or ``None``.

    ``kind`` is one of ``OWNER_KINDS``; ``args`` are that kind's own, by name
    (see the table above). ``None`` for an unknown kind, a bad ``request_id``,
    a missing or unusable target - and for an argument the kind does not take.
    That last one is refused rather than dropped on purpose: the caller is this
    project's own page, so a stray argument is a mistake there, and a discard
    that silently ignored half of what it was handed would hide it."""
    if not isinstance(kind, str) or kind not in OWNER_KINDS:
        return None
    if not is_id(request_id) or not set(args) <= _OWNER_ARGS[kind]:
        return None
    out = {"request_id": request_id}
    if kind == "unpublish":
        slug = clean_slug(args.get("slug"))
        if slug is None:
            return None
        out["slug"] = slug
    else:
        if not is_id(args.get("draft_id")):
            return None
        out["draft_id"] = args["draft_id"]
        if kind == "publish":
            out["fields"] = clean_fields(args.get("fields"))
    return {"type": kind, "args": out}
