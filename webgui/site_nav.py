"""The public site's menu, drawn at the top of every public live screen.

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
import html
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
# A plain page on the site, with an optional fragment: report.html,
# index.html#community. Nothing with a scheme, a host, a slash or a dot-dot.
_PLAIN_HREF_RE = re.compile(r"[a-z0-9-]+\.html(#[a-z0-9-]+)?")
_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")

# Every live screen belongs to the Live Desk, so that is the item marked as the
# page you are on, as live.html marks it.
_CURRENT = "live.html"

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


def site_nav_html(screen_title="", *, site_host=None, page=None) -> str:
    """The site's menu with absolute addresses, or ``""`` when it cannot be built.

    ``screen_title`` is set after the wordmark as the site's own breadcrumb
    (``.ns-crumb``), so a visitor still reads which screen this is. The sticky
    class is dropped: a live screen pins its own table headers to the top of
    the window, and a pinned menu would sit over them.

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
    nav = nav.replace(f'href="{base}{_CURRENT}"',
                      f'href="{base}{_CURRENT}" aria-current="page"', 1)
    if screen_title:
        crumb = f'<span class="ns-crumb">{html.escape(str(screen_title))}</span>'
        nav = nav.replace('<span class="ns-spacer"></span>',
                          f'{crumb}\n    <span class="ns-spacer"></span>', 1)
    return nav


# The menu's styling, from deploy/site/assets/nocturne.css (the tokens, .btn)
# and site.css (the menu rules), every rule scoped under `.ns-site-bar` so none
# of it reaches the screen below. ⚠ The documented escape hatch: the menu is a
# raw HTML string inside `ui.html`, which no `.classes()` can reach.
# webgui/tests/test_site_nav.py compares each declaration with the site's own
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
  padding: 16px clamp(16px, 3vw, 40px);
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
}
"""
