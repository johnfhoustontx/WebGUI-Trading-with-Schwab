"""The document cleaner: an arbitrary submitted document in, a safe one out.

A Blog entry is a self-contained HTML document written in Claude Chat: its own
``<style>``, inline ``<svg>``, tables, outbound links, ``<link>``s to Google
Fonts. ``clean`` turns whatever was submitted into ONE normalised document -
a fixed shell, the entry's stylesheets, the entry's body - and reports what it
took out and which typefaces the entry asked for.

**Why this exists when it is not the security boundary.** The boundary is
structural and lives elsewhere: the entry is shown in a sandboxed frame with no
``allow-scripts``, and the edge sends a Content-Security-Policy on the document
that forbids script and every other origin (``shared.blog_inbox.ENTRY_SANDBOX``
/ ``ENTRY_CSP``). Either stops a script on its own. Cleaning still matters for
three reasons:

* **The preview has to be honest.** The operator approves what is staged. If a
  script, a tracking pixel or a remote stylesheet were left in for the browser
  to block, the draft would say "nothing removed" about a document that tried
  to do something, and the page that is served would differ from the source
  the operator read.
* **The policy has holes on purpose.** ``style-src 'unsafe-inline'`` is what
  lets an entry carry its design, and CSS can fetch (``url()``, ``@import``,
  ``image-set()``). ``img-src 'self'`` and ``font-src 'self'`` stop the request
  leaving the site; the cleaner stops it being made at all.
* **A layer that depends on a header is one misconfigured edge away from
  nothing.** The same file is served by Caddy and by the private preview.

**It is an allow-list, and the output is built, never edited.** The submitted
tree is only READ. Every byte of the result is one of: a tag name from
``KEPT_HTML`` / ``KEPT_SVG`` (spelled from this module's constants, never from
the input), an attribute name from ``ATTRS`` (same) or an ``aria-`` / ``data-``
name made of ``[a-z0-9._-]`` only, an attribute value or a text run with
``& < > "`` escaped, or CSS that was tokenised, filtered and written out again.
No comment, processing instruction, CDATA section or doctype is ever copied.
That is what makes it hold whatever the parser did: lxml's wheels
bundle libxml2 2.11.9 on Windows (measured) and 2.14 on Linux (lxml 6's release
notes; its HTML tokenizer was rewritten to follow HTML5), so the SAME bytes can
parse into different trees on the developer's box and on prod. A tree can be
surprising; it cannot contain a tag this module did not choose to write.

**It repeats until it settles.** Unwrapping an unknown element can leave markup
the parser nests differently the second time (``<p><x><div>`` becomes
``<p><div>``, which a parser closes early). So the result is cleaned again
until cleaning it changes nothing. ``clean(clean(x).html).html ==
clean(x).html`` therefore holds by construction, and a document that never
settles is refused rather than passed on unproven.

What a later step adds: ``fonts.py`` replaces ``FONT_CSS_MARK`` - which appears
in the result exactly once - with ``@font-face`` rules for the typefaces in
``Cleaned.font_links``. Those are the only ``url()``s to a file the finished
entry carries, and they are not written here.

Design: docs/plans/2026-10-06-site-blog-design.md ("Cleaning", "How an entry is
isolated").
"""
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

import lxml.html
import tinycss2
from tinycss2 import ast as css_ast
from tinycss2.serializer import serialize_string_value

from services import _degrade
from shared import blog_inbox

# The comment ``fonts.py`` replaces with the entry's ``@font-face`` rules. It is
# in the result exactly ONCE, as the whole text of the first ``<style>``: every
# other occurrence - in the entry's words, an attribute, a CSS string - is
# respelled on the way out (``_unmark``), so a plain ``str.replace`` there can
# never write typeface rules into the middle of a paragraph.
FONT_CSS_MARK = "/*blog-fonts*/"

# How many times a document may be cleaned before it must have settled. A real
# entry settles on the second pass (the first changes it, the second proves the
# result is stable); a third is the parser re-nesting what an unwrap left. Not a
# tunable: more passes would only spend longer on a document built to oscillate.
MAX_PASSES = 5

# The most attributes ONE element keeps. An element in a real entry has a
# handful; the ceiling is what keeps a tag written with tens of thousands of
# ``data-`` attributes from costing a lookup each (see ``_Pass._attrs``).
MAX_ATTRS = 64


# ── the allow-lists ──────────────────────────────────────────────────────────

# Removed WITH their content, wherever they are. Things that run, things that
# fetch, things that take input, and the three that redirect a whole document
# (``base``, ``meta``, ``link``). Images are in here on purpose: this version of
# the blog carries none, and an ``<img>`` is a request to whoever it names.
# (The ones that HAVE no content lose only themselves: see ``_DROPPED_EMPTY``.)
DROPPED = frozenset(
    "script noscript iframe frame frameset object embed applet form input button "
    "select textarea option template audio video source track canvas img picture "
    "map area base meta link dialog".split())

# Removed with their content INSIDE a drawing. The first row is the plan's: what
# runs, fetches, animates an attribute into a link, or links out of a picture.
# The second row is this module's addition: containers that draw nothing where
# they stand. Unwrapping one (the rule for any other unknown element) would
# paint its shapes onto the picture in the open - a mask's rectangle over the
# chart it was meant to clip. Nothing kept here can use a mask, a filter or a
# symbol (no such attribute survives, and ``<use>`` is gone); a ``fill`` that
# names a dropped pattern paints nothing, which is the honest result.
DROPPED_SVG = frozenset(
    "script foreignObject image use animate animateMotion animateTransform set a "
    "mask pattern symbol filter metadata".split())

# Kept, outside a drawing: text, structure and tables. Everything here has the
# ordinary content model - none is read as raw text by any parser - so escaped
# text inside one can never be re-read as markup.
KEPT_HTML = frozenset(
    "a abbr article aside b blockquote br caption cite code col colgroup dd del "
    "details div dl dt em figcaption figure footer h1 h2 h3 h4 h5 h6 header hr i "
    "ins kbd li main mark nav ol p pre q s section small span strong sub summary "
    "sup table tbody td tfoot th thead time tr u ul var wbr".split())

# Kept, inside a drawing (an ``<svg>`` and what is under it): shapes, text,
# grouping, and the paint servers a shape can name with ``url(#id)``.
KEPT_SVG = frozenset(
    "svg g path line rect circle ellipse polyline polygon text tspan title desc "
    "defs linearGradient radialGradient stop clipPath marker".split())

# Kept attributes, by name, on any kept element. ``href`` is not here: it is
# ``<a>``'s alone and has its own rule (``_href``). ``aria-*`` and ``data-*``
# are matched by ``_PREFIXED_RE``.
#
# Two differences from the plan's list, both deliberate:
# * ``xmlns`` is NOT kept. In an HTML document it does nothing (the parser puts
#   ``<svg>`` in the SVG namespace by itself), and it is the one place an honest
#   entry names a host - www.w3.org - outside a link. Without it, "no host is
#   named anywhere but an anchor's href" is a rule with no exception.
# * The last group is added. ``marker``, ``clipPath`` and the gradients are kept
#   elements, and these are the attributes that make them do anything (a marker
#   with no ``markerWidth`` / ``refX`` draws at the wrong size in the wrong
#   place), plus the handful of presentation attributes a chart uses beside the
#   plan's. Every one takes a number or a keyword; none can name a URL.
ATTRS = frozenset(
    # any element
    "class id title lang dir role style "
    # tables, lists, <time>, <details>
    "colspan rowspan scope headers datetime open start reversed "
    # drawing: geometry
    "viewBox d x y x1 y1 x2 y2 cx cy r rx ry width height transform points dx dy "
    "preserveAspectRatio "
    # drawing: paint and text
    "fill stroke stroke-width stroke-dasharray stroke-linecap stroke-linejoin "
    "opacity fill-opacity stroke-opacity text-anchor font-size font-weight "
    "font-family letter-spacing "
    # drawing: gradients, clips, markers
    "offset stop-color stop-opacity gradientUnits gradientTransform clip-path "
    "marker-start marker-end "
    # added here (see above)
    "markerWidth markerHeight markerUnits refX refY orient marker-mid "
    "clipPathUnits spreadMethod fx fy fill-rule clip-rule stroke-dashoffset "
    "stroke-miterlimit dominant-baseline font-style".split())

# Attributes whose VALUE is CSS and can therefore hold a ``url()``: the style
# attribute, and the drawing attributes that take a paint or a reference. Each
# goes through the same filter as a stylesheet.
CSS_ATTRS = frozenset("style fill stroke clip-path marker-start marker-mid marker-end".split())

# The at-rules a stylesheet keeps. Everything else goes, by name or not:
# ``@import`` and ``@namespace`` name another document, ``@font-face`` names a
# font file (the entry's typefaces are copied onto the box by ``fonts.py``, which
# writes its own), ``@charset`` cannot be honoured in a ``<style>``.
CSS_AT_RULES = frozenset(
    "media supports container layer keyframes -webkit-keyframes -moz-keyframes "
    "page property scope starting-style".split())

# Functions that fetch what they are given - including a bare STRING, which is
# how ``image-set("https://...")`` reaches another host with no ``url(`` in
# sight - and the one that ran script in old Internet Explorer. Replaced by
# ``none``. (``url`` itself is handled where it is found.)
CSS_FETCH_FUNCTIONS = frozenset(
    "image image-set -webkit-image-set -moz-image-set cross-fade -webkit-cross-fade "
    "element -moz-element src expression".split())

# Properties that load and run a file. The whole declaration goes.
CSS_DROPPED_PROPERTIES = frozenset({"behavior", "-ms-behavior", "-moz-binding"})

# The typeface stylesheets ``fonts.py`` will follow: Google's css2 endpoint over
# https, and nothing in the address that is not printable ASCII.
_FONT_LINK_RE = re.compile(r"^https://fonts\.googleapis\.com/css2\?[\x21-\x7e]+\Z")

# What the parser lower-cased, back to the spelling a reader expects. A browser
# repairs ``viewbox`` and ``lineargradient`` by itself; this just keeps the
# staged file looking like the source.
_HTML_NAME = {name: name for name in KEPT_HTML}
_SVG_NAME = {name.lower(): name for name in KEPT_SVG}
_ATTR_NAME = {name.lower(): name for name in ATTRS}
_DROPPED_SVG = frozenset(name.lower() for name in DROPPED_SVG)
_PREFIXED_RE = re.compile(r"^(?:aria|data)-[a-z0-9][a-z0-9._-]*\Z")
_VOID = frozenset({"br", "hr", "col", "wbr"})
# The dropped elements that have no content in HTML. libxml2 does not agree
# with HTML about all of them: the 2.11 parser treats ``<embed>``, ``<source>``
# and ``<track>`` as containers, so ``<embed src=x><p>the rest of the entry``
# arrives with the rest of the entry INSIDE the embed. Dropping one of these
# "with its content" would take the entry with it; a browser, which knows the
# element is empty, would have shown it.
_DROPPED_EMPTY = frozenset(
    "area base embed frame img input link meta source track".split())
# The same question inside a drawing, where an element is written ``<rect/>``.
# libxml2's HTML parser has no notion of a drawing; whether it honours that
# slash is its own business (2.11 does). If one did not, every shape after the
# first would arrive nested INSIDE the first - and a shape draws nothing that is
# inside it, so the chart would be gone. Neither list below depends on the
# answer: a shape keeps only its own tooltip (``title`` / ``desc``) inside it,
# and anything else found under one is read as coming after it; the dropped
# elements that never hold anything lose only themselves.
_SVG_SHAPES = frozenset("path line rect circle ellipse polyline polygon stop".split())
_SVG_OWN = frozenset({"title", "desc"})
_SVG_DROPPED_EMPTY = frozenset(
    "image use animate animatemotion animatetransform set".split())
# The two ``<meta>``s the shell writes itself. One in the submitted document is
# REPLACED by the shell's, so it is not reported as something taken out.
_SHELL_META_NAMES = frozenset({"viewport"})


# ── text ─────────────────────────────────────────────────────────────────────

# Control characters with no business in a document. Tab, newline and carriage
# return stay. Removed from the source before it is parsed (a NUL reads
# differently in every parser) and again from everything written.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# How the mark is spelled when the ENTRY contains it: the same characters to a
# reader, a different string to ``str.replace``. BOTH ends are respelled. With
# only the front changed, ``/*blog-fonts*/*blog-fonts*/`` - two marks sharing a
# slash - came out still holding one: the back of the first plus the rest of
# the second.
_MARK_IN_HTML = "/&#42;" + FONT_CSS_MARK[2:-1] + "&#47;"
_MARK_IN_CSS = "/\\*" + FONT_CSS_MARK[2:-1] + "\\/"


def _respell(text, spelling) -> str:
    """``text`` with every ``FONT_CSS_MARK`` in it written as ``spelling``."""
    while FONT_CSS_MARK in text:
        text = text.replace(FONT_CSS_MARK, spelling)
    return text


def _text(raw) -> str:
    """``raw`` as a text run: no control characters, ``& < >`` escaped."""
    raw = _CONTROL_RE.sub("", raw)
    return raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _attr(raw) -> str:
    """``raw`` as the inside of a double-quoted attribute value."""
    return _text(raw).replace('"', "&quot;")


def _unmark(markup) -> str:
    """``markup`` with any ``FONT_CSS_MARK`` in it respelled. Run on finished
    markup, not on one text run: the entry can split the mark across a removed
    element (``/*blog-<script></script>fonts*/``) and have it join up."""
    return _respell(markup, _MARK_IN_HTML)


def _one_line(raw) -> str:
    return " ".join(raw.split())


# ── links ────────────────────────────────────────────────────────────────────

# What a browser trims from both ends of an address (C0 controls and space) and
# what it deletes from the middle (tab and newlines). Both are done BEFORE the
# scheme is read, which is the whole trick behind ``java\tscript:``.
_URL_EDGE = "".join(chr(code) for code in range(0x21))
_URL_SKIP_RE = re.compile(r"[\t\n\r]")
# An address the entry may link to: a web page with a host, or a mailbox.
# ``https:path`` and ``https:/path`` are refused - with no ``//host`` a browser
# resolves them against the page's own address.
_WEB_RE = re.compile(r"^(https?)://(?=[^/?#\\])", re.I)
_MAIL_RE = re.compile(r"^(mailto):(?=.)", re.I)
# Categories no address has inside it: controls, invisible format characters
# (zero-width, bidi overrides), separators, surrogates.
_NOT_IN_URL = frozenset({"Cc", "Cf", "Cs", "Zs", "Zl", "Zp"})


def _href(raw) -> str | None:
    """The address an ``<a>`` may keep, normalised, or ``None`` to remove it.

    Kept: ``http://host…``, ``https://host…``, ``mailto:…`` and ``#fragment``.
    Everything else is refused, which covers every other scheme (``javascript:``,
    ``data:``, ``vbscript:``, ``file:``, ``blob:`` …) and every relative address:
    an entry is a single document, so a relative link points at nothing of its
    own. Read the way a browser reads it - ends trimmed, tabs and newlines
    deleted - so the scheme tested is the scheme that would be followed."""
    if not isinstance(raw, str):
        return None
    url = _URL_SKIP_RE.sub("", raw.strip(_URL_EDGE))
    if not url or any(unicodedata.category(ch) in _NOT_IN_URL for ch in url):
        return None
    if url.startswith("#"):
        return url
    found = _WEB_RE.match(url) or _MAIL_RE.match(url)
    if found is None:
        return None
    scheme = found.group(1)
    return scheme.lower() + url[len(scheme):]


# ── CSS ──────────────────────────────────────────────────────────────────────
#
# CSS is tokenised by tinycss2 rather than searched with patterns, because every
# pattern for "a url" has a spelling it misses: ``u\72l(``, ``URL( "x" )``,
# ``url(a\29 b)``, a comment in the middle. The tokenizer DECODES escapes, so
# ``u\72l(x)`` arrives here as a url token like any other and ``@\69mport`` as
# the at-keyword ``import``. What is written back is the decoded token,
# re-serialised canonically - so what a browser's tokenizer reads is what this
# one read, with no escape left to mean something else.
#
# Then the written text is searched anyway (``_css_unsafe``): raw and with its
# escapes decoded, with comments in and out. That second look does not share the
# tokenizer's opinion of where a string or a comment ends; if the two ever
# disagree about a stylesheet, the stylesheet goes.

# An in-page reference: ``url(#gradient)``. It names something in the SAME
# document and fetches nothing, and it is the only way a shape can use the
# gradient, clip-path and marker elements kept above. Any other ``url()``
# becomes ``none``.
_LOCAL_REF_RE = re.compile(r"^#[A-Za-z0-9_-]{1,64}\Z")
_LOCAL_URL_RE = re.compile(r"(?<![a-z0-9_-])url\(#[a-z0-9_-]{1,64}\)")

# What must not be in a piece of CSS, as text. Used twice: on a single token's
# decoded value (a class named ``url\(x\)`` is dropped rather than argued with),
# and on the finished text.
_CSS_SUSPECT_RE = re.compile(
    # No word boundary in front of these on purpose: a function NAMED ``texturl``
    # fetches nothing, but nothing honest is named that either, and "there is no
    # ``url(`` in the result except an in-page one" is a rule a reader can check.
    r"url\(|expression\(|image-set\("
    r"|@\s*(?:import|font-face|namespace|charset)"
    r"|(?:java|vb)script\s*:"
    r"|-moz-binding"
    r"|(?<![a-z0-9_-])behavior\s*:")
_CSS_ESCAPE_RE = re.compile(r"\\(?:([0-9a-f]{1,6})[ \t\n\r\f]?|(.))", re.I | re.S)

_DROP_RULE = object()        # "this at-rule or declaration goes, to its end"
_CSS_REPLACEMENT = chr(0xFFFD)   # what CSS reads an impossible escape as

# The punctuation a stylesheet is written with: single characters, and the
# two-character attribute-selector and column operators.
_CSS_PUNCTUATION = frozenset("!$%&*+,-./:;=>?@^_|~#") | {"~=", "|=", "^=", "$=", "*=", "||"}


def _css_suspect(lowered) -> bool:
    return _CSS_SUSPECT_RE.search(_LOCAL_URL_RE.sub("", lowered)) is not None


def _css_unescape(match) -> str:
    if match.group(1) is None:
        return match.group(2)
    point = int(match.group(1), 16)
    if point == 0 or point > 0x10FFFF or 0xD800 <= point <= 0xDFFF:
        return _CSS_REPLACEMENT
    return chr(point)


def _css_uncommented(text) -> str:
    """``text`` with every ``/* … */`` taken out, an unclosed one to the end.

    By ``str.find`` and not a pattern: ``/\\*.*?\\*/`` rescans to the end of the
    text from every ``/*`` that is never closed, and a string of 150,000 of
    them is a document anyone can write."""
    kept, at = [], 0
    while True:
        start = text.find("/*", at)
        end = text.find("*/", start + 2) if start >= 0 else -1
        if end < 0:
            kept.append(text[at:] if start < 0 else text[at:start])
            return "".join(kept)
        kept.append(text[at:start])
        at = end + 2


def _css_unsafe(css) -> bool:
    """The second look at finished CSS. True means do not use it."""
    lowered = css.lower()
    for text in (lowered, _CSS_ESCAPE_RE.sub(_css_unescape, lowered).lower()):
        if _css_suspect(text) or _css_suspect(_css_uncommented(text)):
            return True
    return "<" in css


def _css_none(node):
    return css_ast.IdentToken(node.source_line, node.source_column, "none")


def _css_string(node, value):
    return css_ast.StringToken(node.source_line, node.source_column, value,
                               '"' + serialize_string_value(value) + '"')


def _css_url(node, value, hits):
    """What a ``url(…)`` becomes: itself when it is an in-page reference,
    ``none`` (and one more thing removed) otherwise."""
    if isinstance(value, str) and _LOCAL_REF_RE.match(value):
        return css_ast.URLToken(node.source_line, node.source_column, value, f"url({value})")
    hits["css"] += 1
    return _css_none(node)


def _css_function(node, hits):
    name = node.lower_name
    if name == "url":                       # url("quoted"): a function, not a url token
        inside = [arg for arg in node.arguments if arg.type != "whitespace"]
        quoted = inside[0].value if len(inside) == 1 and inside[0].type == "string" else None
        return _css_url(node, quoted, hits)
    if name in CSS_FETCH_FUNCTIONS or _css_suspect(name + "("):
        hits["css"] += 1
        return _css_none(node)
    node.arguments = _css_tokens(node.arguments, hits)
    return node


def _css_token(node, hits):
    """One token: itself, a replacement, ``None`` to leave it out, or
    ``_DROP_RULE`` to leave out everything up to the end of its rule."""
    kind = node.type
    if kind in ("whitespace", "number", "percentage", "unicode-range"):
        return node
    if kind == "literal":
        # Punctuation only. That leaves out "<!--" and "-->" (legal, useless,
        # and one has a "<" in it), a stray backslash (written out before the
        # NEXT token it would escape it into something else), and any control
        # character.
        return node if node.value in _CSS_PUNCTUATION else None
    if kind == "ident":
        if node.lower_value in CSS_DROPPED_PROPERTIES:
            hits["css"] += 1
            return _DROP_RULE
        return None if _css_suspect(node.lower_value) else node
    if kind == "at-keyword":
        if node.lower_value in CSS_AT_RULES:
            return node
        hits["css"] += 1
        return _DROP_RULE
    if kind == "hash":
        return None if _css_suspect(node.value.lower()) else node
    if kind == "dimension":
        return None if _css_suspect(node.lower_unit) else node
    if kind == "string":
        return _css_string(node, "" if _css_suspect(node.value.lower()) else node.value)
    if kind == "url":
        return _css_url(node, node.value, hits)
    if kind == "function":
        return _css_function(node, hits)
    if kind in ("() block", "[] block", "{} block"):
        node.content = _css_tokens(node.content, hits)
        return node
    if kind == "error" and node.kind == "bad-url":
        return _css_url(node, None, hits)
    return None                             # any other error, a comment, a kind not known here


def _css_tokens(nodes, hits) -> list:
    """``nodes`` filtered, blocks and function arguments included.

    A dropped at-rule or declaration ends at its ``;`` or after its ``{}``
    block, whichever comes first - the two ways a rule can end. At the end of
    the list it simply ends, so an unfinished ``@import url(x`` takes the rest
    of its block with it and nothing else."""
    kept, dropping = [], False
    for node in nodes:
        if dropping:
            dropping = not (node.type == "{} block"
                            or (node.type == "literal" and node.value == ";"))
            continue
        result = _css_token(node, hits)
        if result is _DROP_RULE:
            dropping = True
        elif result is not None:
            kept.append(result)
    return kept


def _css(source, hits) -> str:
    """``source`` - a stylesheet, a ``style`` attribute or a paint attribute -
    as CSS that fetches nothing. ``""`` when nothing usable is left.

    ``hits["css"]`` counts what was taken out. Anything that raises on the way
    (a block nested past the interpreter's depth is the known one) and anything
    the second look refuses costs the WHOLE piece: a stylesheet this cannot
    vouch for is not served."""
    if not source or not source.strip():
        return ""
    try:
        nodes = tinycss2.parse_component_value_list(source, skip_comments=True)
        css = tinycss2.serialize(_css_tokens(nodes, hits)).strip()
    except Exception:
        hits["css"] += 1
        return ""
    # A "<" can only be left inside a string by now. Spelled as an escape it is
    # the same string to CSS and cannot end the <style> element it sits in.
    css = _respell(css.replace("<", "\\3c "), _MARK_IN_CSS)
    if _css_unsafe(css):
        hits["css"] += 1
        return ""
    return css


# ── one pass over one document ───────────────────────────────────────────────

_NODE, _TEXT, _CLOSE = 0, 1, 2


def _handlers(el) -> int:
    """How many event-handler attributes ``el`` carries (``onclick`` …)."""
    return sum(1 for name in el.attrib if name.lower().startswith("on"))


def _shell_meta(el) -> bool:
    """Whether a ``<meta>`` is one of the two the shell writes itself."""
    if el.get("http-equiv") is not None:
        return False
    name = (el.get("name") or "").strip().lower()
    return el.get("charset") is not None or name in _SHELL_META_NAMES


class _Pass:
    """One reading of one parsed document, into the pieces of a clean one.

    The tree is walked with an explicit stack, not recursion: the depth of a
    submitted document is the submitter's choice."""

    def __init__(self):
        self.out = []                 # the body, as finished markup
        self.styles = []              # cleaned stylesheets, in document order
        self.removed = Counter()
        self.font_links = []
        self.title = ""               # the first <title> outside a drawing, with words in it
        self.heading = ""             # the first <h1> with words in it
        self.opening = ""             # the first <p> with words in it
        self._open = {}               # "h1" / "p" -> the text seen since it opened

    def walk(self, root) -> None:
        todo = [(_NODE, root, False)]
        while todo:
            what, item, flag = todo.pop()
            if what == _TEXT:
                self._say(item)
            elif what == _CLOSE:
                self._close(item, flag)
            else:
                self._node(item, flag, todo)

    def _say(self, raw) -> None:
        self.out.append(_text(raw))
        for heard in self._open.values():
            heard.append(raw)

    def _close(self, name, capture) -> None:
        self.out.append(f"</{name}>")
        if capture:
            words = _one_line("".join(self._open.pop(capture)))
            if capture == "h1":
                self.heading = self.heading or words
            else:
                self.opening = self.opening or words

    def _node(self, el, in_svg, todo) -> None:
        # Whatever happens to the element, the text after it is its parent's.
        if el.tail:
            todo.append((_TEXT, el.tail, False))
        tag = el.tag
        if not isinstance(tag, str):          # a comment or a processing instruction
            return
        tag = tag.lower()
        if tag == "style":                    # wherever it is, drawings included
            self.removed["handler"] += _handlers(el)
            self._style(el)
        elif tag == "title" and not in_svg:   # the document's; a drawing's is a tooltip
            self.removed["handler"] += _handlers(el)
            self.title = self.title or _one_line("".join(el.itertext()))
        elif tag in _DROPPED_EMPTY or (in_svg and tag in _SVG_DROPPED_EMPTY):
            # An element that HAS no content: what the parser hung under it is
            # really what came after it, and is read as that.
            if tag == "link":
                self._font_link(el)
            self._count(el, tag, top=True)
            self._inside(el, in_svg, todo)
        elif tag in DROPPED or (in_svg and tag in _DROPPED_SVG):
            self._drop(el, tag)
        else:
            self.removed["handler"] += _handlers(el)
            self._keep_or_unwrap(el, tag, in_svg, todo)

    def _style(self, el) -> None:
        """Collect one ``<style>``. Every stylesheet is moved to the head, so the
        two attributes that decide WHETHER it applies have to travel with it:
        left behind, ``<style media="print">p{display:none}</style>`` would
        hide every paragraph on screen."""
        kind = (el.get("type") or "").strip().lower()
        if kind not in ("", "text/css"):      # a template or data block: not CSS to a browser
            return
        css = _css("".join(el.itertext()), self.removed)
        media = _one_line(el.get("media") or "")
        if css and media.lower() not in ("", "all"):
            # The query is filtered like any CSS, and must still be a query: with
            # a brace or a semicolon in it, it could close the block it is about
            # to open. A browser applies a stylesheet with a bad query to
            # nothing, so that is what it becomes.
            query = _css(media, self.removed)
            usable = query and not set(query) & set("{};")
            css = _css(f"@media {query}{{{css}}}", Counter()) if usable else ""
        if css:
            self.styles.append(css)

    def _drop(self, el, tag) -> None:
        """Count ``el`` and everything droppable under it, and write nothing."""
        self._count(el, tag, top=True)
        for node in el.iterdescendants():
            name = node.tag
            if isinstance(name, str):
                self._count(node, name.lower(), top=False)

    def _count(self, el, tag, *, top) -> None:
        """One removed element, and its event handlers, into ``removed``."""
        self.removed["handler"] += _handlers(el)
        # Under a dropped element only the unambiguous names are counted: an
        # <a> inside a dropped <form> is not "a link taken out of a drawing".
        listed = tag in DROPPED or (tag in _DROPPED_SVG and tag != "a")
        if (top or listed) and not (tag == "meta" and _shell_meta(el)):
            self.removed[tag] += 1

    def _font_link(self, el) -> None:
        rel = (el.get("rel") or "").lower().split()
        href = (el.get("href") or "").strip()
        if "stylesheet" in rel and _FONT_LINK_RE.match(href) and href not in self.font_links:
            self.font_links.append(href)

    def _keep_or_unwrap(self, el, tag, in_svg, todo) -> None:
        inside = in_svg or tag == "svg"
        name = (_SVG_NAME if inside else _HTML_NAME).get(tag)
        if name is not None:
            capture = None
            if not inside and tag in ("h1", "p") and tag not in self._open:
                if not (self.heading if tag == "h1" else self.opening):
                    self._open[tag] = []
                    capture = tag
            self.out.append(f"<{name}{self._attrs(el, tag, inside)}>")
            if inside and tag in _SVG_SHAPES:
                self._shape(el, name, todo)
                return
            if tag not in _VOID:
                todo.append((_CLOSE, name, capture))
        # Kept or unwrapped, what is inside is read next. (``html``, ``head``
        # and ``body`` are unwrapped like any other element not on a list.)
        self._inside(el, inside, todo)

    @staticmethod
    def _shape(el, name, todo) -> None:
        """Queue a shape's content: its tooltip inside it, the rest after it
        (see ``_SVG_SHAPES``). Queued last-first, as the stack reads it."""
        own, rest = [], []
        for child in el:
            mine = isinstance(child.tag, str) and child.tag.lower() in _SVG_OWN
            (own if mine else rest).append(child)
        for child in reversed(rest):
            todo.append((_NODE, child, True))
        if el.text:
            todo.append((_TEXT, el.text, False))
        todo.append((_CLOSE, name, None))
        for child in reversed(own):
            todo.append((_NODE, child, True))

    @staticmethod
    def _inside(el, in_svg, todo) -> None:
        """Queue ``el``'s text and children, to be read in document order."""
        for child in reversed(el):
            todo.append((_NODE, child, in_svg))
        if el.text:
            todo.append((_TEXT, el.text, False))

    def _attrs(self, el, tag, in_svg) -> str:
        kept, outbound, read = [], False, 0
        # NAMES first, values only for the names that will be kept. Listing an
        # element's names is one walk; reading a value is a search through all
        # of them, so ``attrib.items()`` on a tag carrying 20,000 attributes
        # took over a second, and four times that for twice as many.
        for raw_name in el.attrib:
            name = raw_name.lower()
            spelled = "href" if name == "href" else _ATTR_NAME.get(name)
            if spelled is None and _PREFIXED_RE.match(name):
                spelled = name
            if spelled is None or (name == "href" and (tag != "a" or in_svg)):
                continue
            if read == MAX_ATTRS:
                break
            read += 1
            value = el.get(raw_name) or ""
            if name == "href":
                value = _href(value)
                if value is None:
                    self.removed["href"] += 1
                    continue
                outbound = not value.startswith("#")
            elif name in CSS_ATTRS:
                value = _css(value, self.removed)
                if not value:
                    continue
            kept.append((spelled, value))
        if outbound:
            # A new tab, because the entry is framed and the frame cannot
            # navigate the page around it; no opener, so the new tab cannot
            # reach back. Written here, so what the entry asked for is moot.
            kept += [("target", "_blank"), ("rel", "noopener noreferrer")]
        return "".join(f' {name}="{_attr(value)}"' for name, value in kept)


# ── the result ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Cleaned:
    html: str            # a complete document: doctype, head, body
    title: str           # <title>, else first <h1>, else ""
    summary: str         # first <p> text, cut to limits()["summary_chars"]
    removed: dict        # {"script": 2, "form": 1, "handler": 3, "link": 3, ...}
    font_links: tuple    # the fonts.googleapis.com/css2 hrefs found, in order

    @property
    def body(self) -> str:
        """What is between ``<body>`` and ``</body>``. ``""`` means the entry
        had nothing in it once cleaned (or could not be read at all)."""
        return self.html.partition("<body>")[2].rpartition("</body>")[0]


def _document(title, styles, body) -> str:
    """The one shell every entry gets. ``title`` is text; ``styles`` and
    ``body`` are finished CSS and finished markup."""
    sheets = "".join(f"<style>{css}</style>" for css in styles)
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{_unmark(_text(title))}</title><style>{FONT_CSS_MARK}</style>"
            f"{sheets}</head><body>{body}</body></html>")


def _refused() -> Cleaned:
    """The answer for something that is not a readable document."""
    return Cleaned(_document("", (), ""), "", "", {"unparseable": 1}, ())


def _parse(source):
    """``source`` as a tree, or ``None`` when there is nothing to read.

    Handed over as UTF-8 bytes with the encoding stated, so a ``<meta charset>``
    or an XML declaration inside the document cannot have it re-read as
    something else, and a character that cannot be encoded becomes ``?``
    instead of an error. Comments and processing instructions are discarded by
    the parser; the walk would skip them anyway."""
    source = _CONTROL_RE.sub("", source)
    if not source.strip():
        return None
    parser = lxml.html.HTMLParser(encoding="utf-8", remove_comments=True, remove_pis=True,
                                  no_network=True, recover=True)
    try:
        return lxml.html.document_fromstring(source.encode("utf-8", "replace"), parser=parser)
    except Exception:           # lxml's "Document is empty" and its syntax errors
        return None


def _once(source) -> Cleaned | None:
    """One pass: parse, read, rebuild. ``None`` when it cannot be parsed."""
    root = _parse(source)
    if root is None:
        return None
    seen = _Pass()
    seen.walk(root)
    # ``clean_fields`` is what the service runs on a title and a summary anyway:
    # one line each, invisible characters out, cut to ``[limits] title_chars``
    # and ``summary_chars``. Used here so the two cannot disagree.
    fields = blog_inbox.clean_fields({"title": seen.title or seen.heading,
                                      "summary": seen.opening})
    body = _unmark("".join(seen.out).strip())
    removed = {name: count for name, count in seen.removed.items() if count}
    return Cleaned(_document(fields["title"], seen.styles, body), fields["title"],
                   fields["summary"], removed, tuple(seen.font_links))


def _settle(source) -> Cleaned:
    """Clean until cleaning changes nothing, or refuse."""
    removed, font_links = Counter(), None
    for _ in range(MAX_PASSES):
        result = _once(source)
        if result is None:
            return _refused()
        removed.update(result.removed)
        if font_links is None:              # only the first pass still has the links
            font_links = result.font_links
        if result.html == source:
            return Cleaned(result.html, result.title, result.summary, dict(removed), font_links)
        source = result.html
    return _refused()


def clean(html: str) -> Cleaned:
    """``html`` as a safe, normalised document. NEVER raises.

    Anything that is not text, text with nothing in it, text the parser cannot
    read and a document that will not settle all come back as an empty shell
    with ``removed == {"unparseable": 1}``. So does a bug in here - and that one
    is counted for ``/health`` (``blog.clean``), because a refusal that is
    really a crash should not look like a bad upload.

    The size of ``html`` is the caller's to bound (``blog_inbox.html_ok``)."""
    if not isinstance(html, str):
        return _refused()
    try:
        return _settle(html)
    except Exception:
        _degrade.degraded("blog.clean")
        return _refused()
