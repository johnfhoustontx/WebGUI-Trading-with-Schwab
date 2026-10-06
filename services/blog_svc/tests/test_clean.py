"""The document cleaner, against a hostile corpus.

Two kinds of check, on purpose:

* **Needles** - a string that must not be in the output (``<script``,
  ``e.com``). Blunt, and exactly what the plan's corpus asks for.
* **``audit``** - every promise the cleaner makes about its output, checked by
  parsing that output with the standard library's ``html.parser``, which is
  NOT the parser the cleaner used. A needle can pass because the hostile thing
  was spelled another way; the audit reads the tags and attributes a second
  parser actually sees.

⚠ lxml's wheels bundle a different libxml2 per platform: 2.11.9 on Windows
(``lxml.etree.LIBXML_VERSION`` on the box this was written on), and 2.14 on
Linux according to lxml 6's release notes - a release whose HTML tokenizer was
rewritten to follow HTML5. So the SAME input can parse into a different tree on
a developer's machine and in CI or on prod, and this file was only ever RUN
against 2.11. The corpora are split by that: ``RAW_TEXT_TRICKS`` are the inputs
whose reading depends on the parser, and they are held to the audit and to "no
script element", never to a needle that could legitimately survive as visible,
escaped text. A test here that fails on Linux and passes on Windows is most
likely an expectation about the TREE (where a ``<p>`` was implied, what a
``<title>`` holds); one about the OUTPUT'S SAFETY failing anywhere is a bug.
"""
import html as html_lib
import pathlib
import random
import re
import time
from html.parser import HTMLParser

import pytest
import tinycss2

from services import _degrade
from services.blog_svc import clean
from shared import blog_inbox

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "entry_like_the_example.html"

SHELL_OPEN = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
              '<meta name="viewport" content="width=device-width, initial-scale=1">'
              '<title>')
EMPTY_SHELL = SHELL_OPEN + "</title><style>/*blog-fonts*/</style></head><body></body></html>"


def test_the_cleaners_two_libraries_are_pinned_where_prod_installs_from():
    """Prod has its own venv and installs from the lock. Both libraries arrived
    as dependencies of nicegui; the cleaner imports them itself now, so each is
    a named, pinned line - anchored at the line start, so a mention in a comment
    cannot satisfy this."""
    root = pathlib.Path(__file__).resolve().parents[3]
    for listing in ("requirements.txt", "requirements.lock"):
        lines = (root / listing).read_text(encoding="utf-8").splitlines()
        for package in ("lxml", "tinycss2"):
            assert any(re.match(rf"^{package}==", line) for line in lines), (
                f"{package} is not pinned in {listing}")


# ── the audit: a second parser's view of the output ──────────────────────────

_SHELL_META = {("meta", "charset"), ("meta", "name"), ("meta", "content")}
_ALLOWED_TAGS = ({"html", "head", "meta", "title", "style", "body"}
                 | {t.lower() for t in clean.KEPT_HTML | clean.KEPT_SVG})
_ALLOWED_ATTRS = {a.lower() for a in clean.ATTRS}
_PREFIXED = re.compile(r"(?:aria|data)-[a-z0-9][a-z0-9._-]*")
_HREF_OK = re.compile(r"(?:https?://[^\s/?#\\]|mailto:\S|#)\S*")
_CSS_VALUED = {"style", "fill", "stroke", "clip-path", "marker-start", "marker-mid", "marker-end"}

# The shell: the plan's, with whatever the entry's own <html> and <body> handed
# on. Every attribute is double-quoted, so "[^"]*" is one whole value.
_SHELL_RE = re.compile(
    r'<!doctype html><html lang="[^"]*"(?: [a-z][a-z0-9._-]*="[^"]*")*><head><meta charset="utf-8">'
    r'<meta name="viewport" content="width=device-width, initial-scale=1"><title>')

# ⚠ These four lists are typed out HERE, on purpose, and not read from the
# cleaner. An audit that imported the cleaner's allow-lists would agree with
# any mistake made in them.
_ON_THE_SHELL = {"class", "style", "lang", "dir"}
_AT_RULES_THAT_FETCH_NOTHING = {
    "media", "supports", "container", "layer", "keyframes", "-webkit-keyframes", "-moz-keyframes",
    "page", "property", "scope", "starting-style", "counter-style", "font-feature-values", "swash",
    "styleset", "stylistic", "character-variant", "ornaments", "annotation", "font-palette-values",
    "view-transition", "position-try"}
_FUNCTIONS_THAT_FETCH = {
    "url", "image", "image-set", "-webkit-image-set", "-moz-image-set", "cross-fade",
    "-webkit-cross-fade", "element", "-moz-element", "src", "expression"}
_PROPERTIES_THAT_RUN = {"behavior", "-ms-behavior", "-moz-binding"}


def _declared(css, kind):
    """The name of every declaration in ``css``, found with tinycss2's own rule
    and declaration parsers - a different reading of the text from the flat
    token walk the cleaner does."""
    names = []
    todo = [tinycss2.parse_stylesheet(css, True, True) if kind == "sheet"
            else tinycss2.parse_blocks_contents(css, True, True)]
    while todo:
        for item in todo.pop():
            if item.type == "declaration":
                names.append(item.lower_name)
            elif item.type in ("qualified-rule", "at-rule") and item.content is not None:
                todo.append(tinycss2.parse_blocks_contents(item.content, True, True))
    return names


def css_problems(css, kind="sheet"):
    """What should not be in a piece of cleaned CSS. ``kind`` is ``"sheet"``, a
    ``"style"`` attribute, or a single ``"value"`` (a paint attribute).

    Read from the TOKENS a CSS parser makes of the output, not by searching its
    text: a pattern for ``url(`` cannot tell a class named ``.url\\(x\\)`` or the
    words in a string from a request, and one that could not be fooled that way
    would be the cleaner's own second look, checking itself."""
    problems = []
    if "</" in css or "<!" in css:
        problems.append("could close its own element")
    todo = list(tinycss2.parse_component_value_list(css))
    while todo:
        node = todo.pop()
        kind_of = node.type
        if kind_of == "url" and not re.fullmatch(r"#[A-Za-z0-9_-]+", node.value):
            problems.append(f"url({node.value})")
        elif kind_of == "function":
            if node.lower_name in _FUNCTIONS_THAT_FETCH or "url" in node.lower_name:
                problems.append(f"{node.name}()")
            todo.extend(node.arguments)
        elif kind_of == "at-keyword" and node.lower_value not in _AT_RULES_THAT_FETCH_NOTHING:
            problems.append(f"@{node.value}")
        elif kind_of == "error":
            problems.append(f"a {node.kind} the cleaner should have mended")
        elif kind_of == "comment" and node.value:      # "/**/" is the serialiser's own separator
            problems.append("a comment")
        elif kind_of == "literal" and node.value in ("<!--", "-->", "\\"):
            problems.append(node.value)
        elif kind_of.endswith(" block"):
            todo.extend(node.content)
    if kind != "value":
        problems += [f"declares {name}" for name in _declared(css, kind)
                     if name in _PROPERTIES_THAT_RUN]
    return problems


def test_the_audit_sees_what_fetches_and_nothing_that_only_says_so():
    for bad in ("p{a:url(x.png)}", "p{a:URL('x.png')}", "p{a:u\\72l(x.png)}", "p{a:image('x.png')}",
                "p{a:cross-fade(a,b)}", "p{a:src('x')}", "p{a:element(#x)}", "p{a:image-set('x' 1x)}",
                "p{a:texturl(x)}", "@import 'x';", "@font-face{a:b}", "@\\69mport 'x';",
                "p{behavior:x}", "@media print{p{-moz-binding:x}}", ".a{b:c;.d{BEHAVIOR:x}}",
                "p{a:b}}", "p{a:'x\n}", "/* c */p{a:b}", "<!-- p{a:b} -->", "p{a:b}</style", "p{a:<!x}"):
        assert css_problems(bad), bad
    for fine in ("p{a:url(#g)}", ".url\\(x\\){a:b}", "p{content:\"see url\\28 x) and \\40 import\"}",
                 ".javascript:hover{a:b}", ".behavior:hover{a:b}", "p{scroll-behavior:smooth}",
                 "@media (width < 600px){p{a:b}}", "a/**/b{c:d}", "@supports (behavior:x){p{a:b}}"):
        assert not css_problems(fine), (fine, css_problems(fine))
    assert css_problems("behavior:x", "style") and not css_problems("behavior", "value")


class _Seen(HTMLParser):
    """What ``html.parser`` finds in a document: tags, attributes, the whole
    text of every ``<style>``, and anything that is neither (comments,
    declarations)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.attrs, self.other, self.css = [], [], [], []
        self._style = None

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend((tag, name, value) for name, value in attrs)
        self._style = [] if tag == "style" else None

    def handle_endtag(self, tag):
        if tag == "style" and self._style is not None:
            self.css.append("".join(self._style))
            self._style = None

    def handle_data(self, data):
        if self._style is not None:
            self._style.append(data)

    def handle_comment(self, data):
        self.other.append(("comment", data))

    def handle_decl(self, decl):
        self.other.append(("decl", decl))

    def handle_pi(self, data):
        self.other.append(("pi", data))

    def unknown_decl(self, data):
        self.other.append(("unknown", data))


def audit(document):
    """Assert everything the cleaner promises about ``document``; return what
    the second parser saw."""
    assert _SHELL_RE.match(document), document[:300]
    assert document.endswith("</body></html>")
    assert document.count(clean.FONT_CSS_MARK) == 1
    seen = _Seen()
    seen.feed(document)
    seen.close()
    assert seen.other == [("decl", "doctype html")], seen.other
    assert seen.tags[:6] == ["html", "head", "meta", "meta", "title", "style"]
    for once in ("html", "head", "body"):
        assert seen.tags.count(once) == 1, once
    assert seen.tags.count("meta") == 2
    assert set(seen.tags) <= _ALLOWED_TAGS, sorted(set(seen.tags) - _ALLOWED_TAGS)
    for tag, name, value in seen.attrs:
        if (tag, name) in _SHELL_META:
            continue
        where = f"{name}={value!r} on <{tag}>"
        assert not name.startswith("on"), where
        if tag in ("html", "body"):
            assert name in _ON_THE_SHELL or re.fullmatch(r"data-[a-z0-9][a-z0-9._-]*", name), where
        elif tag == "a" and name == "href":
            assert _HREF_OK.fullmatch(value), where
        elif tag == "a" and name in ("target", "rel"):
            assert value == {"target": "_blank", "rel": "noopener noreferrer"}[name], where
        else:
            assert name in _ALLOWED_ATTRS or _PREFIXED.fullmatch(name), where
        if name in _CSS_VALUED:
            problems = css_problems(value, "style" if name == "style" else "value")
            assert not problems, (where, problems)
    # the first stylesheet is the mark and nothing else; the rest are the entry's
    assert seen.css[0] == clean.FONT_CSS_MARK
    for css in seen.css[1:]:
        assert not css_problems(css), (css_problems(css), css[:300])
    return seen


def cleaned(document, *, may_refuse=False):
    """Clean, audit, and prove the result is a fixed point - and that no
    exception was swallowed on the way. A crash inside the cleaner comes back as
    the empty shell, which is a perfectly clean document and would pass the
    audit; the degrade counter is the only thing that says it happened.

    A REFUSAL passes the audit too, and for the same reason. So unless the test
    says a refusal is an acceptable answer (``may_refuse``), getting one fails
    here: a test about what survives must not go green because nothing did."""
    crashes = _degrade.counts().get("blog.clean", 0)
    result = clean.clean(document)
    assert isinstance(result, clean.Cleaned)
    audit(result.html)
    assert clean.clean(result.html).html == result.html, "cleaning its own output changed it"
    assert _degrade.counts().get("blog.clean", 0) == crashes, "the cleaner raised inside"
    assert may_refuse or "unparseable" not in result.removed, "the document was refused"
    return result


def page(payload):
    return f"<html><body><h1>T</h1>{payload}<p>kept</p></body></html>"


# ── the plan's corpus ────────────────────────────────────────────────────────

HOSTILE = [
    "<script>alert(1)</script>", "<SCRIPT SRC=//x></SCRIPT>", "<svg><script>1</script></svg>",
    "<img src=x onerror=alert(1)>", "<p onclick='x()'>t</p>", "<a href='javascript:alert(1)'>x</a>",
    "<a href=' JaVaScRiPt:alert(1)'>x</a>", "<a href='data:text/html,<script>1</script>'>x</a>",
    "<iframe src='https://e.com'></iframe>", "<object data=x></object>", "<embed src=x>",
    "<form action=//e.com><input name=a><button>go</button></form>",
    "<base href='https://e.com/'>", "<meta http-equiv=refresh content='0;url=//e.com'>",
    "<link rel=stylesheet href='https://e.com/x.css'>",
    "<style>@import url(https://e.com/x.css); p{background:url(https://e.com/t.gif)}</style>",
    "<p style='background:url(//e.com/t.gif)'>t</p>", "<style>p{behavior:url(x.htc)}</style>",
    "<svg><foreignObject><iframe src=x></iframe></foreignObject></svg>",
    "<svg><use href='https://e.com/x.svg#a'/></svg>", "<svg><a href='javascript:1'><text>x</text></a></svg>",
    "<style></style><script>1</script>", "<math><mtext><script>1</script></mtext></math>",
    "<details ontoggle=alert(1) open>x</details>", "<div data-x='1' srcdoc='<script>1</script>'>x</div>",
]

NEEDLES = ("<script", "javascript:", "onerror", "onclick", "ontoggle", "<iframe",
           "<object", "<embed", "<form", "<input", "<button", "<base", "http-equiv",
           "<link", "@import", "e.com", "srcdoc", "<foreignobject", "<use", "behavior")


@pytest.mark.parametrize("payload", HOSTILE)
def test_nothing_hostile_survives(payload):
    out = cleaned(page(payload)).html.lower()
    assert "kept" in out
    for needle in NEEDLES:
        assert needle not in out, f"{needle!r} survived {payload!r}"


# ── more of the same: tags and attributes any parser reads the same way ──────

MORE_HOSTILE = [
    "<ScRiPt>alert(1)</sCrIpT>",
    "<p ONCLICK='x()' OnMouseOver=x()>t</p>",
    # entity-encoded and whitespace-split schemes
    "<a href='jav&#x61;script:alert(1)'>x</a>",
    "<a href='java\tscript:alert(1)'>x</a>",
    "<a href='java&#10;script:alert(1)'>x</a>",
    "<a href='java&#13;&#10;script:alert(1)'>x</a>",
    "<a href='&#1;javascript:alert(1)'>x</a>",
    "<a href='&#106;&#97;&#118;&#97;&#115;&#99;&#114;&#105;&#112;&#116;&#58;alert(1)'>x</a>",
    "<a href='JAVASCRIPT&colon;alert(1)'>x</a>",
    "<a href='javascript&#58;alert(1)'>x</a>",
    "<a href='vbscript:msgbox(1)'>x</a>",
    "<A HREF='VBScript:msgbox(1)'>x</A>",
    # other ways to name another host, or no host at all
    "<a href='//e.com/x'>x</a>", "<a href='/\\e.com/x'>x</a>", "<a href='\\\\e.com/x'>x</a>",
    "<a href='https:e.com'>x</a>", "<a href='https:/e.com'>x</a>",
    "<a href='file:///etc/passwd'>x</a>", "<a href='blob:https://e.com/1'>x</a>",
    "<a href='ftp://e.com/x'>x</a>", "<a href='ws://e.com/x'>x</a>",
    "<a xlink:href='javascript:alert(1)'>x</a>",
    "<a href='https://ok.example/' target='_top' rel='opener' ping='https://e.com/p' download>x</a>",
    # SVG
    "<svg onload=alert(1)><circle r=1 onclick=alert(1) /></svg>",
    "<SVG ONLOAD=alert(1)></SVG>",
    "<svg><a xlink:href='javascript:alert(1)'><text>x</text></a></svg>",
    "<svg><image href='https://e.com/x.png' /></svg>",
    "<svg><image xlink:href='https://e.com/x.png' /></svg>",
    "<svg><set attributeName='href' to='javascript:alert(1)' /></svg>",
    "<svg><animate attributeName='href' values='javascript:alert(1)' /></svg>",
    "<svg><animateTransform attributeName='transform' onbegin=alert(1) /></svg>",
    "<svg><use xlink:href='data:image/svg+xml,x#x' /></svg>",
    "<svg><rect fill='url(https://e.com/p.svg#a)' width=1 height=1 /></svg>",
    "<svg><rect stroke='URL( \"https://e.com/p.svg#a\" )' /></svg>",
    "<svg><rect style='fill:url(https://e.com/p.svg#a)' /></svg>",
    "<svg><path clip-path='url(//e.com/c.svg#a)' marker-end='url(//e.com/m.svg#a)' /></svg>",
    "<svg><feImage href='https://e.com/x.png' /></svg>",
    "<svg xmlns:xlink='http://www.w3.org/1999/xlink'><script xlink:href='https://e.com/x.js' /></svg>",
    "<svg><foreignObject><body onload=alert(1)><p onclick=alert(1)>x</p></body></foreignObject></svg>",
    # things that fetch
    "<img src='https://e.com/pixel.gif'>", "<IMG SRC='https://e.com/pixel.gif'>",
    "<picture><source srcset='https://e.com/a.png'><img src='https://e.com/b.png'></picture>",
    "<video src='https://e.com/v.mp4' poster='https://e.com/p.png'></video>",
    "<audio src='https://e.com/a.mp3'><track src='https://e.com/t.vtt'></audio>",
    "<table background='https://e.com/b.png'><tr><td background='//e.com/c.png'>x</td></tr></table>",
    "<input type=image src='https://e.com/x.png' formaction='javascript:alert(1)'>",
    "<button formaction='javascript:alert(1)'>go</button>",
    "<link rel=preload as=image href='https://e.com/x.png'>", "<link rel=dns-prefetch href='//e.com'>",
    "<link rel='stylesheet' href='https://fonts.googleapis.com.e.com/css2?family=X'>",
    "<meta name=referrer content=unsafe-url><meta http-equiv='Set-Cookie' content='a=b'>",
    "<META HTTP-EQUIV='refresh' CONTENT='0;URL=https://e.com/'>",
    "<object data='data:text/html,x'><param name=src value='https://e.com'></object>",
    "<applet code=x.class codebase='https://e.com/'></applet>",
    "<embed src='data:image/svg+xml,x'>",
    "<iframe srcdoc='&lt;script&gt;alert(1)&lt;/script&gt;'></iframe>",
    "<portal src='https://e.com/'></portal>", "<fencedframe src='https://e.com/'></fencedframe>",
    "<template><script>alert(1)</script><img src=x onerror=alert(1)></template>",
    "<dialog open><form method=dialog><button>x</button></form></dialog>",
    "<canvas id=c></canvas><map name=m><area href='javascript:alert(1)'></map>",
    "<select onchange=alert(1)><option onclick=alert(1)>a</option></select>",
    "<textarea onfocus=alert(1) autofocus>t</textarea>",
    # handlers on elements that are kept or unwrapped
    "<details open ontoggle=alert(1)><summary onclick=alert(1)>s</summary></details>",
    "<marquee onstart=alert(1)>m</marquee>",
    "<div contenteditable onfocus=alert(1) autofocus tabindex=0>t</div>",
    "<center onmouseover=alert(1)><font onclick=alert(1)>x</font></center>",
    "<math href='javascript:alert(1)'>x</math>",
    "<math><maction actiontype=statusline xlink:href='javascript:alert(1)'>x</maction></math>",
    "<noscript><img src=x onerror=alert(1)></noscript>",
    # CSS inside attributes
    "<p style=\"background:url('javascript:alert(1)')\">t</p>",
    "<div style='x:expression(alert(1))'>t</div>",
    "<p style='background:u\\72l(//e.com/t.gif)'>t</p>",
    "<p style='background:URL(\"//e.com/t.gif\")'>t</p>",
    "<p style='background:url(&#x2f;&#x2f;e.com/t.gif)'>t</p>",
    "<p style='background-image:image-set(\"//e.com/a.png\" 1x)'>t</p>",
    "<p style='background-image:-webkit-image-set(url(//e.com/a.png) 1x)'>t</p>",
    "<p style='list-style-image:url(//e.com/l.png);cursor:url(//e.com/c.cur),auto'>t</p>",
    "<p style='behavior:url(x.htc)'>t</p>", "<p style='-moz-binding:url(//e.com/x.xml#a)'>t</p>",
    "<p style='color:red;@import url(//e.com/x.css);'>t</p>",
    "<p STYLE='BACKGROUND:URL(//e.com/t.gif)'>t</p>",
    # a stylesheet's own attributes
    "<style media='screen{} @import url(//e.com/x.css); x'>p{color:red}</style>",
    "<style media='x){} p{background:url(//e.com/y)} @media (x'>p{color:red}</style>",
    "<style media='print' onload=alert(1)>p{background:url(//e.com/y)}</style>",
    "<style type='text/css' media='url(//e.com/y)'>p{color:red}</style>",
]

MORE_NEEDLES = NEEDLES + (
    "vbscript:", "onload", "onmouseover", "onfocus", "onstart", "onchange", "onbegin",
    "xlink", "formaction", "background=", "expression", "srcset", "poster", "ping=",
    "download", "tabindex", "contenteditable", "autofocus", "<img", "<image", "<video",
    "<audio", "<track", "<source", "<set", "<animate", "<template", "<dialog", "<canvas",
    "<map", "<area", "<select", "<option", "<textarea", "<param", "<applet", "<portal",
    "<fencedframe", "<feimage", "<math", "<marquee", "<center", "<font", "file:", "blob:",
    "ftp:", "ws:", "data:", "image-set", "-moz-binding", "url(", "xmlns")


@pytest.mark.parametrize("payload", MORE_HOSTILE)
def test_nothing_hostile_survives_in_any_spelling(payload):
    out = cleaned(page(payload)).html.lower()
    assert "kept" in out
    for needle in MORE_NEEDLES:
        assert needle not in out, f"{needle!r} survived {payload!r}"


# ── inputs two parsers read differently ──────────────────────────────────────

RAW_TEXT_TRICKS = [
    "<style>p{}</style><script>alert(1)</script>",
    "<style>p{color:red}</style ><script>alert(1)</script>",
    "<style><!--</style><script>alert(1)</script>--></style>",
    "<style>p{content:'</style><script>alert(1)</script>'}</style>",
    "<style>/*</style><script>alert(1)</script>*/</style>",
    "<svg><style><a title='</style><img src=x onerror=alert(1)>'></style></svg>",
    "<svg><title><script>alert(1)</script></title></svg>",
    "<svg><title><![CDATA[</title><script>alert(1)</script>]]></title></svg>",
    "<svg><desc><img src=x onerror=alert(1)></desc></svg>",
    "<svg></p><script>alert(1)</script>",
    "<math><mtext></p><script>alert(1)</script>",
    "<math><annotation-xml encoding='text/html'><script>alert(1)</script></annotation-xml></math>",
    "<title><script>alert(1)</script></title>",
    "<title></title><script>alert(1)</script>",
    "<title><img src=x onerror=alert(1)></title>",
    "<plaintext><script>alert(1)</script>",
    "<xmp><script>alert(1)</script></xmp>",
    "<xmp></xmp><script>alert(1)</script>",
    "<listing><script>alert(1)</script></listing>",
    "<noembed><script>alert(1)</script></noembed>",
    "<noframes><script>alert(1)</script></noframes>",
    "<noscript><p title='</noscript><script>alert(1)</script>'>",
    "<textarea><script>alert(1)</script></textarea>",
    "<textarea></textarea><script>alert(1)</script>",
    "<iframe><script>alert(1)</script></iframe>",
    "<select><style></select><script>alert(1)</script></style>",
    # comments, in every way one can be closed or left open
    "<!--><script>alert(1)</script>-->",
    "<!---><script>alert(1)</script>-->",
    "<!-- --!><script>alert(1)</script>-->",
    "<!-- <script>alert(1)</script> -->",
    "<!--[if IE]><script>alert(1)</script><![endif]-->",
    "<!--[if !IE]><!--><script>alert(1)</script><!--<![endif]-->",
    "<![if IE]><script>alert(1)</script><![endif]>",
    "<!-- never closed <script>alert(1)</script>",
    # CDATA, processing instructions, doctype subsets
    "<![CDATA[<script>alert(1)</script>]]>",
    "<svg><![CDATA[<script>alert(1)</script>]]></svg>",
    "<?xml-stylesheet href='https://e.com/x.xsl' type='text/xsl'?>",
    "<?php echo '<script>alert(1)</script>' ?>",
    "<?xml version='1.0' encoding='utf-7'?><p>+ADw-script+AD4-alert(1)+ADw-/script+AD4-</p>",
    "<!DOCTYPE html [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><p>&x;</p>",
    "<!ENTITY % x SYSTEM 'https://e.com/x.dtd'>",
    # attribute values that try to end their tag
    "<p title='--><script>alert(1)</script>'>t</p>",
    "<p title='</p><script>alert(1)</script>'>t</p>",
    "<p title='\"><script>alert(1)</script>'>t</p>",
    "<p title=\"'><img src=x onerror=alert(1)>\">t</p>",
    "<p class='a\" onclick=\"alert(1)'>t</p>",
    "<p data-x='`onclick=alert(1)'>t</p>",
    "<a href='https://ok.example/\"onclick=\"alert(1)'>x</a>",
    "<a href='#\"><script>alert(1)</script>'>x</a>",
    # broken and nested markup
    "<div><p>unclosed<script>alert(1)",
    "<scr<script>ipt>alert(1)</scr</script>ipt>",
    "<<script>alert(1)//<</script>",
    "<script\x0c>alert(1)</script>",
    "<script/src=//e.com/x.js></script>",
    "<script\n>alert(1)</script\n>",
    "<svg/onload=alert(1)>",
    "<img/src/onerror=alert(1)>",
    "<img src=x onerror=alert(1)//",
    "<p onclick=alert(1)",
    "<a href=javascript:alert(1)",
    "<math><mi//xlink:href='data:x,<script>alert(1)</script>'>",
    "<table><caption><script>alert(1)</script></caption><tr><td onclick=alert(1)>x</td></tr></table>",
    "<table><script>alert(1)</script><tr><td>x</td></tr></table>",
    "</style><script>alert(1)</script>",
    "</title><script>alert(1)</script>",
    "</body></html><script>alert(1)</script>",
    "</body></html><body onload=alert(1)><p onclick=alert(1)>again</p>",
    "<html onclick=alert(1)><head onclick=alert(1)><body onclick=alert(1)>",
    "<head><body><head><script>alert(1)</script></head>",
    "<frameset onload=alert(1)><frame src='javascript:alert(1)'></frameset>",
    "<isindex action='javascript:alert(1)' type=image>",
    "<p>\x00<scr\x00ipt>alert(1)</scr\x00ipt></p>",
    "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>",
    "<p>&#60;script&#62;alert(1)&#60;/script&#62;</p>",
    "\ufeff<script>alert(1)</script>",
    "<p>\ud800<script>alert(1)</script></p>",
]


# An event handler written as an ATTRIBUTE of a tag: `` onclick=`` after the
# tag's name and outside every quoted value. The cleaner always double-quotes a
# value and escapes a quote inside one, so skipping "..." runs is exact for its
# output - and it has to be skipped, because ``title="x onclick=y"`` is a title
# that says so, and the audit (a real parser) reads it as one.
_HANDLER_IN_A_TAG = re.compile(r'<[a-z][^<>"]*(?:"[^"]*"[^<>"]*)*\son[a-z]+\s*=')


def test_the_handler_pattern_sees_a_handler_and_not_a_quoted_word():
    assert _HANDLER_IN_A_TAG.search('<p onclick=x>')
    assert _HANDLER_IN_A_TAG.search('<p class="a" onclick="x">')
    assert _HANDLER_IN_A_TAG.search('<p class="a"\tonload = "x">')
    assert not _HANDLER_IN_A_TAG.search('<p class="a&quot; onclick=">')
    assert not _HANDLER_IN_A_TAG.search('<p>text onclick=x</p>')


@pytest.mark.parametrize("payload", RAW_TEXT_TRICKS)
def test_markup_two_parsers_read_differently_still_comes_out_inert(payload):
    """Whatever tree the parser builds, the output holds allow-listed tags,
    allow-listed attributes and escaped text. Text that LOOKS like a script
    (``&lt;script&gt;``) is allowed to survive; a script element is not."""
    # may_refuse: several of these end inside a comment, a tag or a raw-text
    # element, and a document cut off like that is refused.
    for document in (page(payload), payload, payload + "<p>kept</p>", "<p>" + payload):
        out = cleaned(document, may_refuse=True).html.lower()
        assert "<script" not in out and "<img" not in out and "<iframe" not in out
        assert not _HANDLER_IN_A_TAG.search(out), _HANDLER_IN_A_TAG.search(out).group()


# ── CSS ──────────────────────────────────────────────────────────────────────

HOSTILE_CSS = [
    "@import url(https://e.com/x.css);",
    "@import 'https://e.com/x.css';",
    '@import "https://e.com/x.css" screen;',
    "@import url('https://e.com/x.css') layer(base) supports(display:grid);",
    "@IMPORT URL(https://e.com/x.css);",
    "@\\69mport url(https://e.com/x.css);",
    "@\\49 MPORT 'https://e.com/x.css';",
    "@import url(https://e.com/x.css)",
    "@import/**/url(https://e.com/x.css);",
    "@/**/import 'https://e.com/x.css';",
    "@/**/font-face{font-family:x;src:url(https://e.com/f.woff2)}",
    "p{background:url/**/(https://e.com/t.gif)}",
    "p{background:u/**/rl(https://e.com/t.gif)}",
    "p{background:\\\nurl(https://e.com/t.gif)}",
    "p{background:url(https://e.com/t.gif)\\",
    "p{background:\\url(https://e.com/t.gif)}",
    "@import\n'https://e.com/x.css'\n;",
    "@charset 'utf-7'; @namespace svg url(https://e.com/ns);",
    "@namespace 'https://e.com/ns';",
    "@font-face{font-family:x;src:url(https://e.com/f.woff2)}",
    "@font-face{font-family:x;src:url('https://e.com/f.woff2') format('woff2'),local(Arial)}",
    "@FONT-FACE{font-family:x;src:url(//e.com/f.woff2)}",
    "@font-\\66 ace{font-family:x;src:url(//e.com/f.woff2)}",
    "@font-face{font-family:x;src:local('https://e.com/f.woff2')}",
    "p{background:url(https://e.com/t.gif)}",
    "p{background:URL( 'https://e.com/t.gif' )}",
    'p{background:Url("https://e.com/t.gif")}',
    "p{background:u\\72l(https://e.com/t.gif)}",
    "p{background:\\75\\72\\6c(https://e.com/t.gif)}",
    "p{background:\\75 \\72 \\6c (https://e.com/t.gif)}",
    "p{background:u\\000072l(https://e.com/t.gif)}",
    "p{background:\\55RL(https://e.com/t.gif)}",
    "p{background:u\\r\\l(https://e.com/t.gif)}",
    "p{background:url(\\68ttps://e.com/t.gif)}",
    "p{background:url(https://e.com/t.gif}",
    "p{background:url('https://e.com/t.gif}",
    "p{background:url(https://e.com/a b)}",
    "p{background:url(https://e.com/a'b)}",
    "p{background:url(/**/https://e.com/t.gif)}",
    "p{background:url(https://e.com/t.gif)!important}",
    "p{background:#fff url(https://e.com/t.gif) no-repeat,url(//e.com/u.gif)}",
    "p{background-image:image-set('https://e.com/a.png' 1x)}",
    "p{background-image:IMAGE-SET('https://e.com/a.png' 1x,'https://e.com/b.png' 2x)}",
    "p{background-image:-webkit-image-set(url(https://e.com/a.png) 1x)}",
    "p{background-image:image-\\73 et('https://e.com/a.png' 1x)}",
    "p{background-image:image('https://e.com/a.png')}",
    "p{background:cross-fade(url(https://e.com/a.png), url(https://e.com/b.png))}",
    "p{background:src('https://e.com/a.png')}",
    "p{list-style:url(https://e.com/l.png);cursor:url(https://e.com/c.cur),auto}",
    "p{--x:url(https://e.com/v.png);background:var(--x)}",
    "p{background:var(--nope,url(https://e.com/v.png))}",
    "p{filter:url(https://e.com/f.svg#x);mask:url(https://e.com/m.svg#y);clip-path:url(https://e.com/c.svg#z)}",
    "p{shape-outside:url(https://e.com/s.png);border-image:url(https://e.com/b.png) 30}",
    "p::after{content:url(https://e.com/c.png) / 'alt'}",
    "p{behavior:url(https://e.com/x.htc)}",
    "p{BEHAVIOR:url(x.htc)}",
    "p{be\\68 avior:url(x.htc)}",
    "p{-ms-behavior:url(x.htc)}",
    "p{behavior : url(x.htc)}",
    "p{-moz-binding:url(https://e.com/x.xml#a)}",
    "p{-MOZ-BINDING:url(https://e.com/x.xml#a)}",
    "p{width:expression(alert(1))}",
    "p{width:EXPRESSION(alert(1))}",
    "p{width:expr/**/ession(alert(1))}",
    "p{width:e\\78pression(alert(1))}",
    "p{width:e\\78 pression(alert(1))}",
    "p{background:url(javascript:alert(1))}",
    "p{background:url('javascript:alert(1)')}",
    "@media screen{@import url(https://e.com/x.css);p{background:url(https://e.com/t.gif)}}",
    "@supports (display:grid){@font-face{font-family:x;src:url(https://e.com/f.woff)}}",
    "@media (min-width:1px){@media (min-width:2px){p{background:url(https://e.com/t.gif)}}}",
    ".a{color:red;.b{background:url(https://e.com/t.gif)}}",
    "@document url(https://e.com/){p{color:red}}",
    "@-moz-document url-prefix(https://e.com/){p{color:red}}",
    "<!-- p{background:url(https://e.com/t.gif)} -->",
    "p{color:red}} p{background:url(https://e.com/t.gif)}",
    "p{color:red;background:url(https://e.com/t.gif)",
    "p{background:url(https://e.com/t.gif);color:'unterminated\n}",
    "[x='}']{background:url(https://e.com/t.gif)}",
    "p{background:url(#ok);background:url(#ok https://e.com/t.gif)}",
    "p{background:url('#ok https://e.com/t.gif')}",
    "p{background:url(#ok\\29 url\\28 https://e.com/t.gif)}",
    "p{background:url(\"#a\\\"), url(https://e.com/t.gif\")}",
]

CSS_NEEDLES = ("e.com", "@import", "@font-face", "@namespace", "@charset", "@document",
               "url(h", "url(/", "url('", 'url("', "url( ", "behavior", "-moz-binding",
               "expression", "ession(", "image-set", "javascript:", "cross-fade", "src(",
               "<!--", "-->")


@pytest.mark.parametrize("css", HOSTILE_CSS)
def test_a_stylesheet_cannot_reach_another_host(css):
    out = cleaned(page(f"<style>{css}</style>")).html.lower()
    assert "kept" in out
    for needle in CSS_NEEDLES:
        assert needle not in out, f"{needle!r} survived {css!r}"


@pytest.mark.parametrize("css", HOSTILE_CSS)
def test_the_same_css_in_a_style_attribute_cannot_either(css):
    attribute = html_lib.escape(css, quote=True)
    out = cleaned(page(f'<p style="{attribute}">t</p>')).html.lower()
    assert "kept" in out
    for needle in CSS_NEEDLES:
        assert needle not in out, f"{needle!r} survived {css!r}"


def test_a_hostile_declaration_goes_and_its_neighbours_stay():
    out = cleaned(page("<style>p{color:red;background:u\\72l(https://e.com/x);margin:0}"
                       "q{behavior:url(x.htc);padding:1px}"
                       "@import 'x.css';h2{width:expression(1);height:2px}</style>"
                       "<p style='color:blue;-moz-binding:url(x);margin:3px'>t</p>")).html
    for piece in ("color:red", "background:none", "margin:0", "padding:1px", "height:2px",
                  "width:none", "color:blue", "margin:3px"):
        assert piece in out, piece
    assert "e.com" not in out and "x.htc" not in out and "x.css" not in out


def test_ordinary_css_survives_with_its_meaning():
    """The other half of the bargain: a design is not flattened. In particular
    ``scroll-behavior`` is not the ``behavior`` property, and an escaped colon
    in a class name is not an attack."""
    css = ("html{scroll-behavior:smooth;overscroll-behavior:contain}"
           ".md\\:flex{display:flex}"
           "@media (prefers-color-scheme: dark){:root:not([data-theme=\"light\"]){--accent:#6AA8FF}}"
           "@supports (display:grid){.g{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}}"
           "@keyframes fade{from{opacity:0}to{opacity:1}}"
           "@layer base,theme;"
           "blockquote::before{content:\"\\201C\"}"
           ".w{width:calc(100% - 2px);color:rgb(0 0 0 / 50%);font:italic 1.2em/1.4 Georgia,serif}"
           ".x>li+li~li{margin:-1px 0 0!important}"
           "h1{font-size:clamp(34px,6vw,52px)}")
    out = cleaned(page(f"<style>{css}</style>")).html
    for piece in ("scroll-behavior:smooth", "overscroll-behavior:contain", ".md\\:flex{display:flex}",
                  "@media (prefers-color-scheme: dark)", '[data-theme="light"]', "--accent:#6AA8FF",
                  "@supports (display:grid)", "repeat(2,minmax(0,1fr))", "@keyframes fade",
                  "@layer base,theme;", "\u201c", "calc(100% - 2px)", "rgb(0 0 0 / 50%)",
                  "italic 1.2em/1.4 Georgia,serif", ".x>li+li~li{margin:-1px 0 0!important}",
                  "clamp(34px,6vw,52px)"):
        assert piece in out, piece


def test_a_stylesheet_keeps_the_condition_it_was_written_under():
    """Every stylesheet moves to the head. One written for print only must not
    start applying on screen because its ``media`` attribute was left behind."""
    c = cleaned("<style media='print'>p{display:none}</style>"
                "<style media=' ALL '>h1{color:red}</style>"
                "<style media='screen and (min-width: 600px)' type='TEXT/CSS'>h2{color:blue}</style>"
                "<style type='text/x-template'>h3{color:green}</style>"
                "<style media='print}p{background:red'>h4{color:pink}</style>"
                "<style media='print;'>h5{color:pink}</style>"
                "<p>x</p>")
    assert "<style>@media print{p{display:none}}</style>" in c.html
    assert "<style>h1{color:red}</style>" in c.html
    assert "<style>@media screen and (min-width: 600px){h2{color:blue}}</style>" in c.html
    for gone in ("h3", "h4", "h5", "pink", "green"):
        assert gone not in c.html, gone
    assert c.html.count("<style>") == 4


def test_css_comments_go_and_an_angle_bracket_cannot_end_the_style_element():
    out = cleaned(page("<style>/* note */p::after{content:'a<b'}/* <b> */"
                       "@property --w{syntax:'<length>';inherits:false;initial-value:0px}</style>")).html
    assert "note" not in out and "/*" not in out.replace(clean.FONT_CSS_MARK, "")
    assert 'content:"a\\3c b"' in out and "syntax:\"\\3c length>\"" in out
    assert "<" not in "".join(audit(out).css)


def test_only_punctuation_passes_as_punctuation():
    """A backslash that escapes nothing is left out: written back, it would sit
    in front of whatever token came NEXT in the output. So are the two HTML
    comment delimiters CSS tolerates."""
    out = cleaned(page("<style><!-- p{color:\\\nred} --> q{margin:0}</style>"
                       "<p style='color:\\\nblue'>t</p>")).html
    css = "".join(audit(out).css)
    assert "\\" not in css and "<!--" not in css and "-->" not in css
    assert "q{margin:0}" in css and "red" in css
    assert "\\" not in out and "blue" in out


def test_an_in_page_reference_keeps_gradients_clips_and_markers_working():
    """``url(#id)`` names something in the SAME document: it fetches nothing.
    Without it the gradient, clip-path and marker elements the cleaner keeps
    would have no way to be used."""
    out = cleaned(
        "<svg viewBox='0 0 10 10' preserveAspectRatio='xMidYMid meet'><defs>"
        "<linearGradient id='g1' gradientUnits='userSpaceOnUse' gradientTransform='rotate(90)'>"
        "<stop offset='0' stop-color='#fff'/></linearGradient>"
        "<radialGradient id='g2'><stop offset='1' stop-opacity='.5'/></radialGradient>"
        "<clipPath id='c'><rect width='5' height='5'/></clipPath>"
        "<marker id='m' markerWidth='4' markerHeight='4' refX='2' refY='2' orient='auto'>"
        "<path d='M0 0L4 2L0 4z'/></marker></defs>"
        "<rect fill='url(#g1)' clip-path='url( \"#c\" )' width='10' height='10'/>"
        "<path d='M0 0L9 9' stroke='URL(#g2)' marker-end='url(#m)' style='fill:url(#g1)'/></svg>"
        "<style>.area{fill:url(#g1)}</style>").html
    for piece in ('viewBox="0 0 10 10"', 'preserveAspectRatio="xMidYMid meet"',
                  '<linearGradient id="g1" gradientUnits="userSpaceOnUse" gradientTransform="rotate(90)">',
                  "</linearGradient>", "<radialGradient", "</radialGradient>", '<clipPath id="c">',
                  "</clipPath>", 'markerWidth="4"', 'refX="2"', 'fill="url(#g1)"',
                  'clip-path="url(#c)"', 'stroke="url(#g2)"', 'marker-end="url(#m)"',
                  'style="fill:url(#g1)"', ".area{fill:url(#g1)}"):
        assert piece in out, piece


def test_css_nested_past_any_sane_depth_is_dropped_not_raised():
    deep = "p{color:red}" + "(" * 50_000
    result = cleaned(page(f"<style>{deep}</style><p style='{'[' * 50_000}'>t</p>"))
    assert "kept" in result.html


# ── the fixture: the shape of the operator's example ─────────────────────────

def test_the_example_shaped_entry_keeps_its_design():
    c = cleaned(FIXTURE.read_text(encoding="utf-8"))
    assert c.title == "Nuclear Stocks Thesis" and len(c.font_links) == 1
    assert "<svg" in c.html and "<table" in c.html and "--accent" in c.html
    assert c.html.count('target="_blank"') == c.html.count('rel="noopener noreferrer"') > 0
    assert c.removed.get("link") == 3 and "fonts.googleapis.com" not in c.html


def test_the_example_shaped_entry_in_detail():
    c = cleaned(FIXTURE.read_text(encoding="utf-8"))
    assert c.font_links == (
        "https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@500;700"
        "&family=Newsreader:ital,opsz,wght@0,6..72,400;1,6..72,400"
        "&family=IBM+Plex+Mono:wght@400&display=swap",)
    # Nothing in it was hostile: three links, and the wrapper's two metas are
    # replaced by the shell's own without being called a removal.
    assert c.removed == {"link": 3}
    assert c.summary.startswith("This opening paragraph is placeholder text")
    # three outside hosts and a mailto open a new tab; the in-page link does not
    assert c.html.count('target="_blank"') == 4
    assert '<a href="#chart">the exhibit above</a>' in c.html
    assert '<a href="https://example.net/third?x=1&amp;y=2" target="_blank"' in c.html
    assert '<a href="mailto:desk@example.com" target="_blank" rel="noopener noreferrer">' in c.html
    # light and dark tokens, the chart and the table, as authored
    for piece in ("@media (prefers-color-scheme: dark)", ':root[data-theme="dark"]',
                  "scroll-behavior: smooth", 'viewBox="0 0 320 120"',
                  'role="img" aria-label="Three placeholder bars and a trend line"',
                  '<path d="M20 90 L100 50 L180 70 L300 20" fill="none" stroke="var(--ink)" stroke-width="2">',
                  '<text x="80" y="114" font-size="10" font-weight="700">Beta</text>',
                  '<span class="pill" style="background:var(--accent)">one</span>',
                  '<main class="wrap">', '<section id="chart">', "<thead><tr><th>Name</th>",
                  '<hr style="margin-top:32px">', "Placeholder kicker \u00b7 fixture"):
        assert piece in c.html, piece
    # each shape written ``<rect …/>`` is its own, closed element: none of them
    # ended up inside the one before it
    for shape in ('<line x1="10" y1="100" x2="310" y2="100" stroke="var(--rule)"></line>',
                  '<rect x="20" y="60" width="40" height="40" fill="var(--accent)"></rect>',
                  '<rect x="80" y="40" width="40" height="60" fill="var(--accent)"></rect>'):
        assert shape in c.html, shape
    assert "SYNTHETIC FIXTURE" not in c.html            # a CSS comment
    assert "fonts.gstatic.com" not in c.html and "preconnect" not in c.html


def test_the_download_wrapper_is_replaced_by_one_clean_shell():
    c = cleaned(FIXTURE.read_text(encoding="utf-8"))
    for once in ("<!doctype html>", "<html", "<head>", "</head>", "<body>", "</body>",
                 "<title>", "<meta charset", "viewport"):
        assert c.html.count(once) == 1, once
    assert c.html.startswith(SHELL_OPEN + "Nuclear Stocks Thesis</title>"
                             "<style>/*blog-fonts*/</style><style>:root{color-scheme:light;")
    assert "viewport-fit" not in c.html and "utf8" not in c.html
    # every kept stylesheet, in document order: the wrapper's, then the entry's
    assert c.html.count("<style>") == 3
    assert c.html.index("color-scheme:light") < c.html.index("--accent")
    assert c.body.startswith('<main class="wrap">') and c.body.endswith("</main>")


def test_the_shell_is_exactly_the_one_the_plan_names():
    c = cleaned("<title>T</title><style>p{color:red}</style><h1>T</h1><p>x</p>")
    assert c.html == (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>T</title><style>/*blog-fonts*/</style><style>p{color:red}</style></head>"
        "<body><h1>T</h1><p>x</p></body></html>")
    assert c.body == "<h1>T</h1><p>x</p>"


def test_a_fragment_with_no_html_or_body_is_accepted():
    """What the chat sends: the artifact's source, with no document around it."""
    c = cleaned("<title>From the chat</title>\n<style>h1{color:red}</style>\n"
                "<main><h1>Heading</h1><p>First paragraph.</p></main>")
    assert c.title == "From the chat" and c.summary == "First paragraph."
    assert c.body == "<main><h1>Heading</h1><p>First paragraph.</p></main>"
    assert "<style>h1{color:red}</style></head>" in c.html
    assert "just some words" in cleaned("just some words").body


def test_a_fragment_link_is_not_opened_in_a_new_tab():
    c = cleaned("<p><a href='#notes'>down</a> <a href='https://example.com/'>out</a></p>"
                "<h2 id='notes'>Notes</h2>")
    assert '<a href="#notes">down</a>' in c.html
    assert '<a href="https://example.com/" target="_blank" rel="noopener noreferrer">out</a>' in c.html
    assert c.html.count("target=") == 1


def test_removed_counts_what_was_removed():
    c = cleaned("<script>1</script><p onclick='x()'>t</p><script>2</script>")
    assert c.removed == {"script": 2, "handler": 1}
    c = cleaned("<form action=x onsubmit=y()><script>3</script><input><input><button>go</button></form>"
                "<link rel=icon href=y><meta http-equiv=refresh content=0><iframe></iframe>"
                "<a href='javascript:1'>x</a><p style='background:url(//e.com/a)'>t</p>"
                "<svg><use href='#a'/><foreignObject><p>x</p></foreignObject></svg>")
    assert c.removed == {"form": 1, "script": 1, "input": 2, "button": 1, "handler": 1,
                         "link": 1, "meta": 1, "iframe": 1, "href": 1, "css": 1,
                         "use": 1, "foreignobject": 1}


def test_a_charset_or_viewport_meta_is_replaced_not_counted():
    c = cleaned("<meta charset='iso-8859-1'><meta name='Viewport' content='width=1'>"
                "<meta name=description content=d><p>x</p>")
    assert c.removed == {"meta": 1}
    assert c.html.count("<meta") == 2 and "iso-8859-1" not in c.html and "width=1" not in c.html


def test_cleaning_a_clean_document_removes_nothing():
    c = cleaned(FIXTURE.read_text(encoding="utf-8"))
    again = clean.clean(c.html)
    assert again.html == c.html and again.removed == {}
    assert again.title == c.title and again.summary == c.summary and again.font_links == ()


UNPARSEABLE = ["", " ", "\n\t ", "\x00", "\x00\x01\x02", "\x7f \x0b"]


@pytest.mark.parametrize("document", UNPARSEABLE)
def test_unparseable_input_never_raises(document):
    c = clean.clean(document)
    assert c.removed == {"unparseable": 1}
    assert c.html == EMPTY_SHELL and c.body == "" and c.title == "" and c.summary == ""
    assert c.font_links == ()
    audit(c.html)


@pytest.mark.parametrize("thing", [None, 5, 1.5, b"<p>x</p>", ["<p>x</p>"], {"html": "x"}, object()])
def test_something_that_is_not_text_is_unparseable(thing):
    c = clean.clean(thing)
    assert c.removed == {"unparseable": 1} and c.html == EMPTY_SHELL


def test_five_megabytes_of_angle_brackets_never_raises():
    c = clean.clean("<" * 5_000_000)
    assert isinstance(c, clean.Cleaned) and c.html.startswith(SHELL_OPEN)
    seen = _Seen()
    seen.feed(c.html[:4096])
    assert set(seen.tags) <= {"html", "head", "meta", "title", "style", "body", "p"}


# Named, because pytest puts a parameter's text in an environment variable and
# Windows refuses one longer than 32,767 characters.
#
# Each is a document and the WORDS in it: one before the hard part, one inside
# it, one after. The only two honest outcomes are that every word is still
# there, or that the document is refused. (This list used to be run through the
# audit alone, which a document that had quietly lost its second half passes.)
_FIRST, _LAST = "<p>FIRST</p>", "<p>LAST</p>"
EXHAUSTING = {
    "100,000 nested divs": _FIRST + "<div>" * 100_000 + "DEEP" + "</div>" * 100_000 + _LAST,
    "100,000 nested inline elements": _FIRST + "<b>" * 100_000 + "DEEP" + "</b>" * 100_000 + _LAST,
    "5,000 nested drawings": _FIRST + "<svg>" * 5_000 + "<text>DEEP</text>" + "</svg>" * 5_000 + _LAST,
    "2,000 nested tables": (_FIRST + "<table><tr><td>" * 2_000 + "DEEP" + "</td></tr></table>" * 2_000
                            + _LAST),
    "300 unclosed divs": _FIRST + "<div>" * 300 + "DEEP" + _LAST,
    "100,000 ampersands": _FIRST + "<p>" + "&" * 100_000 + "DEEP</p>" + _LAST,
    "20,000 repeats of one attribute": _FIRST + "<p " + "a=b " * 20_000 + ">DEEP</p>" + _LAST,
    "100,000 stray end tags": _FIRST + "<p>DEEP" + "</p>" * 100_000 + _LAST,
    "a megabyte of address": _FIRST + "<a href='" + "j" * 1_000_000 + "'>DEEP</a>" + _LAST,
    "10,000 bidi overrides in a title": (_FIRST + "<p title='" + chr(0x202E) * 10_000 + "'>DEEP</p>"
                                         + _LAST),
    "100,000 closing braces of css": _FIRST + "<style>" + "}" * 100_000 + "</style>DEEP" + _LAST,
    "50,000 open css blocks": (_FIRST + "<style>p{color:red}" + "{" * 50_000 + "</style>DEEP"
                               + _LAST),
    "20,000 css rules": (_FIRST + "<style>" + "p{color:red;background:url(//e.com/x)}" * 20_000
                         + "</style>DEEP" + _LAST),
    "100,000 comment openers in a css string": (
        _FIRST + "<style>p{content:\"" + "/*a" * 100_000 + "\"}</style>DEEP" + _LAST),
    "10,000 data attributes": (_FIRST + "<p " + " ".join(f"data-{i}=b" for i in range(10_000))
                               + ">DEEP</p>" + _LAST),
    "1,000 data attributes on each of 100 tags": (
        _FIRST + ("<p " + " ".join(f"data-{i}=b" for i in range(1_000)) + ">x</p>") * 100 + "DEEP"
        + _LAST),
    "30,000 marks": _FIRST + "<p>" + clean.FONT_CSS_MARK[:-1] * 30_000 + "/DEEP</p>" + _LAST,
    "5,000 unclosed style elements": _FIRST + "DEEP" + _LAST + "<style>" * 5_000,
    "20,000 body tags": _FIRST + "<body class='a' data-x='1'>" * 20_000 + "DEEP" + _LAST,
    "20,000 drawings each left by a paragraph": _FIRST + "<svg><p>x</p>" * 20_000 + "DEEP" + _LAST,
    "20,000 font imports": (_FIRST + "<style>"
                            + "".join(f"@import url(https://fonts.googleapis.com/css2?family=F{i});"
                                      for i in range(20_000)) + "</style>DEEP" + _LAST),
}

# Generous: the slowest of these takes a few seconds here. It is a bound on
# "does not hang", checked, not a benchmark.
EXHAUST_SECONDS = 60


@pytest.mark.parametrize("name", sorted(EXHAUSTING))
def test_a_document_built_to_exhaust_the_cleaner_does_not(name):
    started = time.perf_counter()
    c = cleaned(EXHAUSTING[name], may_refuse=True)
    took = time.perf_counter() - started
    assert took < EXHAUST_SECONDS, f"{took:.1f} s"
    if "unparseable" in c.removed:
        assert c.removed == {"unparseable": 1} and c.body == ""
    else:
        for word in ("FIRST", "DEEP", "LAST"):
            assert word in c.body, f"{word} was lost without a refusal"


def test_the_empty_shell_is_a_fixed_point():
    c = clean.clean(EMPTY_SHELL)
    assert c.html == EMPTY_SHELL and c.removed == {}


def test_a_document_that_never_settles_is_refused(monkeypatch):
    """Cleaning repeats until its output cleans to itself. One pass is not
    allowed to be enough for a document that changed, so with the passes cut to
    one the answer is the refusal, never an unproven document."""
    monkeypatch.setattr(clean, "MAX_PASSES", 1)
    c = clean.clean("<p onclick=x>t</p>")
    assert c.removed == {"unparseable": 1} and c.html == EMPTY_SHELL
    # ... while a document that is already clean needs only the one
    assert clean.clean(EMPTY_SHELL).removed == {}


def test_a_bug_inside_the_cleaner_is_a_refusal_with_a_trace(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("a bug")
    before = _degrade.counts().get("blog.clean", 0)
    monkeypatch.setattr(clean, "_href", boom)
    c = clean.clean("<a href='https://example.com/'>x</a>")
    assert c.removed == {"unparseable": 1} and c.html == EMPTY_SHELL
    assert _degrade.counts().get("blog.clean", 0) == before + 1


def test_cleaning_is_idempotent():
    corpus = [page(p) for p in HOSTILE + MORE_HOSTILE + RAW_TEXT_TRICKS]
    corpus += [page(f"<style>{css}</style>") for css in HOSTILE_CSS]
    corpus += [FIXTURE.read_text(encoding="utf-8"), "plain words", "<p>a<p>b", EMPTY_SHELL]
    for document in corpus:
        once = clean.clean(document)
        twice = clean.clean(once.html)
        assert twice.html == once.html, document[:200]
        assert clean.clean(twice.html).html == once.html


@pytest.mark.parametrize("document", [
    # unwrapping an unknown element leaves markup the parser re-nests
    "<p><foo><div>x</div></foo>tail</p>",
    "<p><center><table><tr><td>x</td></tr></table></center>tail</p>",
    "<a href='https://example.com/'><custom><a href='#x'>inner</a></custom></a>",
    "<table><form><tr><td>x</td></tr></form></table>",
    "<ul><li>a<font><li>b</font></ul>",
    "<h1><bogus><h2>x</h2></bogus></h1>",
    "<svg><p>para<svg><div>x</div></svg></p></svg>",
    "<pre>\n\nkept blank lines</pre>",
    "  <p> spaced </p>  ",
    "<dl><dt>a<dd>b<custom><dt>c</custom></dl>",
])
def test_a_document_the_parser_re_nests_still_settles(document):
    assert cleaned(document).body != ""


# ── title and summary ────────────────────────────────────────────────────────

def test_the_title_is_the_title_element_then_the_first_heading_then_nothing():
    assert cleaned("<title>From title</title><h1>From h1</h1>").title == "From title"
    assert cleaned("<h1> From\n  h1 </h1><h1>second</h1>").title == "From h1"
    assert cleaned("<title>  </title><h1>From h1</h1>").title == "From h1"
    assert cleaned("<h2>Not a title</h2><p>x</p>").title == ""
    # the first title wins: the download wrapper has none, the artifact does
    assert cleaned("<title>One</title><p>x</p><title>Two</title>").title == "One"


def test_a_title_inside_a_drawing_is_a_tooltip_not_the_documents_title():
    c = cleaned("<svg><title>Chart tooltip</title><desc>d</desc></svg><h1>Real</h1>")
    assert c.title == "Real"
    assert "<svg><title>Chart tooltip</title><desc>d</desc></svg>" in c.html


def test_the_title_is_text_and_is_escaped_in_the_shell():
    c = cleaned("<title>A &lt;b&gt; &amp; \"C\"</title><p>x</p>")
    assert c.title == 'A <b> & "C"'
    assert '<title>A &lt;b&gt; &amp; "C"</title>' in c.html


def test_the_title_and_summary_are_cut_to_the_limits():
    lim = blog_inbox.limits()
    c = cleaned(f"<title>{'T' * 5000}</title><p>{'word ' * 2000}</p>")
    assert c.title == "T" * lim["title_chars"]
    assert len(c.summary) <= lim["summary_chars"] and c.summary.startswith("word word")
    assert f"<title>{'T' * lim['title_chars']}</title>" in c.html


def test_the_summary_is_the_first_paragraph_with_words_in_it():
    c = cleaned("<h1>T</h1><p>  </p><p><script>hidden()</script>First\n  <b>real</b>   one."
                "<img alt='x'></p><p>Second.</p>")
    assert c.summary == "First real one."
    assert cleaned("<h1>T</h1><div>no paragraph</div>").summary == ""
    # words inside a drawing are labels, not the entry's opening
    assert cleaned("<svg><text>label</text></svg><p>Opening.</p>").summary == "Opening."


def test_invisible_and_control_characters_do_not_reach_the_title():
    c = cleaned("<title>\u202eevil\u200b title\x07</title><p>x</p>")
    assert c.title == "evil title"


# ── elements ─────────────────────────────────────────────────────────────────

def test_an_unknown_element_is_unwrapped_and_its_text_survives():
    c = cleaned("<center><font color=red>hi <blink>there</blink></font></center>"
                "<custom-el some='x'>custom</custom-el><article><nav>kept nav</nav></article>"
                "<path d='M0 0'/>after <tspan>span</tspan> <o:p>office</o:p>")
    assert "<article><nav>kept nav</nav></article>" in c.body
    for gone in ("<center", "<font", "<blink", "<custom", "<path", "<tspan", "<o:p", "red", "some"):
        assert gone not in c.body, gone
    words = html_lib.unescape(re.sub(r"<[^<>]*>", "", c.body))
    assert words == "hi therecustomkept navafter span office"


def test_every_kept_html_element_comes_through():
    void = {"br", "hr", "col", "wbr"}
    for tag in sorted(clean.KEPT_HTML - void - {"a"}):
        c = clean.clean(f"<{tag} class='k'>x</{tag}>")
        audit(c.html)
        assert f'<{tag} class="k">' in c.html, tag
    c = cleaned("<p>a<br>b<wbr>c</p><hr><table><colgroup><col class='c'></colgroup>"
                "<tr><td>x</td></tr></table>")
    for piece in ("a<br>b<wbr>c", "<hr>", '<col class="c">'):
        assert piece in c.html
    assert "</br>" not in c.html and "</hr>" not in c.html and "</col>" not in c.html


# The dropped elements HTML defines as having no content at all.
_HOLD_NOTHING = {"area", "base", "embed", "frame", "img", "input", "link", "meta", "source", "track"}


def test_every_dropped_element_goes_with_its_content():
    for tag in sorted(clean.DROPPED):
        c = clean.clean(f"<p>before</p><{tag} id='gone'>inner words</{tag}><p>after</p>")
        audit(c.html)                       # ... which holds the shell to its own two metas
        assert f"<{tag}" not in c.body and "gone" not in c.html, tag
        assert "before" in c.html and "after" in c.html, tag
        assert c.removed.get(tag, 0) >= 1, tag
        # ... except that an element with no content cannot take any with it:
        # the words written after an <img> are the entry's, not the image's.
        assert ("inner words" in c.html) == (tag in _HOLD_NOTHING), tag


def test_a_drawing_keeps_its_shapes_and_loses_what_can_fetch_or_run():
    c = cleaned(
        "<svg viewBox='0 0 9 9'><g transform='translate(1 1)'><circle cx='1' cy='1' r='1'/>"
        "<ellipse rx='1' ry='2'/><polyline points='0,0 1,1'/><polygon points='0,0 1,1 2,0'/>"
        "<text x='1' dy='0.35em' letter-spacing='1'>la<tspan dx='1'>bel</tspan></text></g>"
        "<a href='https://example.com/'><text>linked</text></a>"
        "<mask id='k'><rect class='in-mask'/></mask><pattern id='p'><circle class='in-pattern'/></pattern>"
        "<symbol id='s'><path class='in-symbol'/></symbol><filter id='f'><feGaussianBlur stdDeviation='2'/></filter>"
        "<metadata>meta words</metadata><image href='x.png'/><custom>loose</custom>"
        "<p>para</p><image href='y.png'/></svg>")
    for piece in ('<g transform="translate(1 1)">', '<circle cx="1" cy="1" r="1"></circle>',
                  '<ellipse rx="1" ry="2"></ellipse>', '<polyline points="0,0 1,1"></polyline>',
                  '<text x="1" dy="0.35em" letter-spacing="1">la<tspan dx="1">bel</tspan></text>',
                  # an unknown element is unwrapped where it stands ...
                  "loose</svg>",
                  # ... and a paragraph ENDS the drawing, as it does in a browser
                  # (review, C5; this used to be unwrapped into the svg as text)
                  "</svg><p>para</p>"):
        assert piece in c.html, piece
    for gone in ("linked", "example.com", "in-mask", "in-pattern", "in-symbol", "stdDeviation",
                 "meta words", "x.png", "y.png", "<image", "<mask", "<pattern", "<symbol", "<filter"):
        assert gone not in c.html, gone


def test_a_shape_never_swallows_the_shapes_after_it():
    """``<rect/>`` is how a drawing is written, and the HTML parser underneath
    has no notion of a drawing. This is the tree a parser that ignored the
    slash would hand over - each shape inside the one before - written out by
    hand so it is the same on every platform. A shape draws nothing that is
    inside it, so read literally the chart would be one line."""
    c = cleaned("<svg viewBox='0 0 9 9'><g><line x1='0' y1='0'><rect width='1' height='1'>"
                "<path d='M0 0'><title>tip</title><circle r='1'><text x='1'>label</text>"
                "</g><linearGradient id='g'><stop offset='0'><stop offset='1'></linearGradient></svg>")
    assert c.body == (
        '<svg viewBox="0 0 9 9"><g><line x1="0" y1="0"></line><rect width="1" height="1"></rect>'
        '<path d="M0 0"><title>tip</title></path><circle r="1"></circle><text x="1">label</text>'
        '</g><linearGradient id="g"><stop offset="0"></stop><stop offset="1"></stop>'
        "</linearGradient></svg>")


def test_an_element_that_holds_nothing_does_not_take_what_follows_with_it():
    """The same hazard for what is dropped: ``<use/>`` and ``<image/>`` hold
    nothing, so whatever a parser hung under one is what came after it."""
    c = cleaned("<svg><use href='#a'><rect width='1'></rect><image href='x.png'>"
                "<animate attributeName='x'><circle r='2'></circle><text>label</text></svg>"
                "<p>before<embed src='x'><img src='y'><source src='z'>after</p>")
    assert '<rect width="1"></rect>' in c.body and '<circle r="2"></circle>' in c.body
    assert "<text>label</text>" in c.body and "before" in c.body and "after" in c.body
    assert c.removed == {"use": 1, "image": 1, "animate": 1, "embed": 1, "img": 1, "source": 1}


def test_a_shape_outside_a_drawing_is_not_a_shape():
    c = cleaned("<rect width='5' height='5'></rect><text>loose</text><g><circle r='1'/></g><p>x</p>")
    assert "loose" in c.body and c.body.endswith("<p>x</p>")
    for gone in ("<rect", "<text", "<g", "<circle", "width"):
        assert gone not in c.body, gone


# ── attributes ───────────────────────────────────────────────────────────────

def _attrs_of(document, tag):
    return {name: value for t, name, value in audit(document).attrs if t == tag}


def test_attributes_are_an_allow_list():
    c = cleaned(
        "<div class='a b' id='x' title='t' lang='fr' dir='rtl' role='note' aria-label='L' "
        "data-k='v' DATA-Up='1' data-ok.1_2-3='y' hidden tabindex='1' accesskey='k' "
        "draggable='true' contenteditable is='x-y' slot='s' part='p' nonce='n' srcdoc='<p>' "
        "src='x' action='y' formaction='z' background='b' xml:lang='en' "
        "xmlns='http://www.w3.org/1999/xhtml' data-='bad' aria-='bad' href='https://example.com/' "
        "target='_blank' rel='noopener' align='center' bgcolor='red' name='n'>t</div>")
    assert _attrs_of(c.html, "div") == {
        "class": "a b", "id": "x", "title": "t", "lang": "fr", "dir": "rtl", "role": "note",
        "aria-label": "L", "data-k": "v", "data-up": "1", "data-ok.1_2-3": "y"}


def test_the_table_list_and_disclosure_attributes_survive():
    c = cleaned("<table><tr><th id='h' scope='col' colspan='2'>a</th></tr>"
                "<tr><td headers='h' rowspan='3'>b</td></tr></table>"
                "<ol start='3' reversed><li>x</li></ol><details open><summary>s</summary>d</details>"
                "<time datetime='2026-10-06'>today</time>")
    for piece in ('<th id="h" scope="col" colspan="2">', '<td headers="h" rowspan="3">',
                  '<ol start="3" reversed="">', '<details open="">',
                  '<time datetime="2026-10-06">'):
        assert piece in c.html, piece


def test_one_element_keeps_a_bounded_number_of_attributes():
    """Reading an attribute's value is a search through all the element's
    attributes, so a tag written with thousands of them is a cost per value
    read. Only names that would be KEPT are read, and only so many of those."""
    # 800 in all: under MAX_TAG_ATTRS, past which the document is refused unread
    junk = " ".join(f"junk{i}='x'" for i in range(400))
    data = " ".join(f"data-n{i}='x'" for i in range(400))
    c = cleaned(f"<p {junk} class='k' onclick='x()' {data} id='late' onmouseover='y()'>t</p>")
    kept = _attrs_of(c.html, "p")
    assert len(kept) == clean.MAX_ATTRS
    assert kept["class"] == "k" and "data-n0" in kept and "id" not in kept
    assert c.removed == {"handler": 2}          # counted however far along the tag they are


def test_attribute_values_are_escaped():
    c = cleaned("<p title='a\"b<c>d&amp;e' data-q=\"it's\">t &lt;b&gt; &amp; u</p>")
    assert '<p title="a&quot;b&lt;c&gt;d&amp;e" data-q="it\'s">t &lt;b&gt; &amp; u</p>' in c.html
    assert _attrs_of(c.html, "p") == {"title": 'a"b<c>d&e', "data-q": "it's"}


def test_a_namespace_declaration_is_not_carried_over():
    """``xmlns`` does nothing in an HTML document, and keeping it would put
    www.w3.org into every entry that draws anything.

    What this proves is that one attribute goes. It does NOT prove that no host
    is ever named outside a link: see the next test for what is guaranteed."""
    c = cleaned("<svg xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink' "
                "viewBox='0 0 1 1'><rect width='1' height='1'/></svg>")
    assert "w3.org" not in c.html and "://" not in c.html
    assert '<svg viewBox="0 0 1 1"><rect width="1" height="1"></rect></svg>' in c.html


def test_a_host_may_be_named_in_text_but_never_where_a_browser_fetches():
    """The guarantee, stated as it is: no attribute a browser FETCHES from
    survives, other than an anchor's ``href``. Words are another matter - a
    title, a class, a ``data-`` value, a font name and the text itself hold
    whatever the entry wrote, and none of it is a request."""
    c = cleaned("<p title='see https://e.com/t' class='https://e.com/c' data-src='https://e.com/d' "
                "id='https://e.com/i' lang='https://e.com/l' src='https://e.com/s' "
                "background='https://e.com/b' poster='https://e.com/p' action='https://e.com/a' "
                "style='background:url(https://e.com/u)'>visit https://e.com/words</p>"
                "<svg><text font-family='https://e.com/f' fill='url(https://e.com/g)' "
                "href='https://e.com/h'>x</text></svg>")
    said = {"t", "c", "d", "i", "l", "f", "words"}      # ... and not s, b, p, a, u, g, h
    assert set(re.findall(r"https://e\.com/(\w+)", c.html)) == said
    seen = audit(c.html)
    assert {name for _tag, name, value in seen.attrs if "e.com" in (value or "")} == {
        "title", "class", "data-src", "id", "lang", "font-family"}


KEPT_HREFS = [
    ("https://example.com/a?b=1&c=2#d", "https://example.com/a?b=1&c=2#d"),
    ("http://example.com", "http://example.com"),
    ("HTTPS://Example.com/Path", "https://Example.com/Path"),
    ("  https://example.com/x \n", "https://example.com/x"),
    # a plain space inside is encoded, not a reason to drop the link (review, C4)
    ("https://example.com/a b", "https://example.com/a%20b"),
    ("#a b", "#a%20b"),
    ("https://user@example.com:8443/x", "https://user@example.com:8443/x"),
    ("https://example.com/caf\u00e9", "https://example.com/caf\u00e9"),
    ("mailto:a@example.com", "mailto:a@example.com"),
    ("MAILTO:a@example.com?subject=Hi", "mailto:a@example.com?subject=Hi"),
    ("#top", "#top"), ("#", "#"), (" #note-1 ", "#note-1"),
]


@pytest.mark.parametrize("raw,kept", KEPT_HREFS)
def test_an_http_mailto_or_in_page_link_is_kept(raw, kept):
    c = cleaned(f'<p><a href="{html_lib.escape(raw, quote=True)}">x</a></p>')
    assert _attrs_of(c.html, "a")["href"] == kept
    assert ("target" in _attrs_of(c.html, "a")) == (not kept.startswith("#"))
    assert "href" not in c.removed


DROPPED_HREFS = [
    "javascript:alert(1)", " JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "java\nscript:alert(1)",
    "\x01javascript:alert(1)", "vbscript:x", "data:text/html,x", "data:image/svg+xml;base64,AAAA",
    "//example.com/x", "/relative", "relative.html", "?q=1", "./x", "../x", "x:y",
    "https:example.com", "https:/example.com", "https:\\\\example.com", "http://", "https:///x",
    "https://?x", "https://#x", "ftp://example.com/x", "file:///c:/x", "tel:+15555550100", "sms:1",
    "blob:https://example.com/x", "about:blank", "ws://example.com", "wss://example.com",
    "view-source:https://example.com", "intent://example.com#Intent;end", "chrome://settings",
    # a tab or a line break INSIDE an address: refused whole, not repaired (review, C4)
    "https://exam\nple.com/\tx", "https://example.com/a\tb", "https://example.com/a\r\nb",
    "https://exam\u200bple.com/", "\u202ehttps://example.com/",
    "https://example.com/\u2028x", "htt\u00adps://example.com/", "mailto:", "",
    "\uff4aavascript:alert(1)", "#a\tb", "feed:https://example.com/",
]


@pytest.mark.parametrize("raw", DROPPED_HREFS)
def test_any_other_link_loses_its_address_and_keeps_its_words(raw):
    c = cleaned(f'<p><a class="k" href="{html_lib.escape(raw, quote=True)}">words</a></p>')
    assert c.body == '<p><a class="k">words</a></p>'
    assert c.removed == {"href": 1}


def test_only_an_anchor_has_an_address():
    c = cleaned("<p href='https://example.com/'>x</p><div href='#y'>z</div>")
    assert "href" not in c.html and "target" not in c.html


# ── typeface links ───────────────────────────────────────────────────────────

GOOD_FONT = "https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap"


def test_typeface_links_are_collected_in_order_before_they_are_dropped():
    second = "https://fonts.googleapis.com/css2?family=Newsreader"
    c = cleaned(f"<head><link rel='stylesheet' href='{GOOD_FONT}'></head><body>"
                f"<link REL='preload StyleSheet' HREF=' {second} '><p>x</p>"
                f"<link rel='stylesheet' href='{GOOD_FONT}'></body>")
    assert c.font_links == (GOOD_FONT, second)
    assert c.removed == {"link": 3} and "googleapis" not in c.html


@pytest.mark.parametrize("link", [
    f"<link rel='preconnect' href='{GOOD_FONT}'>",
    f"<link href='{GOOD_FONT}'>",
    f"<link rel='stylesheet' href='{GOOD_FONT.replace('https:', 'http:')}'>",
    f"<link rel='stylesheet' href='{GOOD_FONT.replace('https:', '')}'>",
    "<link rel='stylesheet' href='https://fonts.googleapis.com/css?family=Inter'>",
    "<link rel='stylesheet' href='https://fonts.googleapis.com/css2'>",
    "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?'>",
    "<link rel='stylesheet' href='https://fonts.googleapis.com.e.com/css2?family=Inter'>",
    "<link rel='stylesheet' href='https://e.com/https://fonts.googleapis.com/css2?family=Inter'>",
    "<link rel='stylesheet' href='https://fonts.googleapis.com@e.com/css2?family=Inter'>",
    "<link rel='stylesheet' href='https://fonts.gstatic.com/css2?family=Inter'>",
    "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Inter&#10;X: y'>",
    "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=In ter'>",
    "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=caf\u00e9'>",
    f"<a rel='stylesheet' href='{GOOD_FONT}'>x</a>",
    f"<template><link rel='stylesheet' href='{GOOD_FONT}'></template>",
])
def test_nothing_else_is_offered_as_a_typeface_link(link):
    assert cleaned(link + "<p>x</p>").font_links == ()


# ── the mark fonts.py fills in ───────────────────────────────────────────────

def test_the_font_mark_appears_exactly_once_whatever_the_document_says():
    mark = clean.FONT_CSS_MARK
    c = cleaned(f"<title>{mark}</title><style>{mark}p{{color:red}}{mark}</style>"
                f"<style>{mark}</style><style>q::after{{content:'{mark}'}}</style>"
                f"<p title='{mark}' style='{mark}color:blue'>{mark} and /*blog-<b></b>fonts*/</p>"
                f"<p>/*blog-<script></script>fonts*/</p>")
    assert c.html.count(mark) == 1
    assert c.html.index(mark) == c.html.index("<style>") + len("<style>")
    assert "p{color:red}" in c.html and "color:blue" in c.html
    # the words are still on the page: only their spelling in the source moved
    assert html_lib.unescape(c.body).count(mark) == 3
    # two marks sharing a slash: respelling the first must not leave the second
    c = cleaned(f"<p>{mark[:-1]}{mark}{mark[1:]}</p><p title='{mark[:-1]}{mark}'>t</p>"
                f"<style>q::after{{content:'{mark[:-1]}{mark}'}}</style>")
    assert c.html.count(mark) == 1
    assert html_lib.unescape(c.body).count(mark[:-1] + mark) == 2


# ── two seeded storms ────────────────────────────────────────────────────────

# The first storm names another host (``e.com``) ONLY inside an attribute of one
# complete tag - a place where naming it would fetch something - so "e.com is
# nowhere in the output" is a fair thing to demand of it. Nothing in this list
# can turn the markup after it into words on the page: no bare ``<style>``,
# ``<title>``, ``<xmp>`` or ``<plaintext>`` opener, and no stylesheet that names
# a host (a ``<?`` in front of one eats its opening tag and leaves its text to be
# printed). A host that is merely PRINTED is not a reference, and a correct
# cleaner keeps it. All of that is the second storm's.
_PIECES = [
    "<p>", "</p>", "<div>", "</div>", "<a href='https://example.com/'>", "<a href='javascript:x'>",
    "</a>", "<svg>", "</svg>", "<svg viewBox='0 0 1 1'>", "<path d='M0 0'/>", "</title>",
    "</style>", "<script>", "</script>", "<table>", "<tr>", "<td>", "</table>",
    "<!--", "-->", "<![CDATA[", "]]>", "<?", "?>", "<math>", "<mtext>", "</math>", "<foreignObject>",
    "<textarea>", "</textarea>", "</xmp>", "<noscript>", "</noscript>",
    "<template>", "</template>", "<iframe>", "</iframe>", "<select>", "<option>", "<form>", "</form>",
    "<img src=x onerror=alert(1)>", "<b onclick=x>", "</b>", "<i style='background:url(//e.com/x)'>",
    "</i>", "<img src=//e.com/p.gif>", "<svg><image href=//e.com/i.png /></svg>",
    "<p style='background:u\\72l(//e.com/y)'>", "<rect fill='url(//e.com/z.svg#a)'/>",
    "@import 'x';", "/*", "*/", "{", "}", "(", ")", "'", '"',
    "\\", "<", ">", "&", "&lt;", "&#x3c;", "=", "/", " ", "\n", "text", "url(", "expression(",
    "<h1>", "</h1>", "<li>", "<ul>", "</ul>", "<pre>", "</pre>", "<br>", "<base href=//e.com/>",
    "<link rel=stylesheet href=//e.com/x.css>", "<meta http-equiv=refresh content=0>",
    "<body onload=x>", "</body>", "<html>", "</html>", "<head>", "</head>", "/*blog-fonts*/",
    "<use href='#a'/>", "<a xlink:href='javascript:1'>", "<desc>", "</desc>", "\u202e", "\x00",
]


# The second storm adds the openers that swallow what follows them: the text of
# a stylesheet, a title, or an element one parser reads as raw text and another
# as markup. A host can now legitimately come out as printed words (or as a CSS
# selector: ``e.com{}`` is the element ``e`` with the class ``com``), so this
# storm is held to the audit instead, which reads every place a host COULD be
# fetched from - each attribute, and every ``url()`` and ``@import`` - with a
# second parser.
_RAW_TEXT_PIECES = _PIECES + [
    "<style>", "<style>", "<title>", "<xmp>", "<plaintext>", "<listing>", "<noembed>",
    "<noframes>", "<svg><style>", "<svg><title>", "p{background:url(//e.com/x)}",
    "<style>p{background:url(//e.com/x)}</style>", "<style>@import '//e.com/x.css';</style>",
    "<style>@font-face{font-family:x;src:url(//e.com/f.woff2)}</style>",
    "@import url(//e.com/x.css);", "q{behavior:url(x.htc)}", "@font-face{src:url(//e.com/f)}",
    "e.com{color:red}", ";", ":", "@", "#", "!important", "url(#a)", "\\75rl(", "image-set(",
]


# Put after every storm document, so the storm reaches the cleaner instead of
# being refused at the door. A document that ENDS inside a comment, a quote, a
# tag or a raw-text element is refused (the parser never reached its end), and
# random markup ends like that half the time. This closes whichever of them is
# open and says one more word.
_STORM_END = "'\">-->]]></style></script></textarea></title></xmp></noscript></iframe><p>the end</p>"


def test_a_storm_around_raw_text_elements_always_passes_the_audit():
    rng = random.Random(61020262)
    crashes = _degrade.counts().get("blog.clean", 0)
    read = 0
    for _ in range(600):
        document = "".join(rng.choice(_RAW_TEXT_PIECES) for _ in range(rng.randint(1, 60))) + _STORM_END
        result = clean.clean(document)
        read += "unparseable" not in result.removed
        try:
            audit(result.html)
            assert clean.clean(result.html).html == result.html
            assert "<script" not in result.html.lower()
        except AssertionError as failure:
            raise AssertionError(f"{failure}\n--- input ---\n{document!r}") from None
    # a refusal that was really an exception inside the cleaner would pass every
    # check above: the empty shell is a perfectly clean document
    assert _degrade.counts().get("blog.clean", 0) == crashes
    # ... and so would a storm that was mostly refused
    assert read >= 400, f"only {read} of 600 documents were read at all"


def test_a_storm_of_broken_markup_always_comes_out_clean():
    rng = random.Random(20261006)
    crashes = _degrade.counts().get("blog.clean", 0)
    read = 0
    for _ in range(400):
        document = "".join(rng.choice(_PIECES) for _ in range(rng.randint(1, 60))) + _STORM_END
        result = clean.clean(document)
        read += "unparseable" not in result.removed
        try:
            audit(result.html)
            again = clean.clean(result.html)
            assert again.html == result.html
        except AssertionError as failure:
            raise AssertionError(f"{failure}\n--- input ---\n{document!r}") from None
        lowered = result.html.lower()
        assert "<script" not in lowered and "e.com" not in lowered.replace("example.com", "")
    assert _degrade.counts().get("blog.clean", 0) == crashes
    assert read >= 300, f"only {read} of 400 documents were read at all"
