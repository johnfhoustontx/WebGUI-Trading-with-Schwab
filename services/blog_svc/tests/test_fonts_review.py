"""What a review of ``fonts.py`` found unbounded, uncounted or unpinned.

``test_fonts.py`` is the module against its plan and a hostile corpus. This
file is what an adversarial review added afterwards, and each section is one
of its findings:

* the RULES written into an entry had no bound (``max_files`` bounds files,
  and many rules can name one file);
* a crash in the stylesheet reader was reported as a network failure and not
  counted;
* eleven validation rules could be loosened without a test failing;
* ``apply`` pasted rules into text that only looked like its slot;
* an icon font vanished without a word;
* the suite's own network guard was swallowed by the code it guards.

Every test goes through an injected ``fetch`` or a replaced ``requests.get``,
as in ``test_fonts.py``. Characters that are invisible or easy to mistype are
built from their code points, never written as an escape or a literal.
"""
import logging

import pytest
import tinycss2

from services import _degrade
from services.blog_svc import clean, fonts
from services.blog_svc.tests.conftest import NetworkReached
from services.blog_svc.tests.test_fonts import (A, B, C, FIXTURE, G, LINK, LINK_2, Requests,
                                                Response, Web, _fetch, audit, face, name_of,
                                                rule, sheet, woff2)
from shared import blog_inbox

LINE_SEPARATOR = chr(0x2028)
PARAGRAPH_SEPARATOR = chr(0x2029)
LAST_CODE_POINT = chr(0x10FFFF)
BACKSLASH = chr(0x5C)
DELETE = chr(0x7F)
for _ch in (LINE_SEPARATOR, PARAGRAPH_SEPARATOR, LAST_CODE_POINT, BACKSLASH, DELETE):
    assert len(_ch) == 1

SOME = "Some typefaces were not copied and are shown in a fallback font: "
NONE = "No typefaces were copied, so the entry is shown in its fallback fonts: "
LINKS = [f"https://fonts.googleapis.com/css2?family=F{n}" for n in range(4)]


def one_file_blocks(family, count) -> str:
    """``count`` blocks, no two alike, every one naming the SAME file: how the
    number of rules grows while the number of files stays at one."""
    assert count <= 2000
    return "".join(
        "/* latin */@font-face{font-family:'%s';font-weight:%d;font-stretch:%d%%;"
        "src:url(%sone.woff2) format('woff2')}\n" % (family, 1 + n % 1000, 100 + n // 1000, G)
        for n in range(count))


def weights(css) -> list:
    """The font-weight of every rule in ``css``, in order."""
    found = []
    for node in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        for item in tinycss2.parse_blocks_contents(node.content, True, True):
            if item.lower_name == "font-weight":
                found.append(tinycss2.serialize(item.value).strip())
    return found


# ── S1: the rules written into an entry are bounded ──────────────────────────

def test_the_rule_cap_holds_and_is_noted():
    web = Web({LINK: sheet(one_file_blocks("F", 10)), G + "one.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_rules": 4}))
    assert weights(result.css) == ["1", "2", "3", "4"]         # the first four, in order
    assert result.files == {name_of(A): A}
    assert result.note == SOME + "6 typeface rules were past the limit of 4."


def test_the_reviewers_five_thousand_rules_are_ninety_six():
    """The measurement that found it: four stylesheets, each inside the shipped
    256 KB, 5,088 distinct blocks between them and ONE file. It came back as
    5,088 rules and 2.3 MB of CSS with nothing in the note."""
    sheets = {link: sheet(one_file_blocks(f"F{n}", 1272)) for n, link in enumerate(LINKS)}
    shipped = blog_inbox.fonts()
    assert all(len(body) <= shipped["max_css_kb"] * 1024 for body in sheets.values())
    web = Web({**sheets, G + "one.woff2": A})
    result = audit(fonts.localize(LINKS, fetch=web))
    assert result.css.count("@font-face") == shipped["max_rules"] == 96
    assert result.files == {name_of(A): A}
    assert web.urls == LINKS + [G + "one.woff2"]
    assert result.note == SOME + "4992 typeface rules were past the limit of 96."
    assert len(result.css) < 16 * 1024


def test_a_rule_past_the_cap_costs_no_request():
    """Once the rules are full, a block's file is not even asked for."""
    files = {G + f"f{n}.woff2": woff2(f"f{n}") for n in range(6)}
    css = sheet(*[face("latin", url, weight=str(100 + 100 * n)) for n, url in enumerate(files)])
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_rules": 2}))
    assert web.urls == [LINK] + list(files)[:2]
    assert result.note == SOME + "4 typeface rules were past the limit of 2."


def test_a_rule_already_written_is_not_counted_against_the_cap():
    """Two stylesheets that ask for the same face are one rule, wherever the
    second mention falls."""
    first = sheet(face("latin", G + "one.woff2", weight="400"),
                  face("latin", G + "one.woff2", weight="500"))
    second = sheet(face("latin", G + "one.woff2", weight="400"),
                   face("latin", G + "one.woff2", weight="500"),
                   face("latin", G + "one.woff2", weight="600"))
    web = Web({LINK: first, LINK_2: second, G + "one.woff2": A})
    result = audit(fonts.localize([LINK, LINK_2], fetch=web, cfg={"max_rules": 2}))
    assert weights(result.css) == ["400", "500"]
    assert result.note == SOME + "1 typeface rule was past the limit of 2."


def test_a_rule_whose_file_failed_does_not_use_up_the_cap():
    files = {G + "f0.woff2": OSError("down"), G + "f1.woff2": B, G + "f2.woff2": C}
    css = sheet(*[face("latin", url, weight=str(100 + 100 * n)) for n, url in enumerate(files)])
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_rules": 2}))
    assert weights(result.css) == ["200", "300"]
    assert result.note == SOME + "1 file could not be fetched or was not a typeface."


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


def _largest_block(n) -> str:
    """A block that reads into the longest rule this module can write: every
    descriptor present and as long as its reader allows. ``n`` makes each one
    different without changing its length."""
    assert 1 <= n <= 999
    return face("latin", G + "one.woff2",
                family="'" + LAST_CODE_POINT * 100 + "'",          # each is written "\10ffff "
                style="oblique -89.999deg -89.999deg",
                weight=f"0{n:03d}.999 0999.999", stretch="0999.999% 0999.999%",
                display="fallback", unicode_range=", ".join(["U+100000-10FFFF"] * 128))


def test_the_size_of_the_rules_is_bounded_and_the_bound_is_exact():
    """``len(Fonts.css) <= max_rules * RULE_CHARS_CEILING + (max_rules - 1)``.

    The ceiling is worked out here from the shape of a rule, typed out, and
    must equal the module's own figure; then the largest rules there can be are
    built and come out at exactly that, so the inequality is not slack.

    What it comes to: 96 rules as shipped is under 300 KB at the very worst (a
    real rule is about a tenth of the ceiling); 1,000 rules, the limit's own
    ceiling, is 3 MB."""
    ceiling = (len("@font-face{")
               + len('font-family:""') + 100 * len(BACKSLASH + "10ffff ")
               + len(";font-style:oblique -89.999deg -89.999deg")
               + len(";font-weight:0999.999 0999.999")
               + len(";font-stretch:0999.999% 0999.999%")
               + len(";font-display:fallback")
               + len(';src:url(../fonts/' + "0" * 20 + '.woff2) format("woff2")')
               + len(";unicode-range:") + 128 * len("U+100000-10FFFF") + 127
               + len("}"))
    assert fonts.RULE_CHARS_CEILING == ceiling == 3075

    def bound(settings):
        return settings["max_rules"] * ceiling + settings["max_rules"] - 1

    web = Web({LINK: sheet(*[_largest_block(n) for n in range(1, 8)]), G + "one.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_rules": 5}))
    lines = result.css.split("\n")
    assert [len(line) for line in lines] == [ceiling] * 5
    assert len(result.css) == bound({"max_rules": 5})
    assert result.note == SOME + "2 typeface rules were past the limit of 5."

    shipped = blog_inbox.fonts()
    assert bound(shipped) == 96 * 3075 + 95 < 300 * 1024
    assert bound({"max_rules": blog_inbox.BOUNDS[("fonts", "max_rules")][1]}) < 3 * 1024 * 1024
    # ... and it holds for the input that found the problem, with room to spare.
    sheets = {link: sheet(one_file_blocks(f"F{n}", 1272)) for n, link in enumerate(LINKS)}
    stress = fonts.localize(LINKS, fetch=Web({**sheets, G + "one.woff2": A}))
    assert len(stress.css) <= bound(shipped)


# ── S2: three ways a stylesheet fails, told apart ────────────────────────────

# Python refuses to turn more than 4,300 digits into an int, and tinycss2 does
# exactly that with a number token: ValueError, from inside the tokenizer.
CRASHES_THE_READER = ".x{width:" + "9" * 4301 + "px}"


def test_a_stylesheet_that_crashes_the_reader_is_counted_and_costs_only_itself():
    """It used to read "1 stylesheet could not be fetched" - which it had been
    - with nothing counted, so a fault in this service looked like Google
    being away."""
    web = Web({LINK: sheet(face("latin", G + "a.woff2"), CRASHES_THE_READER),
               LINK_2: sheet(face("latin", G + "b.woff2", weight="700")),
               G + "a.woff2": A, G + "b.woff2": B})
    _degrade.reset()
    result = audit(fonts.localize([LINK, LINK_2], fetch=web))
    assert result.css == rule(B, weight="700")
    assert web.urls == [LINK, LINK_2, G + "b.woff2"]
    assert result.note == SOME + "1 stylesheet made the reader fail."
    assert _degrade.counts() == {"blog.fonts": 1}
    _degrade.reset()


@pytest.mark.parametrize("answer, said, faults", [
    (OSError("refused"), "1 stylesheet could not be fetched", 0),
    (fonts.FetchError("HTTP 503"), "1 stylesheet could not be fetched", 0),
    (None, "1 stylesheet could not be fetched", 0),                 # not bytes at all
    (b"\xff\xfe\x00 not utf-8", "1 stylesheet was not readable text", 0),
    (sheet(CRASHES_THE_READER), "1 stylesheet made the reader fail", 1),
    (b"<!doctype html><html><body>Error 400</body></html>", "1 stylesheet held no typefaces", 0),
    (b"", "1 stylesheet held no typefaces", 0),
], ids=["network", "status", "not bytes", "not text", "reader", "html", "empty"])
def test_each_way_a_stylesheet_fails_has_its_own_words(answer, said, faults):
    """Not fetched (the network's), not text (the other end's bytes), the
    reader failed (OURS, and the only one counted), nothing in it."""
    _degrade.reset()
    result = audit(fonts.localize([LINK], fetch=Web({LINK: answer})))
    assert result == fonts.Fonts("", {}, NONE + said + ".")
    assert _degrade.counts() == ({"blog.fonts": faults} if faults else {})
    _degrade.reset()


def test_a_reader_that_runs_out_of_stack_is_a_reader_failure(monkeypatch):
    def too_deep(text, subsets):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(fonts, "_faces", too_deep)
    _degrade.reset()
    result = audit(fonts.localize([LINK], fetch=Web({LINK: sheet(face("latin", G + "a.woff2"))})))
    assert result == fonts.Fonts("", {}, NONE + "1 stylesheet made the reader fail.")
    assert _degrade.counts() == {"blog.fonts": 1}
    _degrade.reset()


# ── N4: what "never raises" means ────────────────────────────────────────────

@pytest.mark.parametrize("stop", [KeyboardInterrupt, SystemExit, MemoryError, GeneratorExit])
def test_what_must_stop_the_process_is_never_swallowed(stop, monkeypatch):
    """``localize`` never raises an ordinary exception. A shutdown
    (``KeyboardInterrupt``, ``SystemExit``) and a process out of memory are
    not ordinary: swallowed, the service would carry on as if it had been
    told nothing. Each gets out, from wherever it starts."""
    good = sheet(face("latin", G + "a.woff2"))
    with pytest.raises(stop):                       # while fetching a stylesheet
        fonts.localize([LINK], fetch=Web({LINK: stop()}))
    with pytest.raises(stop):                       # while fetching a file
        fonts.localize([LINK], fetch=Web({LINK: good, G + "a.woff2": stop()}))

    def stops(text, subsets):
        raise stop()
    monkeypatch.setattr(fonts, "_faces", stops)
    with pytest.raises(stop):                       # while reading
        fonts.localize([LINK], fetch=Web({LINK: good}))


@pytest.mark.parametrize("stop", [KeyboardInterrupt, SystemExit, MemoryError])
def test_http_fetch_gives_the_connection_back_even_to_a_shutdown(monkeypatch, stop):
    response = Response(chunks=(b"half",), error=stop())
    fake = Requests(response)
    monkeypatch.setattr(fonts.requests, "get", fake.get)
    with pytest.raises(stop):
        fonts.http_fetch(G + "a.woff2", headers={}, timeout=7, max_bytes=1000)
    assert response.closed


def test_a_test_that_forgets_its_fetch_fails_loudly():
    """The suite's network guard, against the code it guards. ``localize``
    catches ``Exception`` around every fetch, so a guard that raised one was
    turned into "1 stylesheet could not be fetched" and the forgetful test
    passed. The guard raises a ``BaseException`` for that reason."""
    assert issubclass(NetworkReached, BaseException)
    assert not issubclass(NetworkReached, Exception)
    with pytest.raises(NetworkReached):
        fonts.localize([LINK])                      # no fetch=: the real one
    with pytest.raises(NetworkReached):
        fonts.http_fetch(G + "a.woff2", headers={}, timeout=1, max_bytes=10)


# ── N5: a typeface that is not offered in the copied character sets ──────────

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


# ── S3: the rules a mutation could loosen unnoticed ──────────────────────────

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


def test_a_family_name_may_be_a_hundred_characters_and_no_more():
    assert _kept(face("latin", G + "x.woff2", family="'" + "N" * 100 + "'", weight="700"))
    assert not _kept(face("latin", G + "x.woff2", family="'" + "N" * 101 + "'", weight="700"))


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


def _header_only(length) -> bytes:
    """A file of ``length`` bytes whose header says it is ``length`` bytes
    long and has tables: right in every way but, perhaps, having a whole
    header."""
    whole = woff2("header", size=0)
    assert len(whole) == 48
    return (whole[:8] + length.to_bytes(4, "big") + whole[12:])[:length]


@pytest.mark.parametrize("length, kept", [(48, True), (47, False), (16, False), (14, False)])
def test_a_typeface_file_has_a_whole_header(length, kept):
    """48 bytes of header. A shorter file that declares its own short length
    passes every other check."""
    data = _header_only(length)
    assert len(data) == length and int.from_bytes(data[8:12], "big") == length
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": data})
    result = audit(fonts.localize([LINK], fetch=web))
    assert (result.files == {name_of(data): data}) is kept
    assert result.note == ("" if kept else
                           NONE + "1 file could not be fetched or was not a typeface.")


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


def test_a_backslash_in_the_path_of_a_file_address_is_refused(monkeypatch):
    """In the PATH, not only in the host: a browser reads a backslash as a
    slash, and nothing else does. Refused where the stylesheet is read, and
    again where the request is made."""
    odd = G + "a" + BACKSLASH + "b.woff2"
    # In a quoted url() a backslash is written twice to mean itself.
    quoted = 'url("' + G + "a" + BACKSLASH * 2 + 'b.woff2") format("woff2")'
    web = Web({LINK: sheet(face("latin", None, src=quoted)), odd: A})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == "" and web.urls == [LINK]
    assert result.note == NONE + "1 typeface rule was not usable."
    for address in (odd, G + "a" + DELETE + "b.woff2", G + "a" + BACKSLASH,
                    "https://fonts.googleapis.com/css2?family=a" + BACKSLASH + "b"):
        fake = Requests(Response())
        monkeypatch.setattr(fonts.requests, "get", fake.get)
        with pytest.raises(fonts.FetchError):
            fonts.http_fetch(address, headers={}, timeout=7, max_bytes=1000)
        assert fake.calls == []


@pytest.mark.parametrize("status", [True, "200", 200.0, 200 + 0j, b"200", [200]], ids=repr)
def test_a_status_that_is_not_the_number_200_is_refused(monkeypatch, status):
    """``True`` is an int and ``200.0 == 200``: only the plain integer 200 is
    the status meant."""
    response = Response(status=status)
    _fake, outcome = _fetch(monkeypatch, response)
    assert isinstance(outcome, fonts.FetchError)
    # Said as what it is. "HTTP True" would be a status nobody sent, in a log.
    assert str(outcome) == "no HTTP status"
    assert response.read == 0 and response.closed
    _fake, outcome = _fetch(monkeypatch, Response(status=503))
    assert str(outcome) == "HTTP 503"


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


def test_the_log_names_what_failed_and_never_what_it_said(caplog, monkeypatch):
    """The note is pinned elsewhere; this is the LOG. An exception's text is
    whatever the other end, or a library quoting the other end, put in it - an
    address with its query, a piece of a response. The log gets the type's
    name, and for a fault of our own the place it happened. The one text let
    through is a ``FetchError``'s, which is written in ``fonts.py`` itself."""
    planted = "PLANTED-9f3c https://evil.test/?q=secret <script>"
    good = sheet(face("latin", G + "a.woff2"), face("latin", G + "b.woff2", weight="700"))
    web = Web({LINKS[0]: OSError(planted), LINKS[1]: RuntimeError(planted),
               LINKS[2]: fonts.FetchError("HTTP 503"), LINKS[3]: good,
               G + "a.woff2": ValueError(planted), G + "b.woff2": "not bytes"})
    with caplog.at_level(logging.DEBUG):
        audit(fonts.localize(LINKS, fetch=web))
        # ... a reader that fails, and a fault outside any one link or file.
        def reader_fails(text, subsets):
            raise IndexError(planted)
        monkeypatch.setattr(fonts, "_faces", reader_fails)
        audit(fonts.localize([LINK], fetch=Web({LINK: good})))
        monkeypatch.undo()

        def broken_clock():
            raise KeyError(planted)
        audit(fonts.localize([LINK], fetch=Web({LINK: good}), clock=broken_clock))
        fonts.apply(clean.clean("<p>x</p>").html, fonts.Fonts(planted, {}, ""))
    logged = caplog.text
    assert len(caplog.records) >= 7
    for needle in ("PLANTED", "9f3c", "evil", "secret", "<script", "?q="):
        assert needle not in logged, needle
    for said in ("OSError", "RuntimeError", "ValueError", "IndexError", "KeyError", "HTTP 503"):
        assert said in logged, said
    _degrade.reset()


# ── S5: who the service says it is ───────────────────────────────────────────

def test_the_browser_named_to_google_is_a_setting(monkeypatch):
    """It ages, and the cure used to be a code change and a promote."""
    assert fonts.USER_AGENT == blog_inbox.DEFAULTS["fonts"]["user_agent"]
    newer = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/160.0.0.0 Safari/537.36")
    pages = {LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A}

    web = Web(pages)
    audit(fonts.localize([LINK], fetch=web, cfg={"user_agent": newer}))
    assert [call["headers"] for call in web.calls] == [{"User-Agent": newer}] * 2

    web = Web(pages)                                # ... and from the file, with no cfg
    monkeypatch.setattr(blog_inbox, "load", lambda: {"fonts": {"user_agent": newer}})
    audit(fonts.localize([LINK], fetch=web))
    assert [call["headers"] for call in web.calls] == [{"User-Agent": newer}] * 2
    monkeypatch.undo()

    web = Web(pages)                                # one that could not be a header
    audit(fonts.localize([LINK], fetch=web, cfg={"user_agent": newer + chr(0x0A) + "X: 1"}))
    assert [call["headers"] for call in web.calls] == [{"User-Agent": fonts.USER_AGENT}] * 2


# ── N8: a caller's settings and the file's are read by one rule ──────────────

def test_a_callers_settings_are_read_exactly_as_the_files_are(monkeypatch):
    """``cfg={"timeout_sec": 9.5}`` used to read as the shipped 10 while the
    same value in the file read as 9."""
    pages = {LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A}
    web = Web(pages)
    audit(fonts.localize([LINK], fetch=web, cfg={"timeout_sec": 9.5}))
    assert [call["timeout"] for call in web.calls] == [9, 9]

    asked = []

    def reads(over=None, _real=blog_inbox.fonts):
        asked.append(over)
        return _real(over)
    monkeypatch.setattr(blog_inbox, "fonts", reads)
    cfg = {"timeout_sec": 9.5}
    audit(fonts.localize([LINK], fetch=Web(pages), cfg=cfg))
    assert asked == [cfg]                           # the one reading, handed the caller's table


# ── N3: apply fills its slot and nothing that looks like it ──────────────────

MARK = clean.FONT_CSS_MARK
SLOT = "<style>" + MARK + "</style>"
HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"><title>t</title>')
REST = "</head><body><p>x</p></body></html>"

NOT_THE_SLOT = [
    ("no slot", HEAD + REST),
    ("an attribute in the body", HEAD + '</head><body><p title="' + SLOT + '">x</p></body></html>'),
    ("an attribute in the head",
     HEAD.replace("<title>t</title>", '<meta name="x" content="' + SLOT + '"><title>t</title>')
     + REST),
    ("an attribute of html", HEAD.replace('lang="en"', 'lang="' + SLOT + '"') + REST),
    ("the title", HEAD.replace("<title>t</title>", "<title>" + SLOT + "</title>") + REST),
    # The title ends at its FIRST "</title>". Read as "anything up to a
    # </title>", the head below stretches to the one inside the attribute, and
    # the slot text after it - inside the attribute - passes for the slot.
    ("after a second end of title, in an attribute",
     HEAD + '</head><body><p title="</title>' + SLOT + '">x</p></body></html>'),
    # A real slot, behind a head the cleaner did not write: it escapes "<" and
    # ">" in an attribute, so this document is somebody else's.
    ("behind an attribute the cleaner would have escaped",
     HEAD.replace('lang="en"', 'lang="<b>"') + SLOT + REST),
    ("behind a title the cleaner would have escaped",
     HEAD.replace("<title>t</title>", "<title>a <b> c</title>") + SLOT + REST),
    ("a comment in the head", HEAD + "<!-- " + SLOT + " -->" + REST),
    ("a comment around the head", "<!-- " + HEAD + SLOT + " -->" + REST),
    ("a textarea", HEAD + "</head><body><textarea>" + SLOT + "</textarea></body></html>"),
    ("the body", HEAD + "</head><body>" + SLOT + "</body></html>"),
    ("after another stylesheet", HEAD + "<style>p{}</style>" + SLOT + REST),
    ("after the head has closed", HEAD + "</head>" + SLOT + "<body></body></html>"),
    ("two slots", HEAD + SLOT + SLOT + REST),
    ("a slot and a stray mark", HEAD + SLOT + "</head><body><p>" + MARK + "</p></body></html>"),
    ("a slot and one in an attribute",
     HEAD + SLOT + '</head><body><p title="' + SLOT + '">x</p></body></html>'),
    ("not at the very start", " " + HEAD + SLOT + REST),
    ("another doctype", HEAD.replace("<!doctype html>", "<!DOCTYPE html>") + SLOT + REST),
    ("a style with an attribute", HEAD + "<style media=print>" + MARK + "</style>" + REST),
    ("a style with more in it", HEAD + "<style>p{}" + MARK + "</style>" + REST),
]


@pytest.mark.parametrize("document", [doc for _name, doc in NOT_THE_SLOT],
                         ids=[name for name, _doc in NOT_THE_SLOT])
def test_apply_leaves_alone_whatever_is_not_the_cleaners_own_slot(document):
    """The slot is where ``clean`` puts it: the document opens with the
    cleaner's own head, and the marked ``<style>`` comes straight after the
    title. Text that only LOOKS like the slot - in an attribute, a comment, a
    textarea, the body - is somewhere a paste would be read as something
    else. None of these can come out of ``clean``; ``apply`` says it returns
    such a document unchanged, and it does."""
    result = fonts.Fonts(rule(A), {name_of(A): A}, "")
    assert fonts.apply(document, result) == document


def test_every_document_the_cleaner_writes_is_filled():
    """The other half: the check must never be so strict that a real cleaned
    document comes back unfilled, which would look exactly like "this entry
    asked for no typefaces"."""
    result = fonts.Fonts(rule(A), {name_of(A): A}, "")
    sources = [
        FIXTURE.read_text(encoding="utf-8"),
        "<p>a fragment</p>",
        '<html lang="fr" class="dark wide" dir="rtl" data-theme="x&quot;y" style="--a:1">'
        '<head><title>a &lt; b &amp; "c" > d \'e\'</title></head>'
        '<body class="b" data-x="<style>" style="margin:0"><h1>T</h1><p>x</p></body></html>',
        "<title>" + MARK + "</title><p>" + SLOT + "</p>",
        "<title>line one\nline two</title><p>x</p>",
        "",                                         # refused: the empty shell is filled too
    ]
    for source in sources:
        cleaned = clean.clean(source)
        assert cleaned.html.count(SLOT) == 1 and cleaned.html.count(MARK) == 1
        page = fonts.apply(cleaned.html, result)
        assert page == cleaned.html.replace(SLOT, "<style>" + rule(A) + "</style>"), source
        assert page != cleaned.html and MARK not in page
        assert page.index("<style>" + rule(A)) < page.index("</head>")
