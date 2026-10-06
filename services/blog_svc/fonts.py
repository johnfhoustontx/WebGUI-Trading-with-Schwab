"""Copying an entry's typefaces onto the box.

An entry written in Claude Chat asks Google Fonts for its typefaces. The public
site loads nothing from another origin (the entry's own policy says ``font-src
'self'``), so when a draft arrives the service fetches those files ONCE, keeps
them under ``blog/fonts/``, and fills the entry's marked ``<style>`` with
``@font-face`` rules that point at the local copies. The entry is served at
``/blog/<slug>/entry.html`` and a typeface at ``/blog/fonts/<name>``, which is
why every rule says ``../fonts/<name>``.

Three steps, one function each:

* ``clean.clean`` found the links (``Cleaned.font_links``) and left
  ``clean.FONT_CSS_MARK`` as the whole text of the first ``<style>``.
* ``localize`` follows the links, reads what Google sends back, fetches the
  files it names, and returns the rules, the files and a note for the operator.
* ``apply`` puts the rules where the mark is (``clean.font_slot`` says where).

**What is written is built, never passed through.** Nothing cleans a document
after ``apply`` (``clean`` would throw the rules away: it drops every
``@font-face``), so the CSS this module returns is trusted by everything after
it. Google's stylesheet is therefore only READ - parsed by tinycss2, the same
tokenizer the cleaner uses, so an escaped ``u\\72l(`` is a url here too - and
each rule is written again from a table of descriptors (``_BEFORE_SRC``,
``_AFTER_SRC``), each with a value this module understood and spelled itself.
The one piece of free text, a family name, is written with every character
outside ``[A-Za-z0-9 _-]`` as a hex escape. The one address is
``../fonts/<20 hex>.woff2``, where the name is a hash of bytes this module
fetched and checked. Then the finished text is held to a pattern built from
that same table (``_RULE_RE``) before it leaves, and again in ``apply``,
because a ``Fonts`` is a plain record anything can build.

**What is fetched is Google's, and bounded.** A link is followed only if it is
``https://fonts.googleapis.com/css2?...``; a file only if it is
``https://fonts.gstatic.com/....woff2``. ``http_fetch`` checks the host again
on the text before it connects, follows no redirect, and counts the body as it
arrives. How many links, how large a stylesheet, how many files, how large a
file, how long one request and how long all of them: ``config/blog.toml
[fonts]``. None of it is capped by the cleaner.

**What is written is bounded too.** Files are not rules: many blocks can name
one file, so ``[fonts] max_rules`` caps the rules, and one rule can be no
longer than ``RULE_CHARS_CEILING``. Their product is the most ``apply`` can add
to an entry, whose own size limit was applied before any of this.

**It never blocks a draft.** ``localize`` raises nothing that is an
``Exception``. A link or a file that fails costs only itself; the entry falls
back to the fonts its own stylesheet names, and ``Fonts.note`` says so in one
sentence made of this module's own words and counts - never an exception's
text, an address or a family name, all of which belong to someone else. The
log is held to the same rule. A failure that is this service's own, rather
than the network's, is counted for ``/health`` under a label that says where
(see "saying what went wrong").

⚠ Google's real answer was never fetched while this was written: the reader is
built against the SHAPE the css2 endpoint is known to send a desktop Chrome.
What to check on the first live copy is step 5 of Task 12 in
docs/plans/2026-10-06-site-blog-plan.md.

Design: docs/plans/2026-10-06-site-blog-design.md ("Typefaces", "How an entry
is isolated").
"""
import enum
import hashlib
import logging
import re
import struct
import unicodedata
from collections import Counter
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlsplit

import requests
import tinycss2
import urllib3

from services import _degrade
from services.blog_svc import _trace, clean
from shared import blog_inbox

log = logging.getLogger("blog_svc.fonts")

# The only two hosts this module ever connects to: the one that serves the
# stylesheet and the one that serves the files. Never interchangeable - a link
# to the file host is not a stylesheet, and a file on the stylesheet host is
# not fetched.
CSS_HOST, FILE_HOST = "fonts.googleapis.com", "fonts.gstatic.com"
_HOSTS = frozenset({CSS_HOST, FILE_HOST})

# The longest link followed. The pattern a link is held to is the cleaner's
# (``clean.FONT_LINK_RE``), and it has no length in it; the example-shaped
# entry's link - three families, axes and all - is 171 characters. Not a config
# key: past this a link is not a longer link, it is something else.
_LINK_CHARS = 2000

# A typeface file: the file host over https, then path segments made of
# letters, digits and ``_ ~ -`` joined by single dots. So no query, no
# fragment, no ``%``, no empty segment and no ``..`` can be spelled.
_SEGMENT = r"[A-Za-z0-9_~-]+(?:\.[A-Za-z0-9_~-]+)*"
_FILE_RE = re.compile(rf"^https://fonts\.gstatic\.com(?:/{_SEGMENT})*/{_SEGMENT}\Z")
_FILE_CHARS = 512

# The longest family name copied: several times the length of any real one.
# Like the two lengths above, a bound on what is worth reading and not a taste.
_FAMILY_CHARS = 100
# What a family name never holds: controls (a line break among them), invisible
# format characters, lone surrogates, line and paragraph separators. A block
# naming one is dropped rather than escaped - no stylesheet an entry carries
# could be asking for that family by name.
_NOT_IN_A_NAME = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})

# The most ranges ONE rule's ``unicode-range`` may list; a block with more is
# not usable. The lists Google sends for a named character set are a few dozen
# entries, and without a limit one block turned a 256 KB stylesheet into 448 KB
# of rule. Not a config key: with ``_FAMILY_CHARS`` it fixes the longest rule
# there can be (``RULE_CHARS_CEILING``), which ``[fonts] max_rules`` multiplies.
MAX_RANGES = 128

# A WOFF2 file starts with a 48-byte header: the signature, a flavor, the
# file's own total length, the number of tables, and eight more fields.
_WOFF2_SIGNATURE = b"wOF2"
_WOFF2_HEADER = 48
# A stored file is named by this many hex characters of its SHA-256
# (``blog_inbox.FONT_NAME_RE`` is the same number, and is checked).
_NAME_HEX = 20

_CHUNK = 16 * 1024
# The longest ONE request may be given: the most ``[fonts] timeout_sec`` may be
# set to. ``http_fetch`` refuses anything longer, because the one caller it has
# cannot have meant it.
_TIMEOUT_CEILING = blog_inbox.BOUNDS[("fonts", "timeout_sec")][1]
# The least time worth starting a request with. With less than this left of
# ``total_sec`` the copy is out of time: a request sent with a few milliseconds
# to live fails, and would be reported as "could not be fetched" - the
# network's fault - when it was the clock's. Half a second is several times
# what a file from Google takes, and far below any ``timeout_sec``.
_USEFUL_SEC = 0.5


class FetchError(Exception):
    """A fetch that connected and got an answer it could not use: a status, a
    size, the time it took. Its text is this module's own - never the address
    or anything the other end sent - so it is safe to log."""


class FetchRefused(FetchError):
    """``http_fetch`` turned down what it was HANDED - an address, headers, a
    limit - before connecting. Not the other end's doing: ``localize`` only
    hands over what its own checks passed, so this is a disagreement inside
    this file, and it is counted as a fault rather than as an outage."""


@dataclass(frozen=True)
class Fonts:
    css: str          # @font-face rules pointing at ../fonts/<name>, "" when none
    files: dict       # {"<20 hex>.woff2": bytes}, in the order first used
    note: str         # "" or one sentence for the operator


# ── the one network call ─────────────────────────────────────────────────────

def _refuse_unless_google(url) -> None:
    """Raise unless ``url`` is https on EXACTLY one of the two hosts.

    Decided on the text, before anything connects. The whole address must be
    printable ASCII with no space and no backslash, because those are the
    characters two URL parsers disagree about, and this check is only worth
    something if the library that connects reads the same host this function
    did. Measured here: given ``https://fonts.gstatic.com\\@evil.test/a``,
    ``urlsplit`` reads the host as ``evil.test`` and urllib3 - which makes the
    connection - reads ``fonts.gstatic.com``; and ``urlsplit`` deletes a tab
    outright. Then the host part - everything between ``//`` and the next
    ``/``, ``?`` or ``#`` - must EQUAL one of the two names. Equality is what
    refuses userinfo, a port, a trailing dot, another case, an IP literal and a
    look-alike in one comparison: none of them is the name."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise FetchRefused("not an https address")
    if not url.isascii() or any(ch <= " " or ch in "\\\x7f" for ch in url):
        raise FetchRefused("an address with a character no address here has")
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:                      # "https://[not-an-ip]/"
        raise FetchRefused("an address that cannot be read") from None
    if parts.netloc not in _HOSTS or host != parts.netloc:
        raise FetchRefused("not one of the two hosts typefaces come from")


def _declared_length(headers):
    """What Content-Length promises, or ``None`` when it promises nothing
    usable. Only ever used to refuse EARLY; the body is counted regardless."""
    try:
        return int(headers.get("Content-Length"))
    except (TypeError, ValueError, AttributeError):
        return None


def http_fetch(url, *, headers, timeout, max_bytes) -> bytes:
    """GET ``url`` from one of Google's two typeface hosts; the body, or a raise.

    * The address is checked before anything connects (``_refuse_unless_google``).
    * No redirect is followed, and anything but a plain 200 is a failure: a
      redirect is an address nobody checked.
    * The body is streamed and counted as it arrives; one byte past
      ``max_bytes`` raises. Content-Length is the other end's word and is used
      only to refuse sooner. The count is of DECODED bytes, so a compressed
      body is held to the limit too.
    * A body still arriving ``timeout`` seconds after the request began is
      given up on.

    ⚠ What ``timeout`` does and does not bound, because "the whole request" is
    more than is true:

    * It is ``requests``' own timeout as well, so the connect and EACH socket
      read are bounded by it - including each read of the status line and the
      headers, which arrive before ``requests.get`` returns and so before the
      check here can run even once.
    * The total is checked only BETWEEN chunks of the body. A body that drips
      just inside the per-read timeout can hold one read longer than
      ``timeout``; only one of Google's two hosts, over verified TLS, is ever
      in a position to do that (``news_svc/fetch.py`` has the socket watchdog
      that would close it).
    * Resolving the host name is not bounded by it at all.

    ``localize`` contains all of that to one overrun: it checks ``[fonts]
    total_sec`` before every request and makes none once it has passed.

    ⚠ The size limit against a compressed body rests on the pinned urllib3.
    Measured against 2.7.0: a 4,892-byte gzip body that inflates to 5 MB comes
    out of ``iter_content(16 KB)`` as 16,384 DECODED bytes at a time - the
    decoder is asked for a chunk, not handed the whole body. A urllib3 that
    inflated a body whole before handing over its first chunk would make this
    limit a check after the fact; the suite has a test that would say so.

    Raises ``FetchRefused`` for an argument it will not act on (nothing was
    sent), ``FetchError`` for an answer it could not use, and lets
    ``requests``' own exceptions through. The connection is always given
    back."""
    _refuse_unless_google(url)
    if not isinstance(headers, dict):
        raise FetchRefused("no headers given")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise FetchRefused("no size limit given")
    timed = isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
    if not timed or not 0 < timeout <= _TIMEOUT_CEILING:        # nan fails both comparisons
        raise FetchRefused("no time limit given")
    started = monotonic()
    response = requests.get(url, headers=dict(headers), timeout=timeout,
                            allow_redirects=False, stream=True)
    try:
        status = response.status_code
        if status != 200:
            # ``requests`` always hands an int here. The text is spelled from a
            # number only so that a test double's odd status cannot put words
            # of its own into a line that is let into the log.
            raise FetchError(f"HTTP {status}" if type(status) is int else "no HTTP status")
        declared = _declared_length(response.headers)
        if declared is not None and declared > max_bytes:
            raise FetchError(f"larger than {max_bytes} bytes")
        body = bytearray()
        for chunk in response.iter_content(chunk_size=_CHUNK):
            body += chunk
            if len(body) > max_bytes:
                raise FetchError(f"larger than {max_bytes} bytes")
            if monotonic() - started > timeout:
                raise FetchError("took longer than its time limit")
        return bytes(body)
    finally:
        response.close()


# ── reading Google's stylesheet ──────────────────────────────────────────────
#
# The answer to a css2 request, to a desktop Chrome, is a run of these:
#
#     /* latin */
#     @font-face {
#       font-family: 'Inter';
#       font-style: normal;
#       font-weight: 100 900;
#       font-display: swap;
#       src: url(https://fonts.gstatic.com/s/inter/v13/....woff2) format('woff2');
#       unicode-range: U+0000-00FF, U+0131, U+0152-0153;
#     }
#
# It is read with tinycss2's RULE parser and never searched with a pattern: a
# pattern for "a block" has to guess where a block ends, and a string, an
# escape or a nested brace is all it takes to guess wrong. The parser hands
# back the comment and the rule as two nodes, so "the comment directly before
# this rule" is a fact about the tree.
#
# Each descriptor has a reader: given the declaration's tokens (whitespace and
# comments taken out), it returns the value as THIS module spells it, or
# ``None``. ``None`` for any descriptor in the table costs the whole block -
# half-understood is not understood. A descriptor NOT in the table is left out
# and costs nothing, unless it holds a ``url()``.

# A number and an angle as this module will write one. ``[0-9]``, not ``\d``:
# ``\d`` also matches the digits of every other script.
_N = r"[0-9]{1,4}(?:\.[0-9]{1,3})?"
_ANGLE = r"-?[0-9]{1,2}(?:\.[0-9]{1,3})?"
_N_CHARS, _ANGLE_CHARS = len("0000.000"), len("-00.000")      # the longest each can be
_NUMBER_RE = re.compile(_N + r"\Z")
_ANGLE_RE = re.compile(_ANGLE + r"\Z")
_HEX = r"[0-9A-F]{4,6}"
_PLAIN_STYLES = ("normal", "italic")
_STYLES = _PLAIN_STYLES + ("oblique",)
_WEIGHTS = ("normal", "bold")
_STRETCHES = ("normal", "ultra-condensed", "extra-condensed", "condensed", "semi-condensed",
              "semi-expanded", "expanded", "extra-expanded", "ultra-expanded")
_DISPLAYS = ("auto", "block", "swap", "fallback", "optional")
# What ``format()`` may say for the file to be the one wanted. The second is
# the older spelling for a variable face; it is still a WOFF2 file.
_FORMATS = frozenset({"woff2", "woff2-variations"})
_LAST_CODE_POINT = 0x10FFFF


def _one_of(words) -> str:
    return "(?:" + "|".join(re.escape(word) for word in words) + ")"


def _longest(words) -> int:
    return max(len(word) for word in words)


def _keyword(tokens, allowed):
    """The one keyword ``tokens`` is, lower-cased, if it is one of ``allowed``."""
    if len(tokens) == 1 and tokens[0].type == "ident" and tokens[0].lower_value in allowed:
        return tokens[0].lower_value
    return None


def _numbers(tokens, kind, low, high, suffix=""):
    """One or two plain numbers of token type ``kind`` (``"number"`` or
    ``"percentage"``) inside ``low..high``, as written, or ``None``.

    "Plain" is a pattern on the token's own digits: no sign, no exponent, at
    most four digits and three decimals. So what is written back is text this
    function has read character by character, not a float it formatted."""
    if not 1 <= len(tokens) <= 2:
        return None
    written = []
    for token in tokens:
        if token.type != kind or not _NUMBER_RE.match(token.representation):
            return None
        if not low <= token.value <= high:
            return None
        written.append(token.representation + suffix)
    return " ".join(written)


def _read_family(tokens):
    """The family name as a quoted string that cannot be anything else.

    Read from one string, or from a run of bare words (``Inter Display``, which
    CSS joins with single spaces). Written with every character outside
    ``[A-Za-z0-9 _-]`` as a hex escape: the name a browser decodes is exactly
    the one read, and the text holds no quote, no backslash sequence but those,
    no ``<``, no bracket, no ``*`` and no ``/`` - nothing that could end the
    string, the rule or the ``<style>`` around it, or spell the font mark."""
    if len(tokens) == 1 and tokens[0].type == "string":
        name = tokens[0].value
    elif tokens and all(token.type == "ident" for token in tokens):
        name = " ".join(token.value for token in tokens)
    else:
        return None
    if not name.strip() or len(name) > _FAMILY_CHARS:
        return None
    if any(unicodedata.category(ch) in _NOT_IN_A_NAME for ch in name):
        return None
    return '"' + "".join(ch if ch.isascii() and (ch.isalnum() or ch in " _-")
                         else f"\\{ord(ch):x} " for ch in name) + '"'


def _read_style(tokens):
    """``normal``, ``italic``, or ``oblique`` with up to two angles in degrees
    (the slant range of a variable face), each within a right angle."""
    if not tokens or tokens[0].type != "ident" or tokens[0].lower_value not in _STYLES:
        return None
    word, angles = tokens[0].lower_value, tokens[1:]
    if angles and (word != "oblique" or len(angles) > 2):
        return None
    written = [word]
    for token in angles:
        if token.type != "dimension" or token.lower_unit != "deg":
            return None
        if not _ANGLE_RE.match(token.representation) or not -90 <= token.value <= 90:
            return None
        written.append(token.representation + "deg")
    return " ".join(written)


def _read_weight(tokens):
    return _keyword(tokens, _WEIGHTS) or _numbers(tokens, "number", 1, 1000)


def _read_stretch(tokens):
    """A keyword, or one or two percentages from 1% to 1000% - the range a
    weight's numbers have. CSS allows 0%; no typeface is nothing wide."""
    return _keyword(tokens, _STRETCHES) or _numbers(tokens, "percentage", 1, 1000, "%")


def _read_display(tokens):
    return _keyword(tokens, _DISPLAYS)


def _read_range(tokens):
    """A comma-separated list of ranges, each written again from its two ends.
    Nothing but ranges and the commas between them, every range forwards and
    inside Unicode, and at most ``MAX_RANGES`` of them."""
    if not tokens or len(tokens) % 2 == 0 or len(tokens) > 2 * MAX_RANGES - 1:
        return None
    written = []
    for at, token in enumerate(tokens):
        if at % 2:
            if token.type != "literal" or token.value != ",":
                return None
        elif token.type != "unicode-range" or not 0 <= token.start <= token.end <= _LAST_CODE_POINT:
            return None
        elif token.start == token.end:
            written.append(f"U+{token.start:04X}")
        else:
            written.append(f"U+{token.start:04X}-{token.end:04X}")
    return ",".join(written)


def _address(token):
    """The address a ``url(...)`` or ``url("...")`` names, or ``None``."""
    if token.type == "url":
        return token.value
    if token.type == "function" and token.lower_name == "url":
        inside = [arg for arg in token.arguments if arg.type not in ("whitespace", "comment")]
        if len(inside) == 1 and inside[0].type == "string":
            return inside[0].value
    return None


def _read_src(tokens):
    """The file's address when ``src`` is exactly one ``url()`` on the file
    host, ending ``.woff2``, followed by ``format()`` naming WOFF2 - and
    nothing else: no ``local()``, no second source, no other hint.

    The address is the DECODED one (the tokenizer has undone any escape), and
    it is used for the request only. It is never written anywhere."""
    if len(tokens) != 2:
        return None
    address, hint = _address(tokens[0]), tokens[1]
    if not isinstance(address, str) or len(address) > _FILE_CHARS:
        return None
    if not _FILE_RE.match(address) or not address.endswith(".woff2"):
        return None
    if hint.type != "function" or hint.lower_name != "format":
        return None
    said = [arg for arg in hint.arguments if arg.type not in ("whitespace", "comment")]
    if len(said) != 1 or said[0].type not in ("string", "ident"):
        return None
    return address if said[0].value.lower() in _FORMATS else None


@dataclass(frozen=True)
class _Descriptor:
    """One descriptor a rule may carry, and everything this module knows of it.

    Four things used to be kept in step by hand - the reader, the place the
    rule is written, the pattern the written rule is checked with, and the
    arithmetic for the longest rule - and "they cannot drift" was a promise.
    They are all derived from these rows now."""
    name: str
    read: object      # tokens -> the value as written here, or None
    shape: str        # a pattern for EVERYTHING ``read`` can return
    longest: int      # the most characters ``read`` can return


# The descriptors a rule may carry besides ``src``, in the order they are
# written. The first is required. ``src`` sits between the two rows and is in
# neither: what is READ for it is an address to fetch, and what is WRITTEN is
# this module's own (``_Face.rule``), so it has no value of the stylesheet's.
_FAMILY = _Descriptor("font-family", _read_family,
                      r'"(?:[A-Za-z0-9 _-]|\\[0-9a-f]{1,6} )+"',
                      len('""') + _FAMILY_CHARS * len("\\10ffff "))
_BEFORE_SRC = (
    _FAMILY,
    _Descriptor("font-style", _read_style,
                rf"(?:{_one_of(_PLAIN_STYLES)}|oblique(?: {_ANGLE}deg){{0,2}})",
                max(_longest(_PLAIN_STYLES), len("oblique") + 2 * (len(" deg") + _ANGLE_CHARS))),
    _Descriptor("font-weight", _read_weight, rf"(?:{_one_of(_WEIGHTS)}|{_N}(?: {_N})?)",
                max(_longest(_WEIGHTS), 2 * _N_CHARS + len(" "))),
    _Descriptor("font-stretch", _read_stretch, rf"(?:{_one_of(_STRETCHES)}|{_N}%(?: {_N}%)?)",
                max(_longest(_STRETCHES), 2 * (_N_CHARS + len("%")) + len(" "))),
    _Descriptor("font-display", _read_display, _one_of(_DISPLAYS), _longest(_DISPLAYS)),
)
_AFTER_SRC = (
    _Descriptor("unicode-range", _read_range, rf"U\+{_HEX}(?:-{_HEX})?(?:,U\+{_HEX}(?:-{_HEX})?)*",
                MAX_RANGES * len("U+10FFFF-10FFFF") + (MAX_RANGES - 1)),
)
_READERS = {row.name: row.read for row in _BEFORE_SRC + _AFTER_SRC} | {"src": _read_src}


def _urls(tokens) -> int:
    """How many ``url()``s are anywhere in ``tokens``: bare, quoted, broken, or
    inside any bracket or function. By stack, not recursion - the nesting is
    the stylesheet's choice."""
    count, todo = 0, list(tokens)
    while todo:
        token = todo.pop()
        kind = token.type
        if kind == "url" or (kind == "error" and token.kind == "bad-url"):
            count += 1
        elif kind == "function":
            if token.lower_name == "url":
                count += 1
            else:
                todo.extend(token.arguments)
        elif kind in ("() block", "[] block", "{} block"):
            todo.extend(token.content)
    return count


@dataclass(frozen=True)
class _Face:
    """One block, understood: where its file is, and the rule's text on either
    side of the ``src`` this module will write."""
    url: str
    head: str         # font-family:"…";font-style:…  (the descriptors before src)
    tail: str         # "" or ;unicode-range:…

    def rule(self, name) -> str:
        return f'@font-face{{{self.head};src:url(../fonts/{name}) format("woff2"){self.tail}}}'


def _block(rule) -> tuple:
    """``(family, face)`` for ``rule``, an ``@font-face`` node.

    ``family`` is the family the block names, as it would be written, or
    ``None`` when it names none that reads. It is wanted even for a block that
    will not be copied: it is how a typeface that is offered in none of the
    copied character sets gets counted.

    ``face`` is the block as a ``_Face``, or ``None`` unless: nothing stands
    between ``@font-face`` and its block; the block holds declarations only (no
    nested rule, nothing unparseable, no ``!important``); no listed descriptor
    is given twice; every listed one reads; there is a family and a ``src``;
    and the WHOLE block holds exactly one ``url()`` - so an address in a
    descriptor this module does not copy still costs the block."""
    if rule.content is None or any(token.type != "whitespace" for token in rule.prelude):
        return None, None
    read, urls, sound = {}, 0, True
    for item in tinycss2.parse_blocks_contents(rule.content, skip_comments=True,
                                               skip_whitespace=True):
        if item.type != "declaration" or item.important:
            sound = False
            continue
        urls += _urls(item.value)
        reader = _READERS.get(item.lower_name)
        if reader is None:
            continue
        value = reader([t for t in item.value if t.type not in ("whitespace", "comment")])
        if value is None or item.lower_name in read:
            sound = False               # read on: the family may still be wanted
            continue
        read[item.lower_name] = value
    family = read.get(_FAMILY.name)
    if not sound or urls != 1 or family is None or "src" not in read:
        return family, None
    head = ";".join(f"{row.name}:{read[row.name]}" for row in _BEFORE_SRC if row.name in read)
    tail = "".join(f";{row.name}:{read[row.name]}" for row in _AFTER_SRC if row.name in read)
    return family, _Face(read["src"], head, tail)


def _faces(text, subsets) -> tuple:
    """``(faces, unusable, named, offered)`` from one stylesheet.

    ``faces`` are the blocks labelled with one of ``subsets`` that read, in
    order; ``unusable`` is how many so labelled did not. ``named`` is every
    family any block names, and ``offered`` those with a block labelled with
    one of ``subsets`` - usable or not, because a block that was offered here
    and could not be used has a reason of its own.

    A block's label is the comment DIRECTLY before it, used once. Any other
    rule in between, a second comment, or no comment at all leaves the block
    unlabelled, and an unlabelled block is not copied: the subset list is the
    operator's, and "probably latin" is not on it. Only the top level is read;
    a block inside ``@media`` or ``@supports`` is not one Google sends."""
    faces, unusable, named, offered, label = [], 0, set(), set(), None
    for node in tinycss2.parse_stylesheet(text, skip_comments=False, skip_whitespace=True):
        if node.type == "comment":
            label = node.value.strip()
            continue
        mine, label = label, None
        if node.type != "at-rule" or node.lower_at_keyword != "font-face":
            continue
        family, face = _block(node)
        if family is not None:
            named.add(family)
        if mine not in subsets:
            continue
        if family is not None:
            offered.add(family)
        if face is None:
            unusable += 1
        else:
            faces.append(face)
    return faces, unusable, named, offered


# ── what leaves this module ──────────────────────────────────────────────────

def _optional(rows) -> str:
    return "".join(f"(?:;{row.name}:{row.shape})?" for row in rows)


# One finished rule, character for character: the pattern ``_Face.rule``'s
# output is held to before it leaves ``localize`` and again in ``apply``. Built
# from the descriptor table, so a reader that starts writing something its own
# row does not describe fails here, loudly, on the first draft.
_RULE_RE = re.compile(
    rf"@font-face\{{{_FAMILY.name}:{_FAMILY.shape}" + _optional(_BEFORE_SRC[1:])
    + rf';src:url\(\.\./fonts/[0-9a-f]{{{_NAME_HEX}}}\.woff2\) format\("woff2"\)'
    + _optional(_AFTER_SRC) + r"\}")

# The longest ONE rule can be, in characters (all ASCII, so bytes too): every
# descriptor present and each as long as its row says its reader can write.
# With ``[fonts] max_rules`` it is what bounds ``Fonts.css``, and so what
# ``apply`` can add to an entry whose own size limit was applied before any of
# this:
#
#     len(Fonts.css) <= max_rules * RULE_CHARS_CEILING + (max_rules - 1)
#
# 3,075 characters; 96 rules as shipped is under 300 KB at the very worst. A
# real rule is about a tenth of it (a family name is not a hundred characters
# that each need eight to write). The suite works the figure out a second
# time, from the shape of a rule typed out, and builds rules that reach it.
RULE_CHARS_CEILING = (
    len("@font-face{}") + len(f"{_FAMILY.name}:") + _FAMILY.longest
    + sum(len(f";{row.name}:") + row.longest for row in _BEFORE_SRC[1:] + _AFTER_SRC)
    + len(';src:url(../fonts/.woff2) format("woff2")') + _NAME_HEX)


def _written_here(css) -> bool:
    """Whether ``css`` is text this module could have written: nothing, or
    rules of exactly the shape above, one to a line."""
    if not isinstance(css, str):
        return False
    return css == "" or all(_RULE_RE.fullmatch(line) for line in css.split("\n"))


def _is_woff2(data) -> bool:
    """Whether ``data`` is a whole WOFF2 file as far as its header says: the
    signature, all 48 header bytes, at least one table, and a total length
    equal to the number of bytes there are.

    The length is the check that matters. It catches a download cut short and
    a file with something appended, and it is the first thing a browser's own
    decoder tests - so a file refused here would not have drawn anyway."""
    if len(data) < _WOFF2_HEADER or data[:4] != _WOFF2_SIGNATURE:
        return False
    length, tables = struct.unpack_from(">IH", data, 8)
    return length == len(data) and tables > 0


# ── saying what went wrong ───────────────────────────────────────────────────
#
# A failure here is one of three kinds, and they must not be mistaken for each
# other, because only one of them is this service's to fix:
#
# * the NETWORK's - Google was unreachable, slow, or answered something other
#   than 200. Expected on a bad day. Logged, never counted for /health.
# * the OTHER END's bytes - a stylesheet that is not text, a file that is not a
#   typeface. Logged, never counted.
# * OURS - counted, under one of the four labels below, so that a fault in this
#   file does not pass for Google being away.
#
# And one rule for all three: the LOG and the NOTE carry what kind of thing
# failed, never what it said. An exception's text is whatever the other end, or
# a library quoting the other end, put in it - an address with its query, a
# piece of a response body. The log gets the exception's type and, for a fault,
# the frames it came through (``_trace.where``). The one text let through is a
# plain ``FetchError``'s, which is written in this file from numbers.

# Where a fault of ours surfaced, as ``/health`` shows it (one counter each):
# what did the fetching; the stylesheet reader; ``apply``; anything else in
# ``localize``.
AREA_FETCH, AREA_READER = "blog.fonts.fetch", "blog.fonts.reader"
AREA_APPLY, AREA_GUARD = "blog.fonts.apply", "blog.fonts.guard"

# What a network raises. ``requests``' own exceptions are ``OSError``s, and so
# are the standard library's timeouts, resets and TLS failures; urllib3's are
# listed because one can escape ``requests`` while a body is being streamed.
_NETWORK = (FetchError, OSError, requests.RequestException, urllib3.exceptions.HTTPError)
# ... and the part of that which is NOT the network's, because the type tree
# does not draw the line where it is needed. ``requests`` and urllib3 raise
# their complaints about the ARGUMENTS they were given as ``RequestException``s
# too - ``InvalidHeader``, ``InvalidURL``, ``MissingSchema``,
# ``LocationParseError`` are each also a ``ValueError``, ``StreamConsumedError``
# a ``TypeError`` - and those arguments are this file's. So is whatever
# ``http_fetch`` refused before connecting. No outage is a ValueError or a
# TypeError (``requests`` wraps a certificate failure, the one standard-library
# network error that is, in its own ``SSLError``), so these three mean OURS.
#
# ⚠ Knowingly still on the network's side: an ``OSError`` that is this BOX's
# and not the network's - a CA bundle that is not there, a certificate file
# that cannot be read. It is an ``OSError`` like a refused connection is, and
# nothing on the exception tells the two apart, so it is logged and not
# counted. It would show as every draft losing its typefaces at once.
_OURS_AFTER_ALL = (ValueError, TypeError, FetchRefused)


class Reason(enum.Enum):
    """Why something an entry asked for is not in the result - the ONE list.

    Every count of something left out is kept under one of these, and the note
    is built by walking them in this order. So a count can no longer be kept
    under a name the note has never heard of (it was a string once, and a
    mistyped one was silently never reported), and a reason that nothing ever
    counts is a member nothing names - the suite fails on both.

    The value is the wording: ``(for one, for several)`` after the count, or a
    single phrase for a reason that is said once however often it happened. A
    ``{name}`` is filled from the settings."""
    LINK = ("link was not a Google Fonts stylesheet", "links were not Google Fonts stylesheets")
    LINK_OVER = ("stylesheet was past the limit of {max_links}",
                 "stylesheets were past the limit of {max_links}")
    SHEET = ("stylesheet could not be fetched", "stylesheets could not be fetched")
    GARBLED = ("stylesheet was not readable text", "stylesheets were not readable text")
    READER = ("stylesheet made the reader fail", "stylesheets made the reader fail")
    EMPTY = ("stylesheet held no typefaces", "stylesheets held no typefaces")
    FAMILY = ("typeface was not copied because it is not offered in the character sets "
              "copied here",
              "typefaces were not copied because they are not offered in the character sets "
              "copied here")
    RULE = ("typeface rule was not usable", "typeface rules were not usable")
    RULE_OVER = ("typeface rule was past the limit of {max_rules}",
                 "typeface rules were past the limit of {max_rules}")
    FILE = ("file could not be fetched or was not a typeface",
            "files could not be fetched or were not typefaces")
    FILE_OVER = ("file was past the limit of {max_files}",
                 "files were past the limit of {max_files}")
    LATE = ("the time allowed for copying ran out",)
    FAULT = ("the Blog service hit an error",)


def _note(missed, copied, settings) -> str:
    """One sentence for the operator's screen, or ``""`` when nothing was lost.

    ``missed`` is a ``Counter`` of ``Reason``s; anything else in it is a
    ``TypeError``, not a count quietly dropped. Every word is this module's and
    every number is a count or a setting. Nothing here is ever taken from an
    exception, an address or a stylesheet - a typeface that was not copied is
    counted, never named."""
    if any(not isinstance(key, Reason) for key in missed):
        raise TypeError("a count kept under something that is not a Reason")
    said = []
    for reason in Reason:
        count = missed[reason]
        if count <= 0:
            continue
        if len(reason.value) == 1:
            said.append(reason.value[0])
        else:
            one, many = reason.value
            said.append(f"{count} {one if count == 1 else many}".format(**settings))
    if not said:
        return ""
    lead = ("Some typefaces were not copied and are shown in a fallback font" if copied
            else "No typefaces were copied, so the entry is shown in its fallback fonts")
    return f"{lead}: {', '.join(said)}."


# ── one entry's copy ─────────────────────────────────────────────────────────

class _NoFile(enum.Enum):
    """What ``_Copy._name_for`` answers when there is no file to name."""
    FAILED = "asked for, and not usable"
    OVER = "not asked for: the entry is at its limit of files"
    LATE = "not asked for: the copy is out of time"


class _Copy:
    """One entry's copy: its settings, its clock, what it has stored and what
    it has had to leave out."""

    def __init__(self, fetch, settings, clock):
        self.fetch = fetch
        self.settings = settings
        self.subsets = frozenset(settings["subsets"])
        self.clock = clock
        self.deadline = clock() + settings["total_sec"]
        self.late = False             # once True, no further request is made
        self.missed = Counter()       # Reason -> how many
        self.files = {}
        self.named = set()            # every family any stylesheet named
        self.offered = set()          # ... those with a block in a copied character set
        self._asked = {}              # address -> its name, or _NoFile.FAILED: asked once
        self._over = set()            # addresses a limit turned away, each counted once

    def run(self, font_links) -> Fonts:
        faces = []
        for link in self._links(font_links):
            faces += self._sheet(link)
        # A family with NO block in a copied character set: an icon font is the
        # common one (its blocks are labelled "fallback"). Counted across all
        # the stylesheets, by name, so a family two of them ask for is one. A
        # family that keeps its latin blocks and loses its cyrillic ones is the
        # ordinary case and is not here.
        self._miss(Reason.FAMILY, len(self.named - self.offered))
        css = "\n".join(self._rules(faces))
        if not _written_here(css):
            # A reader wrote something its own row does not describe. That is a
            # bug in this file, and what it wrote is not going into a document.
            raise RuntimeError("typeface rules that do not match their own pattern")
        if self.late:
            self._miss(Reason.LATE)
        return Fonts(css, self.files, _note(self.missed, self.files, self.settings))

    def _miss(self, reason, count=1) -> None:
        """Count ``count`` things left out for ``reason``, which must be a
        ``Reason``: a count under anything else would never reach the note."""
        if not isinstance(reason, Reason):
            raise TypeError("a count kept under something that is not a Reason")
        self.missed[reason] += count

    def _left(self):
        """The seconds this copy may still spend, or ``None`` once there are too
        few to start a request with (``_USEFUL_SEC``) - from when it is late."""
        left = self.deadline - self.clock()
        if left < _USEFUL_SEC:
            self.late = True
            return None
        return left

    def _links(self, font_links) -> list:
        """The links to follow: each a Google Fonts stylesheet, each once, in
        the order given, at most ``max_links`` of them.

        The cleaner already matched each one. It is matched again, with the
        cleaner's own pattern, because what this is handed becomes a request
        and "the caller already checked" is not something a request should
        rest on."""
        if font_links is None:
            font_links = ()
        elif not isinstance(font_links, (list, tuple)):
            font_links = (font_links,)          # one link, or one thing that is not a link
        kept, seen = [], set()
        for link in font_links:
            if not (isinstance(link, str) and len(link) <= _LINK_CHARS
                    and clean.FONT_LINK_RE.match(link)):
                self._miss(Reason.LINK)
            elif link not in seen:
                seen.add(link)
                if len(kept) < self.settings["max_links"]:
                    kept.append(link)
                else:
                    self._miss(Reason.LINK_OVER)
        return kept

    def _get(self, url, max_bytes, what, left):
        """The bytes at ``url``, or ``None`` when they could not be had.

        What ``fetch`` raises is sorted, not just caught (see "saying what went
        wrong"): a network's failure is logged; anything else - and that
        includes a ``MemoryError`` - is a fault in whatever did the fetching
        and is counted. Either way the answer is ``None``: to the operator the
        thing was not fetched.

        What ``fetch`` RETURNS is held to what it was asked for. One that hands
        back something that is not bytes, or more than ``max_bytes``, broke its
        word exactly as one that raised a ``TypeError`` did, and is counted the
        same way.

        ``User-Agent`` is ``[fonts] user_agent``. Google chooses what to send
        by who is asking: a client it does not recognise is sent TrueType with
        no ``unicode-range`` - one large file per weight - and every block
        would fail the reader's checks; a desktop Chrome is sent woff2. The
        string ages, which is why it is a setting and not a constant here."""
        try:
            data = self.fetch(url, headers={"User-Agent": self.settings["user_agent"]},
                              timeout=min(self.settings["timeout_sec"], left),
                              max_bytes=max_bytes)
        except Exception as exc:
            if not isinstance(exc, _NETWORK) or isinstance(exc, _OURS_AFTER_ALL):
                _degrade.degraded(AREA_FETCH, detail=_trace.where(f"fetching a {what}", exc),
                                  exc_info=False)
            else:
                # The type always; the text only when it is this module's own.
                said = exc if type(exc) is FetchError else type(exc).__name__
                log.warning("typeface %s not fetched: %s", what, said)
            return None
        if not isinstance(data, (bytes, bytearray)) or len(data) > max_bytes:
            _degrade.degraded(AREA_FETCH, exc_info=False,
                              detail=f"the fetch of a {what} handed back something that is "
                                     "not bytes, or more than it was allowed")
            return None
        return bytes(data)

    def _sheet(self, link) -> list:
        """The usable faces of one stylesheet, and ``[]`` when there are none -
        each way of there being none counted under its own ``Reason``."""
        left = self._left()
        if left is None:
            return []
        data = self._get(link, self.settings["max_css_kb"] * 1024, "stylesheet", left)
        if data is None:
            self._miss(Reason.SHEET)                # not fetched
            return []
        try:
            # "utf-8-sig": a byte-order mark left in is a character CSS reads
            # as the start of a name, and the first block would be swallowed.
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            log.warning("typeface stylesheet not usable: not UTF-8 text")
            self._miss(Reason.GARBLED)              # fetched, and not text
            return []
        try:
            faces, unusable, named, offered = _faces(text, self.subsets)
        except Exception as exc:
            # The READER failed: a fault here, not a failure out there. Measured:
            # a number 4,301 digits long makes tinycss2 raise ValueError (Python
            # will not turn that many digits into an int), and the tokenizer
            # reads the whole stylesheet before any block can be looked at.
            _degrade.degraded(AREA_READER, detail=_trace.where("reading a stylesheet", exc),
                              exc_info=False)
            self._miss(Reason.READER)
            return []
        self._miss(Reason.RULE, unusable)
        self.named |= named
        self.offered |= offered
        if not named and not unusable:
            self._miss(Reason.EMPTY)                # read, and no typeface in it
        return faces

    def _name_for(self, url):
        """The stored name of the file at ``url``, or a ``_NoFile`` saying why
        there is none. Does its own counting.

        Each address is asked for ONCE, however many blocks name it (a variable
        face asked for by weight comes back as one block per weight, all naming
        one file) and whether or not it worked: the answer is remembered.
        ``max_files`` counts those requests, not the files that came of them -
        it bounds the work done for one entry, and a stylesheet whose first
        files all fail does not get the limit again in tries."""
        if url in self._asked:
            return self._asked[url]
        if self.late:
            return _NoFile.LATE
        if len(self._asked) >= self.settings["max_files"]:
            if url not in self._over:
                self._over.add(url)
                self._miss(Reason.FILE_OVER)
            return _NoFile.OVER
        left = self._left()
        if left is None:
            return _NoFile.LATE
        data = self._get(url, self.settings["max_file_kb"] * 1024, "file", left)
        if data is None or not _is_woff2(data):
            self._miss(Reason.FILE)
            self._asked[url] = _NoFile.FAILED
            return _NoFile.FAILED
        # Named by content: the same file under two addresses is one file on
        # disk, and no part of the name is anything the stylesheet chose.
        name = hashlib.sha256(data).hexdigest()[:_NAME_HEX] + ".woff2"
        if not blog_inbox.FONT_NAME_RE.match(name):
            raise RuntimeError("a typeface name the store would refuse")
        self.files[name] = data
        self._asked[url] = name
        return name

    def _rules(self, faces) -> list:
        """The rules to write, in the order of ``faces``, each once, at most
        ``max_rules`` of them.

        ``max_files`` cannot stand in for that limit: 5,088 blocks naming one
        file were 5,088 rules. Once the rules are full nothing more is asked
        for, and a block is counted as past the limit unless it is a rule
        already written (two stylesheets asking for one face) or one whose
        file had already failed."""
        rules = {}                                  # a dict as an ordered set
        for face in faces:
            if len(rules) >= self.settings["max_rules"]:
                asked = self._asked.get(face.url)
                if asked is None or (isinstance(asked, str) and face.rule(asked) not in rules):
                    self._miss(Reason.RULE_OVER)
                continue
            name = self._name_for(face.url)
            if isinstance(name, str):
                rules[face.rule(name)] = None
        return list(rules)


def localize(font_links, *, fetch=http_fetch, cfg=None, clock=None) -> Fonts:
    """The typefaces ``font_links`` ask for, copied.

    **It raises nothing that is an ``Exception``.** A link or a file that fails
    costs only itself. A fault in here costs the whole copy - the entry keeps
    its fallback fonts and the note says the service hit an error - and is
    counted for ``/health``, because a crash should not look like a slow
    network.

    That includes ``MemoryError``. It used to be let out; the command runner
    catches it anyway and carries on, so letting it out bought only a draft
    that became a dead-lettered command with no answer. Here it is most likely
    this copy's own - the tokens of one large stylesheet - and is given back as
    the stack unwinds. It is counted like any other fault, and the draft gets
    its fallback fonts and a note.

    What DOES get out, on purpose: ``KeyboardInterrupt``, ``SystemExit`` and
    every other ``BaseException`` that is not an ``Exception``. A shutdown must
    not be turned into "one stylesheet could not be fetched" and the service
    carry on.

    ``font_links`` is ``Cleaned.font_links``. ``fetch`` is called as
    ``fetch(url, headers=..., timeout=..., max_bytes=...)`` and returns bytes;
    it is a parameter because the suite cannot reach the network. ``cfg`` is a
    table laid over the file's ``[fonts]`` settings and read by the same rules
    (``blog_inbox.fonts(cfg)``); ``clock`` is ``time.monotonic``.

    The result is deterministic: the same links and the same bytes give the
    same rules, byte for byte, in the links' order and then each stylesheet's
    own. ``Fonts.files`` is what the store writes under ``blog/fonts/``;
    ``Fonts.css`` goes to ``apply``; ``Fonts.note`` goes on the draft."""
    try:
        settings = blog_inbox.fonts(cfg)
        if not settings["enabled"]:
            return Fonts("", {}, "")
        return _Copy(fetch, settings, clock or monotonic).run(font_links)
    except Exception as exc:
        _degrade.degraded(AREA_GUARD, detail=_trace.where("copying typefaces", exc),
                          exc_info=False)
        return Fonts("", {}, _note(Counter({Reason.FAULT: 1}), {}, {}))


def apply(html, fonts: Fonts) -> str:
    """``html`` with its font slot filled with ``fonts.css``.

    Where the slot is, is the cleaner's to say: ``clean.font_slot`` finds it,
    in a document that opens with the cleaner's own head, straight after the
    title, with the mark nowhere else. There is no second opinion about that
    in this file. A document with no such slot comes back UNCHANGED - this
    does not guess where rules might go.

    ``fonts.css`` is checked again here (``_written_here``). Rules that are not
    of this module's own shape go in as nothing: nothing cleans the document
    after this, so this is the last place to refuse.

    **Never silently.** If there were rules to put in and the document that
    comes back does not hold them - no slot, or rules that failed that check -
    it is counted for ``/health``. Unsaid, a document left unfilled looks
    exactly like an entry that asked for no typefaces.

    ⚠ Do not ``clean`` the result. The cleaner drops every ``@font-face`` it
    meets, these included."""
    css = fonts.css if isinstance(fonts, Fonts) else None
    slot = clean.font_slot(html)
    if not _written_here(css):
        _degrade.degraded(AREA_APPLY, detail="typeface rules that are not this module's own",
                          exc_info=False)
        css = ""
    elif css and slot is None:
        _degrade.degraded(AREA_APPLY, detail="typeface rules, and no slot to put them in",
                          exc_info=False)
    if slot is None:
        return html
    start, end = slot
    return html[:start] + css + html[end:]
