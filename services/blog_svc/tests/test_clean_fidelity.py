"""The cleaner must not quietly change what an honest entry means.

``test_clean.py`` is about what must NOT survive. This file is the other half,
written after a review found the cleaner safe but lossy in ways no test saw: a
``<`` taken out of a media query, a stylesheet dropped over the name of a class,
a deep document cut short without a word, a link losing its address over a
space. Each test here is one of those findings, and the rule they share is that
a change the cleaner makes is either right, or counted, or a refusal - never
silent.

Characters that are invisible or easy to mistype are built from their code
points (``chr(0x200B)``), never written as an escape or a literal.
"""
import html as html_lib
import re

import pytest
import tinycss2

from services.blog_svc import clean
from services.blog_svc.tests._audit import EMPTY_SHELL, FIXTURE, audit, cleaned, page

ZERO_WIDTH_SPACE = chr(0x200B)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
NO_BREAK_SPACE = chr(0xA0)
for _ch in (ZERO_WIDTH_SPACE, RIGHT_TO_LEFT_OVERRIDE, NO_BREAK_SPACE):
    assert len(_ch) == 1


def sheets(document):
    """The text of every stylesheet in a cleaned document, the font mark's
    excepted. Exact, because no stylesheet the cleaner writes can hold ``</``."""
    return re.findall(r"<style>(.*?)</style>", document, flags=re.S)[1:]


def tokens(css):
    """Every token of ``css``, the ones inside blocks and functions included."""
    found, todo = [], list(reversed(tinycss2.parse_component_value_list(css)))
    while todo:
        node = todo.pop()
        found.append(node)
        todo.extend(reversed(getattr(node, "content", None) or getattr(node, "arguments", None) or []))
    return found


def refused(result) -> bool:
    return result.removed == {"unparseable": 1}


# ── CSS: a "<" is a comparison ────────────────────────────────────────────────

RANGE_QUERIES = [
    ("@media (width < 600px){p{color:red}}", "@media (width < 600px){p{color:red}}"),
    ("@media (width <= 600px){p{color:red}}", "@media (width <= 600px){p{color:red}}"),
    ("@media (400px <= width <= 700px){p{color:red}}", "@media (400px <= width <= 700px){p{color:red}}"),
    ("@container (inline-size < 400px){p{color:red}}", "@container (inline-size < 400px){p{color:red}}"),
    ("@container card (400px < width){p{color:red}}", "@container card (400px < width){p{color:red}}"),
    # written tight: a space is put after the "<" (never between "<" and "=")
    ("@media (width<600px) and (height>=2px){p{color:red}}",
     "@media (width< 600px) and (height>=2px){p{color:red}}"),
    ("@media (400px<width<=700px){p{color:red}}", "@media (400px< width<=700px){p{color:red}}"),
]


@pytest.mark.parametrize("css,written", RANGE_QUERIES)
def test_a_range_query_keeps_its_comparison(css, written):
    """``(width < 600px)`` with the ``<`` taken out is not a narrower query, it
    is an invalid one: the rules inside stop applying, on exactly the screens
    the desktop preview does not show."""
    c = cleaned(page(f"<style>{css}</style>"))
    assert f"<style>{written}</style>" in c.html
    assert c.removed == {}

    def comparisons(text):
        return [n.value for n in tokens(text) if n.type == "literal" and n.value in ("<", ">", "=")]
    assert comparisons(sheets(c.html)[0]) == comparisons(css) != []


ANGLE_BRACKETS = [
    "p{content:'</b>'}", "a</b{color:red}", "p{x:<!important}", "p{x:<!--y}",
    "@media (a</**/b){p{x:y}}", "p{x:a</ b}", "p{x:<\\/b}", "p{x:<\\!b}", ".a\\<\\/b{x:y}",
    ".a\\<\\!--b{x:y}", "p{x:'<!--'}", "p{x:<<<///!!!}", "p{x:<}", "<", "<<", "p{x:< /b}",
    "p{x:url(</b)}", "p{x:f(<)/y}", "p{x:[<]!y}", "#a\\<\\/b{x:y}", "p{x:1\\<\\/b}",
    "p{x:<=/b}", "p{x:<\n/b}", "p{x:</**/!y}", "@media (width</**/=1px){p{x:y}}",
]


@pytest.mark.parametrize("css", ANGLE_BRACKETS)
def test_no_stylesheet_can_close_its_own_element(css):
    """A stylesheet is raw text: the only thing that ends it is ``</``. So a
    ``<`` is kept only where neither ``/`` nor ``!`` can come next."""
    attribute = html_lib.escape(css, quote=True)
    c = cleaned(page(f"<style>{css}</style><style>q{{a:b}}{css}</style><p style=\"{attribute}\">t</p>"))
    for sheet in sheets(c.html):
        assert "</" not in sheet and "<!" not in sheet, sheet


# ── CSS: a class named after a language is a class ────────────────────────────

@pytest.mark.parametrize("css", [
    'pre code.language-javascript::before{content:"JS"}h1{color:red}',
    ".javascript:hover{color:red}h1{color:blue}",
    "pre.vbscript:first-child{color:blue}h2{margin:0}",
    'a[href^="javascript:"]{outline:2px solid red}h3{margin:0}',
])
def test_a_selector_that_names_a_script_language_is_only_a_selector(css):
    c = cleaned(page(f"<style>{css}</style>"))
    assert f"<style>{css}</style>" in c.html and c.removed == {}


# ── CSS: a property is dropped as a declaration, whole ────────────────────────

@pytest.mark.parametrize("css", [
    "p{color:red}.behavior:hover{color:red}h1{color:blue}",
    "#behavior:target{color:red}h1{color:blue}",
    "a.-moz-binding:hover{color:red}h1{color:blue}",
    ".a{color:red;.behavior:hover{color:green}}h1{color:blue}",
    "@media screen{.behavior:hover{color:red}}h1{color:blue}",
    "@supports (behavior:x){p{color:red}}h1{color:blue}",
    "p{scroll-behavior:smooth;transition-behavior:allow-discrete}",
])
def test_a_property_name_in_a_selector_is_left_alone(css):
    """The dropped rule used to leave its ``.`` behind, which then attached
    itself to the NEXT rule: ``.behavior:hover{}h1{}`` came out as ``.h1{}``."""
    c = cleaned(page(f"<style>{css}</style>"))
    assert f"<style>{css}</style>" in c.html and c.removed == {}


@pytest.mark.parametrize("css,written", [
    ("p{*behavior:url(x.htc);color:blue}", "p{color:blue}"),
    ("p{color:blue;*behavior:url(x.htc)}", "p{color:blue;}"),
    ("p{ _behavior : url(x.htc) ; color:blue}", "p{ color:blue}"),
    ("p{+behavior:url(x.htc);color:blue}", "p{color:blue}"),
    ("p{#behavior:url(x.htc);color:blue}", "p{color:blue}"),
    ("p{-moz-binding:url(x.xml#a);color:blue}", "p{color:blue}"),
    ("p{color:blue;-MS-Behavior:url(x.htc);margin:0}", "p{color:blue;margin:0}"),
    ("@media print{p{*behavior:url(x.htc);color:blue}}", "@media print{p{color:blue}}"),
])
def test_a_dropped_declaration_goes_whole_hack_prefix_and_all(css, written):
    """``*behavior:url(x)`` is the same declaration to the browser it was
    written for. Dropping from the name left ``*`` behind, glued to the next
    declaration: ``p{*color:blue}``."""
    c = cleaned(page(f"<style>{css}</style>"))
    assert f"<style>{written}</style>" in c.html and c.removed == {"css": 1}
    c = cleaned(page('<p style="*behavior:url(x.htc);color:blue">t</p>'))
    assert '<p style="color:blue">t</p>' in c.html and c.removed == {"css": 1}


# ── CSS: every change is counted ──────────────────────────────────────────────

@pytest.mark.parametrize("css,count", [
    ("p{color:red}", 0),
    ("/* a comment is not the entry's meaning */p{color:red}", 0),
    ("p{a:url(//e.com/x)}", 1),
    ("p{a:url(//e.com/x) url('//e.com/y')}", 2),
    ("p{behavior:url(x.htc)}", 1),
    ("@import 'x.css';p{a:b}", 1),
    ("@font-face{font-family:x;src:url(x.woff2)}p{a:b}", 1),
    ("@unknown-rule x{a:b}p{a:b}", 1),
    ("p{a:image-set('x.png' 1x)}", 1),
    ("p{a:expression(1)}", 1),
    ("p{a:texturl(x)}", 1),
    ("p{width:expr/**/ession(1)}", 1),
    ("<!-- p{a:b} -->", 2),
    ("p{a:b}}", 1),
    ("p{a:b)}", 1),
    ("p{a:'never closed\n;c:d}", 1),
    ("p{a:b\\\n}", 1),
    ("p{content:'see url(x) for more'}", 0),
    (".url\\(x\\){a:b}", 0),
    ("#expression\\(1\\){a:b}", 0),
])
def test_every_change_to_a_stylesheet_is_counted(css, count):
    c = cleaned(page(f"<style>{css}</style>"))
    assert c.removed.get("css", 0) == count, sheets(c.html)


def test_a_string_that_mentions_a_url_is_still_that_string():
    """Words in a string fetch nothing. They are kept, spelled so that the
    written text still has no ``url(`` or ``@import`` in it for the second look
    to trip on."""
    c = cleaned(page("<style>p::after{content:'see url(x) or @import, expression(1) for more'}"
                     ".\\@import, .url\\(x\\){color:red}</style>"))
    sheet = sheets(c.html)[0]
    assert [n.value for n in tokens(sheet) if n.type == "string"] == [
        "see url(x) or @import, expression(1) for more"]
    assert {"@import", "url(x)"} <= {n.value for n in tokens(sheet) if n.type == "ident"}
    assert 'content:"see url\\28 x) or \\40 import, expression\\28 1) for more"' in sheet
    assert ".\\@import, .url\\(x\\){color:red}" in sheet
    assert "url(" not in sheet and "expression(" not in sheet
    assert c.removed == {}


def test_the_second_look_costs_one_token_not_the_stylesheet():
    """The second look reads the written text with escapes taken out. A string
    or a function name that only reads as a fetch THERE is respelled or
    replaced by itself; the rules around it stay."""
    c = cleaned(page("<style>p{content:\"ur\\\\l(x) and \\\\@import\"}q{a:u\\.rl(x);b:c}h1{color:red}</style>"))
    sheet = sheets(c.html)[0]
    assert [n.value for n in tokens(sheet) if n.type == "string"] == ["ur\\l(x) and \\@import"]
    assert sheet.endswith("q{a:none;b:c}h1{color:red}")
    assert c.removed == {"css": 1}


# ── CSS: an escaped "<" in a name ─────────────────────────────────────────────

def test_an_escaped_angle_bracket_in_a_name_is_still_that_name():
    c = cleaned(page("<style>.a\\<b{color:red}#c\\<\\/d{color:blue}p{margin:1e\\<x}</style>"))
    sheet = sheets(c.html)[0]
    assert [n.value for n in tokens(sheet) if n.type == "ident"][:1] == ["a<b"]
    assert [n.value for n in tokens(sheet) if n.type == "hash"] == ["c</d"]
    assert "<" not in sheet and c.removed == {}


# ── CSS: at-rules that fetch nothing ──────────────────────────────────────────

@pytest.mark.parametrize("css", [
    '@counter-style thumbs{system:cyclic;symbols:"*";suffix:" "}',
    "@font-feature-values Font One{@styleset{nice-style:12}@swash{fancy:1}}",
    "@font-palette-values --warm{font-family:Bixa;base-palette:1;override-colors:0 red}",
    "@view-transition{navigation:auto}",
    "@position-try --above{position-area:top;width:100px}",
])
def test_an_at_rule_that_fetches_nothing_is_kept(css):
    c = cleaned(page(f"<style>{css}p{{color:red}}</style>"))
    assert f"<style>{css}p{{color:red}}</style>" in c.html and c.removed == {}


def test_a_counter_style_cannot_fetch_its_symbols():
    c = cleaned(page("<style>@counter-style x{system:cyclic;symbols:url(https://e.com/a.png)}</style>"))
    assert "<style>@counter-style x{system:cyclic;symbols:none}</style>" in c.html
    assert "e.com" not in c.html and c.removed == {"css": 1}


# ── a document the parser stops reading is refused ───────────────────────

def kept_or_refused(document, *words):
    """The only two honest outcomes for a document that strains the parser:
    everything it said is still there, or it is refused. True when kept."""
    c = clean.clean(document)
    audit(c.html)
    if refused(c):
        assert c.html == EMPTY_SHELL
        return False
    for word in words:
        assert word in c.body, f"{word} was lost without a refusal"
    return True


@pytest.mark.parametrize("depth", [10, 100, 250, 254, 255, 256, 257, 300, 2000, 50_000])
def test_a_document_too_deep_for_the_parser_is_refused_not_cut_short(depth):
    """libxml2 2.11 stops at 256 open elements and hands back what it had.
    Whatever the limit is on another version, the outcome may not be a document
    with its end missing and nothing said."""
    document = "<h1>T</h1>" + "<div>" * depth + "INNER" + "</div>" * depth + "<p>AFTER</p>"
    kept = kept_or_refused(document, "INNER", "AFTER")
    if depth <= 100:
        assert kept, "an ordinary depth must not be refused"


@pytest.mark.parametrize("ending", [
    "<p>x</p><script>never closed", "<p>x</p><style>p{color:red}", "<p>x</p><!-- never closed",
    "<p>x</p><a href='https://example.com/", "<p>x</p><p title=\"never closed", "<p>x</p><p class='a'",
    "<p>x</p><b", "<p>x</p><p>y</p><svg><rect width='1'",
])
def test_a_document_that_ends_inside_something_is_refused(ending):
    """The tail of such a document is not content to any parser: it is the
    inside of the thing left open. That is a file cut off mid-way."""
    c = clean.clean(ending)
    assert refused(c) and c.html == EMPTY_SHELL


@pytest.mark.parametrize("ending", [
    "<p>x</p>", "<p>x", "<div><p>x", "<svg><rect/>", "<table><tr><td>x", "<p>x</p>\n\n", "x <", "x &",
    "<p>x</p></body></html>", "<p>x</p></body></html>\n<!-- trailer -->\n", "<ul><li>x", "<pre>x\n",
    "<p>a < b", "<p>x</p></div></div>", "<details><summary>s</summary>x", "<h1>T</h1><p>x</p><hr>",
])
def test_a_document_that_merely_ends_is_not(ending):
    assert not refused(cleaned(ending))


def test_a_document_cannot_carry_the_end_marker_itself():
    """The marker's name is random per call. A document that guessed a fixed
    one could be cut short and still look finished."""
    names = set()
    real = clean.lxml.html.document_fromstring

    def spy(source, **kwargs):
        names.update(re.findall(rb"<(x-end-[0-9a-f]{16})>", source))
        return real(source, **kwargs)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(clean.lxml.html, "document_fromstring", spy)
        clean.clean("<p>one</p>")
        clean.clean("<p>two</p>")
    assert len(names) >= 3
    # ... so one the document brought along proves nothing: this is refused, or
    # whole, exactly as it would be without the borrowed marker
    kept_or_refused("<p>FIRST</p><x-end-0123456789abcdef>" + "<div>" * 300 + "INNER" + "</div>" * 300
                    + "<p>AFTER</p>", "FIRST", "INNER", "AFTER")


@pytest.mark.parametrize("ending,words", [
    ("<p>x</p>\n</body>\n</html>\n", ()),
    ("<p>x</p></BODY ></HTML >  ", ()),
    ("<p>x</p></body></html>\n<!-- generated in 3 ms -->\n<!-- and another -->\n", ()),
    ("<p>x</p><!-- a --> LAST --> ", ("LAST",)),
    ("<p>x</p><!--> LAST -->", ("LAST",)),
    ("<p>x</p></body></html></body></html>", ()),
    ("<html><head><title>t</title></head><body><p>x</p></body></html>LAST", ("LAST",)),
    ("<!doctype html><html><head><title>t</title></head><body><p>x</p></body></html><p>LAST</p>",
     ("LAST",)),
])
def test_the_end_marker_goes_before_the_closing_tags_and_after_everything_else(ending, words):
    """A document that finishes ``</body></html>`` and a comment is finished.
    One with CONTENT after ``</html>`` is kept only if the parser kept that
    content: libxml2 2.11 drops it when there is a doctype, and that is a
    refusal, not a document with its last paragraph gone."""
    kept = kept_or_refused(ending, "x", *words)
    if not words:
        assert kept, "a document that merely ends must be kept, not refused"


def test_the_parsers_stop_is_seen_behind_thousands_of_ordinary_errors():
    """A sloppy document logs an error per slip, and lxml keeps only the first
    hundred. The stop that matters is the LAST thing the parser says, so the
    log cannot be how it is noticed."""
    slips = "</i>" * 5_000
    deep = "<div>" * 300 + "INNER" + "</div>" * 300
    kept_or_refused("<p>FIRST</p>" + slips + deep + "<p>AFTER</p>", "FIRST", "INNER", "AFTER")
    kept_or_refused("<p>FIRST</p>" + deep + slips + "<p>AFTER</p>", "FIRST", "INNER", "AFTER")
    assert kept_or_refused("<p>FIRST</p>" + slips + "<p>AFTER</p>", "FIRST", "AFTER"), (
        "ordinary errors alone are not a reason to refuse")


# ── a tag with too many attributes never reaches the parser ──────────────

@pytest.fixture
def parses(monkeypatch):
    """How many times the parser was called. The proof that a refusal is cheap
    is that this stays at zero, not that a clock ran fast."""
    calls = []
    real = clean.lxml.html.document_fromstring

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)
    monkeypatch.setattr(clean.lxml.html, "document_fromstring", counted)
    return calls


CROWDED_TAGS = {
    "bare names": lambda n: "<p " + " ".join(f"a{i}" for i in range(n)) + ">t</p>",
    "data attributes": lambda n: "<p " + " ".join(f"data-a{i}=b" for i in range(n)) + ">t</p>",
    "double-quoted values": lambda n: "<p " + " ".join(f'a{i}="x y"' for i in range(n)) + ">t</p>",
    "single-quoted values": lambda n: "<p " + " ".join(f"a{i}='x>y'" for i in range(n)) + ">t</p>",
    "slashes between": lambda n: "<p/" + "/".join(f"a{i}" for i in range(n)) + ">t</p>",
    "newlines between": lambda n: "<P\n" + "\n".join(f"A{i}" for i in range(n)) + "\n>t</P>",
    "never closed": lambda n: "<p " + " ".join(f"a{i}" for i in range(n)),
    "inside a drawing": lambda n: "<svg><rect " + " ".join(f"a{i}=1" for i in range(n)) + "/></svg>",
    "after a comment": lambda n: "<!-- c --><p " + " ".join(f"a{i}" for i in range(n)) + ">t</p>",
    "a prefixed tag": lambda n: "<o:p " + " ".join(f"a{i}" for i in range(n)) + ">t</o:p>",
    # the scan-bypass shapes: on 2.11.9 the end tag / declaration / instruction
    # the crowded <style> or <script> hides behind runs only to its first ">",
    # so the parser builds the crowded tag that follows. A scan that took the
    # <style> for a real opener would skip to </style> and never see it.
    "a style behind an end tag": lambda n: (
        "<p>x</p></x <style><p " + " ".join(f"a{i}" for i in range(n)) + ">t</p></style>"),
    "a style behind a bang": lambda n: (
        "<p>x</p><!<style><p " + " ".join(f"a{i}" for i in range(n)) + ">t</p></style>"),
    "a style behind a doctype": lambda n: (
        "<!DOCTYPE <style><p " + " ".join(f"a{i}" for i in range(n)) + ">t</p></style>"),
    "a script behind an end tag": lambda n: (
        "<p>x</p></x <script><p " + " ".join(f"a{i}" for i in range(n)) + ">t</p></script>"),
}


@pytest.mark.parametrize("shape", sorted(CROWDED_TAGS))
def test_a_tag_with_too_many_attributes_never_reaches_the_parser(shape, parses):
    """libxml2 builds a tag's attributes in quadratic time: 85,000 distinct ones
    in one tag (they fit in the 512 KB limit) held the parser for over a minute.
    The count is taken first, in one pass over the text."""
    document = "<p>before</p>" + CROWDED_TAGS[shape](85_000)
    c = clean.clean(document)
    assert refused(c) and c.html == EMPTY_SHELL
    assert parses == []


def _tree_max(text):
    """The largest ``len(el.attrib)`` anywhere in the tree the parser builds -
    whatever parser that is. Nothing to parse means no tag, so zero."""
    parser = clean.lxml.html.HTMLParser(encoding="utf-8", recover=True)
    try:
        root = clean.lxml.html.document_fromstring(text.encode("utf-8", "replace"), parser=parser)
    except clean.etree.LxmlError:
        return 0
    return max((len(el.attrib) for el in root.iter() if isinstance(el.tag, str)), default=0)


def test_no_tag_the_parser_builds_in_a_kept_document_is_crowded():
    """The crowding guard exists to stop libxml2 building a tag with so many
    attributes that parsing it is a quadratic cost. What matters, then, and what
    this pins on WHATEVER libxml2 is installed, is the COST property: in any
    document the cleaner did NOT refuse, no tag the parser built has more than
    ``MAX_TAG_ATTRS`` attributes.

    (The scan does not try to match libxml2's attribute count tag-for-tag - a
    linear pre-scan cannot replicate a stateful tokenizer's handling of
    comments, foreign content and malformed end tags, and does not need to. It
    needs only to refuse before the parser runs when the parser would build a
    crowded tag, which the attack-scale shapes below exercise directly.)"""
    import random
    from services.blog_svc.tests.test_clean import _PIECES, _RAW_TEXT_PIECES
    documents = []
    rng = random.Random(4321)
    for vocab in (_PIECES, _RAW_TEXT_PIECES):
        for _ in range(1_000):
            documents.append("".join(rng.choice(vocab) for _ in range(rng.randint(1, 50))))
    for build in CROWDED_TAGS.values():
        for n in (3, 50, clean.MAX_TAG_ATTRS - 1, clean.MAX_TAG_ATTRS, clean.MAX_TAG_ATTRS + 1):
            documents.append(build(n))
    for document in documents:
        if "unparseable" in clean.clean(document).removed:
            continue                        # refused before the parser ran
        assert _tree_max(document) <= clean.MAX_TAG_ATTRS, repr(document[:120])


@pytest.mark.parametrize("hide", [
    "<x{{}}><p {attrs}>t</p>",                      # a crowded tag after a brace-abandoned "<"
    "</x <{opener}><p {attrs}>t</p>",               # behind a malformed end tag
    "<!DOCTYPE <{opener}><p {attrs}>t</p>",         # behind a doctype
    "<?pi <{opener}><p {attrs}>t</p>",              # behind a processing instruction
    "<svg><{opener}><p {attrs}>t</p></svg>",        # inside foreign content
    "<title><{opener}><p {attrs}></title>",         # inside a title
    "<math><script><p {attrs}>t</p></script></math>",   # in a script the parser DID build
    "</xmp>t<script><math><script><p {attrs}>t</p></script>",   # the exact nesting the review found
])
@pytest.mark.parametrize("opener", ["script", "style", "div"])
def test_a_crowded_tag_hidden_behind_a_quirk_is_still_refused(hide, opener):
    """Each of these put a crowded tag somewhere a naive scan might skip. At
    attack scale the only honest outcomes are a refusal, or a parse that built
    no crowded tag; never a crowded tag that slipped through to a quadratic
    parse."""
    attrs = " ".join(f"a{i}" for i in range(clean.MAX_TAG_ATTRS + 200))
    document = "<p>before</p>" + hide.format(opener=opener, attrs=attrs)
    result = clean.clean(document)
    assert "unparseable" in result.removed or _tree_max(document) <= clean.MAX_TAG_ATTRS


@pytest.mark.parametrize("shape", sorted(CROWDED_TAGS))
def test_the_attribute_limit_is_exact(shape, parses):
    assert refused(clean.clean(CROWDED_TAGS[shape](clean.MAX_TAG_ATTRS + 1))) and parses == []
    at_the_limit = clean.clean(CROWDED_TAGS[shape](clean.MAX_TAG_ATTRS))
    assert parses != [], "a tag at the limit is the parser's to read"
    # ... and it reads it, unless the tag is never closed: a document that ends
    # inside a tag is refused for that, however few attributes the tag has.
    assert refused(at_the_limit) == (shape == "never closed")


NOT_CROWDED = {
    "the fixture": lambda: FIXTURE.read_text(encoding="utf-8"),
    "a 60 KB path": lambda: ("<svg viewBox='0 0 9 9'><path d=\""
                             + " ".join(f"L{i} {i}" for i in range(7_000)) + "\"/></svg><p>END</p>"),
    "a 60 KB style attribute": lambda: ("<p style='" + ";".join(f"--v{i}: {i}px" for i in range(4_000))
                                        + "'>END</p>"),
    "prose with a stray less-than sign": lambda: ("<p>" + "when a < b and the rest follows " * 300
                                                  + "END</p>"),
    "prose that looks like a tag": lambda: "<p>suppose a <b and c> hold, then " + "word " * 3_000 + "END</p>",
    "a stylesheet with a tight comparison": lambda: (
        "<style>@media (400px<width){" + " ".join(f".c{i} {{ margin : {i}px }}" for i in range(2_000))
        + "}</style><p>END</p>"),
    "a script with a comparison in it": lambda: (
        "<script>if (a<b) { " + " ".join(f"x{i} = {i} ;" for i in range(2_000)) + " }</script><p>END</p>"),
    "many tags, few attributes each": lambda: "<p class='a' id='b' title='c'>x</p>" * 5_000 + "<p>END</p>",
    "an unquoted address": lambda: "<p><a href=https://example.com/a?b=1&c=2>x</a> END</p>",
    "a quote inside an unquoted value": lambda: "<p title=it's class=a\"b>" + "word " * 3_000 + "END</p>",
}


@pytest.mark.parametrize("name", sorted(NOT_CROWDED))
def test_an_honest_document_is_not_mistaken_for_a_crowded_tag(name):
    """A wrong refusal costs more than a missed attack here: the entry is the
    operator's own. Text inside quotes is ONE value, however many words."""
    c = clean.clean(NOT_CROWDED[name]())
    audit(c.html)
    assert not refused(c)
    assert "END" in c.body or name == "the fixture"


# ── typefaces asked for with @import, and what a font link may hold ─

FONT = "https://fonts.googleapis.com/css2?family="


def test_a_typeface_imported_by_a_stylesheet_is_offered_like_a_linked_one():
    c = cleaned(
        f"<link rel='stylesheet' href='{FONT}Lora'>"
        f"<style>@import url('{FONT}Inter:wght@400;700&display=swap');\n"
        f"@import \"{FONT}Newsreader:ital,opsz,wght@0,6..72,400\" screen;\n"
        f"@IMPORT URL({FONT}IBM+Plex+Mono);@import url({FONT}Lora);\n"
        f"@import url(https://e.com/x.css);p{{color:red}}</style><p>x</p>"
        f"<link rel='stylesheet' href='{FONT}Roboto'>")
    assert c.font_links == (FONT + "Lora", FONT + "Inter:wght@400;700&display=swap",
                            FONT + "Newsreader:ital,opsz,wght@0,6..72,400",
                            FONT + "IBM+Plex+Mono", FONT + "Roboto")
    # counted the way a font <link> is; the import that was not a typeface is CSS removed
    assert c.removed == {"link": 6, "css": 1}
    assert sheets(c.html) == ["p{color:red}"] and "googleapis" not in c.html and "e.com" not in c.html


@pytest.mark.parametrize("css", [
    f"@media screen{{@import url({FONT}Inter);}}",          # not where an @import is allowed
    f"p{{background:url({FONT}Inter)}}",                    # a url, not an import
    f"@import url(http://fonts.googleapis.com/css2?family=Inter);",
    f"@import url(//fonts.googleapis.com/css2?family=Inter);",
    f"@import url(https://fonts.googleapis.com.e.com/css2?family=Inter);",
    f"@import url('{FONT}Inter\"><script>');",
    f"@import '{FONT}Inter' url({FONT}Lora);",              # only the address the rule names
])
def test_only_a_real_import_of_a_real_font_stylesheet_is_offered(css):
    c = cleaned(page(f"<style>{css}</style><p style='@import url({FONT}Mono)'>t</p>"))
    assert c.font_links in ((), (FONT + "Inter",)) and "googleapis" not in c.html
    if "Lora" in css:
        assert c.font_links == (FONT + "Inter",)
    else:
        assert c.font_links == ()


@pytest.mark.parametrize("query", [
    "Inter\"><script>alert(1)</script>", "Inter'onload='x", "Inter<b", "Inter>", "Inter x",
    "Inter#frag", "Inter/../x", "Inter\\x", "Inter?x", "Inter|Lora", "Inter(x)", "Inter{x}",
    "Inter`x`", "Inter!", "Inter$", "Inter*", "Inter~", "Inter^", "Inter[0]",
    "Inter" + ZERO_WIDTH_SPACE, "Inter" + NO_BREAK_SPACE + "x",
])
def test_a_font_link_holds_only_what_a_font_query_is_written_with(query):
    """``font_links`` leaves this module for a fetch and, later, a page. It may
    hold letters, digits and the punctuation a css2 query uses, and no more."""
    href = html_lib.escape(FONT + query, quote=True)
    c = cleaned(f'<link rel="stylesheet" href="{href}"><p>x</p>')
    assert c.font_links == () and c.removed == {"link": 1}


def test_a_real_font_query_is_accepted_whole():
    query = ("Schibsted+Grotesk:wght@500;700;800&family=Newsreader:ital,opsz,wght@0,6..72,400;1,6..72,400"
             "&family=IBM+Plex+Mono:wght@400;500&display=swap&text=Hello%20World_1-2")
    c = cleaned(f"<link rel='stylesheet' href='{FONT}{query}'><p>x</p>")
    assert c.font_links == (FONT + query,)


def test_an_alternate_stylesheet_is_not_the_entrys_typeface():
    c = cleaned(f"<link rel='alternate stylesheet' href='{FONT}Inter' title='other'>"
                f"<link rel='Stylesheet Alternate' href='{FONT}Lora'>"
                f"<link rel='preload stylesheet' href='{FONT}Mono'><p>x</p>")
    assert c.font_links == (FONT + "Mono",)


# ── what the entry put on <html> and <body> ──────────────────────────────

def shell_tags(document):
    seen = audit(document)
    return ({name: value for tag, name, value in seen.attrs if tag == "html"},
            {name: value for tag, name, value in seen.attrs if tag == "body"})


def test_the_entrys_own_html_and_body_attributes_reach_the_shell():
    """A stylesheet that says ``:root[data-theme="dark"]`` or ``body.wide``
    means nothing if the shell's ``<html>`` and ``<body>`` are bare."""
    c = cleaned('<html data-theme="dark"><body class="x" style="margin:0"><p>t</p></body></html>')
    assert c.html.startswith('<!doctype html><html lang="en" data-theme="dark"><head>')
    assert '<body class="x" style="margin:0"><p>t</p></body></html>' in c.html
    assert c.body == "<p>t</p>"


def test_the_shell_takes_a_short_list_through_the_same_filter():
    c = cleaned("<html lang='fr' dir='rtl' class='no-js' id='root' onclick='x()' xmlns='http://www.w3.org/1999/xhtml'"
                " data-theme='dark' manifest='x.appcache' style='background:url(//e.com/x);color:red'>"
                "<body class='a b' id='main' onload='y()' background='//e.com/b.png' bgcolor='red'"
                " data-page='1' aria-label='nope' style='margin:0;behavior:url(x.htc)' lang='de'><p>t</p>")
    on_html, on_body = shell_tags(c.html)
    assert on_html == {"lang": "fr", "dir": "rtl", "class": "no-js", "data-theme": "dark",
                       "style": "background:none;color:red"}
    assert on_body == {"class": "a b", "data-page": "1", "style": "margin:0;", "lang": "de"}
    assert "e.com" not in c.html and "x.htc" not in c.html and "appcache" not in c.html
    assert c.removed.get("css") == 2 and c.removed.get("handler") == 2


def test_without_a_language_the_shell_says_english():
    for document in ("<p>t</p>", "<html><body><p>t</p>", "<html lang=''><p>t</p>", "<html lang='  '><p>t</p>"):
        assert cleaned(document).html.startswith('<!doctype html><html lang="en"><head>')


def test_the_later_of_two_body_tags_wins():
    """A file saved from claude.ai is the artifact's own document inside the
    download wrapper's ``<body>``: the second ``<html>`` and ``<body>`` are the
    entry's. (libxml2 2.11 discards the attributes of a second one, which is
    why they are read from the text and not from the tree.)"""
    c = cleaned("<!doctype html><html><head><meta charset=utf8></head><body class='wrapper' data-w='1'>\n"
                "<!DOCTYPE html><html lang='de' data-theme='dark'><head><title>T</title></head>"
                "<body class='entry' style='margin:0'><main>m</main></body></html></body></html>")
    on_html, on_body = shell_tags(c.html)
    assert on_html == {"lang": "de", "data-theme": "dark"}
    assert on_body == {"class": "entry", "data-w": "1", "style": "margin:0"}
    assert c.body == "<main>m</main>"


@pytest.mark.parametrize("decoy", [
    "<p title=\"<body class='evil'>\">t</p>",
    "<!-- <body class='evil'> --><p>t</p>",
    "<script>var s = \"<body class='evil'>\";</script><p>t</p>",
    "<style>/* <body class='evil'> */ p{color:red}</style><p>t</p>",
    "<p>&lt;body class='evil'&gt;</p>",
    "<bodyx class='evil'><p>t</p>", "</body class='evil'><p>t</p>",
])
def test_a_body_tag_that_is_not_a_tag_gives_the_shell_nothing(decoy):
    c = cleaned("<body class='real'>" + decoy)
    assert shell_tags(c.html)[1] == {"class": "real"}


def test_shell_attributes_are_escaped_and_bounded():
    many = " ".join(f"data-n{i}='v'" for i in range(500))
    c = cleaned(f"<body class='a\"b<c>&amp;d' data-x='{clean.FONT_CSS_MARK}' {many}><p>t</p>"
                + "".join(f"<body data-m{i}='v'>" for i in range(500)))
    on_body = shell_tags(c.html)[1]
    assert on_body["class"] == 'a"b<c>&d' and on_body["data-x"] == clean.FONT_CSS_MARK
    assert len(on_body) == clean.MAX_ATTRS


# ── a space inside an address ────────────────────────────────────────────

@pytest.mark.parametrize("raw,kept", [
    ("mailto:a@b.test?subject=Hello there", "mailto:a@b.test?subject=Hello%20there"),
    ("https://example.com/a b", "https://example.com/a%20b"),
    ("https://example.com/a  b c", "https://example.com/a%20%20b%20c"),
    ("#sec 1", "#sec%201"),
    ("  https://example.com/a b  ", "https://example.com/a%20b"),
    ("https://example.com/a%20b", "https://example.com/a%20b"),
])
def test_a_space_inside_an_address_is_encoded_not_a_reason_to_drop_it(raw, kept):
    c = cleaned(f'<p><a href="{html_lib.escape(raw, quote=True)}">words</a></p>')
    assert f'<a href="{html_lib.escape(kept, quote=True)}"' in c.body and c.removed == {}


@pytest.mark.parametrize("raw", [
    "https://example.com/a\tb", "https://example.com/a\nb", "https://example.com/a\rb",
    "https://exam\nple.com/", "java\tscript:alert(1)", "java script:alert(1)", " java\nscript:alert(1)",
    "https://example.com/a" + NO_BREAK_SPACE + "b", "https://example.com/a" + ZERO_WIDTH_SPACE + "b",
    RIGHT_TO_LEFT_OVERRIDE + "https://example.com/", "https://example.com/" + chr(0x2028) + "x",
    "https://example.com/" + chr(0x3000) + "x", "#a" + chr(0x85) + "b",
    "data:text/html,a b", "//example.com/a b", "a b",
])
def test_anything_else_odd_inside_an_address_still_drops_it(raw):
    c = cleaned(f'<p><a class="k" href="{html_lib.escape(raw, quote=True)}">words</a></p>')
    assert c.body == '<p><a class="k">words</a></p>' and c.removed == {"href": 1}


# ── an <svg> that is never closed ────────────────────────────────────────

def test_an_unclosed_drawing_does_not_swallow_the_article():
    """A browser leaves a drawing the moment it meets ``<p>``; libxml2 does
    not, and the rest of the entry arrived as invisible text inside the svg."""
    c = cleaned("<h1>T</h1><svg viewBox='0 0 1 1'><g><rect width='1' height='1'/>"
                "<p>first para</p><h2>Next</h2><rect width='2'/><ul><li>item</li></ul>tail words")
    assert c.body.startswith('<h1>T</h1><svg viewBox="0 0 1 1"><g><rect width="1" height="1"></rect>'
                             "</g></svg><p>first para</p><h2>Next</h2>")
    assert "<ul><li>item</li></ul>" in c.body and "tail words" in c.body
    assert c.body.count("<svg") == c.body.count("</svg>") == 1 and "<rect width=\"2\"" not in c.body
    assert c.summary == "first para"


# The HTML standard's list of start tags that end foreign content, less ``body``
# and ``head``: libxml2 2.11 discards a second one of those before the tree is
# built, so there is nothing in the tree to act on.
@pytest.mark.parametrize("tag", sorted(
    "b big blockquote br center code dd div dl dt em embed h1 h2 h3 h4 h5 h6 hr i img li "
    "listing menu meta nobr ol p pre ruby s small span strong strike sub sup table tt u ul var".split()))
def test_every_element_that_ends_a_drawing_ends_it(tag):
    c = cleaned(f"<p>before</p><svg><circle r='1'/><{tag}>after</{tag}><circle r='2'/></svg>")
    drawing, _, rest = c.body.partition("</svg>")
    assert drawing.endswith('<svg><circle r="1"></circle>'), drawing
    assert "after" in rest and "<circle" not in rest


def test_what_a_drawing_may_hold_does_not_end_it():
    c = cleaned("<svg><title>tip</title><desc><p>para</p></desc><text>la<tspan>bel</tspan></text>"
                "<custom>x</custom><a href='#y'><text>gone</text></a><circle r='1'/></svg><p>after</p>")
    assert c.body == ('<svg><title>tip</title><desc>para</desc><text>la<tspan>bel</tspan></text>'
                      'x<circle r="1"></circle></svg><p>after</p>')


def test_a_drawing_inside_a_drawings_tooltip_ends_on_its_own():
    c = cleaned("<svg><desc><svg><rect/><p>inner para</p></svg>still desc</desc><circle r='1'/></svg>")
    assert c.body == ('<svg><desc><svg><rect></rect></svg>inner parastill desc</desc>'
                      '<circle r="1"></circle></svg>')


# ── how much bigger, and how much slower ─────────────────────────────────

def test_cleaning_cannot_multiply_a_document_past_a_known_factor():
    """The handler bounds what it stores; this is the number it needs. The worst
    a character can do is a double quote in an attribute: one byte in, six out
    (``&quot;``)."""
    worst = {
        "quotes in an attribute": "<p title='" + '"' * 100_000 + "'>t</p>",
        "ampersands": "<p>" + "&" * 100_000 + "</p>",
        "less-than signs": "<p>" + "< " * 50_000 + "</p>",
        "unclosed inline tags": "<b>" * 200,
        "short outbound links": "<a href=http://a>" * 5_000,
        "less-than signs in a css string": "<style>p{content:'" + "<" * 100_000 + "'}</style><p>t</p>",
    }
    for name, document in worst.items():
        c = clean.clean(document)
        assert not refused(c), name
        assert len(c.html) <= 6 * len(document) + 400, name


def test_the_log_only_tells_too_deep_from_cut_off_never_whether_to_refuse():
    """The end marker is the sole authority for "read to the end"; the log is
    read only to choose the word once the marker is already missing. So sloppy
    markup - which logs plenty - is not taken for a depth stop, and a document
    the parser FINISHED is kept however loudly its log complains."""
    def parser_after(document):
        parser = clean.lxml.html.HTMLParser(encoding="utf-8", recover=True)
        clean.lxml.html.document_fromstring(document.encode("utf-8"), parser=parser)
        return parser
    sloppy = parser_after("<p>a<p>b</i></foo><bar baz><table><td>x</b></table>&bogus; <a href=x y='1' y='2'>t</a>")
    assert not clean._said_too_deep(sloppy)
    # an unterminated attribute FINISHES the document (2.11 logs FATAL anyway),
    # so it is kept-or-refused by the marker and, if refused, is cut_off not too_deep
    unquoted = parser_after("<p>x</p><p title=never-finished")
    assert any(entry.level_name == "FATAL" for entry in unquoted.error_log)
    assert clean.clean("<p title=\"never finished").reason == "cut_off"


def test_the_three_bounds_are_the_ones_the_comments_argue_for():
    """Each of these is a constant with its reasoning written beside it. A
    change to the number should have to come here and change the argument."""
    assert (clean.MAX_PASSES, clean.MAX_ATTRS, clean.MAX_TAG_ATTRS) == (5, 64, 1024)
