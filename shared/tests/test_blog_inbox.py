"""shared.blog_inbox: the blog's requests, its validators, the frame policy and
its config - the one module the service, the connector gate and Tier 1 share."""
import pathlib
import subprocess
import sys
import unicodedata

import pytest

from shared import blog_inbox as bi
from shared.config_toml import toml_loader

REPO = pathlib.Path(__file__).resolve().parents[2]

DOC = "<!doctype html><title>T</title><h1>Nuclear Stocks Thesis</h1><p>Body.</p>"

# ⚠ Every invisible character below is written as a CODE POINT and built with
# chr(), never typed and never spelled as a backslash escape. A zero-width
# space in source reads as "" to a person, and anything that tidies text (an
# editor, a formatter, a tool decoding an escape on the way to disk and another
# stripping what it produced) can remove it. The test then runs on an empty
# string and passes while asserting nothing: with ch = "" the format-character
# test below was green. test_these_files_hold_no_invisible_character keeps it so.
ZWSP, ZWJ, WORD_JOINER, RLO, SOFT_HYPHEN, NBSP, LINE_SEP, KELVIN, FDFA = (
    chr(p) for p in (0x200B, 0x200D, 0x2060, 0x202E, 0x00AD, 0x00A0, 0x2028,
                     0x212A, 0xFDFA))

# Unicode category Cf, "format": they draw nothing and sit inside words.
FORMAT_POINTS = (
    0x200B, 0x200C, 0x200D,                    # zero-width space, non-joiner, joiner
    0x2060, 0x00AD, 0xFEFF,                    # word joiner, soft hyphen, byte-order mark
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,    # bidi embeddings and overrides
    0x2066, 0x2067, 0x2068, 0x2069,            # bidi isolates
    0x200E, 0x200F, 0x061C,                    # left-to-right, right-to-left, Arabic letter mark
)
# NOT category Cf, and blank all the same: the "filler" letters (Hangul fillers
# and the halfwidth one are letters, category Lo; the Braille blank is a symbol)
# and the variation selectors (marks, category Mn).
BLANK_POINTS = (0x3164, 0x115F, 0x1160, 0xFFA0, 0x2800) + tuple(range(0xFE00, 0xFE10))


def _point(p):
    return f"U+{p:04X}"


def _cfg(monkeypatch, **sections):
    """Stand a config in for the file. ``limits()`` and friends read ``load``
    at CALL time, so this is what proves a value is read and not a literal."""
    monkeypatch.setattr(bi, "load", lambda: sections)


# ── the streams and the views ───────────────────────────────────────────────

def test_the_inbox_stream_is_its_own_and_never_the_owner_stream():
    """The gate's Redis user may XADD on exactly this key (phase 2). cmd:blog
    carries publish, discard and unpublish."""
    assert bi.INBOX_STREAM == "cmd:blog_inbox"
    assert bi.INBOX_STREAM != f"cmd:{bi.OWNER_DOMAIN}"
    assert bi.OWNER_DOMAIN == "blog"


def test_the_views_are_the_blogs_own():
    assert (bi.VIEW_DRAFTS, bi.VIEW_POSTS, bi.VIEW_RESULT) == (
        "blog:drafts", "blog:posts", "blog:result")


def test_an_answer_view_is_per_request():
    rid = bi.new_id()
    assert bi.answer_view(rid) == f"blog:answer:{rid}"
    assert bi.answer_view(rid) != bi.answer_view(bi.new_id())


@pytest.mark.parametrize("raw", ["", None, 7, "x", "../drafts", "blog:drafts",
                                 "0123456789ABCDEF", "0123456789abcde",
                                 "0123456789abcdef0", "0123456789abcdef\n",
                                 " 0123456789abcdef", "0123456789abcde*"])
def test_a_bad_id_names_no_answer_view(raw):
    """The id becomes part of a Redis KEY NAME. A caller's string that is not an
    id this module made must never be spliced into one."""
    assert bi.is_id(raw) is False
    with pytest.raises(ValueError):
        bi.answer_view(raw)


def test_a_new_id_is_sixteen_hex_and_not_repeated():
    ids = {bi.new_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(bi.is_id(i) for i in ids)


def test_no_pattern_accepts_a_trailing_newline():
    """``$`` matches before a final newline, so ``^...$`` with ``.match`` lets
    "abc\\n" through - and a slug is a folder name, an id a key name, a font
    name a file name. The patterns end in ``\\Z`` so every caller is safe,
    whichever of match / fullmatch it uses."""
    assert bi.ID_RE.match("0123456789abcdef\n") is None
    assert bi.SLUG_RE.match("abc\n") is None
    assert bi.FONT_NAME_RE.match("0123456789abcdef0123.woff2\n") is None
    assert bi.ID_RE.match("0123456789abcdef")
    assert bi.SLUG_RE.match("abc")
    assert bi.FONT_NAME_RE.match("0123456789abcdef0123.woff2")


@pytest.mark.parametrize("raw", ["", "a.woff2", "0123456789abcdef0123.woff",
                                 "0123456789ABCDEF0123.woff2",
                                 "../0123456789abcdef0123.woff2",
                                 "0123456789abcdef0123.woff2.html",
                                 "0123456789abcdef01234.woff2"])
def test_a_font_name_is_twenty_hex_and_woff2_and_nothing_else(raw):
    assert bi.FONT_NAME_RE.match(raw) is None


# ── slugs ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["", None, "Fonts", "fonts", "../x", "a--b", "-a",
                                 "a_b", "a b", "a/b", "x" * 200, "ü"])
def test_a_slug_that_could_escape_or_collide_is_refused(raw):
    assert bi.clean_slug(raw) is None


@pytest.mark.parametrize("raw", [7, 7.5, True, ["a"], {"a": 1}, b"abc", "a-",
                                 "a.b", "a\\b", ".", "..", "a\nb", "a\x00b",
                                 "%2e%2e", "a:b", "a?b", "a#b", "a-é"])
def test_a_slug_is_text_of_the_allowed_characters_only(raw):
    assert bi.clean_slug(raw) is None


@pytest.mark.parametrize("raw, want", [
    ("nuclear-stocks-thesis", "nuclear-stocks-thesis"),
    ("A-B", "a-b"),
    ("  a1  ", "a1"),
    ("2026", "2026"),
])
def test_a_usable_slug_is_returned_lowered_and_trimmed(raw, want):
    assert bi.clean_slug(raw) == want


def test_the_slug_length_limit_is_the_configured_one(monkeypatch):
    cap = bi.limits()["slug_chars"]
    assert bi.clean_slug("x" * cap) == "x" * cap
    assert bi.clean_slug("x" * (cap + 1)) is None
    _cfg(monkeypatch, limits={"slug_chars": 20})
    assert bi.clean_slug("x" * 20) == "x" * 20
    assert bi.clean_slug("x" * 21) is None


def test_every_reserved_slug_is_refused():
    """blog/fonts/ is the typeface folder: an entry with that address would be
    written over it."""
    assert "fonts" in bi.RESERVED_SLUGS
    for slug in bi.RESERVED_SLUGS:
        assert bi.SLUG_RE.match(slug), "a reserved word the pattern refuses anyway"
        assert bi.clean_slug(slug) is None
        assert bi.clean_slug(slug.upper()) is None


def test_slugify_always_returns_a_usable_slug():
    for title in ("Nuclear Stocks Thesis", "  ", "¿Qué?", "a/b\\c", "x" * 500, None,
                  "Fonts", "fonts", "---", "../..", 7, "中文", "a" * 79 + " b c",
                  "\ud800", "a\nb"):
        slug = bi.slugify(title)
        assert bi.clean_slug(slug) == slug, repr(title)


@pytest.mark.parametrize("title, want", [
    ("Nuclear Stocks Thesis", "nuclear-stocks-thesis"),
    ("¿Qué?", "que"),
    ("a/b\\c", "a-b-c"),
    ("  Hello,   World!  ", "hello-world"),
    ("  ", "entry"),
    (None, "entry"),
    ("中文", "entry"),
])
def test_slugify_folds_to_ascii_words_joined_by_hyphens(title, want):
    assert bi.slugify(title) == want


def test_slugify_cuts_to_the_limit_without_a_trailing_hyphen(monkeypatch):
    _cfg(monkeypatch, limits={"slug_chars": 20})
    # 10 + "-" + 8 = 19 characters, so the 20th - where the cut lands - is a "-".
    assert bi.slugify("abcdefghij klmnopqr stuvwxyz") == "abcdefghij-klmnopqr"
    assert len(bi.slugify("x" * 500)) == 20


def test_slugify_bounds_its_work_before_it_folds_the_title(monkeypatch):
    """U+FDFA is one character that NFKD expands to eighteen. Folding a whole
    hostile title first took 4.4 s on 170,000 of them; the title is cut to a
    small multiple of the address limit BEFORE anything else looks at it.

    Measured as what the fold is HANDED, not by a clock: a wall-clock bound
    passes or fails with the machine's load."""
    assert len(FDFA) == 1 and len(unicodedata.normalize("NFKD", FDFA)) == 18
    folded = []
    real = unicodedata.normalize

    def spy(form, text):
        folded.append(len(text))
        return real(form, text)

    monkeypatch.setattr(bi.unicodedata, "normalize", spy)
    slug = bi.slugify(FDFA * 200_000)
    assert bi.clean_slug(slug) == slug
    assert folded and max(folded) < 1000, folded
    # Real words at the front of a very long title still make the address.
    assert bi.slugify("Nuclear Stocks " + FDFA * 200_000) == "nuclear-stocks"
    assert max(folded) < 1000, folded


@pytest.mark.parametrize("raw", ["abc\n", "abc\r\n", "\nabc", "abc\t", "abc\x0b",
                                 "abc" + NBSP, "abc" + LINE_SEP],
                         ids=["LF", "CRLF", "leading-LF", "TAB", "VT", "NBSP",
                              "LINE-SEPARATOR"])
def test_only_plain_spaces_are_trimmed_from_a_slug(raw):
    """A typed address may carry a stray space at either end. A newline, a tab
    or any other whitespace is not something a text field produces: it is
    refused, never quietly removed."""
    # the case really does carry whitespace that str.strip() would have taken
    assert raw != "abc" and raw.strip() == "abc" and " " not in raw
    assert bi.clean_slug(raw) is None
    assert bi.existing_slug(raw) is None


def test_a_letter_that_only_lower_cases_to_ascii_is_not_an_address():
    """U+212A, the Kelvin sign, lower-cases to a plain "k". Lower-casing first
    turned an address typed with it into a different, valid one - a silent
    repair, which ``clean_slug`` never does. ``slugify`` is the function that
    repairs, and it still folds it."""
    assert len(KELVIN) == 1 and not KELVIN.isascii() and KELVIN.lower() == "k"
    typed = KELVIN + "elvin"
    assert bi.clean_slug(typed) is None
    assert bi.existing_slug(typed) is None
    assert bi.clean_fields({"slug": typed})["slug"] == ""
    assert bi.owner_command("unpublish", bi.new_id(), slug=typed) is None
    assert bi.slugify(typed) == "kelvin"
    # plain capitals are still lowered, as before
    assert bi.clean_slug("Kelvin") == "kelvin"


# ── the address of an entry that already exists ─────────────────────────────

def test_an_existing_address_outlives_a_lowered_limit(monkeypatch):
    """``clean_slug`` holds a NEW address to the current [limits] slug_chars.
    Applied to an entry already published, lowering that limit would leave the
    entry with an address nothing could name - so it could never be
    unpublished. An existing address is held to the limit's CEILING instead."""
    slug = "a" * 60
    _cfg(monkeypatch, limits={"slug_chars": 40})
    assert bi.clean_slug(slug) is None                   # too long to publish now
    assert bi.existing_slug(slug) == slug                # still nameable
    rid = bi.new_id()
    assert bi.owner_command("unpublish", rid, slug=slug) == {
        "type": "unpublish", "args": {"request_id": rid, "slug": slug}}


def test_an_existing_address_is_still_an_allow_list():
    ceiling = bi.BOUNDS[("limits", "slug_chars")][1]
    assert bi.SLUG_CHARS_CEILING == ceiling
    assert bi.existing_slug("x" * ceiling) == "x" * ceiling
    assert bi.existing_slug(" Nuclear-Stocks ") == "nuclear-stocks"
    rid = bi.new_id()
    for bad in ("../x", "fonts", "Fonts", "abc\n", "x" * (ceiling + 1), "", None, 7,
                "a--b", "a b", "a/b", "a.b", "ü"):
        assert bi.existing_slug(bad) is None, repr(bad)
        assert bi.owner_command("unpublish", rid, slug=bad) is None, repr(bad)


def test_publishing_and_drafting_keep_the_current_limit(monkeypatch):
    """Only naming an entry that exists is loosened. A new address - typed on
    the Blog page or sent by Claude Chat - is still held to today's limit."""
    slug = "a" * 60
    _cfg(monkeypatch, limits={"slug_chars": 40})
    assert bi.clean_fields({"slug": slug})["slug"] == ""
    cmd = bi.owner_command("publish", bi.new_id(), draft_id=bi.new_id(),
                           fields={"slug": slug})
    assert cmd["args"]["fields"]["slug"] == ""
    sub = bi.submit_command(DOC, {"slug": slug}, source="chat", request_id=bi.new_id())
    assert sub["args"]["fields"]["slug"] == ""


def test_slugify_never_returns_a_reserved_slug():
    for word in bi.RESERVED_SLUGS:
        slug = bi.slugify(word.title())
        assert slug not in bi.RESERVED_SLUGS and bi.clean_slug(slug) == slug


# ── fields ──────────────────────────────────────────────────────────────────

def test_fields_are_strings_cut_to_the_limits_and_nothing_else_survives():
    f = bi.clean_fields({"title": " T " * 200, "summary": 7, "tags": ["a", 3, "b" * 99] * 9,
                         "slug": "../etc", "html": "<x>", "extra": 1})
    assert set(f) == {"title", "summary", "tags", "slug"}
    assert len(f["title"]) <= bi.limits()["title_chars"] and f["summary"] == ""
    assert len(f["tags"]) <= bi.limits()["max_tags"] and f["slug"] == ""


def test_usable_fields_come_back_trimmed():
    f = bi.clean_fields({"title": "  Nuclear   Stocks\n Thesis ", "summary": " Why. ",
                         "tags": [" energy ", "Energy", "", "  ", "uranium"],
                         "slug": " Nuclear-Stocks "})
    assert f == {"title": "Nuclear Stocks Thesis", "summary": "Why.",
                 "tags": ["energy", "uranium"], "slug": "nuclear-stocks"}


def test_each_field_is_cut_to_its_own_limit(monkeypatch):
    _cfg(monkeypatch, limits={"title_chars": 5, "summary_chars": 7, "max_tags": 2,
                              "tag_chars": 3})
    f = bi.clean_fields({"title": "abcdefghij", "summary": "abcdefghij",
                         "tags": ["abcdef", "ghijkl", "mnopqr"]})
    assert f["title"] == "abcde" and f["summary"] == "abcdefg"
    assert f["tags"] == ["abc", "ghi"]


def test_a_cut_never_leaves_trailing_space():
    f = bi.clean_fields({"title": "a" * (bi.limits()["title_chars"] - 1) + " bcd"})
    assert f["title"] == "a" * (bi.limits()["title_chars"] - 1)


def test_control_characters_never_reach_a_field():
    """A title is written into a manifest and a page head. A NUL, an escape or
    a lone surrogate (which cannot be encoded, so it would raise at the write)
    is not text."""
    f = bi.clean_fields({"title": "a\x00b\x1b[31mc\x7f", "summary": "x\ud800y\r\nz",
                         "tags": ["t\x00ag", "\ud800"]})
    assert f["title"] == "a b [31mc" and f["summary"] == "xy z"
    assert f["tags"] == ["t ag"]
    for value in (f["title"], f["summary"], *f["tags"]):
        value.encode("utf-8")                        # raises on a surrogate


def test_a_field_made_only_of_invisible_characters_is_empty():
    """Unicode FORMAT characters (zero-width space and joiners, word joiner,
    soft hyphen, the byte-order mark, the bidi overrides and isolates) draw
    nothing. A title of them is a blank row in the list of entries that reads
    as "has a title"; a bidi override in a summary reorders the text after it."""
    title, summary = ZWSP * 2, "a" + RLO + "b"
    tags = [ZWSP, WORD_JOINER, SOFT_HYPHEN, "ok"]
    assert len(title) == 2 and len(summary) == 3            # nothing was stripped
    assert [len(t) for t in tags] == [1, 1, 1, 2]
    f = bi.clean_fields({"title": title, "summary": summary, "tags": tags})
    assert f["title"] == ""
    assert RLO not in f["summary"] and f["summary"] == "ab"
    assert f["tags"] == ["ok"]


@pytest.mark.parametrize("point", FORMAT_POINTS, ids=_point)
def test_no_format_character_survives_in_any_field(point):
    ch = chr(point)
    assert len(ch) == 1
    assert unicodedata.category(ch) == "Cf"
    f = bi.clean_fields({"title": f"{ch}Ti{ch}tle{ch}", "summary": ch * 5,
                         "tags": [ch, f"t{ch}ag", f"{ch} {ch}"]})
    assert f == {"title": "Title", "summary": "", "tags": ["tag"], "slug": ""}


@pytest.mark.parametrize("point", BLANK_POINTS, ids=_point)
def test_no_blank_letter_or_variation_selector_survives_in_any_field(point):
    """These draw nothing and are NOT format characters, so the category rule
    alone lets them through: a title of Hangul fillers is a blank row."""
    ch = chr(point)
    assert len(ch) == 1
    assert unicodedata.category(ch) != "Cf"
    assert point in bi.BLANK_POINTS
    f = bi.clean_fields({"title": f"{ch}Ti{ch}tle{ch}", "summary": ch * 5,
                         "tags": [ch, f"t{ch}ag", f"{ch} {ch}"]})
    assert f == {"title": "Title", "summary": "", "tags": ["tag"], "slug": ""}


def test_the_blank_characters_removed_are_exactly_the_listed_ones():
    """The module's list and this file's are written out separately, so a
    character dropped from one is noticed."""
    assert set(bi.BLANK_POINTS) == set(BLANK_POINTS)


def test_two_tags_that_differ_only_by_an_invisible_character_are_one_tag():
    second, third = "ener" + ZWSP + "gy", "ENERGY" + WORD_JOINER
    assert (len(second), len(third)) == (7, 7)
    f = bi.clean_fields({"tags": ["energy", second, third]})
    assert f["tags"] == ["energy"]


def test_invisible_characters_in_front_cannot_blank_a_real_title():
    """The field is cut for size before it is cleaned of whitespace. Cut BEFORE
    the invisible characters were removed, 700 zero-width spaces in front used
    up the whole cut and the title behind them came back empty."""
    padding = ZWSP * 700
    assert len(padding) == 700
    f = bi.clean_fields({"title": padding + "Real title",
                         "summary": padding + "Real summary",
                         "tags": [padding + "tag"]})
    assert f == {"title": "Real title", "summary": "Real summary",
                 "tags": ["tag"], "slug": ""}


def test_a_huge_tag_list_is_not_a_huge_amount_of_work(monkeypatch):
    """Only as many tags as could be kept are examined, so the work does not
    follow the payload's size: a million-entry list cleans the first few, not
    all of them."""
    calls = []
    real = bi._text
    monkeypatch.setattr(bi, "_text", lambda raw, cap: calls.append(raw) or real(raw, cap))
    f = bi.clean_fields({"tags": [f"tag{i}" for i in range(1_000_000)]})
    assert len(f["tags"]) == bi.limits()["max_tags"]
    assert len(calls) <= bi.limits()["max_tags"] * 4


def test_only_so_many_tag_candidates_are_examined():
    """The cap on candidates can swallow usable tags that sit past it - the
    price of the bound. Duplicates and blanks among the first few can crowd out
    a real tag deeper in; a submitter who wants all six kept sends at most a few
    times six."""
    lim = bi.limits()
    blanks = [""] * (lim["max_tags"] * 4)
    assert bi.clean_fields({"tags": blanks + ["real"]})["tags"] == []
    near = ["" ] * (lim["max_tags"] * 4 - 1) + ["kept", "lost"]
    assert bi.clean_fields({"tags": near})["tags"] == ["kept"]


def test_the_search_for_a_title_is_still_bounded():
    """Removing first must not mean reading a megabyte character by character:
    past ``_SCAN_CHARS`` the field is not looked at. A title whose real text
    the window does not reach whole is answered ``""`` - a prefix of a title is
    worse than none - not the fragment it managed to keep.

    Pinned across the boundary, not only at its ends: as the padding grows the
    window reaches less and less of "Real title", and every one of those is
    "", never "Real" or "R"."""
    assert bi._SCAN_CHARS >= 4096
    real = "Real title"
    # the whole field fits in the window: kept whole
    assert bi.clean_fields({"title": ZWSP * 50 + real})["title"] == real
    # the field ends exactly at the window: still whole
    edge = ZWSP * (bi._SCAN_CHARS - len(real)) + real
    assert len(edge) == bi._SCAN_CHARS
    assert bi.clean_fields({"title": edge})["title"] == real
    # the real text straddles the window boundary - its head is kept, its tail is
    # not - so the whole field is refused, whatever the padding
    for padding in (bi._SCAN_CHARS - len(real) + 1, bi._SCAN_CHARS - 4,
                    bi._SCAN_CHARS - 1, bi._SCAN_CHARS + 1):
        buried = ZWSP * padding + real
        assert len(buried) > bi._SCAN_CHARS
        assert bi.clean_fields({"title": buried})["title"] == ""
    # a genuinely long field of REAL text reaches "enough" well inside the
    # window, so it is kept (cut to the limit), not refused: the refusal is only
    # for a field the window could not get through to real content.
    long_title = bi.clean_fields({"title": "word " * bi._SCAN_CHARS})["title"]
    assert long_title and len(long_title) <= bi.limits()["title_chars"]


def test_a_compound_emoji_is_split_and_that_is_known():
    """Removing the zero-width joiner has a cost, and it is this one: a family
    or a flag built with it comes apart into its members. Accepted for a title,
    a summary and a tag; the entry's own body is never passed through here."""
    woman, girl = chr(0x1F469), chr(0x1F467)
    family = woman + ZWJ + girl
    assert len(family) == 3
    assert bi.clean_fields({"title": family})["title"] == woman + girl


def test_these_files_hold_no_invisible_character():
    """This file and the module it tests, read as text: no format character,
    no line or paragraph separator, no no-break space, no blank letter. See the
    note at the top of this file for what one costs."""
    blank = set(BLANK_POINTS)
    for path in (pathlib.Path(__file__), REPO / "shared" / "blog_inbox.py"):
        text = path.read_text(encoding="utf-8")
        assert text.strip()
        # split("\n"), not splitlines(): that one breaks lines ON U+2028 and
        # U+2029 and would swallow the very characters this is looking for.
        found = [(n, _point(ord(ch)))
                 for n, line in enumerate(text.split("\n"), 1) for ch in line
                 if unicodedata.category(ch) in ("Cf", "Zl", "Zp", "Cs", "Co")
                 or ord(ch) == 0x00A0 or ord(ch) in blank]
        assert not found, f"{path.name}: {found[:10]}"


@pytest.mark.parametrize("raw", [None, "", "title", 7, ["title"], {"tags": "a,b"},
                                 {"tags": {"a": 1}}, {"title": ["x"]},
                                 {"title": b"x", "summary": None, "slug": 9}])
def test_unusable_fields_are_empty_and_never_raise(raw):
    assert bi.clean_fields(raw) == {"title": "", "summary": "", "tags": [], "slug": ""}


def test_clean_fields_is_stable_on_its_own_output():
    """The gate cleans, then the service cleans what it reads: twice must be
    once."""
    once = bi.clean_fields({"title": " T " * 200, "summary": "s " * 400,
                            "tags": ["a", "b" * 99, "A"], "slug": "My-Slug"})
    assert bi.clean_fields(once) == once


# ── the document ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("html", ["", "   \n\t", None, 7, b"<p>x</p>", ["<p>"]])
def test_a_document_is_non_empty_text(html):
    assert bi.html_ok(html) is False


def test_the_size_limit_counts_utf8_bytes_not_characters(monkeypatch):
    _cfg(monkeypatch, limits={"max_html_kb": 1})
    assert bi.html_ok("x" * 1024) is True
    assert bi.html_ok("x" * 1025) is False
    assert bi.html_ok("é" * 512) is True          # 2 bytes each: 1024
    assert bi.html_ok("é" * 513) is False         # 513 characters, 1026 bytes


def test_a_document_that_cannot_be_encoded_is_refused_not_raised():
    """JSON may carry a lone surrogate; writing it to disk or to Redis raises."""
    assert bi.html_ok("<p>\ud800</p>") is False


def test_a_document_over_the_limit_builds_no_command(monkeypatch):
    big = "x" * (bi.limits()["max_html_kb"] * 1024 + 1)
    assert bi.submit_command(big, {}, source="upload", request_id=bi.new_id()) is None
    assert bi.revise_command(bi.new_id(), big, {}, request_id=bi.new_id()) is None
    _cfg(monkeypatch, limits={"max_html_kb": 1})
    assert bi.submit_command("x" * 1025, {}, source="upload",
                             request_id=bi.new_id()) is None
    assert bi.submit_command("x" * 1024, {}, source="upload",
                             request_id=bi.new_id()) is not None


# ── the commands ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", ["chat", "upload"])
def test_a_submit_command_carries_the_document_and_cleaned_fields(source):
    rid = bi.new_id()
    cmd = bi.submit_command(DOC, {"title": "  A  title ", "slug": "../etc",
                                  "tags": ["x", 3], "junk": 1},
                            source=source, request_id=rid)
    assert cmd == {"type": bi.SUBMIT_TYPE, "args": {
        "request_id": rid, "source": source, "html": DOC,
        "fields": {"title": "A title", "summary": "", "tags": ["x"], "slug": ""}}}
    assert bi.SUBMIT_TYPE == "draft_submit"


def test_a_submit_command_without_fields_still_carries_all_four():
    for fields in (None, {}, "junk"):
        cmd = bi.submit_command(DOC, fields, source="upload", request_id=bi.new_id())
        assert cmd["args"]["fields"] == {"title": "", "summary": "", "tags": [], "slug": ""}


def test_a_submit_command_names_the_entry_it_replaces_only_when_told_to():
    rid = bi.new_id()
    plain = bi.submit_command(DOC, {}, source="upload", request_id=rid)
    assert "revises" not in plain["args"]
    assert "revises" not in bi.submit_command(DOC, {}, source="upload", request_id=rid,
                                              revises=None)["args"]
    cmd = bi.submit_command(DOC, {}, source="upload", request_id=rid,
                            revises="an-older-entry")
    assert cmd == {"type": bi.SUBMIT_TYPE, "args": {
        "request_id": rid, "source": "upload", "html": DOC,
        "fields": {"title": "", "summary": "", "tags": [], "slug": ""},
        "revises": "an-older-entry"}}


def test_a_submit_command_keeps_an_address_longer_than_a_new_one_may_be(monkeypatch):
    """The entry is already out there: held to the limit's ceiling, as
    ``existing_slug`` holds it, not to today's setting."""
    _cfg(monkeypatch, limits={"slug_chars": 16})
    longer = "a" * 60
    assert bi.clean_slug(longer) is None
    cmd = bi.submit_command(DOC, {}, source="upload", request_id=bi.new_id(), revises=longer)
    assert cmd["args"]["revises"] == longer


@pytest.mark.parametrize("bad", ["", "   ", "Not An Address!", "../x", "a--b", "fonts", "con",
                                 "a" * 121, "x" + chr(0x0A), 7, ["a"], {"slug": "a"}, False,
                                 0])
def test_a_submit_command_naming_what_cannot_be_an_entry_is_not_built(bad):
    """Refused, never filed as a NEW entry: a replacement that quietly became a
    second entry is the mistake this stops."""
    assert bi.submit_command(DOC, {}, source="upload", request_id=bi.new_id(),
                             revises=bad) is None


def test_the_entry_a_submit_command_replaces_is_named_by_keyword_only():
    with pytest.raises(TypeError):
        bi.submit_command(DOC, {}, "an-older-entry", source="upload", request_id=bi.new_id())


def test_an_unknown_source_or_a_bad_request_id_builds_no_command():
    rid = bi.new_id()
    for source in ("", None, "Chat", "email", "upload ", 1):
        assert bi.submit_command(DOC, {}, source=source, request_id=rid) is None
    for bad in ("", None, "x", rid.upper(), rid + "0", rid + "\n", 7):
        assert bi.submit_command(DOC, {}, source="chat", request_id=bad) is None
        assert bi.revise_command(bi.new_id(), DOC, {}, request_id=bad) is None
        assert bi.owner_command("discard", bad, draft_id=bi.new_id()) is None
    assert set(bi.SOURCES) == {"chat", "upload"}


def test_a_revise_command_names_the_draft_it_replaces():
    rid, draft = bi.new_id(), bi.new_id()
    cmd = bi.revise_command(draft, DOC, {"summary": " S "}, request_id=rid)
    assert cmd == {"type": bi.REVISE_TYPE, "args": {
        "request_id": rid, "draft_id": draft, "html": DOC,
        "fields": {"title": "", "summary": "S", "tags": [], "slug": ""}}}
    assert bi.REVISE_TYPE == "draft_revise"
    for bad in ("", None, "../x", draft.upper(), 7):
        assert bi.revise_command(bad, DOC, {}, request_id=rid) is None
    assert bi.revise_command(draft, "", {}, request_id=rid) is None


def test_there_is_no_publish_command_for_the_inbox():
    """owner_command builds publish/discard/unpublish; nothing here puts one on
    INBOX_STREAM. The service enforces it; this pins the builder."""
    assert bi.owner_command("publish", bi.new_id(), draft_id=bi.new_id())["type"] == "publish"
    assert bi.owner_command("delete_everything", bi.new_id()) is None
    assert set(bi.OWNER_KINDS) == {"publish", "discard", "unpublish"}
    assert set(bi.INBOX_TYPES) == {bi.SUBMIT_TYPE, bi.REVISE_TYPE}
    assert not set(bi.INBOX_TYPES) & set(bi.OWNER_KINDS)
    # The two builders the gate calls can produce nothing but an inbox type...
    for cmd in (bi.submit_command(DOC, {}, source="chat", request_id=bi.new_id()),
                bi.revise_command(bi.new_id(), DOC, {}, request_id=bi.new_id())):
        assert cmd["type"] in bi.INBOX_TYPES
    # ...and the owner builder refuses to build one of those.
    for kind in bi.INBOX_TYPES:
        assert bi.owner_command(kind, bi.new_id(), draft_id=bi.new_id()) is None


def test_publish_names_a_draft_and_carries_the_operators_edits():
    rid, draft = bi.new_id(), bi.new_id()
    assert bi.owner_command("publish", rid, draft_id=draft) == {
        "type": "publish", "args": {
            "request_id": rid, "draft_id": draft,
            "fields": {"title": "", "summary": "", "tags": [], "slug": ""}}}
    cmd = bi.owner_command("publish", rid, draft_id=draft,
                           fields={"title": " Edited ", "slug": "Edited-Address",
                                   "tags": ["a"], "html": "<x>"})
    assert cmd["args"]["fields"] == {"title": "Edited", "summary": "",
                                     "tags": ["a"], "slug": "edited-address"}


def test_discard_names_a_draft_and_unpublish_names_an_entry():
    rid, draft = bi.new_id(), bi.new_id()
    assert bi.owner_command("discard", rid, draft_id=draft) == {
        "type": "discard", "args": {"request_id": rid, "draft_id": draft}}
    assert bi.owner_command("unpublish", rid, slug=" Nuclear-Stocks ") == {
        "type": "unpublish", "args": {"request_id": rid, "slug": "nuclear-stocks"}}


def test_an_owner_command_missing_or_misnaming_its_target_is_not_built():
    rid, draft = bi.new_id(), bi.new_id()
    for kind in ("publish", "discard"):
        assert bi.owner_command(kind, rid) is None
        for bad in ("", None, "../x", draft.upper(), 7):
            assert bi.owner_command(kind, rid, draft_id=bad) is None
    assert bi.owner_command("unpublish", rid) is None
    for bad in ("", None, "../x", "fonts", "a b", 7):
        assert bi.owner_command("unpublish", rid, slug=bad) is None
    # A target another kind takes, or a name this module does not know, is a
    # caller's mistake: refused whole, never built with the stray part dropped.
    assert bi.owner_command("discard", rid, draft_id=draft, slug="a") is None
    assert bi.owner_command("discard", rid, draft_id=draft, fields={}) is None
    assert bi.owner_command("unpublish", rid, slug="a", draft_id=draft) is None
    assert bi.owner_command("publish", rid, draft_id=draft, html=DOC) is None
    for kind in (None, 7, "", "Publish", ["publish"]):
        assert bi.owner_command(kind, rid, draft_id=draft) is None


# ── the frame ───────────────────────────────────────────────────────────────

def test_the_sandbox_never_allows_scripts():
    assert "allow-scripts" not in bi.ENTRY_SANDBOX and "allow-forms" not in bi.ENTRY_SANDBOX
    assert "sandbox " + bi.ENTRY_SANDBOX in bi.ENTRY_CSP and "default-src 'none'" in bi.ENTRY_CSP


def test_the_policy_is_the_designs_word_for_word():
    """Three places send or apply this (the frame the service writes, the edge's
    header, the private preview). The design states it once; so does this."""
    assert bi.ENTRY_SANDBOX == ("allow-same-origin allow-popups "
                                "allow-popups-to-escape-sandbox")
    assert bi.ENTRY_CSP == (
        "default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; "
        "img-src data:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'self'; "
        "sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox")


def test_an_image_can_only_be_one_the_document_carries():
    """``img-src data:`` and nothing else. The cleaner takes out every ``img``
    and every CSS ``url()`` that leaves the document, so ``'self'`` would buy an
    entry nothing - and on the private app's origin, where the preview is
    served, it is the one source that would let a miss in the cleaner send a
    GET carrying the session cookie to a route of the app's own."""
    directives = dict(d.strip().split(" ", 1) for d in bi.ENTRY_CSP.split(";"))
    assert directives["img-src"] == "data:"
    assert "'self'" not in directives["img-src"]


def test_the_policy_loads_nothing_from_another_origin_and_runs_nothing():
    directives = dict(d.strip().split(" ", 1) for d in bi.ENTRY_CSP.split(";"))
    assert directives["default-src"] == "'none'"
    assert "script-src" not in directives               # so default-src 'none' governs it
    for name, value in directives.items():
        assert "http" not in value and "*" not in value, name
        assert "unsafe-eval" not in value, name
    assert directives["style-src"] == "'unsafe-inline'"
    # A header value: one line, and a quote would end the attribute Caddy and
    # the private preview write it into.
    assert "\n" not in bi.ENTRY_CSP and '"' not in bi.ENTRY_CSP
    assert '"' not in bi.ENTRY_SANDBOX


# ── the config ──────────────────────────────────────────────────────────────

def test_the_limits_have_the_shipped_values():
    assert bi.limits() == {
        "max_html_kb": 512, "max_drafts": 20, "submissions_per_hour": 12,
        "title_chars": 140, "summary_chars": 300, "max_tags": 6, "tag_chars": 24,
        "slug_chars": 80, "max_wait_sec": 120, "answer_keep_sec": 120,
        "clean_sec": 20, "clean_mem_mb": 512}


def test_the_clean_time_limit_stays_under_the_wait_limit():
    """An overrun must be answered before the request itself expires."""
    assert bi.DEFAULTS["limits"]["clean_sec"] < bi.DEFAULTS["limits"]["max_wait_sec"]


# Typed out here, so a change to the shipped string is a change someone made
# twice on purpose.
SHIPPED_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


def test_the_fonts_and_site_sections_have_the_shipped_values():
    assert bi.fonts() == {"enabled": True, "subsets": ["latin", "latin-ext"],
                          "max_links": 4, "max_css_kb": 256,
                          "max_files": 24, "max_file_kb": 400, "max_total_mb": 12,
                          "max_rules": 96, "timeout_sec": 10, "total_sec": 30,
                          "user_agent": SHIPPED_USER_AGENT}
    assert bi.site() == {"enabled": True, "republish_min": 30}


def test_the_bytes_a_typeface_copy_may_hold_are_bounded():
    """Every file of one entry is held in memory at once, until the store
    writes them. ``max_files`` times ``max_file_kb`` was all that bounded it,
    and at those two settings' old ceilings (200 files of 4 MB) that was 800 MB.

    So: lower ceilings on both, and a total of its own. At the ceilings the
    three now agree - 64 files of 1 MB is 64 MB, and 64 MB is the most the
    total may be set to - and as shipped the total is above what the other two
    allow, so it changes nothing until one of them is raised."""
    assert bi.BOUNDS[("fonts", "max_files")] == (1, 64)
    assert bi.BOUNDS[("fonts", "max_file_kb")] == (16, 1024)
    assert bi.BOUNDS[("fonts", "max_total_mb")] == (1, 64)
    shipped = bi.DEFAULTS["fonts"]
    assert (shipped["max_files"], shipped["max_file_kb"], shipped["max_total_mb"]) == (24, 400, 12)
    assert shipped["max_files"] * shipped["max_file_kb"] <= shipped["max_total_mb"] * 1024
    most_files, largest_kb = bi.BOUNDS[("fonts", "max_files")][1], bi.BOUNDS[("fonts", "max_file_kb")][1]
    assert most_files * largest_kb == bi.BOUNDS[("fonts", "max_total_mb")][1] * 1024


@pytest.mark.parametrize("key, value", [
    ("max_files", 65), ("max_files", 200), ("max_files", 0),
    ("max_file_kb", 1025), ("max_file_kb", 4096), ("max_file_kb", 15), ("max_file_kb", 1),
    ("max_total_mb", 65), ("max_total_mb", 0), ("max_total_mb", 800),
])
def test_a_typeface_setting_outside_its_new_bounds_reads_as_the_shipped_one(monkeypatch, key, value):
    """A ``config/local/blog.toml`` written when the ceilings were higher may
    still hold 200 files or 4,096 KB. It reads as the shipped value - not as
    the new ceiling, which would be a number nobody chose."""
    shipped = bi.fonts()
    _cfg(monkeypatch, fonts={key: value})
    assert bi.fonts() == shipped
    assert bi.fonts({key: value}) == shipped


@pytest.mark.parametrize("key, value", [
    ("max_files", 1), ("max_files", 64), ("max_file_kb", 16), ("max_file_kb", 1024),
    ("max_total_mb", 1), ("max_total_mb", 64),
])
def test_a_typeface_setting_at_either_new_bound_is_read(monkeypatch, key, value):
    _cfg(monkeypatch, fonts={key: value})
    assert bi.fonts()[key] == value


def test_the_rules_written_into_an_entry_are_bounded():
    """``max_files`` bounds files, not rules: many rules can name one file.
    The rules are pasted into the entry AFTER its own size limit was applied,
    so their number needs a limit of its own."""
    assert bi.DEFAULTS["fonts"]["max_rules"] == 96
    assert bi.BOUNDS[("fonts", "max_rules")] == (1, 1000)


GOOD_USER_AGENTS = [
    SHIPPED_USER_AGENT,
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/160.0.0.0 Safari/537.36",
    "x" * 20, "x" * 300,
]
DEL = chr(0x7F)
NO_BREAK_SPACE = chr(0xA0)
E_ACUTE = chr(0xE9)
for _ch in (DEL, NO_BREAK_SPACE, E_ACUTE):
    assert len(_ch) == 1
BAD_USER_AGENTS = [
    "", "   ", "x" * 19, "x" * 301, None, 7, True, ["Mozilla/5.0"],
    "Mozilla/5.0 (Windows NT 10.0)\r\nX-Injected: 1",       # a header of its own
    "Mozilla/5.0 (Windows NT 10.0)\nChrome", "Mozilla/5.0 (Windows NT 10.0)\tChrome",
    "Mozilla/5.0 (Windows NT 10.0)" + chr(0) + "Chrome",
    "Mozilla/5.0 (Windows NT 10.0)" + DEL + "Chrome",
    "Mozilla/5.0 (Windows NT 10.0) Caf" + E_ACUTE,          # a header is ASCII
    "Mozilla/5.0 (Windows NT 10.0)" + NO_BREAK_SPACE + "Chrome",
    " Mozilla/5.0 (Windows NT 10.0) Chrome",                # requests refuses a leading space
    "Mozilla/5.0 (Windows NT 10.0) Chrome ",
]


@pytest.mark.parametrize("value", GOOD_USER_AGENTS)
def test_a_usable_user_agent_is_read(monkeypatch, value):
    _cfg(monkeypatch, fonts={"user_agent": value})
    assert bi.fonts()["user_agent"] == value


@pytest.mark.parametrize("value", BAD_USER_AGENTS, ids=repr)
def test_a_user_agent_that_could_not_be_a_header_reads_as_the_shipped_one(monkeypatch, value):
    """It is sent as a request header: printable ASCII only, so no line break
    can start a second header, and 20 to 300 characters."""
    _cfg(monkeypatch, fonts={"user_agent": value})
    assert bi.fonts()["user_agent"] == SHIPPED_USER_AGENT
    assert bi.USER_AGENT_CHARS == (20, 300)


def test_a_caller_and_the_file_are_held_to_one_rule(monkeypatch):
    """``fonts(over)`` lays a caller's table over the file's. It is the SAME
    reading, key by key, so a value is usable from one exactly when it is from
    the other - a float that the file may hold is a float a caller may pass."""
    cases = [("timeout_sec", 59.5, 59), ("timeout_sec", 60, 60), ("timeout_sec", 61, None),
             ("timeout_sec", 0.5, None), ("timeout_sec", "9", None), ("timeout_sec", True, None),
             ("timeout_sec", float("nan"), None), ("timeout_sec", float("inf"), None),
             ("max_rules", 1, 1), ("max_rules", 1000, 1000), ("max_rules", 1001, None),
             ("max_files", 3.0, 3), ("enabled", False, False), ("enabled", 0, None),
             ("subsets", ["greek", "greek", "../x", 7], ["greek"]),
             ("subsets", ("greek", "fallback"), ["greek", "fallback"]),
             ("subsets", "greek", None), ("subsets", [], None), ("subsets", ["Klingon!"], None),
             ("user_agent", "y" * 40, "y" * 40), ("user_agent", "short", None)]
    shipped = bi.fonts()
    for key, raw, expected in cases:
        expected = shipped[key] if expected is None else expected
        assert bi.fonts({key: raw})[key] == expected, (key, raw)
        monkeypatch.setattr(bi, "load", lambda k=key, r=raw: {"fonts": {k: r}})
        assert bi.fonts()[key] == expected, (key, raw)
        monkeypatch.undo()
        other = {k: v for k, v in bi.fonts({key: raw}).items() if k != key}
        assert other == {k: v for k, v in shipped.items() if k != key}, key


def test_what_a_caller_leaves_out_or_gets_wrong_is_the_files_not_the_built_in(monkeypatch):
    """Laid over the FILE's settings: the operator's limits still hold for a
    caller who names one key, or names it badly."""
    _cfg(monkeypatch, fonts={"max_files": 5, "timeout_sec": 3, "subsets": ["greek"]})
    assert bi.fonts({"max_links": 2}) == {**bi.fonts(), "max_links": 2}
    assert bi.fonts({"max_files": 10 ** 9, "subsets": [], "timeout_sec": None}) == bi.fonts()
    assert bi.fonts()["max_files"] == 5 and bi.fonts()["subsets"] == ["greek"]
    for not_a_table in (None, 7, "x", [], [("max_files", 1)]):
        assert bi.fonts(not_a_table) == bi.fonts()


def test_a_typeface_copy_is_bounded_in_every_direction():
    """What one entry can make this box ask Google for: how many stylesheets,
    how large each, how many files, how large each, how long each request may
    take and how long all of them together. The cleaner caps none of it - it
    hands over every link it found - so each needs a number here, with bounds."""
    for key in ("max_links", "max_css_kb", "max_files", "max_file_kb", "max_total_mb",
                "max_rules", "timeout_sec", "total_sec"):
        assert ("fonts", key) in bi.BOUNDS, key
    assert bi.BOUNDS[("fonts", "max_links")] == (1, 16)
    assert bi.BOUNDS[("fonts", "max_css_kb")] == (16, 2048)
    assert bi.BOUNDS[("fonts", "total_sec")] == (1, 600)
    # All of an entry's requests together get less than a draft from Claude
    # Chat may wait: one slow copy must not expire the request behind it.
    assert bi.DEFAULTS["fonts"]["total_sec"] < bi.DEFAULTS["limits"]["max_wait_sec"]


def test_the_shipped_file_matches_the_defaults():
    """The TOML overrides defaults; shipped equal, so a missing file changes
    nothing. Whole-file equality, so a key added to one and not the other
    fails here."""
    import tomllib
    shipped = tomllib.loads((REPO / "config" / "blog.toml").read_text(encoding="utf-8"))
    assert shipped == bi.DEFAULTS


def test_every_number_has_bounds_and_ships_inside_them():
    """BOUNDS is what the validators enforce and what the Settings catalogue
    must offer. A number without bounds would be accepted at any size."""
    numbers = {(section, key) for section, values in bi.DEFAULTS.items()
               for key, value in values.items()
               if isinstance(value, (int, float)) and not isinstance(value, bool)}
    assert set(bi.BOUNDS) == numbers
    for (section, key), (low, high) in bi.BOUNDS.items():
        assert low <= bi.DEFAULTS[section][key] <= high, (section, key)


def test_a_value_inside_its_bounds_is_read(monkeypatch):
    _cfg(monkeypatch, limits={"max_drafts": 3, "max_wait_sec": 45.0},
         fonts={"enabled": False, "subsets": ["cyrillic", "latin", "latin"],
                "timeout_sec": 4, "max_links": 2, "max_css_kb": 64, "total_sec": 12,
                "max_rules": 40, "user_agent": "Mozilla/5.0 (a newer browser)",
                "max_total_mb": 30},
         site={"enabled": False, "republish_min": 5})
    assert bi.limits()["max_drafts"] == 3
    assert bi.limits()["max_wait_sec"] == 45 and isinstance(bi.limits()["max_wait_sec"], int)
    assert bi.limits()["title_chars"] == 140            # an unnamed key keeps its default
    assert bi.fonts() == {"enabled": False, "subsets": ["cyrillic", "latin"],
                          "max_links": 2, "max_css_kb": 64,
                          "max_files": 24, "max_file_kb": 400, "max_total_mb": 30,
                          "max_rules": 40,
                          "timeout_sec": 4, "total_sec": 12,
                          "user_agent": "Mozilla/5.0 (a newer browser)"}
    assert bi.site() == {"enabled": False, "republish_min": 5}


BAD_TOML = """
[site]
enabled = "yes"
republish_min = 0

[limits]
max_html_kb = 99999999
max_drafts = -1
submissions_per_hour = "many"
title_chars = true
summary_chars = nan
max_tags = inf
tag_chars = 0
slug_chars = 4
max_wait_sec = [120]
answer_keep_sec = 0.2

[fonts]
enabled = 1
subsets = "latin"
max_links = 17
max_css_kb = 8
max_files = 0
max_file_kb = -400
max_total_mb = 800
max_rules = 1001
timeout_sec = 100000
total_sec = "30"
user_agent = "curl"
"""


def test_bad_config_values_read_as_the_shipped_ones(tmp_path, monkeypatch):
    """Through the REAL loader and its merge, from a file on disk: every key
    carries a value of the wrong type or outside its bounds."""
    shipped = (bi.limits(), bi.fonts(), bi.site())
    path = tmp_path / "blog.toml"
    path.write_text(BAD_TOML, encoding="utf-8")
    load, _reset = toml_loader(path, bi.DEFAULTS, label="blog.toml")
    monkeypatch.setattr(bi, "load", load)
    assert load()["limits"]["max_drafts"] == -1          # the bad file IS what is read
    assert (bi.limits(), bi.fonts(), bi.site()) == shipped


@pytest.mark.parametrize("text", [
    "this is not toml [",
    "limits = 5\nfonts = 'x'\nsite = [1, 2]\n",          # a table replaced by a scalar
    "",
])
def test_a_malformed_file_reads_as_the_shipped_values(tmp_path, monkeypatch, text):
    shipped = (bi.limits(), bi.fonts(), bi.site())
    path = tmp_path / "blog.toml"
    path.write_text(text, encoding="utf-8")
    load, _reset = toml_loader(path, bi.DEFAULTS, label="blog.toml")
    monkeypatch.setattr(bi, "load", load)
    assert (bi.limits(), bi.fonts(), bi.site()) == shipped


@pytest.mark.parametrize("subsets", [[], ["LATIN!"], [7, None], "latin", None,
                                     ["../x"], ["a" * 40]])
def test_a_subset_list_with_nothing_usable_is_the_shipped_list(monkeypatch, subsets):
    _cfg(monkeypatch, fonts={"subsets": subsets})
    assert bi.fonts()["subsets"] == ["latin", "latin-ext"]


def test_a_bad_subset_is_dropped_and_the_rest_kept(monkeypatch):
    """A subset name is matched against Google's stylesheet comments; it is
    never a path, but it is still held to the characters a name has."""
    _cfg(monkeypatch, fonts={"subsets": ["greek", "../x", 7, "Latin", "greek"]})
    assert bi.fonts()["subsets"] == ["greek"]


def test_the_loader_reset_is_named_as_every_other_config_module_names_it():
    """``reset_cache``, as shared.public_scan, service_limits and the rest call
    theirs: a test or a tool that resets "every config cache" finds it by name."""
    from shared import public_scan, service_limits
    assert callable(bi.reset_cache)
    assert callable(public_scan.reset_cache) and callable(service_limits.reset_cache)
    assert not hasattr(bi, "reset")
    bi.reset_cache()
    assert bi.limits()["max_drafts"] == 20          # and it still reads after one


def test_the_accessors_never_hand_out_the_cached_mapping():
    """``load()`` returns the loader's CACHED dict. A caller that edited what
    limits() or fonts() returned would otherwise edit everyone's config."""
    bi.limits()["max_drafts"] = 0
    bi.fonts()["subsets"].append("x")
    bi.site()["enabled"] = False
    assert bi.limits()["max_drafts"] == 20
    assert bi.fonts()["subsets"] == ["latin", "latin-ext"]
    assert bi.site()["enabled"] is True
    assert bi.DEFAULTS["fonts"]["subsets"] == ["latin", "latin-ext"]


# ── the store's layout, and the two readers everything shares ───────────────

WINDOWS_DEVICES = (["con", "prn", "aux", "nul"]
                   + [f"com{n}" for n in range(1, 10)] + [f"lpt{n}" for n in range(1, 10)])


def _sha(data) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def test_the_layout_names_are_the_ones_on_disk():
    """The service writes under these names; the private preview and the site
    writer read under them. One definition, so they cannot drift."""
    assert (bi.STAGING_DIR, bi.PUBLISHED_DIR, bi.FONTS_DIR) == ("staging", "published", "fonts")
    assert (bi.DOC_NAME, bi.NEXT_NAME) == ("entry.html", "entry.html.next")
    # The typeface folder's name is the reserved address, and for that reason.
    assert bi.FONTS_DIR in bi.RESERVED_SLUGS


@pytest.mark.parametrize("name", WINDOWS_DEVICES)
def test_a_windows_device_name_is_not_an_address(name):
    """``con`` and ``nul`` have the shape of an address, and on Windows they
    are not folders: opening one opens the console or discards what is
    written. Prod is Linux, where one would simply work - which is how an
    entry no Windows box can restore would get published."""
    assert bi.SLUG_RE.match(name), "the shape alone does not refuse it"
    assert name in bi.RESERVED_SLUGS
    assert bi.clean_slug(name) is None and bi.clean_slug(name.upper()) is None
    assert bi.existing_slug(name) is None and bi.existing_slug(f" {name} ") is None
    assert bi.clean_fields({"slug": name})["slug"] == ""
    assert bi.owner_command("unpublish", bi.new_id(), slug=name) is None


@pytest.mark.parametrize("name", WINDOWS_DEVICES)
def test_a_title_that_is_a_device_name_still_gets_an_address(name):
    for title in (name, name.upper(), name.title(), f"  {name}!  "):
        slug = bi.slugify(title)
        assert slug == f"{bi.FALLBACK_SLUG}-{name}", title
        assert bi.clean_slug(slug) == slug


@pytest.mark.parametrize("name", ["console", "nulls", "com10", "com0", "lpt", "lpt0",
                                  "con-artists", "aux-in", "a-con", "com", "prn1"])
def test_an_address_that_only_looks_like_a_device_is_fine(name):
    assert bi.clean_slug(name) == name and bi.slugify(name) == name


def test_a_typeface_is_named_by_its_content():
    data = b"wOF2 some bytes"
    name = bi.font_name_for(data)
    assert name == _sha(data)[:20] + ".woff2"
    assert bi.FONT_NAME_RE.match(name)
    assert bi.font_name_for(bytearray(data)) == name
    assert bi.font_name_for(b"") == _sha(b"")[:20] + ".woff2"
    assert bi.font_name_for(data + b"!") != name


def test_read_document_returns_the_document_the_digest_names(tmp_path):
    one, two = b"<p>one</p>", b"<p>two</p>"
    (tmp_path / bi.DOC_NAME).write_bytes(one)
    assert bi.read_document(tmp_path, _sha(one)) == one
    assert bi.read_document(str(tmp_path), _sha(one)) == one          # a str path too
    assert bi.read_document(tmp_path, _sha(two)) is None

    # A replacement whose rename has not happened yet: the digest picks it.
    (tmp_path / bi.NEXT_NAME).write_bytes(two)
    assert bi.read_document(tmp_path, _sha(two)) == two
    assert bi.read_document(tmp_path, _sha(one)) == one
    assert bi.read_document(tmp_path, _sha(b"neither")) is None

    # ...and with no entry.html at all.
    (tmp_path / bi.DOC_NAME).unlink()
    assert bi.read_document(tmp_path, _sha(two)) == two
    assert bi.read_document(tmp_path, _sha(one)) is None


def test_read_document_is_byte_for_byte(tmp_path):
    """No newline is translated and nothing is decoded on the way."""
    data = ("caf" + chr(0xE9) + " " + chr(0x1F600) + "\r\nline\rline\n").encode("utf-8") + bytes([0xFF, 0x00])
    (tmp_path / bi.DOC_NAME).write_bytes(data)
    assert bi.read_document(tmp_path, _sha(data)) == data


def test_read_document_never_raises(tmp_path):
    data = b"<p>kept</p>"
    digest = _sha(data)
    assert bi.read_document(tmp_path / "missing", digest) is None
    assert bi.read_document(tmp_path / "a" / "b" / "c", digest) is None
    assert bi.read_document(tmp_path, digest) is None                 # an empty folder

    # A directory where the file should be - for either name.
    (tmp_path / bi.DOC_NAME).mkdir()
    assert bi.read_document(tmp_path, digest) is None
    (tmp_path / bi.NEXT_NAME).write_bytes(data)
    assert bi.read_document(tmp_path, digest) == data, "the other name is still tried"
    (tmp_path / bi.NEXT_NAME).unlink()
    (tmp_path / bi.NEXT_NAME).mkdir()
    assert bi.read_document(tmp_path, digest) is None

    # The folder is a FILE.
    plain = tmp_path / "plain"
    plain.write_bytes(data)
    assert bi.read_document(plain, digest) is None
    for folder in (None, 7, b"bytes", ["x"], "", chr(0)):
        assert bi.read_document(folder, digest) is None, repr(folder)


@pytest.mark.parametrize("bad", ["", "abc", "0" * 63, "0" * 65, None, 7, b"0" * 64,
                                 ["0" * 64], "g" * 64, " " + "0" * 63, "0" * 64 + "\n"])
def test_read_document_takes_only_a_whole_lower_case_digest(tmp_path, bad):
    (tmp_path / bi.DOC_NAME).write_bytes(b"<p>x</p>")
    assert bi.read_document(tmp_path, bad) is None


def test_read_document_refuses_an_upper_case_digest(tmp_path):
    """One spelling of a digest. Upper-case is the same number and would also
    have to be the same row; refusing it means two rows cannot name one file
    in two spellings."""
    data = b"<p>x</p>"
    (tmp_path / bi.DOC_NAME).write_bytes(data)
    assert _sha(data) != _sha(data).upper()
    assert bi.read_document(tmp_path, _sha(data).upper()) is None
    assert bi.read_document(tmp_path, _sha(data)) == data


def test_read_font_returns_a_file_that_is_what_its_name_says(tmp_path):
    data = b"wOF2 the real thing"
    name = bi.font_name_for(data)
    (tmp_path / name).write_bytes(data)
    assert bi.read_font(tmp_path, name) == data
    assert bi.read_font(str(tmp_path), name) == data

    # Altered on disk: the name no longer describes it.
    (tmp_path / name).write_bytes(data + b"!")
    assert bi.read_font(tmp_path, name) is None
    (tmp_path / name).write_bytes(b"")
    assert bi.read_font(tmp_path, name) is None


@pytest.mark.parametrize("bad", ["", "fonts", "../x", "0" * 20, "0" * 20 + ".woff",
                                 "A" * 20 + ".woff2", "0" * 19 + ".woff2", "0" * 21 + ".woff2",
                                 "0" * 20 + ".woff2\n", "../" + "0" * 20 + ".woff2",
                                 "x/" + "0" * 20 + ".woff2", None, 7, b"0" * 20 + b".woff2"])
def test_read_font_opens_nothing_that_is_not_named_like_a_typeface(tmp_path, bad):
    """The name is checked BEFORE it is joined to a path: a file is planted
    where each bad name would lead, and it must not come back."""
    (tmp_path / "pool").mkdir()
    (tmp_path / "x").write_bytes(b"outside the pool")
    (tmp_path / "pool" / "fonts").write_bytes(b"not a typeface")
    assert bi.read_font(tmp_path / "pool", bad) is None


def test_read_font_never_raises(tmp_path):
    data = b"wOF2"
    name = bi.font_name_for(data)
    assert bi.read_font(tmp_path / "missing", name) is None
    assert bi.read_font(tmp_path, name) is None                       # not there
    (tmp_path / name).mkdir()                                         # a directory of that name
    assert bi.read_font(tmp_path, name) is None
    plain = tmp_path / "plain"
    plain.write_bytes(data)
    assert bi.read_font(plain, name) is None                          # the folder is a file
    for folder in (None, 7, b"bytes", ["x"], chr(0)):
        assert bi.read_font(folder, name) is None, repr(folder)


# ── Tier 1 may import it ────────────────────────────────────────────────────

# The project and third-party modules importing it may load. Stdlib is free;
# anything else - an engine, the bus, redis, requests, lxml, a service - fails
# here. tzdata (zoneinfo's data package on hosts without a system tz database,
# i.e. Windows; repo_paths reads the clock's zone at import) is allowed by
# PREFIX in the test below, which is what covers its submodules too.
EXPECTED = {"repo_paths", "shared", "shared.config_toml", "shared.blog_inbox"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.blog_inbox
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(m for m in new
                              if m.split(".")[0] not in sys.stdlib_module_names
                              # CPython generates this one per platform at build time, so it is
                              # not in stdlib_module_names: on Linux, zoneinfo pulls in
                              # _sysconfigdata__linux_x86_64-linux-gnu.
                              and not m.startswith("_sysconfigdata"))))
""" % REPO


def test_the_module_imports_nothing_but_stdlib_config_and_paths():
    """On the Tier-1 allow-list: no engine, no bus, no service. Run in a FRESH
    interpreter so a transitive import cannot hide behind a module an earlier
    test already loaded."""
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    stray = {m for m in new if m not in EXPECTED and m.split(".")[0] != "tzdata"}
    assert not stray, sorted(stray)
    assert {"shared.blog_inbox", "shared.config_toml", "repo_paths"} <= new
