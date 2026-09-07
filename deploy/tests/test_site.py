"""The public site at neuralstrike.co, checked as a tree of files.

``deploy/caddy/tests/test_caddyfile.py`` asserts the EDGE serves this directory
and nothing above it. These tests assert what is IN it -- which the Caddy suite
cannot see, and which a browser only reveals to whoever looks at the live page.

Three failure modes motivate the file, all of them silent:

* **a renamed or missing screenshot.** The gallery references 24 images by
  path. Nothing breaks at deploy time; the page simply shows a broken image to
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
PAGES = ("index.html", "gallery.html", "live.html", "glossary.html")

# The site calls nobody. Empty on purpose, and widening it is a decision:
# every entry is a third party learning the IP of everyone who loads the page.
ALLOWED_ORIGINS: tuple[str, ...] = ()

# Where a visitor may be CHOOSING to go. All five live in the community block on
# the landing page and are the only outbound links on the site.
#
# ⚠ This is not the same permission as ALLOWED_ORIGINS above. A link is followed
# only when someone clicks it; an origin in that list is fetched on their behalf
# the moment the page loads. Adding to this list costs a visitor nothing until
# they act, which is why it may hold five entries while the other holds none.
ALLOWED_OUTBOUND = (
    "https://discord.gg/",
    "https://t.me/",
    "https://x.com/",
    "https://www.facebook.com/",
    "https://www.instagram.com/",
)

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
    """
    for name, text in pages.items():
        for ref in _refs(text):
            if ref.startswith(("#", "http://", "https://", "mailto:", "data:")):
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


# --- C. the gallery and its images agree, in BOTH directions ----------------

def test_the_gallery_references_every_shot_on_disk():
    """A shot on disk that no page references is dead weight -- published to
    the internet, downloaded by nobody, and invisible in review. This is the
    direction a link-checker never covers."""
    on_disk = {p.name for p in (SITE / "assets" / "shots").iterdir() if p.is_file()}
    referenced = {r.rsplit("/", 1)[-1] for r in _refs(_markup("gallery.html"))
                  if "assets/shots/" in r}
    assert on_disk == referenced, (
        f"orphaned on disk: {sorted(on_disk - referenced)}; "
        f"referenced but absent: {sorted(referenced - on_disk)}")


def test_the_rail_and_the_panels_are_the_same_length():
    """gallery.js pairs them by index and bails out if they disagree, so a
    mismatch does not throw -- the gallery just silently stops switching."""
    text = _markup("gallery.html")
    rails = re.findall(r'id="(rail-\d+)"', text)
    panels = re.findall(r'id="(screen-\d+)"', text)
    assert len(rails) == len(panels) == 16, f"{len(rails)} rail rows, {len(panels)} panels"
    for rail, panel in zip(rails, panels):
        assert rail.split("-")[1] == panel.split("-")[1]


def test_every_rail_row_controls_a_panel_that_exists():
    text = _markup("gallery.html")
    ids = set(re.findall(r'\bid="([^"]+)"', text))
    for controls in re.findall(r'aria-controls="([^"]+)"', text):
        assert controls in ids, f"a rail row controls {controls}, which does not exist"


def test_exactly_one_panel_and_one_shot_start_active():
    """A second `is-active` panel would show two screens at once."""
    text = _markup("gallery.html")
    panels = re.findall(r'<section class="ns-screen[^"]*"[^>]*>', text)
    assert len(panels) == 16
    active = [p for p in panels if "is-active" in p]
    assert len(active) == 1, f"{len(active)} panels start active, expected 1"

    # Each multi-section screen opens on its own first shot.
    for block in re.split(r'(?=<section class="ns-screen)', text)[1:]:
        shots = re.findall(r'<figure class="ns-shot[^"]*"', block)
        act = [s for s in shots if "is-active" in s]
        assert len(act) == 1, f"a screen has {len(act)} active shots, expected 1"


def test_no_panel_or_shot_uses_the_hidden_ATTRIBUTE():
    """THE NO-JS FALLBACK LIVES OR DIES HERE, and it died once already.

    `hidden` is native HTML: the browser's OWN stylesheet carries
    ``[hidden] { display: none }``. An earlier build marked the inactive panels
    with it and wrote ``.js .ns-screen[hidden] { display: none }``, believing the
    hiding was opt-in behind the `.js` hook. It was not -- that selector was pure
    decoration and the UA rule did the work, so a visitor with scripting off saw
    ONE screen and no way to reach the other fifteen. The whole reason these
    pages were rebuilt instead of copied from the design's bundle was that the
    bundle needed JavaScript to show anything.

    Nothing in the suite caught it: the content really was all in the source, so
    every other check here passed. It took loading the page with the scripts
    stripped. This test is the cheap standing version of that.

    WARNING: this test's FIRST draft could not fail either. A shell-escaping
    slip put a literal backspace byte where each ``\b`` belonged, so the
    pattern read ``<BS>hidden<BS>`` and matched nothing -- a guard against a
    silent bug that was itself silently broken. It was caught only by putting
    the bug back and watching the suite stay green. Do that after editing this.
    """
    markup = _markup("gallery.html")
    for tag in re.findall(r"<(?:section|figure)\b[^>]*>", markup):
        if "ns-screen" in tag or "ns-shot" in tag:
            assert not re.search(r"\bhidden\b", tag), (
                f"a gallery element uses the hidden ATTRIBUTE, which hides it "
                f"from scripting-off visitors too -- use `is-active`: {tag}")


def test_the_visibility_rules_are_scoped_to_the_js_hook():
    """The other half of the same invariant. If a rule that hides a panel is not
    under `.js`, it applies to everyone -- including the fallback."""
    css = _css("assets/site.css")
    hiding = re.findall(r"([^{}]*\.ns-(?:screen|shot)[^{}]*)\{([^}]*)\}", css)
    for selector, body in hiding:
        if "display: none" in body:
            assert ".js" in selector, (
                f"`{selector.strip()}` hides a gallery element for everyone, "
                f"including visitors with no JavaScript")


def test_the_chip_row_breakpoint_is_the_same_number_in_the_css_and_the_js():
    """The rail is a sidebar above 1000px and a horizontal chip strip below it.
    CSS decides the layout; ``gallery.js`` reads the same width through
    ``matchMedia`` to set ``aria-orientation``, because a strip announced as
    vertical is a lie told only to the people who cannot see it.

    Two copies of one number. If they drift, nothing breaks visibly and nothing
    else in this suite notices -- the layout is simply described wrongly to
    assistive technology across a band of widths.
    """
    css_widths = set(re.findall(r"@media\s*\(max-width:\s*(\d+)px\)", _css("assets/site.css")))
    js_widths = set(re.findall(r"matchMedia\(\s*[\"']\(max-width:\s*(\d+)px\)",
                               _text("assets/gallery.js")))
    assert js_widths, "gallery.js no longer reads a breakpoint; drop this test with it"
    assert js_widths <= css_widths, (
        f"gallery.js watches {sorted(js_widths)} but site.css defines "
        f"{sorted(css_widths)} -- the orientation announced to screen readers "
        f"would disagree with the layout")


def test_the_stack_and_the_chip_row_switch_together():
    """The chip styles and the column stack MUST live in one media query.

    Left to itself the gallery wraps when the rail and viewer stop fitting,
    which falls out of two clamp() values at about 967px. A chip row keyed to a
    different number would leave a band of widths rendering chips inside a 340px
    sidebar, or a vertical list stretched across the full width. Declaring the
    stack in the same block makes them the same number by construction.
    """
    css = _css("assets/site.css")
    blocks = re.findall(r"@media\s*\(max-width:\s*(\d+)px\)\s*\{(.*?)\n\}", css, re.S)
    owning = [(w, b) for w, b in blocks if "flex-direction: row" in b and ".ns-rail-list" in b]
    assert owning, "no media query turns the rail list into a row"
    for width, body in owning:
        assert "flex-direction: column" in body and ".ns-gallery" in body, (
            f"the {width}px block makes the rail horizontal without also forcing "
            f".ns-gallery to stack, so the two can disagree")


def test_the_stacked_rail_resets_its_flex_basis():
    """`flex-basis` sizes the MAIN axis, so a column container reads
    ``flex: 1 1 260px`` as a HEIGHT. Measured before the reset existed: a 260px
    rail around 77px of chips and a 620px viewer around 310px of content, ~490px
    of dead space that reads as a spacing bug and is a flex-axis one."""
    css = _css("assets/site.css")
    blocks = re.findall(r"@media\s*\(max-width:\s*\d+px\)\s*\{(.*?)\n\}", css, re.S)
    stacking = [b for b in blocks if ".ns-gallery { flex-direction: column; }" in b]
    assert stacking, "nothing forces the gallery to stack"
    for body in stacking:
        assert re.search(r"\.ns-rail,\s*\.ns-viewer\s*\{[^}]*flex:\s*0 0 auto", body), (
            "the stacked gallery does not reset the rail/viewer flex basis, so "
            "their 260px/620px bases become heights")


# --- C2. the mark, which exists in seven copies -----------------------------

# The two optical sizes. LARGE is the site's nav and hero; SMALL is the favicon
# and the app's 28px header slot, drawn heavier so a 1-device-pixel rule
# survives at 16px. They are DIFFERENT DRAWINGS on purpose -- scaling one to
# both sizes is the mistake optical sizing exists to prevent.
MARK_LARGE = ("M20 11 L32 23 L44 11", "M20 53 L32 41 L44 53")
MARK_SMALL = ("M22 12 L32 23.5 L42 12", "M22 52 L32 41 L42 52")

MARK_LARGE_FILES = ("index.html", "gallery.html", "live.html", "assets/mark.svg")
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
        f"re-run the generator. e.g. {sorted(missing)[:5]}")


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


def test_the_gallery_has_all_sixteen_screen_titles_in_its_source():
    """With scripting off the panels stack; the text must therefore be readable
    without running anything."""
    text = _markup("gallery.html")
    assert len(re.findall(r"<h2>", text)) == 16
    for title in ("The Desk", "Gamma Heatmap", "Strategy Calculator", "Daily Briefings"):
        assert f"<h2>{title}</h2>" in text, f"{title} is not in the gallery source"


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
