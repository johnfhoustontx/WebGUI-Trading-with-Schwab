"""Copying an entry's typefaces onto the box.

The suite cannot reach the network and must not try: every test here hands
``localize`` a fake ``fetch`` that records what it was asked for (``Web``), and
the tests of ``http_fetch`` itself replace ``requests.get``.

⚠ **Google's real answer was never fetched while this was written.** The
stylesheets below are typed in the shape the css2 endpoint is known to send a
desktop Chrome - a ``/* subset */`` comment, then an ``@font-face`` block with
single-quoted strings - including the variable-weight form (``font-weight: 100
900``) and ``font-stretch: 100%``. They pin what the parser does with THAT
shape. Whether the endpoint still sends it is a check for the first live run,
and ``fonts.py``'s docstring lists what to look at.

Two kinds of check, as in ``test_clean.py``: needles, and ``audit`` - every
promise the module makes about what it returns, read back with tinycss2's RULE
parser (the module itself validates its output with a pattern, so the audit is
a second reading and not the same one twice).

Characters that are invisible or easy to mistype are built from their code
points (``chr(0x200B)``), never written as an escape or a literal.
"""
import hashlib
import inspect
import pathlib
import random
import re
import struct
import time
from urllib.parse import urlsplit

import pytest
import requests
import tinycss2
import urllib3
from tinycss2.serializer import serialize_string_value

from services import _degrade
from services.blog_svc import clean, fonts
from shared import blog_inbox

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "entry_like_the_example.html"

LINK = "https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap"
LINK_2 = "https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400&display=swap"
G = "https://fonts.gstatic.com/s/inter/v13/"
LATIN = "U+0000-00FF, U+0131, U+0152-0153"
LATIN_OUT = "U+0000-00FF,U+0131,U+0152-0153"

ZERO_WIDTH_SPACE = chr(0x200B)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
E_ACUTE = chr(0xE9)
CJK_MIDDLE = chr(0x4E2D)
ONE_DOT_LEADER = chr(0x2024)            # draws as a full stop, and is not one
CYRILLIC_A = chr(0x430)                 # draws as a Latin "a", and is not one
for _ch in (ZERO_WIDTH_SPACE, RIGHT_TO_LEFT_OVERRIDE, E_ACUTE, CJK_MIDDLE,
            ONE_DOT_LEADER, CYRILLIC_A):
    assert len(_ch) == 1


# ── what the tests are built from ────────────────────────────────────────────

def woff2(seed, size=200) -> bytes:
    """A file with a real WOFF2 header and ``size`` bytes after it: the
    signature, a flavor, the file's own total length, a table count, and the
    rest of the 48 header bytes. Different ``seed``, different bytes."""
    payload = (hashlib.sha256(seed.encode("ascii")).digest() * (size // 32 + 1))[:size]
    header = b"wOF2" + b"\x00\x01\x00\x00" + struct.pack(">IHH", 48 + size, 9, 0)
    return header + bytes(48 - len(header)) + payload


def name_of(data) -> str:
    return hashlib.sha256(data).hexdigest()[:20] + ".woff2"


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


# ⚠ Typed out HERE, on purpose, and not read from the module: an audit that
# imported the module's allow-list would agree with any mistake made in it.
_DESCRIPTORS = {"font-family", "font-style", "font-weight", "font-stretch",
                "font-display", "src", "unicode-range"}
_LOCAL_URL = re.compile(r"url\(\.\./fonts/[0-9a-f]{20}\.woff2\)")
_FAMILY = re.compile(r'font-family:"[^"]*"')
_ONE_SENTENCE = re.compile(r"[A-Z][A-Za-z0-9 ,:']*\.")


def audit(result):
    """Every promise about a ``Fonts``, checked; returns it.

    The CSS is the part that matters: it is pasted into a ``<style>`` that
    nothing cleans afterwards. So it must be nothing but ``@font-face`` rules,
    each made only of the seven descriptors, each naming exactly one file, that
    file one of ``files`` - and no stored file may go unused."""
    assert isinstance(result, fonts.Fonts)
    assert isinstance(result.css, str) and isinstance(result.files, dict)
    assert result.note == "" or _ONE_SENTENCE.fullmatch(result.note), result.note
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


A, B, C = woff2("a"), woff2("b"), woff2("c")


# ── the shape Google sends ───────────────────────────────────────────────────

def test_a_stylesheet_shaped_like_googles_becomes_local_rules():
    """The whole path on the ordinary case: one link, three subsets offered,
    two copied; a static weight, an italic, and the variable form."""
    css = sheet(
        face("cyrillic", G + "cyr.woff2", unicode_range="U+0301, U+0400-045F"),
        face("latin-ext", G + "ext.woff2", weight="100 900", stretch="100%",
             unicode_range="U+0100-02BA, U+1E00-1E9F, U+A720-A7FF"),
        face("latin", G + "lat.woff2", weight="100 900", stretch="100%"),
        face("latin", G + "ital-KFOmCnqEu92Fr1Mu_4.woff2", style="italic", weight="700"),
    )
    web = Web({LINK: css, G + "ext.woff2": A, G + "lat.woff2": B,
               G + "ital-KFOmCnqEu92Fr1Mu_4.woff2": C})
    result = audit(fonts.localize((LINK,), fetch=web))
    assert result.css == "\n".join([
        rule(A, weight="100 900", stretch="100%",
             unicode_range="U+0100-02BA,U+1E00-1E9F,U+A720-A7FF"),
        rule(B, weight="100 900", stretch="100%"),
        rule(C, style="italic", weight="700"),
    ])
    assert result.files == {name_of(A): A, name_of(B): B, name_of(C): C}
    assert result.note == ""
    assert web.urls == [LINK, G + "ext.woff2", G + "lat.woff2",
                        G + "ital-KFOmCnqEu92Fr1Mu_4.woff2"]


def test_every_request_is_sent_as_a_desktop_chrome_and_capped():
    """Google serves woff2, split by unicode-range, only to a browser it knows
    can take it. The stylesheet and the files have different size limits."""
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    audit(fonts.localize([LINK], fetch=web))
    assert re.fullmatch(r"Mozilla/5\.0 \(Windows NT 10\.0; Win64; x64\) AppleWebKit/537\.36 "
                        r"\(KHTML, like Gecko\) Chrome/\d+\.0\.0\.0 Safari/537\.36",
                        fonts.USER_AGENT)
    cfg = blog_inbox.fonts()
    assert [call["headers"]["User-Agent"] for call in web.calls] == [fonts.USER_AGENT] * 2
    assert [call["max_bytes"] for call in web.calls] == [cfg["max_css_kb"] * 1024,
                                                         cfg["max_file_kb"] * 1024]
    assert all(0 < call["timeout"] <= cfg["timeout_sec"] for call in web.calls)


def test_the_real_fetch_is_the_default_and_the_shipped_config_is_read(monkeypatch):
    assert inspect.signature(fonts.localize).parameters["fetch"].default is fonts.http_fetch
    assert (fonts.CSS_HOST, fonts.FILE_HOST) == ("fonts.googleapis.com", "fonts.gstatic.com")
    web = Web({LINK: sheet(face("greek", G + "g.woff2"), face("latin", G + "l.woff2")),
               G + "g.woff2": A, G + "l.woff2": B})
    monkeypatch.setattr(blog_inbox, "load", lambda: {"fonts": {"subsets": ["greek"]}})
    assert audit(fonts.localize([LINK], fetch=web)).files == {name_of(A): A}


def test_an_entry_with_no_links_asks_for_nothing():
    web = Web({})
    for nothing in ((), [], None):
        assert fonts.localize(nothing, fetch=web) == fonts.Fonts("", {}, "")
    assert web.calls == []


def test_switched_off_nothing_is_fetched_and_nothing_is_said():
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    assert fonts.localize([LINK], fetch=web, cfg={"enabled": False}) == fonts.Fonts("", {}, "")
    assert web.calls == []


def test_the_example_shaped_entry_end_to_end():
    """clean -> localize -> apply, on the fixture: the one link the entry
    carries is the one followed, and the rules land in the marked <style>."""
    cleaned = clean.clean(FIXTURE.read_text(encoding="utf-8"))
    (link,) = cleaned.font_links
    web = Web({link: sheet(face("latin", G + "sg.woff2", family="'Schibsted Grotesk'",
                                weight="500")),
               G + "sg.woff2": A})
    result = audit(fonts.localize(cleaned.font_links, fetch=web))
    assert result.css == rule(A, family="Schibsted Grotesk", weight="500")
    page = fonts.apply(cleaned.html, result)
    assert f"<title>{cleaned.title}</title><style>{result.css}</style>" in page
    assert clean.FONT_CSS_MARK not in page and "fonts.googleapis.com" not in page
    assert len(page) == len(cleaned.html) - len(clean.FONT_CSS_MARK) + len(result.css)


# ── which addresses are ever asked for ───────────────────────────────────────

NOT_A_STYLESHEET_LINK = [
    "http://fonts.googleapis.com/css2?family=Inter",
    "https://fonts.googleapis.com/css?family=Inter",                # the old endpoint
    "https://fonts.googleapis.com/css2",
    "https://fonts.googleapis.com/css2?",
    "https://fonts.googleapis.com/icon?family=Material+Icons",
    "https://fonts.googleapis.com.evil.test/css2?family=Inter",
    "https://fonts.googleapis.com@evil.test/css2?family=Inter",
    "https://evil.test/css2?family=Inter",
    "https://fonts.googleapis.com:8443/css2?family=Inter",
    "https://fonts.googleapis.com./css2?family=Inter",
    "https://FONTS.GOOGLEAPIS.COM/css2?family=Inter",
    "https://fonts.googleapis.com/css2?family=Inter#x",
    "https://fonts.googleapis.com/css2?family=Inter/../../x",
    "https://fonts.googleapis.com/css2?family=Inter\n",
    " https://fonts.googleapis.com/css2?family=Inter",
    "https://fonts.googleapis.com/css2?family=Caf" + E_ACUTE,
    "https://fonts.googleapis" + ONE_DOT_LEADER + "com/css2?family=Inter",
    "https://fonts.googleapis.com/css2?family=" + "A" * 5000,       # no real link is this long
    "//fonts.googleapis.com/css2?family=Inter",
    "", 7, None, b"https://fonts.googleapis.com/css2?family=Inter", ["nested"],
]


@pytest.mark.parametrize("link", NOT_A_STYLESHEET_LINK, ids=repr)
def test_only_a_google_fonts_stylesheet_link_is_followed(link):
    """The cleaner already matches the links it hands over. This module checks
    again rather than trust its caller: what it is given becomes a request."""
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    result = audit(fonts.localize([link, LINK], fetch=web))
    assert web.urls == [LINK, G + "a.woff2"]
    assert result.files == {name_of(A): A}                  # the good link is not lost
    assert result.note.startswith("Some typefaces were not copied")
    assert "1 link was not a Google Fonts stylesheet" in result.note


def test_the_link_pattern_is_the_cleaners_own():
    """Two copies of one rule, on purpose: this module checks what the cleaner
    already checked. Pinned to each other, so a character allowed in one and
    not the other cannot turn into links that are found and then all skipped."""
    assert fonts._LINK_RE.pattern == clean._FONT_LINK_RE.pattern
    assert fonts._LINK_RE.flags == clean._FONT_LINK_RE.flags


def test_only_googles_two_hosts_are_ever_fetched():
    """Whatever the links say and whatever the stylesheet says, every address
    handed to ``fetch`` is https on one of the two hosts - the stylesheet host
    for a link, the file host for a file, never the other way round."""
    hostile = [
        "https://evil.test/a.woff2", "http://fonts.gstatic.com/s/a.woff2",
        "https://fonts.gstatic.com.evil.test/s/a.woff2",
        "https://fonts.gstatic.com@evil.test/s/a.woff2",
        "https://user:pw@fonts.gstatic.com/s/a.woff2",
        "https://fonts.gstatic.com:8443/s/a.woff2", "https://fonts.gstatic.com./s/a.woff2",
        "https://FONTS.GSTATIC.COM/s/a.woff2", "//fonts.gstatic.com/s/a.woff2",
        "https://fonts.googleapis.com/s/a.woff2",           # the stylesheet host is not a file host
        "https://fonts.gstatic.com/s/a.ttf", "https://fonts.gstatic.com/s/a.woff2?x=1",
        "https://fonts.gstatic.com/s/a.woff2#x", "https://fonts.gstatic.com/s/../a.woff2",
        "https://fonts.gstatic.com/s/a%2e%2e/b.woff2", "https://fonts.gstatic.com//a.woff2",
        "https://fonts.gstatic.com/.woff2", "https://fonts.gstatic.com/s/" + "a" * 600 + ".woff2",
        "data:font/woff2;base64,d09GMg==", "../fonts/" + "0" * 20 + ".woff2",
        "/blog/fonts/" + "0" * 20 + ".woff2", "file:///etc/passwd", "a.woff2",
    ]
    css = sheet(*[face("latin", url, family=f"'F{n}'") for n, url in enumerate(hostile)],
                face("latin", G + "good.woff2"))
    web = Web({LINK: css, LINK_2: css, G + "good.woff2": A})
    result = audit(fonts.localize(
        [LINK, "https://evil.test/css2?family=X", "https://fonts.gstatic.com/css2?family=X",
         LINK_2], fetch=web))
    assert web.urls == [LINK, LINK_2, G + "good.woff2"]
    for url in web.urls:
        parts = urlsplit(url)
        assert parts.scheme == "https" and parts.netloc in (fonts.CSS_HOST, fonts.FILE_HOST)
    assert result.css == rule(A) and result.files == {name_of(A): A}
    assert f"{2 * len(hostile)} typeface rules were not usable" in result.note
    assert "2 links were not Google Fonts stylesheets" in result.note


def test_a_local_looking_address_in_the_stylesheet_is_not_passed_through():
    """The one ``url()`` the result may hold is ``../fonts/<name>``. A
    stylesheet that already says exactly that names a file this module never
    fetched and never checked."""
    planted = "../fonts/" + "ab" * 10 + ".woff2"
    web = Web({LINK: sheet(face("latin", planted))})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == "" and result.files == {} and web.urls == [LINK]


# ── which blocks are kept ────────────────────────────────────────────────────

def test_only_the_configured_subsets_are_kept():
    blocks = {subset: woff2(subset) for subset in
              ("cyrillic-ext", "cyrillic", "greek", "vietnamese", "latin-ext", "latin")}
    css = sheet(*[face(subset, G + subset + ".woff2") for subset in blocks])
    pages = {LINK: css, **{G + subset + ".woff2": data for subset, data in blocks.items()}}

    web = Web(pages)
    shipped = audit(fonts.localize([LINK], fetch=web))
    assert set(shipped.files) == {name_of(blocks["latin-ext"]), name_of(blocks["latin"])}
    assert web.urls == [LINK, G + "latin-ext.woff2", G + "latin.woff2"]
    assert shipped.note == ""                               # the others are not a failure

    web = Web(pages)
    greek = audit(fonts.localize([LINK], fetch=web, cfg={"subsets": ["greek"]}))
    assert set(greek.files) == {name_of(blocks["greek"])}
    assert web.urls == [LINK, G + "greek.woff2"]


@pytest.mark.parametrize("label", [
    None,                               # no comment at all
    "/* [0] */",                        # how Google labels the slices of a CJK face
    "/* LATIN */", "/* latin, latin-ext */", "/* ../latin */", "/**/", "/* */",
    "/* latin */ /* something else */",                     # the LAST comment is the label
    "/* latin */ .x{color:red}",                            # a rule in between: no longer its label
    "/* latin */ @media print {}",
    "/* latin */ ;",
], ids=repr)
def test_a_block_with_no_recognisable_subset_label_is_dropped(label):
    block = face("latin", G + "x.woff2").split("\n", 1)[1]              # the block, unlabelled
    css = sheet(face("latin", G + "a.woff2"), (label or "") + "\n" + block,
                face("latin", G + "b.woff2", weight="700"))
    web = Web({LINK: css, G + "a.woff2": A, G + "b.woff2": B, G + "x.woff2": C})
    result = audit(fonts.localize([LINK], fetch=web))
    assert G + "x.woff2" not in web.urls
    assert result.css == rule(A) + "\n" + rule(B, weight="700")


def test_a_label_is_used_once():
    """One comment labels the ONE block that follows it. A second block riding
    on the first one's label would be a way round the subset list."""
    unlabelled = face("latin", G + "x.woff2").split("\n", 1)[1]
    web = Web({LINK: sheet(face("latin", G + "a.woff2"), unlabelled),
               G + "a.woff2": A, G + "x.woff2": C})
    assert audit(fonts.localize([LINK], fetch=web)).css == rule(A)
    assert web.urls == [LINK, G + "a.woff2"]


def test_a_stylesheet_with_nothing_in_the_copied_character_sets_says_so():
    web = Web({LINK: sheet(face("cyrillic", G + "c.woff2"), face("greek", G + "g.woff2"))})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == "" and result.files == {} and web.urls == [LINK]
    assert result.note == ("No typefaces were copied, so the entry is shown in its fallback "
                           "fonts: 1 typeface was not copied because it is not offered in the "
                           "character sets copied here.")


def test_a_block_with_a_second_url_is_dropped():
    two = f"url({G}x.woff2) format('woff2'), url({G}y.woff2) format('woff2')"
    css = sheet(
        face("latin", G + "a.woff2"),
        face("latin", None, src=two, family="'Two'"),
        # ... wherever the second one is: in a descriptor this module does not
        # copy, spelled with an escape, quoted, or broken.
        face("latin", G + "x.woff2", family="'Elsewhere'",
             extra=f"size-adjust: url({G}y.woff2);"),
        face("latin", G + "x.woff2", family="'Escaped'", extra=f"x: u\\72l({G}y.woff2);"),
        face("latin", G + "x.woff2", family="'Quoted'", extra=f"x: url('{G}y.woff2');"),
        face("latin", G + "x.woff2", family="'Nested'", extra=f"x: f([{{url({G}y.woff2)}}]);"),
        face("latin", G + "x.woff2", family="'Broken'", extra="x: url(a b);"),
        face("latin", G + "x.woff2", family="'Twice'", extra=f"src: url({G}y.woff2) "
                                                             "format('woff2');"),
    )
    web = Web({LINK: css, G + "a.woff2": A, G + "x.woff2": B, G + "y.woff2": C})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == rule(A) and web.urls == [LINK, G + "a.woff2"]
    assert "7 typeface rules were not usable" in result.note


# One line each: something in the place of a good block, or beside it. The
# good block before it must always survive; the one after it survives unless
# the junk is a rule that swallows it (a stray "<" opens a qualified rule,
# which runs to the next "{}").
HOSTILE_SHEETS = [
    ("script", "</style><script>alert(1)</script>", False),
    ("script in a comment", "/* </style><script>alert(1)</script> */", True),
    ("html comment", "<!-- --> ", True),
    ("charset", '@charset "utf-8";', True),
    ("import", "@import url(https://evil.test/x.css);", True),
    ("import of a real stylesheet",
     '@import "https://fonts.googleapis.com/css2?family=Evil";', True),
    ("namespace", "@namespace url(https://evil.test/ns);", True),
    ("another rule", "body{background:url(https://evil.test/t.gif)}", True),
    ("nested in a condition",
     "@media screen { " + face("latin", G + "x.woff2", family="'N'") + "}", True),
    ("nested in supports",
     "@supports (display:grid) { " + face("latin", G + "x.woff2", family="'N'") + "}", True),
    ("an at-rule inside the block",
     face("latin", G + "x.woff2", family="'N'", extra="@import url(https://evil.test/x);"), True),
    ("a rule inside the block",
     face("latin", G + "x.woff2", family="'N'", extra="a{color:red}"), True),
    ("a prelude", face("latin", G + "x.woff2").replace("@font-face {", "@font-face x {"), True),
    ("no block", "/* latin */ @font-face;", True),
    ("another host", face("latin", "https://evil.test/a.woff2"), True),
    ("plain http", face("latin", "http://fonts.gstatic.com/s/a.woff2"), True),
    ("a look-alike host", face("latin", "https://fonts.gstatic.com.evil.test/s/a.woff2"), True),
    ("a host behind userinfo", face("latin", "https://fonts.gstatic.com@evil.test/s/a.woff2"),
     True),
    ("local first", face("latin", None, src=f"local('Inter'), url({G}x.woff2) format('woff2')"),
     True),
    ("no format", face("latin", None, src=f"url({G}x.woff2)"), True),
    ("another format", face("latin", None, src=f"url({G}x.woff2) format('truetype')"), True),
    ("two formats", face("latin", None, src=f"url({G}x.woff2) format('woff2', 'woff')"), True),
    ("a format and more", face("latin", None, src=f"url({G}x.woff2) format('woff2') tech(x)"),
     True),
    ("important", face("latin", None, src=f"url({G}x.woff2) format('woff2') !important"), True),
    ("no src", face("latin", G + "x.woff2").replace("  src:", "  x-src:"), True),
    ("no family", face("latin", G + "x.woff2").replace("  font-family:", "  x-family:"), True),
    ("two families", face("latin", G + "x.woff2", family="'A', 'B'"), True),
    ("two family declarations", face("latin", G + "x.woff2", extra="font-family: 'Z';"), True),
    ("a number for a family", face("latin", G + "x.woff2", family="12"), True),
    ("a function for a family", face("latin", G + "x.woff2", family="var(--f)"), True),
    ("an empty family", face("latin", G + "x.woff2", family="''"), True),
    ("a blank family", face("latin", G + "x.woff2", family="'   '"), True),
    ("a very long family", face("latin", G + "x.woff2", family="'" + "A" * 500 + "'"), True),
    ("a family with a line break", face("latin", G + "x.woff2", family="'a\\A b'"), True),
    ("a family with a control", face("latin", G + "x.woff2", family="'a\\1 b'"), True),
    ("a family with a zero-width space",
     face("latin", G + "x.woff2", family="'a" + ZERO_WIDTH_SPACE + "b'"), True),
    ("a family that reorders the line",
     face("latin", G + "x.woff2", family="'a" + RIGHT_TO_LEFT_OVERRIDE + "b'"), True),
    ("weight: exponent", face("latin", G + "x.woff2", weight="1e3"), True),
    ("weight: negative", face("latin", G + "x.woff2", weight="-400"), True),
    ("weight: zero", face("latin", G + "x.woff2", weight="0"), True),
    ("weight: too heavy", face("latin", G + "x.woff2", weight="1001"), True),
    ("weight: three", face("latin", G + "x.woff2", weight="100 400 900"), True),
    ("weight: a function", face("latin", G + "x.woff2", weight="calc(400)"), True),
    ("weight: a variable", face("latin", G + "x.woff2", weight="var(--w)"), True),
    ("weight: a unit", face("latin", G + "x.woff2", weight="400px"), True),
    ("style: unknown", face("latin", G + "x.woff2", style="slanted"), True),
    ("style: italic with an angle", face("latin", G + "x.woff2", style="italic 5deg"), True),
    ("style: a right angle and more", face("latin", G + "x.woff2", style="oblique 100deg"), True),
    ("style: another unit", face("latin", G + "x.woff2", style="oblique 1rad"), True),
    ("style: an expression", face("latin", G + "x.woff2", style="expression(alert(1))"), True),
    ("stretch: a bare number", face("latin", G + "x.woff2", stretch="100"), True),
    ("stretch: negative", face("latin", G + "x.woff2", stretch="-5%"), True),
    ("stretch: three", face("latin", G + "x.woff2", stretch="50% 100% 150%"), True),
    ("stretch: unknown", face("latin", G + "x.woff2", stretch="squashed"), True),
    ("display: unknown", face("latin", G + "x.woff2", display="always"), True),
    ("display: two", face("latin", G + "x.woff2", display="swap block"), True),
    ("range: backwards", face("latin", G + "x.woff2", unicode_range="U+5-3"), True),
    ("range: past the end", face("latin", G + "x.woff2", unicode_range="U+110000"), True),
    ("range: too many digits", face("latin", G + "x.woff2", unicode_range="U+FFFFFFF"), True),
    ("range: a word", face("latin", G + "x.woff2", unicode_range="U+0-7F, javascript"), True),
    ("range: an expression",
     face("latin", G + "x.woff2", unicode_range="U+0-7F, expression(alert(1))"), True),
    ("range: no comma", face("latin", G + "x.woff2", unicode_range="U+0-7F U+80"), True),
    ("range: a trailing comma", face("latin", G + "x.woff2", unicode_range="U+0-7F,"), True),
    ("range: a doubled comma", face("latin", G + "x.woff2", unicode_range="U+0-7F,,U+80"), True),
    ("range: empty", face("latin", G + "x.woff2", unicode_range=" "), True),
    ("range: a string", face("latin", G + "x.woff2", unicode_range="'U+0-7F'"), True),
    ("the font mark for a family",
     face("latin", G + "x.woff2", family="'a', " + clean.FONT_CSS_MARK), True),
]


@pytest.mark.parametrize("junk, good_after_survives",
                         [(junk, after) for _id, junk, after in HOSTILE_SHEETS],
                         ids=[name for name, _junk, _after in HOSTILE_SHEETS])
def test_nothing_hostile_in_a_stylesheet_reaches_the_entry(junk, good_after_survives):
    css = sheet(face("latin", G + "a.woff2"), junk, "\n",
                face("latin", G + "b.woff2", weight="700"))
    web = Web({LINK: css, G + "a.woff2": A, G + "b.woff2": B, G + "x.woff2": C,
               G + "y.woff2": C})
    result = audit(fonts.localize([LINK], fetch=web))
    wanted = [rule(A)] + ([rule(B, weight="700")] if good_after_survives else [])
    assert result.css == "\n".join(wanted)
    assert web.urls == [LINK, G + "a.woff2"] + ([G + "b.woff2"] if good_after_survives else [])


def test_a_descriptor_this_module_does_not_copy_is_left_out_not_fatal():
    """Google may add a descriptor one day (``size-adjust``, ``ascent-override``).
    One that fetches nothing costs the block nothing: it is simply not copied."""
    css = sheet(face("latin", G + "a.woff2",
                     extra="size-adjust: 90%; ascent-override: 92%; font-feature-settings: 'ss01';"))
    web = Web({LINK: css, G + "a.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == rule(A) and result.note == ""


@pytest.mark.parametrize("written, expected", [
    ("font-style: oblique", "font-style:oblique"),
    ("font-style: oblique -10deg 0deg", "font-style:oblique -10deg 0deg"),
    ("font-style: oblique 12.5deg", "font-style:oblique 12.5deg"),
    ("font-style: ITALIC", "font-style:italic"),
    ("font-weight: bold", "font-weight:bold"),
    ("font-weight: 1 1000", "font-weight:1 1000"),
    ("font-weight: 350.5", "font-weight:350.5"),
    ("font-stretch: 75% 125%", "font-stretch:75% 125%"),
    ("font-stretch: semi-condensed", "font-stretch:semi-condensed"),
    ("font-display: optional", "font-display:optional"),
    ("unicode-range: u+41, U+4??, U+1F600-1F64F",
     "unicode-range:U+0041,U+0400-04FF,U+1F600-1F64F"),
])
def test_each_descriptor_is_written_from_what_it_means(written, expected):
    """The honest spellings beyond the common case, each rebuilt canonically."""
    block = ("/* latin */\n@font-face { font-family: Inter Display; "
             f"{written}; src: url({G}a.woff2) format(woff2) }}")
    web = Web({LINK: sheet(block), G + "a.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == ('@font-face{font-family:"Inter Display";'
                          + (expected + ";" if not expected.startswith("unicode") else "")
                          + f'src:url(../fonts/{name_of(A)}) format("woff2")'
                          + (";" + expected if expected.startswith("unicode") else "") + "}")


@pytest.mark.parametrize("family", [
    "Inter", "IBM Plex Mono", "Source Sans 3", "M PLUS Rounded 1c",
    'O\'Brien "Display"', "back\\slash", "</style><script>alert(1)</script>",
    "x" + clean.FONT_CSS_MARK + "y", "url(https://evil.test/x)", "@import 'x'",
    "a;b}c{d", "Caf" + E_ACUTE, CJK_MIDDLE + " Sans", "*/ x /*", "a\\3c b",
], ids=repr)
def test_a_family_name_is_rewritten_and_still_names_the_same_family(family):
    """The entry's own stylesheet asks for the family by name, so the name must
    come out the same - and it is the one piece of free text in the result, so
    it must come out unable to be anything else."""
    source = '"' + serialize_string_value(family) + '"'
    web = Web({LINK: sheet(face("latin", G + "a.woff2", family=source)), G + "a.woff2": A})
    css = fonts.localize([LINK], fetch=web).css
    assert families(css) == [family]
    opening = '@font-face{font-family:"'
    assert css.startswith(opening)
    written = css[len(opening):].split('";', 1)[0]
    assert re.fullmatch(r"(?:[A-Za-z0-9 _-]|\\[0-9a-f]{1,6} )+", written), written
    for ch in "<>'\"()@*/{};:":
        assert ch not in written, ch
    page = fonts.apply(clean.clean("<p>x</p>").html, fonts.localize([LINK], fetch=web))
    assert page.count("<style>") == 1 and page.count("</style>") == 1
    assert "<script" not in page and clean.FONT_CSS_MARK not in page


# ── the files ────────────────────────────────────────────────────────────────

NOT_A_TYPEFACE = [
    ("html", b"<!doctype html><html><body>Error 404</body></html>"),
    ("the signature and nothing else", b"wOF2"),
    ("a header cut short", woff2("x")[:47]),
    ("a header and no table", woff2("x")[:12] + b"\x00\x00" + woff2("x")[14:]),
    ("woff 1", b"wOFF" + woff2("x")[4:]),
    ("truetype", b"\x00\x01\x00\x00" + woff2("x")[4:]),
    ("cut short", woff2("x")[:-1]),
    ("with a tail", woff2("x") + b"<script>"),
    ("lower case", b"wof2" + woff2("x")[4:]),
    ("empty", b""),
    ("text", woff2("x").decode("latin-1")),
    ("none", None),
    ("a number", 7),
    ("a list", [woff2("x")]),
    ("too large", woff2("x", size=401 * 1024)),
]


@pytest.mark.parametrize("answer", [answer for _id, answer in NOT_A_TYPEFACE],
                         ids=[name for name, _answer in NOT_A_TYPEFACE])
def test_a_file_that_is_not_woff2_is_dropped(answer):
    """A file is stored only when it is what a browser would accept as WOFF2:
    the signature, a whole header, the length the header itself declares. The
    rule that named it goes with it; the others stay."""
    css = sheet(face("latin", G + "bad.woff2"), face("latin", G + "a.woff2", weight="700"))
    web = Web({LINK: css, G + "bad.woff2": answer, G + "a.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == rule(A, weight="700") and result.files == {name_of(A): A}
    assert "1 file could not be fetched or was not a typeface" in result.note


def test_a_file_at_the_size_limit_is_kept_and_one_byte_more_is_not():
    cap = blog_inbox.fonts()["max_file_kb"] * 1024
    exact, over = woff2("exact", size=cap - 48), woff2("over", size=cap - 47)
    assert (len(exact), len(over)) == (cap, cap + 1)
    css = sheet(face("latin", G + "exact.woff2"), face("latin", G + "over.woff2", weight="700"))
    web = Web({LINK: css, G + "exact.woff2": exact, G + "over.woff2": over})
    assert audit(fonts.localize([LINK], fetch=web)).files == {name_of(exact): exact}


def test_a_bytearray_is_stored_as_bytes():
    web = Web({LINK: bytearray(sheet(face("latin", G + "a.woff2"))), G + "a.woff2": bytearray(A)})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.files == {name_of(A): A} and type(result.files[name_of(A)]) is bytes


def test_the_same_bytes_get_the_same_name():
    """A name is the first 20 hex characters of the content's SHA-256, so the
    same file under two addresses is ONE file on disk - and a name can never be
    anything the stylesheet chose."""
    css = sheet(face("latin", G + "one.woff2"), face("latin", G + "two.woff2", weight="700"),
                face("latin-ext", G + "three.woff2"))
    web = Web({LINK: css, G + "one.woff2": A, G + "two.woff2": bytes(A), G + "three.woff2": B})
    result = audit(fonts.localize([LINK], fetch=web))
    expected = hashlib.sha256(A).hexdigest()[:20] + ".woff2"
    assert list(result.files) == [expected, name_of(B)]
    assert result.css.count(expected) == 2
    assert blog_inbox.FONT_NAME_RE.match(expected)


def test_a_file_named_by_several_blocks_is_fetched_once():
    """How Google serves a variable face asked for by weight: one block per
    weight, every one naming the same file."""
    css = sheet(*[face("latin", G + "var.woff2", weight=weight)
                  for weight in ("400", "500", "600", "700")])
    web = Web({LINK: css, G + "var.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web))
    assert web.urls == [LINK, G + "var.woff2"]
    assert result.css == "\n".join(rule(A, weight=w) for w in ("400", "500", "600", "700"))
    assert result.files == {name_of(A): A} and result.note == ""


def test_a_file_that_failed_is_not_asked_for_again():
    css = sheet(*[face("latin", G + "var.woff2", weight=weight) for weight in ("400", "700")])
    web = Web({LINK: css, G + "var.woff2": OSError("down")})
    result = audit(fonts.localize([LINK], fetch=web))
    assert web.urls == [LINK, G + "var.woff2"] and result.css == ""
    assert "1 file could not be fetched or was not a typeface" in result.note


def test_a_rule_repeated_word_for_word_is_written_once():
    css = sheet(face("latin", G + "a.woff2")) * 3
    web = Web({LINK: css, LINK_2: css, G + "a.woff2": A})
    result = audit(fonts.localize([LINK, LINK_2], fetch=web))
    assert result.css == rule(A) and web.urls == [LINK, LINK_2, G + "a.woff2"]


def test_the_file_cap_holds_and_is_noted():
    files = {G + f"f{n}.woff2": woff2(f"f{n}") for n in range(7)}
    css = sheet(*[face("latin", url, weight=str(100 + 100 * n)) for n, url in enumerate(files)])
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_files": 3}))
    assert web.urls == [LINK] + list(files)[:3]
    assert len(result.files) == 3 and result.css.count("@font-face") == 3
    assert result.note == ("Some typefaces were not copied and are shown in a fallback "
                           "font: 4 files were past the limit of 3.")


def test_the_file_cap_counts_requests_not_successes():
    """The cap is on the work done for one entry. A stylesheet whose first
    files all fail must not get the cap again in tries."""
    files = {G + f"f{n}.woff2": (OSError("down") if n < 3 else woff2(f"f{n}")) for n in range(6)}
    css = sheet(*[face("latin", url, weight=str(100 + 100 * n)) for n, url in enumerate(files)])
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_files": 3}))
    assert len(web.calls) == 1 + 3 and result.files == {}
    assert "3 files could not be fetched" in result.note
    assert "3 files were past the limit of 3" in result.note


def test_the_link_cap_holds_and_is_noted():
    links = [f"https://fonts.googleapis.com/css2?family=F{n}" for n in range(7)]
    web = Web({link: sheet(face("latin", G + f"f{n}.woff2", family=f"'F{n}'"))
               for n, link in enumerate(links)}
              | {G + f"f{n}.woff2": woff2(f"f{n}") for n in range(7)})
    result = audit(fonts.localize(links + links, fetch=web))       # a repeat is not a second link
    shipped = blog_inbox.fonts()["max_links"]
    assert shipped == 4
    assert [url for url in web.urls if "css2" in url] == links[:4]
    assert len(result.files) == 4
    assert result.note == ("Some typefaces were not copied and are shown in a fallback "
                           "font: 3 stylesheets were past the limit of 4.")


def test_a_stylesheet_over_its_size_limit_is_not_read():
    """Whatever ``fetch`` promises about ``max_bytes``, what comes back is
    measured here too: the fetch is injected."""
    cap = 16 * 1024
    block = face("latin", G + "a.woff2")
    big = sheet(block, "/*" + "x" * cap + "*/")
    web = Web({LINK: big, LINK_2: sheet(face("latin", G + "b.woff2")), G + "a.woff2": A,
               G + "b.woff2": B})
    result = audit(fonts.localize([LINK, LINK_2], fetch=web, cfg={"max_css_kb": 16}))
    assert web.calls[0]["max_bytes"] == cap
    assert web.urls == [LINK, LINK_2, G + "b.woff2"] and result.files == {name_of(B): B}
    assert "1 stylesheet could not be fetched" in result.note


def test_ten_thousand_blocks_cost_a_bounded_amount_of_work():
    """10,000 blocks naming 10,000 files fit in a stylesheet at the size
    limit's ceiling. The file cap bounds the requests; the parse is one pass."""
    css = sheet(*["/* latin */@font-face{font-family:A;font-weight:%d;"
                  "src:url(%sn%05d.woff2) format('woff2')}" % (1 + n % 1000, G, n)
                  for n in range(10000)])
    assert 512 * 1024 < len(css) < 2048 * 1024
    files = {G + "n%05d.woff2" % n: woff2(str(n)) for n in range(24)}
    web = Web({LINK: css, **files})
    started = time.perf_counter()
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_css_kb": 2048}))
    assert time.perf_counter() - started < 20
    assert len(web.calls) == 1 + 24 and len(result.files) == 24
    assert "9976 files were past the limit of 24" in result.note
    # ... and at the shipped limit it is not read at all.
    web = Web({LINK: css, **files})
    assert audit(fonts.localize([LINK], fetch=web)).files == {} and web.urls == [LINK]


def test_a_stylesheet_broken_at_random_never_breaks_the_promises():
    """The corpus above is the breakage someone thought of. This is the rest:
    a good stylesheet with pieces inserted, cut out and moved, from a fixed
    seed. Whatever comes of each, the audit holds, nothing is counted as a bug,
    only the file host is asked for files, and the filled document still has
    one ``<style>`` and no way out of it."""
    rng = random.Random(20261006)
    bits = ["{", "}", "(", ")", "[", "]", ";", ":", ",", "'", '"', "\\", "/*", "*/", "<", ">",
            "</style>", "<!--", "-->", "@import ", "@font-face", "url(", "U+", "?", "\n", " ",
            "url(https://evil.test/x.woff2)", "!important", "\\72 ", "\\0 ", "format(", "src:",
            "font-family:", "expression(", "e9", "%", "deg", "-", clean.FONT_CSS_MARK,
            "../fonts/" + "a" * 20 + ".woff2", G + "q.woff2", chr(0), chr(0xFEFF)]
    good = (face("latin", G + "a.woff2", weight="100 900", stretch="100%")
            + face("latin-ext", G + "b.woff2", style="italic") + face("cyrillic", G + "c.woff2"))
    files = {G + "a.woff2": A, G + "b.woff2": B, G + "c.woff2": C, G + "q.woff2": woff2("q")}
    shell = clean.clean("<p>x</p>").html
    _degrade.reset()
    for _ in range(400):
        text = good
        for _ in range(rng.randint(1, 6)):
            at, how = rng.randrange(len(text)), rng.random()
            if how < 0.5:
                text = text[:at] + rng.choice(bits) + text[at:]
            elif how < 0.8:
                text = text[:at] + text[at + rng.randint(1, 12):]
            else:
                end = rng.randrange(at, len(text))
                text = text[:at] + text[end:] + text[at:end]
        web = Web({LINK: text.encode("utf-8"), **files})
        result = audit(fonts.localize([LINK], fetch=web))
        assert web.urls[0] == LINK
        for url in web.urls[1:]:
            parts = urlsplit(url)
            assert (parts.scheme, parts.netloc) == ("https", fonts.FILE_HOST), url
            assert url.endswith(".woff2") and not set(url) & set("?#%@\\ "), url
        page = fonts.apply(shell, result)
        assert page.count("<style>") == 1 and page.count("</style>") == 1, text
    assert _degrade.counts() == {}


def test_a_byte_order_mark_does_not_cost_the_first_block():
    """Left in, the mark is a character CSS reads as the start of a name: the
    first block would be swallowed as the body of a rule called that."""
    web = Web({LINK: b"\xef\xbb\xbf" + sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    assert audit(fonts.localize([LINK], fetch=web)).css == rule(A)


def test_windows_line_endings_change_nothing():
    web = Web({LINK: sheet(face("latin", G + "a.woff2")).replace(b"\n", b"\r\n"),
               G + "a.woff2": A})
    assert audit(fonts.localize([LINK], fetch=web)).css == rule(A)


def test_a_deeply_nested_stylesheet_costs_that_stylesheet_only():
    web = Web({LINK: sheet("/* latin */ @font-face {" + "{[(" * 30000),
               LINK_2: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    result = audit(fonts.localize([LINK, LINK_2], fetch=web))
    assert result.files == {name_of(A): A}


# ── failure ──────────────────────────────────────────────────────────────────

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
    requests.exceptions.SSLError(), urllib3.exceptions.ProtocolError("x")]
# What a network does not do: a fault in whatever did the fetching. The
# operator's sentence is the same - the stylesheet was not fetched - and each
# one IS counted. (``MemoryError`` is in neither list: it is not swallowed at
# all, see ``test_what_must_stop_the_process_is_never_swallowed``.)
FAULTS = [ValueError("bad"), Loud(), RuntimeError("x"), KeyError("y"), RecursionError(),
          UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"), ZeroDivisionError(), TypeError(),
          AttributeError("x")]
FAILURES = NETWORK_FAILURES + FAULTS


def counted(failure, times=1) -> dict:
    """What ``_degrade.counts()`` should read after ``failure`` happened
    ``times`` times."""
    return {"blog.fonts": times} if any(failure is fault for fault in FAULTS) else {}


@pytest.mark.parametrize("failure", FAILURES, ids=lambda exc: type(exc).__name__)
def test_a_failed_fetch_never_raises_and_says_so(failure):
    """The stylesheet cannot be had: no typefaces, one plain sentence, and
    nothing of the exception in it."""
    web = Web({LINK: failure})
    _degrade.reset()
    result = audit(fonts.localize([LINK], fetch=web))
    assert result == fonts.Fonts("", {}, "No typefaces were copied, so the entry is shown in "
                                 "its fallback fonts: 1 stylesheet could not be fetched.")
    assert _degrade.counts() == counted(failure)
    _degrade.reset()


@pytest.mark.parametrize("answer", [
    None, 7, "text, not bytes", [b"x"], object(), b"\xff\xfe\x00 not utf-8",
    b"<!doctype html><html><body><h1>Error 400</h1></body></html>", b"", b"   ",
    b"{}{}{}", b"@font-face", b"\x00" * 100,
], ids=repr)
def test_a_stylesheet_that_is_not_one_never_raises(answer):
    web = Web({LINK: answer, LINK_2: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    result = audit(fonts.localize([LINK, LINK_2], fetch=web))
    assert result.css == rule(A)
    assert result.note.startswith("Some typefaces were not copied")
    assert "1 stylesheet" in result.note


@pytest.mark.parametrize("failure", FAILURES, ids=lambda exc: type(exc).__name__)
def test_a_failure_part_way_keeps_what_was_already_copied(failure):
    """The second link fails and so does the third file: the first link's
    faces and the two files already copied are the result."""
    css = sheet(face("latin", G + "a.woff2"), face("latin", G + "b.woff2", weight="500"),
                face("latin", G + "c.woff2", weight="600"),
                face("latin", G + "d.woff2", weight="700"))
    web = Web({LINK: css, LINK_2: failure, G + "a.woff2": A, G + "b.woff2": B,
               G + "c.woff2": failure, G + "d.woff2": C})
    _degrade.reset()
    result = audit(fonts.localize([LINK, LINK_2], fetch=web))
    assert result.css == "\n".join([rule(A), rule(B, weight="500"), rule(C, weight="700")])
    assert result.note == ("Some typefaces were not copied and are shown in a fallback font: "
                           "1 stylesheet could not be fetched, "
                           "1 file could not be fetched or was not a typeface.")
    assert _degrade.counts() == counted(failure, times=2)
    _degrade.reset()


def test_the_note_never_carries_what_the_other_end_said():
    """Not the exception, not an address, not a family name: counts, and this
    module's own words."""
    css = sheet(face("latin", G + "a.woff2", family="'Evil Family'"),
                face("latin", "https://evil.test/secret.woff2", family="'Evil Two'"))
    web = Web({LINK: css, LINK_2: Loud(), G + "a.woff2": Loud()})
    result = audit(fonts.localize(
        [LINK, LINK_2, "https://evil.test/css2?family=Secret"], fetch=web, cfg={"max_files": 1}))
    for needle in ("SECRET", "secret", "evil", "Evil", "token", "http", "script", "Loud",
                   "family", "Inter", "?", "<", "/"):
        assert needle not in result.note, needle
    assert result.note.count(".") == 1 and result.note.endswith(".")


def test_a_fetch_that_takes_no_keywords_fails_like_any_other():
    def positional_only(url):               # a fake written against the wrong signature
        return b""
    result = fonts.localize([LINK], fetch=positional_only)
    assert result.css == "" and "1 stylesheet could not be fetched" in result.note
    assert fonts.localize([LINK], fetch=None).css == ""
    assert fonts.localize([LINK], fetch="not callable").files == {}


@pytest.mark.parametrize("links", [7, object(), {"a": 1}, b"bytes", [[LINK]], [None, 7],
                                   iter([LINK])], ids=repr)
def test_links_that_are_not_a_list_of_text_never_raise(links):
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    result = audit(fonts.localize(links, fetch=web))
    assert web.calls == [] and result.css == "" and result.note


def test_one_link_given_as_text_is_one_link_not_its_characters():
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    assert audit(fonts.localize(LINK, fetch=web)).css == rule(A)


@pytest.mark.parametrize("cfg", [
    {"max_files": 0}, {"max_files": 10 ** 9}, {"max_files": "many"}, {"max_files": True},
    {"max_links": -1}, {"max_css_kb": None}, {"max_file_kb": float("nan")},
    {"timeout_sec": float("inf")}, {"total_sec": [30]}, {"subsets": "latin"},
    {"subsets": []}, {"subsets": [7, None]}, {"subsets": None}, {"unknown": 1},
    {"enabled": "yes"}, {"enabled": None}, "not a dict", 7, [],
], ids=repr)
def test_a_setting_that_is_not_usable_reads_as_the_shipped_one(cfg):
    """``cfg`` is laid over the shipped settings and held to the same bounds,
    so a caller cannot switch a limit off by handing in a bad one."""
    files = {G + f"f{n}.woff2": woff2(f"f{n}") for n in range(30)}
    css = sheet(*[face("latin", url, weight=str(100 + n)) for n, url in enumerate(files)])
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg=cfg))
    shipped = blog_inbox.fonts()
    assert len(result.files) == shipped["max_files"] == 24
    assert web.calls[0]["max_bytes"] == shipped["max_css_kb"] * 1024
    assert all(call["timeout"] == shipped["timeout_sec"] for call in web.calls)


def test_the_whole_copy_has_a_time_limit():
    """``timeout_sec`` bounds one request. Twenty-four of them timing out one
    after another would hold the service for minutes, so all of an entry's
    requests share ``total_sec`` - and a request is never given longer than
    what is left of it."""
    now = [100.0]
    files = {G + f"f{n}.woff2": woff2(f"f{n}") for n in range(5)}
    web = Web({LINK: sheet(*[face("latin", url, weight=str(100 + n))
                             for n, url in enumerate(files)]), **files})

    def slow(url, **kwargs):
        answer = web(url, **kwargs)
        now[0] += 4                          # every request takes four seconds
        return answer

    result = audit(fonts.localize([LINK], fetch=slow, clock=lambda: now[0],
                                  cfg={"timeout_sec": 5, "total_sec": 10}))
    assert [call["timeout"] for call in web.calls] == [5, 5, 2]
    assert len(result.files) == 2
    assert result.note == ("Some typefaces were not copied and are shown in a fallback "
                           "font: the time allowed for copying ran out.")


def test_a_clock_that_fails_never_raises():
    def broken():
        raise OSError("no clock")
    result = fonts.localize([LINK], fetch=Web({}), clock=broken)
    assert result.css == "" and result.files == {} and result.note


def test_a_bug_in_here_is_counted_and_never_raised(monkeypatch):
    """A crash must not look like a slow network: it is counted for /health."""
    def boom():
        raise RuntimeError("SECRET")
    monkeypatch.setattr(blog_inbox, "fonts", boom)
    _degrade.reset()
    result = fonts.localize([LINK], fetch=Web({}))
    assert result == fonts.Fonts("", {}, "No typefaces were copied, so the entry is shown in "
                                 "its fallback fonts: the Blog service hit an error.")
    assert _degrade.counts() == {"blog.fonts": 1}
    _degrade.reset()


def test_the_same_links_and_bytes_give_the_same_result_byte_for_byte():
    css = sheet(face("latin-ext", G + "e.woff2", weight="100 900", stretch="100%"),
                face("latin", G + "l.woff2", weight="100 900", stretch="100%"),
                face("latin", G + "i.woff2", style="italic"))
    css_2 = sheet(face("latin", G + "n.woff2", family="'Newsreader'"))
    pages = {LINK: css, LINK_2: css_2, G + "e.woff2": A, G + "l.woff2": B, G + "i.woff2": C,
             G + "n.woff2": woff2("n")}
    first = audit(fonts.localize([LINK, LINK_2], fetch=Web(pages)))
    again = audit(fonts.localize((LINK, LINK_2), fetch=Web(dict(reversed(list(pages.items()))))))
    assert first == again and list(first.files) == list(again.files)
    assert first.css.encode("utf-8") == again.css.encode("utf-8")
    assert first.css.isascii()
    # The order is the links' and then the stylesheet's own.
    other = audit(fonts.localize([LINK_2, LINK], fetch=Web(pages)))
    assert other.css.split("\n") == first.css.split("\n")[3:] + first.css.split("\n")[:3]


# ── apply ────────────────────────────────────────────────────────────────────

def _some_fonts():
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    return audit(fonts.localize([LINK], fetch=web))


def test_apply_fills_the_marked_style_and_nothing_else():
    cleaned = clean.clean("<html><head><style>p{color:red}</style></head>"
                          "<body><h1>T</h1><p>the mark is " + clean.FONT_CSS_MARK
                          + " in words</p></body></html>")
    result = _some_fonts()
    page = fonts.apply(cleaned.html, result)
    before, mark, after = cleaned.html.partition(clean.FONT_CSS_MARK)
    assert mark and page == before + result.css + after
    assert page.count(result.css) == 1 and "p{color:red}" in page
    assert fonts.apply(page, result) == page                # nothing left to fill


def test_apply_with_no_typefaces_leaves_an_empty_style():
    cleaned = clean.clean("<p>x</p>")
    page = fonts.apply(cleaned.html, fonts.Fonts("", {}, "a note"))
    assert page == cleaned.html.replace(clean.FONT_CSS_MARK, "")
    assert "<style></style>" in page


@pytest.mark.parametrize("document", [
    "<!doctype html><html><head><title>t</title></head><body><p>no mark</p></body></html>",
    "<style>" + clean.FONT_CSS_MARK + "</style><style>" + clean.FONT_CSS_MARK + "</style>",
    "<style>" + clean.FONT_CSS_MARK + "</style><p>" + clean.FONT_CSS_MARK + "</p>",
    "<p>" + clean.FONT_CSS_MARK + "</p>",                   # once, but not as a <style>'s text
    "<style>p{}" + clean.FONT_CSS_MARK + "</style>",        # once, but not its WHOLE text
    "<style media=print>" + clean.FONT_CSS_MARK + "</style>",
    "", clean.FONT_CSS_MARK,
], ids=repr)
def test_apply_does_not_guess_where_the_rules_go(document):
    """The mark is in a cleaned document exactly once, as the whole text of a
    ``<style>``. Anything else is not a cleaned document, and comes back as it
    went in."""
    assert fonts.apply(document, _some_fonts()) == document


@pytest.mark.parametrize("document", [None, 7, b"<style>/*blog-fonts*/</style>", ["x"]],
                         ids=repr)
def test_apply_hands_back_what_is_not_text(document):
    assert fonts.apply(document, _some_fonts()) is document


@pytest.mark.parametrize("css", [
    "</style><script>alert(1)</script>",
    "@import url(https://evil.test/x.css);",
    "body{background:url(https://evil.test/t.gif)}",
    '@font-face{font-family:"A";src:url(https://fonts.gstatic.com/s/a.woff2) format("woff2")}',
    '@font-face{font-family:"A";src:url(../fonts/' + "0" * 20 + '.woff2) format("woff2")}'
    "p{color:red}",
    '@font-face{font-family:"A";src:url(../fonts/../../x.woff2) format("woff2")}',
    '@font-face{font-family:"A<";src:url(../fonts/' + "0" * 20 + '.woff2) format("woff2")}',
    '@font-face{font-family:"A";src:url(../fonts/' + "0" * 20 + '.woff2) format("woff2")}\n\n',
    '@font-face{font-family:"A";src:url(../fonts/' + "0" * 20 + '.woff2) format("woff2");'
    "unicode-range:U+0-7F;behavior:url(x)}",
    7, None, b"bytes",
], ids=repr)
def test_apply_writes_only_rules_this_module_could_have_written(css):
    """``Fonts`` is a plain record: anything can build one. Nothing cleans the
    document after ``apply``, so ``apply`` checks the rules itself - and what
    fails goes in as nothing, counted, rather than as itself."""
    cleaned = clean.clean("<p>x</p>")
    _degrade.reset()
    page = fonts.apply(cleaned.html, fonts.Fonts(css, {}, ""))
    assert page == cleaned.html.replace(clean.FONT_CSS_MARK, "")
    assert _degrade.counts() == {"blog.fonts": 1}
    _degrade.reset()
    assert fonts.apply(cleaned.html, "not a Fonts") == page
    assert fonts.apply(cleaned.html, None) == page


def test_a_filled_document_is_still_a_clean_one():
    """The mark is gone, so cleaning the filled document again must not find
    one - and must throw the @font-face rules away, which is why NOTHING may
    clean a document after ``apply``. Pinned so that rule is not learned twice."""
    result = _some_fonts()
    page = fonts.apply(clean.clean("<h1>T</h1><p>x</p>").html, result)
    again = clean.clean(page)
    assert "@font-face" in page and "@font-face" not in again.html
    assert again.html.count(clean.FONT_CSS_MARK) == 1


# ── http_fetch ───────────────────────────────────────────────────────────────

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
    """Stands where ``requests.get`` is. No test in this file may reach the
    real one."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _fetch(monkeypatch, response, url=G + "a.woff2", max_bytes=1000):
    fake = Requests(response)
    monkeypatch.setattr(fonts.requests, "get", fake.get)
    try:
        return fake, fonts.http_fetch(url, headers={"User-Agent": fonts.USER_AGENT},
                                      timeout=7, max_bytes=max_bytes)
    except Exception as exc:
        return fake, exc


def test_http_fetch_asks_once_follows_nothing_and_streams(monkeypatch):
    response = Response(chunks=(b"wOF2", b"", b"rest"))
    fake, body = _fetch(monkeypatch, response)
    assert body == b"wOF2rest" and type(body) is bytes
    ((url, kwargs),) = fake.calls
    assert url == G + "a.woff2"
    assert kwargs == {"headers": {"User-Agent": fonts.USER_AGENT}, "timeout": 7,
                      "allow_redirects": False, "stream": True}
    assert response.closed


REFUSED_ADDRESSES = [
    "http://fonts.gstatic.com/s/a.woff2", "ftp://fonts.gstatic.com/s/a.woff2",
    "HTTPS://fonts.gstatic.com/s/a.woff2", "https:fonts.gstatic.com/s/a.woff2",
    "https:/fonts.gstatic.com/s/a.woff2", "https:///fonts.gstatic.com/s/a.woff2",
    "//fonts.gstatic.com/s/a.woff2", "fonts.gstatic.com/s/a.woff2",
    "https://evil.test/a.woff2", "https://gstatic.com/a.woff2",
    "https://www.fonts.gstatic.com/a.woff2",
    "https://fonts.gstatic.com.evil.test/a.woff2", "https://fonts.gstatic.com@evil.test/a.woff2",
    "https://evil.test@fonts.gstatic.com/a.woff2", "https://user:pw@fonts.gstatic.com/a.woff2",
    "https://@fonts.gstatic.com/a.woff2",
    "https://fonts.gstatic.com:443/a.woff2", "https://fonts.gstatic.com:8443/a.woff2",
    "https://fonts.gstatic.com:/a.woff2",
    "https://fonts.gstatic.com./a.woff2", "https://FONTS.GSTATIC.COM/a.woff2",
    "https://Fonts.Gstatic.Com/a.woff2",
    "https://142.250.72.14/a.woff2", "https://[::1]/a.woff2", "https://[fonts.gstatic.com]/a",
    "https://2398766094/a.woff2", "https://0x8efa480e/a.woff2", "https://localhost/a.woff2",
    "https://fonts.gstatic.com\\@evil.test/a.woff2", "https://evil.test\\fonts.gstatic.com/a",
    "https://fonts.gstatic.com\t/a.woff2", "https://fonts.gstatic.com /a.woff2",
    "https://fonts.gstatic.com/a.woff2\n", " https://fonts.gstatic.com/a.woff2",
    "https://fonts.gstatic.com/a b.woff2", "https://fonts.gstatic.com/a.woff2\x00",
    "https://fonts.gstatic" + ONE_DOT_LEADER + "com/a.woff2",
    "https://fonts.gst" + CYRILLIC_A + "tic.com/a.woff2",
    "https://fonts.gstatic.com/caf" + E_ACUTE + ".woff2",
    "https://xn--fonts-gstatic.com/a.woff2",
    "", None, 7, b"https://fonts.gstatic.com/a.woff2", ["https://fonts.gstatic.com/a.woff2"],
]


@pytest.mark.parametrize("url", REFUSED_ADDRESSES, ids=repr)
def test_http_fetch_refuses_an_address_before_it_connects(monkeypatch, url):
    """https, and a host that is EXACTLY one of the two: no userinfo, no port,
    no trailing dot, no other case, no number, nothing that is not ASCII. The
    refusal is made on the text - ``requests`` is never called."""
    fake, outcome = _fetch(monkeypatch, Response(), url=url)
    assert isinstance(outcome, fonts.FetchError), outcome
    assert fake.calls == []


@pytest.mark.parametrize("url", [
    "https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,400;1,700&display=swap",
    "https://fonts.gstatic.com/s/inter/v13/UcC73FwrK3iLTeHuS_fvQtMwCp50KnMa1ZL7W0Q5nw.woff2",
])
def test_http_fetch_takes_both_of_googles_hosts(monkeypatch, url):
    fake, body = _fetch(monkeypatch, Response(), url=url)
    assert body == b"body" and fake.calls[0][0] == url


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 204, 206, 304, 400, 403, 404,
                                    429, 500, 503, 0, None, "200"])
def test_http_fetch_refuses_another_host_and_a_redirect(monkeypatch, status):
    """A redirect is an address this module did not check, so none is followed:
    ``allow_redirects=False`` and then anything but a plain 200 is a failure -
    whatever the body, which is never read."""
    response = Response(status=status, headers={"Location": "https://evil.test/a.woff2"},
                        chunks=(woff2("x"),))
    fake, outcome = _fetch(monkeypatch, response)
    assert isinstance(outcome, fonts.FetchError)
    assert fake.calls[0][1]["allow_redirects"] is False
    assert response.read == 0 and response.closed
    # ... and the other host, as the plan's test names it.
    fake, outcome = _fetch(monkeypatch, Response(), url="https://evil.test/a.woff2")
    assert isinstance(outcome, fonts.FetchError) and fake.calls == []


def test_http_fetch_stops_reading_at_the_size_limit(monkeypatch):
    """Counted as the body arrives, because Content-Length is the other end's
    word: absent, wrong or a lie. An endless body costs one chunk past the cap."""
    def endless():
        while True:
            yield b"x" * 400

    response = Response(chunks=endless())
    _fake, outcome = _fetch(monkeypatch, response, max_bytes=1000)
    assert isinstance(outcome, fonts.FetchError)
    assert response.read == 1200 and response.closed

    response = Response(chunks=(b"x" * 600, b"x" * 400))
    _fake, body = _fetch(monkeypatch, response, max_bytes=1000)
    assert body == b"x" * 1000                              # exactly at the limit is inside it

    lying = Response(chunks=(b"x" * 600, b"x" * 401), headers={"Content-Length": "10"})
    _fake, outcome = _fetch(monkeypatch, lying, max_bytes=1000)
    assert isinstance(outcome, fonts.FetchError) and lying.closed


def test_http_fetch_refuses_a_declared_size_over_the_limit_unread(monkeypatch):
    response = Response(chunks=(b"x",), headers={"Content-Length": "1001"})
    _fake, outcome = _fetch(monkeypatch, response, max_bytes=1000)
    assert isinstance(outcome, fonts.FetchError)
    assert response.read == 0 and response.closed
    for odd in ("", "abc", "-1", "1e3", None):              # an odd header is just not a promise
        _fake, body = _fetch(monkeypatch, Response(headers={"Content-Length": odd}))
        assert body == b"body"


@pytest.mark.parametrize("max_bytes", [0, -1, None, "1000", float("nan"), True])
def test_http_fetch_needs_a_real_size_limit(monkeypatch, max_bytes):
    fake, outcome = _fetch(monkeypatch, Response(), max_bytes=max_bytes)
    assert isinstance(outcome, fonts.FetchError) and fake.calls == []


@pytest.mark.parametrize("timeout", [0, -1, None, "7", float("nan"), float("inf"), True])
def test_http_fetch_needs_a_real_time_limit(monkeypatch, timeout):
    """``timeout=None`` is how ``requests`` spells "wait for ever"."""
    fake = Requests(Response())
    monkeypatch.setattr(fonts.requests, "get", fake.get)
    with pytest.raises(fonts.FetchError):
        fonts.http_fetch(G + "a.woff2", headers={}, timeout=timeout, max_bytes=1000)
    assert fake.calls == []


def test_http_fetch_sends_a_copy_of_the_headers_it_was_given(monkeypatch):
    given = {"User-Agent": fonts.USER_AGENT}
    fake = Requests(Response())
    monkeypatch.setattr(fonts.requests, "get", fake.get)
    fonts.http_fetch(G + "a.woff2", headers=given, timeout=7, max_bytes=1000)
    sent = fake.calls[0][1]["headers"]
    assert sent == given and sent is not given
    for not_headers in (None, "User-Agent: x", [("User-Agent", "x")], 7):
        with pytest.raises(fonts.FetchError):
            fonts.http_fetch(G + "a.woff2", headers=not_headers, timeout=7, max_bytes=1000)
    assert len(fake.calls) == 1


def test_http_fetch_lets_a_network_failure_through_and_closes(monkeypatch):
    """``localize`` catches whatever this raises; what matters here is that the
    connection is given back whichever way the read ends."""
    _fake, outcome = _fetch(monkeypatch, ConnectionError("refused"))
    assert isinstance(outcome, ConnectionError)
    response = Response(chunks=(b"half",), error=OSError("reset"))
    _fake, outcome = _fetch(monkeypatch, response)
    assert isinstance(outcome, OSError) and response.closed


def test_http_fetch_gives_one_request_no_longer_than_its_timeout(monkeypatch):
    """``requests``' timeout bounds each read, not the request: a body arriving
    a chunk at a time, each just inside it, would otherwise never end."""
    now = [0.0]

    def dripping():
        while True:
            now[0] += 3
            yield b"x"

    monkeypatch.setattr(fonts, "monotonic", lambda: now[0])
    response = Response(chunks=dripping())
    _fake, outcome = _fetch(monkeypatch, response, max_bytes=10 ** 6)
    assert isinstance(outcome, fonts.FetchError)
    assert response.read == 3 and response.closed           # 3 s, 6 s, 9 s: past 7 on the third


def test_localize_through_the_real_fetch_end_to_end(monkeypatch):
    """The two halves together, with only ``requests.get`` replaced: the
    default ``fetch`` really is called with what ``localize`` builds."""
    pages = {LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A}
    asked = []

    def get(url, **kwargs):
        asked.append((url, kwargs))
        return Response(chunks=(pages[url],))

    monkeypatch.setattr(fonts.requests, "get", get)
    result = audit(fonts.localize([LINK]))
    assert result.css == rule(A) and result.note == ""
    assert [url for url, _kw in asked] == [LINK, G + "a.woff2"]
    for _url, kwargs in asked:
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert kwargs["headers"] == {"User-Agent": fonts.USER_AGENT}
