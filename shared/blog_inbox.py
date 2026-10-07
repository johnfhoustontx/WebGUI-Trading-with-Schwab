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
import hashlib
import math
import pathlib
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
# Words that have the shape of an address and may never be one.
#
# * ``fonts``: ``blog/fonts/`` is the typeface folder. An entry with that
#   address would be written over it, and unpublishing the entry would delete
#   every typeface.
# * The names Windows gives to devices. A folder called ``con`` or ``nul``
#   cannot be made there, and opening one opens the console or discards what
#   is written. Prod is Linux, where one would simply work - which is how an
#   entry no Windows box can restore (or test) would get published. Refused on
#   every platform, HERE, so the page, the service and the store agree and
#   ``slugify`` turns a title "Con" into ``entry-con`` like any reserved word.
_WINDOWS_DEVICES = frozenset({"con", "prn", "aux", "nul"}
                             | {f"com{n}" for n in range(1, 10)}
                             | {f"lpt{n}" for n in range(1, 10)})
RESERVED_SLUGS = frozenset({"fonts"}) | _WINDOWS_DEVICES
# A typeface on disk is named by a hash of its content (``font_name_for``).
FONT_NAME_RE = re.compile(r"^[0-9a-f]{20}\.woff2\Z")

# ── where the store keeps things, and how anything reads them ───────────────
#
# ``blog_svc`` is the only WRITER of the blog data folder
# (``repo_paths.BLOG_DATA``). Two other things read it without importing the
# service - the private preview route in Tier 1, and the site writer through
# the store - so the layout and the two reads live here, once:
#
#     <data>/staging/<draft id>/entry.html     a draft's document
#     <data>/published/<slug>/entry.html       an entry's document
#     <data>/fonts/<20 hex>.woff2              one pool of typefaces
#
# ⚠ Read a document with ``read_document`` and a typeface with ``read_font``,
# never by opening the file. A row carries the SHA-256 of its document, and
# while a replacement is being put in place the document that row describes is
# under ``NEXT_NAME``, not ``DOC_NAME``: opening ``entry.html`` directly can
# show the PREVIOUS document. The digest is what says which file is the row's.
STAGING_DIR, PUBLISHED_DIR, FONTS_DIR = "staging", "published", "fonts"
DOC_NAME, NEXT_NAME = "entry.html", "entry.html.next"

# A SHA-256 as this project writes one: lower-case hex, and only that spelling.
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}\Z")


def font_name_for(data) -> str:
    """The name a typeface with these bytes is stored under: twenty hex
    characters of its SHA-256, then ``.woff2``. Always matches ``FONT_NAME_RE``.

    Named by content so the same file under two addresses is one file on disk,
    and so a reader can tell a file that is not what its name says."""
    return hashlib.sha256(data).hexdigest()[:20] + ".woff2"


def _file_bytes(path):
    """The bytes at ``path``, or ``None``. Never raises: ``ValueError`` covers a
    path with a NUL in it, ``TypeError`` one that is not a path at all."""
    try:
        return pathlib.Path(path).read_bytes()
    except (OSError, ValueError, TypeError):
        return None


def read_document(folder, digest):
    """The bytes of the document in ``folder`` that ``digest`` names, or ``None``.

    ``folder`` is ``<data>/staging/<id>`` or ``<data>/published/<slug>`` and
    ``digest`` is the row's. ``DOC_NAME`` is tried, then ``NEXT_NAME``; whichever
    has that SHA-256 is the document. ``None`` when neither does - a file
    altered on disk reads as missing, so what is previewed is what was cleaned
    and what is served is what was previewed - when ``digest`` is not 64
    lower-case hex characters, when the folder is not there, and on any error
    reading. **Never raises.**

    Bytes, not text: nothing is decoded and no newline is translated."""
    if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
        return None
    try:
        base = pathlib.Path(folder)
    except TypeError:
        return None
    for name in (DOC_NAME, NEXT_NAME):
        data = _file_bytes(base / name)
        if data is not None and hashlib.sha256(data).hexdigest() == digest:
            return data
    return None


def read_font(fonts_folder, name):
    """The bytes of the typeface ``name`` in ``fonts_folder``, or ``None``.

    Only for a ``name`` matching ``FONT_NAME_RE`` - checked BEFORE it is joined
    to a path, so nothing outside the folder can be asked for - and only when
    the file's content is what the name says (``font_name_for``): a typeface
    altered on disk is not served. **Never raises.**"""
    if not isinstance(name, str) or not FONT_NAME_RE.match(name):
        return None
    try:
        path = pathlib.Path(fonts_folder) / name
    except TypeError:
        return None
    data = _file_bytes(path)
    return data if data is not None and font_name_for(data) == name else None


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
#
# ``img-src data:`` and NOT ``'self'``. The cleaner takes out every ``img`` and
# every CSS ``url()`` that is not a reference to an id in the same document, so
# ``'self'`` would buy an entry nothing. And the private preview is served on the
# APP's origin: there ``'self'`` is the one source that would let a miss in the
# cleaner send a GET, carrying the session cookie, to a route of the app's own.
ENTRY_CSP = ("default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; "
             "img-src data:; base-uri 'none'; form-action 'none'; "
             f"frame-ancestors 'self'; sandbox {ENTRY_SANDBOX}")


# ── the config ───────────────────────────────────────────────────────────────

DEFAULTS = {
    "site": {
        # Off = drafts still arrive and can be previewed; nothing is written
        # under deploy/site/blog/.
        "enabled": True,
        # Minutes between re-publishing the views and re-checking the site's
        # blog files against the store. Both happen on every change; this heals
        # a flushed Redis, retries a failed site write, and catches the site up
        # after ``enabled`` is switched back on.
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
        # Each document is cleaned in a WORKER PROCESS killed after this many
        # seconds; an overrun is refused ("too_slow"). The HTML parser's cost is
        # not linear in the input (one tag with tens of thousands of attributes
        # takes minutes at the size limit), and this - not a pre-parse scan - is
        # what bounds it. Must stay well under max_wait_sec, so an overrun is
        # answered before the request itself expires.
        "clean_sec": 20,
        # The address space the clean worker may use, in MB, lowered with
        # setrlimit on POSIX (on Windows only the wall-clock kill applies). It
        # contains a memory blow-up inside the parser as the time limit contains
        # a slow one.
        "clean_mem_mb": 512,
    },
    "fonts": {
        # Copy the typefaces an entry asks Google Fonts for onto this box.
        "enabled": True,
        # The character sets copied; each is a separate file per weight.
        "subsets": ["latin", "latin-ext"],
        # The typeface stylesheets followed for one entry, and the most one may
        # send back, in KB. The cleaner hands over EVERY link it found, so this
        # is where their number is held.
        "max_links": 4,
        "max_css_kb": 256,
        "max_files": 24,
        "max_file_kb": 400,
        # The bytes of ALL of one entry's files together, in MB. They are held
        # in memory at once until the store writes them, and the two limits
        # above only bound that as a product. As shipped it is above what they
        # allow (24 x 400 KB is 9.6 MB), so it binds once one of them is raised.
        "max_total_mb": 12,
        # The @font-face rules written into one entry, across all its
        # stylesheets. ``max_files`` does not bound this: many rules can name
        # one file (a variable face asked for by weight is one rule per weight),
        # and the rules are pasted into the entry AFTER its own size limit was
        # applied. 96 is three families in four weights, upright and italic, in
        # four character sets.
        "max_rules": 96,
        # Seconds one request may take, and seconds ALL of an entry's requests
        # may take together. The second is the one that protects the queue: 24
        # files timing out one after another at 10 s each would hold the
        # service for four minutes, twice ``[limits] max_wait_sec``.
        "timeout_sec": 10,
        "total_sec": 30,
        # Who the service says it is when it asks Google Fonts. Google chooses
        # what to send by who is asking: a client it does not know is sent
        # TrueType with no character-set split, a desktop Chrome is sent woff2.
        # It ages. If drafts start saying their typeface rules were not usable,
        # put a current Chrome's User-Agent here; that needs no code change.
        "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"),
    },
}

# ``(lowest, highest)`` each number may be. Outside them - or not a number at
# all - a value reads as the shipped one. Data rather than arguments scattered
# through the accessors, so the Settings catalogue (webgui/config_schema.py) can
# offer exactly the range that is enforced here.
#
# The ceilings are not decoration. A document travels inside ONE stream entry,
# and each of the two blog streams keeps its newest ``[stream_keep]`` entries
# (config/services.toml: shipped 50, at most 500 for these two streams -
# ``shared.service_limits.STREAM_KEEP_CEILINGS``). ``max_html_kb`` times that
# number is what one stream can hold of Redis memory:
#     512 KB x  50   about  25 MB   as shipped
#    4096 KB x  50   about 200 MB   this limit at its ceiling
#     512 KB x 500   about 250 MB   the stream's at its ceiling
#    4096 KB x 500   about   2 GB   both at once
# The last line is why each has a ceiling, and it is still a number to stay
# away from. ``slug_chars`` has a FLOOR because ``slugify`` must always have
# room for its fallback, and its CEILING is what an entry that already exists
# is held to (``existing_slug``).
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
    ("limits", "clean_sec"): (2, 120),
    ("limits", "clean_mem_mb"): (128, 4096),
    ("fonts", "max_links"): (1, 16),
    ("fonts", "max_css_kb"): (16, 2048),
    # Three bounds on one quantity: the bytes a typeface copy holds in memory.
    # The ceilings agree - 64 files of 1,024 KB is 64 MB. (They were 200 files
    # of 4,096 KB with no total: 800 MB.)
    ("fonts", "max_files"): (1, 64),
    ("fonts", "max_file_kb"): (16, 1024),
    ("fonts", "max_total_mb"): (1, 64),
    ("fonts", "max_rules"): (1, 1000),
    ("fonts", "timeout_sec"): (1, 60),
    ("fonts", "total_sec"): (1, 600),
}

# A subset as Google's stylesheet names one ("latin-ext", "cyrillic"). Never a
# path, but still held to the characters a name has - which is the shape of an
# address exactly (lower-case words joined by single hyphens), so it is the one
# pattern and not a second copy of it to keep in step.
_SUBSET_RE = SLUG_RE
_SUBSET_CHARS = 32
_MAX_SUBSETS = 16

# ``(shortest, longest)`` a User-Agent may be, in characters. It is sent as a
# request header, so beyond its length it must be printable ASCII (no line
# break can start a second header) with no space at either end (``requests``
# refuses a header value that starts with one). The Settings catalogue refuses
# the same things, and a test pins the two to each other.
USER_AGENT_CHARS = (20, 300)

# ``reset_cache``, as every other config module here names its own.
load, reset_cache = toml_loader(BLOG_TOML, DEFAULTS, label="blog.toml")


def _section(name) -> dict:
    """One config table. ``{}`` when the file put something that is not a table
    under that name (``limits = 5``): the merge lets a scalar replace a table,
    and ``.get`` on an int would be the raise this module promises not to make."""
    table = load().get(name)
    return table if isinstance(table, dict) else {}


def _number(raw, section, key, otherwise):
    """``raw`` as a number of the default's own type inside its ``BOUNDS``, or
    ``otherwise``. A bool is refused (``True`` is an int and would read as 1),
    and so are nan and inf, which TOML accepts and ``int()`` raises on. A float
    for a whole-number setting is cut to one (59.5 seconds is 59)."""
    low, high = BOUNDS[(section, key)]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return otherwise
    if not math.isfinite(raw):
        return otherwise
    value = type(DEFAULTS[section][key])(raw)
    return value if low <= value <= high else otherwise


def _bounded(section, key):
    """The file's number for ``key``, or the shipped one (see ``_number``)."""
    default = DEFAULTS[section][key]
    return _number(_section(section).get(key, default), section, key, default)


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


def _subsets(raw) -> list:
    """The usable names in ``raw``, each once, in order; ``[]`` when there are
    none. Usable is the SHAPE of a name (lower-case words joined by single
    hyphens). Whether Google uses it is not asked: there is no list of its
    names to ask, and one it does not use simply matches no stylesheet."""
    kept = []
    for item in raw if isinstance(raw, (list, tuple)) else ():
        if (isinstance(item, str) and len(item) <= _SUBSET_CHARS
                and _SUBSET_RE.match(item) and item not in kept):
            kept.append(item)
        if len(kept) == _MAX_SUBSETS:
            break
    return kept


def _is_user_agent(raw) -> bool:
    """Whether ``raw`` can be sent as a User-Agent header (``USER_AGENT_CHARS``)."""
    low, high = USER_AGENT_CHARS
    return (isinstance(raw, str) and low <= len(raw) <= high
            and all(" " <= ch <= "~" for ch in raw) and raw == raw.strip(" "))


def _fonts_from(table, otherwise) -> dict:
    """``table`` read as a ``[fonts]`` table, key by key. A key that is absent
    or not usable reads as ``otherwise[key]``. The ONE reading of these
    settings: the file's and a caller's both come through here."""
    out = {}
    for key in DEFAULTS["fonts"]:
        raw, fallback = table.get(key), otherwise[key]
        if key == "enabled":
            out[key] = raw if isinstance(raw, bool) else fallback
        elif key == "subsets":
            out[key] = _subsets(raw) or list(fallback)
        elif key == "user_agent":
            out[key] = raw if _is_user_agent(raw) else fallback
        else:
            out[key] = _number(raw, "fonts", key, fallback)
    return out


def fonts(over=None) -> dict:
    """The validated ``[fonts]`` table. A fresh dict on every call.

    ``subsets`` keeps each usable name once, in the file's order, and drops the
    rest. A list with NOTHING usable in it - empty included - reads as the
    shipped list: copying no typefaces is what ``enabled = false`` is for, and
    it says so on the page.

    ``over`` is a caller's own table (``blog_svc.fonts.localize(cfg=...)``),
    laid over the FILE's settings and read by the same rules: a key it leaves
    out, or gives a value the file could not have held, keeps the file's. So a
    caller cannot switch one of the operator's limits off by handing in a bad
    number, and the two can never disagree about what a usable value is."""
    read = _fonts_from(_section("fonts"), DEFAULTS["fonts"])
    return _fonts_from(over, read) if isinstance(over, dict) else read


# ── validation ───────────────────────────────────────────────────────────────

# The longest address ANY setting of ``[limits] slug_chars`` could have allowed.
SLUG_CHARS_CEILING = BOUNDS[("limits", "slug_chars")][1]


def _slug(raw, longest) -> str | None:
    """``raw`` lower-cased, if it is an address of at most ``longest``
    characters. The one check behind ``clean_slug`` and ``existing_slug``.

    Only plain SPACES are trimmed: a typed address may carry one at either end.
    A newline, a tab or any other whitespace is not something a text field
    produces, so it is refused by the pattern rather than quietly removed."""
    if not isinstance(raw, str) or not raw.isascii():
        # ASCII is checked BEFORE lower-casing. ``str.lower`` is Unicode-aware:
        # U+212A, the Kelvin sign, lowers to a plain "k", so lower-casing first
        # turned one address into a different, valid one. This function
        # refuses; ``slugify`` is the one that repairs.
        return None
    slug = raw.strip(" ").lower()
    if len(slug) > longest or not SLUG_RE.match(slug):
        return None
    return None if slug in RESERVED_SLUGS else slug


def clean_slug(raw) -> str | None:
    """The address ``raw`` spells, lower-cased and trimmed, or ``None``.

    For a NEW address: a draft's, or the one the operator types before Publish.
    ``None`` means refuse. A slug becomes a folder name under the served root,
    so this is the gate every one passes: the allow-list pattern, at most
    ``[limits] slug_chars`` long, and not a reserved word. It never repairs a
    string into something usable; ``slugify`` is what makes a new slug."""
    return _slug(raw, limits()["slug_chars"])


def existing_slug(raw) -> str | None:
    """The address of an entry that ALREADY exists, or ``None``.

    The same allow-list as ``clean_slug``, held to the CEILING of
    ``slug_chars`` instead of its current setting. An entry published at 60
    characters keeps that address when the limit is later lowered to 40; held
    to the current limit, nothing could name it again and it could never be
    unpublished. Use this to FIND or REMOVE an entry, never to create one."""
    return _slug(raw, SLUG_CHARS_CEILING)


# What ``slugify`` returns when a title has nothing an address can be made of.
FALLBACK_SLUG = "entry"
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title) -> str:
    """An address made from a title. ALWAYS one ``clean_slug`` accepts.

    Accents are folded to ASCII, everything else that is not a letter or a
    digit becomes one hyphen, and the result is cut to ``[limits] slug_chars``.
    A title with nothing left ("  ", one written in a script with no ASCII
    form, something that is not text) gives ``FALLBACK_SLUG``; a reserved word
    gets it as a prefix. Uniqueness is the store's business, not this one's.

    ⚠ The title is cut BEFORE it is folded. One character can fold to many
    (U+FDFA to eighteen), so folding a hostile title whole is where the time
    went: 4.4 s on 170,000 of them. Only the head of a title can reach an
    address of ``slug_chars`` characters anyway."""
    cap = limits()["slug_chars"]
    head = title[:cap * 4 + 64] if isinstance(title, str) else ""
    text = unicodedata.normalize("NFKD", head)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NOT_SLUG.sub("-", text).strip("-")[:cap].strip("-")
    if slug in RESERVED_SLUGS:
        slug = f"{FALLBACK_SLUG}-{slug}"[:cap].strip("-")
    return slug if clean_slug(slug) == slug else FALLBACK_SLUG


# What ``_text`` removes outright, and it is a LIST, not a promise.
#
# By Unicode category: lone surrogates (Cs) and format characters (Cf).
_REMOVED_CATEGORIES = frozenset({"Cs", "Cf"})
# By code point, because their categories are a letter's, a symbol's and a
# mark's, so no category rule can catch them: the "filler" letters that draw as
# a blank (U+3164 Hangul filler, U+115F and U+1160 the two Hangul jamo fillers,
# U+FFA0 the halfwidth one), U+2800 the blank Braille pattern, and the sixteen
# variation selectors U+FE00..U+FE0F. Written as numbers: see the note at the
# top of shared/tests/test_blog_inbox.py on invisible characters in source.
BLANK_POINTS = frozenset({0x3164, 0x115F, 0x1160, 0xFFA0, 0x2800}
                         | set(range(0xFE00, 0xFE10)))
_BLANK = frozenset(chr(point) for point in BLANK_POINTS)
# How far into a field ``_text`` looks for something to keep. It reads character
# by character, so this is what bounds the work on a hostile megabyte "title".
_SCAN_CHARS = 16384


def _text(raw, cap) -> str:
    """One line of plain text, at most ``cap`` characters, or ``""``.

    Anything that is not a ``str`` is ``""``. Whitespace runs (newlines
    included) collapse to one space and control characters count as whitespace.
    These are removed outright - not turned into a space, because they sit
    INSIDE words:

    * lone surrogates (``Cs``): JSON can carry one, and it cannot be encoded,
      so it would raise at the first write to Redis or to disk;
    * FORMAT characters (``Cf``): zero-width space, non-joiner and joiner, word
      joiner, soft hyphen, the byte-order mark, the bidi marks, embeddings,
      overrides and isolates. A title made of them is a blank row that reads as
      "has a title", two tags can differ by one and look the same, and a bidi
      override reorders whatever follows it on the page;
    * the characters in ``BLANK_POINTS``: the blank "filler" letters, the blank
      Braille pattern and the variation selectors U+FE00..U+FE0F.

    ⚠ Two costs, both accepted for a title, a summary and a tag (an entry's
    BODY never passes through here). Removing the zero-width JOINER splits a
    compound emoji into its members - a family into its people. Removing the
    variation selectors can turn an emoji drawn in colour into its plain text
    form. And Persian or Indic text that uses the non-joiner loses it.

    ⚠ It is NOT a guarantee that what comes back shows something. Unicode has
    more invisible and blank glyphs than these (other scripts' fillers, combining
    marks with nothing to combine with, and whatever the next version adds), and
    a font can draw any character as nothing. (Tag characters U+E00xx are
    category Cf, so the ``Cf`` rule already removes them.) What IS removed comes
    back as ``""`` when nothing else is left, which every caller already treats
    as "not given".

    The removal happens BEFORE the cut for size. Cut first, 700 zero-width
    spaces in front of a real title used up the whole cut and blanked it. The
    work is bounded instead by ``_SCAN_CHARS`` (how far in it looks) and by
    stopping once a few times ``cap`` has been kept: collapsing whitespace can
    only shrink that, so nothing further in could survive the final cut.

    ⚠ But when the window runs out INSIDE the field - more than ``_SCAN_CHARS``
    characters in and not yet enough kept to be past the final cut - what was
    kept is a PREFIX of a longer field, and half a title is worse than none
    (16,380 zero-width spaces then "Real title" would otherwise come back
    "Real"). That is answered ``""``, not the fragment. A real title is a few
    hundred characters; only a hostile one reaches the window at all."""
    if not isinstance(raw, str):
        return ""
    enough = cap * 4 + 64
    kept, reached_enough = [], False
    for ch in raw[:_SCAN_CHARS]:
        if ch in _BLANK:
            continue
        kind = unicodedata.category(ch)
        if kind in _REMOVED_CATEGORIES:
            continue
        kept.append(" " if kind == "Cc" else ch)
        if len(kept) == enough:
            reached_enough = True
            break
    if len(raw) > _SCAN_CHARS and not reached_enough:
        return ""                   # the window ended inside the field: a prefix, not the field
    return " ".join("".join(kept).split())[:cap].rstrip()


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
    # Only as many as could be kept are even EXAMINED: past max_tags distinct
    # usable ones there is nothing to add, and cleaning each costs work, so a
    # list of a million tags must not be a million calls to ``_text``. Four per
    # kept tag leaves room for blanks and duplicates among them.
    candidates = listed[: lim["max_tags"] * 4] if isinstance(listed, list) else ()
    for item in candidates:
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


def submit_command(html, fields, *, source, request_id, revises=None) -> dict | None:
    """The command that files ``html`` as a new draft, or ``None``.

    ``None`` means refuse, before anything is written: a document ``html_ok``
    rejects, a ``source`` that is not one of ``SOURCES``, or a ``request_id``
    that is not an id. ``fields`` goes through ``clean_fields``, so what is
    enqueued is never the caller's dict itself.

    ``revises`` is the address of the published entry this document REPLACES.
    Left out (``None``), the command has no ``revises`` key at all and files a
    new entry. Given, it must be an address ``existing_slug`` accepts - held to
    the limit's ceiling, because the entry is already out there - and anything
    else builds NO command: a replacement that quietly became a second entry is
    the mistake that refusal stops. Whether such an entry is published is the
    service's to say, not this builder's.

    Valid on both streams: the gate puts it on ``INBOX_STREAM``, the private
    page's upload on ``cmd:blog``. Either way it makes a DRAFT."""
    if source not in SOURCES or not is_id(request_id) or not html_ok(html):
        return None
    args = {"request_id": request_id, "source": source, "html": html,
            "fields": clean_fields(fields)}
    if revises is not None:
        slug = existing_slug(revises)
        if slug is None:
            return None
        args["revises"] = slug
    return {"type": SUBMIT_TYPE, "args": args}


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
#   unpublish  slug (an EXISTING entry's: held to the limit's ceiling, see
#              ``existing_slug``)
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
        # ``existing_slug``, not ``clean_slug``: the entry is already out there
        # under whatever address the limit allowed on the day it was published.
        slug = existing_slug(args.get("slug"))
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
