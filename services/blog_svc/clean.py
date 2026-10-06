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

**A change it makes is right, or counted, or a refusal - never silent.** The
operator publishes what the preview shows, and a preview cannot show what is
missing on a screen the operator is not looking at. So: an honest construct is
kept with its meaning (a ``<`` in a media query is a comparison; a class named
``.javascript`` is a class); whatever is taken out of a stylesheet adds to
``removed["css"]``; and a document the parser stopped reading part-way, or
that would cost it minutes, comes back as ``{"unparseable": 1}`` with nothing
in it rather than as its first half.

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
import secrets
import unicodedata
from collections import Counter
from dataclasses import dataclass
from html import unescape

import lxml.html
import tinycss2
from lxml import etree
from tinycss2 import ast as css_ast
from tinycss2.serializer import serialize_identifier, serialize_string_value

from services import _degrade
from shared import blog_inbox

# The comment ``fonts.py`` replaces with the entry's ``@font-face`` rules. It is
# in the result exactly ONCE, as the whole text of the first ``<style>``: every
# other occurrence - in the entry's words, an attribute, a CSS string - is
# respelled on the way out (``_unmark``), so a plain ``str.replace`` there can
# never write typeface rules into the middle of a paragraph.
FONT_CSS_MARK = "/*blog-fonts*/"

# ── three bounds, and why none of them is a config key ───────────────────────
#
# ``config/blog.toml`` holds what an operator might reasonably tune. These are
# not that: each is the point past which a document stops being an entry and
# becomes a cost, and each is far above anything an entry contains. Raising one
# buys nothing an entry needs; lowering one only starts refusing real entries.

# How many times a document may be cleaned before it must have settled. A real
# entry settles on the second pass (the first changes it, the second proves the
# result is stable); a third is the parser re-nesting what an unwrap left. A
# constant because the only thing more passes buy is longer spent on a document
# built to oscillate.
MAX_PASSES = 5

# The most attributes ONE element keeps (and the most the shell's ``<html>`` or
# ``<body>`` takes over). An element in a real entry has a handful. A constant
# because it exists to bound a cost - reading a value is a search through all
# of an element's attributes (see ``_Pass._attrs``) - not to express a taste.
MAX_ATTRS = 64

# The most attribute names ONE tag may be written with before the document is
# refused unread. libxml2 builds a tag's attribute list in quadratic time: one
# tag with 85,000 distinct names - it fits in the 512 KB limit - held the parser
# for 77 seconds, and ``MAX_ATTRS`` cannot help because the time is spent before
# this module sees a tree. At 1,024 the worst a full-size document can cost is
# well under a second, and that is some thirty times more attributes than any
# honest tag has. A constant for the same reason as the other two; if it ever
# needs moving, it belongs in ``[limits]`` beside ``max_html_kb``.
MAX_TAG_ATTRS = 1024


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
# What this list guarantees: no attribute a browser FETCHES from survives,
# other than an anchor's ``href``. It does not mean no host is ever NAMED
# outside a link - ``title``, ``class``, ``data-*`` and ``font-family`` hold
# whatever text the entry put there, and text is inert.
#
# Two differences from the plan's list, both deliberate:
# * ``xmlns`` is NOT kept. In an HTML document it does nothing (the parser puts
#   ``<svg>`` in the SVG namespace by itself), and keeping it would put
#   www.w3.org into every entry that draws anything.
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

# What the entry's own ``<html>`` and ``<body>`` tags hand on to the shell's: the
# hooks a stylesheet selects on (``body.wide``, ``:root[data-theme="dark"]``),
# the language and direction, and a ``style``. ``data-*`` is matched by pattern.
SHELL_ATTRS = frozenset("class style lang dir".split())

# Attributes whose VALUE is CSS and can therefore hold a ``url()``: the style
# attribute, and the drawing attributes that take a paint or a reference. Each
# goes through the same filter as a stylesheet.
CSS_ATTRS = frozenset("style fill stroke clip-path marker-start marker-mid marker-end".split())

# The at-rules a stylesheet keeps: the ones that can neither fetch nor run
# anything. Row by row -
# * conditions and structure: they decide WHICH rules apply, nothing more.
# * ``@counter-style``: how a list is numbered. Its ``symbols`` may by the
#   standard be images; a ``url()`` there meets the same end as any other.
# * ``@font-feature-values`` and the six blocks inside one: names for features
#   of a font already loaded. ``@font-palette-values``: colours for one.
# * ``@view-transition``: whether a same-site navigation animates.
#   ``@position-try``: fallback positions for an anchored element.
# Everything else goes, by name or not: ``@import`` and ``@namespace`` name
# another document, ``@font-face`` names a font file (the entry's typefaces are
# copied onto the box by ``fonts.py``, which writes its own), ``@charset``
# cannot be honoured in a ``<style>``.
CSS_AT_RULES = frozenset(
    "media supports container layer keyframes -webkit-keyframes -moz-keyframes "
    "page property scope starting-style "
    "counter-style "
    "font-feature-values swash styleset stylistic character-variant ornaments "
    "annotation font-palette-values "
    "view-transition position-try".split())

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
# https, with a query made only of what such a query is written with - letters,
# digits and ``: ; , @ + & = . _ % -``. The address leaves this module for a
# fetch and may later be shown on a page, so it carries no quote, bracket,
# space or slash.
_FONT_LINK_RE = re.compile(r"^https://fonts\.googleapis\.com/css2\?[A-Za-z0-9:;,@+&=._%-]+\Z")

# What the parser lower-cased, back to the spelling a reader expects. A browser
# repairs ``viewbox`` and ``lineargradient`` by itself; this just keeps the
# staged file looking like the source.
_HTML_NAME = {name: name for name in KEPT_HTML}
_SVG_NAME = {name.lower(): name for name in KEPT_SVG}
_ATTR_NAME = {name.lower(): name for name in ATTRS}
_DROPPED_SVG = frozenset(name.lower() for name in DROPPED_SVG)
_PREFIXED_RE = re.compile(r"^(?:aria|data)-[a-z0-9][a-z0-9._-]*\Z")
_DATA_RE = re.compile(r"^data-[a-z0-9][a-z0-9._-]*\Z")
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
# The start tags that END a drawing: the HTML standard's list (its "in foreign
# content" rules, less ``font``, which only counts with certain attributes). A
# browser that meets ``<p>`` inside an ``<svg>`` closes the svg and carries on
# in HTML. libxml2 does not, so an svg that was never closed arrived holding
# the rest of the article as text a drawing does not show.
_ENDS_DRAWING = frozenset(
    "b big blockquote body br center code dd div dl dt em embed h1 h2 h3 h4 h5 "
    "h6 head hr i img li listing menu meta nobr ol p pre ruby s small span "
    "strong strike sub sup table tt u ul var".split())
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

# What a browser trims from both ends of an address: C0 controls and space.
_URL_EDGE = "".join(chr(code) for code in range(0x21))
# An address the entry may link to: a web page with a host, or a mailbox.
# ``https:path`` and ``https:/path`` are refused - with no ``//host`` a browser
# resolves them against the page's own address.
_WEB_RE = re.compile(r"^(https?)://(?=[^/?#\\])", re.I)
_MAIL_RE = re.compile(r"^(mailto):(?=.)", re.I)
# Categories no address has inside it: controls, invisible format characters
# (zero-width, bidi overrides), separators, surrogates. A plain space is not
# among them by the time this is asked: it has become ``%20``. A tab and a line
# break ARE (they are controls), and that one matters: a browser deletes them
# before it reads the scheme - the whole trick behind ``java\tscript:`` - so an
# address holding one is not what it looks like, and is refused whole.
_NOT_IN_URL = frozenset({"Cc", "Cf", "Cs", "Zs", "Zl", "Zp"})


def _href(raw) -> str | None:
    """The address an ``<a>`` may keep, normalised, or ``None`` to remove it.

    Kept: ``http://host…``, ``https://host…``, ``mailto:…`` and ``#fragment``.
    Everything else is refused, which covers every other scheme (``javascript:``,
    ``data:``, ``vbscript:``, ``file:``, ``blob:`` …) and every relative address:
    an entry is a single document, so a relative link points at nothing of its
    own.

    A plain space inside is written ``%20``, which is what a browser sends for
    it (``mailto:a@b.test?subject=Hello there`` is an ordinary thing to write).
    Nothing else odd is repaired: a tab or line break, any other kind of space,
    a control, zero-width or bidi character inside the address removes it."""
    if not isinstance(raw, str):
        return None
    url = raw.strip(_URL_EDGE).replace(" ", "%20")
    if not url or any(unicodedata.category(ch) in _NOT_IN_URL for ch in url):
        return None
    if url.startswith("#"):
        return url
    found = _WEB_RE.match(url) or _MAIL_RE.match(url)
    if found is None:
        return None
    scheme = found.group(1)
    return scheme.lower() + url[len(scheme):]


# ── what one pass finds ──────────────────────────────────────────────────────

class _Found:
    """What a pass takes out and takes note of, shared by the walk over the
    tree and the CSS filter it calls."""
    __slots__ = ("removed", "font_links", "_fonts")

    def __init__(self):
        self.removed = Counter()
        self.font_links = []          # in the order met, each once
        self._fonts = set()

    def font(self, href) -> bool:
        """Note ``href`` if it is a typeface stylesheet; say whether it was."""
        href = href.strip() if isinstance(href, str) else ""
        if not _FONT_LINK_RE.match(href):
            return False
        if href not in self._fonts:
            self._fonts.add(href)
            self.font_links.append(href)
        return True


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
# A token is judged by what it IS, never by what its text looks like. A class
# named ``.javascript``, an id named ``#behavior``, a string that says
# ``see url(x)`` are a class, an id and a string: none of them fetches, so none
# of them is touched. (An earlier version searched the whole stylesheet for
# ``javascript:`` and dropped a syntax-highlighting sheet over the selector
# ``code.language-javascript::before``.)
#
# Then the written text is searched anyway (``_css_unsafe``). That second look
# does not share the tokenizer's opinion of where one token ends and the next
# begins: it reads the characters, with escapes and comments taken out, for the
# few spellings that fetch. It exists to catch two tokens that were harmless
# apart being written next to each other (``@`` and ``import``); if it finds
# one, the stylesheet goes. Strings and names are written so that it cannot
# trip on what they merely SAY.

# Where a run of tokens sits, which decides what can appear in it: the top of a
# stylesheet (rules, and ``@import``), the inside of ``{ }`` or a ``style``
# attribute (declarations, and nested rules), or a value (the inside of
# ``( )``, ``[ ]``, a function, a paint attribute).
_SHEET, _BODY, _VALUE = "sheet", "body", "value"

# An in-page reference: ``url(#gradient)``. It names something in the SAME
# document and fetches nothing, and it is the only way a shape can use the
# gradient, clip-path and marker elements kept above. Any other ``url()``
# becomes ``none``.
_LOCAL_REF_RE = re.compile(r"^#[A-Za-z0-9_-]{1,64}\Z")
_LOCAL_URL_RE = re.compile(r"(?<![a-z0-9_-])url\(#[a-z0-9_-]{1,64}\)")

# The spellings that fetch, as written text. No word boundary in front of
# ``url(``: a function NAMED ``texturl`` fetches nothing, but nothing honest is
# named that either, and "there is no ``url(`` in the result except an in-page
# one" is a rule a reader can check.
_CSS_WRITTEN_RE = re.compile(r"url\(|expression\(|image-set\(|@(?:import|font-face|namespace|charset)")
_CSS_ESCAPE_RE = re.compile(r"\\(?:[0-9a-f]{1,6}[ \t\n\r\f]?|.)", re.I | re.S)

# The punctuation a stylesheet is written with: single characters, and the
# two-character attribute-selector and column operators. ``<`` is here because
# ``(width < 600px)`` is a comparison; see ``_css_spaced`` for why it is safe.
_CSS_PUNCTUATION = frozenset("!$%&*+,-./:;<=>?@^_|~#") | {"~=", "|=", "^=", "$=", "*=", "||"}
# What may stand in front of a property name and still leave it that property
# to the browser the hack was written for: ``*behavior:…``, ``+behavior:…``.
_CSS_HACK_PREFIXES = ("*", "+")


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
    """The second look at finished CSS. True means do not use it.

    ``</`` and ``<!`` are the two things that could end the ``<style>`` element
    the text will sit in. The rest is read with every escape taken OUT: an
    escaped character forms no token, so ``.url\\(x\\)`` is not a ``url(``."""
    return "</" in css or "<!" in css or _css_spells_a_fetch(css)


def _css_spells_a_fetch(written) -> bool:
    """Whether ``written`` - finished CSS, or one token of it - reads as one of
    the spellings that fetch once its escapes and comments are taken out."""
    bare = _CSS_ESCAPE_RE.sub("", written.lower())
    return any(_CSS_WRITTEN_RE.search(_LOCAL_URL_RE.sub("", view))
               for view in (bare, _css_uncommented(bare)))


def _escaping_less_than(cls):
    """``cls``, writing an escaped ``<`` in a NAME as ``\\3c `` instead of
    ``\\<``. Both are the same name to CSS; only the second puts a ``<`` into
    the text. (Exact: tinycss2 escapes every ``<`` in a name, so a backslash
    directly before one is always that escape.)"""
    class Written(cls):
        __slots__ = ()

        def _serialize_to(self, write):
            parts = []
            super()._serialize_to(parts.append)
            write("".join(parts).replace("\\<", "\\3c "))
    return Written


_NAMED = {cls: _escaping_less_than(cls)
          for cls in (css_ast.IdentToken, css_ast.HashToken, css_ast.DimensionToken,
                      css_ast.FunctionBlock)}


def _css_named(node, name):
    """A token that carries a name, safe to write whatever the name holds."""
    if "<" in name:
        node.__class__ = _NAMED[type(node)]
    return node


def _css_none(node):
    return css_ast.IdentToken(node.source_line, node.source_column, "none")


def _css_string(node):
    """A string, written canonically. It keeps what it SAYS - words fetch
    nothing - but not the characters that would let the second look, or the
    ``<style>`` element, misread it: ``<`` always, and ``(`` and ``@`` when the
    string spells something that look searches for."""
    value = node.value
    written = serialize_string_value(value).replace("<", "\\3c ")
    if _css_spells_a_fetch(written):        # asked of the text as it will be WRITTEN
        written = written.replace("(", "\\28 ").replace("@", "\\40 ")
    return css_ast.StringToken(node.source_line, node.source_column, value, f'"{written}"')


def _css_url(node, value, found):
    """What a ``url(…)`` becomes: itself when it is an in-page reference,
    ``none`` (and one more thing removed) otherwise."""
    if isinstance(value, str) and _LOCAL_REF_RE.match(value):
        return css_ast.URLToken(node.source_line, node.source_column, value, f"url({value})")
    found.removed["css"] += 1
    return _css_none(node)


def _css_address(node):
    """The address a ``url`` token, a ``url("…")`` function or a bare string
    names, or ``None`` for anything else."""
    if node.type in ("url", "string"):
        return node.value
    if node.type == "function" and node.lower_name == "url":
        inside = [arg for arg in node.arguments if arg.type != "whitespace"]
        if len(inside) == 1 and inside[0].type == "string":
            return inside[0].value
    return None


def _css_function(node, found):
    name = node.lower_name
    if name == "url":                       # url("quoted"): a function, not a url token
        return _css_url(node, _css_address(node), found)
    # ... and by its name as the second look will read it: a function named
    # ``u.rl`` is written with its dot escaped, loses the escape there, and is
    # ``url(``. Better one function than the whole stylesheet.
    if name in CSS_FETCH_FUNCTIONS or _css_spells_a_fetch(serialize_identifier(name) + "("):
        found.removed["css"] += 1
        return _css_none(node)
    node.arguments = _css_tokens(node.arguments, found, _VALUE)
    return _css_named(node, node.name)


def _css_token(node, found):
    """One token that is not whitespace or an at-keyword: itself, a
    replacement, or ``None`` to leave it out. Leaving one out is counted."""
    kind = node.type
    if kind in ("number", "percentage", "unicode-range"):
        return node
    if kind == "literal":
        # Punctuation only. That leaves out "<!--" and "-->" (legal, useless,
        # and one would open a comment in the markup), a stray backslash
        # (written out before the NEXT token, it would escape it into something
        # else), and any control character.
        if node.value in _CSS_PUNCTUATION:
            return node
    elif kind in ("ident", "hash"):
        return _css_named(node, node.value)
    elif kind == "dimension":
        return _css_named(node, node.unit)
    elif kind == "string":
        return _css_string(node)
    elif kind == "url":
        return _css_url(node, node.value, found)
    elif kind == "function":
        return _css_function(node, found)
    elif kind in ("() block", "[] block", "{} block"):
        node.content = _css_tokens(node.content, found, _BODY if kind == "{} block" else _VALUE)
        return node
    elif kind == "error" and node.kind == "bad-url":
        return _css_url(node, None, found)
    elif kind == "comment" or (kind == "error" and node.kind.startswith("eof")):
        return None             # says nothing / already mended by closing the string
    found.removed["css"] += 1   # punctuation that is not, a broken string, a stray bracket
    return None


def _css_dropped_property(nodes, at) -> bool:
    """Whether the declaration starting at ``nodes[at]`` sets a property from
    ``CSS_DROPPED_PROPERTIES``: an optional hack prefix, the name, a colon.

    Asked only where a declaration can START, which is what keeps it off
    selectors: in ``.behavior:hover{}`` the name comes after a ``.``."""
    node = nodes[at]
    if node.type == "literal" and node.value in _CSS_HACK_PREFIXES and at + 1 < len(nodes):
        at += 1
        node = nodes[at]
    if node.type == "ident":
        name = node.lower_value
    elif node.type == "hash":               # "#behavior:…", another spelling of the hack
        name = node.value.lower()
    else:
        return False
    if name.lstrip("_") not in CSS_DROPPED_PROPERTIES:
        return False
    at += 1
    while at < len(nodes) and nodes[at].type == "whitespace":
        at += 1
    return at < len(nodes) and nodes[at].type == "literal" and nodes[at].value == ":"


def _css_drop_at_rule(nodes, at, found, where) -> None:
    """Count the at-rule starting at ``nodes[at]``, which is about to go.

    One kind is worth more than a count: ``@import`` of a Google Fonts
    stylesheet, at the top of a stylesheet, is how many entries ask for their
    typefaces. That address is offered to ``fonts.py`` exactly as a ``<link>``
    to it is, and counted the same way."""
    if nodes[at].lower_value == "import" and where is _SHEET:
        at += 1
        while at < len(nodes) and nodes[at].type == "whitespace":
            at += 1
        if at < len(nodes) and found.font(_css_address(nodes[at])):
            found.removed["link"] += 1
            return
    found.removed["css"] += 1


def _css_spaced(nodes) -> list:
    """``nodes`` with a space after any ``<`` that is not followed by one, or
    by ``=``.

    A stylesheet is raw text inside ``<style>``, and the only things that can
    end or disturb it start ``</`` or ``<!``. With this, the character after a
    ``<`` is always a space or ``=``, so neither can be written - whatever was
    dropped between the two, and whatever the serialiser puts between tokens."""
    spaced = []
    for node in nodes:
        if spaced and spaced[-1].type == "literal" and spaced[-1].value == "<":
            if not (node.type == "whitespace" or (node.type == "literal" and node.value == "=")):
                spaced.append(css_ast.WhitespaceToken(node.source_line, node.source_column, " "))
        spaced.append(node)
    return spaced


def _css_tokens(nodes, found, where) -> list:
    """``nodes`` filtered, blocks and function arguments included.

    A dropped at-rule or declaration ends at its ``;`` or after its ``{}``
    block, whichever comes first - the two ways a rule can end. At the end of
    the list it simply ends, so an unfinished ``@import url(x`` takes the rest
    of its block with it and nothing else."""
    kept, dropping, fresh = [], False, True     # fresh: a declaration could start here
    for at, node in enumerate(nodes):
        kind = node.type
        ends = kind == "{} block" or (kind == "literal" and node.value == ";")
        if dropping:
            dropping, fresh = not ends, ends
        elif kind == "whitespace":
            kept.append(node)
        elif fresh and where is _BODY and _css_dropped_property(nodes, at):
            found.removed["css"] += 1
            dropping = True
            while kept and kept[-1].type == "whitespace":   # the space in front of it goes too
                kept.pop()
        elif kind == "at-keyword" and node.lower_value not in CSS_AT_RULES:
            _css_drop_at_rule(nodes, at, found, where)
            dropping = True
        else:
            fresh = ends
            result = node if kind == "at-keyword" else _css_token(node, found)
            if result is not None:
                kept.append(result)
    return _css_spaced(kept)


def _css(source, found, where) -> str:
    """``source`` - a stylesheet, a ``style`` attribute or a paint attribute -
    as CSS that fetches nothing. ``""`` when nothing usable is left.

    ``found.removed["css"]`` counts every change made to it, comments apart.
    Anything that raises on the way (a block nested past the interpreter's
    depth is the known one) and anything the second look refuses costs the
    WHOLE piece: a stylesheet this cannot vouch for is not served."""
    if not source or not source.strip():
        return ""
    try:
        nodes = tinycss2.parse_component_value_list(source, skip_comments=True)
        css = tinycss2.serialize(_css_tokens(nodes, found, where)).strip()
    except Exception:
        found.removed["css"] += 1
        return ""
    css = _respell(css, _MARK_IN_CSS)
    if _css_unsafe(css):
        found.removed["css"] += 1
        return ""
    return css


# ── the text, before it is parsed ────────────────────────────────────────────
#
# Two things have to be read from the TEXT of a document, because by the time
# there is a tree it is too late for one and too lossy for the other:
#
# * how many attributes each tag is written with (``MAX_TAG_ATTRS``): the cost
#   being bounded is the parser's own;
# * the attributes of every ``<html>`` and ``<body>`` tag: libxml2 2.11 throws
#   away those of a second one, and a file saved from claude.ai IS a second
#   one - the artifact's own document inside the download wrapper's ``<body>``.
#
# ``_scan`` is one pass, and quote-aware: whatever is inside quotes is ONE value
# however long (an svg path, a style attribute). It does not have to agree with
# the parser about every broken tag - only never to count fewer attributes than
# the parser will build, and never to refuse an honest document. So a ``<`` that
# does not begin a tag (``a < b``) is text, and the inside of ``<style>`` and
# ``<script>`` is skipped, where ``(400px<width)`` is CSS and not a tag.
#
# ⚠ Known gap, the price of not refusing an honest stylesheet: skipping to
# ``</style>`` trusts that the ``<style>`` tag WAS a tag. Written inside an
# element only an HTML5 tokenizer reads as raw text (``<textarea>``,
# ``<title>``), it is not one there, and what follows it up to the next
# ``</style>`` goes uncounted on such a parser. The size limit, the hourly
# limit and the consumer's own thread still bound what that can cost.

_TAG_RE = re.compile(r"<(?:(!--)|([A-Za-z_:][^\s/>]*))")
_TAG_ATTR_RE = re.compile(
    r"""[\s/]*(?:(>)|([^\s/>][^\s/>=]*)(?:\s*=\s*(?:"([^"]*)"?|'([^']*)'?|([^\s>]*)))?)""")
_COMMENT_END_RE = re.compile(r"--!?>")
_RAW_TEXT_END = {"script": re.compile(r"</script", re.I), "style": re.compile(r"</style", re.I)}


def _scan(text) -> dict | None:
    """``{"html": {...}, "body": {...}}``: the attributes written on those two
    tags, raw, the later of two winning. ``None`` when some tag is written with
    more than ``MAX_TAG_ATTRS`` attributes."""
    shell = {"html": {}, "body": {}}
    unclosed = set()                # raw-text elements known to have no end tag ahead
    at = 0
    while True:
        tag = _TAG_RE.search(text, at)
        if tag is None:
            return shell
        at = tag.end()
        if tag.group(1):            # a comment: skip it, by the EARLIEST place it could end
            if text.startswith(">", at) or text.startswith("->", at):
                at = text.index(">", at) + 1
                continue
            end = _COMMENT_END_RE.search(text, at)
            if end is None:
                return shell
            at = end.end()
            continue
        name = tag.group(2).lower()
        wanted, count = shell.get(name), 0
        while True:
            attr = _TAG_ATTR_RE.match(text, at)
            if attr is None:        # the text ended inside the tag
                return shell
            at = attr.end()
            if attr.group(1):
                break
            count += 1
            if count > MAX_TAG_ATTRS:
                return None
            if wanted is not None:
                value = attr.group(3) or attr.group(4) or attr.group(5) or ""
                wanted[attr.group(2).lower()] = value
        if name in _RAW_TEXT_END and name not in unclosed:
            end = _RAW_TEXT_END[name].search(text, at)
            if end is None:
                unclosed.add(name)  # so a thousand unclosed openers are one search, not a thousand
            else:
                at = end.start()


# ── one pass over one document ───────────────────────────────────────────────

_NODE, _TEXT, _CLOSE = 0, 1, 2

# Where the walk is: in HTML, in a drawing, or in a drawing's tooltip (the
# inside of an svg ``<title>`` or ``<desc>``, which a browser reads as HTML and
# which therefore cannot be "broken out of").
_HTML, _SVG, _TIP = "html", "svg", "tip"
# The flag on an ``<svg>``'s own end tag: where the walk returns to after it.
_RETURNS_TO = {"to-html": _HTML, "to-tip": _TIP}
_LEAVING_FOR = {where: flag for flag, where in _RETURNS_TO.items()}


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

    def __init__(self, found):
        self.found = found
        self.removed = found.removed
        self.out = []                 # the body, as finished markup
        self.styles = []              # cleaned stylesheets, in document order
        self.title = ""               # the first <title> outside a drawing, with words in it
        self.heading = ""             # the first <h1> with words in it
        self.opening = ""             # the first <p> with words in it
        self._open = {}               # "h1" / "p" -> the text seen since it opened

    def walk(self, root) -> None:
        todo = [(_NODE, root, _HTML)]
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

    def _close(self, name, flag) -> None:
        self.out.append(f"</{name}>")
        if flag in self._open:
            words = _one_line("".join(self._open.pop(flag)))
            if flag == "h1":
                self.heading = self.heading or words
            else:
                self.opening = self.opening or words

    def _node(self, el, where, todo) -> None:
        # Whatever happens to the element, the text after it is its parent's.
        if el.tail:
            todo.append((_TEXT, el.tail, None))
        tag = el.tag
        if not isinstance(tag, str):          # a comment or a processing instruction
            return
        tag = tag.lower()
        if where is _SVG and tag in _ENDS_DRAWING:
            where = self._leave_drawing(todo)
        if tag == "style":                    # wherever it is, drawings included
            self.removed["handler"] += _handlers(el)
            self._style(el)
        elif tag == "title" and where is _HTML:   # the document's; a drawing's is a tooltip
            self.removed["handler"] += _handlers(el)
            self.title = self.title or _one_line("".join(el.itertext()))
        elif tag in _DROPPED_EMPTY or (where is not _HTML and tag in _SVG_DROPPED_EMPTY):
            # An element that HAS no content: what the parser hung under it is
            # really what came after it, and is read as that.
            if tag == "link":
                self._font_link(el)
            self._count(el, tag, top=True)
            self._inside(el, where, todo)
        elif tag in DROPPED or (where is not _HTML and tag in _DROPPED_SVG):
            self._drop(el, tag)
        else:
            self.removed["handler"] += _handlers(el)
            self._keep_or_unwrap(el, tag, where, todo)

    def _leave_drawing(self, todo) -> str:
        """End the drawing the walk is in, here, and say where that leaves it.

        Everything still queued up to the drawing's own end tag is either an end
        tag of something open inside it (written now, innermost first) or
        content that has not been read yet (put back, to be read as what
        FOLLOWS the drawing rather than as part of it)."""
        where, later = _HTML, []
        while todo:
            what, item, flag = todo.pop()
            if what != _CLOSE:
                later.append((what, item, flag))
                continue
            self.out.append(f"</{item}>")
            if flag in _RETURNS_TO:
                where = _RETURNS_TO[flag]
                break
        for what, item, flag in reversed(later):
            todo.append((what, item, where if what == _NODE else flag))
        return where

    def _style(self, el) -> None:
        """Collect one ``<style>``. Every stylesheet is moved to the head, so the
        two attributes that decide WHETHER it applies have to travel with it:
        left behind, ``<style media="print">p{display:none}</style>`` would
        hide every paragraph on screen."""
        kind = (el.get("type") or "").strip().lower()
        if kind not in ("", "text/css"):      # a template or data block: not CSS to a browser
            return
        css = _css("".join(el.itertext()), self.found, _SHEET)
        media = _one_line(el.get("media") or "")
        if css and media.lower() not in ("", "all"):
            # The query is filtered like any CSS, and must still be a query: with
            # a brace or a semicolon in it, it could close the block it is about
            # to open. A browser applies a stylesheet with a bad query to
            # nothing, so that is what it becomes.
            query = _css(media, self.found, _VALUE)
            usable = query and not set(query) & set("{};")
            css = _css(f"@media {query}{{{css}}}", _Found(), _SHEET) if usable else ""
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
        """Offer a ``<link>`` to a typeface stylesheet - one that is the page's
        stylesheet, not an ``alternate`` a reader would have to switch to."""
        rel = (el.get("rel") or "").lower().split()
        if "stylesheet" in rel and "alternate" not in rel:
            self.found.font(el.get("href"))

    def _keep_or_unwrap(self, el, tag, where, todo) -> None:
        drawing = where is not _HTML or tag == "svg"
        name = (_SVG_NAME if drawing else _HTML_NAME).get(tag)
        if name is None:
            # Unwrapped: what is inside is read where the element stood.
            # (``html``, ``head`` and ``body`` are unwrapped like any other
            # element not on a list; their attributes were read by ``_scan``.)
            self._inside(el, where, todo)
            return
        flag = None
        if tag == "svg" and where is not _SVG:
            flag = _LEAVING_FOR[where]        # the outermost svg of this drawing
        elif not drawing and tag in ("h1", "p") and tag not in self._open:
            if not (self.heading if tag == "h1" else self.opening):
                self._open[tag] = []
                flag = tag
        self.out.append(f"<{name}{self._attrs(el, tag, drawing)}>")
        if drawing and tag in _SVG_SHAPES:
            self._shape(el, name, where, todo)
            return
        if tag not in _VOID:
            todo.append((_CLOSE, name, flag))
        if not drawing:
            self._inside(el, _HTML, todo)
        else:
            self._inside(el, _TIP if tag in _SVG_OWN else _SVG, todo)

    @staticmethod
    def _inside(el, where, todo) -> None:
        """Queue ``el``'s text and children, to be read in document order."""
        for child in reversed(el):
            todo.append((_NODE, child, where))
        if el.text:
            todo.append((_TEXT, el.text, None))

    @staticmethod
    def _shape(el, name, where, todo) -> None:
        """Queue a shape's content: its tooltip inside it, the rest after it
        (see ``_SVG_SHAPES``). Queued last-first, as the stack reads it."""
        own, rest = [], []
        for child in el:
            mine = isinstance(child.tag, str) and child.tag.lower() in _SVG_OWN
            (own if mine else rest).append(child)
        for child in reversed(rest):
            todo.append((_NODE, child, where))
        if el.text:
            todo.append((_TEXT, el.text, None))
        todo.append((_CLOSE, name, None))
        for child in reversed(own):
            todo.append((_NODE, child, _SVG))

    def _attrs(self, el, tag, drawing) -> str:
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
            if spelled is None or (name == "href" and (tag != "a" or drawing)):
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
                value = _css(value, self.found, _BODY if name == "style" else _VALUE)
                if not value:
                    continue
            kept.append((spelled, value))
        if outbound:
            # A new tab, because the entry is framed and the frame cannot
            # navigate the page around it; no opener, so the new tab cannot
            # reach back. Written here, so what the entry asked for is moot.
            kept += [("target", "_blank"), ("rel", "noopener noreferrer")]
        return "".join(f' {name}="{_attr(value)}"' for name, value in kept)


def _shell_attrs(written, found, kept) -> str:
    """The attributes of the shell's ``<html>`` or ``<body>``: ``kept`` (what
    the shell says by itself), then what the entry's own tag was ``written``
    with - through the same filter as any element's, and a shorter list."""
    for name, raw in written.items():
        if name not in SHELL_ATTRS and not _DATA_RE.match(name):
            continue
        value = _CONTROL_RE.sub("", unescape(raw))
        if name == "style":
            value = _css(value, found, _BODY)
        if not value.strip() and name in ("style", "lang"):
            continue                # an empty language is no language: the shell's stands
        if name in kept or len(kept) < MAX_ATTRS:
            kept[name] = value
    return _unmark("".join(f' {name}="{_attr(value)}"' for name, value in kept.items()))


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
        """What is between the ``<body …>`` tag and ``</body>``. ``""`` means
        the entry had nothing in it once cleaned (or could not be read at all).

        The first ``>`` after ``<body`` ends the tag: one inside an attribute
        value is written ``&gt;``."""
        start = self.html.index(">", self.html.index("<body")) + 1
        return self.html[start:self.html.rindex("</body>")]


def _document(title, styles, body, html_attrs=' lang="en"', body_attrs="") -> str:
    """The one shell every entry gets. ``title`` is text; the rest is finished
    CSS and finished markup."""
    sheets = "".join(f"<style>{css}</style>" for css in styles)
    return (f'<!doctype html><html{html_attrs}><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{_unmark(_text(title))}</title><style>{FONT_CSS_MARK}</style>"
            f"{sheets}</head><body{body_attrs}>{body}</body></html>")


def _refused() -> Cleaned:
    """The answer for something that is not a readable document."""
    return Cleaned(_document("", (), ""), "", "", {"unparseable": 1}, ())


# What the parser says when it STOPPED rather than recovered. An HTML parser
# forgives almost anything and logs it as an error; these are the entries that
# mean the tree ends where it gave up. Judged by level and kind - the wording is
# only the last resort, for a libxml2 that files the same stop differently.
# A second witness, not the proof: see ``_parse`` for why the log alone misses
# a stop that comes after a hundred ordinary errors.
_STOPPED_KINDS = frozenset({"ERR_INTERNAL_ERROR", "ERR_NO_MEMORY", "ERR_RESOURCE_LIMIT"})
_STOPPED_WORDS = ("excessive depth", "resource limit")


def _stopped_early(parser) -> bool:
    """Whether the parser gave up part-way (a document nested past its depth
    limit, a text run past its size limit) and handed back what it had."""
    for entry in parser.error_log:
        if entry.level >= etree.ErrorLevels.FATAL or entry.type_name in _STOPPED_KINDS:
            return True
        if any(words in (entry.message or "").lower() for words in _STOPPED_WORDS):
            return True
    return False


_CLOSING_TAG_RE = re.compile(r"</(?:body|html)\s*>\Z", re.I)


def _content_end(text) -> int:
    """Where the content of ``text`` ends: before the closing ``</body>`` and
    ``</html>`` tags, the comments and the blank space a document finishes
    with. The end marker goes HERE and not after them, because what a parser
    does with anything written after ``</html>`` is its own affair.

    Worked from the end by index: nothing here is asked to rescan the text."""
    end = len(text)
    while True:
        while end and text[end - 1].isspace():
            end -= 1
        closing = _CLOSING_TAG_RE.search(text, max(0, end - 16), end)
        if closing is not None:
            end = closing.start()
            continue
        start = text.rfind("<!--", 0, end - 3) if text.endswith("-->", 0, end) else -1
        inside = text[start + 4:end - 3] if start >= 0 else "-->"
        if "-->" in inside or "--!>" in inside or inside.startswith((">", "->")):
            return end              # not one whole comment: leave it where it is
        end = start


def _parse(text):
    """``text`` as a tree, or ``None`` when it cannot be read to its end.

    Handed over as UTF-8 bytes with the encoding stated, so a ``<meta charset>``
    or an XML declaration inside the document cannot have it re-read as
    something else, and a character that cannot be encoded becomes ``?``
    instead of an error. Comments and processing instructions are discarded by
    the parser; the walk would skip them anyway.

    ⚠ ``recover=True`` means the parser returns a tree even when it stopped
    half-way: 256 nested elements deep, libxml2 2.11 hands back everything
    before that point. A tree with its end missing is not this document, so it
    is refused - and the proof that the end was reached is an element of our
    own, appended after the last character and looked for in the tree. The
    parser's error log is read as well, but cannot be the proof: lxml keeps its
    first 100 entries, 2.11 logs one for every ``<svg>`` and ``<path>`` it does
    not know, and the entry that matters is the last.

    The same check refuses a document that ENDS inside something - an unclosed
    tag, comment, ``<script>`` or ``<style>`` - since whatever swallowed its
    tail swallowed the end marker too. That is a document cut off mid-way, and
    saying so is better than staging the part before the cut. It also refuses
    one with content AFTER ``</html>`` when the parser throws that content away
    (2.11 does, for a document with a doctype): the marker goes with it.

    The marker's name is random per call, so a document cannot carry one."""
    end = "x-end-" + secrets.token_hex(8)
    at = _content_end(text)
    marked = f"{text[:at]}<{end}>{text[at:]}"
    parser = lxml.html.HTMLParser(encoding="utf-8", remove_comments=True, remove_pis=True,
                                  no_network=True, recover=True)
    try:
        root = lxml.html.document_fromstring(marked.encode("utf-8", "replace"), parser=parser)
    except Exception:           # lxml's "Document is empty" and its syntax errors
        return None
    if _stopped_early(parser) or next(root.iter(end), None) is None:
        return None
    return root


def _once(source) -> Cleaned | None:
    """One pass: scan, parse, read, rebuild. ``None`` when the document cannot
    be read - nothing in it, a tag too crowded to parse, a parser that stopped."""
    text = _CONTROL_RE.sub("", source)
    if not text.strip():
        return None
    shell = _scan(text)
    root = _parse(text) if shell is not None else None
    if root is None:
        return None
    found = _Found()
    seen = _Pass(found)
    seen.walk(root)
    # ``clean_fields`` is what the service runs on a title and a summary anyway:
    # one line each, invisible characters out, cut to ``[limits] title_chars``
    # and ``summary_chars``. Used here so the two cannot disagree.
    fields = blog_inbox.clean_fields({"title": seen.title or seen.heading,
                                      "summary": seen.opening})
    body = _unmark("".join(seen.out).strip())
    html_attrs = _shell_attrs(shell["html"], found, {"lang": "en"})
    body_attrs = _shell_attrs(shell["body"], found, {})
    removed = {name: count for name, count in found.removed.items() if count}
    return Cleaned(_document(fields["title"], seen.styles, body, html_attrs, body_attrs),
                   fields["title"], fields["summary"], removed, tuple(found.font_links))


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
    read to its end and a document that will not settle all come back as an
    empty shell with ``removed == {"unparseable": 1}``. So does a bug in here -
    and that one is counted for ``/health`` (``blog.clean``), because a refusal
    that is really a crash should not look like a bad upload.

    The size of ``html`` is the caller's to bound (``blog_inbox.html_ok``), and
    so is the size of the result, which can be up to six times the input
    (every ``"`` in an attribute is written ``&quot;``)."""
    if not isinstance(html, str):
        return _refused()
    try:
        return _settle(html)
    except Exception:
        _degrade.degraded("blog.clean")
        return _refused()
