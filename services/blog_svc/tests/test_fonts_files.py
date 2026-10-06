"""The typeface files: what is accepted as one, what it is named, how often
it is asked for.
"""
import hashlib

import pytest

from services import _degrade
from services.blog_svc import fonts
from services.blog_svc.tests._fonts_kit import (A, B, FETCH, G, LINK, NONE, SOME, Web, audit,
                                                face, name_of, rule, sheet, woff2)
from shared import blog_inbox


NOT_A_TYPEFACE = [
    ("html", b"<!doctype html><html><body>Error 404</body></html>"),
    ("the signature and nothing else", b"wOF2"),
    ("a header cut short", woff2("x")[:47]),
    ("a header and no table", woff2("x")[:12] + b"\x00\x00" + woff2("x")[14:]),
    ("woff 1", b"wOFF" + woff2("x")[4:]),
    ("truetype", b"\x00\x01\x00\x00" + woff2("x")[4:]),
    ("cut short", woff2("x")[:-1]),
    ("with a tail", woff2("x") + b"<script>"),
    ("lower case", b"wof2" + woff2("x")[4:]),
    ("empty", b""),
    ("text", woff2("x").decode("latin-1")),
    ("none", None),
    ("a number", 7),
    ("a list", [woff2("x")]),
    ("too large", woff2("x", size=401 * 1024)),
]


@pytest.mark.parametrize("answer", [answer for _id, answer in NOT_A_TYPEFACE],
                         ids=[name for name, _answer in NOT_A_TYPEFACE])
def test_a_file_that_is_not_woff2_is_dropped(answer):
    """A file is stored only when it is what a browser would accept as WOFF2:
    the signature, a whole header, the length the header itself declares. The
    rule that named it goes with it; the others stay."""
    css = sheet(face("latin", G + "bad.woff2"), face("latin", G + "a.woff2", weight="700"))
    web = Web({LINK: css, G + "bad.woff2": answer, G + "a.woff2": A})
    _degrade.reset()
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.css == rule(A, weight="700") and result.files == {name_of(A): A}
    assert result.note == SOME + "1 file could not be fetched or was not a typeface."
    # Bytes that are not a typeface are the other end's. Something that is not
    # bytes, or is more than was asked for, is a fetch that broke its word.
    limit = blog_inbox.fonts()["max_file_kb"] * 1024
    broke_its_word = not isinstance(answer, bytes) or len(answer) > limit
    assert _degrade.counts() == ({FETCH: 1} if broke_its_word else {})
    _degrade.reset()


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


def test_a_file_at_the_size_limit_is_kept_and_one_byte_more_is_not():
    cap = blog_inbox.fonts()["max_file_kb"] * 1024
    exact, over = woff2("exact", size=cap - 48), woff2("over", size=cap - 47)
    assert (len(exact), len(over)) == (cap, cap + 1)
    css = sheet(face("latin", G + "exact.woff2"), face("latin", G + "over.woff2", weight="700"))
    web = Web({LINK: css, G + "exact.woff2": exact, G + "over.woff2": over})
    assert audit(fonts.localize([LINK], fetch=web)).files == {name_of(exact): exact}


def test_a_bytearray_is_stored_as_bytes():
    web = Web({LINK: bytearray(sheet(face("latin", G + "a.woff2"))), G + "a.woff2": bytearray(A)})
    result = audit(fonts.localize([LINK], fetch=web))
    assert result.files == {name_of(A): A} and type(result.files[name_of(A)]) is bytes


def test_the_same_bytes_get_the_same_name():
    """A name is the first 20 hex characters of the content's SHA-256, so the
    same file under two addresses is ONE file on disk - and a name can never be
    anything the stylesheet chose."""
    css = sheet(face("latin", G + "one.woff2"), face("latin", G + "two.woff2", weight="700"),
                face("latin-ext", G + "three.woff2"))
    web = Web({LINK: css, G + "one.woff2": A, G + "two.woff2": bytes(A), G + "three.woff2": B})
    result = audit(fonts.localize([LINK], fetch=web))
    expected = hashlib.sha256(A).hexdigest()[:20] + ".woff2"
    assert list(result.files) == [expected, name_of(B)]
    assert result.css.count(expected) == 2
    assert blog_inbox.FONT_NAME_RE.match(expected)


def test_a_file_named_by_several_blocks_is_fetched_once():
    """How Google serves a variable face asked for by weight: one block per
    weight, every one naming the same file."""
    css = sheet(*[face("latin", G + "var.woff2", weight=weight)
                  for weight in ("400", "500", "600", "700")])
    web = Web({LINK: css, G + "var.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web))
    assert web.urls == [LINK, G + "var.woff2"]
    assert result.css == "\n".join(rule(A, weight=w) for w in ("400", "500", "600", "700"))
    assert result.files == {name_of(A): A} and result.note == ""


def test_a_file_that_failed_is_not_asked_for_again():
    css = sheet(*[face("latin", G + "var.woff2", weight=weight) for weight in ("400", "700")])
    web = Web({LINK: css, G + "var.woff2": OSError("down")})
    result = audit(fonts.localize([LINK], fetch=web))
    assert web.urls == [LINK, G + "var.woff2"] and result.css == ""
    assert "1 file could not be fetched or was not a typeface" in result.note
