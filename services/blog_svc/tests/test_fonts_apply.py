"""Filling an entry's typeface slot: ``apply``, and the whole path into it.

The slot is recognised by ``clean.font_slot`` and by nothing in ``fonts.py``.
What is pinned here is that every document the cleaner writes IS filled, that
nothing else is, and that a document left unfilled while there were rules to
put in it is never silent.
"""
import pytest

from services import _degrade
from services.blog_svc import clean, fonts
from services.blog_svc.tests._fonts_kit import (A, APPLY, FIXTURE, G, HEAD, MARK, REST, SLOT, Web,
                                                audit, face, name_of, rule, sheet, some_fonts)


def test_the_example_shaped_entry_end_to_end():
    """clean -> localize -> apply, on the fixture: the one link the entry
    carries is the one followed, and the rules land in the marked <style>."""
    cleaned = clean.clean(FIXTURE.read_text(encoding="utf-8"))
    (link,) = cleaned.font_links
    web = Web({link: sheet(face("latin", G + "sg.woff2", family="'Schibsted Grotesk'",
                                weight="500")),
               G + "sg.woff2": A})
    result = audit(fonts.localize(cleaned.font_links, fetch=web))
    assert result.css == rule(A, family="Schibsted Grotesk", weight="500")
    page = fonts.apply(cleaned.html, result)
    assert f"<title>{cleaned.title}</title><style>{result.css}</style>" in page
    assert clean.FONT_CSS_MARK not in page and "fonts.googleapis.com" not in page
    assert len(page) == len(cleaned.html) - len(clean.FONT_CSS_MARK) + len(result.css)


def test_apply_fills_the_marked_style_and_nothing_else():
    cleaned = clean.clean("<html><head><style>p{color:red}</style></head>"
                          "<body><h1>T</h1><p>the mark is " + clean.FONT_CSS_MARK
                          + " in words</p></body></html>")
    result = some_fonts()
    page = fonts.apply(cleaned.html, result)
    before, mark, after = cleaned.html.partition(clean.FONT_CSS_MARK)
    assert mark and page == before + result.css + after
    assert page.count(result.css) == 1 and "p{color:red}" in page
    # A second time there is nothing left to fill: unchanged, and SAID - rules
    # with nowhere to go are never dropped quietly.
    _degrade.reset()
    assert fonts.apply(page, result) == page
    assert _degrade.counts() == {APPLY: 1}
    _degrade.reset()


def test_apply_with_no_typefaces_leaves_an_empty_style():
    cleaned = clean.clean("<p>x</p>")
    page = fonts.apply(cleaned.html, fonts.Fonts("", {}, "a note"))
    assert page == cleaned.html.replace(clean.FONT_CSS_MARK, "")
    assert "<style></style>" in page


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
    # A real slot, behind a title the cleaner did not write: it escapes "<".
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
    ("the mark as a paragraph", HEAD + "<style></style></head><body><p>" + MARK + "</p></body>"),
    ("the slot and nothing before it", SLOT + REST),
    ("the mark and nothing else", MARK),
    ("nothing at all", ""),
]


@pytest.mark.parametrize("document", [doc for _name, doc in NOT_THE_SLOT],
                         ids=[name for name, _doc in NOT_THE_SLOT])
def test_apply_leaves_alone_whatever_is_not_the_cleaners_own_slot(document):
    """The slot is where ``clean`` puts it (``clean.font_slot``): the document
    opens with the cleaner's own head, and the marked ``<style>`` comes
    straight after the title. Text that only LOOKS like the slot - in an
    attribute, a comment, a textarea, the body - is somewhere a paste would be
    read as something else. None of these can come out of ``clean``; ``apply``
    says it returns such a document unchanged, and it does.

    Unchanged, and never silently: there were rules to put in, so it is
    counted. A document left unfilled with nothing said looks exactly like an
    entry that asked for no typefaces."""
    result = fonts.Fonts(rule(A), {name_of(A): A}, "")
    _degrade.reset()
    assert fonts.apply(document, result) == document
    assert _degrade.counts() == {APPLY: 1}
    _degrade.reset()


@pytest.mark.parametrize("document", [None, 7, b"<style>/*blog-fonts*/</style>", ["x"]],
                         ids=repr)
def test_apply_hands_back_what_is_not_text(document):
    _degrade.reset()
    assert fonts.apply(document, some_fonts()) is document
    assert _degrade.counts() == {APPLY: 1}
    _degrade.reset()


def test_apply_is_quiet_only_when_there_was_nothing_to_put_in():
    """No slot and no rules: nothing was lost, nothing is said. No slot and
    rules: said once. A slot and rules that fail the last check: said once, and
    the slot is emptied. Both at once: still once."""
    cleaned = clean.clean("<p>x</p>").html
    nowhere = HEAD + REST
    good, bad = fonts.Fonts(rule(A), {}, ""), fonts.Fonts("p{color:red}", {}, "")
    for document, typefaces, result, count in [
            (nowhere, fonts.Fonts("", {}, ""), nowhere, 0),
            (nowhere, good, nowhere, 1),
            (nowhere, bad, nowhere, 1),
            (cleaned, bad, cleaned.replace(MARK, ""), 1),
            (cleaned, fonts.Fonts("", {}, ""), cleaned.replace(MARK, ""), 0),
            (cleaned, good, cleaned.replace(MARK, rule(A)), 0)]:
        _degrade.reset()
        assert fonts.apply(document, typefaces) == result
        assert _degrade.counts() == ({APPLY: count} if count else {})
    _degrade.reset()


@pytest.mark.parametrize("css", [
    "</style><script>alert(1)</script>",
    "@import url(https://evil.test/x.css);",
    "body{background:url(https://evil.test/t.gif)}",
    '@font-face{font-family:"A";src:url(https://fonts.gstatic.com/s/a.woff2) format("woff2")}',
    '@font-face{font-family:"A";src:url(../fonts/' + "0" * 20 + '.woff2) format("woff2")}'
    "p{color:red}",
    '@font-face{font-family:"A";src:url(../fonts/../../x.woff2) format("woff2")}',
    '@font-face{font-family:"A<";src:url(../fonts/' + "0" * 20 + '.woff2) format("woff2")}',
    '@font-face{font-family:"A";src:url(../fonts/' + "0" * 20 + '.woff2) format("woff2")}\n\n',
    '@font-face{font-family:"A";src:url(../fonts/' + "0" * 20 + '.woff2) format("woff2");'
    "unicode-range:U+0-7F;behavior:url(x)}",
    7, None, b"bytes",
], ids=repr)
def test_apply_writes_only_rules_this_module_could_have_written(css):
    """``Fonts`` is a plain record: anything can build one. Nothing cleans the
    document after ``apply``, so ``apply`` checks the rules itself - and what
    fails goes in as nothing, counted, rather than as itself."""
    cleaned = clean.clean("<p>x</p>")
    _degrade.reset()
    page = fonts.apply(cleaned.html, fonts.Fonts(css, {}, ""))
    assert page == cleaned.html.replace(clean.FONT_CSS_MARK, "")
    assert _degrade.counts() == {APPLY: 1}
    _degrade.reset()
    assert fonts.apply(cleaned.html, "not a Fonts") == page
    assert fonts.apply(cleaned.html, None) == page
    assert _degrade.counts() == {APPLY: 2}
    _degrade.reset()


def test_a_filled_document_is_still_a_clean_one():
    """The mark is gone, so cleaning the filled document again must not find
    one - and must throw the @font-face rules away, which is why NOTHING may
    clean a document after ``apply``. Pinned so that rule is not learned twice."""
    result = some_fonts()
    page = fonts.apply(clean.clean("<h1>T</h1><p>x</p>").html, result)
    again = clean.clean(page)
    assert "@font-face" in page and "@font-face" not in again.html
    assert again.html.count(clean.FONT_CSS_MARK) == 1
