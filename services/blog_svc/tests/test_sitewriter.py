"""The site writer: entry pages, documents, typefaces, the manifest, the sitemap.

Every test writes under ``sw.SITE_ROOT``, which the conftest fixture has
pointed at this test's own folder (with a copy of the tracked ``blog.html``).
``REAL_SITE`` is worked out from the checkout, for the tests that compare
against the tracked page itself.

The store here is a FAKE with the three calls the writer makes (``entries``,
``entry_html``, ``font_bytes``). That is deliberate: what the writer must do
when the store cannot supply a document or a typeface is half of what is
tested, and a real store does not get into those states on request.

Unusual characters are built from their code points, never written as literals.
"""
import html
import json
import os
import pathlib
import re

import pytest

import repo_paths
from services import _degrade
from services.blog_svc import sitewriter as sw
from shared import blog_inbox

REAL_SITE = pathlib.Path(repo_paths.REPO_ROOT) / "deploy" / "site"

EM_DASH = chr(0x2014)
MIDDLE_DOT = chr(0xB7)
E_ACUTE = chr(0xE9)
for _ch in (EM_DASH, MIDDLE_DOT, E_ACUTE):
    assert len(_ch) == 1

HOST = "example.test"
NOW = "2026-10-06T18:00:00+00:00"
LATER = "2026-10-06T19:30:00+00:00"
DOC = "<!doctype html><html lang=\"en\"><head><title>T</title></head><body><p>Hello</p></body></html>"
# The classes site.css is written against (deploy/tests/test_site.py pins the
# same list from the other side, as SERVICE_CLASSES).
CLASSES = ("ns-page-sub", "ns-live-page", "ns-live-main", "ns-report-main",
           "ns-live-head", "ns-report-head", "ns-eyebrow", "ns-blog-crumb",
           "ns-report-links", "ns-report-frame", "ns-foot", "ns-live-foot")
DISCLAIMER = ("NeuralStrike " + EM_DASH + " dealer flow, measured. Educational and simulation "
              "software, not investment advice. No orders are transmitted to any broker.")


def at(day, hour=12, minute=0) -> str:
    """A stored time, in the store's own spelling (UTC, microseconds written)."""
    return f"2026-10-{day:02d}T{hour:02d}:{minute:02d}:00.000000+00:00"


def woff(seed) -> tuple:
    """``(name, bytes)`` of a stand-in typeface, named as the store names one."""
    data = b"wOF2" + bytes([seed]) * 64
    return blog_inbox.font_name_for(data), data


class FakeStore:
    """The three calls the writer makes, and a way to break each."""

    def __init__(self):
        self.rows, self.docs, self.fonts = {}, {}, {}

    def add(self, slug, *, title="A title", summary="A summary", tags=("one", "two"),
            published=None, updated=None, doc=DOC, fonts=()):
        published = published or at(5)
        self.rows[slug] = {"slug": slug, "title": title, "summary": summary,
                           "tags": list(tags), "fonts": [name for name, _ in fonts],
                           "font_links": [], "digest": "0" * 64,
                           "published_at": published, "updated_at": updated or published}
        self.docs[slug] = doc
        self.fonts.update(dict(fonts))
        return self.rows[slug]

    def entries(self):
        rows = sorted(self.rows.values(), key=lambda row: row["slug"])
        return sorted(rows, key=lambda row: row["published_at"], reverse=True)

    def entry_html(self, slug):
        return self.docs.get(slug)

    def font_bytes(self, name):
        return self.fonts.get(name)


@pytest.fixture
def site():
    return sw.SITE_ROOT


@pytest.fixture
def parts(site):
    found = sw.read_parts(site)
    assert found is not None
    return found


def entry(**over) -> dict:
    row = {"slug": "a-first-entry", "title": "A first entry", "summary": "What it says.",
           "tags": ["one"], "fonts": [], "published_at": at(5), "updated_at": at(5)}
    row.update(over)
    return row


def tree(root) -> dict:
    """Every file under ``root`` and its bytes, by relative POSIX path."""
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()}


def degrades(area="blog.site") -> int:
    return _degrade.counts().get(area, 0)


def link_folder(target, at) -> None:
    """Make ``at`` a link to the folder ``target``, or skip the test.

    A symbolic link where the account may make one. On Windows without that
    right, a JUNCTION: it needs no privilege, and it is the harder case - this
    Python does not report one as a link, so only resolving the path shows that
    it leads somewhere else."""
    try:
        os.symlink(target, at, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(at))
    except (ImportError, AttributeError, OSError):
        pytest.skip("this account can make neither a symbolic link nor a junction")


# ── the tracked page's pieces ────────────────────────────────────────────────

def test_the_menu_expression_is_the_one_the_site_tests_pin():
    """deploy/tests/test_site.py holds the same expression as SERVICE_NAV_RE
    and checks it finds exactly the menu in the tracked page. Two files that
    never import each other, joined by this string."""
    assert sw.NAV_RE.pattern == r'<nav class="ns-nav.*?</nav>'
    assert sw.NAV_RE.flags & re.S
    site_tests = (REAL_SITE.parent / "tests" / "test_site.py").read_text(encoding="utf-8")
    assert "SERVICE_NAV_RE = re.compile(r'<nav class=\"ns-nav.*?</nav>', re.S)" in site_tests
    for name in CLASSES:
        assert f'"{name}"' in site_tests, name

def test_the_parts_are_read_from_the_tracked_page(parts):
    raw = (REAL_SITE / "blog.html").read_bytes().decode("utf-8")
    navs = re.findall(r'<nav class="ns-nav.*?</nav>', raw, re.S)
    assert len(navs) == 1 and parts["nav"] == navs[0]
    assert parts["icons"] == (
        '<link rel="icon" href="assets/favicon.svg" type="image/svg+xml">',
        '<link rel="icon" href="/favicon.ico" sizes="16x16 32x32 48x48">',
        '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">')
    assert parts["styles"] == ('<link rel="stylesheet" href="assets/nocturne.css">',
                               '<link rel="stylesheet" href="assets/site.css">')
    assert DISCLAIMER in parts["footer"]


@pytest.mark.parametrize("cut", [
    r'<nav class="ns-nav.*?</nav>',
    r'<link rel="stylesheet" href="assets/site\.css">',
    r'<link rel="(?:icon|apple-touch-icon)"[^>]*>',
    r"<footer.*?</footer>",
])
def test_a_page_missing_any_piece_gives_no_parts(site, cut):
    page = site / "blog.html"
    raw = page.read_bytes().decode("utf-8")
    assert re.search(cut, raw, re.S)
    page.write_bytes(re.sub(cut, "", raw, flags=re.S).encode("utf-8"))
    assert sw.read_parts(site) is None


def test_a_page_that_quotes_the_menu_tag_twice_gives_no_parts(site):
    """Two matches means the expression may have started inside a comment, and
    an entry would be published under a menu that begins with prose."""
    page = site / "blog.html"
    raw = page.read_bytes().decode("utf-8")
    page.write_bytes(raw.replace(
        "</head>", '<!-- the <nav class="ns-nav"> element, then </nav> -->\n</head>').encode("utf-8"))
    assert sw.read_parts(site) is None


def test_a_link_inside_a_comment_is_not_a_piece(site):
    page = site / "blog.html"
    raw = page.read_bytes().decode("utf-8")
    page.write_bytes(raw.replace(
        "</head>", '<!-- <link rel="stylesheet" href="assets/old.css"> -->\n</head>').encode("utf-8"))
    found = sw.read_parts(site)
    assert found is not None and not any("old.css" in link for link in found["styles"])


def test_a_page_whose_menu_runs_script_gives_no_parts(site):
    page = site / "blog.html"
    raw = page.read_bytes().decode("utf-8")
    page.write_bytes(raw.replace("</nav>", "<SCRIPT>x()</SCRIPT></nav>").encode("utf-8"))
    assert sw.read_parts(site) is None


# ── the entry page ───────────────────────────────────────────────────────────

def test_the_entry_page_carries_the_tracked_menu_byte_for_byte(parts):
    raw = (REAL_SITE / "blog.html").read_bytes().decode("utf-8")
    nav = re.search(r'<nav class="ns-nav.*?</nav>', raw, re.S).group(0)
    page = sw.entry_page(parts, entry(), HOST)
    assert page.count(nav) == 1
    assert page.count("<nav") == 1


def test_the_entry_page_escapes_every_field(parts):
    title, summary = '"><script>x</script>', "<b>'&\"</b>"
    page = sw.entry_page(parts, entry(title=title, summary=summary), HOST)
    assert "<script" not in page.lower()
    assert "<b>" not in page
    assert page.count(html.escape(title, quote=True)) == 5     # title, og:title, h1 twice, frame
    assert page.count(html.escape(summary, quote=True)) == 2   # description, og:description
    # Nothing a field holds can end an attribute: no raw quote from either.
    assert "'&\"" not in page


def test_the_host_is_escaped_too(parts):
    page = sw.entry_page(parts, entry(), 'h"><i>')
    assert "<i>" not in page and "h&quot;&gt;&lt;i&gt;" in page


def test_the_frame_is_sandboxed_with_the_shared_tokens(parts):
    page = sw.entry_page(parts, entry(), HOST)
    frames = re.findall(r"<iframe\b[^>]*>", page)
    assert len(frames) == 1
    assert f'sandbox="{blog_inbox.ENTRY_SANDBOX}"' in frames[0]
    assert "allow-scripts" not in frames[0]
    assert 'src="blog/a-first-entry/entry.html"' in frames[0]
    assert 'class="ns-report-frame"' in frames[0]
    assert 'title="A first entry"' in frames[0]


def test_the_entry_page_runs_nothing(parts):
    page = sw.entry_page(parts, entry(), HOST).lower()
    assert "<script" not in page
    assert "javascript:" not in page
    assert not re.search(r"\son[a-z]+\s*=", page)


def test_the_head_says_which_entry_this_is(parts):
    page = sw.entry_page(parts, entry(), HOST)
    head = page[page.index("<head>"):page.index("</head>")]
    lines = [line for line in head.splitlines() if line.strip()]
    # The base address comes before anything it must resolve.
    assert lines[1] == '<meta charset="utf-8">'
    assert lines[2] == '<base href="/">'
    assert f"<title>NeuralStrike {EM_DASH} A first entry</title>" in head
    assert '<meta name="description" content="What it says.">' in head
    assert '<link rel="canonical" href="https://example.test/blog/a-first-entry/">' in head
    assert '<meta property="og:type" content="article">' in head
    assert '<meta property="og:title" content="A first entry">' in head
    assert '<meta property="og:description" content="What it says.">' in head
    assert '<meta property="og:url" content="https://example.test/blog/a-first-entry/">' in head
    assert '<meta name="theme-color" content="#161826">' in head
    for link in parts["icons"] + parts["styles"]:
        assert head.count(link) == 1
        assert head.index(link) > head.index('<base href="/">')
    assert page.startswith("<!doctype html>\n<html lang=\"en\">\n<head>\n")


def test_the_body_is_the_frame_the_stylesheet_was_written_against(parts):
    page = sw.entry_page(parts, entry(), HOST)
    for name in CLASSES:
        assert re.search(rf'class="[^"]*\b{re.escape(name)}\b', page), name
    assert '<p class="ns-eyebrow">Blog</p>' in page
    assert '<h1 class="ns-blog-crumb" title="A first entry">A first entry</h1>' in page
    assert '<a href="blog.html">All entries</a>' in page
    assert ('<a href="blog/a-first-entry/entry.html">Open the entry on its own page</a>'
            in page)
    assert MIDDLE_DOT in page
    assert page.count(DISCLAIMER) == 1
    assert page.rstrip().endswith("</html>")
    assert page.index("</nav>") < page.index("<main") < page.index("<iframe") \
        < page.index("<footer")


def test_the_date_is_the_central_time_day(parts):
    # 03:30 UTC on the 7th is 22:30 on the 6th in Chicago.
    stamp = "2026-10-07T03:30:00.000000+00:00"
    page = sw.entry_page(parts, entry(published_at=stamp), HOST)
    assert f'<time datetime="{stamp}">October 6, 2026</time>' in page
    # And a winter one, across the year's end: 05:59 UTC is 23:59 the day before.
    page = sw.entry_page(parts, entry(published_at="2027-01-01T05:59:00.000000+00:00"), HOST)
    assert ">December 31, 2026</time>" in page


@pytest.mark.parametrize("bad", ["", "yesterday", None, "2026-10-07T03:30:00"])
def test_a_date_that_cannot_be_read_is_left_out_not_guessed(parts, bad):
    page = sw.entry_page(parts, entry(published_at=bad), HOST)
    assert "<time" not in page
    assert '<a href="blog.html">All entries</a>' in page


# ── the manifest ─────────────────────────────────────────────────────────────

def test_the_manifest_is_newest_first_and_holds_no_html():
    rows = [entry(slug="old", published_at=at(1), updated_at=at(9)),
            entry(slug="new", published_at=at(3)),
            entry(slug="mid", published_at=at(2))]
    out = sw.manifest(rows, NOW)
    assert out["updated"] == NOW
    assert [row["slug"] for row in out["entries"]] == ["new", "mid", "old"]
    assert all(set(row) == {"slug", "title", "summary", "tags", "published", "updated"}
               for row in out["entries"])
    assert out["entries"][2]["published"] == at(1)
    assert out["entries"][2]["updated"] == at(9)
    assert "<" not in json.dumps(out)
    # A revision does not move an entry up: order is by FIRST publication.
    assert [row["slug"] for row in sw.manifest(list(reversed(rows)), NOW)["entries"]] == [
        "new", "mid", "old"]


def test_the_manifest_is_pure():
    rows = [entry(tags=["one"])]
    out = sw.manifest(rows, NOW)
    out["entries"][0]["tags"].append("two")
    assert rows[0]["tags"] == ["one"]


# ── rebuild ──────────────────────────────────────────────────────────────────

def test_rebuild_writes_pages_documents_fonts_manifest_and_sitemap(site, monkeypatch):
    monkeypatch.setattr(repo_paths, "SITE_HOST", HOST)
    name, data = woff(1)
    doc = "<!doctype html>\r\n<p>caf" + E_ACUTE + "</p>\n"
    store = FakeStore()
    store.add("first", published=at(1), doc=doc, fonts=[(name, data)])
    store.add("second", published=at(2), title="Second")
    before = degrades()

    out = sw.rebuild(site, store, NOW)

    assert out["ok"] is True and out["skipped"] == [] and out["removed"] == 0
    assert out["written"] == 7
    files = tree(site)
    assert set(files) == {"blog.html", "blog.json", "blog/sitemap.txt",
                          "blog/first/index.html", "blog/first/entry.html",
                          "blog/second/index.html", "blog/second/entry.html",
                          f"blog/fonts/{name}"}
    # The document, byte for byte: no newline translated, UTF-8.
    assert files["blog/first/entry.html"] == doc.encode("utf-8")
    assert files[f"blog/fonts/{name}"] == data
    page = files["blog/second/index.html"].decode("utf-8")
    assert page == sw.entry_page(sw.read_parts(site), store.rows["second"], HOST)
    manifest = json.loads(files["blog.json"].decode("utf-8"))
    assert manifest == sw.manifest(store.entries(), NOW)
    assert [row["slug"] for row in manifest["entries"]] == ["second", "first"]
    assert files["blog/sitemap.txt"] == (b"https://example.test/blog/second/\n"
                                         b"https://example.test/blog/first/\n")
    assert degrades() == before
    assert sw.pending() is False


def test_rebuild_with_nothing_published_writes_an_empty_list(site):
    out = sw.rebuild(site, FakeStore(), NOW)
    assert out["ok"] is True
    files = tree(site)
    assert json.loads(files["blog.json"]) == {"updated": NOW, "entries": []}
    assert files["blog/sitemap.txt"] == b""


def test_a_second_identical_rebuild_rewrites_no_file(site, monkeypatch):
    name, data = woff(1)
    store = FakeStore()
    store.add("first", fonts=[(name, data)])
    assert sw.rebuild(site, store, NOW)["written"] == 5
    before = tree(site)
    stamps = {path: path.stat().st_mtime_ns for path in site.rglob("*") if path.is_file()}

    written = []
    real = sw._write
    monkeypatch.setattr(sw, "_write", lambda path, payload: written.append(path.name)
                        or real(path, payload))
    # A LATER clock: the manifest's own stamp must not be what makes it differ.
    out = sw.rebuild(site, store, LATER)

    assert out == {"ok": True, "written": 0, "removed": 0, "skipped": [], "fonts_skipped": []}
    assert written == []
    assert tree(site) == before
    assert {path: path.stat().st_mtime_ns
            for path in site.rglob("*") if path.is_file()} == stamps
    assert json.loads((site / "blog.json").read_bytes())["updated"] == NOW


def test_a_changed_entry_is_rewritten_and_the_list_restamped(site):
    store = FakeStore()
    store.add("first")
    sw.rebuild(site, store, NOW)
    store.docs["first"] = DOC.replace("Hello", "Hello again")
    store.rows["first"]["title"] = "A better title"
    store.rows["first"]["updated_at"] = at(6)

    out = sw.rebuild(site, store, LATER)

    assert out["ok"] is True and out["written"] == 3       # document, page, manifest
    files = tree(site)
    assert b"Hello again" in files["blog/first/entry.html"]
    assert b"A better title" in files["blog/first/index.html"]
    manifest = json.loads(files["blog.json"])
    assert manifest["updated"] == LATER
    assert manifest["entries"][0]["title"] == "A better title"


def test_a_page_exists_before_the_list_links_to_it(site, monkeypatch):
    store = FakeStore()
    store.add("first")
    order = []
    real = sw._write
    monkeypatch.setattr(sw, "_write", lambda path, payload: order.append(path.name)
                        or real(path, payload))
    sw.rebuild(site, store, NOW)
    assert order[-1] == "blog.json"
    assert order.index("entry.html") < order.index("index.html") < order.index("blog.json")


def test_rebuild_removes_an_unpublished_entry_and_only_that(site):
    kept_name, kept = woff(1)
    gone_name, gone = woff(2)
    store = FakeStore()
    store.add("stays", published=at(2), fonts=[(kept_name, kept)])
    store.add("goes", published=at(1), fonts=[(gone_name, gone)])
    sw.rebuild(site, store, NOW)
    blog = site / "blog"
    (blog / "README").write_bytes(b"keep me")
    (blog / "notes").write_bytes(b"a FILE named like an address")
    (blog / "Not_A_Slug").mkdir()
    (blog / "Not_A_Slug" / "index.html").write_bytes(b"not ours")
    (blog / "fonts" / "README").write_bytes(b"keep me too")
    (blog / "fonts" / "other.woff2").write_bytes(b"not named like ours")
    (site / "report.html").write_bytes(b"another page")

    del store.rows["goes"], store.docs["goes"]
    out = sw.rebuild(site, store, LATER)

    assert out["ok"] is True
    assert out["removed"] == 2                 # the entry's folder, its typeface
    files = tree(site)
    assert set(files) == {
        "blog.html", "report.html", "blog.json", "blog/sitemap.txt", "blog/README",
        "blog/notes", "blog/Not_A_Slug/index.html", "blog/fonts/README",
        "blog/fonts/other.woff2", f"blog/fonts/{kept_name}",
        "blog/stays/index.html", "blog/stays/entry.html"}
    assert not (blog / "goes").exists()
    assert [row["slug"] for row in json.loads(files["blog.json"])["entries"]] == ["stays"]
    assert files["blog/sitemap.txt"].count(b"\n") == 1


def test_the_list_is_rewritten_before_the_entry_is_removed(site, monkeypatch):
    store = FakeStore()
    store.add("goes")
    sw.rebuild(site, store, NOW)
    store.rows.clear()
    order = []
    real_write, real_remove = sw._write, sw._remove_entry
    monkeypatch.setattr(sw, "_write", lambda path, payload: order.append(path.name)
                        or real_write(path, payload))
    monkeypatch.setattr(sw, "_remove_entry", lambda folder: order.append("removed")
                        or real_remove(folder))
    sw.rebuild(site, store, LATER)
    assert order.index("blog.json") < order.index("removed")


def test_a_folder_holding_something_else_loses_only_what_was_written(site):
    """An unpublished entry's page and document go. Anything beside them was
    not put there by this service and is not this service's to delete."""
    store = FakeStore()
    store.add("goes")
    sw.rebuild(site, store, NOW)
    (site / "blog" / "goes" / "notes.txt").write_bytes(b"someone's")
    store.rows.clear()

    out = sw.rebuild(site, store, LATER)

    assert out["ok"] is True and out["removed"] == 0
    assert sorted(path.name for path in (site / "blog" / "goes").iterdir()) == ["notes.txt"]


def test_a_link_is_never_followed_when_removing(site, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.html").write_bytes(b"precious")
    (outside / "entry.html").write_bytes(b"precious")
    sw.rebuild(site, FakeStore(), NOW)
    link = site / "blog" / "linked"
    link_folder(outside, link)

    out = sw.rebuild(site, FakeStore(), LATER)

    assert out["removed"] == 0
    assert (outside / "index.html").read_bytes() == b"precious"
    assert (outside / "entry.html").read_bytes() == b"precious"
    assert (link / "index.html").read_bytes() == b"precious"     # the link itself is still there


def test_a_link_is_never_written_through(site, tmp_path):
    """An entry whose folder is a link somewhere else is skipped, not followed."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (site / "blog").mkdir()
    link_folder(outside, site / "blog" / "first")
    store = FakeStore()
    store.add("first")

    out = sw.rebuild(site, store, NOW)

    assert out["ok"] is False and out["skipped"] == ["first"]
    assert list(outside.iterdir()) == []


def test_an_entry_the_store_cannot_supply_keeps_its_public_files(site):
    store = FakeStore()
    store.add("kept", published=at(2))
    store.add("other", published=at(1))
    sw.rebuild(site, store, NOW)
    before = tree(site)
    store.docs["kept"] = None
    store.rows["kept"]["title"] = "A title that must not reach a half-written page"
    count = degrades()

    out = sw.rebuild(site, store, LATER)

    assert out["ok"] is False and out["skipped"] == ["kept"] and out["removed"] == 0
    after = tree(site)
    assert after["blog/kept/index.html"] == before["blog/kept/index.html"]
    assert after["blog/kept/entry.html"] == before["blog/kept/entry.html"]
    # Its page is still there, so it stays in the list.
    assert [row["slug"] for row in json.loads(after["blog.json"])["entries"]] == [
        "kept", "other"]
    assert b"/blog/kept/" in after["blog/sitemap.txt"]
    assert degrades() > count
    assert sw.pending() is True


def test_an_entry_never_written_is_not_listed_without_its_document(site):
    store = FakeStore()
    store.add("ghost", published=at(2))
    store.add("real", published=at(1))
    store.docs["ghost"] = None

    out = sw.rebuild(site, store, NOW)

    assert out["ok"] is False and out["skipped"] == ["ghost"]
    files = tree(site)
    assert not any(name.startswith("blog/ghost/") for name in files)
    assert [row["slug"] for row in json.loads(files["blog.json"])["entries"]] == ["real"]
    assert files["blog/sitemap.txt"] == f"https://{repo_paths.SITE_HOST}/blog/real/\n".encode()


def test_a_typeface_the_store_cannot_supply_keeps_its_public_copy(site):
    name, data = woff(1)
    store = FakeStore()
    store.add("first", fonts=[(name, data)])
    sw.rebuild(site, store, NOW)
    store.fonts[name] = None
    count = degrades()

    out = sw.rebuild(site, store, LATER)

    assert out["ok"] is False and out["fonts_skipped"] == [name] and out["skipped"] == []
    assert (site / "blog" / "fonts" / name).read_bytes() == data
    assert degrades() > count


def test_a_row_with_a_name_that_is_not_an_address_builds_no_path(site):
    store = FakeStore()
    store.add("fine")
    for bad in ("../escape", "Has_Capitals", "fonts", "a/b", ""):
        store.rows[bad] = dict(store.rows["fine"], slug=bad)
        store.docs[bad] = DOC
    name, data = woff(1)
    store.rows["fine"]["fonts"] = [name, "../../blog.html", "x.woff2", 7]
    store.fonts[name] = data

    out = sw.rebuild(site, store, NOW)

    assert out["ok"] is False
    assert set(tree(site)) == {"blog.html", "blog.json", "blog/sitemap.txt",
                               "blog/fine/index.html", "blog/fine/entry.html",
                               f"blog/fonts/{name}"}
    assert not (site.parent / "escape").exists()
    assert [row["slug"] for row in json.loads((site / "blog.json").read_bytes())["entries"]] == [
        "fine"]


def test_rebuild_without_the_tracked_page_writes_nothing(site):
    (site / "blog.html").unlink()
    store = FakeStore()
    store.add("first")
    count = degrades()

    out = sw.rebuild(site, store, NOW)

    assert out["ok"] is False and out["written"] == 0
    assert tree(site) == {}
    assert not (site / "blog").exists()
    assert degrades() == count + 1
    assert sw.pending() is True


def test_without_the_tracked_page_nothing_is_removed_either(site):
    store = FakeStore()
    store.add("first")
    sw.rebuild(site, store, NOW)
    before = tree(site)
    (site / "blog.html").unlink()
    del before["blog.html"]
    store.rows.clear()

    out = sw.rebuild(site, store, LATER)

    assert out["ok"] is False and out["removed"] == 0
    assert tree(site) == before


MARKER = "MARKER-in-an-exception-7731"


def test_a_failed_write_returns_not_ok_and_raises_nothing(site, monkeypatch, caplog):
    store = FakeStore()
    store.add("first")

    def refuse(path, payload):
        raise OSError(MARKER)

    monkeypatch.setattr(sw, "_write", refuse)
    count = degrades()

    with caplog.at_level("DEBUG"):
        out = sw.rebuild(site, store, NOW)

    assert out["ok"] is False and out["written"] == 0
    assert degrades() > count
    assert sw.pending() is True
    assert not (site / "blog.json").exists()
    # What is logged says where, never what: an exception's text can quote a path
    # or a document.
    assert MARKER not in caplog.text


def test_one_entry_that_cannot_be_written_does_not_stop_the_others(site, monkeypatch):
    store = FakeStore()
    store.add("bad", published=at(2))
    store.add("good", published=at(1))
    real = sw._write

    def picky(path, payload):
        if path.parent.name == "bad":
            raise OSError(MARKER)
        return real(path, payload)

    monkeypatch.setattr(sw, "_write", picky)

    out = sw.rebuild(site, store, NOW)

    assert out["ok"] is False and out["skipped"] == ["bad"]
    files = tree(site)
    assert "blog/good/index.html" in files and "blog/good/entry.html" in files
    assert [row["slug"] for row in json.loads(files["blog.json"])["entries"]] == ["good"]


def test_a_store_that_raises_is_not_ok_and_touches_nothing(site, monkeypatch, caplog):
    store = FakeStore()
    store.add("first")
    sw.rebuild(site, store, NOW)
    before = tree(site)

    def boom():
        raise RuntimeError(MARKER)

    monkeypatch.setattr(store, "entries", boom)
    count = degrades()

    with caplog.at_level("DEBUG"):
        out = sw.rebuild(site, store, LATER)

    assert out["ok"] is False and out["removed"] == 0
    assert tree(site) == before
    assert degrades() == count + 1
    assert MARKER not in caplog.text


def test_site_disabled_writes_nothing(site, monkeypatch):
    store = FakeStore()
    store.add("first")
    sw.rebuild(site, store, NOW)
    before = tree(site)
    store.rows.clear()
    monkeypatch.setattr(blog_inbox, "site", lambda: {"enabled": False, "republish_min": 30})

    out = sw.rebuild(site, store, LATER)

    assert out == {"ok": True, "written": 0, "removed": 0, "skipped": [], "fonts_skipped": []}
    assert tree(site) == before
    assert sw.pending() is False


def test_a_good_rebuild_clears_what_a_failed_one_left_pending(site):
    store = FakeStore()
    store.add("first")
    store.docs["first"] = None
    assert sw.rebuild(site, store, NOW)["ok"] is False and sw.pending() is True
    store.docs["first"] = DOC
    assert sw.rebuild(site, store, LATER)["ok"] is True and sw.pending() is False


def test_leftover_temporary_files_are_swept_and_only_those(site):
    name, data = woff(1)
    store = FakeStore()
    store.add("first", fonts=[(name, data)])
    sw.rebuild(site, store, NOW)
    blog = site / "blog"
    ours = [site / ".blog.json.0123456789ab.tmp",
            blog / ".sitemap.txt.0123456789ab.tmp",
            blog / "first" / ".index.html.0123456789ab.tmp",
            blog / "first" / ".entry.html.0123456789ab.tmp",
            blog / "fonts" / f".{name}.0123456789ab.tmp"]
    theirs = [site / ".ideas.json.abc123",                 # site_ideas' own temporary name
              site / ".index.html.0123456789ab.tmp",       # shaped like ours, in a folder we never write it
              blog / "upload.tmp",
              blog / "first" / ".notes.0123456789ab.tmp",
              blog / "fonts" / ".other.woff2.0123456789ab.tmp"]
    for path in ours + theirs:
        path.write_bytes(b"half")

    out = sw.rebuild(site, store, LATER)

    assert out["ok"] is True
    assert [path.name for path in ours if path.exists()] == []
    assert [path.name for path in theirs if not path.exists()] == []


def test_rebuild_uses_the_real_site_host_and_its_default_root(site):
    """``root`` of ``None`` is the module's ``SITE_ROOT`` at CALL time, so the
    conftest redirect is what a caller that passes nothing gets."""
    store = FakeStore()
    store.add("first")
    out = sw.rebuild(None, store, NOW)
    assert out["ok"] is True
    page = (site / "blog" / "first" / "index.html").read_bytes().decode("utf-8")
    assert f'href="https://{repo_paths.SITE_HOST}/blog/first/"' in page


def test_the_suite_never_writes_into_the_served_site(site):
    assert pathlib.Path(sw.SITE_ROOT) != REAL_SITE
    assert REAL_SITE not in pathlib.Path(sw.SITE_ROOT).parents
