"""The kit for the typeface tests: fakes, sample stylesheets, the audit.

The suite cannot reach the network and must not try: every test hands
``localize`` a fake ``fetch`` that records what it was asked for (``Web``), and
the tests of ``http_fetch`` itself replace ``requests.get`` (``Requests``,
``Response``, ``fetched``).

**Google's real answer was never fetched while these were written.** The
stylesheets ``face`` builds are typed in the shape the css2 endpoint is known
to send a desktop Chrome - a ``/* subset */`` comment, then an ``@font-face``
block with single-quoted strings - including the variable-weight form
(``font-weight: 100 900``) and ``font-stretch: 100%``. They pin what the
reader does with THAT shape. Whether the endpoint still sends it is a check
for the first live run (docs/plans/2026-10-06-site-blog-plan.md, Task 12).

Two kinds of check, as in the cleaner's tests: needles, and ``audit`` - every
promise the module makes about what it returns, read back with tinycss2's RULE
parser (the module validates its own output with a pattern, so the audit is a
second reading and not the same one twice).

Characters that are invisible or easy to mistype are built from their code
points (``chr(0x200B)``), never written as an escape or a literal.

Not a test module, so ``conftest.py`` registers it for assert rewriting.
"""
import hashlib
import pathlib
import re
import socket
import ssl
import struct

import requests
import tinycss2
import urllib3

from services import _degrade
from services.blog_svc import clean, fonts
from shared import blog_inbox


# ── addresses, and pieces of the sentences a note is made of ──────────────────

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "entry_like_the_example.html"
LINK = "https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap"
LINK_2 = "https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400&display=swap"
LINKS = [f"https://fonts.googleapis.com/css2?family=F{n}" for n in range(4)]
G = "https://fonts.gstatic.com/s/inter/v13/"
LATIN = "U+0000-00FF, U+0131, U+0152-0153"
LATIN_OUT = "U+0000-00FF,U+0131,U+0152-0153"
SOME = "Some typefaces were not copied and are shown in a fallback font: "
NONE = "No typefaces were copied, so the entry is shown in its fallback fonts: "
MARK = clean.FONT_CSS_MARK
SLOT = "<style>" + MARK + "</style>"
HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>t</title>')
REST = "</head><body><p>x</p></body></html>"
# Who the service says it is, as shipped: what every request carries unless a
# test sets another.
SHIPPED_AGENT = blog_inbox.DEFAULTS["fonts"]["user_agent"]


# ── characters that must never be typed ────────────────────────────────────────

ZERO_WIDTH_SPACE = chr(0x200B)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
E_ACUTE = chr(0xE9)
CJK_MIDDLE = chr(0x4E2D)
ONE_DOT_LEADER = chr(0x2024)            # draws as a full stop, and is not one
CYRILLIC_A = chr(0x430)                 # draws as a Latin "a", and is not one
LINE_SEPARATOR = chr(0x2028)
PARAGRAPH_SEPARATOR = chr(0x2029)
LAST_CODE_POINT = chr(0x10FFFF)
BACKSLASH = chr(0x5C)
DELETE = chr(0x7F)
for _ch in (ZERO_WIDTH_SPACE, RIGHT_TO_LEFT_OVERRIDE, E_ACUTE, CJK_MIDDLE,
            ONE_DOT_LEADER, CYRILLIC_A):
    assert len(_ch) == 1
for _ch in (LINE_SEPARATOR, PARAGRAPH_SEPARATOR, LAST_CODE_POINT, BACKSLASH, DELETE):
    assert len(_ch) == 1


# ── files and stylesheets ────────────────────────────────────────────────────────

def woff2(seed, size=200) -> bytes:
    """A file with a real WOFF2 header and ``size`` bytes after it: the
    signature, a flavor, the file's own total length, a table count, and the
    rest of the 48 header bytes. Different ``seed``, different bytes."""
    payload = (hashlib.sha256(seed.encode("ascii")).digest() * (size // 32 + 1))[:size]
    header = b"wOF2" + b"\x00\x01\x00\x00" + struct.pack(">IHH", 48 + size, 9, 0)
    return header + bytes(48 - len(header)) + payload


def name_of(data) -> str:
    return hashlib.sha256(data).hexdigest()[:20] + ".woff2"


A, B, C = woff2("a"), woff2("b"), woff2("c")


def face(subset, url, *, family="'Inter'", style="normal", weight="400", stretch=None,
         display="swap", src=None, unicode_range=LATIN, extra="") -> str:
    """One block as the css2 endpoint writes it. ``family`` is CSS source,
    quotes included; ``src`` replaces the whole value of the src descriptor."""
    if src is None:
        src = f"url({url}) format('woff2')"
    lines = [f"/* {subset} */", "@font-face {", f"  font-family: {family};",
             f"  font-style: {style};", f"  font-weight: {weight};"]
    if stretch is not None:
        lines.append(f"  font-stretch: {stretch};")
    lines += [f"  font-display: {display};", f"  src: {src};",
              f"  unicode-range: {unicode_range};"]
    if extra:
        lines.append(f"  {extra}")
    return "\n".join(lines + ["}"]) + "\n"


def rule(data, *, family="Inter", style="normal", weight="400", stretch=None,
         display="swap", unicode_range=LATIN_OUT) -> str:
    """The rule ``fonts.py`` is expected to write for a block whose file holds
    ``data``. Typed out here, not asked of the module."""
    parts = [f'font-family:"{family}"', f"font-style:{style}", f"font-weight:{weight}"]
    if stretch is not None:
        parts.append(f"font-stretch:{stretch}")
    parts += [f"font-display:{display}",
              f'src:url(../fonts/{name_of(data)}) format("woff2")',
              f"unicode-range:{unicode_range}"]
    return "@font-face{" + ";".join(parts) + "}"


def sheet(*blocks) -> bytes:
    return "".join(blocks).encode("utf-8")


def one_file_blocks(family, count) -> str:
    """``count`` blocks, no two alike, every one naming the SAME file: how the
    number of rules grows while the number of files stays at one."""
    assert count <= 2000
    return "".join(
        "/* latin */@font-face{font-family:'%s';font-weight:%d;font-stretch:%d%%;"
        "src:url(%sone.woff2) format('woff2')}\n" % (family, 1 + n % 1000, 100 + n // 1000, G)
        for n in range(count))


# ── the fake fetch ───────────────────────────────────────────────────────────────

class Web:
    """The fake ``fetch``: a dict of address -> what to hand back. An exception
    is raised, anything else is returned AS IT IS (so a test can hand back a
    string, a number or ``None``). An address not in the dict fails the way a
    network does (``OSError``): a failure of the other end, which is not
    counted as a fault of this service's - a ``KeyError`` would be."""

    def __init__(self, pages):
        self.pages = dict(pages)
        self.calls = []

    def __call__(self, url, *, headers, timeout, max_bytes):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout,
                           "max_bytes": max_bytes})
        if url not in self.pages:
            raise OSError("no such page")
        answer = self.pages[url]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    @property
    def urls(self):
        return [call["url"] for call in self.calls]


def some_fonts():
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    return audit(fonts.localize([LINK], fetch=web))


# ── the audit: a second reading of what localize returns ──────────────────────

# ⚠ Typed out HERE, on purpose, and not read from the module: an audit that
# imported the module's allow-list would agree with any mistake made in it.
_DESCRIPTORS = {"font-family", "font-style", "font-weight", "font-stretch",
                "font-display", "src", "unicode-range"}
_LOCAL_URL = re.compile(r"url\(\.\./fonts/[0-9a-f]{20}\.woff2\)")
_FAMILY = re.compile(r'font-family:"[^"]*"')
ONE_SENTENCE = re.compile(r"[A-Z][A-Za-z0-9 ,:']*\.")


def audit(result):
    """Every promise about a ``Fonts``, checked; returns it.

    The CSS is the part that matters: it is pasted into a ``<style>`` that
    nothing cleans afterwards. So it must be nothing but ``@font-face`` rules,
    each made only of the seven descriptors, each naming exactly one file, that
    file one of ``files`` - and no stored file may go unused."""
    assert isinstance(result, fonts.Fonts)
    assert isinstance(result.css, str) and isinstance(result.files, dict)
    assert result.note == "" or ONE_SENTENCE.fullmatch(result.note), result.note
    for name, data in result.files.items():
        assert blog_inbox.FONT_NAME_RE.match(name), name
        assert isinstance(data, bytes) and data[:4] == b"wOF2" and name == name_of(data)
    css = result.css
    for needle in ("<", ">", "//", "/*", "*/", "'", "@import", clean.FONT_CSS_MARK):
        assert needle not in css, f"{needle!r} in the typeface rules"
    # A family name is the one piece of free text, and LETTERS in it are only
    # letters: a face called "Expression" is a face. So the words are looked
    # for outside the names (a name holds no quote, so "[^"]*" is one name).
    lowered = _FAMILY.sub("", css.lower())
    for needle in ("expression", "javascript", "evil", "http", "import", "\\"):
        assert needle not in lowered, f"{needle!r} in the typeface rules"
    assert "url(" not in _LOCAL_URL.sub("", lowered)
    used = set()
    for node in tinycss2.parse_stylesheet(css, skip_comments=False, skip_whitespace=True):
        assert node.type == "at-rule" and node.lower_at_keyword == "font-face", node.type
        assert not [t for t in node.prelude if t.type != "whitespace"]
        declared = tinycss2.parse_blocks_contents(node.content, skip_comments=False,
                                                  skip_whitespace=True)
        assert all(item.type == "declaration" for item in declared)
        names = [item.lower_name for item in declared]
        assert set(names) <= _DESCRIPTORS and len(names) == len(set(names)), names
        assert {"font-family", "src"} <= set(names)
        src = [t for t in declared[names.index("src")].value if t.type != "whitespace"]
        assert [t.type for t in src] == ["url", "function"]
        assert src[0].value.startswith("../fonts/")
        assert tinycss2.serialize(src[1:]) == 'format("woff2")'
        used.add(src[0].value[len("../fonts/"):])
    assert used == set(result.files), "a rule without its file, or a file without a rule"
    return result


def families(css) -> list:
    """The value of every font-family in ``css``, as a browser would read it."""
    found = []
    for node in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        for item in tinycss2.parse_blocks_contents(node.content, True, True):
            if item.lower_name == "font-family":
                value = [t for t in item.value if t.type != "whitespace"]
                assert [t.type for t in value] == ["string"]
                found.append(value[0].value)
    return found


def weights(css) -> list:
    """The font-weight of every rule in ``css``, in order."""
    found = []
    for node in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        for item in tinycss2.parse_blocks_contents(node.content, True, True):
            if item.lower_name == "font-weight":
                found.append(tinycss2.serialize(item.value).strip())
    return found


# ── failures, and which of them are ours ────────────────────────────────────────

class Loud(Exception):
    """An exception whose text must never reach the operator's screen."""

    def __str__(self):
        return "SECRET https://evil.test/?token=abc <script>"


# What a network does. The other end's failure, so NOT counted for /health: a
# day when Google is unreachable must not look like a fault in this service.
NETWORK_FAILURES = [
    OSError("connection refused"), TimeoutError(), ConnectionResetError(),
    fonts.FetchError("HTTP 500"), requests.exceptions.ConnectionError("x"),
    requests.exceptions.ReadTimeout(), requests.exceptions.ChunkedEncodingError(),
    requests.exceptions.SSLError(), urllib3.exceptions.ProtocolError("x"),
    requests.exceptions.ConnectTimeout(), requests.exceptions.ContentDecodingError(),
    requests.exceptions.TooManyRedirects(), requests.exceptions.ProxyError(),
    urllib3.exceptions.ReadTimeoutError(None, None, "x"), socket.gaierror(11001, "x"),
    ssl.SSLError("x"),
    # Not the network's at all - this box's own disk and permissions - and
    # still not counted, KNOWINGLY: a missing CA bundle and a refused connection
    # are both ``OSError``, and nothing on the exception tells them apart.
    FileNotFoundError("no CA bundle"), PermissionError("denied")]

# What a network does not do: a fault of ours. The operator's sentence is the
# same - the stylesheet was not fetched - and each one IS counted. Three kinds:
# * anything no network raises;
# * what ``requests`` raises about its ARGUMENTS, which are this service's. They
#   are RequestExceptions (so OSErrors), and each is also a ValueError or a
#   TypeError - which is how they are told from an outage;
# * ``http_fetch`` refusing what it was handed before connecting.
# ``MemoryError`` is a fault like any other: counted, and the draft still gets
# its fallback fonts and a note.
FAULTS = [ValueError("bad"), Loud(), RuntimeError("x"), KeyError("y"), RecursionError(),
          UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"), ZeroDivisionError(), TypeError(),
          AttributeError("x"), MemoryError(), AssertionError(), NotImplementedError(),
          requests.exceptions.InvalidHeader("x"), requests.exceptions.InvalidURL("x"),
          requests.exceptions.MissingSchema("x"), requests.exceptions.InvalidSchema("x"),
          requests.exceptions.StreamConsumedError(), requests.exceptions.InvalidProxyURL("x"),
          urllib3.exceptions.LocationParseError("x"),
          fonts.FetchRefused("no headers given"), fonts.FetchRefused("not an https address")]
FAILURES = NETWORK_FAILURES + FAULTS
# The degrade labels, one for each place a fault of ours can surface.
FETCH, READER, APPLY, GUARD = ("blog.fonts.fetch", "blog.fonts.reader", "blog.fonts.apply",
                               "blog.fonts.guard")


def failure_id(exc) -> str:
    """A parametrize id that tells two failures of one type apart."""
    return type(exc).__name__ + ("" if not exc.args else f"-{len(str(exc.args[0]))}")


def counted(failure, times=1) -> dict:
    """What ``_degrade.counts()`` should read after a fetch raised ``failure``
    ``times`` times."""
    return {FETCH: times} if any(failure is fault for fault in FAULTS) else {}


# ── standing where requests.get is ─────────────────────────────────────────────

class Response:
    def __init__(self, status=200, chunks=(b"body",), headers=None, error=None):
        self.status_code = status
        self.chunks = chunks
        self.headers = headers or {}
        self.error = error
        self.closed = False
        self.read = 0

    def iter_content(self, chunk_size=None):
        assert chunk_size and chunk_size <= 64 * 1024
        for chunk in self.chunks:
            self.read += len(chunk)
            yield chunk
        if self.error is not None:
            raise self.error

    def close(self):
        self.closed = True


class Requests:
    """Stands where ``requests.get`` is. No typeface test may reach the real
    one."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def fetched(monkeypatch, response, url=G + "a.woff2", max_bytes=1000):
    fake = Requests(response)
    monkeypatch.setattr(fonts.requests, "get", fake.get)
    try:
        return fake, fonts.http_fetch(url, headers={"User-Agent": SHIPPED_AGENT},
                                      timeout=7, max_bytes=max_bytes)
    except Exception as exc:
        return fake, exc
