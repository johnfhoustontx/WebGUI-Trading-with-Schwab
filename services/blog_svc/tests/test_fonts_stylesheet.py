"""What a Google Fonts stylesheet is read into.

Which blocks are kept (the label, the character sets), what each descriptor
may say, what a rule is written as, and what a stylesheet can do to get
something of its own into an entry: nothing. The kit - fakes, sample
stylesheets, the audit - is ``_fonts_kit.py``; read its docstring first.
"""
import random
import re
from urllib.parse import urlsplit

import pytest
from tinycss2.serializer import serialize_string_value

from services import _degrade
from services.blog_svc import clean, fonts
from services.blog_svc.tests._fonts_kit import (A, B, C, CJK_MIDDLE, E_ACUTE, G, LINE_SEPARATOR,
                                                LINK, LINK_2, NONE, PARAGRAPH_SEPARATOR,
                                                RIGHT_TO_LEFT_OVERRIDE, SOME, Web,
                                                ZERO_WIDTH_SPACE, audit, face, families, name_of,
                                                rule, sheet, woff2)


def test_a_stylesheet_shaped_like_googles_becomes_local_rules():
    """The whole path on the ordinary case: one link, three subsets offered,
    two copied; a static weight, an italic, and the variable form."""
    css = sheet(
        face("cyrillic", G + "cyr.woff2", unicode_range="U+0301, U+0400-045F"),
        face("latin-ext", G + "ext.woff2", weight="100 900", stretch="100%",
             unicode_range="U+0100-02BA, U+1E00-1E9F, U+A720-A7FF"),
        face("latin", G + "lat.woff2", weight="100 900", stretch="100%"),
        face("latin", G + "ital-KFOmCnqEu92Fr1Mu_4.woff2", style="italic", weight="700"),
    )
    web = Web({LINK: css, G + "ext.woff2": A, G + "lat.woff2": B,
               G + "ital-KFOmCnqEu92Fr1Mu_4.woff2": C})
    result = audit(fonts.localize((LINK,), fetch=web))
    assert result.css == "\n".join([
        rule(A, weight="100 900", stretch="100%",
             unicode_range="U+0100-02BA,U+1E00-1E9F,U+A720-A7FF"),
        rule(B, weight="100 900", stretch="100%"),
        rule(C, style="italic", weight="700"),
    ])
    assert result.files == {name_of(A): A, name_of(B): B, name_of(C): C}
    assert result.note == ""
    assert web.urls == [LINK, G + "ext.woff2", G + "lat.woff2",
                        G + "ital-KFOmCnqEu92Fr1Mu_4.woff2"]


def test_only_the_configured_subsets_are_kept():
    blocks = {subset: woff2(subset) for subset in
              ("cyrillic-ext", "cyrillic", "greek", "vietnamese", "latin-ext", "latin")}
    css = sheet(*[face(subset, G + subset + ".woff2") for subset in blocks])
    pages = {LINK: css, **{G + subset + ".woff2": data for subset, data in blocks.items()}}

    web = Web(pages)
    shipped = audit(fonts.localize([LINK], fetch=web))
    assert set(shipped.files) == {name_of(blocks["latin-ext"]), name_of(blocks["latin"])}
    assert web.urls == [LINK, G + "latin-ext.woff2", G + "latin.woff2"]
    assert shipped.note == ""                               # the others are not a failure

    web = Web(pages)
    greek = audit(fonts.localize([LINK], fetch=web, cfg={"subsets": ["greek"]}))
    assert set(greek.files) == {name_of(blocks["greek"])}
    assert web.urls == [LINK, G + "greek.woff2"]


@pytest.mark.parametrize("label", [
    None,                               # no comment at all
    "/* [0] */",                        # how Google labels the slices of a CJK face
    "/* LATIN */", "/* latin, latin-ext */", "/* ../latin */", "/**/", "/* */",
    "/* latin */ /* something else */",                     # the LAST comment is the label
    "/* latin */ .x{color:red}",                            # a rule in between: no longer its label
    "/* latin */ @media print {}",
    "/* latin */ ;",
], ids=repr)
def test_a_block_with_no_recognisable_subset_label_is_dropped(label):
    block = face("latin", G + "x.woff2").split("\n", 1)[1]              # the block, unlabelled
    css = sheet(face("latin", G + "a.woff2"), (label or "") + "\n" + block,
                face("latin", G + "b.woff2", weight="700"))
    web = Web({LINK: css, G + "a.woff2": A, G + "b.woff2": B, G + "x.woff2": C})
    result = audit(fonts.localize([LINK], fetch=web))
    assert G + "x.woff2" not in web.urls
    assert result.css == rule(A) + "\n" + rule(B, weight="700")


def test_a_label_is_used_once():
    """One comment labels the ONE block that follows it. A second block riding
    on the first one's label would be a way round the subset list."""
    unlabelled = face("latin", G + "x.woff2").split("\n", 1)[1]
    web = Web({LINK: sheet(face("latin", G + "a.woff2"), unlabelled),
               G + "a.woff2": A, G + "x.woff2": C})
    assert audit(fonts.localize([LINK], fetch=web)).css == rule(A)
    assert web.urls == [LINK, G + "a.woff2"]


def test_a_stylesheet_with_nothing_in_the_copied_character_sets_says_so():
    web = Web({LINK: sheet(face("cyrillic", G + "c.woff2"), face("greek", G + "g.woff2"))})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == "" and result.files == {} and web.urls == [LINK]
    assert result.note == ("No typefaces were copied, so the entry is shown in its fallback "
                           "fonts: 1 typeface was not copied because it is not offered in the "
                           "character sets copied here.")


def test_only_a_stylesheet_with_nothing_at_all_in_it_is_said_to_hold_no_typefaces():
    """A stylesheet whose one block was offered here and was not usable has
    ONE thing wrong with it, and it is not that it held nothing."""
    unusable = sheet(face("latin", "https://evil.test/a.woff2"))
    result = audit(fonts.localize([LINK], fetch=Web({LINK: unusable})))
    assert result.note == NONE + "1 typeface rule was not usable."
    # ... and the same when the block names no family this module can read.
    nameless = sheet(face("latin", G + "a.woff2").replace("  font-family:", "  x-family:"))
    result = audit(fonts.localize([LINK], fetch=Web({LINK: nameless})))
    assert result.note == NONE + "1 typeface rule was not usable."
    nothing = audit(fonts.localize([LINK], fetch=Web({LINK: b"body{color:red}"})))
    assert nothing.note == NONE + "1 stylesheet held no typefaces."


def test_a_block_with_a_second_url_is_dropped():
    two = f"url({G}x.woff2) format('woff2'), url({G}y.woff2) format('woff2')"
    css = sheet(
        face("latin", G + "a.woff2"),
        face("latin", None, src=two, family="'Two'"),
        # ... wherever the second one is: in a descriptor this module does not
        # copy, spelled with an escape, quoted, or broken.
        face("latin", G + "x.woff2", family="'Elsewhere'",
             extra=f"size-adjust: url({G}y.woff2);"),
        face("latin", G + "x.woff2", family="'Escaped'", extra=f"x: u\\72l({G}y.woff2);"),
        face("latin", G + "x.woff2", family="'Quoted'", extra=f"x: url('{G}y.woff2');"),
        face("latin", G + "x.woff2", family="'Nested'", extra=f"x: f([{{url({G}y.woff2)}}]);"),
        face("latin", G + "x.woff2", family="'Broken'", extra="x: url(a b);"),
        face("latin", G + "x.woff2", family="'Twice'", extra=f"src: url({G}y.woff2) "
                                                             "format('woff2');"),
    )
    web = Web({LINK: css, G + "a.woff2": A, G + "x.woff2": B, G + "y.woff2": C})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == rule(A) and web.urls == [LINK, G + "a.woff2"]
    assert "7 typeface rules were not usable" in result.note


def test_a_local_looking_address_in_the_stylesheet_is_not_passed_through():
    """The one ``url()`` the result may hold is ``../fonts/<name>``. A
    stylesheet that already says exactly that names a file this module never
    fetched and never checked."""
    planted = "../fonts/" + "ab" * 10 + ".woff2"
    web = Web({LINK: sheet(face("latin", planted))})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == "" and result.files == {} and web.urls == [LINK]


# One line each: something in the place of a good block, or beside it. The
# good block before it must always survive; the one after it survives unless
# the junk is a rule that swallows it (a stray "<" opens a qualified rule,
# which runs to the next "{}").
HOSTILE_SHEETS = [
    ("script", "</style><script>alert(1)</script>", False),
    ("script in a comment", "/* </style><script>alert(1)</script> */", True),
    ("html comment", "<!-- --> ", True),
    ("charset", '@charset "utf-8";', True),
    ("import", "@import url(https://evil.test/x.css);", True),
    ("import of a real stylesheet",
     '@import "https://fonts.googleapis.com/css2?family=Evil";', True),
    ("namespace", "@namespace url(https://evil.test/ns);", True),
    ("another rule", "body{background:url(https://evil.test/t.gif)}", True),
    ("nested in a condition",
     "@media screen { " + face("latin", G + "x.woff2", family="'N'") + "}", True),
    ("nested in supports",
     "@supports (display:grid) { " + face("latin", G + "x.woff2", family="'N'") + "}", True),
    ("an at-rule inside the block",
     face("latin", G + "x.woff2", family="'N'", extra="@import url(https://evil.test/x);"), True),
    ("a rule inside the block",
     face("latin", G + "x.woff2", family="'N'", extra="a{color:red}"), True),
    ("a prelude", face("latin", G + "x.woff2").replace("@font-face {", "@font-face x {"), True),
    ("no block", "/* latin */ @font-face;", True),
    ("another host", face("latin", "https://evil.test/a.woff2"), True),
    ("plain http", face("latin", "http://fonts.gstatic.com/s/a.woff2"), True),
    ("a look-alike host", face("latin", "https://fonts.gstatic.com.evil.test/s/a.woff2"), True),
    ("a host behind userinfo", face("latin", "https://fonts.gstatic.com@evil.test/s/a.woff2"),
     True),
    ("local first", face("latin", None, src=f"local('Inter'), url({G}x.woff2) format('woff2')"),
     True),
    ("no format", face("latin", None, src=f"url({G}x.woff2)"), True),
    ("another format", face("latin", None, src=f"url({G}x.woff2) format('truetype')"), True),
    ("two formats", face("latin", None, src=f"url({G}x.woff2) format('woff2', 'woff')"), True),
    ("a format and more", face("latin", None, src=f"url({G}x.woff2) format('woff2') tech(x)"),
     True),
    ("important", face("latin", None, src=f"url({G}x.woff2) format('woff2') !important"), True),
    ("no src", face("latin", G + "x.woff2").replace("  src:", "  x-src:"), True),
    ("no family", face("latin", G + "x.woff2").replace("  font-family:", "  x-family:"), True),
    ("two families", face("latin", G + "x.woff2", family="'A', 'B'"), True),
    ("two family declarations", face("latin", G + "x.woff2", extra="font-family: 'Z';"), True),
    ("a number for a family", face("latin", G + "x.woff2", family="12"), True),
    ("a function for a family", face("latin", G + "x.woff2", family="var(--f)"), True),
    ("an empty family", face("latin", G + "x.woff2", family="''"), True),
    ("a blank family", face("latin", G + "x.woff2", family="'   '"), True),
    ("a very long family", face("latin", G + "x.woff2", family="'" + "A" * 500 + "'"), True),
    ("a family with a line break", face("latin", G + "x.woff2", family="'a\\A b'"), True),
    ("a family with a control", face("latin", G + "x.woff2", family="'a\\1 b'"), True),
    ("a family with a zero-width space",
     face("latin", G + "x.woff2", family="'a" + ZERO_WIDTH_SPACE + "b'"), True),
    ("a family that reorders the line",
     face("latin", G + "x.woff2", family="'a" + RIGHT_TO_LEFT_OVERRIDE + "b'"), True),
    ("weight: exponent", face("latin", G + "x.woff2", weight="1e3"), True),
    ("weight: negative", face("latin", G + "x.woff2", weight="-400"), True),
    ("weight: zero", face("latin", G + "x.woff2", weight="0"), True),
    ("weight: too heavy", face("latin", G + "x.woff2", weight="1001"), True),
    ("weight: three", face("latin", G + "x.woff2", weight="100 400 900"), True),
    ("weight: a function", face("latin", G + "x.woff2", weight="calc(400)"), True),
    ("weight: a variable", face("latin", G + "x.woff2", weight="var(--w)"), True),
    ("weight: a unit", face("latin", G + "x.woff2", weight="400px"), True),
    ("style: unknown", face("latin", G + "x.woff2", style="slanted"), True),
    ("style: italic with an angle", face("latin", G + "x.woff2", style="italic 5deg"), True),
    ("style: a right angle and more", face("latin", G + "x.woff2", style="oblique 100deg"), True),
    ("style: another unit", face("latin", G + "x.woff2", style="oblique 1rad"), True),
    ("style: an expression", face("latin", G + "x.woff2", style="expression(alert(1))"), True),
    ("stretch: a bare number", face("latin", G + "x.woff2", stretch="100"), True),
    ("stretch: negative", face("latin", G + "x.woff2", stretch="-5%"), True),
    ("stretch: three", face("latin", G + "x.woff2", stretch="50% 100% 150%"), True),
    ("stretch: unknown", face("latin", G + "x.woff2", stretch="squashed"), True),
    ("display: unknown", face("latin", G + "x.woff2", display="always"), True),
    ("display: two", face("latin", G + "x.woff2", display="swap block"), True),
    ("range: backwards", face("latin", G + "x.woff2", unicode_range="U+5-3"), True),
    ("range: past the end", face("latin", G + "x.woff2", unicode_range="U+110000"), True),
    ("range: too many digits", face("latin", G + "x.woff2", unicode_range="U+FFFFFFF"), True),
    ("range: a word", face("latin", G + "x.woff2", unicode_range="U+0-7F, javascript"), True),
    ("range: an expression",
     face("latin", G + "x.woff2", unicode_range="U+0-7F, expression(alert(1))"), True),
    ("range: no comma", face("latin", G + "x.woff2", unicode_range="U+0-7F U+80"), True),
    ("range: a trailing comma", face("latin", G + "x.woff2", unicode_range="U+0-7F,"), True),
    ("range: a doubled comma", face("latin", G + "x.woff2", unicode_range="U+0-7F,,U+80"), True),
    ("range: empty", face("latin", G + "x.woff2", unicode_range=" "), True),
    ("range: a string", face("latin", G + "x.woff2", unicode_range="'U+0-7F'"), True),
    ("the font mark for a family",
     face("latin", G + "x.woff2", family="'a', " + clean.FONT_CSS_MARK), True),
]


@pytest.mark.parametrize("junk, good_after_survives",
                         [(junk, after) for _id, junk, after in HOSTILE_SHEETS],
                         ids=[name for name, _junk, _after in HOSTILE_SHEETS])
def test_nothing_hostile_in_a_stylesheet_reaches_the_entry(junk, good_after_survives):
    css = sheet(face("latin", G + "a.woff2"), junk, "\n",
                face("latin", G + "b.woff2", weight="700"))
    web = Web({LINK: css, G + "a.woff2": A, G + "b.woff2": B, G + "x.woff2": C,
               G + "y.woff2": C})
    result = audit(fonts.localize([LINK], fetch=web))
    wanted = [rule(A)] + ([rule(B, weight="700")] if good_after_survives else [])
    assert result.css == "\n".join(wanted)
    assert web.urls == [LINK, G + "a.woff2"] + ([G + "b.woff2"] if good_after_survives else [])


def test_a_descriptor_this_module_does_not_copy_is_left_out_not_fatal():
    """Google may add a descriptor one day (``size-adjust``, ``ascent-override``).
    One that fetches nothing costs the block nothing: it is simply not copied."""
    css = sheet(face("latin", G + "a.woff2",
                     extra="size-adjust: 90%; ascent-override: 92%; font-feature-settings: 'ss01';"))
    web = Web({LINK: css, G + "a.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == rule(A) and result.note == ""


@pytest.mark.parametrize("written, expected", [
    ("font-style: oblique", "font-style:oblique"),
    ("font-style: oblique -10deg 0deg", "font-style:oblique -10deg 0deg"),
    ("font-style: oblique 12.5deg", "font-style:oblique 12.5deg"),
    ("font-style: ITALIC", "font-style:italic"),
    ("font-weight: bold", "font-weight:bold"),
    ("font-weight: 1 1000", "font-weight:1 1000"),
    ("font-weight: 350.5", "font-weight:350.5"),
    ("font-stretch: 75% 125%", "font-stretch:75% 125%"),
    ("font-stretch: semi-condensed", "font-stretch:semi-condensed"),
    ("font-display: optional", "font-display:optional"),
    ("unicode-range: u+41, U+4??, U+1F600-1F64F",
     "unicode-range:U+0041,U+0400-04FF,U+1F600-1F64F"),
])
def test_each_descriptor_is_written_from_what_it_means(written, expected):
    """The honest spellings beyond the common case, each rebuilt canonically."""
    block = ("/* latin */\n@font-face { font-family: Inter Display; "
             f"{written}; src: url({G}a.woff2) format(woff2) }}")
    web = Web({LINK: sheet(block), G + "a.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == ('@font-face{font-family:"Inter Display";'
                          + (expected + ";" if not expected.startswith("unicode") else "")
                          + f'src:url(../fonts/{name_of(A)}) format("woff2")'
                          + (";" + expected if expected.startswith("unicode") else "") + "}")


@pytest.mark.parametrize("family", [
    "Inter", "IBM Plex Mono", "Source Sans 3", "M PLUS Rounded 1c",
    'O\'Brien "Display"', "back\\slash", "</style><script>alert(1)</script>",
    "x" + clean.FONT_CSS_MARK + "y", "url(https://evil.test/x)", "@import 'x'",
    "a;b}c{d", "Caf" + E_ACUTE, CJK_MIDDLE + " Sans", "*/ x /*", "a\\3c b",
], ids=repr)
def test_a_family_name_is_rewritten_and_still_names_the_same_family(family):
    """The entry's own stylesheet asks for the family by name, so the name must
    come out the same - and it is the one piece of free text in the result, so
    it must come out unable to be anything else."""
    source = '"' + serialize_string_value(family) + '"'
    web = Web({LINK: sheet(face("latin", G + "a.woff2", family=source)), G + "a.woff2": A})
    css = fonts.localize([LINK], fetch=web).css
    assert families(css) == [family]
    opening = '@font-face{font-family:"'
    assert css.startswith(opening)
    written = css[len(opening):].split('";', 1)[0]
    assert re.fullmatch(r"(?:[A-Za-z0-9 _-]|\\[0-9a-f]{1,6} )+", written), written
    for ch in "<>'\"()@*/{};:":
        assert ch not in written, ch
    page = fonts.apply(clean.clean("<p>x</p>").html, fonts.localize([LINK], fetch=web))
    assert page.count("<style>") == 1 and page.count("</style>") == 1
    assert "<script" not in page and clean.FONT_CSS_MARK not in page


def _kept(block) -> bool:
    """Whether ``block`` - one labelled @font-face - reads into a rule. Sent
    with a good block before it, which must survive either way."""
    web = Web({LINK: sheet(face("latin", G + "a.woff2"), block),
               G + "a.woff2": A, G + "x.woff2": B})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css.split("\n")[0] == rule(A)
    kept = result.css.count("@font-face") == 2
    assert result.note == ("" if kept else SOME + "1 typeface rule was not usable.")
    assert (G + "x.woff2" in web.urls) is kept
    return kept


@pytest.mark.parametrize("separator", [LINE_SEPARATOR, PARAGRAPH_SEPARATOR],
                         ids=["U+2028", "U+2029"])
def test_a_family_name_holding_a_line_or_paragraph_separator_is_dropped(separator):
    """Neither is a control character (their categories are Zl and Zp), and
    both end a line wherever the name is shown."""
    assert not _kept(face("latin", G + "x.woff2", family="'a" + separator + "b'", weight="700"))
    assert _kept(face("latin", G + "x.woff2", family="'a b'", weight="700"))


def test_a_family_name_may_be_a_hundred_characters_and_no_more():
    assert _kept(face("latin", G + "x.woff2", family="'" + "N" * 100 + "'", weight="700"))
    assert not _kept(face("latin", G + "x.woff2", family="'" + "N" * 101 + "'", weight="700"))


@pytest.mark.parametrize("style, kept", [
    ("oblique 90deg", True), ("oblique -90deg", True), ("oblique -90deg 90deg", True),
    ("oblique 91deg", False), ("oblique -91deg", False), ("oblique 90.001deg", False),
    ("oblique 0deg 91deg", False), ("oblique 0deg 1deg 2deg", False),
])
def test_an_oblique_angle_is_within_a_right_angle(style, kept):
    assert _kept(face("latin", G + "x.woff2", style=style, weight="700")) is kept


@pytest.mark.parametrize("stretch, kept", [
    ("1%", True), ("1000%", True), ("1% 1000%", True),
    ("0%", False), ("0.999%", False), ("1001%", False), ("1000.001%", False),
    ("50% 1001%", False),
])
def test_a_stretch_is_from_one_to_a_thousand_percent(stretch, kept):
    """The lower bound is 1%, not 0%: CSS allows a width of nothing and no
    typeface has one. The same range as a weight's numbers."""
    assert _kept(face("latin", G + "x.woff2", stretch=stretch, weight="700")) is kept


@pytest.mark.parametrize("weight, kept", [
    ("1", True), ("1000", True), ("0.999", False), ("1000.001", False), ("1 1001", False),
])
def test_a_weight_is_from_one_to_a_thousand(weight, kept):
    assert _kept(face("latin", G + "x.woff2", weight=weight, style="italic")) is kept


@pytest.mark.parametrize("hint, kept", [
    ("'woff2'", True), ("woff2", True), ("'WOFF2'", True),
    ("'woff2-variations'", True), ("woff2-variations", True),
    ("'woff'", False), ("'woff2x'", False), ("'woff2-variation'", False), ("'truetype'", False),
    ("'opentype'", False), ("'embedded-opentype'", False), ("'svg'", False),
    ("'collection'", False), ("''", False), ("'woff2', 'woff'", False), ("2", False),
])
def test_only_a_woff2_format_hint_is_accepted(hint, kept):
    """``woff2-variations`` is the older way to say "a variable WOFF2": still a
    WOFF2 file, and written out as plain ``woff2``."""
    block = face("latin", None, src=f"url({G}x.woff2) format({hint})", weight="700")
    assert _kept(block) is kept
    if kept:
        web = Web({LINK: sheet(block), G + "x.woff2": B})
        assert audit(fonts.localize([LINK], fetch=web)).css == rule(B, weight="700")


def test_the_unicode_range_list_has_a_length_limit():
    """One block with a very long list turned a 256 KB stylesheet into 448 KB
    of rule. The lists Google sends for a named character set are a few dozen
    entries long."""
    assert fonts.MAX_RANGES == 128
    at_limit = ", ".join(f"U+{0x1000 + n:X}" for n in range(128))
    web = Web({LINK: sheet(face("latin", G + "a.woff2", unicode_range=at_limit),
                           face("latin", G + "b.woff2", unicode_range=at_limit + ", U+2000")),
               G + "a.woff2": A, G + "b.woff2": B})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == rule(A, unicode_range=",".join(f"U+{0x1000 + n:04X}" for n in range(128)))
    assert web.urls == [LINK, G + "a.woff2"]
    assert result.note == SOME + "1 typeface rule was not usable."


def test_a_file_address_may_be_512_characters_and_no_more():
    def block(length):
        address = G + "n" * (length - len(G) - len(".woff2")) + ".woff2"
        assert len(address) == length
        return address, face("latin", address, weight="700")

    address, at_limit = block(512)
    web = Web({LINK: sheet(at_limit), address: B})
    assert audit(fonts.localize([LINK], fetch=web)).css == rule(B, weight="700")
    address, over = block(513)
    web = Web({LINK: sheet(over), address: B})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == "" and web.urls == [LINK]


ICONS = ("/* fallback */\n@font-face {\n  font-family: 'Material Icons';\n  font-style: normal;\n"
         "  font-weight: 400;\n  src: url(" + G + "icons.woff2) format('woff2');\n}\n")


def test_an_icon_font_that_is_not_copied_is_said():
    """Google files an icon font's blocks under ``fallback``. With the shipped
    character sets none is copied - and nothing used to say so, unless the
    whole stylesheet offered nothing."""
    web = Web({LINK: sheet(ICONS, face("latin", G + "a.woff2")), G + "a.woff2": A,
               G + "icons.woff2": B})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == rule(A) and web.urls == [LINK, G + "a.woff2"]
    assert result.note == SOME + ("1 typeface was not copied because it is not offered in the "
                                  "character sets copied here.")
    assert "Material" not in result.note and "Icons" not in result.note


def test_an_icon_font_is_copied_when_its_label_is_on_the_list():
    web = Web({LINK: sheet(ICONS, face("latin", G + "a.woff2")), G + "a.woff2": A,
               G + "icons.woff2": B})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"subsets": ["latin", "fallback"]}))
    assert set(result.files) == {name_of(A), name_of(B)} and result.note == ""
    assert '"Material Icons"' in result.css


def test_a_typeface_that_keeps_some_of_its_character_sets_is_not_noted():
    """The ordinary case: latin kept, cyrillic and greek left. Not a loss."""
    web = Web({LINK: sheet(face("cyrillic", G + "c.woff2"), face("greek", G + "g.woff2"),
                           face("latin", G + "a.woff2")), G + "a.woff2": A})
    assert audit(fonts.localize([LINK], fetch=web)).note == ""


def test_typefaces_not_offered_are_counted_by_name_across_stylesheets():
    """Three names, each wholly outside the copied sets, one of them asked for
    by both stylesheets: three. A fourth is kept by the second stylesheet."""
    first = sheet(face("cyrillic", G + "x.woff2", family="'One'"),
                  face("greek", G + "x.woff2", family="'One'"),
                  face("cyrillic", G + "x.woff2", family="'Two'"),
                  face("cyrillic", G + "x.woff2", family="'Kept'"))
    second = sheet(face("cyrillic", G + "x.woff2", family="'One'"),
                   face("latin", G + "x.woff2", family="'Three'").split("\n", 1)[1],  # no label
                   face("latin", G + "a.woff2", family="'Kept'"))
    web = Web({LINK: first, LINK_2: second, G + "a.woff2": A})
    result = audit(fonts.localize([LINK, LINK_2], fetch=web))
    assert result.css == rule(A, family="Kept")
    assert result.note == SOME + ("3 typefaces were not copied because they are not offered in "
                                  "the character sets copied here.")


def test_a_typeface_offered_here_that_failed_some_other_way_is_not_counted_twice():
    """Its block was in a copied set and was not usable, or its file did not
    arrive: that has its own reason, and "not offered" would be untrue."""
    css = sheet(face("latin", "https://evil.test/a.woff2", family="'Broken'"),
                face("latin", G + "gone.woff2", family="'Gone'"))
    result = audit(fonts.localize([LINK], fetch=Web({LINK: css})))
    assert result.note == NONE + ("1 typeface rule was not usable, "
                                  "1 file could not be fetched or was not a typeface.")


def test_a_byte_order_mark_does_not_cost_the_first_block():
    """Left in, the mark is a character CSS reads as the start of a name: the
    first block would be swallowed as the body of a rule called that."""
    web = Web({LINK: b"\xef\xbb\xbf" + sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    assert audit(fonts.localize([LINK], fetch=web)).css == rule(A)


def test_windows_line_endings_change_nothing():
    web = Web({LINK: sheet(face("latin", G + "a.woff2")).replace(b"\n", b"\r\n"),
               G + "a.woff2": A})
    assert audit(fonts.localize([LINK], fetch=web)).css == rule(A)


def test_a_deeply_nested_stylesheet_costs_that_stylesheet_only():
    """90,000 brackets, none closed. On tinycss2 1.5.1 that is NOT a crash: its
    parser keeps its own stack, reads to the end, and hands back one
    ``@font-face`` block holding a nested block and no declaration. So it is
    one block that was offered here and was not usable - nothing counted as a
    fault. (A tinycss2 that recursed instead would make this "1 stylesheet made
    the reader fail", counted; either way the other stylesheet is untouched.)"""
    web = Web({LINK: sheet("/* latin */ @font-face {" + "{[(" * 30000),
               LINK_2: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    _degrade.reset()
    result = audit(fonts.localize([LINK, LINK_2], fetch=web))
    assert result.css == rule(A) and result.files == {name_of(A): A}
    assert result.note == SOME + "1 typeface rule was not usable."
    assert _degrade.counts() == {}


def test_a_stylesheet_broken_at_random_never_breaks_the_promises():
    """The corpus above is the breakage someone thought of. This is the rest:
    a good stylesheet with pieces inserted, cut out and moved, from a fixed
    seed. Whatever comes of each, the audit holds, nothing is counted as a bug,
    only the file host is asked for files, and the filled document still has
    one ``<style>`` and no way out of it."""
    rng = random.Random(20261006)
    bits = ["{", "}", "(", ")", "[", "]", ";", ":", ",", "'", '"', "\\", "/*", "*/", "<", ">",
            "</style>", "<!--", "-->", "@import ", "@font-face", "url(", "U+", "?", "\n", " ",
            "url(https://evil.test/x.woff2)", "!important", "\\72 ", "\\0 ", "format(", "src:",
            "font-family:", "expression(", "e9", "%", "deg", "-", clean.FONT_CSS_MARK,
            "../fonts/" + "a" * 20 + ".woff2", G + "q.woff2", chr(0), chr(0xFEFF)]
    good = (face("latin", G + "a.woff2", weight="100 900", stretch="100%")
            + face("latin-ext", G + "b.woff2", style="italic") + face("cyrillic", G + "c.woff2"))
    files = {G + "a.woff2": A, G + "b.woff2": B, G + "c.woff2": C, G + "q.woff2": woff2("q")}
    shell = clean.clean("<p>x</p>").html
    _degrade.reset()
    for _ in range(400):
        text = good
        for _ in range(rng.randint(1, 6)):
            at, how = rng.randrange(len(text)), rng.random()
            if how < 0.5:
                text = text[:at] + rng.choice(bits) + text[at:]
            elif how < 0.8:
                text = text[:at] + text[at + rng.randint(1, 12):]
            else:
                end = rng.randrange(at, len(text))
                text = text[:at] + text[end:] + text[at:end]
        web = Web({LINK: text.encode("utf-8"), **files})
        result = audit(fonts.localize([LINK], fetch=web))
        assert web.urls[0] == LINK
        for url in web.urls[1:]:
            parts = urlsplit(url)
            assert (parts.scheme, parts.netloc) == ("https", fonts.FILE_HOST), url
            assert url.endswith(".woff2") and not set(url) & set("?#%@\\ "), url
        page = fonts.apply(shell, result)
        assert page.count("<style>") == 1 and page.count("</style>") == 1, text
    assert _degrade.counts() == {}
