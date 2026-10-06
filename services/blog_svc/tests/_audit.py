"""Shared check for the cleaner tests: a second parser's view of the output.

Every test that cleans a document runs its result through ``audit``, which
re-parses it with the standard library's ``html.parser`` - NOT the parser the
cleaner used - and asserts every promise the cleaner makes. The point of a
DIFFERENT parser is that a trick which slips past one does not slip past both.

The allow-lists and the fetching-function / running-property / open-url sets
here are typed out, on purpose, and never imported from ``clean``: an audit that
read the cleaner's own lists would agree with any mistake in them.

Registered with ``pytest.register_assert_rewrite`` in ``conftest.py`` so the
asserts in here keep their explanatory messages.
"""
import pathlib
import re
from html.parser import HTMLParser

import tinycss2

from services import _degrade
from services.blog_svc import clean

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "entry_like_the_example.html"

SHELL_OPEN = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
              '<meta name="viewport" content="width=device-width, initial-scale=1">'
              '<title>')
EMPTY_SHELL = SHELL_OPEN + "</title><style>/*blog-fonts*/</style></head><body></body></html>"

# The shell: the plan's, with whatever the entry's own <html> and <body> handed
# on. Every attribute is double-quoted, so "[^"]*" is one whole value.
_SHELL_RE = re.compile(
    r'<!doctype html><html lang="[^"]*"(?: [a-z][a-z0-9._-]*="[^"]*")*><head><meta charset="utf-8">'
    r'<meta name="viewport" content="width=device-width, initial-scale=1"><title>')

_SHELL_META = {("meta", "charset"), ("meta", "name"), ("meta", "content")}
_ALLOWED_TAGS = ({"html", "head", "meta", "title", "style", "body"}
                 | {t.lower() for t in clean.KEPT_HTML | clean.KEPT_SVG})
_ALLOWED_ATTRS = {a.lower() for a in clean.ATTRS}
_PREFIXED = re.compile(r"(?:aria|data)-[a-z0-9][a-z0-9._-]*")
_HREF_OK = re.compile(r"(?:https?://[^\s/?#\\]|mailto:\S|#)\S*")
_CSS_VALUED = {"style", "fill", "stroke", "clip-path", "marker-start", "marker-mid", "marker-end"}

# ⚠ Typed out here, not read from the cleaner (see the module docstring).
_ON_THE_SHELL = {"class", "style", "lang", "dir"}
# Attributes a browser FETCHES from, or runs. None may appear in any output,
# ever (an anchor's ``href`` is handled on its own, by ``_HREF_OK``). This is
# the deny side of the allow-list: a check that the allow-list has no hole an
# attacker's attribute slipped through.
_NEVER_AN_ATTRIBUTE = {
    "src", "srcset", "action", "formaction", "background", "poster", "ping", "srcdoc",
    "xlink:href", "lowsrc", "dynsrc", "longdesc", "usemap", "code", "codebase", "data",
    "manifest", "cite", "profile", "classid", "archive"}
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
        assert name not in _NEVER_AN_ATTRIBUTE, where
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
