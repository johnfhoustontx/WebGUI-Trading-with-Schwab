"""``cmd:blog``: upload, publish, discard, unpublish - and what the page is told.

The store is the REAL one, on this test's own folder (the conftest redirect),
and so is the site writer, on this test's own copy of the site. What is faked:

* the cleaner's worker process. ``fast_clean`` (autouse) runs ``clean.clean``
  in this process instead, because the worker costs a third of a second a
  document; ``test_the_real_worker_cleans_an_upload`` puts the real one back.
* the typeface fetch. ``no_typefaces`` (autouse) makes ``fonts.localize`` copy
  nothing; the tests about typefaces hand it a fake web. Either way nothing
  here reaches the network, and the conftest guard fails the test if it tries.

A refusal is checked three ways: the sentence, that NOTHING was stored or
written, and that the handler returned normally.
"""
import json

import pytest

from services import _degrade
from services.blog_svc import clean, clean_bound, fonts, handlers, sitewriter
from services.blog_svc import store as store_mod
from services.blog_svc.tests import _fonts_kit as kit
from shared import blog_inbox
from shared.bus import Bus
from shared.contracts.envelope import Command

MARKER = "MARKER-in-an-exception-4409"
REAL_CLEAN_BOUNDED = clean_bound.clean_bounded
REAL_LOCALIZE = fonts.localize

DOC = ('<!doctype html><html><head><title>Why spreads work</title></head><body>'
       '<h1>Why spreads work</h1><p>A first paragraph that says something.</p>'
       '<script>alert(1)</script><p onclick="x()">Second.</p></body></html>')
DOC_WITH_FONT = DOC.replace(
    "</head>", f'<link href="{kit.LINK}" rel="stylesheet"></head>')
OTHER_DOC = ('<!doctype html><html><head><title>A second look</title></head><body>'
             '<h1>A second look</h1><p>Different words this time.</p></body></html>')

# The store's codes as the brief fixes them; ``store.REFUSAL_CODES`` is checked
# too wherever the store already has it.
STORE_CODES = ("no_draft", "no_title", "slug_taken", "slug_changed", "document_missing",
               "bad_name", "bad_input", "busy")


class Refused(ValueError):
    """Shaped like ``store.StoreRefusal``: a ``ValueError`` carrying a code.
    Its TEXT is the marker, so a handler that showed it would be caught."""

    def __init__(self, code):
        super().__init__(MARKER)
        self.code = code


# ── fixtures and helpers ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fast_clean(monkeypatch):
    calls = []

    def bounded(html, **_kw):
        calls.append(html)
        return clean.clean(html)

    monkeypatch.setattr(clean_bound, "clean_bounded", bounded)
    return calls


@pytest.fixture(autouse=True)
def no_typefaces(monkeypatch):
    asked = []

    def localize(font_links, **_kw):
        asked.append(tuple(font_links))
        return fonts.Fonts("", {}, "")

    monkeypatch.setattr(fonts, "localize", localize)
    return asked


@pytest.fixture
def bus():
    return Bus()


@pytest.fixture
def site():
    return sitewriter.SITE_ROOT


def view(bus, name):
    env = bus.cache_get(f"cache:{name}")
    return None if env is None else env.payload


def result(bus):
    return view(bus, blog_inbox.VIEW_RESULT)


def everything_on(bus) -> str:
    """Every string the fake Redis holds, joined: what any reader could see."""
    raw = bus._r
    return "\n".join(str(raw.get(key)) for key in sorted(raw.keys("*"))
                     if raw.type(key) == "string")


def send(bus, kind, **args) -> dict:
    """Put one command through the handler as the scaffold would, and return
    its answer. A fresh request id unless one is given."""
    args.setdefault("request_id", blog_inbox.new_id())
    handlers.handle_command(bus, Command(type=kind, args=args))
    answer = result(bus)
    assert answer is not None and answer["request_id"] == args["request_id"]
    return answer


def upload(bus, html=DOC, fields=None, revises=None, source="upload") -> dict:
    rid = blog_inbox.new_id()
    command = blog_inbox.submit_command(html, fields or {}, source=source, request_id=rid)
    assert command is not None
    if revises is not None:
        command["args"]["revises"] = revises
    handlers.handle_command(bus, Command(**command))
    answer = result(bus)
    assert answer["request_id"] == rid
    return answer


def publish(bus, draft_id, **fields) -> dict:
    with store_mod.Store() as st:
        draft = st.draft(draft_id)
    filled = {"title": draft["title"], "summary": draft["summary"], "tags": draft["tags"],
              "slug": draft["slug"]}
    filled.update(fields)
    return send(bus, "publish", draft_id=draft_id, fields=filled)


def published(bus, html=DOC, **fields) -> str:
    """Upload and publish; the entry's address."""
    filed = upload(bus, html)
    assert filed["ok"], filed
    done = publish(bus, filed["draft_id"], **fields)
    assert done["ok"], done
    return done["slug"]


def stored() -> tuple:
    with store_mod.Store() as st:
        return st.drafts(), st.entries()


def site_files(site) -> set:
    return {path.relative_to(site).as_posix() for path in site.rglob("*") if path.is_file()}


def degrades(area) -> int:
    return _degrade.counts().get(area, 0)


def a_sentence(text) -> bool:
    return (isinstance(text, str) and len(text) > 10 and text[0].isupper()
            and text.endswith(".") and "<" not in text and MARKER not in text
            and "Traceback" not in text and "\\" not in text and "/" not in text)


# ── upload ───────────────────────────────────────────────────────────────────

def test_an_upload_becomes_a_draft_and_never_an_entry(bus, site, fast_clean):
    answer = upload(bus, fields={"tags": ["Options", "spreads"]})

    assert answer["ok"] is True and answer["command"] == "draft_submit"
    assert answer["message"] == handlers.MESSAGES["filed"]
    assert blog_inbox.is_id(answer["draft_id"]) and answer["slug"] == "why-spreads-work"
    drafts, entries = stored()
    assert entries == []
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft["id"] == answer["draft_id"]
    assert (draft["title"], draft["slug"]) == ("Why spreads work", "why-spreads-work")
    assert draft["summary"] == "A first paragraph that says something."
    assert draft["tags"] == ["Options", "spreads"]
    assert draft["source"] == "upload" and draft["revises"] is None
    assert draft["removed"].get("script") == 1
    with store_mod.Store() as st:
        staged = st.draft_html(draft["id"])
    assert "<script" not in staged and "onclick" not in staged and "Second." in staged
    assert clean.FONT_CSS_MARK not in staged          # the font slot was filled
    assert fast_clean == [DOC]
    # Nothing public: the site is as the conftest left it.
    assert site_files(site) == {"blog.html"}
    assert view(bus, blog_inbox.VIEW_POSTS) == {"entries": []}
    rows = view(bus, blog_inbox.VIEW_DRAFTS)["drafts"]
    assert len(rows) == 1 and set(rows[0]) == set(handlers.DRAFT_KEYS)
    assert rows[0]["id"] == draft["id"] and rows[0]["revises"] == ""
    assert rows[0]["digest"] == draft["digest"] and rows[0]["bytes"] == draft["bytes"]


def test_the_operators_fields_win_over_the_documents(bus):
    answer = upload(bus, fields={"title": "My own title", "summary": "My own summary",
                                 "slug": "my-own-address"})
    assert answer["slug"] == "my-own-address"
    draft = stored()[0][0]
    assert (draft["title"], draft["summary"], draft["slug"]) == (
        "My own title", "My own summary", "my-own-address")


def test_a_draft_is_an_upload_whatever_the_command_claims(bus):
    assert upload(bus, source="chat")["ok"] is True
    assert stored()[0][0]["source"] == "upload"


def test_the_typefaces_are_applied_once_and_nothing_cleans_after(bus, monkeypatch, fast_clean):
    web = kit.Web({kit.LINK: kit.sheet(kit.face("latin", kit.G + "a.woff2")),
                   kit.G + "a.woff2": kit.A})
    monkeypatch.setattr(fonts, "localize", lambda links, **kw: REAL_LOCALIZE(links, fetch=web))
    applied = []
    real_apply = fonts.apply
    monkeypatch.setattr(fonts, "apply", lambda html, copied: applied.append(1)
                        or real_apply(html, copied))

    answer = upload(bus, DOC_WITH_FONT)

    assert answer["ok"] is True
    assert len(applied) == 1 and len(fast_clean) == 1
    name = kit.name_of(kit.A)
    draft = stored()[0][0]
    assert draft["fonts"] == [name] and draft["font_links"] == [kit.LINK]
    with store_mod.Store() as st:
        staged = st.draft_html(draft["id"])
        assert st.font_bytes(name) == kit.A
    assert f"url(../fonts/{name})" in staged and "fonts.googleapis.com" not in staged


def test_the_real_worker_cleans_an_upload(bus, monkeypatch):
    """Once through the worker process the service really uses."""
    monkeypatch.setattr(clean_bound, "clean_bounded", REAL_CLEAN_BOUNDED)
    answer = upload(bus)
    assert answer["ok"] is True, answer
    draft = stored()[0][0]
    assert draft["title"] == "Why spreads work" and draft["removed"].get("script") == 1


def test_the_handler_asks_the_bounded_cleaner_never_the_bare_one(bus, monkeypatch, fast_clean):
    def bare(html):
        raise AssertionError("the service must clean in the worker, never in-process")

    def bounded(html, **_kw):
        fast_clean.append(html)
        return clean.Cleaned(*_CLEANED)

    _CLEANED = tuple(getattr(clean.clean(DOC), name) for name in (
        "html", "title", "summary", "removed", "font_links", "reason"))
    monkeypatch.setattr(clean, "clean", bare)
    monkeypatch.setattr(clean_bound, "clean_bounded", bounded)
    assert upload(bus)["ok"] is True
    assert fast_clean == [DOC]


@pytest.mark.parametrize("code", clean.REFUSALS)
def test_each_cleaner_refusal_is_answered_with_its_sentence(bus, monkeypatch, code, site):
    monkeypatch.setattr(clean_bound, "clean_bounded", lambda html, **kw: clean.refusal(code))

    answer = upload(bus)

    assert answer["ok"] is False and answer["command"] == "draft_submit"
    assert answer["message"] == handlers.MESSAGES[code]
    assert answer["draft_id"] == "" and answer["slug"] == ""
    assert stored() == ([], [])
    assert site_files(site) == {"blog.html"}


def test_a_document_with_nothing_left_is_refused(bus):
    answer = upload(bus, "<!doctype html><html><body><script>only()</script></body></html>")
    assert answer["ok"] is False and answer["message"] == handlers.MESSAGES["empty"]
    assert stored() == ([], [])


@pytest.mark.parametrize("html", ["", "   ", None, 7, ["<p>x</p>"]])
def test_a_document_that_is_not_one_is_refused_before_cleaning(bus, fast_clean, html):
    answer = send(bus, "draft_submit", source="upload", html=html, fields={})
    assert answer["ok"] is False
    assert answer["message"] == handlers.MESSAGES["bad_document"].format(
        kb=blog_inbox.limits()["max_html_kb"])
    assert a_sentence(answer["message"])
    assert fast_clean == [] and stored() == ([], [])


def test_a_document_over_the_limit_is_refused_before_cleaning(bus, fast_clean, monkeypatch):
    real = blog_inbox.limits
    monkeypatch.setattr(blog_inbox, "limits", lambda: dict(real(), max_html_kb=1))
    answer = send(bus, "draft_submit", source="upload", html="<p>" + "x" * 2000 + "</p>",
                  fields={})
    assert answer["ok"] is False and "1 KB" in answer["message"]
    assert fast_clean == [] and stored() == ([], [])


def test_the_draft_limit_refuses_before_cleaning(bus, monkeypatch, fast_clean):
    real = blog_inbox.limits
    monkeypatch.setattr(blog_inbox, "limits", lambda: dict(real(), max_drafts=2))
    assert upload(bus)["ok"] and upload(bus, OTHER_DOC)["ok"]
    del fast_clean[:]

    answer = upload(bus, fields={"slug": "a-third"})

    assert answer["ok"] is False
    assert answer["message"] == handlers.MESSAGES["full"].format(count=2)
    assert a_sentence(answer["message"])
    assert fast_clean == [] and len(stored()[0]) == 2


def test_an_oversized_staged_document_is_refused(bus, monkeypatch):
    real = blog_inbox.limits
    monkeypatch.setattr(blog_inbox, "limits", lambda: dict(real(), max_html_kb=64))
    bound = (handlers.STAGED_GROWTH * 64 + handlers.STAGED_SLACK_KB) * 1024
    sizes = iter([bound + 1, bound])
    real_apply = fonts.apply

    def padded(html, copied):
        out = real_apply(html, copied)
        return out + " " * (next(sizes) - len(out.encode("utf-8")))

    monkeypatch.setattr(fonts, "apply", padded)

    answer = upload(bus)
    assert answer["ok"] is False and answer["message"] == handlers.MESSAGES["too_large"]
    assert stored() == ([], [])
    # One byte less is kept: the bound is the bound.
    assert upload(bus)["ok"] is True


@pytest.mark.parametrize("code", STORE_CODES)
def test_each_store_refusal_of_a_draft_is_answered_with_its_sentence(bus, monkeypatch, code):
    def add_draft(self, draft, html, font_files):
        raise Refused(code)

    monkeypatch.setattr(store_mod.Store, "add_draft", add_draft)

    answer = upload(bus)

    assert answer["ok"] is False and answer["message"] == handlers.MESSAGES[code]
    assert MARKER not in everything_on(bus)


# ── publish ──────────────────────────────────────────────────────────────────

def test_publish_writes_the_site_and_removes_the_draft(bus, site):
    filed = upload(bus)

    answer = publish(bus, filed["draft_id"], title="Why spreads work, revised",
                     tags=["options"])

    assert answer == {"request_id": answer["request_id"], "command": "publish", "ok": True,
                      "message": handlers.MESSAGES["published"],
                      "draft_id": filed["draft_id"], "slug": "why-spreads-work"}
    drafts, entries = stored()
    assert drafts == [] and len(entries) == 1
    entry = entries[0]
    assert (entry["slug"], entry["title"], entry["tags"]) == (
        "why-spreads-work", "Why spreads work, revised", ["options"])
    assert site_files(site) == {"blog.html", "blog.json", "blog/sitemap.txt",
                                "blog/why-spreads-work/index.html",
                                "blog/why-spreads-work/entry.html"}
    with store_mod.Store() as st:
        document = st.entry_html("why-spreads-work")
    assert (site / "blog" / "why-spreads-work" / "entry.html").read_bytes() == \
        document.encode("utf-8")
    listing = json.loads((site / "blog.json").read_bytes())
    assert [row["slug"] for row in listing["entries"]] == ["why-spreads-work"]
    assert listing["entries"][0]["published"] == entry["published_at"]
    assert view(bus, blog_inbox.VIEW_DRAFTS) == {"drafts": []}
    rows = view(bus, blog_inbox.VIEW_POSTS)["entries"]
    assert len(rows) == 1 and set(rows[0]) == set(handlers.ENTRY_KEYS)
    assert rows[0]["published_at"] == entry["published_at"]
    assert sitewriter.pending() is False


def test_a_published_entrys_typefaces_reach_the_site_and_a_discards_are_pruned(
        bus, site, monkeypatch):
    web = kit.Web({kit.LINK: kit.sheet(kit.face("latin", kit.G + "a.woff2")),
                   kit.G + "a.woff2": kit.A,
                   kit.LINK_2: kit.sheet(kit.face("latin", kit.G + "b.woff2")),
                   kit.G + "b.woff2": kit.B})
    monkeypatch.setattr(fonts, "localize", lambda links, **kw: REAL_LOCALIZE(links, fetch=web))
    name_a, name_b = kit.name_of(kit.A), kit.name_of(kit.B)

    published(bus, DOC_WITH_FONT)
    other = upload(bus, OTHER_DOC.replace(
        "</head>", f'<link href="{kit.LINK_2}" rel="stylesheet"></head>'))

    assert (site / "blog" / "fonts" / name_a).read_bytes() == kit.A
    # A draft's typeface is in the store's pool and NOT on the public site.
    assert not (site / "blog" / "fonts" / name_b).exists()
    with store_mod.Store() as st:
        assert st.font_bytes(name_b) == kit.B

    assert send(bus, "discard", draft_id=other["draft_id"])["ok"] is True

    with store_mod.Store() as st:
        assert st.font_bytes(name_b) is None           # pruned with its only user
        assert st.font_bytes(name_a) == kit.A


@pytest.mark.parametrize("code", STORE_CODES)
def test_each_store_refusal_of_a_publish_is_answered_with_its_sentence(
        bus, monkeypatch, code, site):
    filed = upload(bus)

    def refuse(self, draft_id, fields, now):
        raise Refused(code)

    monkeypatch.setattr(store_mod.Store, "publish", refuse)

    answer = publish(bus, filed["draft_id"])

    assert answer["ok"] is False and answer["command"] == "publish"
    # The only name a publish still hands the store unchecked is the address.
    expected = (handlers.MESSAGES["bad_address"].format(chars=blog_inbox.limits()["slug_chars"])
                if code == "bad_name" else handlers.MESSAGES[code])
    assert answer["message"] == expected
    assert answer["draft_id"] == filed["draft_id"]
    assert site_files(site) == {"blog.html"}
    assert MARKER not in everything_on(bus)


def test_an_address_that_is_a_device_name_is_refused_by_the_real_store(bus, site):
    """``aux`` has the shape of an address and passes ``clean_slug``; the store
    refuses it on every platform (no Windows box could restore the entry)."""
    filed = upload(bus)
    answer = publish(bus, filed["draft_id"], slug="aux")
    assert answer["ok"] is False
    assert answer["message"] in (
        handlers.MESSAGES["bad_address"].format(chars=blog_inbox.limits()["slug_chars"]),
        handlers.MESSAGES["bad_input"])            # the store before it had codes
    assert stored()[1] == [] and site_files(site) == {"blog.html"}


def test_a_store_that_answers_none_is_a_refusal_too(bus, monkeypatch, site):
    """The store's earlier contract: ``None`` for a publish it would not do."""
    filed = upload(bus)
    monkeypatch.setattr(store_mod.Store, "publish", lambda self, draft_id, fields, now: None)
    answer = publish(bus, filed["draft_id"])
    assert answer["ok"] is False
    assert answer["message"] == handlers.MESSAGES["not_published"]
    assert site_files(site) == {"blog.html"}


def test_publishing_onto_a_taken_address_is_refused_by_the_real_store(bus, site):
    slug = published(bus)
    before = site_files(site)
    second = upload(bus, OTHER_DOC)

    answer = publish(bus, second["draft_id"], slug=slug)

    assert answer["ok"] is False
    assert answer["message"] in (handlers.MESSAGES["slug_taken"],
                                 handlers.MESSAGES["not_published"])
    drafts, entries = stored()
    assert len(drafts) == 1 and len(entries) == 1 and entries[0]["title"] == "Why spreads work"
    assert site_files(site) == before


def test_publishing_a_draft_that_is_gone_says_so(bus):
    answer = send(bus, "publish", draft_id="a" * 16,
                  fields={"title": "T", "summary": "", "tags": [], "slug": "t"})
    assert answer["ok"] is False and answer["message"] == handlers.MESSAGES["no_draft"]


def test_publishing_without_a_title_says_so(bus, site):
    filed = upload(bus)
    answer = publish(bus, filed["draft_id"], title="   ")
    assert answer["ok"] is False and answer["message"] == handlers.MESSAGES["no_title"]
    assert stored()[1] == [] and site_files(site) == {"blog.html"}


@pytest.mark.parametrize("typed", ["Not An Address!", "a--b", "../up", "fonts", "", None, 7,
                                   "x" * 200])
def test_an_address_that_cannot_be_used_is_said_not_defaulted(bus, site, typed):
    filed = upload(bus)

    answer = publish(bus, filed["draft_id"], slug=typed)

    assert answer["ok"] is False
    assert answer["message"] == handlers.MESSAGES["bad_address"].format(
        chars=blog_inbox.limits()["slug_chars"])
    assert a_sentence(answer["message"])
    drafts, entries = stored()
    assert entries == [] and len(drafts) == 1
    assert site_files(site) == {"blog.html"}


def test_an_address_typed_with_capitals_and_spaces_is_the_address_it_spells(bus):
    filed = upload(bus)
    answer = publish(bus, filed["draft_id"], slug="  My-Entry ")
    assert answer["ok"] is True and answer["slug"] == "my-entry"


def test_a_failed_site_write_is_reported_and_degrades(bus, site, monkeypatch, caplog):
    filed = upload(bus)

    def refuse(path, payload):
        raise OSError(MARKER)

    monkeypatch.setattr(sitewriter, "_write", refuse)
    before = degrades("blog.site")

    with caplog.at_level("DEBUG"):
        answer = publish(bus, filed["draft_id"])

    assert answer["ok"] is False
    assert answer["message"] == handlers.MESSAGES["published_site_failed"]
    assert answer["message"] == ("Published, but the site could not be written. "
                                 "It will be retried.")
    assert answer["slug"] == "why-spreads-work"
    # Still published in the store, and the views say so.
    drafts, entries = stored()
    assert drafts == [] and [entry["slug"] for entry in entries] == ["why-spreads-work"]
    assert [row["slug"] for row in view(bus, blog_inbox.VIEW_POSTS)["entries"]] == [
        "why-spreads-work"]
    assert degrades("blog.site") > before
    assert sitewriter.pending() is True            # the scheduler will run it again
    assert MARKER not in everything_on(bus) and MARKER not in caplog.text


def test_publishing_with_the_site_switched_off_says_so(bus, site, monkeypatch):
    filed = upload(bus)
    monkeypatch.setattr(blog_inbox, "site", lambda: {"enabled": False, "republish_min": 30})
    answer = publish(bus, filed["draft_id"])
    assert answer["ok"] is True
    assert answer["message"] == handlers.MESSAGES["published_site_off"]
    assert site_files(site) == {"blog.html"} and len(stored()[1]) == 1


# ── a replacement ────────────────────────────────────────────────────────────

def test_a_replacement_keeps_the_address_and_reuses_the_typeface_links(
        bus, site, monkeypatch):
    web = kit.Web({kit.LINK: kit.sheet(kit.face("latin", kit.G + "a.woff2")),
                   kit.G + "a.woff2": kit.A})
    asked = []

    def localize(links, **kw):
        asked.append(tuple(links))
        return REAL_LOCALIZE(links, fetch=web)

    monkeypatch.setattr(fonts, "localize", localize)
    slug = published(bus, DOC_WITH_FONT)
    first = stored()[1][0]
    del asked[:]

    # The new document has another title (so another address of its own) and
    # asks for no typefaces.
    filed = upload(bus, OTHER_DOC, revises=slug)

    assert filed["ok"] is True and filed["slug"] == slug
    assert asked == [(kit.LINK,)]
    draft = stored()[0][0]
    assert (draft["revises"], draft["slug"], draft["title"]) == (slug, slug, "A second look")
    assert draft["font_links"] == [kit.LINK] and draft["fonts"] == [kit.name_of(kit.A)]
    assert view(bus, blog_inbox.VIEW_DRAFTS)["drafts"][0]["revises"] == slug

    answer = publish(bus, filed["draft_id"])

    assert answer["ok"] is True and answer["slug"] == slug
    drafts, entries = stored()
    assert drafts == [] and len(entries) == 1
    assert entries[0]["title"] == "A second look"
    assert entries[0]["published_at"] == first["published_at"]
    assert entries[0]["updated_at"] >= first["updated_at"]
    document = (site / "blog" / slug / "entry.html").read_bytes().decode("utf-8")
    assert "Different words this time." in document
    assert f"url(../fonts/{kit.name_of(kit.A)})" in document
    assert (site / "blog" / "fonts" / kit.name_of(kit.A)).is_file()


def test_a_replacement_keeps_its_entrys_tags(bus):
    """An upload carries no fields, so a replacement used to arrive with no
    tags and publishing it as offered cleared the entry's. The draft is offered
    with the entry's tags; the title and summary are still the new document's."""
    slug = published(bus, tags=["Options", "spreads"])
    assert stored()[1][0]["tags"] == ["Options", "spreads"]

    filed = upload(bus, OTHER_DOC, revises=slug)

    draft = stored()[0][0]
    assert draft["tags"] == ["Options", "spreads"]
    assert (draft["title"], draft["summary"]) == ("A second look", "Different words this time.")
    assert view(bus, blog_inbox.VIEW_DRAFTS)["drafts"][0]["tags"] == ["Options", "spreads"]
    # Published with exactly what the draft offered, as the page prefills it.
    assert publish(bus, filed["draft_id"])["ok"] is True
    entry = stored()[1][0]
    assert entry["tags"] == ["Options", "spreads"] and entry["title"] == "A second look"


def test_a_replacement_that_names_its_own_tags_gets_those(bus):
    slug = published(bus, tags=["Options", "spreads"])
    upload(bus, OTHER_DOC, fields={"tags": ["Volatility"]}, revises=slug)
    assert stored()[0][0]["tags"] == ["Volatility"]


def test_a_new_entry_starts_with_no_tags(bus):
    published(bus, tags=["Options", "spreads"])
    upload(bus, OTHER_DOC)
    assert stored()[0][0]["tags"] == []


def test_a_replacement_that_asks_for_its_own_typefaces_gets_those(bus, no_typefaces):
    slug = published(bus)
    del no_typefaces[:]
    assert upload(bus, DOC_WITH_FONT, revises=slug)["ok"] is True
    assert no_typefaces == [(kit.LINK,)]


def test_a_replacement_is_published_at_its_entrys_address_when_none_is_given(bus):
    """What ``owner_command`` sends for an address longer than a new one may
    now be: ``""``. A replacement has one possible address."""
    slug = published(bus)
    filed = upload(bus, OTHER_DOC, revises=slug)
    answer = publish(bus, filed["draft_id"], slug="")
    assert answer["ok"] is True and answer["slug"] == slug


def test_a_replacement_cannot_move_to_another_address(bus):
    slug = published(bus)
    filed = upload(bus, OTHER_DOC, revises=slug)
    answer = publish(bus, filed["draft_id"], slug="somewhere-else")
    assert answer["ok"] is False
    assert answer["message"] in (handlers.MESSAGES["slug_changed"],
                                 handlers.MESSAGES["not_published"])
    assert [entry["title"] for entry in stored()[1]] == ["Why spreads work"]


def test_a_replacement_for_an_entry_that_is_gone_is_refused(bus, fast_clean):
    answer = upload(bus, revises="never-published")
    assert answer["ok"] is False and answer["message"] == handlers.MESSAGES["revises_gone"]
    assert fast_clean == [] and stored() == ([], [])


@pytest.mark.parametrize("bad", ["Not_A_Slug", "../x", "A", " padded ", 7, ["a"], "fonts"])
def test_a_replacement_naming_no_address_is_not_understood(bus, fast_clean, bad):
    answer = upload(bus, revises=bad)
    assert answer["ok"] is False and answer["message"] == handlers.MESSAGES["bad_input"]
    assert fast_clean == [] and stored() == ([], [])


# ── discard, unpublish ───────────────────────────────────────────────────────

def test_discard_removes_the_draft(bus):
    filed = upload(bus)
    answer = send(bus, "discard", draft_id=filed["draft_id"])
    assert answer["ok"] is True and answer["message"] == handlers.MESSAGES["discarded"]
    assert answer["draft_id"] == filed["draft_id"] and answer["command"] == "discard"
    assert stored() == ([], [])
    assert view(bus, blog_inbox.VIEW_DRAFTS) == {"drafts": []}


def test_discarding_a_draft_that_is_gone_says_so(bus):
    answer = send(bus, "discard", draft_id="b" * 16)
    assert answer["ok"] is False and answer["message"] == handlers.MESSAGES["no_draft"]


def test_unpublish_removes_the_entry_from_the_store_and_the_site(bus, site):
    keep = published(bus, OTHER_DOC)
    slug = published(bus)

    answer = send(bus, "unpublish", slug=slug)

    assert answer == {"request_id": answer["request_id"], "command": "unpublish",
                      "ok": True, "message": handlers.MESSAGES["unpublished"],
                      "draft_id": "", "slug": slug}
    assert [entry["slug"] for entry in stored()[1]] == [keep]
    assert site_files(site) == {"blog.html", "blog.json", "blog/sitemap.txt",
                                f"blog/{keep}/index.html", f"blog/{keep}/entry.html"}
    assert not (site / "blog" / slug).exists()
    assert [row["slug"] for row in json.loads((site / "blog.json").read_bytes())["entries"]] == [
        keep]
    assert [row["slug"] for row in view(bus, blog_inbox.VIEW_POSTS)["entries"]] == [keep]


def test_unpublishing_what_is_not_published_says_so(bus):
    answer = send(bus, "unpublish", slug="never-published")
    assert answer["ok"] is False and answer["message"] == handlers.MESSAGES["no_entry"]
    assert answer["slug"] == "never-published"


def test_unpublishing_with_the_site_switched_off_says_the_page_is_still_up(
        bus, site, monkeypatch):
    """With the site switched off a rebuild writes and removes nothing, so the
    entry's page and its row in the list are still being served. The answer
    must not say the entry is simply gone."""
    slug = published(bus)
    before = site_files(site)
    assert f"blog/{slug}/index.html" in before
    monkeypatch.setattr(blog_inbox, "site", lambda: {"enabled": False, "republish_min": 30})

    answer = send(bus, "unpublish", slug=slug)

    assert answer["message"] == handlers.MESSAGES["unpublished_site_off"]
    assert answer["message"] != handlers.MESSAGES["unpublished"]
    assert "still on the site" in answer["message"]
    assert answer["slug"] == slug
    assert stored()[1] == []                       # gone from the store
    assert site_files(site) == before              # and exactly as public as before
    assert [row["slug"] for row in json.loads((site / "blog.json").read_bytes())["entries"]] == [
        slug]


def test_unpublishing_nothing_with_the_site_switched_off_is_still_no_entry(bus, monkeypatch):
    monkeypatch.setattr(blog_inbox, "site", lambda: {"enabled": False, "republish_min": 30})
    answer = send(bus, "unpublish", slug="never-published")
    assert answer["ok"] is False and answer["message"] == handlers.MESSAGES["no_entry"]


def test_an_unpublish_whose_site_write_fails_is_reported(bus, site, monkeypatch):
    slug = published(bus)

    def refuse(folder):
        raise OSError(MARKER)

    monkeypatch.setattr(sitewriter, "_remove_entry", refuse)

    answer = send(bus, "unpublish", slug=slug)

    assert answer["ok"] is False
    assert answer["message"] == handlers.MESSAGES["unpublished_site_failed"]
    assert stored()[1] == []                       # gone from the store all the same
    assert MARKER not in everything_on(bus)


# ── every command is answered, and nothing leaks ─────────────────────────────

def test_every_code_has_a_whole_sentence():
    codes = set(clean.REFUSALS) | set(STORE_CODES) | set(getattr(store_mod, "REFUSAL_CODES", ()))
    assert codes <= set(handlers.MESSAGES), sorted(codes - set(handlers.MESSAGES))
    for code, text in handlers.MESSAGES.items():
        shown = text.format(kb=512, count=20, chars=80)
        assert a_sentence(shown), (code, shown)
        assert "{" not in shown


def test_the_brief_sentences_are_the_ones_used():
    said = handlers.MESSAGES
    assert said["empty"] == said["not_text"] == "The file had no content that could be shown."
    assert said["crowded_tag"] == said["too_deep"] == said["cut_off"] == (
        "The file could not be read to its end. It may be cut off or malformed.")
    assert said["too_slow"] == "Cleaning the file took too long, so it was refused."
    assert said["did_not_settle"] == said["internal"] == (
        "The file could not be cleaned because of a fault in the cleaner. It has been logged.")
    assert said["slug_taken"] == (
        "An entry already uses this address. Choose another address, or upload the file "
        "as a replacement for that entry.")
    assert said["slug_changed"] == "A replacement must keep the address of the entry it replaces."
    assert said["no_title"] == "Give the entry a title before publishing."
    assert said["no_draft"] == said["document_missing"] == "That draft is no longer there."
    assert said["busy"] == ("The store is busy finishing an earlier change. "
                            "Try again in a moment.")
    assert said["bad_name"] == said["bad_input"] == "That request was not understood."


def test_the_command_table_is_the_four_and_no_more():
    assert set(handlers.COMMANDS) == {"draft_submit", "publish", "discard", "unpublish"}
    assert blog_inbox.SUBMIT_TYPE in handlers.COMMANDS
    assert set(blog_inbox.OWNER_KINDS) <= set(handlers.COMMANDS)
    # The connector's revise is not built.
    assert blog_inbox.REVISE_TYPE not in handlers.COMMANDS


@pytest.mark.parametrize("kind", ["draft_revise", "publish_all", "", "PUBLISH", None, 7])
def test_an_unknown_type_is_answered_when_it_can_be(bus, kind):
    rid = blog_inbox.new_id()
    handlers.handle_command(bus, Command.model_construct(type=kind, args={"request_id": rid}))
    answer = result(bus)
    assert answer == {"request_id": rid, "command": "", "ok": False,
                      "message": handlers.MESSAGES["bad_input"], "draft_id": "", "slug": ""}
    assert stored() == ([], [])


@pytest.mark.parametrize("args", [{}, {"request_id": "not-an-id"}, {"request_id": None},
                                  {"request_id": "A" * 16}, None, "text", ["a"]])
def test_a_command_nobody_could_be_told_about_is_dropped_not_run(bus, args):
    filed = upload(bus)
    before = everything_on(bus)
    for kind in ("discard", "publish", "unpublish", "draft_submit", "nonsense"):
        extra = dict(args, draft_id=filed["draft_id"]) if isinstance(args, dict) else args
        handlers.handle_command(bus, Command.model_construct(type=kind, args=extra))
    assert everything_on(bus) == before
    assert len(stored()[0]) == 1


@pytest.mark.parametrize("kind,args", [
    ("publish", {}),
    ("publish", {"draft_id": "nope"}),
    ("publish", {"draft_id": ["a" * 16]}),
    ("discard", {}),
    ("discard", {"draft_id": "../../etc"}),
    ("unpublish", {}),
    ("unpublish", {"slug": "../x"}),
    ("unpublish", {"slug": "Has Capitals"}),
    ("unpublish", {"slug": ["a"]}),
])
def test_malformed_arguments_are_answered_as_not_understood(bus, site, kind, args):
    slug = published(bus)
    before = site_files(site)
    answer = send(bus, kind, **args)
    assert answer["ok"] is False and answer["command"] == kind
    assert answer["message"] == handlers.MESSAGES["bad_input"]
    assert [entry["slug"] for entry in stored()[1]] == [slug]
    assert site_files(site) == before


def test_a_publish_whose_fields_are_not_a_mapping_publishes_nothing(bus, site):
    filed = upload(bus)
    for fields in (None, "title", ["title"], 7):
        answer = send(bus, "publish", draft_id=filed["draft_id"], fields=fields)
        assert answer["ok"] is False and a_sentence(answer["message"])
    assert stored()[1] == [] and site_files(site) == {"blog.html"}


def test_an_unexpected_fault_is_answered_and_does_not_propagate(bus, monkeypatch, caplog):
    def boom(self, draft, html, font_files):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(store_mod.Store, "add_draft", boom)
    before = degrades("blog.handlers")

    with caplog.at_level("DEBUG"):
        answer = upload(bus)                       # returns: nothing was raised

    assert answer["ok"] is False and answer["command"] == "draft_submit"
    assert answer["message"] == handlers.MESSAGES["fault"]
    assert a_sentence(answer["message"])
    assert degrades("blog.handlers") == before + 1
    assert MARKER not in everything_on(bus) and MARKER not in caplog.text


def test_a_store_that_cannot_be_opened_is_answered(bus, monkeypatch):
    def refuse():
        raise OSError(MARKER)

    monkeypatch.setattr(handlers, "open_store", refuse)
    answer = send(bus, "discard", draft_id="a" * 16)
    assert answer["ok"] is False and answer["message"] == handlers.MESSAGES["fault"]
    assert MARKER not in everything_on(bus)


def test_a_bus_that_cannot_be_written_raises_nothing(monkeypatch):
    class Dead:
        def cache_set(self, *args, **kwargs):
            raise ConnectionError(MARKER)

    before = degrades("blog.handlers")
    handlers.handle_command(Dead(), Command(type="discard", args={
        "request_id": blog_inbox.new_id(), "draft_id": "a" * 16}))
    assert degrades("blog.handlers") == before + 2      # the command, then its answer


def test_no_answer_or_view_carries_an_exceptions_text(bus, monkeypatch, caplog):
    """Three places an exception's text could ride out, each with the marker
    planted, then everything any reader could see is searched for it."""
    first = upload(bus)
    second = upload(bus, OTHER_DOC)

    with caplog.at_level("DEBUG"):
        # 1. the store refuses a publish with a plain ValueError
        with monkeypatch.context() as patch:
            def refuse(self, draft_id, fields, now):
                raise ValueError(MARKER)
            patch.setattr(store_mod.Store, "publish", refuse)
            assert publish(bus, first["draft_id"])["message"] == handlers.MESSAGES["bad_input"]
        # 2. the site write fails under a real publish
        with monkeypatch.context() as patch:
            def no_write(path, payload):
                raise OSError(MARKER)
            patch.setattr(sitewriter, "_write", no_write)
            assert publish(bus, first["draft_id"])["ok"] is False
        # 3. the typeface prune fails after a discard
        with monkeypatch.context() as patch:
            def no_prune(self):
                raise RuntimeError(MARKER)
            patch.setattr(store_mod.Store, "prune_fonts", no_prune)
            assert send(bus, "discard", draft_id=second["draft_id"])["ok"] is True
        # 4. the views cannot be read from the store
        with monkeypatch.context() as patch:
            def no_rows(self):
                raise RuntimeError(MARKER)
            patch.setattr(store_mod.Store, "entries", no_rows)
            assert send(bus, "unpublish", slug="why-spreads-work")["message"] == (
                handlers.MESSAGES["fault"])

    assert MARKER not in everything_on(bus)
    assert MARKER not in caplog.text


def test_no_view_carries_a_document(bus):
    # The first paragraph is the summary, which IS in a view. The second is in
    # the document and nowhere else.
    body_only = "Only-the-document-says-this"
    published(bus, DOC.replace("Second.", body_only))
    upload(bus, OTHER_DOC.replace("</body>", f"<p>{body_only}</p></body>"))
    upload(bus, f"<!doctype html><title>Third</title><p>More.</p><p>{body_only}</p>",
           revises="why-spreads-work")
    send(bus, "publish", draft_id="c" * 16, fields={})
    assert len(stored()[0]) == 2 and len(stored()[1]) == 1
    for name in (blog_inbox.VIEW_DRAFTS, blog_inbox.VIEW_POSTS, blog_inbox.VIEW_RESULT):
        text = json.dumps(view(bus, name))
        assert "<" not in text, name
        assert "html" not in json.loads(text), name
    held = everything_on(bus)
    assert "<" not in held and body_only not in held


def test_the_views_hold_no_time_of_their_own_and_skip_when_unchanged(bus):
    published(bus)
    upload(bus, OTHER_DOC)
    assert set(view(bus, blog_inbox.VIEW_DRAFTS)) == {"drafts"}
    assert set(view(bus, blog_inbox.VIEW_POSTS)) == {"entries"}
    versions = [bus.cache_version(key) for key in (handlers.CACHE_DRAFTS, handlers.CACHE_POSTS)]
    with store_mod.Store() as st:
        handlers.publish_views(bus, st)
        handlers.publish_views(bus, st)
    assert [bus.cache_version(key) for key in (handlers.CACHE_DRAFTS, handlers.CACHE_POSTS)] \
        == versions


def test_the_views_are_read_and_written_as_one_step():
    """The scheduler's pass and a command both publish the views. Read and
    write under one lock, or the slower of two could write its older reading
    last."""
    held = []

    class WatchedStore:
        def drafts(self):
            held.append(handlers._VIEWS_LOCK.locked())
            return []

        def entries(self):
            held.append(handlers._VIEWS_LOCK.locked())
            return []

    class WatchedBus:
        def cache_set(self, key, payload, **kwargs):
            held.append(handlers._VIEWS_LOCK.locked())

    handlers.publish_views(WatchedBus(), WatchedStore())
    assert held == [True, True, True, True]
    assert handlers._VIEWS_LOCK.locked() is False


def test_the_views_are_newest_first(bus):
    one = published(bus)
    two = published(bus, OTHER_DOC)
    upload(bus, DOC, fields={"slug": "draft-one"})
    upload(bus, DOC, fields={"slug": "draft-two"})
    assert [row["slug"] for row in view(bus, blog_inbox.VIEW_POSTS)["entries"]] == [two, one]
    assert [row["slug"] for row in view(bus, blog_inbox.VIEW_DRAFTS)["drafts"]] == [
        "draft-two", "draft-one"]


def test_the_same_answer_twice_still_repaints(bus):
    send(bus, "discard", draft_id="a" * 16, request_id="1" * 16)
    first = bus.cache_version(handlers.CACHE_RESULT)
    send(bus, "discard", draft_id="a" * 16, request_id="1" * 16)
    assert bus.cache_version(handlers.CACHE_RESULT) == first + 1


def test_an_answer_never_carries_a_name_that_is_not_one(bus):
    handlers.answer(bus, "not-an-id", "rm -rf", 1, "A sentence.", draft_id="../x",
                    slug="Not A Slug")
    assert result(bus) == {"request_id": "", "command": "", "ok": True,
                           "message": "A sentence.", "draft_id": "", "slug": ""}


def test_the_view_names_are_the_shared_ones():
    assert handlers.CACHE_DRAFTS == "cache:blog:drafts"
    assert handlers.CACHE_POSTS == "cache:blog:posts"
    assert handlers.CACHE_RESULT == "cache:blog:result"


def test_a_view_row_holds_exactly_what_the_page_was_promised():
    """Typed out, not read from the module: these keys are readable by the
    public live process, and the private page is written against them. A
    draft's typeface list and links stay in the store."""
    assert handlers.DRAFT_KEYS == ("id", "source", "revises", "slug", "title", "summary",
                                   "tags", "removed", "font_note", "bytes", "digest",
                                   "received_at")
    assert handlers.ENTRY_KEYS == ("slug", "title", "summary", "tags", "published_at",
                                   "updated_at")


# ── a command that will never run ────────────────────────────────────────────

@pytest.mark.parametrize("why", ["restart", "expired"])
def test_a_dropped_command_is_answered(bus, why):
    rid = blog_inbox.new_id()
    command = Command(**blog_inbox.submit_command(DOC, {}, source="upload", request_id=rid))
    handlers.on_dropped(bus, command, why)
    assert result(bus) == {"request_id": rid, "command": "draft_submit", "ok": False,
                           "message": handlers.MESSAGES["dropped"], "draft_id": "", "slug": ""}
    assert stored() == ([], [])


def test_a_dropped_command_nobody_can_be_told_about_raises_nothing(bus):
    before = everything_on(bus)
    for command in (None, object(), Command.model_construct(type=None, args=None)):
        handlers.on_dropped(bus, command, "restart")
    assert everything_on(bus) == before
