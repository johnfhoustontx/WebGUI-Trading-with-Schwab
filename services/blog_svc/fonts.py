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
* ``apply`` puts the rules where the mark is.

**What is written is built, never passed through.** Nothing cleans a document
after ``apply`` (``clean`` would throw the rules away: it drops every
``@font-face``), so the CSS this module returns is trusted by everything after
it. Google's stylesheet is therefore only READ - parsed by tinycss2, the same
tokenizer the cleaner uses, so an escaped ``u\\72l(`` is a url here too - and
each rule is written again from an allow-list of seven descriptors, each with a
value this module understood and spelled itself. The one piece of free text, a
family name, is written with every character outside ``[A-Za-z0-9 _-]`` as a
hex escape. The one address is ``../fonts/<20 hex>.woff2``, where the name is a
hash of bytes this module fetched and checked. Then the finished text is held
to a pattern of exactly that shape (``_RULE_RE``) before it leaves, and again
in ``apply``, because a ``Fonts`` is a plain record anything can build.

**What is fetched is Google's, and bounded.** A link is followed only if it is
``https://fonts.googleapis.com/css2?...``; a file only if it is
``https://fonts.gstatic.com/....woff2``. ``http_fetch`` checks the host again
on the text before it connects, follows no redirect, and counts the body as it
arrives. How many links, how large a stylesheet, how many files, how large a
file, how long one request and how long all of them: ``config/blog.toml
[fonts]``. None of it is capped by the cleaner.

**It never blocks a draft.** ``localize`` never raises. A link or a file that
fails costs only itself; the entry falls back to the fonts its own stylesheet
names, and ``Fonts.note`` says so in one sentence made of this module's own
words and counts - never an exception's text, an address or a family name,
all of which belong to someone else.

⚠ **Never fetched for real while this was written.** The suite cannot reach the
network and the build did not either, so the parser was written against the
SHAPE the css2 endpoint is known to send a desktop Chrome, not against an
answer. On the first live run, look at the draft's note and check:

1. The answer still labels each block with a ``/* subset */`` comment directly
   before ``@font-face``. An unlabelled block is dropped ("offered nothing in
   the character sets copied here").
2. ``src`` is still exactly ``url(https://fonts.gstatic.com/....woff2)
   format('woff2')``. Anything else in it - a ``local()``, a second source,
   another format hint - drops the block ("typeface rules were not usable").
3. The file addresses still fit ``_FILE_RE`` (letters, digits, ``. _ ~ -``).
4. A fetched file passes ``_is_woff2``: its header's length field equals its
   size. A browser's decoder refuses a file where it does not, so this should
   hold - but it is a belief about Google's files, not a measurement.
5. Google answers with a plain 200 and no redirect.
6. ``USER_AGENT`` is still one Google answers with woff2.

Design: docs/plans/2026-10-06-site-blog-design.md ("Typefaces", "How an entry
is isolated").
"""
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

from services import _degrade
from services.blog_svc import clean
from shared import blog_inbox

log = logging.getLogger("blog_svc.fonts")

# The only two hosts this module ever connects to: the one that serves the
# stylesheet and the one that serves the files. Never interchangeable - a link
# to the file host is not a stylesheet, and a file on the stylesheet host is
# not fetched.
CSS_HOST, FILE_HOST = "fonts.googleapis.com", "fonts.gstatic.com"
_HOSTS = frozenset({CSS_HOST, FILE_HOST})

# Sent on every request, and it is here for one reason: Google chooses what to
# send by who is asking. A client it does not recognise (``python-requests``)
# is sent TrueType with no ``unicode-range`` - one large file per weight
# instead of one small woff2 per character set - and every block would fail the
# checks below. A desktop Chrome is sent woff2. The version only has to be one
# Google still takes for a browser that reads woff2; this one is a late-2025
# release, picked without the network, and ⚠ it WILL AGE: if every draft starts
# saying its typeface rules were not usable, raise the number before looking
# anywhere else. A constant and not a config key because nothing but that
# would ever be a reason to change it.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

# A stylesheet link, exactly as the cleaner matches one (``clean._FONT_LINK_RE``):
# checked again here because what this module is handed becomes a request, and
# "the caller already checked" is not something a request should rest on.
_LINK_RE = re.compile(r"^https://fonts\.googleapis\.com/css2\?[A-Za-z0-9:;,@+&=._%-]+\Z")
# The longest link followed. The cleaner's pattern has no length in it, and the
# example-shaped entry's link - three families, axes and all - is 171
# characters. Not a config key: past this a link is not a longer link, it is
# something else.
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

# A WOFF2 file starts with a 48-byte header: the signature, a flavor, the
# file's own total length, the number of tables, and eight more fields.
_WOFF2_SIGNATURE = b"wOF2"
_WOFF2_HEADER = 48

_CHUNK = 16 * 1024


class FetchError(Exception):
    """A fetch ``http_fetch`` refused or gave up on. Its text is this module's
    own - a status, a size, a reason - and never the address or anything the
    other end sent, so it is safe to log."""


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
        raise FetchError("not an https address")
    if not url.isascii() or any(ch <= " " or ch in "\\\x7f" for ch in url):
        raise FetchError("an address with a character no address here has")
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:                      # "https://[not-an-ip]/"
        raise FetchError("an address that cannot be read") from None
    if parts.netloc not in _HOSTS or host != parts.netloc:
        raise FetchError("not one of the two hosts typefaces come from")


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
    * The whole request gets ``timeout`` seconds, checked between chunks:
      ``requests``' own timeout bounds each read, not the request.

    ⚠ That last check runs BETWEEN chunks. A body that arrives slower than one
    chunk per ``timeout`` is cut by ``requests``' read timeout instead; one
    that drips just inside it can hold a single read for longer than
    ``timeout``. Only one of Google's two hosts, over verified TLS, is ever in
    a position to do that, so it is accepted here and not engineered against
    (``news_svc/fetch.py`` has the socket watchdog that would close it).

    Raises ``FetchError`` for everything decided here, and lets ``requests``'
    own exceptions through. The connection is always given back."""
    _refuse_unless_google(url)
    if not isinstance(headers, dict):
        raise FetchError("no headers given")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise FetchError("no size limit given")
    timed = isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
    if not timed or not 0 < timeout < 3600:             # nan fails both comparisons
        raise FetchError("no time limit given")
    started = monotonic()
    response = requests.get(url, headers=dict(headers), timeout=timeout,
                            allow_redirects=False, stream=True)
    try:
        status = response.status_code
        if type(status) is not int:
            raise FetchError("no HTTP status")
        if status != 200:
            raise FetchError(f"HTTP {status}")
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
# ``None``. ``None`` for any descriptor on the allow-list costs the whole
# block - half-understood is not understood. A descriptor NOT on the list is
# left out and costs nothing, unless it holds a ``url()``.

# A number and an angle as this module will write one. ``[0-9]``, not ``\d``:
# ``\d`` also matches the digits of every other script. The same two texts
# build ``_RULE_RE`` below, so what a reader accepts is what the pattern allows.
_N = r"[0-9]{1,4}(?:\.[0-9]{1,3})?"
_ANGLE = r"-?[0-9]{1,2}(?:\.[0-9]{1,3})?"
_NUMBER_RE = re.compile(_N + r"\Z")
_ANGLE_RE = re.compile(_ANGLE + r"\Z")
_STYLES = ("normal", "italic", "oblique")
_WEIGHTS = ("normal", "bold")
_STRETCHES = ("normal", "ultra-condensed", "extra-condensed", "condensed", "semi-condensed",
              "semi-expanded", "expanded", "extra-expanded", "ultra-expanded")
_DISPLAYS = ("auto", "block", "swap", "fallback", "optional")
# What ``format()`` may say for the file to be the one wanted. The second is
# the older spelling for a variable face; it is still a WOFF2 file.
_FORMATS = frozenset({"woff2", "woff2-variations"})
_LAST_CODE_POINT = 0x10FFFF


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
    return _keyword(tokens, _STRETCHES) or _numbers(tokens, "percentage", 0, 1000, "%")


def _read_display(tokens):
    return _keyword(tokens, _DISPLAYS)


def _read_range(tokens):
    """A comma-separated list of ranges, each written again from its two ends.
    Nothing but ranges and the commas between them, every range forwards and
    inside Unicode."""
    if not tokens or len(tokens) % 2 == 0:
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


# The seven descriptors a rule may carry, in the order they are written.
_READERS = {
    "font-family": _read_family,
    "font-style": _read_style,
    "font-weight": _read_weight,
    "font-stretch": _read_stretch,
    "font-display": _read_display,
    "src": _read_src,
    "unicode-range": _read_range,
}
_BEFORE_SRC = ("font-family", "font-style", "font-weight", "font-stretch", "font-display")


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


def _face(rule):
    """``rule`` (an ``@font-face`` node) as a ``_Face``, or ``None``.

    ``None`` unless: nothing stands between ``@font-face`` and its block; the
    block holds declarations only (no nested rule, nothing unparseable, no
    ``!important``); no listed descriptor is given twice; every listed one
    reads; there is a family and a ``src``; and the WHOLE block holds exactly
    one ``url()`` - so an address in a descriptor this module does not copy
    still costs the block."""
    if rule.content is None or any(token.type != "whitespace" for token in rule.prelude):
        return None
    read, urls = {}, 0
    for item in tinycss2.parse_blocks_contents(rule.content, skip_comments=True,
                                               skip_whitespace=True):
        if item.type != "declaration" or item.important:
            return None
        urls += _urls(item.value)
        reader = _READERS.get(item.lower_name)
        if reader is None:
            continue
        if item.lower_name in read:
            return None
        value = reader([t for t in item.value if t.type not in ("whitespace", "comment")])
        if value is None:
            return None
        read[item.lower_name] = value
    if urls != 1 or "font-family" not in read or "src" not in read:
        return None
    head = ";".join(f"{name}:{read[name]}" for name in _BEFORE_SRC if name in read)
    tail = f";unicode-range:{read['unicode-range']}" if "unicode-range" in read else ""
    return _Face(read["src"], head, tail)


def _faces(text, subsets) -> tuple:
    """``(faces, unusable)`` from one stylesheet: the blocks labelled with one
    of ``subsets`` that read, in order, and how many so labelled did not.

    A block's label is the comment DIRECTLY before it, used once. Any other
    rule in between, a second comment, or no comment at all leaves the block
    unlabelled, and an unlabelled block is not copied: the subset list is the
    operator's, and "probably latin" is not on it. Only the top level is read;
    a block inside ``@media`` or ``@supports`` is not one Google sends."""
    faces, unusable, label = [], 0, None
    for node in tinycss2.parse_stylesheet(text, skip_comments=False, skip_whitespace=True):
        if node.type == "comment":
            label = node.value.strip()
            continue
        mine, label = label, None
        if node.type != "at-rule" or node.lower_at_keyword != "font-face" or mine not in subsets:
            continue
        face = _face(node)
        if face is None:
            unusable += 1
        else:
            faces.append(face)
    return faces, unusable


# ── what leaves this module ──────────────────────────────────────────────────

def _one_of(words) -> str:
    return "(?:" + "|".join(re.escape(word) for word in words) + ")"


# One finished rule, character for character. Built from the same word lists
# the readers accept, so the two cannot drift: a reader that starts writing
# something this does not describe fails here, loudly, on the first draft.
_HEX = r"[0-9A-F]{4,6}"
_RULE_RE = re.compile(
    r'@font-face\{font-family:"(?:[A-Za-z0-9 _-]|\\[0-9a-f]{1,6} )+"'
    rf"(?:;font-style:(?:normal|italic|oblique(?: {_ANGLE}deg){{0,2}}))?"
    rf"(?:;font-weight:(?:{_one_of(_WEIGHTS)}|{_N}(?: {_N})?))?"
    rf"(?:;font-stretch:(?:{_one_of(_STRETCHES)}|{_N}%(?: {_N}%)?))?"
    rf"(?:;font-display:{_one_of(_DISPLAYS)})?"
    r';src:url\(\.\./fonts/[0-9a-f]{20}\.woff2\) format\("woff2"\)'
    rf"(?:;unicode-range:U\+{_HEX}(?:-{_HEX})?(?:,U\+{_HEX}(?:-{_HEX})?)*)?"
    r"\}")


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


def _settings(cfg) -> dict:
    """The ``[fonts]`` settings a copy runs under.

    ``cfg`` is laid over the shipped settings, key by key, and held to the same
    bounds the config file is (``blog_inbox.BOUNDS``). A value that is not
    usable reads as the shipped one - a caller cannot switch a limit off by
    handing in a bad number. ``None`` is the shipped settings."""
    settings = blog_inbox.fonts()
    if not isinstance(cfg, dict):
        return settings
    for key in settings:
        raw = cfg.get(key)
        if key == "enabled":
            usable = isinstance(raw, bool)
        elif key == "subsets":
            # Only ever COMPARED with a stylesheet's labels, so any text will
            # do: a name Google does not use simply matches nothing.
            listed = raw if isinstance(raw, (list, tuple)) else ()
            raw = [name for name in listed if isinstance(name, str)]
            usable = bool(raw)
        else:
            low, high = blog_inbox.BOUNDS[("fonts", key)]
            usable = isinstance(raw, int) and not isinstance(raw, bool) and low <= raw <= high
        if usable:
            settings[key] = raw
    return settings


def _counted(count, one, many) -> str:
    return f"{count} {one if count == 1 else many}"


def _note(missed, copied, settings) -> str:
    """One sentence for the operator's screen, or ``""`` when nothing was lost.

    Every word is this module's and every number is a count or a setting.
    Nothing here is ever taken from an exception, an address or a stylesheet."""
    reasons = []
    for key, one, many in (
            ("link", "link was not a Google Fonts stylesheet",
             "links were not Google Fonts stylesheets"),
            ("link_over", "stylesheet was past the limit of {max_links}",
             "stylesheets were past the limit of {max_links}"),
            ("sheet", "stylesheet could not be fetched", "stylesheets could not be fetched"),
            ("empty", "stylesheet offered nothing in the character sets copied here",
             "stylesheets offered nothing in the character sets copied here"),
            ("rule", "typeface rule was not usable", "typeface rules were not usable"),
            ("file", "file could not be fetched or was not a typeface",
             "files could not be fetched or were not typefaces"),
            ("file_over", "file was past the limit of {max_files}",
             "files were past the limit of {max_files}")):
        if missed[key]:
            reasons.append(_counted(missed[key], one, many).format(**settings))
    if missed["time"]:
        reasons.append("the time allowed for copying ran out")
    if missed["error"]:
        reasons.append("the Blog service hit an error")
    if not reasons:
        return ""
    lead = ("Some typefaces were not copied and are shown in a fallback font" if copied
            else "No typefaces were copied, so the entry is shown in its fallback fonts")
    return f"{lead}: {', '.join(reasons)}."


# Handed back by ``_Copy._get`` when the time for the whole copy has run out:
# not a failure of that request, which was never made.
_LATE = object()


class _Copy:
    """One entry's copy: its settings, its clock, what it has stored and what
    it has had to leave out."""

    def __init__(self, fetch, settings, clock):
        self.fetch = fetch
        self.settings = settings
        self.subsets = frozenset(settings["subsets"])
        self.clock = clock
        self.deadline = clock() + settings["total_sec"]
        self.missed = Counter()
        self.files = {}

    def run(self, font_links) -> Fonts:
        faces = []
        for link in self._links(font_links):
            faces += self._sheet(link)
        css = "\n".join(self._rules(faces))
        if not _written_here(css):
            # A reader wrote something the pattern does not describe. That is a
            # bug in this file, and what it wrote is not going into a document.
            raise RuntimeError("typeface rules that do not match their own pattern")
        return Fonts(css, self.files, _note(self.missed, self.files, self.settings))

    def _links(self, font_links) -> list:
        """The links to follow: each a Google Fonts stylesheet, each once, in
        the order given, at most ``max_links`` of them."""
        if font_links is None:
            font_links = ()
        elif not isinstance(font_links, (list, tuple)):
            font_links = (font_links,)          # one link, or one thing that is not a link
        kept, seen = [], set()
        for link in font_links:
            if not (isinstance(link, str) and len(link) <= _LINK_CHARS and _LINK_RE.match(link)):
                self.missed["link"] += 1
            elif link not in seen:
                seen.add(link)
                if len(kept) < self.settings["max_links"]:
                    kept.append(link)
                else:
                    self.missed["link_over"] += 1
        return kept

    def _get(self, url, max_bytes, what):
        """The bytes at ``url``, ``None`` when they could not be had, ``_LATE``
        when the copy's time ran out before the request was made.

        Whatever ``fetch`` raises is caught: it is injected, and "the network
        failed" has more spellings than any list of exception types. Whatever
        it returns is measured: a ``fetch`` that ignored ``max_bytes`` does not
        get to hand over more than that."""
        left = self.deadline - self.clock()
        if left <= 0:
            self.missed["time"] = 1
            return _LATE
        try:
            data = self.fetch(url, headers={"User-Agent": USER_AGENT},
                              timeout=min(self.settings["timeout_sec"], left),
                              max_bytes=max_bytes)
        except Exception as exc:
            # The type always; the text only when it is this module's own.
            said = exc if isinstance(exc, FetchError) else type(exc).__name__
            log.warning("typeface %s not fetched: %s", what, said)
            return None
        if not isinstance(data, (bytes, bytearray)) or len(data) > max_bytes:
            log.warning("typeface %s not usable: not bytes, or over its size limit", what)
            return None
        return bytes(data)

    def _sheet(self, link) -> list:
        """The usable faces of one stylesheet; ``[]`` when it failed."""
        data = self._get(link, self.settings["max_css_kb"] * 1024, "stylesheet")
        if data is _LATE:
            return []
        if data is None:
            self.missed["sheet"] += 1
            return []
        try:
            # "utf-8-sig": a byte-order mark left in is a character CSS reads
            # as the start of a name, and the first block would be swallowed.
            faces, unusable = _faces(data.decode("utf-8-sig"), self.subsets)
        except Exception as exc:        # not UTF-8, or nested past what the parser takes
            log.warning("typeface stylesheet not readable: %s", type(exc).__name__)
            self.missed["sheet"] += 1
            return []
        self.missed["rule"] += unusable
        if not faces and not unusable:
            self.missed["empty"] += 1
        return faces

    def _file(self, url):
        """Fetch, check and keep one file; its name, ``None`` when it is not
        usable, ``_LATE`` when it was never asked for."""
        data = self._get(url, self.settings["max_file_kb"] * 1024, "file")
        if data is _LATE:
            return _LATE
        if data is None or not _is_woff2(data):
            self.missed["file"] += 1
            return None
        # Named by content: the same file under two addresses is one file on
        # disk, and no part of the name is anything the stylesheet chose.
        name = hashlib.sha256(data).hexdigest()[:20] + ".woff2"
        if not blog_inbox.FONT_NAME_RE.match(name):
            raise RuntimeError("a typeface name the store would refuse")
        self.files[name] = data
        return name

    def _rules(self, faces) -> list:
        """The rules to write, in the order of ``faces``, each once.

        Each address is asked for ONCE, however many blocks name it (a variable
        face asked for by weight comes back as one block per weight, all naming
        one file) and whether or not it worked. ``max_files`` counts those
        requests, not the files that came of them: it bounds the work done for
        one entry, and a stylesheet whose first files all fail does not get the
        limit again in tries."""
        names, over, rules = {}, set(), []
        for face in faces:
            if face.url not in names:
                if self.missed["time"]:
                    continue
                if len(names) >= self.settings["max_files"]:
                    over.add(face.url)
                    continue
                name = self._file(face.url)
                if name is _LATE:
                    continue
                names[face.url] = name
            if names[face.url]:
                rules.append(face.rule(names[face.url]))
        self.missed["file_over"] = len(over)
        return list(dict.fromkeys(rules))


def localize(font_links, *, fetch=http_fetch, cfg=None, clock=None) -> Fonts:
    """The typefaces ``font_links`` ask for, copied. NEVER raises.

    ``font_links`` is ``Cleaned.font_links``. ``fetch`` is called as
    ``fetch(url, headers=..., timeout=..., max_bytes=...)`` and returns bytes;
    it is a parameter because the suite cannot reach the network. ``cfg`` is
    laid over ``blog_inbox.fonts()`` (see ``_settings``); ``clock`` is
    ``time.monotonic``.

    The result is deterministic: the same links and the same bytes give the
    same rules, byte for byte, in the links' order and then each stylesheet's
    own. ``Fonts.files`` is what the store writes under ``blog/fonts/``;
    ``Fonts.css`` goes to ``apply``; ``Fonts.note`` goes on the draft.

    A link or a file that fails costs only itself. A bug in here costs the
    whole copy - the entry keeps its fallback fonts - and is counted for
    ``/health`` (``blog.fonts``), because a crash should not look like a slow
    network."""
    try:
        settings = _settings(cfg)
        if not settings["enabled"]:
            return Fonts("", {}, "")
        return _Copy(fetch, settings, clock or monotonic).run(font_links)
    except Exception:
        _degrade.degraded("blog.fonts")
        return Fonts("", {}, _note(Counter(error=1), {}, {}))


# The mark exactly as the cleaner writes it: the whole text of a ``<style>``.
_SLOT = f"<style>{clean.FONT_CSS_MARK}</style>"


def apply(html, fonts: Fonts) -> str:
    """``html`` with its font mark replaced by ``fonts.css``.

    The mark is in a cleaned document exactly once, as the whole text of a
    ``<style>``. A document where that is not so - no mark, two, or one that is
    somewhere else - is not one ``clean`` wrote, and comes back UNCHANGED: this
    does not guess where rules might go.

    ``fonts.css`` is checked again here (``_written_here``). Rules that are not
    of this module's own shape go in as nothing, and that is counted: nothing
    cleans the document after this, so this is the last place to refuse.

    ⚠ Do not ``clean`` the result. The cleaner drops every ``@font-face`` it
    meets, these included."""
    if not isinstance(html, str) or html.count(clean.FONT_CSS_MARK) != 1 or _SLOT not in html:
        return html
    css = fonts.css if isinstance(fonts, Fonts) else None
    if not _written_here(css):
        _degrade.degraded("blog.fonts", detail="rules refused at apply", exc_info=False)
        css = ""
    return html.replace(clean.FONT_CSS_MARK, css)
