"""The /blog page: its pure helpers, the two preview reads, and the page itself.

The page is the owner's side of the site Blog: upload a document, look at the
draft the service made of it, publish, discard, unpublish. Everything the page
decides is a module-level function tested here without a browser; ``render`` is
then driven the way the browser drives it (the ``test_x_post_page`` recipe).
"""
import asyncio
import hashlib
import pathlib
import types

import pytest

from pages import blog as page
from shared import blog_inbox

PAGES = pathlib.Path(page.__file__).resolve().parent

ID_A, ID_B, ID_C = "a" * 16, "b" * 16, "c" * 16
DOC = b"<!doctype html><html><head><title>T</title></head><body><p>Hi</p></body></html>"
DIGEST = hashlib.sha256(DOC).hexdigest()
JUNK = (None, [], "text", 5, 1.5, True, {"drafts": 5}, {"drafts": "x"},
        {"entries": {"a": 1}}, {"drafts": [None, 5, "x", [], {"id": 7}]})


def _draft(draft_id=ID_A, **over):
    base = {"id": draft_id, "source": "upload", "revises": "", "slug": "a-first-entry",
            "title": "A first entry", "summary": "What it says.", "tags": ["gamma", "vol"],
            "removed": {"script": 2, "form": 1}, "font_note": "", "bytes": 35840,
            "digest": DIGEST, "received_at": "2026-10-06T19:03:00+00:00"}
    return {**base, **over}


def _entry(slug="an-older-entry", **over):
    base = {"slug": slug, "title": "An older entry", "summary": "s", "tags": [],
            "published_at": "2026-10-01T15:00:00+00:00",
            "updated_at": "2026-10-01T15:00:00+00:00"}
    return {**base, **over}


# ── draft_rows / post_rows ───────────────────────────────────────────────────
def test_draft_rows_normalise_a_draft():
    (row,) = page.draft_rows({"drafts": [_draft()]})
    assert row == {"id": ID_A, "source": "upload", "revises": "", "slug": "a-first-entry",
                   "title": "A first entry", "summary": "What it says.",
                   "tags": ["gamma", "vol"], "removed": {"script": 2, "form": 1},
                   "font_note": "", "bytes": 35840,
                   "received_at": "2026-10-06T19:03:00+00:00"}


def test_draft_rows_keep_the_order_they_were_published_in():
    rows = page.draft_rows({"drafts": [_draft(ID_B), _draft(ID_A)]})
    assert [r["id"] for r in rows] == [ID_B, ID_A]


@pytest.mark.parametrize("junk", JUNK)
def test_draft_rows_never_raise_on_junk(junk):
    assert page.draft_rows(junk) == []


def test_draft_rows_drop_a_draft_whose_id_is_not_an_id():
    rows = page.draft_rows({"drafts": [_draft("../../etc"), _draft(ID_A.upper()),
                                       _draft(ID_A + "\n"), _draft(ID_B)]})
    assert [r["id"] for r in rows] == [ID_B]


def test_draft_rows_replace_wrong_types_field_by_field():
    (row,) = page.draft_rows({"drafts": [_draft(
        source=5, revises=["x"], slug=None, title=7, summary={"a": 1},
        tags="gamma", removed=[1, 2], font_note=None, bytes="big", received_at=12)]})
    assert row["source"] == "" and row["revises"] == "" and row["slug"] == ""
    assert row["title"] == "" and row["summary"] == "" and row["tags"] == []
    assert row["removed"] is None and row["font_note"] == ""
    assert row["bytes"] is None and row["received_at"] == ""


def test_draft_rows_keep_only_text_tags_and_a_usable_revises():
    (row,) = page.draft_rows({"drafts": [_draft(
        tags=["a", 5, None, "b"], revises="An-Older-Entry")]})
    assert row["tags"] == ["a", "b"]
    assert row["revises"] == "an-older-entry"
    (bad,) = page.draft_rows({"drafts": [_draft(revises="../x")]})
    assert bad["revises"] == ""
    (true_bytes,) = page.draft_rows({"drafts": [_draft(bytes=True)]})
    assert true_bytes["bytes"] is None      # a bool is not a size


def test_post_rows_add_the_public_address():
    (row,) = page.post_rows({"entries": [_entry()]}, "neuralstrike.co")
    assert row["slug"] == "an-older-entry"
    assert row["title"] == "An older entry"
    assert row["url"] == "https://neuralstrike.co/blog/an-older-entry/"
    assert row["address"] == "/blog/an-older-entry/"
    assert row["published"] == "Oct 1, 2026"
    assert row["published_at"] == "2026-10-01T15:00:00+00:00"


@pytest.mark.parametrize("junk", JUNK)
def test_post_rows_never_raise_on_junk(junk):
    assert page.post_rows(junk, "neuralstrike.co") == []
    assert page.post_rows(junk, None) == []


def test_post_rows_drop_an_address_that_could_not_be_one():
    rows = page.post_rows({"entries": [
        _entry("../x"), _entry("fonts"), _entry("a b"), _entry(5), _entry("good-one"),
        "not a dict"]}, "neuralstrike.co")
    assert [r["slug"] for r in rows] == ["good-one"]


def test_post_rows_title_falls_back_to_the_address():
    (row,) = page.post_rows({"entries": [_entry(title=None, published_at="junk")]},
                            "neuralstrike.co")
    assert row["title"] == "an-older-entry"
    assert row["published"] == "" and row["published_at"] == ""


@pytest.mark.parametrize("host", [None, "", 5, "evil.example/x", "a b", "x\"y",
                                  "javascript:alert(1)"])
def test_post_rows_build_no_link_for_a_host_that_is_not_one(host):
    (row,) = page.post_rows({"entries": [_entry()]}, host)
    assert row["url"] == ""


def test_public_url_is_one_function():
    assert page.public_url("neuralstrike.co", "a-b") == "https://neuralstrike.co/blog/a-b/"
    assert page.public_url("neuralstrike.co", "../x") == ""
    assert page.public_url("neuralstrike.co", "") == ""


# ── removed_text ─────────────────────────────────────────────────────────────
def test_removed_text_counts_in_whole_words():
    assert page.removed_text({"script": 2, "form": 1}) == "Removed 2 scripts and 1 form."
    assert page.removed_text({"script": 1}) == "Removed 1 script."
    assert page.removed_text({"handler": 3, "link": 3, "css": 1, "img": 2}) == (
        "Removed 3 event handlers, 3 outside links to stylesheets or fonts, "
        "1 style rule and 2 images.")
    assert page.removed_text({"handler": 1, "link": 1}) == (
        "Removed 1 event handler and 1 outside link to a stylesheet or font.")


def test_removed_text_sums_what_it_has_no_word_for():
    assert page.removed_text({"object": 2, "embed": 1}) == "Removed 3 other elements."
    assert page.removed_text({"object": 1}) == "Removed 1 other element."
    assert page.removed_text({"script": 1, "object": 2}) == (
        "Removed 1 script and 2 other elements.")


def test_removed_text_ignores_what_is_not_a_positive_count():
    assert page.removed_text({}) == "Nothing was removed."
    assert page.removed_text({"script": 0, "form": -2, "css": "3", "img": None,
                              "handler": True, "link": 1.5}) == "Nothing was removed."
    assert page.removed_text({5: 2, "script": 1}) == "Removed 1 script and 2 other elements."


@pytest.mark.parametrize("junk", [None, [], "script", 5, True])
def test_removed_text_says_nothing_about_a_count_it_was_not_given(junk):
    """Not a dict is "not known", which is not the same as "nothing removed"."""
    assert page.removed_text(junk) == ""


def test_the_removal_words_are_one_module_level_table():
    assert isinstance(page.REMOVED_WORDS, dict)
    for one, many in page.REMOVED_WORDS.values():
        assert one and many and one != many


# ── source_text / size_text / when_text / date_text ──────────────────────────
def test_source_text_is_written_from_the_owners_side():
    assert page.source_text("upload") == "Uploaded file"
    assert page.source_text("chat") == "Claude Chat"
    for junk in (None, "", "UPLOAD", 5, ["upload"], {"a": 1}):
        assert page.source_text(junk) == "Unknown"


def test_size_text():
    assert page.size_text(35840) == "35 KB"
    assert page.size_text(1) == "1 KB"            # never "0 KB" for a real file
    assert page.size_text(1024 * 1024) == "1 MB"
    assert page.size_text(1536 * 1024) == "1.5 MB"
    assert page.size_text(0) == "0 KB"
    for junk in (None, "big", [], True, -5, float("nan"), float("inf")):
        assert page.size_text(junk) == ""


def test_when_text_is_central_time():
    assert page.when_text("2026-10-06T19:03:00+00:00") == "Oct 6, 2:03 PM"
    assert page.when_text("2026-10-06T19:03:00Z") == "Oct 6, 2:03 PM"
    # A naive stamp is UTC, as the bus writes one.
    assert page.when_text("2026-10-06T19:03:00") == "Oct 6, 2:03 PM"
    assert page.when_text("2026-01-05T06:00:00+00:00") == "Jan 5, 12:00 AM"
    assert page.when_text("2026-01-05T18:00:00+00:00") == "Jan 5, 12:00 PM"


def test_when_text_across_both_clock_changes():
    # Spring forward, 8 March 2026: 1:59 AM CST is followed by 3:00 AM CDT.
    assert page.when_text("2026-03-08T07:59:00+00:00") == "Mar 8, 1:59 AM"
    assert page.when_text("2026-03-08T08:00:00+00:00") == "Mar 8, 3:00 AM"
    # Fall back, 1 November 2026: 1:30 AM happens twice, an hour apart.
    assert page.when_text("2026-11-01T06:30:00+00:00") == "Nov 1, 1:30 AM"
    assert page.when_text("2026-11-01T07:30:00+00:00") == "Nov 1, 1:30 AM"
    assert page.when_text("2026-11-01T08:30:00+00:00") == "Nov 1, 2:30 AM"
    # The date is Central's too: 03:00 UTC is still the evening before.
    assert page.when_text("2026-10-07T03:00:00+00:00") == "Oct 6, 10:00 PM"


@pytest.mark.parametrize("junk", [None, "", "garbage", 5, [], {}, "2026-13-45T00:00:00"])
def test_when_and_date_text_are_empty_on_junk(junk):
    assert page.when_text(junk) == ""
    assert page.date_text(junk) == ""


def test_date_text_is_the_central_date():
    assert page.date_text("2026-10-07T03:00:00+00:00") == "Oct 6, 2026"
    assert page.date_text("2026-10-07T12:00:00+00:00") == "Oct 7, 2026"


# ── decode_upload ────────────────────────────────────────────────────────────
def test_decode_upload_reads_utf8_with_or_without_a_mark():
    text = "<p>caf" + chr(0xE9) + "</p>"
    assert len(text) == 11
    assert page.decode_upload(text.encode("utf-8")) == text
    marked = b"\xef\xbb\xbf" + text.encode("utf-8")
    assert page.decode_upload(marked) == text          # the mark is not in the text
    assert page.decode_upload(b"") == ""


def test_decode_upload_refuses_everything_else():
    text = "<p>hello</p>"
    assert page.decode_upload(text.encode("utf-16")) is None        # FF FE ...
    # Without a mark UTF-16 of plain ASCII IS valid UTF-8 - full of NULs.
    assert page.decode_upload(text.encode("utf-16-le")) is None
    assert page.decode_upload(text.encode("utf-16-be")) is None
    assert page.decode_upload(bytes(range(256))) is None
    assert page.decode_upload(b"\x89PNG\r\n\x1a\n\x00\x00") is None
    assert page.decode_upload(b"caf\xe9") is None                   # Latin-1
    for junk in (None, "already text", 5, [b"x"]):
        assert page.decode_upload(junk) is None
    assert page.decode_upload(bytearray(b"<p>x</p>")) == "<p>x</p>"


# ── preview_src ──────────────────────────────────────────────────────────────
def test_preview_src():
    assert page.preview_src(ID_A) == f"/blog/preview/{ID_A}/entry.html"
    for bad in ("../x", "", None, 5, ID_A.upper(), ID_A + "/", ID_A[:-1], ID_A + "\n",
                "..%2f..%2fetc"):
        assert page.preview_src(bad) == ""


# ── publish_fields / address_problem ─────────────────────────────────────────
def test_publish_fields_builds_what_the_command_takes():
    fields = page.publish_fields(" A title ", "A summary", "My-Address", "gamma, vol ,,  skew ")
    assert fields == {"title": " A title ", "summary": "A summary", "slug": "My-Address",
                      "tags": ["gamma", "vol", "skew"]}
    # ...and it is what ``owner_command`` accepts.
    command = blog_inbox.owner_command("publish", ID_B, draft_id=ID_A, fields=fields)
    assert command["args"]["fields"] == {"title": "A title", "summary": "A summary",
                                         "slug": "my-address",
                                         "tags": ["gamma", "vol", "skew"]}


def test_publish_fields_never_raise_on_junk():
    assert page.publish_fields(None, 5, [], None) == {
        "title": "", "summary": "", "slug": "", "tags": []}
    assert page.publish_fields("t", "s", "a", "") ["tags"] == []


def test_address_problem_is_empty_for_a_usable_address():
    for good in ("a-first-entry", "A-First-Entry", " spaced ", "x", "2026-outlook"):
        assert page.address_problem(good) == "", good


def test_address_problem_says_why_in_one_sentence():
    cap = blog_inbox.limits()["slug_chars"]
    blank = page.address_problem("")
    too_long = page.address_problem("a" * (cap + 1))
    chars = page.address_problem("has spaces in it")
    reserved = page.address_problem("fonts")
    assert "address" in blank.lower()
    assert str(cap) in too_long and "character" in too_long
    assert "letters" in chars and "hyphen" in chars
    assert "fonts" in reserved and "reserved" in reserved
    assert len({blank, too_long, chars, reserved}) == 4
    for text in (blank, too_long, chars, reserved):
        assert text.endswith(".") and "slug" not in text.lower()
    for bad in ("a--b", "-a", "a-", "a/b", "a.b", "caf" + chr(0xE9), "a\nb", None, 5):
        assert page.address_problem(bad), bad


def test_address_problem_agrees_with_the_validator_the_service_runs():
    for raw in ("ok", "Not OK", "fonts", "a" * 200, "", "a-b", "a--b", " x "):
        assert (page.address_problem(raw) == "") == (blog_inbox.clean_slug(raw) is not None)


def test_an_existing_address_is_held_to_the_ceiling_not_todays_limit():
    """An entry published when the limit was higher keeps its address."""
    cap = blog_inbox.limits()["slug_chars"]
    longer = "a" * (cap + 1)
    assert longer == blog_inbox.existing_slug(longer)
    assert page.address_problem(longer)
    assert page.address_problem(longer, existing=True) == ""
    assert page.address_problem("../x", existing=True)


def test_publish_problem_is_empty_when_the_entry_can_be_published():
    assert page.publish_problem("A title", "a-first-entry") == ""
    assert page.publish_problem("  A title  ", " A-First-Entry ") == ""


@pytest.mark.parametrize("blank", ["", "   ", None, 5, []])
def test_publish_problem_says_a_blank_title_is_why(blank):
    why = page.publish_problem(blank, "a-first-entry")
    assert why == page.NO_TITLE
    assert "title" in why.lower() and why.endswith(".") and "slug" not in why.lower()


def test_publish_problem_is_the_address_reason_once_there_is_a_title():
    for bad in ("", "Not an address!", "fonts", "a--b"):
        assert page.publish_problem("A title", bad) == page.address_problem(bad) != ""
    # The first reason only: the title is the first field on the card.
    assert page.publish_problem("", "Not an address!") == page.NO_TITLE


def test_publish_problem_holds_a_replacement_to_the_ceiling_like_address_problem():
    longer = "a" * (blog_inbox.limits()["slug_chars"] + 1)
    assert page.publish_problem("A title", longer) == page.address_problem(longer) != ""
    assert page.publish_problem("A title", longer, existing=True) == ""


# ── the two preview reads ────────────────────────────────────────────────────
def _stage(tmp_path, draft_id=ID_A, data=DOC, name=None):
    folder = tmp_path / blog_inbox.STAGING_DIR / draft_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name or blog_inbox.DOC_NAME)).write_bytes(data)
    return {"drafts": [_draft(draft_id, digest=hashlib.sha256(data).hexdigest())]}


def test_preview_document_serves_the_staged_file_the_view_describes(tmp_path):
    view = _stage(tmp_path)
    assert page.preview_document(ID_A, view, tmp_path) == DOC


def test_preview_document_finds_a_replacement_still_being_put_in_place(tmp_path):
    view = _stage(tmp_path, name=blog_inbox.NEXT_NAME)
    assert page.preview_document(ID_A, view, tmp_path) == DOC


@pytest.mark.parametrize("bad", ["../x", "..%2f..%2fetc", "..", "", None, 5, ID_A.upper(),
                                 ID_A + "0", ID_A[:-1], ID_A + "\n", "fonts"])
def test_preview_document_refuses_an_id_that_is_not_one(tmp_path, bad):
    view = _stage(tmp_path)
    assert page.preview_document(bad, view, tmp_path) is None


def test_preview_document_refuses_an_id_the_view_does_not_list(tmp_path):
    _stage(tmp_path, ID_B)               # on disk...
    view = {"drafts": [_draft(ID_A)]}    # ...but not a draft that is waiting
    assert page.preview_document(ID_B, view, tmp_path) is None


def test_preview_document_refuses_a_file_that_is_not_the_one_described(tmp_path):
    view = _stage(tmp_path)
    (tmp_path / blog_inbox.STAGING_DIR / ID_A / blog_inbox.DOC_NAME).write_bytes(
        DOC + b"<script>alert(1)</script>")
    assert page.preview_document(ID_A, view, tmp_path) is None


def test_preview_document_refuses_a_missing_file_and_a_missing_digest(tmp_path):
    assert page.preview_document(ID_A, {"drafts": [_draft()]}, tmp_path) is None
    view = _stage(tmp_path)
    for digest in (None, "", 5, "z" * 64, DIGEST.upper(), DIGEST[:-1]):
        listed = {"drafts": [_draft(digest=digest)]}
        assert page.preview_document(ID_A, listed, tmp_path) is None
    assert page.preview_document(ID_A, view, tmp_path) == DOC      # the control


@pytest.mark.parametrize("junk", JUNK)
def test_preview_document_never_raises_on_a_junk_view(tmp_path, junk):
    _stage(tmp_path)
    assert page.preview_document(ID_A, junk, tmp_path) is None
    assert page.preview_document(ID_A, {"drafts": [_draft()]}, junk) is None


def _font(tmp_path, data=b"wOF2 not really a typeface"):
    folder = tmp_path / blog_inbox.FONTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    name = blog_inbox.font_name_for(data)
    (folder / name).write_bytes(data)
    return name, data


def test_preview_font_serves_a_typeface_named_by_its_content(tmp_path):
    name, data = _font(tmp_path)
    assert page.preview_font(name, tmp_path) == data


def test_preview_font_refuses_a_name_that_is_not_twenty_hex_and_woff2(tmp_path):
    name, _data = _font(tmp_path)
    (tmp_path / "secret.txt").write_bytes(b"secret")
    for bad in ("../secret.txt", "..%2fsecret.txt", "secret.txt", name.upper(),
                name[1:], "0" + name, name + "\n", name.replace(".woff2", ".woff"),
                name.replace(".woff2", ".ttf"), "", None, 5, ID_A):
        assert page.preview_font(bad, tmp_path) is None, bad


def test_preview_font_refuses_a_file_whose_content_is_not_its_name(tmp_path):
    name, _data = _font(tmp_path)
    (tmp_path / blog_inbox.FONTS_DIR / name).write_bytes(b"something else")
    assert page.preview_font(name, tmp_path) is None
    assert page.preview_font("0" * 20 + ".woff2", tmp_path) is None     # not there
    assert page.preview_font(name, None) is None


# ── the two routes, on the app that ships ────────────────────────────────────
PASSWORD, SECRET, KEY, EPOCH = "hunter2", "JBSWY3DPEHPK3PXP" * 2, "k" * 43, 1


@pytest.fixture
def served(tmp_path, monkeypatch):
    """``main.app`` with real credentials, the staged folder and a drafts view."""
    from starlette.testclient import TestClient

    import auth
    import auth_middleware
    import auth_store
    import bus_client
    import main
    import repo_paths

    creds = auth_store.Credentials(
        password_hash=auth.hash_password(PASSWORD), totp_secret=SECRET,
        session_secret=KEY, epoch=EPOCH, last_totp_counter=0)
    path = tmp_path / "webgui_auth.json"
    auth_store.save(creds, path)
    monkeypatch.setattr(auth_store, "DEFAULT_PATH", path)

    data_dir = tmp_path / "data"
    view = _stage(data_dir)
    name, font = _font(data_dir)
    monkeypatch.setattr(repo_paths, "BLOG_DATA", data_dir)
    monkeypatch.setattr(bus_client, "read",
                        lambda v: view if v == blog_inbox.VIEW_DRAFTS else None)

    def client(signed_in):
        c = TestClient(main.app, base_url="https://testserver",
                       headers={auth_middleware.EDGE_HEADER: "1"},
                       raise_server_exceptions=False)
        if signed_in:
            c.cookies.set(auth_middleware.SESSION_COOKIE,
                          auth.mint_token(KEY, kind=auth.KIND_SESSION, epoch=EPOCH))
        return c

    return types.SimpleNamespace(owner=client(True), stranger=client(False),
                                 font_name=name, font=font, data_dir=data_dir)


def test_the_preview_route_serves_the_draft_under_the_entry_policy(served):
    r = served.owner.get(f"/blog/preview/{ID_A}/entry.html", follow_redirects=False)
    assert r.status_code == 200 and r.content == DOC
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert r.headers["content-security-policy"] == blog_inbox.ENTRY_CSP
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", [
    f"/blog/preview/{ID_B}/entry.html",                 # not a draft that is waiting
    f"/blog/preview/{ID_A}0/entry.html",                # an over-long id
    f"/blog/preview/{ID_A.upper()}/entry.html",
    "/blog/preview/fonts/entry.html",
    "/blog/preview/..%2e/entry.html",
    "/blog/preview/fonts/secret.txt",
    "/blog/preview/fonts/" + "0" * 20 + ".woff2",       # a good name, no such file
    "/blog/preview/fonts/" + "0" * 20 + ".ttf",
    "/blog/preview/fonts/" + "0" * 20 + ".woff2%0a",
])
def test_the_preview_routes_answer_a_plain_404_for_anything_else(served, path):
    r = served.owner.get(path, follow_redirects=False)
    assert r.status_code == 404
    assert r.text == "Not found"
    assert r.headers["content-type"].startswith("text/plain")
    assert "content-security-policy" not in r.headers


@pytest.mark.parametrize("path", [
    "/blog/preview/../../etc/entry.html",               # ..%2f..%2fetc, decoded
    "/blog/preview/fonts/../secret.txt",                # fonts/..%2fsecret.txt
    f"/blog/preview/{ID_A}/other.html",
    f"/blog/preview/{ID_A}/../{ID_A}/entry.html",
    f"/blog/preview/{ID_A}/entry.html/more",
    "/blog/preview/fonts/a/b.woff2",
])
def test_a_path_with_a_slash_in_the_id_matches_neither_preview_route(path):
    """A server hands the router the DECODED path, so ``%2f`` arrives as a
    slash - and a path parameter does not match across one. These never reach
    a handler. Asked of the routes' own patterns rather than by requesting the
    path: a request nothing matches is answered by NiceGUI's not-found page,
    which is not this page's to test (and building it here would leave a
    client behind for every later test in the run)."""
    import main
    routes = [r for r in main.app.routes
              if getattr(r, "path", "").startswith("/blog/preview/")]
    # By path, not by count: tests/test_deepdive_routes.py imports the shell as
    # ``webgui.main``, a second module object, which registers every one of its
    # routes on the shared app again whenever the whole folder is collected.
    assert {r.path for r in routes} == {"/blog/preview/fonts/{name}",
                                        "/blog/preview/{draft_id}/entry.html"}
    assert not [r.path for r in routes if r.path_regex.match(path)]
    # The control: the same patterns do match the two real shapes.
    assert [r.path for r in routes
            if r.path_regex.match(f"/blog/preview/{ID_A}/entry.html")]
    assert [r.path for r in routes
            if r.path_regex.match("/blog/preview/fonts/" + "0" * 20 + ".woff2")]


def test_the_fonts_route_serves_a_typeface(served):
    r = served.owner.get(f"/blog/preview/fonts/{served.font_name}",
                         follow_redirects=False)
    assert r.status_code == 200 and r.content == served.font
    assert r.headers["content-type"] == "font/woff2"
    assert r.headers["cache-control"] == "private, max-age=3600"


def test_the_fonts_route_is_registered_before_the_document_route():
    """``/blog/preview/{draft_id}/entry.html`` would not match a font's path,
    but the order is what the design states, and it is cheap to hold."""
    import main
    paths = [getattr(r, "path", "") for r in main.app.routes]
    assert paths.index("/blog/preview/fonts/{name}") < paths.index(
        "/blog/preview/{draft_id}/entry.html")


def test_both_preview_routes_refuse_a_stranger(served):
    """They are parameterised, so the sweep in test_auth_covers_every_route.py
    (which requests only concrete paths) does not reach them. Driven here on
    paths that DO serve the owner, so a 303 can only be the gate."""
    for path in (f"/blog/preview/{ID_A}/entry.html",
                 f"/blog/preview/fonts/{served.font_name}", "/blog"):
        r = served.stranger.get(path, follow_redirects=False)
        assert r.status_code == 303, path
        assert r.headers["location"].startswith("/login?next="), path
        assert DOC not in r.content and served.font not in r.content


def test_the_public_process_has_no_blog_route_and_no_blog_screen():
    import live_screens
    assert not [s for s in live_screens.SCREENS if "blog" in s.route]
    for name in ("live_main.py", "live_screens.py"):
        src = (PAGES.parent / name).read_text(encoding="utf-8")
        assert "blog" not in src.lower(), name


# ── the Tier-1 rule ──────────────────────────────────────────────────────────
def test_the_page_imports_no_service_and_no_engine():
    src = (PAGES / "blog.py").read_text(encoding="utf-8")
    for banned in ("services.", "sqlite3", "lxml", "tinycss2"):
        assert banned not in src, banned


def test_the_page_imports_only_what_tier_1_allows():
    import ast
    tree = ast.parse((PAGES / "blog.py").read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m and m.startswith(("services", "shared.notify", "requests", "main",
                                       "redis")) for m in mods), mods
    shared = {m for m in mods if m and m.startswith("shared")}
    assert shared <= {"shared", "shared.blog_inbox"}, shared


def test_the_page_never_says_slug_to_the_owner():
    """Every string the owner can read says "address"."""
    import ast
    tree = ast.parse((PAGES / "blog.py").read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant)}
    shown = [n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and id(n) not in docstrings and " " in n.value.strip()
             and "<q-" not in n.value]          # a Vue template names row fields
    assert len(shown) > 20, "found too few sentences - the scan is vacuous"
    assert not [s for s in shown if "slug" in s.lower()]


# ── render: driven the way the browser does ──────────────────────────────────
class _Page:
    """One rendered page, with everything it would have done recorded."""

    def __init__(self, monkeypatch, views=None, fail_request=False):
        from nicegui import ui

        import bus_client
        self.views = dict(views or {})
        self.sent, self.toasts, self.watch = [], [], {}
        monkeypatch.setattr(bus_client, "read", lambda view: self.views.get(view))
        monkeypatch.setattr(bus_client, "read_version", lambda view: 1)
        monkeypatch.setattr(bus_client, "read_meta", lambda view: (1, None))

        def request(domain, command):
            if fail_request:
                raise ConnectionError("no bus")
            self.sent.append((domain, command))
            return "1-0"

        monkeypatch.setattr(bus_client, "request", request)
        monkeypatch.setattr(page.kit, "toast",
                            lambda kind, text: self.toasts.append((kind, text)))
        monkeypatch.setattr(page, "watch_view",
                            lambda view, fn, **kw: self.watch.__setitem__(view, fn))
        monkeypatch.setattr(page, "SITE_HOST", "neuralstrike.co")
        with ui.card() as host:
            self._dialogs_before = {d.id for d in self._client_dialogs()}
            page.render()
        self.host = host

    # -- finding things ----------------------------------------------------
    def all(self, cls):
        return [e for e in self.host.descendants() if isinstance(e, cls)]

    def labels(self):
        from nicegui import ui
        return [e.text for e in self.all(ui.label)]

    def buttons(self, text):
        from nicegui import ui
        return [b for b in self.all(ui.button) if b.text == text]

    def inputs(self):
        from nicegui import ui
        return [e for e in self.all(ui.input) if not isinstance(e, ui.select)]

    def card_fields(self, n=0):
        """``(title, summary, address, tags)`` of the n-th draft card."""
        return tuple(self.inputs()[n * 4:n * 4 + 4])

    @staticmethod
    def _client_dialogs():
        from nicegui import context, ui
        return [d for d in context.client.layout.descendants() if isinstance(d, ui.dialog)]

    def dialogs(self):
        """THIS page's dialogs. Every test renders into one shared client, and
        a dialog is not a descendant of the element it was built under - so
        asking the client alone would also find the ones earlier tests left
        open."""
        return [d for d in self._client_dialogs() if d.id not in self._dialogs_before]

    def frame(self):
        frames = [e for d in self.dialogs() for e in d.descendants() if e.tag == "iframe"]
        return frames[-1]

    # -- doing things ------------------------------------------------------
    @staticmethod
    def click(btn):
        from nicegui import helpers
        from nicegui.events import GenericEventArguments
        e = GenericEventArguments(sender=btn, client=btn.client, args=None)
        for li in list(btn._event_listeners.values()):
            if li.type == "click" and li.handler is not None:
                li.handler(e) if helpers.expects_arguments(li.handler) else li.handler()

    def confirm(self, text):
        """Press the confirm whose button says ``text``, as Enter would."""
        from nicegui import ui
        dlg = [d for d in self.dialogs()
               if any(isinstance(b, ui.button) and b.text == text for b in d.descendants())
               and d.value][-1]
        (run_,) = [li.handler for li in dlg._event_listeners.values()
                   if li.type == "keydown.enter"]

        async def _drive(slot):
            with slot:
                await run_(None)

        asyncio.run(_drive(dlg.parent_slot))
        return dlg

    def open_confirms(self):
        return [d for d in self.dialogs() if d.value]

    def publish(self, view, payload):
        self.views[view] = payload
        self.watch[view]()

    def upload(self, name, data):
        from nicegui import ui
        (up,) = self.all(ui.upload)

        class _File:
            def __init__(self):
                self.name = name

            async def read(self):
                return data

        event = types.SimpleNamespace(file=_File(), sender=up)

        async def _drive(slot):
            with slot:
                for handler in up._upload_handlers:
                    await handler(event)

        asyncio.run(_drive(up.parent_slot))
        return up

    def fire(self, element, event, args):
        from nicegui.events import GenericEventArguments
        e = GenericEventArguments(sender=element, client=element.client, args=args)
        for li in list(element._event_listeners.values()):
            if li.type == event and li.handler is not None:
                li.handler(e)


TWO_DRAFTS = {"drafts": [
    _draft(ID_A, revises="an-older-entry", slug="an-older-entry", title="An older entry, redone"),
    _draft(ID_B, removed={"script": 2, "handler": 3},
           font_note="One typeface could not be copied, so the entry falls back to a system face.")]}
THREE_ENTRIES = {"entries": [_entry("newest-entry", title="Newest entry"),
                             _entry("an-older-entry"),
                             _entry("the-oldest", title="The oldest")]}


def _views(drafts=None, entries=None):
    return {blog_inbox.VIEW_DRAFTS: drafts, blog_inbox.VIEW_POSTS: entries}


def test_an_empty_page_says_so_twice(monkeypatch):
    p = _Page(monkeypatch)
    labels = p.labels()
    assert "No drafts are waiting." in labels
    assert "Nothing has been published yet." in labels
    assert set(p.watch) == {blog_inbox.VIEW_DRAFTS, blog_inbox.VIEW_POSTS,
                            blog_inbox.VIEW_RESULT}


def test_junk_views_render_as_an_empty_page(monkeypatch):
    p = _Page(monkeypatch, _views({"drafts": "x"}, ["not", "a", "dict"]))
    assert "No drafts are waiting." in p.labels()
    assert "Nothing has been published yet." in p.labels()


def test_a_draft_card_says_where_it_came_from_and_what_was_removed(monkeypatch):
    p = _Page(monkeypatch, _views(TWO_DRAFTS, THREE_ENTRIES))
    text = " | ".join(p.labels())
    assert "Uploaded file" in text and "35 KB" in text and "Oct 6, 2:03 PM" in text
    assert "Removed 2 scripts and 1 form." in text
    assert "Removed 2 scripts and 3 event handlers." in text
    assert "One typeface could not be copied" in text
    assert "Replaces An older entry" in text
    assert "No drafts are waiting." not in text
    title, summary, address, tags = p.card_fields(1)
    assert title.value == "A first entry" and summary.value == "What it says."
    assert address.value == "a-first-entry" and tags.value == "gamma, vol"
    assert "https://neuralstrike.co/blog/a-first-entry/" in text


def test_the_address_of_a_replacement_cannot_be_edited(monkeypatch):
    p = _Page(monkeypatch, _views(TWO_DRAFTS, THREE_ENTRIES))
    replacing, fresh = p.card_fields(0)[2], p.card_fields(1)[2]
    assert replacing.value == "an-older-entry"
    assert replacing._props.get("readonly") is True
    assert not fresh._props.get("readonly")


def test_publish_is_held_while_the_title_is_blank_or_the_address_unusable(monkeypatch):
    p = _Page(monkeypatch, _views({"drafts": [_draft()]}, THREE_ENTRIES))
    (publish,) = p.buttons("Publish")
    title, _summary, address, _tags = p.card_fields()
    assert publish.enabled
    address.value = "Not an address!"
    assert not publish.enabled
    assert page.address_problem("Not an address!") in p.labels()
    address.value = "a-better-one"
    assert publish.enabled
    assert page.address_problem("Not an address!") not in [t for t in p.labels() if t]
    assert "https://neuralstrike.co/blog/a-better-one/" in " ".join(p.labels())
    title.value = "   "
    assert not publish.enabled
    title.value = "A title"
    assert publish.enabled


def test_a_blank_title_says_why_publish_is_held_the_way_a_bad_address_does(monkeypatch):
    """Publish greyed out with nothing on the card saying why was the fault:
    the address had its sentence and the title had none."""
    from nicegui import ui
    p = _Page(monkeypatch, _views({"drafts": [_draft()]}, THREE_ENTRIES))
    (publish,) = p.buttons("Publish")
    title, _summary, address, _tags = p.card_fields()

    def reason():
        """The card's one red line, when it is showing."""
        return [e.text for e in p.all(ui.label)
                if e.visible and e.text in (page.NO_TITLE, page.address_problem(address.value))
                and e.text]

    assert publish.enabled and reason() == []
    title.value = ""
    assert not publish.enabled and reason() == [page.NO_TITLE]
    # The public address is still shown: the address itself is fine.
    assert "https://neuralstrike.co/blog/a-first-entry/" in " ".join(p.labels())
    title.value = "A title again"
    assert publish.enabled and reason() == []
    # The text and the held state are one verdict, whichever field is wrong.
    for typed_title, typed_address in (("", "a-first-entry"), ("T", "Not an address!"),
                                       ("", "Not an address!"), ("T", "fine")):
        title.value, address.value = typed_title, typed_address
        why = page.publish_problem(typed_title, typed_address)
        assert publish.enabled == (why == "")
        assert reason() == ([why] if why else [])


def test_publish_asks_first_and_sends_the_fields_as_edited(monkeypatch):
    p = _Page(monkeypatch, _views({"drafts": [_draft()]}, THREE_ENTRIES))
    title, summary, address, tags = p.card_fields()
    title.value, summary.value = "Edited title", "Edited summary"
    address.value, tags.value = "edited-address", "one, two"
    (publish,) = p.buttons("Publish")
    p.click(publish)
    assert not p.sent, "nothing is sent before the owner confirms"
    dlg = p.confirm("Publish")
    words = " ".join(e.text for e in dlg.descendants() if hasattr(e, "text") and e.text)
    assert "at once" in words and "https://neuralstrike.co/blog/edited-address/" in words
    ((domain, command),) = p.sent
    assert domain == blog_inbox.OWNER_DOMAIN == "blog"
    assert command["type"] == "publish"
    args = command["args"]
    assert args["draft_id"] == ID_A and blog_inbox.is_id(args["request_id"])
    assert args["fields"] == {"title": "Edited title", "summary": "Edited summary",
                              "slug": "edited-address", "tags": ["one", "two"]}
    assert set(args) == {"request_id", "draft_id", "fields"}
    assert not publish.enabled                  # held until the service answers


def test_discard_asks_first_and_sends_only_the_draft(monkeypatch):
    p = _Page(monkeypatch, _views({"drafts": [_draft()]}))
    (discard,) = p.buttons("Discard")
    p.click(discard)
    assert not p.sent
    p.confirm("Discard")
    ((domain, command),) = p.sent
    assert domain == "blog" and command["type"] == "discard"
    assert set(command["args"]) == {"request_id", "draft_id"}
    assert command["args"]["draft_id"] == ID_A


def test_each_cards_buttons_act_on_their_own_draft(monkeypatch):
    p = _Page(monkeypatch, _views(TWO_DRAFTS, THREE_ENTRIES))
    p.click(p.buttons("Discard")[1])
    p.confirm("Discard")
    p.click(p.buttons("Publish")[0])
    p.confirm("Publish")
    assert [(c["type"], c["args"]["draft_id"]) for _d, c in p.sent] == [
        ("discard", ID_B), ("publish", ID_A)]


def test_what_was_typed_survives_a_repaint_of_the_same_draft(monkeypatch):
    p = _Page(monkeypatch, _views({"drafts": [_draft(ID_A)]}))
    title, _summary, address, _tags = p.card_fields()
    title.value, address.value = "Half typed", "half-typed"
    # A second draft arrives; the first is repainted with it.
    p.publish(blog_inbox.VIEW_DRAFTS, {"drafts": [
        _draft(ID_B, title="Newer"), _draft(ID_A, summary="The service changed this.")]})
    assert len(p.buttons("Publish")) == 2
    newer, kept = p.card_fields(0), p.card_fields(1)
    assert newer[0].value == "Newer"
    assert kept[0].value == "Half typed" and kept[2].value == "half-typed"
    # ...and a field the owner did NOT touch follows the draft.
    assert kept[1].value == "The service changed this."
    # Once the draft is gone, so is what was typed for it.
    p.publish(blog_inbox.VIEW_DRAFTS, {"drafts": [_draft(ID_B, title="Newer")]})
    p.publish(blog_inbox.VIEW_DRAFTS, {"drafts": [_draft(ID_A), _draft(ID_B, title="Newer")]})
    assert p.card_fields(0)[0].value == "A first entry"


def test_an_unchanged_view_does_not_rebuild_the_cards_under_the_owner(monkeypatch):
    """The service republishes its views on a timer. Rebuilding a card takes
    the cursor out of the field being typed in."""
    p = _Page(monkeypatch, _views({"drafts": [_draft()]}, THREE_ENTRIES))
    before = p.card_fields()
    p.publish(blog_inbox.VIEW_DRAFTS, {"drafts": [_draft()]})
    p.publish(blog_inbox.VIEW_POSTS, dict(THREE_ENTRIES))
    assert [f.id for f in p.card_fields()] == [f.id for f in before]


def test_the_services_answer_is_shown_as_it_is_and_releases_the_button(monkeypatch):
    p = _Page(monkeypatch, _views({"drafts": [_draft()]}))
    (publish,) = p.buttons("Publish")
    p.click(publish)
    p.confirm("Publish")
    request_id = p.sent[0][1]["args"]["request_id"]
    p.toasts.clear()
    message = "That address is already used by another entry."
    p.publish(blog_inbox.VIEW_RESULT, {"request_id": request_id, "command": "publish",
                                       "ok": False, "message": message,
                                       "draft_id": ID_A, "slug": ""})
    assert p.toasts == [("warn", message)]
    assert publish.enabled
    # The same answer read twice is reported once.
    p.watch[blog_inbox.VIEW_RESULT]()
    assert p.toasts == [("warn", message)]


def test_a_good_answer_is_an_info_toast(monkeypatch):
    p = _Page(monkeypatch, _views({"drafts": [_draft()]}))
    p.click(p.buttons("Discard")[0])
    p.confirm("Discard")
    request_id = p.sent[0][1]["args"]["request_id"]
    p.toasts.clear()
    p.publish(blog_inbox.VIEW_RESULT, {"request_id": request_id, "command": "discard",
                                       "ok": True, "message": "The draft was discarded."})
    assert p.toasts == [("info", "The draft was discarded.")]


@pytest.mark.parametrize("answer", [
    {"request_id": ID_C, "command": "publish", "ok": True, "message": "Someone else's."},
    {"request_id": None, "ok": True, "message": "No id."},
    {"ok": True}, None, [], "text", {"request_id": ["x"]}])
def test_an_answer_to_a_request_this_page_did_not_send_is_ignored(monkeypatch, answer):
    p = _Page(monkeypatch, _views({"drafts": [_draft()]}))
    (publish,) = p.buttons("Publish")
    p.click(publish)
    p.confirm("Publish")
    p.toasts.clear()
    p.publish(blog_inbox.VIEW_RESULT, answer)
    assert p.toasts == []
    assert not publish.enabled                  # still waiting for its own answer


def test_a_request_that_cannot_be_sent_says_so_and_holds_nothing(monkeypatch):
    p = _Page(monkeypatch, _views({"drafts": [_draft()]}), fail_request=True)
    (publish,) = p.buttons("Publish")
    p.click(publish)
    p.confirm("Publish")
    assert p.toasts == [("error", "Could not reach the blog service.")]
    assert publish.enabled


def _later(monkeypatch, seconds):
    """Move the clock the page and the kit both read."""
    import time
    at = time.monotonic() + seconds
    monkeypatch.setattr(time, "monotonic", lambda: at)


def test_a_button_whose_answer_never_comes_is_handed_back(monkeypatch):
    """The kit's backstop, through the page's own gate: the button comes back,
    and comes back only as far as the fields allow."""
    p = _Page(monkeypatch, _views({"drafts": [_draft()]}))
    (publish,) = p.buttons("Publish")
    (discard,) = p.buttons("Discard")
    p.click(publish)
    p.confirm("Publish")
    assert not publish.enabled and not discard.enabled
    publish._kit_busy["tick"]()                 # the backstop's own timer, early
    assert not publish.enabled
    _later(monkeypatch, page.kit.BUSY_TIMEOUT_SEC + 1)
    p.card_fields()[0].value = ""               # the title was cleared meanwhile
    publish._kit_busy["tick"]()
    assert discard.enabled
    assert not publish.enabled, "a blank title must not be handed a live Publish"
    p.card_fields()[0].value = "A title again"
    assert publish.enabled
    # A late answer is still reported.
    request_id = p.sent[0][1]["args"]["request_id"]
    p.toasts.clear()
    p.publish(blog_inbox.VIEW_RESULT, {"request_id": request_id, "ok": True,
                                       "message": "Published."})
    assert p.toasts == [("info", "Published.")]


def test_a_card_rebuilt_while_its_command_is_out_is_rebuilt_held(monkeypatch):
    p = _Page(monkeypatch, _views({"drafts": [_draft(ID_A)]}))
    p.click(p.buttons("Discard")[0])
    p.confirm("Discard")
    p.publish(blog_inbox.VIEW_DRAFTS, {"drafts": [_draft(ID_B), _draft(ID_A)]})
    other, held = p.buttons("Discard")
    assert other.enabled and not held.enabled
    assert p.buttons("Publish")[0].enabled and not p.buttons("Publish")[1].enabled
    request_id = p.sent[0][1]["args"]["request_id"]
    p.publish(blog_inbox.VIEW_RESULT, {"request_id": request_id, "ok": False,
                                       "message": "It could not be discarded."})
    assert p.buttons("Discard")[1].enabled and p.buttons("Publish")[1].enabled


def test_a_held_row_whose_answer_never_comes_is_handed_back(monkeypatch):
    from nicegui import ui
    p = _Page(monkeypatch, _views(None, THREE_ENTRIES))
    (holds,) = [t for t in p.all(ui.timer)
                if getattr(t.callback, "__name__", "") == "_check_holds"]
    assert not holds.active
    (table,) = p.all(ui.table)
    p.fire(table, "unpublish", "the-oldest")
    p.confirm("Unpublish")
    assert holds.active
    holds.callback()
    (table,) = p.all(ui.table)
    assert [r["_busy"] for r in table.rows] == [False, False, True]
    _later(monkeypatch, page.kit.BUSY_TIMEOUT_SEC + 1)
    holds.callback()
    (table,) = p.all(ui.table)
    assert not any(r["_busy"] for r in table.rows)
    assert not holds.active


# -- upload --
HTML ="<!doctype html><html><head><title>Up</title></head><body><p>Hello</p></body></html>"


def test_an_upload_is_filed_as_a_draft(monkeypatch):
    p = _Page(monkeypatch, _views(None, THREE_ENTRIES))
    up = p.upload("entry.html", HTML.encode("utf-8"))
    ((domain, command),) = p.sent
    assert domain == "blog" and command["type"] == blog_inbox.SUBMIT_TYPE
    args = command["args"]
    assert args["html"] == HTML and args["source"] == "upload"
    assert blog_inbox.is_id(args["request_id"])
    assert "revises" not in args
    assert not up.enabled                       # held until the service answers
    assert p.toasts and p.toasts[-1][0] == "info"
    # The answer releases it.
    p.publish(blog_inbox.VIEW_RESULT, {"request_id": args["request_id"], "ok": True,
                                       "command": "draft_submit",
                                       "message": "The draft is ready to preview."})
    assert up.enabled
    assert p.toasts[-1] == ("info", "The draft is ready to preview.")


def test_the_upload_takes_html_only_up_to_the_configured_size(monkeypatch):
    from nicegui import ui
    p = _Page(monkeypatch)
    (up,) = p.all(ui.upload)
    assert up._props["max-file-size"] == blog_inbox.limits()["max_html_kb"] * 1024
    assert up._props["accept"] == ".html,.htm,text/html"
    assert up._props.get("auto-upload") is True and not up._props.get("multiple")


def test_an_upload_can_replace_an_entry(monkeypatch):
    from nicegui import ui
    p = _Page(monkeypatch, _views(None, THREE_ENTRIES))
    (choice,) = p.all(ui.select)
    assert choice.value == page.NEW_ENTRY
    assert choice.options[page.NEW_ENTRY] == "No, this is a new entry"
    assert list(choice.options.values())[1:] == ["Newest entry", "An older entry",
                                                 "The oldest"]
    choice.value = "an-older-entry"
    p.upload("entry.html", HTML.encode("utf-8"))
    ((_domain, command),) = p.sent
    assert command["args"]["revises"] == "an-older-entry"
    assert choice.value == page.NEW_ENTRY       # one upload, one replacement


def test_the_upload_command_is_the_shared_builders_own(monkeypatch):
    """The page hands the entry it replaces to ``submit_command`` and sends
    what comes back, untouched: it never writes into the command's arguments
    itself, so the builder's check of that address cannot be stepped around."""
    from nicegui import ui
    built = []
    real = blog_inbox.submit_command

    def spy(html, fields, **named):
        command = real(html, fields, **named)
        built.append((named, command, repr(command)))
        return command

    monkeypatch.setattr(blog_inbox, "submit_command", spy)
    p = _Page(monkeypatch, _views(None, THREE_ENTRIES))
    (choice,) = p.all(ui.select)

    choice.value = "an-older-entry"
    p.upload("entry.html", HTML.encode("utf-8"))
    p.upload("entry.html", HTML.encode("utf-8"))        # the choice went back to "new"

    (first, second) = built
    assert first[0]["revises"] == "an-older-entry" and first[0]["source"] == "upload"
    assert second[0].get("revises") is None
    assert [command for _domain, command in p.sent] == [first[1], second[1]]
    assert p.sent[0][1] is first[1] and repr(first[1]) == first[2]   # sent as built
    assert "revises" not in second[1]["args"]


def test_an_upload_the_builder_refuses_is_not_sent(monkeypatch):
    monkeypatch.setattr(blog_inbox, "submit_command", lambda *a, **k: None)
    p = _Page(monkeypatch, _views(None, THREE_ENTRIES))
    p.upload("entry.html", HTML.encode("utf-8"))
    assert not p.sent and [k for k, _t in p.toasts] == ["warn"]


def test_an_upload_naming_an_entry_that_is_gone_is_refused_not_filed_as_new(monkeypatch):
    from nicegui import ui
    p = _Page(monkeypatch, _views(None, THREE_ENTRIES))
    (choice,) = p.all(ui.select)
    choice.value = "an-older-entry"
    p.views[blog_inbox.VIEW_POSTS] = {"entries": [_entry("newest-entry")]}   # not yet repainted
    p.upload("entry.html", HTML.encode("utf-8"))
    assert not p.sent
    assert [k for k, _t in p.toasts] == ["warn"]


# Named, and built inside the test: pytest writes a parameter's repr into an
# environment variable, and half a megabyte of it is more than Windows allows.
_NOT_DOCUMENTS = {
    "utf-16": lambda: HTML.encode("utf-16"),
    "binary": lambda: bytes(range(256)),
    "empty": lambda: b"",
    "blank": lambda: b"   \n  ",
    "one byte too large": lambda: b"x" * (blog_inbox.limits()["max_html_kb"] * 1024 + 1),
}


@pytest.mark.parametrize("kind", sorted(_NOT_DOCUMENTS))
def test_a_file_that_cannot_be_a_document_is_refused_in_plain_words(monkeypatch, kind):
    from nicegui import ui
    p = _Page(monkeypatch)
    p.upload("entry.html", _NOT_DOCUMENTS[kind]())
    assert not p.sent
    ((kind, text),) = p.toasts
    assert kind == "warn" and text.endswith(".") and len(text) > 20
    (up,) = p.all(ui.upload)
    assert up.enabled


def test_an_upload_that_cannot_be_sent_says_so(monkeypatch):
    from nicegui import ui
    p = _Page(monkeypatch, fail_request=True)
    p.upload("entry.html", HTML.encode("utf-8"))
    assert p.toasts == [("error", "Could not reach the blog service.")]
    (up,) = p.all(ui.upload)
    assert up.enabled


# -- preview --
def test_preview_frames_the_draft_with_no_scripts(monkeypatch):
    p = _Page(monkeypatch, _views(TWO_DRAFTS, THREE_ENTRIES))
    frame = p.frame()
    assert frame._props["sandbox"] == blog_inbox.ENTRY_SANDBOX
    assert "allow-scripts" not in frame._props["sandbox"]
    assert frame._props["title"]
    assert frame._props["src"] == "about:blank"
    assert any(c.startswith("h-[") for c in frame.classes), "the frame needs a height"
    p.click(p.buttons("Preview")[1])
    assert frame._props["src"] == page.preview_src(ID_B)
    dlg = [d for d in p.dialogs() if frame in list(d.descendants())][0]
    assert dlg.value
    dlg.close()
    assert frame._props["src"] == "about:blank"
    p.click(p.buttons("Preview")[0])
    assert frame._props["src"] == page.preview_src(ID_A)


# -- published --
def test_the_published_table_lists_entries_with_their_addresses(monkeypatch):
    from nicegui import ui
    p = _Page(monkeypatch, _views(None, THREE_ENTRIES))
    (table,) = p.all(ui.table)
    assert [r["title"] for r in table.rows] == ["Newest entry", "An older entry", "The oldest"]
    assert table.rows[0]["url"] == "https://neuralstrike.co/blog/newest-entry/"
    assert [c["label"] for c in table.columns][:3] == ["Title", "Published", "Address"]
    slot = table.slots["body-cell-actions"].template
    assert 'rel="noopener"' in slot and 'target="_blank"' in slot
    assert "Open on the site" in slot and "Unpublish" in slot
    assert "Nothing has been published yet." not in p.labels()


def test_unpublish_acts_on_the_row_it_was_clicked_in(monkeypatch):
    from nicegui import ui
    p = _Page(monkeypatch, _views(None, THREE_ENTRIES))
    (table,) = p.all(ui.table)
    assert not table.selected
    p.fire(table, "unpublish", "the-oldest")
    assert not p.sent
    dlg = p.confirm("Unpublish")
    words = " ".join(e.text for e in dlg.descendants() if hasattr(e, "text") and e.text)
    assert "The oldest" in words and "removed from the public site" in words
    assert "https://neuralstrike.co/blog/the-oldest/" in words and "stops working" in words
    ((domain, command),) = p.sent
    assert domain == "blog"
    assert command == {"type": "unpublish",
                       "args": {"request_id": command["args"]["request_id"],
                                "slug": "the-oldest"}}
    # Held: the row's own button is disabled until the service answers.
    (table,) = p.all(ui.table)
    busy = {r["slug"]: r["_busy"] for r in table.rows}
    assert busy == {"newest-entry": False, "an-older-entry": False, "the-oldest": True}
    p.publish(blog_inbox.VIEW_RESULT, {"request_id": command["args"]["request_id"],
                                       "ok": False, "message": "It could not be removed."})
    (table,) = p.all(ui.table)
    assert not any(r["_busy"] for r in table.rows)


@pytest.mark.parametrize("args", ["not-a-published-entry", "../x", None, 5, {"slug": "the-oldest"},
                                  ["the-oldest"]])
def test_unpublish_ignores_a_row_the_page_did_not_draw(monkeypatch, args):
    """The event comes from the browser. Only an address in the list the page
    is showing can be asked about."""
    from nicegui import ui
    p = _Page(monkeypatch, _views(None, THREE_ENTRIES))
    (table,) = p.all(ui.table)
    p.fire(table, "unpublish", args)
    assert not p.open_confirms() and not p.sent


def test_the_replace_choice_follows_the_published_list(monkeypatch):
    from nicegui import ui
    p = _Page(monkeypatch, _views(None, THREE_ENTRIES))
    (choice,) = p.all(ui.select)
    choice.value = "the-oldest"
    p.publish(blog_inbox.VIEW_POSTS, {"entries": [_entry("newest-entry", title="Newest entry"),
                                                  _entry("the-oldest", title="The oldest")]})
    assert list(choice.options) == [page.NEW_ENTRY, "newest-entry", "the-oldest"]
    assert choice.value == "the-oldest"         # still there: the choice is kept
    p.publish(blog_inbox.VIEW_POSTS, {"entries": [_entry("newest-entry")]})
    assert choice.value == page.NEW_ENTRY       # gone: back to a new entry
    p.publish(blog_inbox.VIEW_POSTS, None)
    assert "Nothing has been published yet." in p.labels()
