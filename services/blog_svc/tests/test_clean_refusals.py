"""Why a document was refused, and that a refusal is never a quiet crash.

Every refusal looks the same from outside - an empty shell and
``removed == {"unparseable": 1}`` - because that is the contract the rest of
the service reads. ``Cleaned.reason`` says which of several different things
happened, as a short stable code from ``clean.REFUSALS``, so the operator can
be told something more useful than "unparseable" and so a bug in the cleaner
cannot pass for a bad upload.
"""
import logging

import pytest
from lxml import etree

from services import _degrade
from services.blog_svc import clean

EMPTY = clean.clean("").html


def degrades(area):
    return _degrade.counts().get(area, 0)


def refusal(document, reason):
    """Clean ``document`` and assert it was refused for ``reason``."""
    result = clean.clean(document)
    assert result.removed == {"unparseable": 1} and result.html == EMPTY and result.body == ""
    assert result.reason == reason
    return result


def test_the_codes_are_a_fixed_short_list():
    assert clean.REFUSALS == ("empty", "not_text", "crowded_tag", "cut_off", "too_deep",
                              "did_not_settle", "internal", "too_slow")


def test_a_document_that_is_kept_has_no_reason():
    result = clean.clean("<h1>T</h1><p>words</p>")
    assert result.reason == "" and "unparseable" not in result.removed


@pytest.mark.parametrize("document", ["", " ", "\n\t ", chr(0), chr(0) + chr(1) + chr(0x7F)])
def test_nothing_to_read_is_empty(document):
    refusal(document, "empty")


@pytest.mark.parametrize("thing", [None, 5, 1.5, b"<p>x</p>", ["<p>x</p>"], {"html": "x"}])
def test_something_that_is_not_text_says_so(thing):
    refusal(thing, "not_text")


def test_a_tag_with_too_many_attributes_is_crowded():
    names = " ".join(f"a{i}" for i in range(clean.MAX_TAG_ATTRS + 1))
    refusal(f"<p>before</p><p {names}>t</p>", "crowded_tag")


@pytest.mark.parametrize("document", [
    "<p>x</p><script>never closed", "<p>x</p><style>p{color:red}", "<p>x</p><p title=\"never closed",
])
def test_a_document_that_ends_inside_something_is_cut_off(document):
    refusal(document, "cut_off")


def test_a_document_too_deep_is_either_whole_or_refused_for_its_depth():
    """Where the parser's depth limit is, and whether it says it stopped, are
    both the parser's business. What is promised is the pair of outcomes."""
    document = "<p>FIRST</p>" + "<div>" * 50_000 + "INNER" + "</div>" * 50_000 + "<p>LAST</p>"
    result = clean.clean(document)
    if "unparseable" in result.removed:
        assert result.reason in ("too_deep", "cut_off") and result.body == ""
    else:
        assert result.reason == "" and all(word in result.body for word in ("FIRST", "INNER", "LAST"))


def test_the_parsers_log_only_chooses_the_word(monkeypatch):
    """The end marker alone decides whether a document was read to its end. The
    parser's error log is asked one thing, and only once the marker is missing:
    was it depth that stopped it? So a parser that logs something alarming
    about a document it FINISHED cannot get that document refused."""
    monkeypatch.setattr(clean, "_said_too_deep", lambda parser: True)
    assert clean.clean("<h1>T</h1><p>words</p>").reason == ""
    refusal("<p>x</p><script>never closed", "too_deep")
    monkeypatch.setattr(clean, "_said_too_deep", lambda parser: False)
    refusal("<p>x</p><script>never closed", "cut_off")


def test_a_document_that_never_settles_is_a_bug_and_is_counted_as_one(monkeypatch):
    """Cleaning repeats until its output cleans to itself. If that does not
    happen, the fault is the cleaner's, not the document's: refused, and
    counted where a swallowed fault is counted."""
    before = degrades("blog.clean.unsettled")
    monkeypatch.setattr(clean, "MAX_PASSES", 1)
    refusal("<p onclick=x>t</p>", "did_not_settle")
    assert degrades("blog.clean.unsettled") == before + 1


def test_a_bug_in_the_markup_code_is_internal_and_counted(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("a bug")
    before = degrades("blog.clean")
    monkeypatch.setattr(clean, "_href", boom)
    refusal("<a href='https://example.com/'>x</a>", "internal")
    assert degrades("blog.clean") == before + 1


def test_an_internal_bug_logs_a_trace_but_never_the_document(caplog):
    """The internal degrade used to log the default full traceback, which can
    quote a fragment of the document (an attribute name in a KeyError). It now
    logs a document-free trace: the exception type and the frames, nothing of
    ``str(exc)`` and nothing of the input."""
    secret = "attr-that-must-not-be-logged"

    def boom(*_args, **_kwargs):
        raise KeyError(secret)
    with caplog.at_level(logging.WARNING, logger="services.degrade"):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(clean, "_href", boom)
            refusal(f"<a href='https://example.com/' data-{secret}='x'>y</a>", "internal")
    text = caplog.text
    assert "blog.clean" in text and "KeyError" in text        # the type is named
    assert secret not in text                                  # the document is not


def test_a_bug_in_the_css_code_is_internal_and_counted_too(monkeypatch):
    """The CSS filter guards ONE known failure, a stylesheet nested past the
    interpreter's depth. Anything else raised in there is a bug, and used to be
    reported as "CSS removed": every style gone, nothing counted."""
    def boom(*_args, **_kwargs):
        raise AttributeError("a bug")
    before = degrades("blog.clean")
    monkeypatch.setattr(clean, "_css_token", boom)
    refusal("<style>p{color:red}</style><p style='color:blue'>t</p>", "internal")
    assert degrades("blog.clean") == before + 1


def test_css_nested_past_the_interpreters_depth_is_still_only_that_stylesheet():
    before = degrades("blog.clean")
    result = clean.clean("<style>p{color:red}" + "(" * 50_000 + "</style><p>kept</p>")
    assert result.reason == "" and "kept" in result.body and result.removed == {"css": 1}
    assert degrades("blog.clean") == before


def test_a_bug_around_the_parser_is_internal_but_its_own_complaint_is_not(monkeypatch):
    def type_error(*_args, **_kwargs):
        raise TypeError("a bug")

    def no_document(*_args, **_kwargs):
        raise etree.ParserError("Document is empty")
    before = degrades("blog.clean")
    monkeypatch.setattr(clean.lxml.html, "document_fromstring", type_error)
    refusal("<p>x</p>", "internal")
    assert degrades("blog.clean") == before + 1
    monkeypatch.setattr(clean.lxml.html, "document_fromstring", no_document)
    refusal("<p>x</p>", "empty")
    assert degrades("blog.clean") == before + 1


def test_each_refusal_is_logged_once_by_its_code_and_never_with_the_document(caplog):
    secret = "words-that-must-not-reach-a-log"
    with caplog.at_level(logging.INFO, logger=clean.log.name):
        clean.clean(f"<p>{secret}</p><script>never closed")
        clean.clean(None)
        clean.clean(f"<p>{secret}</p>")
    lines = [record.getMessage() for record in caplog.records if record.name == clean.log.name]
    assert lines == ["refused: cut_off", "refused: not_text"]
    assert secret not in caplog.text


def test_an_element_the_author_hid_stays_hidden():
    """Without ``hidden`` on the allow-list a note the author hid came out
    visible: the attribute went and the words stayed."""
    result = clean.clean("<p hidden>draft note</p><div HIDDEN='until-found'>later</div><p>shown</p>")
    assert '<p hidden="">draft note</p>' in result.body
    assert '<div hidden="until-found">later</div>' in result.body
