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
  ``image-set()``). ``img-src data:`` and ``font-src 'self'`` stop the request
  leaving the document or the site; the cleaner stops it being made at all.
* **A layer that depends on a header is one misconfigured edge away from
  nothing.** The same file is served by Caddy and by the private preview.

**It is an allow-list, and the output is built, never edited.** The submitted
tree is only READ. Every name written in the result comes from this module's
own constants, never from the input: a tag name from ``KEPT_HTML`` /
``KEPT_SVG``; an attribute name from ``ATTRS``, or ``href`` on an anchor with
the ``target`` / ``rel`` the module adds, or ``class`` / ``style`` / ``lang`` /
``dir`` / ``data-*`` on the shell; or an ``aria-`` / ``data-`` name of
``[a-z0-9._-]``. Every value and text run is escaped (``& < > "``), and CSS is
tokenised, filtered and serialised afresh. No comment, processing instruction,
CDATA section or doctype is ever copied. That is what makes it hold whatever
the parser did: lxml's wheels
bundle libxml2 2.11.9 on Windows (measured) and 2.14 on Linux (lxml 6's release
notes; its HTML tokenizer was rewritten to follow HTML5), so the SAME bytes can
parse into different trees on the developer's box and on prod. A tree can be
surprising; it cannot contain a tag this module did not choose to write.

**What it changes, it changes toward honesty.** The operator publishes what the
preview shows, and a preview cannot show what is missing on a screen the
operator is not looking at. So an honest construct is kept with its meaning (a
``<`` in a media query is a comparison; a class named ``.javascript`` is a
class); whatever is taken out of a stylesheet adds to ``removed["css"]``;
anything the whole document could not survive comes back as a refusal
(``removed == {"unparseable": 1}`` with a ``reason``) rather than as its first
half. Two things ARE silent, by choice: an unknown element is unwrapped (its
text stays, no count), and a disallowed non-handler attribute is dropped; a
count per unwrap would be noise, not a signal. Scripts, handlers, links,
images, forms, CSS edits and whole-document failures are all counted or named.

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
import logging
import re
import secrets
import unicodedata
from collections import Counter
from dataclasses import dataclass
from html import unescape
from typing import NamedTuple

import lxml.html
import tinycss2
from lxml import etree
from tinycss2 import ast as css_ast
from tinycss2.serializer import serialize_identifier, serialize_string_value

from services import _degrade
from services.blog_svc import _trace
from shared import blog_inbox

log = logging.getLogger("blog_svc.clean")

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

# The most attribute names the pre-parse scan will read on ONE tag before it
# refuses the document as an obvious crowded tag. It is NOT the cost bound -
# the worker process's time limit is (see ``_start_tags`` and
# ``clean_bound``) - only the threshold for the cheap first refusal. Set some
# thirty times above any honest tag, so it refuses a blatant one (85,000
# attributes, which parse in over a minute) without ever catching a real entry.
# A constant for the same reason as the other two; if it ever needs moving, it
# belongs in ``[limits]`` beside ``max_html_kb``.
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
# Lower-cased at definition (``foreignObject`` → ``foreignobject`` …), because
# every lookup is against a lower-cased tag name.
DROPPED_SVG = frozenset(
    "script foreignobject image use animate animatemotion animatetransform set a "
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
    # ``hidden`` is inert, and leaving it out is not: the attribute would go and
    # the words the author hid would come out visible
    "hidden "
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
FONT_LINK_RE = re.compile(r"^https://fonts\.googleapis\.com/css2\?[A-Za-z0-9:;,@+&=._%-]+\Z")
_FONT_LINK_RE = FONT_LINK_RE        # the older private name; kept for existing callers

# What the parser lower-cased, back to the spelling a reader expects. A browser
# repairs ``viewbox`` and ``lineargradient`` by itself; this just keeps the
# staged file looking like the source.
_HTML_NAME = {name: name for name in KEPT_HTML}
_SVG_NAME = {name.lower(): name for name in KEPT_SVG}
_ATTR_NAME = {name.lower(): name for name in ATTRS}
_PREFIXED_RE = re.compile(r"^(?:aria|data)-[a-z0-9][a-z0-9._-]*\Z")
_DATA_RE = re.compile(r"^data-[a-z0-9][a-z0-9._-]*\Z")
_VOID = frozenset({"br", "hr", "col", "wbr"})
# The dropped elements that have no content in HTML. libxml2 does not agree
# with HTML about all of them: the 2.11 parser treats ``<embed>``, ``<source>``
# and ``<track>`` as containers, so ``<embed src=x><p>the rest of the entry``
# arrives with the rest of the entry INSIDE the embed. Dropping one of these
# "with its content" would take the entry with it; a browser, which knows the
# element is empty, would have shown it.
_DROPPED_EMPTY_HTML = frozenset(
    "area base embed frame img input link meta source track".split())
# The same question inside a drawing, where an element is written ``<rect/>``.
# libxml2's HTML parser has no notion of a drawing; whether it honours that
# slash is its own business (2.11 does). If one did not, every shape after the
# first would arrive nested INSIDE the first - and a shape draws nothing that is
# inside it, so the chart would be gone. Neither list below depends on the
# answer: a shape keeps only its own tooltip (``title`` / ``desc``) inside it,
# and anything else found under one is read as coming after it; the dropped
# elements that never hold anything lose only themselves.
_DROPPED_EMPTY_SVG = frozenset(
    "image use animate animatemotion animatetransform set".split())
_SVG_SHAPES = frozenset("path line rect circle ellipse polyline polygon stop".split())
_SVG_OWN = frozenset({"title", "desc"})
# The start tags that END a drawing: the HTML standard's list (its "in foreign
# content" rules, less ``font``, which only counts with certain attributes). A
# browser that meets ``<p>`` inside an ``<svg>`` closes the svg and carries on
# in HTML. libxml2 does not, so an svg that was never closed arrived holding
# the rest of the article as text a drawing does not show.
_ENDS_DRAWING = frozenset(
    "b big blockquote body br center code dd div dl dt em embed h1 h2 h3 h4 h5 "
    "h6 head hr i img li listing menu meta nobr ol p pre ruby s small span "
    "strong strike sub sup table tt u ul var".split())
# The ``<meta name=...>`` whose job the shell does itself. One in the submitted
# document is REPLACED by the shell's, so it is not reported as something taken
# out (a charset meta is caught the same way, by its ``charset`` attribute).
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


class _Written:
    """A stand-in for a token whose text is already settled.

    ``tinycss2.serialize`` reads a node's ``.type`` (for the spacing it inserts
    between tokens) and calls the node's ``_serialize_to`` to emit its text.
    This supplies both: the real type, and text computed once at construction.

    ⚠ ``_serialize_to`` is tinycss2 private API. The pin in ``requirements.txt``
    (and the test that checks it) is what keeps this working across upgrades."""
    __slots__ = ("type", "source_line", "source_column", "_text")

    def __init__(self, node, text):
        self.type = node.type
        self.source_line = node.source_line
        self.source_column = node.source_column
        self._text = text

    def _serialize_to(self, write):
        write(self._text)


def _css_named(node, name):
    """``node`` made safe to write whatever its NAME holds. tinycss2 escapes a
    ``<`` in a name as ``\\<``, which leaves a literal ``<`` in the output; the
    same name written ``\\3c `` does not, and reads back identically. Only a
    name with a ``<`` in it needs the fresh stand-in."""
    if "<" not in name:
        return node
    return _Written(node, tinycss2.serialize([node]).replace("\\<", "\\3c "))


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
        # else), and any control character. A literal that is none of those is a
        # token dropped, counted below like any other.
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
    elif kind == "error" and node.kind.startswith("eof"):
        return None             # already mended by closing the string / block
    # (no "comment" arm: the caller tokenises with skip_comments=True)
    found.removed["css"] += 1   # a non-punctuation literal, a broken string, a stray bracket
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
    except RecursionError:      # and ONLY that: anything else raised in there is a fault
        found.removed["css"] += 1  # in this module, and has to reach ``clean`` to be counted
        return ""
    css = _respell(css, _MARK_IN_CSS)
    if _css_unsafe(css):
        found.removed["css"] += 1
        return ""
    return css


# ── the text, before it is parsed ────────────────────────────────────────────
#
# Two things are read from the TEXT of a document, because by the time there is
# a tree it is too late for one and too lossy for the other:
#
# * an obvious crowded tag (more than ``MAX_TAG_ATTRS`` attributes), refused
#   before a worker process is spent on it - a FAST FIRST pass, not the bound;
# * the attributes of every ``<html>`` and ``<body>`` tag: libxml2 2.11 throws
#   away those of a second one, and a file saved from claude.ai IS a second
#   one - the artifact's own document inside the download wrapper's ``<body>``.
#
# ``_start_tags`` is one pass yielding ``(name, attribute pairs, foreign)`` for
# every start tag. It is a FAST FIRST REFUSAL, not a bound: a document it passes
# still goes to the worker process, which ``clean_bound`` kills at a time limit
# (the design doc, "The cleaner runs in a worker process with a time limit").
# Three rounds of review each found a shape where this scan and the parser read
# the same bytes differently, because a linear pass cannot reproduce a stateful
# HTML tokenizer; so it no longer tries to predict the parser's cost. What it
# still does, cheaply and in-process, is refuse the OBVIOUS crowded tag before a
# process is spent. Two rules keep it from doing harm:
#
# * The CROWDING count never LOWERS itself on a doubtful byte. An unquoted
#   ``{``/``}`` (which a real start tag never holds before its ``>``) is a
#   SKIPPED character, not a reason to abandon the candidate - so ``<p { a b
#   c…>`` counts a, b, c and is refused, where an earlier version abandoned at
#   the ``{`` and let the parser build them all.
# * It must not REFUSE an honest document. The one place a legitimate ``<`` and
#   ``{`` crowd together is CSS (``@media (400px<width){…}``), so ``<script>``
#   and ``<style>`` content is skipped to the first matching close tag and not
#   read as tags. That is best-effort: an exotic nesting where the parser builds
#   tags inside one (``</xmp><math><script>…``) slips the scan and is left to the
#   worker's timer. Preferring the honest document here is deliberate.
# * SHELL ADOPTION must not take ``<html>`` / ``<body>`` attributes from a tag
#   that is not the document's own - one inside a drawing, a title, a textarea,
#   a template. Those are marked ``foreign`` and the shell ignores them; the
#   crowding count still sees them (the parser builds a crowded tag inside a
#   ``<title>``, so the scan must too).

# A start tag's name; OR an end tag, declaration (``<!...``) or instruction
# (``<?...``), which carry no attributes and run to the next ``>``; OR a comment.
_TAG_RE = re.compile(r"<(?:(!--)|([A-Za-z_:][^\s/>]*)|([/!?]))")
# One attribute: the closing ``>``; OR an unquoted ``{``/``}`` (group 2), which
# a real start tag never holds, so it is skipped without ending the count; OR a
# name with an optional value. Whatever is inside quotes is ONE value however
# long (an svg path, a style attribute).
_TAG_ATTR_RE = re.compile(
    r"""[\s/]*(?:(>)|([{}])|([^\s/>{}][^\s/>={}]*)(?:\s*=\s*(?:"([^"]*)"?|'([^']*)'?|([^\s>]*)))?)""")
_COMMENT_END_RE = re.compile(r"--!?>")
_END_NAME_RE = re.compile(r"</([A-Za-z][^\s/>]*)")
# Skipped to their first close tag: the only two whose content is reliably raw
# text, and the one place a legitimate ``<`` and ``{`` crowd (CSS). Best-effort
# (see the block above). A body inside one is not read either, which is also
# correct.
_SKIP_CONTENT = {name: re.compile(rf"</{name}", re.I) for name in ("script", "style")}
# Elements a ``<html>`` / ``<body>`` written inside is not the document's own:
# foreign content, and the text-content elements the shell must not adopt from.
# Marked ``foreign`` for the shell; their inner tags are still COUNTED for
# crowding (the parser builds them). A ``<plaintext>`` has no end tag, so once
# open it keeps everything after it.
_SHELL_TRANSPARENT = frozenset(
    "svg math title textarea xmp noembed noframes listing plaintext desc "
    "template iframe noscript select option".split())


def _start_tags(text):
    """Yield ``(lower-case name, [(lower-case attr, raw value), ...], foreign)``
    for each start tag in ``text``, in order; ``foreign`` is true for a tag that
    is inside content a browser does not read as the document body.

    ⚠ A tag with more than ``MAX_TAG_ATTRS`` attributes yields its name with
    the list ``None`` and nothing after it: the scan stops there, so the fast
    refusal is returned without reading on."""
    skipped = set()                 # skippable elements known to have no close ahead
    transparent = []                # the open shell-transparent elements, by name
    at = 0
    while True:
        tag = _TAG_RE.search(text, at)
        if tag is None:
            return
        at = tag.end()
        if tag.group(3):            # an end tag, a declaration (<!…), or an instruction (<?…)
            # Do NOT skip to the next ">": where libxml2 ends these varies by
            # nesting, and skipping past a tag it DID build would lower the
            # crowding count. Step past just the "<" and read on; a real tag the
            # parser builds inside gets counted. An end tag's name is read, only
            # to pop the shell-transparent stack.
            if tag.group(3) == "/":
                closing = _END_NAME_RE.match(text, tag.start())
                if closing:
                    if transparent and closing.group(1).lower() == transparent[-1]:
                        transparent.pop()
                    at = closing.end()
                    continue
            at = tag.start() + 1
            continue
        if tag.group(1):            # a comment: skip it, by the EARLIEST place it could end
            if text.startswith(">", at) or text.startswith("->", at):
                at = text.index(">", at) + 1
                continue
            end = _COMMENT_END_RE.search(text, at)
            if end is None:
                return
            at = end.end()
            continue
        name = tag.group(2).lower()
        pairs, crowded = [], False
        while True:
            attr = _TAG_ATTR_RE.match(text, at)
            if attr is None:        # the text ended inside the tag
                return
            at = attr.end()
            if attr.group(2):       # "{" or "}": skipped, the count carries on
                continue
            if attr.group(1):       # ">"
                break
            pairs.append((attr.group(3).lower(), attr.group(4) or attr.group(5) or attr.group(6) or ""))
            if len(pairs) > MAX_TAG_ATTRS:
                crowded = True
                break
        if crowded:
            yield name, None, bool(transparent)
            return
        yield name, pairs, bool(transparent)
        if name in _SKIP_CONTENT and name not in skipped:
            end = _SKIP_CONTENT[name].search(text, at)
            if end is None:
                skipped.add(name)   # a thousand unclosed openers are one search, not a thousand
            else:
                at = end.start()
        elif name in _SHELL_TRANSPARENT:
            transparent.append(name)


def _scan(text):
    """``(shell, handlers)`` or ``None`` when a tag has more than
    ``MAX_TAG_ATTRS`` attributes.

    ``shell`` is ``{"html": {...}, "body": {...}}``: the attributes written on
    those two tags, the FIRST spelling of a repeated one winning (as a browser
    resolves a duplicate), and a LATER whole tag winning over an earlier one
    (the artifact over the wrapper). A tag inside foreign content is not the
    document's and does not count. ``handlers`` is how many ``on*`` attributes
    those adopted tags carried: the parser discards a second ``<body>``'s whole
    tag, so a handler on the artifact's own body is seen only here, and has to
    be counted here or nowhere."""
    shell = {"html": {}, "body": {}}
    for name, pairs, foreign in _start_tags(text):
        if pairs is None:
            return None
        if name in shell and not foreign:
            one = {}
            for attr, value in pairs:
                one.setdefault(attr, value)         # within one tag, first wins
            shell[name].update(one)                 # across tags, the later tag wins per key
    handlers = sum(1 for attrs in shell.values() for attr in attrs if attr.startswith("on"))
    return shell, handlers


# ── one pass over one document ───────────────────────────────────────────────

# The work list holds three kinds of item, each its own type so a reader never
# has to ask what a bare tuple's third slot means this time.
class _Node(NamedTuple):
    el: object
    where: str                      # one of _HTML / _SVG / _TIP


class _Text(NamedTuple):
    raw: str


class _Close(NamedTuple):
    name: str
    returns_to: str | None = None   # an svg's own close: the ``where`` to resume in
    capture: str | None = None      # "h1"/"p": collect this element's text as heading/opening


# Where the walk is: in HTML, in a drawing, or in a drawing's tooltip (the
# inside of an svg ``<title>`` or ``<desc>``, which a browser reads as HTML and
# which therefore cannot be "broken out of").
_HTML, _SVG, _TIP = "html", "svg", "tip"


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
        todo = [_Node(root, _HTML)]
        while todo:
            item = todo.pop()
            if type(item) is _Text:
                self._say(item.raw)
            elif type(item) is _Close:
                self._close(item)
            else:
                self._node(item.el, item.where, todo)

    def _say(self, raw) -> None:
        self.out.append(_text(raw))
        for heard in self._open.values():
            heard.append(raw)

    def _close(self, item) -> None:
        self.out.append(f"</{item.name}>")
        if item.capture in self._open:
            words = _one_line("".join(self._open.pop(item.capture)))
            if item.capture == "h1":
                self.heading = self.heading or words
            else:
                self.opening = self.opening or words

    def _node(self, el, where, todo) -> None:
        # Whatever happens to the element, the text after it is its parent's.
        if el.tail:
            todo.append(_Text(el.tail))
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
        elif tag in _DROPPED_EMPTY_HTML or (where is not _HTML and tag in _DROPPED_EMPTY_SVG):
            # An element that HAS no content: what the parser hung under it is
            # really what came after it, and is read as that.
            if tag == "link":
                self._font_link(el)
            self._count(el, tag, top=True)
            self._inside(el, where, todo)
        elif tag in DROPPED or (where is not _HTML and tag in DROPPED_SVG):
            self._drop(el, tag)
        else:
            # ``html`` and ``body`` carry no handler count here: ``_scan`` owns
            # theirs, because it sees the tag the parser discarded (the
            # artifact's own, in a claude.ai download) and the walk does not.
            if tag not in ("html", "body"):
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
            item = todo.pop()
            if type(item) is not _Close:
                later.append(item)
                continue
            self.out.append(f"</{item.name}>")
            if item.returns_to is not None:
                where = item.returns_to
                break
        for item in reversed(later):
            todo.append(item._replace(where=where) if type(item) is _Node else item)
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
            # The wrap re-cleans CSS this method already cleaned, so its counts
            # are discarded (a throwaway ``_Found``): counting them would double
            # every change the inner ``_css`` calls above already counted.
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
        listed = tag in DROPPED or (tag in DROPPED_SVG and tag != "a")
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
        close = _Close(name)
        if tag == "svg" and where is not _SVG:
            close = _Close(name, returns_to=where)   # the outermost svg of this drawing
        elif not drawing and tag in ("h1", "p") and tag not in self._open:
            if not (self.heading if tag == "h1" else self.opening):
                self._open[tag] = []
                close = _Close(name, capture=tag)
        self.out.append(f"<{name}{self._attrs(el, tag, drawing)}>")
        if drawing and tag in _SVG_SHAPES:
            self._shape(el, close, where, todo)
            return
        if tag not in _VOID:
            todo.append(close)
        if not drawing:
            self._inside(el, _HTML, todo)
        else:
            self._inside(el, _TIP if tag in _SVG_OWN else _SVG, todo)

    @staticmethod
    def _inside(el, where, todo) -> None:
        """Queue ``el``'s text and children, to be read in document order."""
        for child in reversed(el):
            todo.append(_Node(child, where))
        if el.text:
            todo.append(_Text(el.text))

    @staticmethod
    def _shape(el, close, where, todo) -> None:
        """Queue a shape's content: its tooltip inside it, the rest after it
        (see ``_SVG_SHAPES``). Queued last-first, as the stack reads it."""
        own, rest = [], []
        for child in el:
            mine = isinstance(child.tag, str) and child.tag.lower() in _SVG_OWN
            (own if mine else rest).append(child)
        for child in reversed(rest):
            todo.append(_Node(child, where))
        if el.text:
            todo.append(_Text(el.text))
        todo.append(close)
        for child in reversed(own):
            todo.append(_Node(child, _SVG))

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
            else:
                value = _kept_value(name, value, self.found)
                if value is None:
                    continue
            kept.append((spelled, value))
        if outbound:
            # A new tab, because the entry is framed and the frame cannot
            # navigate the page around it; no opener, so the new tab cannot
            # reach back. Written here, so what the entry asked for is moot.
            kept += [("target", "_blank"), ("rel", "noopener noreferrer")]
        return "".join(f' {name}="{_attr(value)}"' for name, value in kept)


def _kept_value(name, value, found):
    """An allow-listed attribute's value, cleaned, or ``None`` to drop it.

    The one place both an element's attributes and the shell's go through: a
    ``style`` or an SVG paint attribute is filtered as CSS (and dropped when
    nothing usable is left); every other allow-listed attribute is kept as
    written."""
    if name not in CSS_ATTRS:
        return value
    return _css(value, found, _BODY if name == "style" else _VALUE) or None


def _shell_attrs(written, found, kept) -> str:
    """The attributes of the shell's ``<html>`` or ``<body>``: ``kept`` (what
    the shell says by itself), then what the entry's own tag was ``written``
    with - through the same filter (``_kept_value``) as any element's, and a
    shorter allow-list."""
    for name, raw in written.items():
        if name not in SHELL_ATTRS and not _DATA_RE.match(name):
            continue
        value = _kept_value(name, _CONTROL_RE.sub("", unescape(raw)), found)
        if value is None or (not value.strip() and name == "lang"):
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
    reason: str = ""     # "" for a document that was kept; else a code from REFUSALS

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


# The head ``_document`` writes, up to and including the ``<style>`` that opens
# the font slot: fixed but for ``<html>``'s attributes and the title's text
# (which holds no ``<`` - ``_text`` escapes it - and no raw mark - ``_unmark``
# respells it). Matched to find the slot WITHOUT re-typing this string in
# another module.
_SLOT_HEAD_RE = re.compile(
    r'<!doctype html><html(?: [a-z][a-z0-9:._-]*="[^"]*")*><head>'
    r'<meta charset="utf-8">'
    r'<meta name="viewport" content="width=device-width, initial-scale=1">'
    r"<title>[^<]*</title><style>")


def font_slot(html) -> tuple[int, int] | None:
    """The ``(start, end)`` of ``FONT_CSS_MARK`` inside the ONE font slot the
    shell writes - the ``<style>`` right after the title - or ``None``.

    ``None`` unless the document opens with exactly the head ``_document``
    writes, that slot is there holding the mark, and the mark appears NOWHERE
    else (so the span returned is unambiguous). ``fonts.py`` fills the slot by
    this span instead of re-typing the head as its own pattern."""
    if not isinstance(html, str) or html.count(FONT_CSS_MARK) != 1:
        return None
    head = _SLOT_HEAD_RE.match(html)
    if head is None:
        return None
    start = head.end()
    end = start + len(FONT_CSS_MARK)
    if html[start:end] != FONT_CSS_MARK or not html.startswith("</style>", end):
        return None
    return start, end


# ── refusing ─────────────────────────────────────────────────────────────────

# Why a document was refused, as ``Cleaned.reason``. Every refusal is the same
# empty shell and the same ``removed == {"unparseable": 1}``, because that is
# what the rest of the service reads; the code is what tells seven different
# things apart. Codes, not sentences: the handler owns the operator's wording.
#
#   empty           nothing in it to read (blank, or no document the parser found)
#   not_text        not a string at all
#   crowded_tag     some tag is written with more than MAX_TAG_ATTRS attributes
#   cut_off         the parser did not reach the end: the document ends inside a
#                   tag, quote, comment, <script> or <style>, or has content
#                   after </html> that the parser discarded
#   too_deep        as cut_off, and the parser said it stopped at its depth limit
#   did_not_settle  cleaning its own output kept changing it (a fault HERE)
#   internal        an exception inside this module (a fault HERE)
#   too_slow        cleaning took longer than the time allowed (set by
#                   clean_bounded, which runs clean() in a worker process; never
#                   set by clean() itself)
REFUSALS = ("empty", "not_text", "crowded_tag", "cut_off", "too_deep", "did_not_settle",
            "internal", "too_slow")


class _Refuse(Exception):
    """Raised anywhere below ``clean`` to refuse the document, with a code from
    ``REFUSALS``. An exception, because the decision is made several calls deep
    and nothing between there and ``clean`` has anything to add to it."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def refusal(reason) -> Cleaned:
    """The empty-shell ``Cleaned`` for a refusal ``reason`` (a ``REFUSALS``
    code). Public so ``clean_bound`` can return ``crowded_tag`` / ``too_slow`` /
    ``internal`` without re-parsing a document in-process. It does NOT log or
    count - the caller owns that - so that the worker path's own logging and
    degrade counters are not duplicated here."""
    return Cleaned(_document("", (), ""), "", "", {"unparseable": 1}, (), reason)


def _refused(reason) -> Cleaned:
    """A refusal from inside ``clean()``: logged by its code alone, since the
    document is the submitter's and never goes in a log."""
    log.info("refused: %s", reason)
    return refusal(reason)


# Whether the parser's log says it stopped at its DEPTH limit. Asked only after
# the end marker has been found missing, and only to choose between two codes
# (``too_deep`` or ``cut_off``). It is never a reason to refuse by itself, and
# could not be one: lxml keeps the first hundred log entries and the stop is the
# last thing a parser says; and what a parser files as fatal is its own affair
# (libxml2 2.11 logs "Memory allocation failed", FATAL, for an attribute left
# unquoted at the end of a document). So this reads for the one thing it is
# asked about, by its wording.
def _said_too_deep(parser) -> bool:
    return any("depth" in (entry.message or "").lower() for entry in parser.error_log)


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
        if not text.endswith("-->", 0, end):
            return end
        start = text.rfind("<!--", 0, end - 3)
        if start < 0:
            return end              # a stray "-->": not a comment at all
        inside = text[start + 4:end - 3]
        if "-->" in inside or "--!>" in inside or inside.startswith((">", "->")):
            return end              # not ONE whole comment: leave it where it is
        end = start


def _parse(text):
    """``text`` as a tree. Raises ``_Refuse`` when it cannot be read to its end.

    Handed over as UTF-8 bytes with the encoding stated, so a ``<meta charset>``
    or an XML declaration inside the document cannot have it re-read as
    something else, and a character that cannot be encoded becomes ``?``
    instead of an error. Comments and processing instructions are discarded by
    the parser; the walk would skip them anyway.

    ⚠ ``recover=True`` means the parser returns a tree even when it stopped
    half-way: 256 nested elements deep, libxml2 2.11 hands back everything
    before that point. A tree with its end missing is not this document, so it
    is refused - and the ONE proof that the end was reached is an element of
    our own, placed after the content and looked for in the tree.

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
    except etree.LxmlError:     # "Document is empty", a syntax error: the parser's own verdict.
        raise _Refuse("empty") from None    # (Anything else raised there is a fault, and counted.)
    if next(root.iter(end), None) is None:
        raise _Refuse("too_deep" if _said_too_deep(parser) else "cut_off")
    return root


def _once(source) -> Cleaned:
    """One pass: scan, parse, read, rebuild. Raises ``_Refuse`` when the
    document cannot be read."""
    text = _CONTROL_RE.sub("", source)
    if not text.strip():
        raise _Refuse("empty")
    scanned = _scan(text)
    if scanned is None:
        raise _Refuse("crowded_tag")
    shell, handlers = scanned
    root = _parse(text)
    found = _Found()
    found.removed["handler"] += handlers        # a discarded <html>/<body>'s own
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
    """Clean until cleaning changes nothing. Raises ``_Refuse`` otherwise."""
    removed, font_links = Counter(), None
    for _ in range(MAX_PASSES):
        result = _once(source)
        removed.update(result.removed)
        if font_links is None:              # only the first pass still has the links
            font_links = result.font_links
        if result.html == source:
            return Cleaned(result.html, result.title, result.summary, dict(removed), font_links)
        source = result.html
    # Not the document's fault: each pass is this module reading its OWN output,
    # and a result it cannot reproduce is one it cannot vouch for.
    _degrade.degraded("blog.clean.unsettled", exc_info=False)
    raise _Refuse("did_not_settle")


def clean(html: str) -> Cleaned:
    """``html`` as a safe, normalised document. NEVER raises.

    Anything that is not text, text with nothing in it, text the parser cannot
    read to its end and a document that will not settle all come back as an
    empty shell with ``removed == {"unparseable": 1}`` and a ``reason`` from
    ``REFUSALS``. So does a bug in here - and that one is counted for
    ``/health`` (``blog.clean``), because a refusal that is really a crash
    should not look like a bad upload.

    The size of ``html`` is the caller's to bound (``blog_inbox.html_ok``), and
    so is the size of the result, which can be up to six times the input
    (every ``"`` in an attribute is written ``&quot;``)."""
    if not isinstance(html, str):
        return _refused("not_text")
    try:
        return _settle(html)
    except _Refuse as refuse:
        return _refused(refuse.reason)
    except Exception as exc:
        # A document-free note, not the default full traceback: a traceback can
        # quote a fragment of the document (an attribute name in a KeyError).
        _degrade.degraded("blog.clean", detail=_trace.where("cleaning", exc), exc_info=False)
        return _refused("internal")
