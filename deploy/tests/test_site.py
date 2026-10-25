"""The public site at neuralstrike.co, checked as a tree of files.

``deploy/caddy/tests/test_caddyfile.py`` asserts the EDGE serves this directory
and nothing above it. These tests assert what is IN it -- which the Caddy suite
cannot see, and which a browser only reveals to whoever looks at the live page.

Three failure modes motivate the file, all of them silent:

* **a renamed or missing image.** The pages reference images by path. Nothing breaks at deploy time; the page simply shows a broken image to
  every visitor until somebody scrolls to that screen.
* **a leaked reference to the app.** The public site must not name the app's
  hostname, its port, or a loopback address. The Caddy config is already pinned;
  the HTML was not.
* **an off-origin request creeping back.** Inter is self-hosted precisely so the
  site calls nobody. The design's stylesheet shipped its own Google Fonts
  ``@import``, which is exactly how such a thing returns.

Run with ``.venv/bin/python -m pytest deploy/site``.
"""
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

import repo_paths  # noqa: E402

SITE = pathlib.Path(repo_paths.SITE_ROOT)

# ⚠ EVERY page belongs here. A page left out is not partially checked, it is
# UNCHECKED -- no link resolution, no app-host guard, no origin guard. The
# glossary shipped with none of them until it was added.
PAGES = ("index.html", "live.html", "glossary.html", "report.html",
         "ideas.html", "blog.html")

# The site calls nobody. Empty on purpose, and widening it is a decision:
# every entry is a third party learning the IP of everyone who loads the page.
ALLOWED_ORIGINS: tuple[str, ...] = ()

# Where a visitor may be CHOOSING to go. Five are the community block on the
# landing page; the sixth is the public live origin, which every tile on
# live.html links to.
#
# ⚠ This is not the same permission as ALLOWED_ORIGINS above. A link is followed
# only when someone clicks it; an origin in that list is fetched on their behalf
# the moment the page loads. Adding to this list costs a visitor nothing until
# they act, which is why it may hold six entries while the other holds none.
# The live origin is here for exactly that reason: the grid LINKS to it and
# loads nothing from it -- the tiles are local captures, not embeds.
ALLOWED_OUTBOUND = (
    "https://discord.gg/",
    "https://t.me/",
    "https://x.com/",
    "https://www.facebook.com/",
    "https://www.instagram.com/",
    f"https://{repo_paths.LIVE_HOST}",
)

# References that are RUNTIME STATE rather than files in this repo, and so
# cannot be resolved on disk here.
#
# ⚠ An exemption, never a deletion of the check. `live/<slug>.webp` is written
# by tools/capture_live_shots.py every 15 minutes on the box that serves the
# site, and gitignored -- committed, the captures would dirty prod's tree the
# moment the timer first fires, and tools/promote.sh refuses a dirty tree. The
# guarantee that these paths are RIGHT comes from
# test_the_live_grid_offers_every_published_screen instead: the slugs are
# checked against webgui/live_screens.py, which is also what the capture script
# names its files from.
# `reports/` is the same shape: the market reports are uploaded into the served
# root after each scheduled run and gitignored for the same promote reason. What
# pins those paths instead is test_the_report_page_frames_the_latest_report.
# `ideas/` too: the trade idea cards are written by services/options_svc/site_ideas.py
# after each post, and only ideas.js (never the markup) names a card.
# `blog/` last: each entry's page, its framed document, the typefaces and the
# entry sitemap are written by services/blog_svc/sitewriter.py when the operator
# publishes, and only blog.js (never the markup) names an entry. What pins those
# paths instead is test_the_blog_script_links_an_entry_where_the_service_writes_it
# and test_the_blog_script_and_the_service_agree_on_what_a_slug_is.
GENERATED_REF_PREFIXES = ("live/", "reports/", "ideas/", "blog/")


# Attributes that make the browser fetch something or follow somewhere.
REF_RE = re.compile(r'(?:href|src)="([^"]+)"')
# url(...) inside a stylesheet -- how the @font-face faces are reached.
CSS_URL_RE = re.compile(r'url\(["\']?([^)"\']+)["\']?\)')

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)


def _text(name):
    return (SITE / name).read_text(encoding="utf-8")


def _markup(name):
    """The document with its comments removed.

    These files carry long explanatory comments that quote the very markup and
    CSS they are explaining -- ``<section class="ns-screen">``, the removed
    ``@import`` line. A structural check reading raw text counts those quotes as
    real, so structure is asserted against this and never against ``_text``.

    ⚠ The reverse holds for the leak checks: a hostname or a port sitting in a
    comment is still published to the internet, so those read ``_text``.
    """
    return HTML_COMMENT_RE.sub("", _text(name))


def _css(name):
    return CSS_COMMENT_RE.sub("", _text(name))


def _refs(text):
    """Every href/src in a document, in source order."""
    return REF_RE.findall(text)


@pytest.fixture(scope="module")
def pages():
    """Comment-free, because most assertions here are structural.
    The leak checks below deliberately re-read the raw file instead."""
    return {name: _markup(name) for name in PAGES}


# --- A. the tree is there at all --------------------------------------------

def test_every_page_exists():
    """Not vacuous: without this, a missing page makes the rest of this module
    silently assert nothing at all."""
    for name in PAGES:
        assert (SITE / name).is_file(), f"{name} is missing from {SITE}"


def test_the_placeholder_is_gone():
    """The one-pager this replaced was scaffolding with REPLACE-ME copy in it.
    Shipping that to the internet is the failure this catches."""
    for name in PAGES:
        assert "REPLACE-ME" not in _text(name), f"{name} still carries placeholder copy"


# --- B. every internal reference resolves -----------------------------------

def test_every_internal_reference_resolves_to_a_file(pages):
    """THE ONE THAT CATCHES A RENAMED SCREENSHOT.

    A broken href is invisible until a visitor clicks it and a broken <img> is
    invisible until one scrolls to it, so neither shows up in any other check
    here -- and by then it is on the public internet.

    ``GENERATED_REF_PREFIXES`` are skipped: those files are written on the
    serving box, not committed. See it for why, and for what pins them instead.
    """
    for name, text in pages.items():
        for ref in _refs(text):
            if ref.startswith(("#", "http://", "https://", "mailto:", "data:")):
                continue
            if ref.startswith(GENERATED_REF_PREFIXES):
                continue
            target = ref.split("#", 1)[0].split("?", 1)[0]
            # A leading "/" is root-absolute, and this site IS served at the
            # root -- `/favicon.ico` is a path browsers request by convention,
            # so it has to be declared that way rather than relatively.
            assert (SITE / target.lstrip("/")).is_file(), (
                f"{name} references missing {target}")


def test_the_stylesheets_own_urls_resolve():
    """The @font-face faces are reached from CSS, not from any page, so the
    check above cannot see them -- and a missing font degrades to system-ui,
    which looks *almost* right and so goes unnoticed."""
    for sheet in ("assets/site.css", "assets/nocturne.css"):
        for ref in CSS_URL_RE.findall(_css(sheet)):
            if ref.startswith(("http://", "https://", "data:", "#")):
                continue      # "#..." is an SVG fragment, checked just below
            assert (SITE / "assets" / ref).is_file(), f"{sheet} references missing {ref}"


def test_every_svg_fragment_the_css_paints_with_exists():
    """``fill: url(#id)`` names an element in the SAME DOCUMENT, not a file.

    The hero mark's rule is painted with a gradient defined inside that page's
    inline SVG, so the rule only works on a page that carries the gradient. A
    fragment that resolves nowhere does not error -- SVG paints the shape with
    NOTHING, and the element silently disappears.
    """
    frags = {r[1:] for r in CSS_URL_RE.findall(_css("assets/site.css"))
             if r.startswith("#")}
    assert frags, "no SVG fragments referenced; drop this test with them"
    for frag in frags:
        owners = [n for n in PAGES if f'id="{frag}"' in _markup(n)]
        assert owners, f"site.css paints with url(#{frag}), which no page defines"


def test_every_anchor_target_exists(pages):
    """An in-page link to a section that was renamed scrolls nowhere."""
    for name, text in pages.items():
        ids = set(re.findall(r'\bid="([^"]+)"', text))
        for ref in _refs(text):
            if ref.startswith("#") and len(ref) > 1:
                assert ref[1:] in ids, f"{name} links to #{ref[1:]}, which has no element"


# --- C. the Gallery, retired 2026-10-24 --------------------------------------

def test_the_gallery_is_retired_and_nothing_links_to_it(pages):
    """The Live Desk shows every published screen live, so the Gallery's
    pictures of the private app went, with the daily recapture behind them.
    Its address answers with a redirect to live.html (deploy/caddy, pinned in
    test_a_retired_page_redirects_to_its_replacement), so no page may link to
    it: a link would be a redirect on every click, and a sign the page is back."""
    assert not (SITE / "gallery.html").exists()
    assert not (SITE / "assets/gallery.js").exists()
    for name, text in pages.items():
        assert "gallery.html" not in text, f"{name} still links the Gallery"
    assert "gallery" not in _text("sitemap.txt")
    assert ".ns-gallery" not in _css("assets/site.css")


# --- C2. the mark, which exists in six copies -----------------------------

# The two optical sizes. LARGE is the site's nav and hero; SMALL is the favicon
# and the app's 28px header slot, drawn heavier so a 1-device-pixel rule
# survives at 16px. They are DIFFERENT DRAWINGS on purpose -- scaling one to
# both sizes is the mistake optical sizing exists to prevent.
MARK_LARGE = ("M20 11 L32 23 L44 11", "M20 53 L32 41 L44 53")
MARK_SMALL = ("M22 12 L32 23.5 L42 12", "M22 52 L32 41 L42 52")

MARK_LARGE_FILES = ("index.html", "live.html", "report.html", "ideas.html",
                    "blog.html", "assets/mark.svg")
MARK_SMALL_FILES = ("assets/favicon.svg",)


def test_every_copy_of_the_mark_is_the_same_drawing():
    """THE MARK LIVES IN SEVEN FILES AND NOTHING MAKES THEM AGREE.

    It cannot be one shared file: an <img> gets no stylesheet, so the favicon,
    the social card and the app header need literal colours, while the site's
    nav and hero inline the geometry to take the page's accent from CSS. The
    duplication buys that, and this is the rent.

    A mark that drifts between surfaces does not look broken anywhere -- it
    looks slightly different in the tab than in the header, which nobody
    reports and everybody half-notices.
    """
    for name in MARK_LARGE_FILES:
        text = _text(name)
        for path_d in MARK_LARGE:
            assert path_d in text, f"{name} does not carry the large mark path {path_d!r}"
        for path_d in MARK_SMALL:
            assert path_d not in text, f"{name} carries the SMALL mark; it should be large"

    for name in MARK_SMALL_FILES:
        text = _text(name)
        for path_d in MARK_SMALL:
            assert path_d in text, f"{name} does not carry the small mark path {path_d!r}"


def test_the_apex_clears_the_rule():
    """THE GAP IS THE MEANING, and the first draw did not have one.

    The chevrons converge ON a level; they do not touch it. Drawn first with the
    apexes ~2 units off the rule, the stroke width closed the gap and the mark
    rendered as an X struck through -- a cancel icon. Every test passed; only
    the render showed it.

    Checked as arithmetic rather than by eye: apex + half the stroke width must
    stay clear of the rule's edge.
    """
    import re as _re
    for name, (rule_y, rule_h) in ((("assets/mark.svg"), (30.75, 2.5)),
                                   (("assets/favicon.svg"), (30.0, 4.0))):
        text = _text(name)
        paths = _re.findall(r'<path d="M\d+ [\d.]+ L32 ([\d.]+) L\d+ [\d.]+"[^>]*?'
                            r'stroke-width="([\d.]+)"', text, _re.S)
        assert len(paths) == 2, f"{name}: expected 2 chevrons, found {len(paths)}"
        for apex_s, w_s in paths:
            apex, half = float(apex_s), float(w_s) / 2
            if apex < rule_y:                      # the upper chevron
                clearance = rule_y - (apex + half)
            else:                                  # the lower one
                clearance = apex - half - (rule_y + rule_h)
            assert clearance >= 2.0, (
                f"{name}: a chevron clears the rule by only {clearance:.2f} units. "
                f"Below ~2 the stroke closes the gap and the mark reads as an X.")


def test_the_app_and_the_site_draw_the_same_mark():
    """The web GUI's header mark is a different FILE with a different accent --
    the two surfaces run different palettes on purpose -- but it must not be a
    different SHAPE, or they stop being one brand.

    ⚠ It takes the LARGE drawing. `.brand-mark` renders at 44px, which is well
    inside large territory; it shipped briefly with the small variant because a
    comment claimed the header slot was 28px and nobody measured it. At 44 the
    small variant's heavier strokes read as clumsy.
    """
    app = (pathlib.Path(repo_paths.REPO_ROOT) / "webgui/static/img/neuralstrike-mark.svg")
    assert app.is_file(), "the app's header mark is missing"
    text = app.read_text(encoding="utf-8")
    for path_d in MARK_LARGE:
        assert path_d in text, f"the app mark does not carry {path_d!r}"
    for path_d in MARK_SMALL:
        assert path_d not in text, (
            "the app mark uses the SMALL drawing, but it renders at 44px")


def test_the_social_card_exists_and_is_the_right_shape():
    """og:image is the brand's most-seen surface: every link pasted into Discord
    or Telegram renders from it. 1200x630 is what the crawlers expect, and the
    declared dimensions have to match the file or the card is letterboxed."""
    from struct import unpack
    png = pathlib.Path(SITE / "assets/social.png")
    assert png.is_file(), "no social card"
    head = png.read_bytes()[:24]
    # Bytes 1-3 of a PNG are the ASCII letters "PNG" -- checked that way
    # rather than against the full 8-byte signature, whose escapes have
    # been mangled by shell quoting three times in this session.
    # Bytes 1-3 of a PNG are the ASCII letters "PNG". Compared that way
    # rather than against the full 8-byte signature, whose escapes were
    # mangled by shell quoting three times while this was written.
    assert head[1:4] == b"PNG", "not a PNG"
    w, h = unpack(">II", head[16:24])
    assert (w, h) == (1200, 630), f"social card is {w}x{h}, expected 1200x630"

    page = _markup("index.html")
    assert f'content="{w}"' in page and f'content="{h}"' in page, (
        "og:image:width/height do not match the file")


def test_the_landing_page_declares_a_social_preview():
    """Without these every link to the site previews as a bare URL.

    ⚠ Matched as a WHOLE property value, closing quote included. A substring
    test cannot see `og:image` go missing, because `og:image:width` and
    `og:image:alt` both contain it -- the guard's first draft passed with the
    image tag deleted, which mutation testing caught and reading it did not.
    """
    page = _markup("index.html")
    for tag in ("og:title", "og:description", "og:image", "og:url",
                "og:image:alt", "twitter:card"):
        attr = "name" if tag.startswith("twitter:") else "property"
        assert f'{attr}="{tag}"' in page, f"index.html has no {tag}"
    assert "https://neuralstrike.co/assets/social.png" in page, (
        "og:image must be absolute -- a crawler resolves it against nothing")


def test_the_retired_tagline_is_gone():
    """\"AI option signals\" is the phrase every signal-selling account uses, and
    the site's own community section promises no signal-selling."""
    for name in PAGES:
        assert "AI option signals" not in _text(name), (
            f"{name} still carries the retired tagline")


def test_the_site_answers_the_icon_paths_browsers_ask_for_unprompted():
    """A BROWSER REQUESTS THESE WHETHER OR NOT THE PAGE MENTIONS THEM.

    The site declared only an SVG favicon, which Chrome, Firefox and Edge take
    happily. ⚠ Safari does not support SVG favicons at all: it falls back to
    ``/favicon.ico``, which this static tree answered with **404**, so the site
    had no icon there. And an iPhone home-screen shortcut asks for
    ``/apple-touch-icon.png`` -- without one it saves a screenshot thumbnail
    instead of the mark.

    Both live at the ROOT because that is where the request goes, declared or
    not. Checked as files on disk, since Caddy serves this tree literally.
    """
    for name in ("favicon.ico", "apple-touch-icon.png"):
        assert (SITE / name).is_file(), (
            f"/{name} is not in the served root; browsers asking for it get a 404")


def test_the_apple_touch_icon_is_opaque_and_full_bleed():
    """iOS masks the corners itself and composites onto BLACK.

    A transparent icon therefore shows black behind the mark, and a
    pre-rounded one shows its own corners inside Apple's squircle. 180x180 is
    what current iPhones request.
    """
    from struct import unpack

    png = SITE / "apple-touch-icon.png"
    head = png.read_bytes()[:26]
    # Bytes 1-3 of a PNG are the ASCII letters "PNG". Compared that way
    # rather than against the full 8-byte signature, whose escapes were
    # mangled by shell quoting three times while this was written.
    assert head[1:4] == b"PNG", "not a PNG"
    w, h = unpack(">II", head[16:24])
    assert (w, h) == (180, 180), f"apple-touch-icon is {w}x{h}, expected 180x180"
    colour_type = head[25]
    assert colour_type in (0, 2), (
        f"apple-touch-icon has an alpha channel (colour type {colour_type}); "
        f"iOS composites transparency onto black")


def test_the_favicon_ico_carries_the_small_sizes():
    """The point of the format is that the browser picks a size instead of
    scaling one bitmap. A single-size .ico is a .png with extra steps."""
    from PIL import Image

    with Image.open(SITE / "favicon.ico") as im:
        sizes = {tuple(s) for s in im.info.get("sizes", ())}
    for want in ((16, 16), (32, 32)):
        assert want in sizes, f"favicon.ico has no {want[0]}px entry; has {sorted(sizes)}"


def test_every_page_declares_all_three_icons():
    """One of the three is enough for any given browser, and no single one is
    enough for all of them."""
    for name in PAGES:
        markup = _markup(name)
        assert 'href="assets/favicon.svg"' in markup, f"{name} lost the SVG icon"
        assert 'href="/favicon.ico"' in markup, f"{name} lost the .ico fallback"
        assert 'rel="apple-touch-icon"' in markup, f"{name} lost the iOS icon"


# --- C3. the glossary, which has one source and two artifacts ---------------

GLOSSARY_MD = (pathlib.Path(repo_paths.REPO_ROOT)
               / "docs" / "manuals" / "glossary" / "glossary.md")


def _glossary_terms_from_source():
    """Every bolded term at the start of a definition line in the markdown."""
    md = GLOSSARY_MD.read_text(encoding="utf-8")
    return {m.group(1).strip() for m in
            re.finditer(r"^\*\*([^*]+)\*\*\s+[\u2014-]\s+", md, re.M)}


def _glossary_terms_on_page():
    """The rendered terms, UNESCAPED so they compare against the markdown.

    The page is correctly escaped -- "P&L attribution" ships as "P&amp;L
    attribution" -- so a raw string comparison reports a drift that is only the
    escaping. Normalising here rather than loosening the assertion keeps the
    test able to see a real divergence.
    """
    import html as _html

    return {_html.unescape(t) for t in
            re.findall(r"<dt>([^<]+)</dt>", _markup("glossary.html"))}


def test_the_site_glossary_carries_every_term_in_the_source():
    """ONE SOURCE, TWO ARTIFACTS, TWO DIFFERENT BUILDERS.

    ``docs/manuals/glossary/glossary.md`` feeds both the app's fifth manual
    (via ``build_docs.py``) and this site page (via a one-shot generator). Edit
    the markdown and rebuild only the manual, and the public page silently keeps
    saying the old thing -- with nothing failing, because each artifact is
    internally consistent.

    Compared as a SET of terms rather than byte-for-byte: the two renderings are
    deliberately different documents -- one is a printable reference, the other a
    web page with its own navigation -- so only the content they share can be
    pinned.
    """
    source = _glossary_terms_from_source()
    assert len(source) > 80, f"only {len(source)} terms parsed; the format changed"

    missing = source - _glossary_terms_on_page()
    assert not missing, (
        f"{len(missing)} term(s) in glossary.md are not on the site page — "
        f"run tools/generate_glossary_page.py. e.g. {sorted(missing)[:5]}")


def test_the_site_glossary_invents_nothing():
    """The other direction. A term on the page that is not in the source means
    the page was hand-edited, and the next regeneration will silently drop it."""
    source = _glossary_terms_from_source()
    extra = _glossary_terms_on_page() - source
    assert not extra, (
        f"the site glossary shows {len(extra)} term(s) absent from glossary.md; "
        f"a regeneration would lose them: {sorted(extra)[:5]}")


def test_the_glossary_is_a_definition_list_not_bold_paragraphs():
    """A glossary's shape IS a definition list, and that is what a screen reader
    announces and what find-in-page matches. Rendering it as paragraphs of
    <strong> loses both and looks identical."""
    page = _markup("glossary.html")
    assert page.count("<dt>") == page.count("<dd>"), "unbalanced <dt>/<dd>"
    assert page.count("<dt>") > 80, "the definitions are not marked up as terms"


def test_the_glossary_is_listed_as_a_manual_too():
    """It is a page on the site AND the app's fifth manual. The app registry is
    also the serving whitelist, so a built-but-unlisted manual is unreachable."""
    reg = (pathlib.Path(repo_paths.REPO_ROOT)
           / "webgui" / "pages" / "manuals.py").read_text(encoding="utf-8")
    assert '"glossary"' in reg, "the glossary is not registered in the app"
    built = (pathlib.Path(repo_paths.REPO_ROOT)
             / "docs" / "manuals" / "glossary" / "glossary.html")
    assert built.is_file(), "the glossary manual has not been built"


# --- C4. the live grid, whose tiles come from the app's published table ------

def _live_screens():
    """``webgui/live_screens.py``, loaded BY PATH rather than by import.

    ``webgui/`` holds top-level modules named ``main``, ``proxy`` and ``auth``.
    Putting that directory on ``sys.path`` -- what the obvious
    ``sys.path.insert`` would do -- does it for the whole pytest session, since
    ``tests deploy tools/tests`` run in one command, and any of those names can
    then shadow a same-named module another suite imports. Loading one file by
    its path reaches nothing else.

    A REAL import, not an AST parse: ``live_screens`` imports ``dataclasses``
    and nothing more, so there is no import to be afraid of here.
    ``shared/tests/test_cross_tier_mirrors.py`` parses instead because ITS
    subjects sit in packages that trigger the documented cross-app ``scoring``
    collision -- a reason that does not apply to a table of fourteen strings.
    """
    import importlib.util

    path = pathlib.Path(repo_paths.WEBGUI) / "live_screens.py"
    spec = importlib.util.spec_from_file_location("_live_screens_site_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# One picture tile on live.html: (its href, the capture's slug).
TILE_RE = (r'<a class="ns-live-tile ns-desk-spot" href="([^"]+)"[^>]*>\s*'
           r'<span class="ns-desk-thumb"><img src="live/([^"]+)\.webp"')


def _tiled():
    """The published screens drawn as tiles. The interactive tools are
    published but untiled (``Screen.tile``) -- a tool card reaches each one."""
    return [s for s in _live_screens().SCREENS if s.tile]


def test_the_interactive_tools_are_not_tiled():
    """Removed from the grid 2026-09-21: an empty form makes a blank tile."""
    screens = _live_screens().SCREENS
    for s in screens:
        if not s.tile:
            assert f"live/{s.slug}.webp" not in _markup("live.html"), s.slug


def test_a_sub_view_is_a_link_under_its_parent_tile_and_nowhere_else():
    """Screen.parent draws a small link inside the parent tile's group, so a
    sub-view is one click from the picture it belongs to."""
    import html as _html

    markup = _markup("live.html")
    screens = _live_screens().SCREENS
    by_slug = {s.slug: s for s in screens}
    groups = re.findall(r'<div class="ns-live-group">(.*?)</div>', markup, re.S)
    parents = {s.parent for s in screens if s.parent}
    assert len(groups) == len(parents), f"{len(groups)} groups for {parents}"
    for group in groups:
        head = re.search(TILE_RE, group)
        assert head, "a group with no tile"
        parent = head.group(2)
        assert by_slug[parent].tile, parent
        sub = re.search(r'<nav class="ns-live-sub"[^>]*>(.*?)</nav>', group, re.S)
        links = [(h, _html.unescape(t)) for h, t in
                 re.findall(r'<a href="([^"]+)">([^<]+)</a>', sub.group(1))]
        want = [(f"https://{repo_paths.LIVE_HOST}{s.route}", s.title)
                for s in screens if s.parent == parent]
        assert links == want, (parent, links)
        assert not any(s.tile for s in screens if s.parent == parent)


def test_the_live_grid_offers_every_published_screen():
    """The grid and the live app read ONE source, so a screen cannot be
    published without a tile or tiled without being published.

    The captures themselves are not on disk here (see
    ``GENERATED_REF_PREFIXES``), so this is what makes the fourteen ``<img>``
    paths right: the slug in the ``src`` is the slug the capture script names
    its output from.

    ⚠ Captions are compared UNESCAPED, the way ``_glossary_terms_on_page`` does
    and for the same reason: the page correctly ships "Sector &amp; Industry",
    so a raw comparison would report a drift that is only the escaping.
    """
    import html as _html

    text = _text("live.html")
    captions = {_html.unescape(c) for c in
                re.findall(r'<span class="ns-live-cap">([^<]+)</span>',
                           _markup("live.html"))}
    for s in _tiled():
        assert f"{s.slug}.webp" in text, f"no tile for {s.slug}"
        assert s.title in captions, f"no caption for {s.title}"
    for s in _live_screens().SCREENS:
        if s.parent:            # a sub-view: a link, not a tile
            assert f'<a href="https://{repo_paths.LIVE_HOST}{s.route}">' in text, s.slug


def test_the_grid_tiles_nothing_the_app_does_not_publish():
    """The other direction, which a link-checker never covers: a tile left
    behind after a screen was unpublished points at a route that 404s, and the
    picture beside it keeps the old capture until someone deletes the file."""
    slugs = {s.slug for s in _tiled()}
    tiled = set(re.findall(r'src="live/([^"]+)\.webp"', _markup("live.html")))
    assert tiled == slugs, (
        f"tiled but not published: {sorted(tiled - slugs)}; "
        f"published but not tiled: {sorted(slugs - tiled)}")


def test_every_tile_links_to_its_own_route_on_the_live_origin():
    """A tile that shows one screen and opens another is worse than a missing
    tile, and looks like nothing at all until someone clicks."""
    markup = _markup("live.html")
    tiles = re.findall(TILE_RE, markup)
    by_slug = {s.slug: s.route for s in _tiled()}
    assert len(tiles) == len(by_slug), f"{len(tiles)} tiles parsed"
    for href, slug in tiles:
        assert href == f"https://{repo_paths.LIVE_HOST}{by_slug[slug]}", (href, slug)


# The Live Desk's groups (2026-10-24), in page order: the section id, the jump
# pill's label, and the tiles inside. The arrangement is the page's own -- it
# exists nowhere in live_screens.py -- so this is where it is written down.
DESK_GROUPS = (
    ("regime", "Market regime", ("desk", "macro", "sentiment", "bullbear")),
    ("dealer", "Dealer positioning", ("gamma", "net-premium", "flow", "opportunity")),
    ("rotation", "Sector rotation", ("sectors", "rotation", "rrg", "momentum")),
)


def _desk_sections():
    """{section id: its markup} for every group section on live.html."""
    return dict(re.findall(
        r'<section class="ns-desk-group" id="([^"]+)">(.*?)</section>',
        _markup("live.html"), re.S))


def test_every_tile_sits_in_exactly_one_group():
    """A screen added to the table needs a group as well as a tile. Without
    this a new tile could land outside every section -- or a slug could be
    pasted into two -- and the grid test above would still pass."""
    sections = _desk_sections()
    assert list(sections) == [gid for gid, _, _ in DESK_GROUPS]
    placed = []
    for gid, _, slugs in DESK_GROUPS:
        got = tuple(slug for _, slug in re.findall(TILE_RE, sections[gid]))
        assert got == slugs, (gid, got)
        placed += got
    assert sorted(placed) == sorted(s.slug for s in _tiled()), (
        "the groups and the published tiles disagree")


def _desk_tools():
    return [s for s in _live_screens().SCREENS if not s.tile and not s.parent]


def test_every_tool_has_a_card_in_table_order():
    """The tool cards are the published, untiled screens -- each its own card,
    the three Strategy Tools included.
    Numbered in the order the table lists them."""
    m = re.search(r'<section class="ns-desk-tools-section" id="tools">(.*?)</section>',
                  _markup("live.html"), re.S)
    assert m, "live.html has no tools section"
    cards = re.findall(
        r'<a class="ns-desk-tool ns-desk-spot" href="([^"]+)">.*?'
        r'<span class="ns-desk-step">(\d+)</span>.*?'
        r'<span class="ns-desk-tool-name">([^<]+)</span>', m.group(1), re.S)
    want = [(f"https://{repo_paths.LIVE_HOST}{s.route}", f"{i:02d}", s.title)
            for i, s in enumerate(_desk_tools(), 1)]
    assert cards == want, cards


def test_the_jump_pills_reach_each_section_and_count_it():
    """A count on a pill is a fact that goes stale in silence, like the lede's."""
    pills = re.findall(
        r'<a class="ns-desk-pill" href="#([^"]+)">.*?</svg>([^<]+)<span>(\d+)</span></a>',
        _markup("live.html"))
    want = [("tools", "Tools", str(len(_desk_tools())))] + [
        (gid, label, str(len(slugs))) for gid, label, slugs in DESK_GROUPS]
    assert pills == want, pills


def test_the_eyebrow_counts_what_is_open():
    eyebrow = re.search(r'<p class="ns-desk-eyebrow">(.*?)</p>', _markup("live.html"), re.S)
    assert eyebrow, "live.html has no eyebrow"
    total = len(_tiled()) + len(_desk_tools())
    assert f"{total} open, nothing to sign into" in eyebrow.group(1)


def test_the_menu_button_is_named_live_desk():
    """Renamed from "Live screens" on 2026-10-24, on every page at once."""
    for name in PAGES:
        button = re.search(r'<a class="btn btn-primary" href="live\.html"[^>]*>([^<]+)</a>',
                           _nav(name))
        assert button and button.group(1) == "Live Desk", f"{name}: {button and button.group(1)}"
    assert "<title>NeuralStrike — Live Desk</title>" in _markup("live.html")


def test_the_grid_links_to_the_live_origin_and_not_the_app():
    text = _text("live.html")
    assert repo_paths.LIVE_HOST in text
    assert repo_paths.APP_HOST not in text     # re-asserted; also pinned below


def test_the_placeholder_copy_is_gone():
    """live.html shipped saying "Not published yet" and "Nothing mounted"."""
    text = _text("live.html")
    for gone in ("Not published yet", "Nothing mounted", "Live view slot"):
        assert gone not in text


def test_the_live_page_is_indexable_now_that_it_has_content():
    """`noindex` was there because a placeholder in search results is worse
    than no result. It is not a placeholder any more, and a stale meta tag is
    invisible: nothing renders differently, the page simply never appears."""
    assert "noindex" not in _text("live.html")


def test_the_grid_carries_no_timestamp():
    """Baking a capture time into committed HTML would make a source file a
    build artifact -- and it would go stale in the one way a visitor cannot
    check, since the file is served long after the capture it claims. The grid
    is navigation; the live pages carry their own freshness."""
    markup = _markup("live.html")
    assert not re.search(r"\b(?:updated|captured|as of)\b\s*[:,]?\s*\d",
                         markup, re.I)
    assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b", markup)


_WORDS = {7: "seven", 12: "twelve", 19: "nineteen"}


def test_the_live_page_states_the_published_counts_in_words():
    """A COUNT IN COPY IS A FACT THAT GOES STALE IN SILENCE.
    live.html said "Twenty-four screens ...
    twenty read-only ... four tools" from 2026-09-21 until 2026-09-26, while the
    table published seventeen (twelve tiles and five tools), because the tiles
    are pinned against ``live_screens.SCREENS`` and the prose never was. (It is
    eighteen and six since Option Signals joined the tools on 2026-10-07.)

    Derived from the same table the route registration reads, so the words
    cannot be right by luck. ``_WORDS`` is deliberately narrow: a count it does
    not name fails here, which is the moment to add the word AND reread the
    lede. Since the Live Desk (2026-10-24) the lede describes the tools and the
    cards name them -- ``test_every_tool_has_a_card_in_table_order``.
    """
    screens = _live_screens().SCREENS
    tiles = sum(1 for s in screens if s.tile)
    tools = sum(1 for s in screens if not s.tile and not s.parent)
    total = tiles + tools
    lede = re.search(r'<p class="ns-live-lede">(.*?)</p>', _markup("live.html"), re.S)
    assert lede, "live.html has no lede"
    prose = " ".join(lede.group(1).split())
    for n in (total, tiles, tools):
        assert n in _WORDS, f"no word for {n}; add it and reread the lede"
    assert prose.startswith(f"{_WORDS[total].capitalize()} screens"), prose[:60]
    assert f"{_WORDS[tiles].capitalize()} are the readings" in prose, prose
    assert f"{_WORDS[tools].capitalize()} are tools" in prose, prose
    meta = re.search(r'<meta name="description" content="([^"]+)"', _text("live.html"))
    assert meta and meta.group(1).startswith(f"{_WORDS[total].capitalize()} NeuralStrike screens"), (
        "the meta description states a different count from the table")
    assert f"plus {_WORDS[tools]} tools" in meta.group(1)


DISCLAIMER = ("NeuralStrike — dealer flow, measured. Educational and simulation "
              "software, not investment advice. No orders are transmitted to any broker.")


def test_every_footer_carries_the_one_disclaimer_verbatim():
    """The 2026-09-26 rewrite reduced five phrasings of "paper only, no orders"
    to one sentence, reused byte-for-byte -- the same rule the app keeps in
    ``webgui/pages/copy.py``: two surfaces wording one condition differently
    read as a defect, not a style. report.html frames a document that carries
    its own footer, and the glossary is a reference page; the three pages that
    make the claim make it identically."""
    for name in ("index.html", "live.html", "ideas.html", "blog.html"):
        markup = " ".join(_markup(name).split())
        assert DISCLAIMER in markup, f"{name} does not carry the disclaimer verbatim"


def test_live_screens_is_the_primary_call_to_action():
    """The nav's own comment records that Live Screens was the design's primary
    button and lost the slot only because the page was an empty placeholder.
    It is not a placeholder any more, so this pins the swap back -- structurally,
    on the class, rather than on copy a later edit would break.

    ⚠ Reads ``_markup``, not ``_text``. The nav comment names BOTH hrefs while
    explaining the history, so a raw-text search matches inside the comment and
    passes for the wrong reason -- exactly what ``_markup`` exists to prevent.
    """
    nav = re.search(r"<nav\b.*?</nav>", _markup("index.html"), re.S)
    assert nav, "index.html has no <nav>"
    live = re.search(r'<a[^>]*href="live\.html"[^>]*>', nav.group(0))
    assert live, "no live.html link in the index nav"
    assert "btn-primary" in live.group(0), "Live Desk is not the primary button"
    assert nav.group(0).count("btn-primary") == 1, "more than one primary button"


def test_the_grid_embeds_nothing_and_runs_nothing():
    """The tiles are PICTURES. An <iframe> onto the live origin would put a
    NiceGUI session behind every tile -- fourteen per visitor -- and hand the
    static site a runtime dependency on the app being up.

    Scripting is checked too, against an exact list: the market glow every page
    carries for its own nav button, and since the Live Desk (2026-10-24)
    ``assets/live.js``, the cursor spotlight on a card. Anything else here
    could only be something that crept in."""
    markup = _markup("live.html")
    assert "<iframe" not in markup
    assert _scripts_besides_the_glow(markup) == ['<script src="assets/live.js" defer>']


# --- D. the site never points at the app ------------------------------------

def test_no_page_names_the_app_host():
    """Noise reduction rather than a security control -- the subdomain is in
    Certificate Transparency regardless -- but the public face should not point
    at the door. The Caddy half of this is
    test_the_app_host_is_not_advertised_by_the_public_block.

    Reads the RAW file, comments included: a hostname sitting in a comment is
    served to the internet exactly like one in an href."""
    for name in PAGES:
        text = _text(name)
        assert repo_paths.APP_HOST not in text, f"{name} names {repo_paths.APP_HOST}"


def test_no_page_names_a_loopback_address_or_the_app_port():
    """Raw text again, for the reason above."""
    for name in PAGES:
        text = _text(name)
        assert "127.0.0.1" not in text, f"{name} names a loopback address"
        assert "localhost" not in text, f"{name} names localhost"
        assert str(repo_paths.NICEGUI_PORT) not in text, f"{name} names the app port"


# --- E. nothing off-origin --------------------------------------------------

def test_no_page_reaches_an_external_origin(pages):
    """Inter is self-hosted so this site fetches nothing from anyone. An
    outbound LINK a visitor chooses to click is a different thing from a
    resource the page loads on their behalf, and only the latter is banned."""
    for name, text in pages.items():
        for ref in _refs(text):
            if not ref.startswith(("http://", "https://")):
                continue
            if ref.startswith(ALLOWED_OUTBOUND):
                continue
            assert ref.startswith(ALLOWED_ORIGINS or ("\0",)), (
                f"{name} loads or links {ref}, which is not an allowed origin")


def test_no_stylesheet_imports_a_remote_font():
    """The design system's sheet shipped with its own Google Fonts @import.
    Removing it is what makes the site call nobody; this is what keeps it
    removed when the sheet is next refreshed from the design bundle."""
    for sheet in ("assets/site.css", "assets/nocturne.css"):
        live = _css(sheet)
        assert "@import" not in live, f"{sheet} has an @import, which may be remote"
        for host in ("googleapis", "gstatic"):
            assert host not in live, f"{sheet} reaches {host}"
            # A commented-out URL is one uncomment away from being live again,
            # so it may only appear alongside a note saying it was REMOVED.
            if host in _text(sheet):
                assert "REMOVED" in _text(sheet), (
                    f"{sheet} mentions {host} without recording that it was removed")


def test_the_fonts_are_actually_present_and_are_woff2():
    """Self-hosting is only self-hosting if the files shipped. A woff2 whose
    bytes are an HTML error page renders as a silent fallback to system-ui."""
    fonts = sorted((SITE / "assets").glob("*.woff2"))
    assert fonts, "no self-hosted fonts, so the pages fall back to system-ui"
    for f in fonts:
        assert f.read_bytes()[:4] == b"wOF2", f"{f.name} is not a woff2 file"


def test_outbound_links_are_safe(pages):
    """A target=_blank without noopener hands the opened tab a handle on this
    one. Both community links open in a new tab."""
    for name, text in pages.items():
        for tag in re.findall(r"<a\b[^>]*>", text):
            if 'target="_blank"' in tag:
                assert "noopener" in tag, f"{name} has a target=_blank without noopener"


# --- F. the pages are real HTML ---------------------------------------------

def test_each_page_carries_its_own_content_without_scripting(pages):
    """The design's bundled build rendered a BLANK PAGE with JavaScript off --
    a ~700 KB runtime and no server-rendered markup. Rebuilding the pages was
    the whole point; this is the assertion that says so."""
    for name, text in pages.items():
        assert "<h1" in text or "<h2" in text, f"{name} has no heading in its source"
        assert "<nav" in text, f"{name} has no navigation in its source"


def test_no_page_carries_a_form_or_an_input(pages):
    """This tree is a file server. There is nowhere to POST, so a form could
    only lie about what it does with what it collects -- which is why the
    design's email capture was removed rather than pointed somewhere."""
    for name, text in pages.items():
        assert "<form" not in text, f"{name} has a form and no backend to receive it"
        assert "<input" not in text, f"{name} has an input and no backend to receive it"


def test_every_image_declares_its_size(pages):
    """Without width/height the page reflows as each screenshot arrives."""
    for name, text in pages.items():
        for tag in re.findall(r"<img\b[^>]*>", text):
            assert "width=" in tag and "height=" in tag, f"{name} has an unsized image: {tag[:70]}"
            assert "alt=" in tag, f"{name} has an image with no alt text: {tag[:70]}"


# --- C5. the market glow, which is decoration with a data dependency --------

GLOW_SCRIPTS = ('<script src="assets/market-clock.js" defer>',
                '<script src="assets/market-glow.js" defer>')


def _scripts_besides_the_glow(markup):
    """Every <script> opening tag on a page except the market glow's two.

    The pages that pin "runs nothing" still pin it for everything else: the
    glow is the one script each page carries on purpose (the nav button is on
    every page), and the exception names its two files exactly rather than
    excusing anything under ``assets/``."""
    return [tag for tag in re.findall(r"<script\b[^>]*>", markup)
            if tag not in GLOW_SCRIPTS]


def test_the_live_link_can_be_glowed():
    """Structural: the CSS rule and the class the script toggles must agree.

    Two files that never import each other, joined by a string. Rename the
    class in one and the glow simply never appears -- there is no error, no
    console warning, and the button looks exactly like the un-glowed state it
    is supposed to leave when the market shuts.
    """
    css = _css("assets/site.css")
    assert ".ns-market-open" in css
    js = _text("assets/market-glow.js")
    assert "ns-market-open" in js, "the script and the stylesheet disagree on the class"


def test_the_glow_is_green():
    """The one thing the request actually specifies -- and it must assert the
    COLOUR, not merely that a glow exists.

    ⚠ This test shipped asserting only ``box-shadow or filter``, under this same
    name and docstring. A red glow would have passed it. That is worse than a
    weak test: the name and the docstring both promised the colour, so a reader
    would reasonably stop looking. Rewritten to read the hue it claims.

    The rule builds every shade from one ``--ns-open`` custom property, so the
    channel check has exactly one place to look -- and pinning the property
    rather than the literal means a re-theme that keeps the variable keeps the
    test honest.
    """
    css = _css("assets/site.css")
    rule = re.search(r"\.ns-market-open\s*\{[^}]*\}", css)
    assert rule, "no .ns-market-open rule to check"
    body = rule.group(0)
    assert "box-shadow" in body or "filter" in body, "the glow has no glow"

    hexes = re.findall(r"#([0-9a-fA-F]{6})", body)
    assert hexes, "the glow colour is not a hex literal this test can read"
    r, g, b = (int(hexes[0][i:i + 2], 16) for i in (0, 2, 4))
    assert g > r and g > b, (
        f"#{hexes[0]} is not green: red={r} green={g} blue={b}")


def test_the_glow_script_does_not_reach_off_origin():
    """The site makes zero third-party requests and that is pinned.

    ``test_no_page_reaches_an_external_origin`` reads the HTML and cannot see
    inside a script, which is the one place an off-origin fetch would now be
    easiest to add and hardest to notice.
    """
    for name in ("assets/market-glow.js", "assets/live.js"):
        js = _text(name)
        assert "http://" not in js and "https://" not in js, name


def test_the_clock_data_loads_before_the_glow_logic():
    """ORDER IS THE WHOLE CONTRACT BETWEEN THE TWO FILES.

    ``market-clock.js`` is generated data and ``market-glow.js`` is the logic
    that reads ``window.NS_MARKET_CLOCK`` from it. Both are ``defer``, which
    runs them in DOCUMENT ORDER -- so listing the logic first leaves it reading
    an undefined global. It degrades silently by design (a missing clock must
    never break the page), which is exactly why nothing would report the swap:
    the button would simply never light up again.

    EVERY page, not only the home page: each carries the same Live Desk
    button, and until 2026-10-20 only index.html loaded the scripts, so the
    button went dark the moment a visitor clicked anywhere else in the menu.
    """
    for name in PAGES:
        markup = _markup(name)
        srcs = [r for r in _refs(markup) if r.endswith(".js")]
        assert "assets/market-clock.js" in srcs, f"{name} does not load the clock data"
        assert "assets/market-glow.js" in srcs, f"{name} does not load the glow logic"
        assert srcs.index("assets/market-clock.js") < srcs.index("assets/market-glow.js"), (
            f"{name}: market-glow.js is loaded before the data it reads")
        for tag in re.findall(r"<script\b[^>]*>", markup):
            if "market-" in tag:
                assert "defer" in tag, (
                    f"{name}: {tag} is not deferred, so it runs before the nav exists")


def test_every_page_has_the_link_the_glow_selects():
    """The script finds its button with ``nav a[href="live.html"]`` and says
    nothing when there is none. A page whose menu spelled the address any other
    way would load both scripts and still never light."""
    for name in PAGES:
        navs = re.findall(r"<nav\b.*?</nav>", _markup(name), re.S)
        assert any('href="live.html"' in nav for nav in navs), (
            f"{name}: no nav link to live.html for the glow to light")


# --- G. the market report ----------------------------------------------------

def _nav(name):
    m = re.search(r"<nav\b.*?</nav>", _markup(name), re.S)
    assert m, f"{name} has no <nav>"
    return m.group(0)


def test_the_report_page_frames_the_latest_report():
    """report.html is a frame around ONE generated file, and the file name is the
    contract with the publisher that uploads it (tools outside this repo write
    ``reports/latest.html``). Renaming either side leaves a page that loads a
    404 inside a perfectly good nav, which no other test here would notice --
    the reference check skips ``reports/`` because the file is not committed."""
    markup = _markup("report.html")
    frames = re.findall(r"<iframe\b[^>]*>", markup)
    assert len(frames) == 1, f"report.html should frame exactly one document, has {len(frames)}"
    assert 'src="reports/latest.html"' in frames[0]
    assert "title=" in frames[0], "the report frame has no accessible title"
    assert 'href="reports/latest.html"' in markup, (
        "no plain link to the report, so a reader whose frame fails has no way in")


def test_the_report_page_runs_nothing():
    """The frame scrolls on its own. A script here could only be something that
    crept in -- the same rule as the live grid, and the same one exception."""
    assert _scripts_besides_the_glow(_markup("report.html")) == []


def test_the_market_report_is_in_every_destination_nav():
    """The menu item IS the feature: a report page reachable only by typing its
    URL is not published. Scoped to the <nav>: a link buried in a paragraph is
    not navigation, and a whole-document search passes on body prose."""
    for name in ("index.html", "live.html", "glossary.html"):
        assert 'href="report.html"' in _nav(name), f"{name}'s nav has no Market report link"
    for page in ("report.html", "ideas.html"):
        nav = _nav(page)
        for dest in ("glossary.html", "live.html", "index.html"):
            assert f'href="{dest}"' in nav, f"{page}'s nav has no way to {dest}"
    assert 'href="report.html"' in _nav("ideas.html")


def test_the_published_reports_are_never_committed():
    """Uploads land in the served root on prod. Un-ignored, the first one dirties
    the checkout and the next promote refuses to run -- during the session the
    reports are being written for."""
    import subprocess

    probe = "deploy/site/reports/latest.html"
    res = subprocess.run(["git", "check-ignore", "-q", probe],
                         cwd=repo_paths.REPO_ROOT, capture_output=True)
    assert res.returncode == 0, f"{probe} is not gitignored"


def test_the_report_is_in_the_sitemap():
    assert "https://neuralstrike.co/report.html" in _text("sitemap.txt")


# --- F. the Tools menu, retired 2026-10-24 ----------------------------------
def test_no_page_carries_a_tools_menu():
    """The Tools dropdown listed the interactive public screens; the Live Desk
    now shows all of them as cards (test_every_tool_has_a_card_in_table_order),
    so the menu went. A second list would be a second thing to keep in step."""
    for name in PAGES:
        nav = _nav(name)
        assert "ns-menu" not in nav and "<details" not in nav, f"{name} has a Tools menu"
        assert "live.neuralstrike.co" not in nav, f"{name}'s menu links a live screen"
    assert ".ns-menu" not in _css("assets/site.css")


# ── Trade ideas (ideas.html + the home page strip) ──────────────────────────
def test_the_generated_trade_ideas_are_never_committed():
    """services/options_svc/site_ideas.py writes the cards and ideas.json into
    this served tree on prod after every post. Tracked, the first post would
    dirty prod's tree and tools/promote.sh refuses a dirty tree."""
    import subprocess
    root = pathlib.Path(repo_paths.REPO_ROOT)
    for rel in ("deploy/site/ideas/2026-09-29/1435-MU.png",
                "deploy/site/ideas/2026-09-29/1435-MU.webp",
                "deploy/site/ideas.json"):
        out = subprocess.run(["git", "check-ignore", "-q", rel], cwd=root)
        assert out.returncode == 0, f"{rel} is not gitignored"


def test_every_page_carries_the_same_menu():
    """ONE menu, the operator's rule (2026-09-29): every page's <nav> is
    byte-identical once ``aria-current`` is removed. Before it, six pages held
    six menus -- different order, each page leaving itself out, the glossary with
    no links at all, and the right-hand button "Live screens" on one page and
    "Back to overview" on the rest -- because each was hand-edited on its own."""
    def bare(name):
        return re.sub(r'\s+aria-current="page"', "", _nav(name))
    first = bare(PAGES[0])
    for name in PAGES[1:]:
        assert bare(name) == first, f"{name}'s menu differs from {PAGES[0]}'s"


def test_the_menu_marks_the_page_you_are_on_and_nothing_else():
    """The current page is MARKED, not left out -- the item stays where it
    always is, so the menu never shifts under the reader's hand."""
    for name in PAGES:
        marked = re.findall(r'href="([^"]+)"\s+aria-current="page"', _nav(name))
        assert marked == [name], f"{name} marks {marked}"


def test_the_home_strip_starts_hidden_and_the_script_fills_it():
    """The cards are generated state: on a fresh deploy, or before the first
    post, there is nothing to show -- so the strip ships HIDDEN and ideas.js
    unhides it only once it has a card. Never an empty frame on the landing page."""
    markup = _markup("index.html")
    strips = re.findall(r"<section[^>]*data-ideas-strip[^>]*>", markup)
    assert len(strips) == 1, "index.html needs exactly one trade ideas strip"
    assert re.search(r"\shidden(\s|>|=)", strips[0]), "the strip must ship hidden"
    assert '<script src="assets/ideas.js" defer></script>' in markup


def test_the_ideas_page_mounts_the_script():
    markup = _markup("ideas.html")
    assert "data-ideas-page" in markup
    assert '<script src="assets/ideas.js" defer></script>' in markup


def test_ideas_js_reads_the_manifest_safely():
    """Always revalidated (the Caddy rule is installed by hand, and a stale
    manifest shows yesterday's trades as today's), dated in Central time like
    the posts, and drawn with textContent -- the manifest is data, never markup."""
    js = _text("assets/ideas.js")
    assert re.search(r'fetch\(\s*"ideas\.json"\s*,\s*\{\s*cache:\s*"no-cache"', js)
    assert "America/Chicago" in js
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js


def test_the_sitemap_lists_the_ideas_page():
    assert "https://neuralstrike.co/ideas.html" in _text("sitemap.txt").split()


def test_an_open_result_is_labelled_as_an_expiry_payoff_not_a_mark():
    """The site measures an idea from the STOCK price only (no option quote is
    published). An open idea's number is what the trade would pay AT EXPIRY at
    today's price - a long option also carries time value - so the words must
    say so, and no line may present it as a current value."""
    js = _text("assets/ideas.js")
    assert "At the close of today" in js
    # What a visitor can read is the script's string literals, not its comments.
    shown = " ".join(re.findall(r'"([^"\n]*)"', js)).lower()
    for word in ("unrealized", "current value", "now worth", "mark"):
        assert word not in shown, word


def test_the_home_strip_states_the_record_and_it_ships_hidden():
    """One line under the strip's heading: how every idea the site keeps has
    done. Hidden until the script fills it, like the strip itself; the dollar
    figure is the CLOSED ideas only, since an open idea's value is an estimate."""
    markup = _markup("index.html")
    boxes = re.findall(r"<p[^>]*data-ideas-record[^>]*>", markup)
    assert len(boxes) == 1 and re.search(r"\shidden(\s|>|=)", boxes[0])
    js = _text("assets/ideas.js")
    assert "Closed so far: " in js and "trading days" in js


# ── Blog (blog.html + assets/blog.js; the entries themselves are generated) ──
# The page and its script are tracked. Everything they show is not: the manifest
# blog.json and each entry under blog/<slug>/ are written by
# services/blog_svc/sitewriter.py on the box that serves the site, when the
# operator publishes. So these tests pin the two halves this tree owns -- the
# page and the script -- and the CONTRACT with that writer: the menu it lifts
# out of blog.html, the classes it wraps a frame in, where it puts an entry and
# what it accepts as an entry's address.

# The service's own expression for the menu it copies (the plan's NAV_RE). It
# runs over the RAW file, comments included, which is why the first test below
# reads ``_text`` and not ``_markup``.
SERVICE_NAV_RE = re.compile(r'<nav class="ns-nav.*?</nav>', re.S)

# The classes the service writes into an entry page. They are defined HERE, in
# site.css, and written THERE: two files that never import each other, joined
# by strings. ``ns-blog-crumb`` is the only one that exists for the entry page
# alone; the rest are report.html's frame, reused.
SERVICE_CLASSES = ("ns-page-sub", "ns-live-page", "ns-live-main", "ns-report-main",
                   "ns-live-head", "ns-report-head", "ns-eyebrow", "ns-blog-crumb",
                   "ns-report-links", "ns-report-frame", "ns-foot", "ns-live-foot")


def _blog_js():
    return _text("assets/blog.js")


def _blog_js_code():
    """blog.js without its comments, for the checks that are about what RUNS.

    Block comments and whole-line ``//`` comments only: a trailing ``//`` is left
    alone, since telling one from the ``//`` inside a string needs a parser, and
    the script has no need of either."""
    code = CSS_COMMENT_RE.sub("", _blog_js())
    return "\n".join(ln for ln in code.splitlines() if not ln.lstrip().startswith("//"))


def test_the_blog_is_in_every_menu_between_trade_ideas_and_the_glossary():
    """The menu item IS the feature, and its place is decided once: after Trade
    ideas, before the Glossary, on every page."""
    for name in PAGES:
        nav = re.sub(r'\s+aria-current="page"', "", _nav(name))
        assert '<a class="ns-navlink" href="blog.html">Blog</a>' in nav, (
            f"{name}'s menu has no Blog link")
        assert (nav.index('href="ideas.html"') < nav.index('href="blog.html"')
                < nav.index('href="glossary.html"')), f"{name}: Blog is out of place"


def test_the_blog_page_marks_itself_current():
    assert '<a class="ns-navlink" href="blog.html" aria-current="page">Blog</a>' in (
        _nav("blog.html"))


def test_the_blog_page_draws_from_the_manifest_and_says_so_when_empty():
    """The list ships EMPTY and the note ships HIDDEN: on a fresh deploy, or
    before the first entry, the script unhides the note. Never an empty frame,
    and never a sentence saying "no entries" above a list that then fills."""
    markup = _markup("blog.html")
    mains = re.findall(r"<main\b[^>]*>", markup)
    assert len(mains) == 1 and "data-blog-page" in mains[0], mains

    lists = re.findall(r"<div\b[^>]*data-blog-list[^>]*>", markup)
    assert len(lists) == 1 and 'class="ns-blog-list"' in lists[0], lists
    assert re.search(r"<div\b[^>]*data-blog-list[^>]*>\s*</div>", markup), (
        "the list must ship empty: its rows are generated, never committed")

    notes = re.findall(r"<p\b[^>]*data-blog-empty[^>]*>", markup)
    assert len(notes) == 1, notes
    assert re.search(r"\shidden(\s|>|=)", notes[0]), "the empty note must ship hidden"
    assert 'class="ns-live-note"' in notes[0]
    assert "No entries have been published yet." in markup

    assert '<script src="assets/blog.js" defer></script>' in markup
    assert _scripts_besides_the_glow(markup) == [
        '<script src="assets/blog.js" defer>'], "blog.html runs one script, its own"
    assert "<iframe" not in markup, "the list page frames nothing; an entry page does"


def test_the_blog_page_says_what_it_is_without_scripting():
    """The heading and the lede are in the source, as on ideas.html: with
    scripting off the page still says what the Blog is."""
    markup = _markup("blog.html")
    assert re.search(r'<p class="ns-eyebrow">Blog</p>', markup)
    assert re.search(r'<h1 class="ns-live-title">[^<]+</h1>', markup)
    lede = re.search(r'<p class="ns-live-lede">(.*?)</p>', markup, re.S)
    assert lede and len(" ".join(lede.group(1).split())) > 80, "blog.html has no lede"
    assert re.search(r"<title>NeuralStrike \S Blog</title>", markup)
    assert re.search(r'<meta name="description" content="[^"]{40,}">', markup)


def test_the_blog_page_carries_no_timestamp():
    """Same rule as the live grid: this file is served long after anything it
    could say. Each entry carries its own date, from the manifest."""
    markup = _markup("blog.html")
    assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b", markup)
    assert not re.search(r"\b(?:updated|published|as of)\b\s*[:,]?\s*\d", markup, re.I)


def test_the_blog_script_builds_text_never_markup():
    """The manifest is DATA. A title is whatever was typed into the private
    Blog page, so it reaches the document through textContent and nothing that
    parses a string as HTML -- the same rule ideas.js keeps.

    Read from the RAW file, comments included, so the script cannot even quote
    the calls it must not make."""
    js = _blog_js()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                   "DOMParser", "createContextualFragment", "srcdoc", "eval("):
        assert banned not in js, f"blog.js names {banned}"
    assert not re.search(r"\bnew\s+Function\b", js)
    assert "createElement" in js and "textContent" in js
    assert re.search(r'fetch\(\s*"blog\.json"\s*,\s*\{\s*cache:\s*"no-cache"', js), (
        "the manifest must always be revalidated: a stale one hides a new entry "
        "and keeps showing one that was unpublished")
    assert 'cache: "no-cache"' in js


def test_the_blog_script_reaches_nothing_off_origin():
    """One request, to the manifest beside the page, and nothing kept or
    scheduled: no other fetch, no storage, no timer. The HTML origin guard
    cannot see inside a script, so this is where an off-origin call would hide."""
    js = _blog_js()
    assert "http://" not in js and "https://" not in js
    code = _blog_js_code()
    # With the block comments and the whole-line comments gone, a "//" left over
    # is a protocol-relative address in a string, or a trailing comment that
    # could hide one from this check. The script needs neither.
    assert "//" not in code, "a protocol-relative address, or a trailing // comment"
    assert len(re.findall(r"\bfetch\(", code)) == 1, "blog.js makes exactly one request"
    for banned in ("XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon",
                   "localStorage", "sessionStorage", "indexedDB", "document.cookie",
                   "setInterval", "setTimeout", "requestAnimationFrame",
                   "importScripts", "import(", "window.open", "location.href",
                   "location.assign", "location.replace"):
        assert banned not in code, f"blog.js uses {banned}"


def test_the_blog_script_and_the_service_agree_on_what_a_slug_is():
    """A slug is a FOLDER NAME under the served root, and it exists twice: the
    service refuses anything else when it writes (``shared.blog_inbox.SLUG_RE``)
    and the script refuses anything else before it builds a link.

    How the two are compared, exactly: the script's literal must be
    ``var SLUG = /<source>/;`` with NO flags, and its source must end in ``$``.
    That ``$`` is swapped for ``\\Z`` and the result must equal the Python
    pattern string, character for character. The swap is sound because without
    the ``m`` flag a JavaScript ``$`` matches only at the very end of the input
    -- which is what Python spells ``\\Z``, and NOT what Python's own ``$`` means
    (that one also matches before a final newline; see the note in
    ``blog_inbox``)."""
    from shared import blog_inbox

    found = re.findall(r"var SLUG = /(.+)/([a-z]*);", _blog_js_code())
    assert len(found) == 1, f"expected one SLUG literal in blog.js, found {found}"
    source, flags = found[0]
    assert flags == "", f"a flag changes what the pattern accepts: /{flags}"
    assert source.endswith("$") and not source.endswith("\\$")
    assert source[:-1] + "\\Z" == blog_inbox.SLUG_RE.pattern
    # Non-vacuity: the pattern compared is the one the script actually applies.
    assert re.search(r"\bSLUG\.test\(", _blog_js_code())


def test_the_blog_script_links_an_entry_where_the_service_writes_it():
    """``blog/<slug>/`` -- the folder whose index.html is the entry's page.
    Relative, with no leading slash, like every other address on this site; the
    slug in it has passed ``SLUG`` (the test above), so the address cannot leave
    the blog folder."""
    code = _blog_js_code()
    hrefs = re.findall(r"\.href\s*=\s*([^;]+);", code)
    assert len(hrefs) == 1, f"blog.js sets {len(hrefs)} addresses, expected the card's"
    assert re.fullmatch(r'"blog/" \+ [\w.]+ \+ "/"', hrefs[0]), hrefs[0]
    assert "target" not in code, "an entry opens in the same tab: it is this site"


def test_the_blog_script_caps_the_tags_it_draws():
    assert re.search(r"var MAX_TAGS = 6;", _blog_js_code())
    assert re.search(r"\.slice\(0,\s*MAX_TAGS\)", _blog_js_code())


def test_the_blog_script_dates_an_entry_in_central_time_or_not_at_all():
    """The clock the site keeps (ideas.js names the same zone), and a date that
    does not parse draws NO date: never the words a failed parse prints."""
    js = _blog_js()
    assert "America/Chicago" in js and '"en-US"' in js
    assert "isNaN(" in js
    assert "Invalid Date" not in js


def test_every_class_the_blog_draws_with_is_defined_and_none_is_orphaned():
    """A class written by a script, or by a service in another folder, that the
    stylesheet does not define renders as unstyled text and reports nothing.

    Both directions. ``ns-blog-*`` in site.css that nothing writes is dead CSS
    that reads as a feature."""
    css = _css("assets/site.css")
    defined = set(re.findall(r"\.(ns-blog-[a-z-]+)", css))

    written = set(re.findall(r'"(ns-blog-[a-z-]+)"', _blog_js_code()))
    written |= set(re.findall(r'class="[^"]*?(ns-blog-[a-z-]+)', _markup("blog.html")))
    assert written, "nothing parsed; this test would then assert nothing"
    by_service = {c for c in SERVICE_CLASSES if c.startswith("ns-blog-")}

    assert written | by_service <= defined, (
        f"written but not styled: {sorted((written | by_service) - defined)}")
    assert defined <= written | by_service, (
        f"styled but written by nothing: {sorted(defined - written - by_service)}")


def test_the_stylesheet_defines_every_class_the_service_wraps_an_entry_in():
    """The entry page is generated outside this tree and styled from inside it.
    ``ns-blog-crumb`` is the compact title (the framed document carries its own
    large headline) and ``ns-report-frame`` is the frame."""
    css = _css("assets/site.css") + _css("assets/nocturne.css")
    for cls in SERVICE_CLASSES:
        assert re.search(rf"\.{re.escape(cls)}(?![\w-])", css), (
            f"the service writes .{cls}, which no stylesheet defines")
    crumb = re.search(r"\.ns-blog-crumb\s*\{([^}]*)\}", _css("assets/site.css"))
    assert crumb, "no .ns-blog-crumb rule"
    for prop in ("white-space: nowrap", "overflow: hidden", "text-overflow: ellipsis"):
        assert prop in crumb.group(1), f"the compact title is not held to one line: {prop}"


# The narrowest viewport, in px, at which the menu below fits on ONE row,
# measured in a browser (a vertical scrollbar present) for exactly the words in
# MENU_WORDS. Not derivable here: it is the rendered width of text.
# 905 with Gallery and the Tools menu; 775 once both went (measured
# 2026-10-24: 3px over at 770, clear at 780). The wrap stays at 960, so the
# menu now wraps earlier than it must -- loosening that is a choice, not a fix.
MENU_FITS_FROM = 775
MENU_WORDS = ["Market report", "Trade ideas", "Blog", "Glossary", "Community"]


def test_the_menu_wraps_before_it_stops_fitting():
    """A NEW WORD IN THE MENU MOVES A BREAKPOINT NOBODY THINKS TO MOVE.

    The menu wrapped at 700px and nowhere above it. Adding the Blog link was
    checked at a desktop width and at a phone width, where it looked right; at
    800 the Live screens button was off the right edge. Measured across widths,
    it had been off the edge from 701 to 852 since BEFORE this link (the menu
    never fitted a 768px tablet), and the new link stretched that to 905.

    A stylesheet cannot be rendered here, so this pins the two facts that were
    measured and makes them move together: the words the measurement was taken
    for, and the width the wrap must cover. Change the menu and the first
    assertion fails -- which is the moment to measure again, not to edit the
    list."""
    words = re.findall(r'<a class="ns-navlink"[^>]*>([^<]+)</a>', _nav("index.html"))
    assert words == MENU_WORDS, (
        "the menu changed: measure the width one row needs again, then move "
        "MENU_FITS_FROM and the wrap breakpoint in site.css with it")

    blocks = re.findall(r"@media\s*\(max-width:\s*(\d+)px\)\s*\{(.*?)\n\}",
                        _css("assets/site.css"), re.S)
    wrapping = [int(w) for w, body in blocks
                if re.search(r"\.ns-nav\s*\{[^}]*flex-wrap:\s*wrap", body)]
    assert wrapping, "no media query wraps the menu"
    assert max(wrapping) >= MENU_FITS_FROM, (
        f"the menu wraps only below {max(wrapping)}px but needs {MENU_FITS_FROM}px "
        f"to fit on one row; between the two it runs off the right edge")
    # The break that makes it two clean rows travels with the wrap.
    for width, body in blocks:
        if int(width) == max(wrapping):
            assert re.search(r"\.ns-nav \.ns-spacer\s*\{[^}]*flex:\s*0 0 100%", body), (
                "the menu wraps without its break, so only the last item drops")


def test_an_entry_with_no_background_of_its_own_is_readable_in_its_frame():
    """SEEN IN A BROWSER, and invisible to everything else here. A document
    that sets no background is transparent inside a frame, so the FRAME's
    ground shows through it -- and ``.ns-report-frame`` is near-black, for the
    market report, which paints its own. An entry written as plain black text
    rendered as black on black.

    The entry frame is told from the report's by its ``sandbox`` attribute
    (the service always writes one; report.html's has none) and takes the
    browser's own page colour, ``Canvas``: what that document would have had
    behind it on a tab of its own.

    The second half keeps the fix from restyling the report: its frame carries
    no ``sandbox``, so the rule cannot reach it."""
    rule = re.search(r"\.ns-report-frame\[sandbox\]\s*\{([^}]*)\}", _css("assets/site.css"))
    assert rule, "no ground for a sandboxed (Blog entry) frame"
    assert re.search(r"background:\s*Canvas\s*;", rule.group(1)), rule.group(1)

    report_frames = re.findall(r"<iframe\b[^>]*>", _markup("report.html"))
    assert len(report_frames) == 1 and "sandbox" not in report_frames[0], report_frames


def test_the_service_can_lift_the_menu_out_of_the_blog_page():
    """THE ENTRY PAGES' MENU IS THIS FILE'S MENU. The service copies the block
    its expression finds in the RAW blog.html, byte for byte, into every entry
    page. If a comment in this file ever quoted the opening tag, that expression
    would start its match inside the comment and every entry would be published
    under a menu that begins with prose."""
    found = SERVICE_NAV_RE.findall(_text("blog.html"))
    assert len(found) == 1, f"the service's expression finds {len(found)} menus"
    assert found[0] == _nav("blog.html")
    assert "<!--" not in found[0]


def test_the_menu_survives_being_served_from_an_entry_page():
    """An entry page lives at ``blog/<slug>/`` and sets ``<base href="/">``, so
    every address in the copied menu is resolved from the site root. A plain
    relative path (what the menu uses) lands where it does today. A bare
    ``#fragment`` would point at the HOME page's fragment, and ``./`` or ``../``
    say outright that the author was thinking of one folder."""
    refs = _refs(_nav("blog.html"))
    assert refs, "no addresses parsed from the menu"
    for ref in refs:
        assert not ref.startswith(("#", "./", "../")), (
            f"the menu's {ref!r} would not survive <base href=\"/\">")


def test_the_blog_is_in_the_sitemap_and_robots_names_the_entry_sitemap():
    """The page is tracked, so it is in the tracked sitemap. The entries are
    not, so they are in a sitemap of their own that the service rewrites, and
    robots.txt is the one tracked file that can point a crawler at it."""
    assert "https://neuralstrike.co/blog.html" in _text("sitemap.txt").split()
    directives = [ln.strip() for ln in _text("robots.txt").splitlines()
                  if ln.strip() and not ln.lstrip().startswith("#")]
    assert "Sitemap: https://neuralstrike.co/sitemap.txt" in directives
    assert "Sitemap: https://neuralstrike.co/blog/sitemap.txt" in directives
    # It said the entry sitemap is absent until the first entry. The service
    # writes an empty one at its first start (the site writer's own suite pins
    # the empty file), so a crawler following this line is not sent to a 404.
    assert "absent until the first entry" not in _text("robots.txt")


def test_the_generated_blog_is_never_committed():
    """Written on the serving box when an entry is published. Tracked, the first
    entry would dirty prod's tree and tools/promote.sh refuses a dirty tree.

    The second half is the trap the ignore rule's own comment names: the page
    and its script sit one character away from the ignored paths and must stay
    TRACKED, or a promote ships a menu link to a page that is not there."""
    import subprocess
    root = pathlib.Path(repo_paths.REPO_ROOT)

    def ignored(rel):
        return subprocess.run(["git", "check-ignore", "-q", rel], cwd=root).returncode == 0

    for rel in ("deploy/site/blog.json",
                "deploy/site/blog/nuclear-stocks-thesis/index.html",
                "deploy/site/blog/nuclear-stocks-thesis/entry.html",
                "deploy/site/blog/fonts/0123456789abcdef0123.woff2",
                "deploy/site/blog/sitemap.txt"):
        assert ignored(rel), f"{rel} is not gitignored"
    for rel in ("deploy/site/blog.html", "deploy/site/assets/blog.js"):
        assert not ignored(rel), f"{rel} is ignored, so it would never reach prod"
