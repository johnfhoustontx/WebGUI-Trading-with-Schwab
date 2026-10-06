"""A copy that fails never blocks a draft, and says truthfully what failed.

Three things are held here: ``localize`` does not raise; a failure is the
network's, the other end's bytes, or OURS, and only ours is counted for
``/health`` - under a label that says where; and neither the note nor the log
ever carries what the other end said.
"""
import ast
import inspect
import logging
from collections import Counter

import pytest

from services import _degrade
from services.blog_svc import clean, fonts
from services.blog_svc.tests._fonts_kit import (A, APPLY, B, C, FAILURES, FETCH, G, GUARD, LINK,
                                                LINKS, LINK_2, Loud, NONE, ONE_SENTENCE, READER,
                                                SOME, Web, audit, counted, face, failure_id, rule,
                                                sheet)
from shared import blog_inbox


# ── a fetch that fails: the network's, or ours ───────────────────────────────

@pytest.mark.parametrize("failure", FAILURES, ids=failure_id)
def test_a_failed_fetch_never_raises_and_says_so(failure):
    """The stylesheet cannot be had: no typefaces, one plain sentence, and
    nothing of the exception in it. Whether it is COUNTED is the difference
    between the two lists the failures come from (``_fonts_kit``)."""
    web = Web({LINK: failure})
    _degrade.reset()
    result = audit(fonts.localize([LINK], fetch=web))
    assert result == fonts.Fonts("", {}, "No typefaces were copied, so the entry is shown in "
                                 "its fallback fonts: 1 stylesheet could not be fetched.")
    assert _degrade.counts() == counted(failure)
    _degrade.reset()


@pytest.mark.parametrize("failure", FAILURES, ids=failure_id)
def test_a_failure_part_way_keeps_what_was_already_copied(failure):
    """The second link fails and so does the third file: the first link's
    faces and the two files already copied are the result."""
    css = sheet(face("latin", G + "a.woff2"), face("latin", G + "b.woff2", weight="500"),
                face("latin", G + "c.woff2", weight="600"),
                face("latin", G + "d.woff2", weight="700"))
    web = Web({LINK: css, LINK_2: failure, G + "a.woff2": A, G + "b.woff2": B,
               G + "c.woff2": failure, G + "d.woff2": C})
    _degrade.reset()
    result = audit(fonts.localize([LINK, LINK_2], fetch=web))
    assert result.css == "\n".join([rule(A), rule(B, weight="500"), rule(C, weight="700")])
    assert result.note == ("Some typefaces were not copied and are shown in a fallback font: "
                           "1 stylesheet could not be fetched, "
                           "1 file could not be fetched or was not a typeface.")
    assert _degrade.counts() == counted(failure, times=2)
    _degrade.reset()


def test_a_fetch_that_takes_no_keywords_fails_like_any_other():
    def positional_only(url):               # a fake written against the wrong signature
        return b""
    _degrade.reset()
    result = fonts.localize([LINK], fetch=positional_only)
    assert result.css == "" and "1 stylesheet could not be fetched" in result.note
    assert fonts.localize([LINK], fetch=None).css == ""
    assert fonts.localize([LINK], fetch="not callable").files == {}
    assert _degrade.counts() == {FETCH: 3}          # a TypeError each time: ours
    _degrade.reset()


def test_http_fetch_refusing_what_it_was_handed_is_a_fault_of_ours():
    """``http_fetch`` turns down an address, headers or a limit BEFORE it
    connects. ``localize`` only hands it what its own checks passed, so a
    refusal there is a disagreement inside this file - not Google being away,
    which is what the same "could not be fetched" would otherwise pass for."""
    def hands_over_no_headers(url, *, headers, timeout, max_bytes):
        return fonts.http_fetch(url, headers=None, timeout=timeout, max_bytes=max_bytes)

    _degrade.reset()
    result = audit(fonts.localize([LINK], fetch=hands_over_no_headers))
    assert result == fonts.Fonts("", {}, NONE + "1 stylesheet could not be fetched.")
    assert _degrade.counts() == {FETCH: 1}
    _degrade.reset()


@pytest.mark.parametrize("answer, faults", [
    (None, 1), (7, 1), ("text, not bytes", 1), ([b"x"], 1), (object(), 1),
    (b"\xff\xfe\x00 not utf-8", 0),
    (b"<!doctype html><html><body><h1>Error 400</h1></body></html>", 0), (b"", 0), (b"   ", 0),
    (b"{}{}{}", 0), (b"@font-face", 0), (b"\x00" * 100, 0),
], ids=lambda value: type(value).__name__ if not isinstance(value, bytes) else repr(value)[:24])
def test_a_stylesheet_that_is_not_one_never_raises(answer, faults):
    """What comes back is not a stylesheet. Bytes that are not one are the
    other end's; something that is not bytes at all is a ``fetch`` that broke
    its word, and is counted exactly as one that raised would be."""
    web = Web({LINK: answer, LINK_2: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    _degrade.reset()
    result = audit(fonts.localize([LINK, LINK_2], fetch=web))
    assert result.css == rule(A)
    assert result.note.startswith("Some typefaces were not copied")
    assert "1 stylesheet" in result.note
    assert _degrade.counts() == ({FETCH: faults} if faults else {})
    _degrade.reset()


def test_a_fetch_that_hands_back_more_than_it_was_allowed_is_counted():
    """``max_bytes`` is part of what ``fetch`` is asked. One that ignored it
    broke its word, whatever it handed back."""
    big = sheet(face("latin", G + "a.woff2"), "/*" + "x" * (16 * 1024) + "*/")
    _degrade.reset()
    result = audit(fonts.localize([LINK], fetch=Web({LINK: big}), cfg={"max_css_kb": 16}))
    assert result == fonts.Fonts("", {}, NONE + "1 stylesheet could not be fetched.")
    assert _degrade.counts() == {FETCH: 1}
    _degrade.reset()


# ── a stylesheet that arrives and cannot be used ─────────────────────────────

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
    assert _degrade.counts() == {READER: 1}
    _degrade.reset()


@pytest.mark.parametrize("answer, said, faults", [
    (OSError("refused"), "1 stylesheet could not be fetched", {}),
    (fonts.FetchError("HTTP 503"), "1 stylesheet could not be fetched", {}),
    (None, "1 stylesheet could not be fetched", {FETCH: 1}),        # not bytes at all
    (b"\xff\xfe\x00 not utf-8", "1 stylesheet was not readable text", {}),
    (sheet(CRASHES_THE_READER), "1 stylesheet made the reader fail", {READER: 1}),
    (b"<!doctype html><html><body>Error 400</body></html>", "1 stylesheet held no typefaces", {}),
    (b"", "1 stylesheet held no typefaces", {}),
], ids=["network", "status", "not bytes", "not text", "reader", "html", "empty"])
def test_each_way_a_stylesheet_fails_has_its_own_words(answer, said, faults):
    """Not fetched (the network's, or a ``fetch`` that broke its word), not
    text (the other end's bytes), the reader failed (ours), nothing in it.
    What is ours is counted, and the label says which of ours it was."""
    _degrade.reset()
    result = audit(fonts.localize([LINK], fetch=Web({LINK: answer})))
    assert result == fonts.Fonts("", {}, NONE + said + ".")
    assert _degrade.counts() == faults
    _degrade.reset()


def test_a_reader_that_runs_out_of_stack_is_a_reader_failure(monkeypatch):
    def too_deep(text, subsets):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(fonts, "_faces", too_deep)
    _degrade.reset()
    result = audit(fonts.localize([LINK], fetch=Web({LINK: sheet(face("latin", G + "a.woff2"))})))
    assert result == fonts.Fonts("", {}, NONE + "1 stylesheet made the reader fail.")
    assert _degrade.counts() == {READER: 1}
    _degrade.reset()


# ── a fault outside any one link or file ─────────────────────────────────────

def test_a_clock_that_fails_never_raises():
    def broken():
        raise OSError("no clock")
    _degrade.reset()
    result = fonts.localize([LINK], fetch=Web({}), clock=broken)
    assert result == fonts.Fonts("", {}, NONE + "the Blog service hit an error.")
    assert _degrade.counts() == {GUARD: 1}
    _degrade.reset()


def test_a_bug_in_here_is_counted_and_never_raised(monkeypatch):
    """A crash must not look like a slow network: it is counted for /health."""
    def boom():
        raise RuntimeError("SECRET")
    monkeypatch.setattr(blog_inbox, "fonts", boom)
    _degrade.reset()
    result = fonts.localize([LINK], fetch=Web({}))
    assert result == fonts.Fonts("", {}, "No typefaces were copied, so the entry is shown in "
                                 "its fallback fonts: the Blog service hit an error.")
    assert _degrade.counts() == {GUARD: 1}
    _degrade.reset()


# ── what does get out, and what does not ─────────────────────────────────────

@pytest.mark.parametrize("stop", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_what_must_stop_the_process_is_never_swallowed(stop, monkeypatch):
    """``localize`` raises nothing that is an ``Exception``. A shutdown
    (``KeyboardInterrupt``, ``SystemExit``) is not one: swallowed, the service
    would carry on as if it had been told nothing. Each gets out, from
    wherever it starts."""
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


def test_running_out_of_memory_is_a_fault_like_any_other(monkeypatch):
    """``MemoryError`` used to be let out of ``localize``. The command runner
    catches it anyway and carries on, so all that bought was a draft that
    became a dead-lettered command with NO answer, instead of a draft in its
    fallback fonts with a note. Here it is most likely this copy's own (the
    tokens of one large stylesheet) and is given back as the stack unwinds.

    So: counted, wherever it starts; the copy carries on; nothing gets out."""
    good = sheet(face("latin", G + "a.woff2"), face("latin", G + "b.woff2", weight="700"))
    _degrade.reset()
    web = Web({LINK: MemoryError(), LINK_2: good, G + "a.woff2": MemoryError(), G + "b.woff2": B})
    result = audit(fonts.localize([LINK, LINK_2], fetch=web))           # while fetching
    assert result.css == rule(B, weight="700")
    assert result.note == SOME + ("1 stylesheet could not be fetched, "
                                  "1 file could not be fetched or was not a typeface.")
    assert _degrade.counts() == {FETCH: 2}

    def reads_out_of_memory(text, subsets):
        raise MemoryError()
    _degrade.reset()
    with monkeypatch.context() as patch:
        patch.setattr(fonts, "_faces", reads_out_of_memory)
        result = audit(fonts.localize([LINK], fetch=Web({LINK: good})))  # while reading
    assert result == fonts.Fonts("", {}, NONE + "1 stylesheet made the reader fail.")
    assert _degrade.counts() == {READER: 1}

    def no_memory_for_a_clock():
        raise MemoryError()
    _degrade.reset()
    result = fonts.localize([LINK], fetch=Web({LINK: good}), clock=no_memory_for_a_clock)
    assert result == fonts.Fonts("", {}, NONE + "the Blog service hit an error.")
    assert _degrade.counts() == {GUARD: 1}
    _degrade.reset()


# ── what a note and a log line may say ───────────────────────────────────────

def test_every_reason_has_its_words_and_no_other_is_ever_used():
    """What was left out is counted under a ``Reason``, and the note is built
    from them. Two ways that could go quietly wrong: a count kept under a name
    the note does not know (never reported), and a reason nothing ever counts
    (wording for something that cannot happen). Neither is possible here."""
    source = ast.parse(inspect.getsource(fonts))
    used = {node.attr for node in ast.walk(source)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "Reason"}
    assert used == set(fonts.Reason.__members__)
    settings, said = blog_inbox.fonts(), set()
    for reason in fonts.Reason:
        one = fonts._note(Counter({reason: 1}), {}, settings)
        many = fonts._note(Counter({reason: 3}), {"x": b""}, settings)
        assert ONE_SENTENCE.fullmatch(one) and ONE_SENTENCE.fullmatch(many), reason
        assert one.startswith(NONE) and many.startswith(SOME), reason
        assert "{" not in one + many, reason                # every setting it names was filled
        said.add(one[len(NONE):])
    assert len(said) == len(fonts.Reason)                   # no two reasons say the same thing
    # A count under anything that is not a Reason is refused, not dropped.
    for stray in ("sheet", "time", 7, None):
        with pytest.raises(TypeError):
            fonts._note(Counter({stray: 1}), {}, settings)
    assert fonts._note(Counter(), {}, settings) == ""
    assert fonts._note(Counter({fonts.Reason.FILE: 0}), {}, settings) == ""


def test_the_note_never_carries_what_the_other_end_said():
    """Not the exception, not an address, not a family name: counts, and this
    module's own words."""
    css = sheet(face("latin", G + "a.woff2", family="'Evil Family'"),
                face("latin", "https://evil.test/secret.woff2", family="'Evil Two'"))
    web = Web({LINK: css, LINK_2: Loud(), G + "a.woff2": Loud()})
    result = audit(fonts.localize(
        [LINK, LINK_2, "https://evil.test/css2?family=Secret"], fetch=web, cfg={"max_files": 1}))
    for needle in ("SECRET", "secret", "evil", "Evil", "token", "http", "script", "Loud",
                   "family", "Inter", "?", "<", "/"):
        assert needle not in result.note, needle
    assert result.note.count(".") == 1 and result.note.endswith(".")
    _degrade.reset()


def test_the_log_names_what_failed_and_never_what_it_said(caplog, monkeypatch):
    """The note is pinned above; this is the LOG. An exception's text is
    whatever the other end, or a library quoting the other end, put in it - an
    address with its query, a piece of a response. The log gets the type's
    name, and for a fault of our own the place it happened. The one text let
    through is a ``FetchError``'s, which is written in ``fonts.py`` itself."""
    planted = "PLANTED-9f3c https://evil.test/?q=secret <script>"
    good = sheet(face("latin", G + "a.woff2"), face("latin", G + "b.woff2", weight="700"))
    web = Web({LINKS[0]: OSError(planted), LINKS[1]: RuntimeError(planted),
               LINKS[2]: fonts.FetchError("HTTP 503"), LINKS[3]: good,
               G + "a.woff2": ValueError(planted), G + "b.woff2": "not bytes"})

    def reader_fails(text, subsets):
        raise IndexError(planted)

    def broken_clock():
        raise KeyError(planted)

    with caplog.at_level(logging.DEBUG):
        audit(fonts.localize(LINKS, fetch=web))
        # ... a reader that fails, a fault outside any one link or file, and
        # rules ``apply`` will not write or has nowhere to put.
        with monkeypatch.context() as patch:
            patch.setattr(fonts, "_faces", reader_fails)
            audit(fonts.localize([LINK], fetch=Web({LINK: good})))
        audit(fonts.localize([LINK], fetch=Web({LINK: good}), clock=broken_clock))
        fonts.apply(clean.clean("<p>x</p>").html, fonts.Fonts(planted, {}, ""))
        fonts.apply("<p>" + planted + "</p>", fonts.Fonts(rule(A), {}, ""))
    logged = caplog.text
    assert len(caplog.records) >= 8
    for needle in ("PLANTED", "9f3c", "evil", "secret", "<script", "?q="):
        assert needle not in logged, needle
    for said in ("OSError", "RuntimeError", "ValueError", "IndexError", "KeyError", "HTTP 503",
                 FETCH, READER, GUARD, APPLY):
        assert said in logged, said
    _degrade.reset()
