"""The public site's menu, for the pages the site does not draw itself.

Two kinds of page carry it through here: the public live screens
(``webgui/live_main.py``), and the documents the site shows in a frame -- a
Blog entry (``services/blog_svc/sitewriter.py``) and the market report (the
report pipeline's publish step, outside this repo). Each such document drew a
logo of its own, at its own size and in its own colours, so the Blog and
Market report pages showed two different logos one above the other.
``inject_into_document`` replaces that: the document gets the site's bar, its
own lockup is hidden, and the bar hides itself when the document is loaded as
``…#ns-framed`` (``framed_src``), which is how the site's frames load it. So a
framed document shows one logo, the page's, and the same document opened on
its own page shows the site's bar like every other page.

neuralstrike.co's six pages carry ONE menu, byte for byte (the mark, the
wordmark, the links, the Live Desk button), and the blog writer copies it into
every entry page. The live screens on ``live.neuralstrike.co`` drew a header of
their own instead -- the app's 32px image mark beside a Montserrat wordmark, a
different size and face from the site's, and not a link -- so a visitor moving
from the site to a screen watched the logo change under them and had no way
back but the browser's.

So this module takes the menu from the SAME place the blog writer does
(``deploy/site/blog.html``, ``services/blog_svc/sitewriter.NAV_RE``) and makes
it absolute: the live origin serves none of the site's pages, so ``report.html``
becomes ``https://<site_host>/report.html`` and the logo goes to the home page.
A change to the site's menu therefore reaches the live screens on their next
restart without anyone remembering to make it twice.

⚠ Every address it emits is on the PUBLIC site. That is what keeps this inside
the rule the live header was written under: the private app's routes do not
exist on the live origin and must never be advertised from it. A menu link that
is not a plain path on the site (an absolute URL, a scheme, ``../``) refuses the
whole menu rather than passing it through, and ``site_nav_html`` then answers
``""`` so the caller draws its plain lockup.

Pure: stdlib and ``repo_paths`` only, no NiceGUI, so it can be tested as text.
"""
import datetime as dt
import html
import json
import pathlib
import re

import repo_paths

# The page whose menu is copied. blog.html because the blog writer already
# treats it as the menu's source and deploy/tests/test_site.py pins its
# addresses as plain paths from the site root.
MENU_PAGE = pathlib.Path(repo_paths.SITE_ROOT) / "blog.html"

# The same pattern as services/blog_svc/sitewriter.NAV_RE (this process may not
# import a service). Exactly one match is accepted.
_NAV_RE = re.compile(r'<nav class="ns-nav.*?</nav>', re.S)
_HREF_RE = re.compile(r'href="([^"]*)"')
# The public name of the menu pattern: services/blog_svc/sitewriter.NAV_RE is
# this object, and deploy/tests/test_site.py pins it from the site's side.
PAGE_NAV_RE = _NAV_RE
# ``page_parts``: the rest is looked for with the comments taken out, so a link
# that is only quoted in one is not copied into every generated page.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_ICON_RE = re.compile(r'<link rel="(?:icon|apple-touch-icon)"[^>]*>')
_STYLE_RE = re.compile(r'<link rel="stylesheet"[^>]*>')
_FOOT_RE = re.compile(r'<footer class="ns-foot ns-live-foot">(.*?)</footer>', re.S)
# The stylesheet a generated page's own classes are defined in.
_SITE_CSS = 'href="assets/site.css"'
# A plain page on the site, with an optional fragment: report.html,
# index.html#community. Nothing with a scheme, a host, a slash or a dot-dot.
_PLAIN_HREF_RE = re.compile(r"[a-z0-9-]+\.html(#[a-z0-9-]+)?")
_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")

# Every live screen belongs to the Live Desk, so that is the item marked as the
# page you are on, as live.html marks it. A document names its own section.
LIVE_DESK, BLOG, REPORT = "live.html", "blog.html", "report.html"

# ── the "new" tag on Blog ────────────────────────────────────────────────────
# For ``[site] new_badge_hours`` (config/blog.toml) after an entry is first
# published, the menu shows a small glowing "new" on Blog. The window is decided
# ONCE, by the blog writer, as ``blog.json``'s ``new_until``
# (``services/blog_svc/sitewriter.new_until``): the site's pages read it in the
# browser (``assets/blog-new.js``), and the live screens' menu reads it here
# when the page is built. The tag is the app's Option Signals "new" badge
# (``webgui/pages/options/scanner.py`` ``_SYMBOL_SLOT``), tilted and glowing.
BLOG_LIST = pathlib.Path(repo_paths.SITE_ROOT) / "blog.json"
NEW_CLASS, NEW_LINK_CLASS = "ns-new", "ns-has-new"
NEW_TEXT, NEW_LABEL = "new", "Blog, new entry"
NEW_BADGE_HTML = f'<span class="{NEW_CLASS}" aria-hidden="true">{NEW_TEXT}</span>'


def blog_is_new(now=None, *, path=None) -> bool:
    """Whether the clock is before ``blog.json``'s ``new_until``.

    ``now`` is an aware datetime (``None``: the current time). Never raises: no
    file, a file that does not parse, no ``new_until``, or a time with no offset
    all answer ``False`` -- a missing tag is the quiet failure."""
    try:
        raw = json.loads(pathlib.Path(path or BLOG_LIST).read_text(encoding="utf-8"))
        until = dt.datetime.fromisoformat(raw["new_until"])
        if until.utcoffset() is None:
            return False
        moment = dt.datetime.now(dt.timezone.utc) if now is None else now
        return moment < until
    except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError, AttributeError):
        return False


def _mark_new(nav, base) -> str:
    """``nav`` with the "new" tag on its Blog link; unchanged unless there is
    exactly one such link."""
    link = re.compile(r'<a class="ns-navlink"( href="' + re.escape(f"{base}{BLOG}")
                      + r'"[^>]*)>(.*?)</a>', re.S)
    if len(link.findall(nav)) != 1:
        return nav
    return link.sub(lambda m: (f'<a class="ns-navlink {NEW_LINK_CLASS}"{m.group(1)} '
                               f'aria-label="{NEW_LABEL}">{m.group(2)}{NEW_BADGE_HTML}</a>'),
                    nav, count=1)

# The site self-hosts Inter (site.css). The live process already takes its two
# faces from Google Fonts ([typography].font_url, [brand].font_url), so the
# menu's face comes from there too rather than as a new route on this origin.
# Weights: 400 the links, 500 the button, 600 the wordmark -- site.css's own.
FONT_HEAD_HTML = ('<link rel="stylesheet" href="https://fonts.googleapis.com/'
                  'css2?family=Inter:wght@400;500;600&display=swap">')


def _absolute(href, base):
    """``report.html`` -> ``https://host/report.html``; ``index.html`` -> home."""
    page, _, fragment = href.partition("#")
    path = "" if page == "index.html" else page
    return f"{base}{path}" + (f"#{fragment}" if fragment else "")


def site_nav_html(screen_title="", *, site_host=None, page=None,
                  current=LIVE_DESK, mark_new=False) -> str:
    """The site's menu with absolute addresses, or ``""`` when it cannot be built.

    ``screen_title`` is set after the wordmark as the site's own breadcrumb
    (``.ns-crumb``), so a visitor still reads which screen this is. ``current``
    is the menu item marked as the page you are on. The sticky class is
    dropped: a live screen pins its own table headers to the top of the window,
    and a pinned menu would sit over them. ``mark_new`` asks ``blog_is_new``
    now and, inside the window, puts the "new" tag on Blog. Only a page built
    per visit may pass it: a document built once and kept would carry the tag
    past its window.

    Never raises: a missing file, a second menu, an odd address or a bad host
    all answer ``""``."""
    host = str(repo_paths.SITE_HOST if site_host is None else site_host).strip().lower()
    if not _HOST_RE.fullmatch(host):
        return ""
    try:
        raw = pathlib.Path(page or MENU_PAGE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    navs = _NAV_RE.findall(raw)
    if len(navs) != 1 or '<span class="ns-spacer"></span>' not in navs[0]:
        return ""
    nav = navs[0]
    hrefs = _HREF_RE.findall(nav)
    if not hrefs or not all(_PLAIN_HREF_RE.fullmatch(h) for h in hrefs):
        return ""
    base = f"https://{host}/"
    nav = nav.replace(" ns-nav-sticky", "").replace(' aria-current="page"', "")
    nav = _HREF_RE.sub(lambda m: f'href="{_absolute(m.group(1), base)}"', nav)
    nav = nav.replace(f'href="{base}{current}"',
                      f'href="{base}{current}" aria-current="page"', 1)
    if mark_new and blog_is_new():
        nav = _mark_new(nav, base)
    if screen_title:
        crumb = f'<span class="ns-crumb">{html.escape(str(screen_title))}</span>'
        nav = nav.replace('<span class="ns-spacer"></span>',
                          f'{crumb}\n    <span class="ns-spacer"></span>', 1)
    return nav


# The menu's styling, from deploy/site/assets/nocturne.css (the tokens, .btn)
# and site.css (the menu rules), every rule scoped under `.ns-site-bar` so none
# of it reaches the screen below. ⚠ The documented escape hatch: the menu is a
# raw HTML string inside `ui.html`, which no `.classes()` can reach.
# shared/tests/test_site_nav.py compares each declaration with the site's own
# stylesheets, so a retune there fails here until it is copied.
SITE_NAV_CSS = """
.ns-site-bar {
  --color-bg: #161826;
  --color-text: #e9e9ed;
  --color-accent: #9184d9;
  --color-accent-300: #d2cefd;
  --color-divider: color-mix(in srgb, #e9e9ed 16%, transparent);
  --font-heading: "Inter", system-ui, sans-serif;
  --font-heading-weight: 500;
  --font-body: "Inter", system-ui, sans-serif;
  --space-2: 5.6px;
  --space-3: 8.4px;
  --radius-md: 8px;
  --ns-nav-pad: clamp(16px, 3vw, 40px);
  font-family: var(--font-body);
  font-size: 15px;
  line-height: 1.55;
  font-weight: 400;
  color: var(--color-text);
}
.ns-site-bar *, .ns-site-bar *::before, .ns-site-bar *::after { box-sizing: border-box; }
.ns-site-bar a {
  color: var(--color-accent);
  text-decoration: none;
  text-underline-offset: 3px;
}
.ns-site-bar a:hover { color: var(--color-accent-300); text-decoration: underline; }
.ns-site-bar .ns-nav {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 16px var(--ns-nav-pad);
  border-bottom: 1px solid var(--color-divider);
}
.ns-site-bar .ns-spacer { flex: 1; }
.ns-site-bar .ns-brand {
  font-family: var(--font-heading);
  font-weight: 600;
  font-size: 14px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--color-text);
}
.ns-site-bar .ns-brand .ns-brand-b { color: var(--color-accent); }
.ns-site-bar .ns-lockup { display: inline-flex; align-items: center; gap: 9px; }
.ns-site-bar .ns-mark { width: 26px; height: 26px; display: block; flex: none; overflow: visible; }
.ns-site-bar .ns-mark-rule { fill: var(--color-accent); }
.ns-site-bar .ns-mark-fig {
  fill: none;
  stroke: var(--color-text);
  stroke-linecap: round;
  stroke-linejoin: round;
}
.ns-site-bar .ns-crumb {
  font-size: 13px;
  color: color-mix(in srgb, var(--color-text) 55%, transparent);
}
.ns-site-bar .ns-navlink {
  font-size: 13.5px;
  white-space: nowrap;
  color: color-mix(in srgb, var(--color-text) 72%, transparent);
}
.ns-site-bar .ns-navlink:hover { color: var(--color-accent); text-decoration: none; }
.ns-site-bar .ns-navlink[aria-current="page"] {
  color: var(--color-text);
  text-decoration: underline;
  text-decoration-color: var(--color-accent);
  text-decoration-thickness: 2px;
  text-underline-offset: 6px;
}
.ns-site-bar .btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  cursor: pointer; text-decoration: none;
  font-family: var(--font-heading); font-weight: var(--font-heading-weight);
  font-size: 14px; line-height: 1.2; color: var(--color-text);
  background: transparent; border: 1px solid transparent;
  padding: var(--space-2) calc(var(--space-3) * 1.2);
  border-radius: var(--radius-md);
}
.ns-site-bar .btn-primary { color: var(--color-accent); border-color: var(--color-accent); }
.ns-site-bar .btn-primary:hover { background: color-mix(in srgb, var(--color-accent) 12%, transparent); }
.ns-site-bar .btn-primary:active { background: color-mix(in srgb, var(--color-accent) 22%, transparent); }
@media (max-width: 960px) {
  .ns-site-bar .ns-nav { flex-wrap: wrap; gap: 10px 14px; }
  .ns-site-bar .ns-nav .ns-spacer { flex: 0 0 100%; height: 0; }
  /* The gap narrows here, so the "new" tag's tail would touch the next link. */
  .ns-site-bar .ns-navlink.ns-has-new { margin-right: 8px; }
}
.ns-site-bar .ns-navlink.ns-has-new { position: relative; }
.ns-site-bar .ns-new {
  position: absolute;
  top: -13px;
  right: -17px;
  display: inline-block;
  padding: 0 4px;
  min-height: 12px;
  font-size: 9px;
  font-weight: 400;
  line-height: 12px;
  letter-spacing: normal;
  text-transform: none;
  text-decoration: none;
  white-space: nowrap;
  color: #ffffff;
  background: #1565c0;
  border-radius: 4px;
  transform: rotate(14deg);
  transform-origin: left center;
  pointer-events: none;
  box-shadow: 0 0 5px rgba(21, 101, 192, 0.9), 0 0 12px rgba(30, 136, 229, 0.55);
  animation: ns-new-glow 2.4s ease-in-out infinite;
}
@keyframes ns-new-glow {
  0%, 100% { box-shadow: 0 0 4px rgba(21, 101, 192, 0.8), 0 0 9px rgba(30, 136, 229, 0.4); }
  50% { box-shadow: 0 0 7px rgba(30, 136, 229, 1), 0 0 18px rgba(30, 136, 229, 0.75), 0 0 28px rgba(66, 165, 245, 0.35); }
}
@media (prefers-reduced-motion: reduce) {
  .ns-site-bar .ns-new { animation: none; }
}
"""


# ── the bar inside a framed document ─────────────────────────────────────────
# The fragment the site's frames load a document with, and the id of the bar
# injected into it: `.ns-site-bar:target` hides the bar exactly when the
# document was loaded as `…#ns-framed`. CSS only, because a Blog entry runs no
# script (its sandbox and its policy both forbid one), and the report's frame
# page runs nothing either. The target is the bar itself, at the very top of
# the body and hidden, so loading the fragment scrolls nowhere.
FRAMED_ID = "ns-framed"

# The site's own Inter files (site.css's @font-face), by root path: every
# document this is put into is served from the site's origin, and a Blog
# entry's policy allows fonts from that origin only. Under a family name of the
# bar's own, so a document that declares an "Inter" of its own is not mixed in.
_DOC_FONT = "NS Site Inter"
_DOC_FONT_CSS = """
@font-face {
  font-family: "NS Site Inter";
  font-style: normal;
  font-weight: 400 600;
  font-display: swap;
  src: url("/assets/inter-latin-ext.woff2") format("woff2");
  unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF,
    U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020,
    U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF;
}
@font-face {
  font-family: "NS Site Inter";
  font-style: normal;
  font-weight: 400 600;
  font-display: swap;
  src: url("/assets/inter-latin.woff2") format("woff2");
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA,
    U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193,
    U+2212, U+2215, U+FEFF, U+FFFD;
}
"""

# What a document adds to SITE_NAV_CSS:
# * the face above in place of "Inter";
# * the bar above anything the document positions (the report paints a fixed
#   background as its first element);
# * the document's own lockup HIDDEN, framed or not, so a page never shows two
#   logos. `visibility`, not `display`: the lockup shares a header row with the
#   document's date and desk line, which keep their places.
# * the bar gone when the document was loaded as `#ns-framed`.
DOCUMENT_CSS = _DOC_FONT_CSS + SITE_NAV_CSS + f"""
.ns-site-bar.ns-doc-bar {{
  --font-heading: "{_DOC_FONT}", system-ui, sans-serif;
  --font-body: "{_DOC_FONT}", system-ui, sans-serif;
  position: relative;
  z-index: 10;
  display: block;
  margin: 0;
  padding: 0;
  background: transparent;
}}
.ns-site-bar.ns-doc-bar .ns-nav {{
  margin: 0; max-width: none; width: auto; position: static; background: transparent;
}}
.ns-site-bar.ns-doc-bar .ns-mark {{ max-width: none; margin: 0; }}
.ns-site-bar.ns-doc-bar .ns-navlink, .ns-site-bar.ns-doc-bar .ns-brand,
.ns-site-bar.ns-doc-bar .ns-crumb {{ margin: 0; padding: 0; background: none; border: 0; }}
.ns-site-bar.ns-doc-bar:target {{ display: none; }}
.lockup {{ visibility: hidden !important; }}
"""

_HEAD_END_RE = re.compile(r"</head\s*>", re.I)
_BODY_OPEN_RE = re.compile(r"<body\b[^>]*>", re.I)


def page_parts(root, template):
    """What a generated page takes from the tracked ``template`` under ``root``:
    ``{"nav", "icons", "styles", "footer"}``, each exactly as that file has it.
    ``None`` when the file is not there or ANY of them is missing.

    Used by the blog writer (``blog.html``) and the trade and report pages
    (``ideas.html``, ``report.html``), which live in different services and so
    share it here. Read as bytes and decoded, so no newline is translated: the
    menu is the file's own bytes. A piece holding a script is refused: a
    generated page adds the scripts it runs itself."""
    try:
        raw = (pathlib.Path(root) / template).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    navs = PAGE_NAV_RE.findall(raw)
    markup = _COMMENT_RE.sub("", raw)
    icons, styles = _ICON_RE.findall(markup), _STYLE_RE.findall(markup)
    feet = _FOOT_RE.findall(markup)
    if len(navs) != 1 or len(feet) != 1 or not icons:
        return None
    if not any(_SITE_CSS in link for link in styles):
        return None
    pieces = (navs[0], feet[0], *icons, *styles)
    if any("<script" in piece.lower() for piece in pieces):
        return None
    return {"nav": navs[0], "icons": tuple(icons), "styles": tuple(styles), "footer": feet[0]}


def framed_src(url) -> str:
    """The address a site frame loads a document at: ``url#ns-framed``."""
    return f"{url}#{FRAMED_ID}"


def inject_into_document(document, *, current, site_host=None, page=None) -> str:
    """``document`` with the site's bar at the top of its body.

    The stylesheet goes in before ``</head>``, the bar straight after
    ``<body…>``. ``current`` is the menu item to mark (``BLOG``, ``REPORT``).
    ``document`` comes back UNCHANGED when the menu cannot be built, when it has
    no head or body to place them in, or when it already carries the bar -- so
    running this twice, or on a document it cannot read, changes nothing, and a
    document never loses its own lockup without gaining the site's."""
    if not isinstance(document, str) or f'id="{FRAMED_ID}"' in document:
        return document
    nav = site_nav_html(site_host=site_host, page=page, current=current)
    head_end, body_open = _HEAD_END_RE.search(document), _BODY_OPEN_RE.search(document)
    if not nav or not head_end or not body_open or head_end.start() > body_open.start():
        return document
    bar = f'\n<div class="ns-site-bar ns-doc-bar" id="{FRAMED_ID}">{nav}</div>\n'
    style = f"<style>{DOCUMENT_CSS}</style>\n"
    at_body = body_open.end()
    return (document[:head_end.start()] + style + document[head_end.start():at_body]
            + bar + document[at_body:])
