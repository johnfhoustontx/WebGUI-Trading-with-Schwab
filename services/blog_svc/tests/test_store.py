"""The draft and entry store: rows in SQLite, documents and typefaces as files.

Every test here works in ``tmp_path``. The conftest fixture points the store's
DEFAULT paths there too, and ``test_the_default_store_cannot_open_the_live_folder``
shows what happens to a test that gets round it.

Three kinds of check:

* what a call returns and what is then on disk, read back with ``pathlib`` and
  a second ``sqlite3`` connection (never through the store alone: a store that
  agrees with itself proves little);
* interrupted writes. ``Crash`` is a ``BaseException`` raised at a seam
  (``_commit``, ``_settle``) with the store's own clean-up switched off, so what
  is left on disk is what a killed process would leave. A FRESH ``Store`` then
  opens the folder, as the service does at start;
* refusals, each with ``tree()`` taken before and after: a refused name must
  not have reached the disk at all, not even to make a folder.

Unusual characters are built from their code points and their length asserted,
never written as a literal or an escape.
"""
import contextlib
import hashlib
import os
import pathlib
import sqlite3
import threading

import pytest

import repo_paths
from services import _degrade
from services.blog_svc import store
from shared import blog_inbox

# The live folder, worked out from the checkout and NOT read from
# ``repo_paths.BLOG_DATA``: by the time a test runs the conftest fixture has
# already pointed that name somewhere else.
REAL_DATA = repo_paths.REPO_ROOT / "services" / "blog_svc" / "data"

ID_A, ID_B, ID_C = "a" * 16, "b" * 16, "c" * 16
LINK = "https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap"

E_ACUTE = chr(0xE9)
CJK_MIDDLE = chr(0x4E2D)
GRINNING_FACE = chr(0x1F600)            # outside the BMP: four bytes in UTF-8
COMBINING_ACUTE = chr(0x301)
LONE_SURROGATE = chr(0xD800)
for _ch in (E_ACUTE, CJK_MIDDLE, GRINNING_FACE, COMBINING_ACUTE, LONE_SURROGATE):
    assert len(_ch) == 1


class Crash(BaseException):
    """The process died here. A ``BaseException`` so nothing that tidies up
    after an ordinary error can mistake it for one."""


def at(minute, hour=12) -> str:
    return f"2026-10-06T{hour:02d}:{minute:02d}:00+00:00"


def stored(minute, hour=12) -> str:
    """``at`` as the store keeps a time: UTC, always with microseconds."""
    return f"2026-10-06T{hour:02d}:{minute:02d}:00.000000+00:00"


def font(seed):
    """A typeface file and the name ``fonts.py`` gives it: a hash of the bytes."""
    data = b"wOF2" + seed
    return hashlib.sha256(data).hexdigest()[:20] + ".woff2", data


def doc(body="Hello") -> str:
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f"<title>T</title><style></style></head><body><p>{body}</p></body></html>")


def a_draft(draft_id=ID_A, **over) -> dict:
    base = {"id": draft_id, "source": "upload", "revises": None, "slug": "first-entry",
            "title": "First entry", "summary": "What it says.", "tags": ["one", "two"],
            "removed": {"script": 2, "form": 1}, "font_links": [LINK],
            "font_note": "", "received_at": at(0)}
    base.update(over)
    return base


def fields(slug="first-entry", title="First entry", summary="What it says.", tags=("one",)) -> dict:
    return {"title": title, "summary": summary, "tags": list(tags), "slug": slug}


def tree(root):
    """Everything under ``root``: ``None`` for no folder at all, else each path
    with its size, its modification time and a hash of its bytes. Two equal
    trees are a disk nothing wrote to.

    SQLite's own files are compared by SIZE only, and its ``-shm`` file not at
    all: that one is a memory map every reader touches and Windows keeps part
    of it locked. What the ROWS hold is asserted through the store beside
    every use of this."""
    root = pathlib.Path(root)
    if not root.exists():
        return None
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            out[rel] = "dir"
        elif path.name.endswith("-shm"):
            continue
        elif "blog.db" in path.name:
            out[rel] = path.stat().st_size
        else:
            seen = path.stat()
            out[rel] = (seen.st_size, seen.st_mtime_ns,
                        hashlib.sha256(path.read_bytes()).hexdigest())
    return out


def leftovers(root) -> list:
    """Files only an unfinished write leaves behind."""
    return sorted(p.relative_to(root).as_posix() for p in pathlib.Path(root).rglob("*")
                  if p.is_file() and (p.name.endswith(".tmp") or p.name.endswith(".next")))


def rows(root, sql, *args) -> list:
    """Read the database with a connection of the test's own."""
    con = sqlite3.connect(str(pathlib.Path(root) / "blog.db"))
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def run_sql(root, sql, *args) -> None:
    con = sqlite3.connect(str(pathlib.Path(root) / "blog.db"))
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


@pytest.fixture
def root(tmp_path):
    return tmp_path / "blog"


@pytest.fixture
def st(root):
    made = store.Store(data_dir=root)
    yield made
    made.close()


def reopened(root):
    """A second store on the same folder, as the service makes one at start."""
    return store.Store(data_dir=root)


# ── drafts ───────────────────────────────────────────────────────────────────

def test_a_draft_round_trips_with_its_document(st, root):
    name, data = font(b"one")
    assert st.add_draft(a_draft(), doc("Hello"), {name: data}) is None

    got = st.draft(ID_A)
    assert got == {
        "id": ID_A, "source": "upload", "revises": None, "slug": "first-entry",
        "title": "First entry", "summary": "What it says.", "tags": ["one", "two"],
        "removed": {"script": 2, "form": 1}, "fonts": [name], "font_links": [LINK],
        "font_note": "", "bytes": len(doc("Hello").encode("utf-8")),
        "digest": hashlib.sha256(doc("Hello").encode("utf-8")).hexdigest(),
        "received_at": stored(0)}
    assert st.draft_html(ID_A) == doc("Hello")
    assert st.drafts() == [got]
    # Where the private preview and the site writer will look for them.
    assert (root / "staging" / ID_A / "entry.html").read_bytes() == doc("Hello").encode("utf-8")
    assert (root / "fonts" / name).read_bytes() == data
    assert leftovers(root) == []


def test_a_draft_that_is_not_there_is_none(st):
    assert st.draft(ID_A) is None
    assert st.draft_html(ID_A) is None
    assert st.drafts() == []


def test_drafts_are_newest_first(st):
    st.add_draft(a_draft(ID_A, received_at=at(1)), doc("a"), {})
    st.add_draft(a_draft(ID_B, received_at=at(3)), doc("b"), {})
    st.add_draft(a_draft(ID_C, received_at=at(2)), doc("c"), {})
    assert [d["id"] for d in st.drafts()] == [ID_B, ID_C, ID_A]


def test_drafts_received_at_one_instant_keep_the_later_one_first(st):
    st.add_draft(a_draft(ID_A), doc("a"), {})
    st.add_draft(a_draft(ID_B), doc("b"), {})
    assert [d["id"] for d in st.drafts()] == [ID_B, ID_A]


def test_a_second_draft_under_one_id_is_refused_and_the_first_is_untouched(st, root):
    st.add_draft(a_draft(), doc("the first"), {})
    before = tree(root)
    with pytest.raises(ValueError):
        st.add_draft(a_draft(title="Another"), doc("the second"), {})
    assert tree(root) == before
    assert st.draft_html(ID_A) == doc("the first")


def test_what_the_store_can_work_out_is_not_taken_from_the_caller(st):
    """``fonts``, ``bytes`` and ``digest`` describe what is ON DISK. A caller's
    own figures for them are not believed, so a row cannot name a typeface that
    was never stored or a size the document does not have."""
    name, data = font(b"x")
    lie = a_draft(fonts=["0" * 20 + ".woff2"], bytes=1, digest="f" * 64)
    st.add_draft(lie, doc(), {name: data})
    got = st.draft(ID_A)
    assert got["fonts"] == [name]
    assert got["bytes"] == len(doc().encode("utf-8"))
    assert got["digest"] == hashlib.sha256(doc().encode("utf-8")).hexdigest()


def test_a_time_is_kept_as_utc_whatever_offset_it_came_with(st):
    st.add_draft(a_draft(received_at="2026-10-06T07:00:00-05:00"), doc(), {})
    assert st.draft(ID_A)["received_at"] == stored(0)


@pytest.mark.parametrize("bad", ["2026-10-06T12:00:00", "yesterday", "", None, 1759752000])
def test_a_time_without_a_timezone_is_refused(st, root, bad):
    """A naive time would be read as this box's clock on one machine and as UTC
    on another. The store has no clock of its own to settle it."""
    before = tree(root)
    with pytest.raises(ValueError):
        st.add_draft(a_draft(received_at=bad), doc(), {})
    assert tree(root) == before
    assert st.drafts() == []


@pytest.mark.parametrize("field, bad", [
    ("source", "email"), ("source", None), ("title", None), ("title", 7),
    ("summary", None), ("tags", "one"), ("tags", [1]), ("removed", ["script"]),
    ("removed", {"script": "2"}), ("removed", {"script": True}), ("removed", {1: 2}),
    ("font_links", LINK), ("font_links", [None]), ("font_note", None),
])
def test_a_draft_of_the_wrong_shape_is_refused_before_anything_is_written(st, root, field, bad):
    before = tree(root)
    with pytest.raises(ValueError):
        st.add_draft(a_draft(**{field: bad}), doc(), {})
    assert tree(root) == before


def test_a_draft_that_is_not_a_mapping_is_refused(st, root):
    before = tree(root)
    for bad in (None, [], "draft", 7):
        with pytest.raises(ValueError):
            st.add_draft(bad, doc(), {})
    assert tree(root) == before


@pytest.mark.parametrize("bad", [None, b"<p>bytes</p>", 7, ["<p>"]])
def test_a_document_that_is_not_text_is_refused(st, root, bad):
    before = tree(root)
    with pytest.raises(ValueError):
        st.add_draft(a_draft(), bad, {})
    assert tree(root) == before


def test_replace_draft_keeps_who_and_when_and_replaces_the_rest(st, root):
    old_name, old_data = font(b"old")
    new_name, new_data = font(b"new")
    st.add_draft(a_draft(source="chat", received_at=at(5)), doc("before"), {old_name: old_data})

    ok = st.replace_draft(ID_A, a_draft(
        ID_B, source="upload", revises="something-else", received_at=at(40),
        slug="second-thoughts", title="Second thoughts", summary="New.", tags=["three"],
        removed={"iframe": 1}, font_links=[], font_note="Typefaces could not be fetched."),
        doc("after"), {new_name: new_data})

    assert ok is True
    got = st.draft(ID_A)
    assert (got["id"], got["source"], got["revises"], got["received_at"]) == (
        ID_A, "chat", None, stored(5))
    assert (got["slug"], got["title"], got["summary"], got["tags"]) == (
        "second-thoughts", "Second thoughts", "New.", ["three"])
    assert got["removed"] == {"iframe": 1}
    assert got["fonts"] == [new_name] and got["font_links"] == []
    assert got["font_note"] == "Typefaces could not be fetched."
    assert got["bytes"] == len(doc("after").encode("utf-8"))
    assert st.draft_html(ID_A) == doc("after")
    assert (root / "staging" / ID_A / "entry.html").read_bytes() == doc("after").encode("utf-8")
    assert st.draft(ID_B) is None
    assert leftovers(root) == []


def test_replacing_a_draft_that_is_not_there_is_false_and_writes_nothing(st, root):
    name, data = font(b"x")
    before = tree(root)
    assert st.replace_draft(ID_A, a_draft(), doc(), {name: data}) is False
    assert tree(root) == before
    assert not (root / "staging").exists() and not (root / "fonts").exists()


def test_discard_removes_the_row_and_the_staged_folder(st, root):
    st.add_draft(a_draft(), doc(), {})
    assert st.discard(ID_A) is True
    assert st.draft(ID_A) is None and st.draft_html(ID_A) is None
    assert not (root / "staging" / ID_A).exists()
    assert st.discard(ID_A) is False


# ── publishing ───────────────────────────────────────────────────────────────

def test_publish_moves_the_document_and_removes_the_draft(st, root):
    name, data = font(b"one")
    st.add_draft(a_draft(), doc("the entry"), {name: data})

    entry = st.publish(ID_A, fields(title="As edited", summary="By the operator.",
                                    tags=("alpha", "beta")), at(30))

    assert entry == {
        "slug": "first-entry", "title": "As edited", "summary": "By the operator.",
        "tags": ["alpha", "beta"], "fonts": [name], "font_links": [LINK],
        "digest": hashlib.sha256(doc("the entry").encode("utf-8")).hexdigest(),
        "published_at": stored(30), "updated_at": stored(30)}
    assert st.entry("first-entry") == entry and st.entries() == [entry]
    assert st.entry_html("first-entry") == doc("the entry")
    assert (root / "published" / "first-entry" / "entry.html").read_bytes() == \
        doc("the entry").encode("utf-8")
    assert st.draft(ID_A) is None and st.drafts() == []
    assert not (root / "staging" / ID_A).exists()
    assert leftovers(root) == []


def test_publishing_a_draft_that_is_not_there_is_none(st, root):
    assert st.publish(ID_A, fields(), at(1)) is None
    assert st.entries() == [] and not (root / "published").exists()


def test_a_revision_keeps_the_first_publication_date(st, root):
    old_name, old_data = font(b"old")
    new_name, new_data = font(b"new")
    st.add_draft(a_draft(ID_A), doc("first words"), {old_name: old_data})
    st.publish(ID_A, fields(), at(10))

    st.add_draft(a_draft(ID_B, revises="first-entry", font_links=[]), doc("second words"),
                 {new_name: new_data})
    entry = st.publish(ID_B, fields(title="Reworded", tags=()), at(50))

    assert entry["published_at"] == stored(10) and entry["updated_at"] == stored(50)
    assert entry["title"] == "Reworded" and entry["tags"] == []
    assert entry["fonts"] == [new_name] and entry["font_links"] == []
    assert st.entries() == [entry]
    assert st.entry_html("first-entry") == doc("second words")
    assert (root / "published" / "first-entry" / "entry.html").read_bytes() == \
        doc("second words").encode("utf-8")
    assert st.drafts() == [] and not (root / "staging" / ID_B).exists()
    assert leftovers(root) == []


def test_a_revision_cannot_rename_its_entry(st, root):
    st.add_draft(a_draft(ID_A), doc("first"), {})
    st.publish(ID_A, fields(), at(10))
    st.add_draft(a_draft(ID_B, revises="first-entry"), doc("second"), {})
    before = tree(root)

    assert st.publish(ID_B, fields(slug="another-address"), at(20)) is None

    assert tree(root) == before
    assert [e["slug"] for e in st.entries()] == ["first-entry"]
    assert st.entry_html("first-entry") == doc("first")
    assert st.draft_html(ID_B) == doc("second")


def test_publishing_a_new_draft_onto_a_taken_slug_is_refused(st, root):
    """Only a draft filed AS a revision may replace an entry. A new one that
    happens to ask for the same address changes nothing."""
    st.add_draft(a_draft(ID_A), doc("first"), {})
    first = st.publish(ID_A, fields(), at(10))
    st.add_draft(a_draft(ID_B), doc("second"), {})
    before = tree(root)

    assert st.publish(ID_B, fields(), at(20)) is None

    assert tree(root) == before
    assert st.entries() == [first] and st.entry_html("first-entry") == doc("first")
    assert st.draft(ID_B) is not None and st.draft_html(ID_B) == doc("second")


def test_a_revision_of_an_entry_since_unpublished_is_a_new_entry(st):
    st.add_draft(a_draft(ID_A), doc("first"), {})
    st.publish(ID_A, fields(), at(10))
    st.add_draft(a_draft(ID_B, revises="first-entry"), doc("second"), {})
    st.unpublish("first-entry")

    entry = st.publish(ID_B, fields(slug="fresh-start"), at(40))

    assert entry["slug"] == "fresh-start"
    assert entry["published_at"] == stored(40) == entry["updated_at"]
    assert st.entry_html("fresh-start") == doc("second")


@pytest.mark.parametrize("title", ["", "   "])
def test_an_entry_without_a_title_is_refused(st, root, title):
    st.add_draft(a_draft(), doc(), {})
    before = tree(root)
    assert st.publish(ID_A, fields(title=title), at(1)) is None
    assert tree(root) == before
    assert st.entries() == [] and st.draft(ID_A) is not None


@pytest.mark.parametrize("bad", [
    None, [], {"title": "T", "summary": "", "tags": []},                    # no slug
    {"title": None, "summary": "", "tags": [], "slug": "first-entry"},
    {"title": "T", "summary": 7, "tags": [], "slug": "first-entry"},
    {"title": "T", "summary": "", "tags": "one", "slug": "first-entry"},
    {"title": "T", "summary": "", "tags": [3], "slug": "first-entry"},
])
def test_publish_refuses_fields_of_the_wrong_shape(st, root, bad):
    st.add_draft(a_draft(), doc(), {})
    before = tree(root)
    with pytest.raises(ValueError):
        st.publish(ID_A, bad, at(1))
    assert tree(root) == before


def test_publish_refuses_a_time_without_a_timezone(st, root):
    st.add_draft(a_draft(), doc(), {})
    before = tree(root)
    with pytest.raises(ValueError):
        st.publish(ID_A, fields(), "2026-10-06T12:00:00")
    assert tree(root) == before


def test_unpublish_removes_the_entry_and_its_document(st, root):
    st.add_draft(a_draft(), doc(), {})
    st.publish(ID_A, fields(), at(1))
    assert st.unpublish("first-entry") is True
    assert st.entry("first-entry") is None and st.entry_html("first-entry") is None
    assert st.entries() == [] and not (root / "published" / "first-entry").exists()
    assert st.unpublish("first-entry") is False


def test_entries_are_newest_published_first(st):
    for draft_id, slug, minute in ((ID_A, "one", 5), (ID_B, "two", 9), (ID_C, "three", 7)):
        st.add_draft(a_draft(draft_id, slug=slug), doc(slug), {})
        st.publish(draft_id, fields(slug=slug), at(minute))
    assert [e["slug"] for e in st.entries()] == ["two", "three", "one"]


def test_a_revision_does_not_move_an_entry_up_the_list(st):
    """The list is by FIRST publication. Fixing a typo in an old entry must not
    put it back at the top of the Blog page."""
    for draft_id, slug, minute in ((ID_A, "older", 5), (ID_B, "newer", 9)):
        st.add_draft(a_draft(draft_id, slug=slug), doc(slug), {})
        st.publish(draft_id, fields(slug=slug), at(minute))
    st.add_draft(a_draft(ID_C, slug="older", revises="older"), doc("fixed"), {})
    st.publish(ID_C, fields(slug="older"), at(55))
    assert [e["slug"] for e in st.entries()] == ["newer", "older"]


# ── typefaces ────────────────────────────────────────────────────────────────

def test_a_typeface_already_in_the_pool_is_not_written_again(st, root):
    name, data = font(b"shared")
    st.add_draft(a_draft(ID_A), doc("a"), {name: data})
    long_ago = 1_000_000_000
    os.utime(root / "fonts" / name, (long_ago, long_ago))

    st.add_draft(a_draft(ID_B), doc("b"), {name: data})

    assert int((root / "fonts" / name).stat().st_mtime) == long_ago
    assert sorted(p.name for p in (root / "fonts").iterdir()) == [name]
    assert st.draft(ID_B)["fonts"] == [name]


def test_a_typeface_cut_short_on_disk_is_written_again(st, root):
    """The name is a hash of the whole file. A file of that name and another
    size is not that file - a write that was cut off - so it is replaced."""
    name, data = font(b"whole")
    st.add_draft(a_draft(ID_A), doc("a"), {name: data})
    (root / "fonts" / name).write_bytes(data[:3])
    st.add_draft(a_draft(ID_B), doc("b"), {name: data})
    assert (root / "fonts" / name).read_bytes() == data


def test_fonts_in_use_is_every_draft_and_every_entry(st):
    one, two, three = font(b"1"), font(b"2"), font(b"3")
    st.add_draft(a_draft(ID_A, slug="published"), doc("a"), dict([one, two]))
    st.publish(ID_A, fields(slug="published"), at(1))
    st.add_draft(a_draft(ID_B), doc("b"), dict([two, three]))
    assert st.fonts_in_use() == {one[0], two[0], three[0]}
    assert st.font_bytes(two[0]) == two[1]
    assert st.font_bytes(font(b"never stored")[0]) is None


def test_a_font_still_named_by_a_draft_is_not_pruned(st, root):
    kept, gone = font(b"kept"), font(b"gone")
    st.add_draft(a_draft(ID_A), doc("a"), dict([kept]))
    st.add_draft(a_draft(ID_B), doc("b"), dict([gone]))
    st.discard(ID_B)

    assert st.prune_fonts() == 1

    assert sorted(p.name for p in (root / "fonts").iterdir()) == [kept[0]]
    assert st.prune_fonts() == 0


def test_a_font_still_named_by_an_entry_is_not_pruned(st, root):
    kept = font(b"kept")
    st.add_draft(a_draft(), doc(), dict([kept]))
    st.publish(ID_A, fields(), at(1))
    assert st.prune_fonts() == 0
    assert (root / "fonts" / kept[0]).read_bytes() == kept[1]
    st.unpublish("first-entry")
    assert st.prune_fonts() == 1
    assert list((root / "fonts").iterdir()) == []


def test_pruning_deletes_only_what_is_named_like_a_typeface(st, root):
    gone = font(b"gone")
    st.add_draft(a_draft(), doc(), dict([gone]))
    st.discard(ID_A)
    pool = root / "fonts"
    (pool / "README").write_bytes(b"keep")
    (pool / "0123456789abcdef0123.woff").write_bytes(b"keep")       # woff, not woff2
    (pool / ("0" * 19 + ".woff2")).write_bytes(b"keep")             # one character short
    (pool / "nested").mkdir()

    assert st.prune_fonts() == 1

    assert sorted(p.name for p in pool.iterdir()) == [
        "0" * 19 + ".woff2", "0123456789abcdef0123.woff", "README", "nested"]


def test_pruning_with_no_pool_at_all_is_nothing(st, root):
    assert st.prune_fonts() == 0
    assert not (root / "fonts").exists()


def test_a_prune_cannot_run_between_a_typeface_and_the_row_that_names_it(st, root, monkeypatch):
    """``add_draft`` writes its typefaces before its row. A prune arriving in
    between would see files nothing names yet and delete them - so it must WAIT.
    The prune is started from inside ``add_draft``, after the typefaces are on
    disk and before the row is; it has to still be waiting a third of a second
    later, and what it finally deletes must not be the new draft's."""
    name, data = font(b"new")
    seen = {}
    land = st._land

    def land_and_let_a_prune_try(*args, **kwargs):
        assert (root / "fonts" / name).exists(), "the typeface is written before the document"
        seen["prune"] = threading.Thread(target=lambda: seen.setdefault("count", st.prune_fonts()))
        seen["prune"].start()
        seen["prune"].join(0.3)
        seen["was_waiting"] = seen["prune"].is_alive()
        return land(*args, **kwargs)

    monkeypatch.setattr(st, "_land", land_and_let_a_prune_try)
    st.add_draft(a_draft(), doc(), {name: data})
    seen["prune"].join(10)

    assert seen["was_waiting"] is True
    assert not seen["prune"].is_alive() and seen["count"] == 0
    assert (root / "fonts" / name).read_bytes() == data


def test_a_prune_from_a_second_store_waits_too(root):
    """Two ``Store`` objects on one folder (one per thread is an ordinary way to
    use SQLite) must be as safe as one."""
    name, data = font(b"new")
    one, two = store.Store(data_dir=root), store.Store(data_dir=root)
    seen = {}
    land = one._land

    def land_and_let_a_prune_try(*args, **kwargs):
        seen["prune"] = threading.Thread(target=lambda: seen.setdefault("count", two.prune_fonts()))
        seen["prune"].start()
        seen["prune"].join(0.3)
        seen["was_waiting"] = seen["prune"].is_alive()
        return land(*args, **kwargs)

    one._land = land_and_let_a_prune_try
    try:
        one.add_draft(a_draft(), doc(), {name: data})
        seen["prune"].join(10)
        assert seen["was_waiting"] is True and seen["count"] == 0
        assert (root / "fonts" / name).read_bytes() == data
    finally:
        one.close()
        two.close()


def test_a_typeface_must_be_named_by_its_own_content(st, root):
    """A file already in the pool is never rewritten, so a file stored under a
    name that is not its hash would be served to every later entry that asks
    for the real one."""
    name, data = font(b"honest")
    before = tree(root)
    with pytest.raises(ValueError):
        st.add_draft(a_draft(), doc(), {name: data + b"!"})
    assert tree(root) == before


@pytest.mark.parametrize("bad", [b"", "text", None, 7])
def test_a_typeface_must_be_bytes_and_not_empty(st, root, bad):
    name, _ = font(b"x")
    before = tree(root)
    with pytest.raises(ValueError):
        st.add_draft(a_draft(), doc(), {name: bad})
    assert tree(root) == before


def test_typefaces_must_come_as_a_mapping(st, root):
    before = tree(root)
    for bad in (None, [], "x"):
        with pytest.raises(ValueError):
            st.add_draft(a_draft(), doc(), bad)
    assert tree(root) == before


# ── no path is built from a name that was not checked ────────────────────────

BAD_IDS = ["../x", "a/b", "", "fonts", "A", "0" * 15, "0" * 17, "A" * 16, "g" * 16,
           "a" * 16 + "\n", " " + "a" * 16, None, 7, b"a" * 16, "..", "con"]


@pytest.mark.parametrize("bad", BAD_IDS)
def test_no_path_is_built_from_an_unvalidated_name(st, root, bad):
    """A draft id becomes a folder name. Anything ``blog_inbox.new_id`` could
    not have made raises before the disk is touched - not even a folder."""
    st.add_draft(a_draft(ID_B), doc(), {})
    before = tree(root)
    for call in (lambda: st.add_draft(a_draft(bad), doc(), {}),
                 lambda: st.replace_draft(bad, a_draft(), doc(), {}),
                 lambda: st.draft(bad),
                 lambda: st.draft_html(bad),
                 lambda: st.discard(bad),
                 lambda: st.publish(bad, fields(), at(1))):
        with pytest.raises(ValueError):
            call()
    assert tree(root) == before


BAD_SLUGS = ["../x", "a/b", "a\\b", "", "fonts", "A", "First-Entry", "a--b", "-a", "a-",
             "a.b", "a b", "abc.", "abc ", " abc", "abc\n", "..", ".", "x" * 121,
             None, 7, "first-entry/../../x", chr(0x212A) + "elvin"]


def test_the_kelvin_sign_is_one_character():
    assert len(chr(0x212A)) == 1


@pytest.mark.parametrize("bad", BAD_SLUGS)
def test_no_path_is_built_from_an_unvalidated_slug(st, root, bad):
    """A slug becomes a folder name. It must already be spelled the way the
    validator spells it: "A" lower-cases to a valid address and "abc " trims to
    one, and neither is what was handed in."""
    st.add_draft(a_draft(ID_A), doc(), {})
    st.publish(ID_A, fields(), at(1))
    st.add_draft(a_draft(ID_B, slug="waiting"), doc(), {})
    before = tree(root)
    calls = [lambda: st.entry(bad),
             lambda: st.entry_html(bad),
             lambda: st.unpublish(bad),
             lambda: st.publish(ID_B, fields(slug=bad), at(2)),
             lambda: st.add_draft(a_draft(ID_C, slug=bad), doc(), {}),
             lambda: st.replace_draft(ID_B, a_draft(ID_B, slug=bad), doc(), {})]
    if bad is not None:                     # ``revises=None`` is "not a revision"
        calls.append(lambda: st.add_draft(a_draft(ID_C, revises=bad), doc(), {}))
    for call in calls:
        with pytest.raises(ValueError):
            call()
    assert tree(root) == before
    assert [e["slug"] for e in st.entries()] == ["first-entry"]
    assert st.draft(ID_B)["slug"] == "waiting"


BAD_FONT_NAMES = ["../x", "a/b", "", "fonts", "A", "0" * 15, "0" * 20, "0" * 20 + ".woff",
                  "0" * 20 + ".WOFF2", "A" * 20 + ".woff2", "0" * 19 + ".woff2",
                  "0" * 21 + ".woff2", "0" * 20 + ".woff2\n", "../" + "0" * 20 + ".woff2",
                  None, 7]


@pytest.mark.parametrize("bad", BAD_FONT_NAMES)
def test_no_path_is_built_from_an_unvalidated_font_name(st, root, bad):
    before = tree(root)
    with pytest.raises(ValueError):
        st.add_draft(a_draft(), doc(), {bad: b"wOF2 data"})
    with pytest.raises(ValueError):
        st.font_bytes(bad)
    assert tree(root) == before
    assert st.drafts() == []


DEVICE_NAMES = ["con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9"]


@pytest.mark.parametrize("name", DEVICE_NAMES)
def test_an_address_that_is_a_windows_device_is_refused(st, root, name):
    """``con`` and ``nul`` are well-formed addresses, and on Windows they are
    not folders: opening one opens the console or discards what is written.
    Prod is Linux, where they would work - which is exactly how an entry
    nobody can restore on a Windows box would get published. Refused on every
    platform, by the store itself and not by the shape of an address."""
    assert blog_inbox.SLUG_RE.match(name), "the shape alone does not refuse it"
    st.add_draft(a_draft(ID_A), doc(), {})
    before = tree(root)
    for call in (lambda: st.add_draft(a_draft(ID_B, slug=name), doc(), {}),
                 lambda: st.add_draft(a_draft(ID_B, revises=name), doc(), {}),
                 lambda: st.replace_draft(ID_A, a_draft(slug=name), doc(), {}),
                 lambda: st.publish(ID_A, fields(slug=name), at(1)),
                 lambda: st.entry(name),
                 lambda: st.entry_html(name),
                 lambda: st.unpublish(name)):
        with pytest.raises(ValueError):
            call()
    assert tree(root) == before
    assert st.entries() == []


@pytest.mark.parametrize("name", ["console", "nulls", "com10", "lpt", "con-artists", "aux-in"])
def test_an_address_that_only_looks_like_a_device_is_fine(st, name):
    st.add_draft(a_draft(slug=name), doc(), {})
    assert st.publish(ID_A, fields(slug=name), at(1))["slug"] == name
    assert st.entry_html(name) == doc()


@pytest.mark.parametrize("bad", ["abc.", "abc ", "abc. ", "a.", ". ", "abc" + chr(0xA0)])
def test_the_shape_of_an_address_already_excludes_a_trailing_dot_or_space(bad):
    """Windows drops a trailing dot or space from a name, so ``abc.`` and
    ``abc`` would be one folder. No such address can be spelled."""
    assert blog_inbox.SLUG_RE.match(bad) is None


def test_no_break_space_is_one_character():
    assert len(chr(0xA0)) == 1


def test_a_validator_that_let_a_path_through_is_still_stopped(st, root, monkeypatch):
    """Defence in depth. With every validator replaced by one that accepts
    anything, a name that climbs out of its folder is still refused, because
    the finished path's parent is not the folder it was built in."""
    st.add_draft(a_draft(ID_A), doc(), {})
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "entry.html").write_bytes(b"not the store's")
    monkeypatch.setattr(blog_inbox, "is_id", lambda raw: True)
    monkeypatch.setattr(blog_inbox, "existing_slug", lambda raw: raw)
    monkeypatch.setattr(blog_inbox, "clean_slug", lambda raw: raw)
    before = tree(root.parent)

    for call in (lambda: st.draft_html("../../outside"),
                 lambda: st.discard("../../outside"),
                 lambda: st.add_draft(a_draft("../../outside"), doc(), {}),
                 lambda: st.entry_html("../../outside"),
                 lambda: st.unpublish("../../outside"),
                 lambda: st.publish(ID_A, fields(slug="../../outside"), at(1)),
                 lambda: st.publish(ID_A, fields(slug="a/b"), at(1))):
        with pytest.raises(ValueError):
            call()

    assert tree(root.parent) == before
    assert (outside / "entry.html").read_bytes() == b"not the store's"


# ── where the store lives ────────────────────────────────────────────────────

def test_the_default_paths_resolve_at_call_time(monkeypatch, tmp_path):
    """Read from ``repo_paths`` when a ``Store`` is MADE, never when the module
    is imported or a function defined: a default bound at ``def`` time cannot
    be redirected, which is how a suite once wrote into production stores."""
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setattr(repo_paths, "BLOG_DATA", elsewhere)
    monkeypatch.setattr(repo_paths, "BLOG_DB", elsewhere / "blog.db")
    with store.Store() as made:
        made.add_draft(a_draft(), doc(), {})
        assert (elsewhere / "blog.db").is_file()
        assert (elsewhere / "staging" / ID_A / "entry.html").is_file()


def test_this_suite_never_sees_the_live_folder_by_default(tmp_path):
    """The conftest fixture, seen from a test that did nothing to ask for it."""
    assert pathlib.Path(repo_paths.BLOG_DATA).resolve() != REAL_DATA.resolve()
    assert tmp_path.resolve() in pathlib.Path(repo_paths.BLOG_DATA).resolve().parents
    assert pathlib.Path(repo_paths.BLOG_DB).parent == pathlib.Path(repo_paths.BLOG_DATA)
    with store.Store() as made:
        made.add_draft(a_draft(), doc(), {})
    assert (pathlib.Path(repo_paths.BLOG_DATA) / "staging" / ID_A / "entry.html").is_file()


def test_the_default_store_cannot_open_the_live_folder(monkeypatch):
    """A test that gets round the fixture - here by putting the real paths
    back - is REFUSED by the repo-root guard on ``sqlite3.connect``, and the
    refusal comes before the store has made anything: not the database, not
    even the data folder."""
    monkeypatch.setattr(repo_paths, "BLOG_DATA", REAL_DATA)
    monkeypatch.setattr(repo_paths, "BLOG_DB", REAL_DATA / "blog.db")
    before = tree(REAL_DATA)
    with pytest.raises(RuntimeError, match="live database"):
        store.Store()
    assert tree(REAL_DATA) == before


def test_the_live_folder_is_refused_however_it_is_reached(tmp_path):
    for kwargs in ({"db_path": REAL_DATA / "blog.db"},
                   {"data_dir": REAL_DATA},
                   {"db_path": REAL_DATA / "blog.db", "data_dir": tmp_path / "files"}):
        with pytest.raises(RuntimeError, match="live database"):
            store.Store(**kwargs)
    assert not (tmp_path / "files").exists()


def test_a_database_given_alone_keeps_its_files_beside_it(tmp_path):
    """Never ``db_path`` from the caller and the FILES from ``repo_paths``: the
    guard watches ``sqlite3.connect`` and would not see documents landing in the
    live folder."""
    with store.Store(db_path=tmp_path / "x" / "blog.db") as made:
        made.add_draft(a_draft(), doc(), {})
    assert (tmp_path / "x" / "staging" / ID_A / "entry.html").is_file()


def test_making_a_store_writes_only_its_database(root):
    with store.Store(data_dir=root):
        pass
    assert sorted(p.name for p in root.iterdir() if "blog.db" not in p.name) == []
    assert (root / "blog.db").is_file()


def test_the_tables_are_the_ones_the_design_names(st, root):
    columns = {name: [r[1] for r in rows(root, f"PRAGMA table_info({name})")]
               for name in ("drafts", "entries", "submissions", "kv")}
    assert columns == {
        "drafts": ["id", "source", "revises", "slug", "title", "summary", "tags", "removed",
                   "fonts", "font_links", "font_note", "bytes", "digest", "received_at"],
        "entries": ["slug", "title", "summary", "tags", "fonts", "font_links", "digest",
                    "published_at", "updated_at"],
        "submissions": ["at"],
        "kv": ["key", "value"]}


# ── an interrupted write, and what repair makes of it ───────────────────────

def test_a_draft_whose_row_fails_leaves_no_staged_folder(st, root, monkeypatch):
    """Files first, then the row. If the row does not go in, the folder this
    call made is taken away again - by the call itself, with no repair."""
    with failing_commit(monkeypatch, st), pytest.raises(sqlite3.OperationalError):
        st.add_draft(a_draft(), doc(), {})

    assert st.draft(ID_A) is None
    assert not (root / "staging" / ID_A).exists()
    assert leftovers(root) == []
    st.add_draft(a_draft(), doc("again"), {})          # and the store still works
    assert st.draft_html(ID_A) == doc("again")


@contextlib.contextmanager
def failing_commit(monkeypatch, target):
    """An ORDINARY failure of the commit, for as long as the block lasts.

    ⚠ Never ``monkeypatch.undo()`` to end a patch in this suite. The conftest's
    redirect of the default paths and the repo-root guard on ``sqlite3.connect``
    are set through the SAME fixture, and undoing would take them off too."""
    def boom():
        raise sqlite3.OperationalError("disk I/O error")
    with monkeypatch.context() as patch:
        patch.setattr(target, "_commit", boom)
        yield


def crash_at(monkeypatch, target, seam):
    """Kill the process at ``seam``: raise there, and switch off the clean-up
    an ordinary failure would get."""
    def die(*_args, **_kwargs):
        raise Crash()
    monkeypatch.setattr(target, seam, die)
    monkeypatch.setattr(target, "_undo", lambda *a, **k: None)


def test_a_crash_after_a_drafts_files_and_before_its_row(root, monkeypatch):
    name, data = font(b"orphan")
    first = store.Store(data_dir=root)
    crash_at(monkeypatch, first, "_commit")
    with pytest.raises(Crash):
        first.add_draft(a_draft(), doc(), {name: data})
    first.close()
    assert (root / "staging" / ID_A / "entry.html").is_file()      # the state left behind

    with reopened(root) as after:
        report = after.repair()
        assert report["ok"] is True and report["staging_removed"] == [ID_A]
        assert after.draft(ID_A) is None and after.drafts() == []
        assert not (root / "staging" / ID_A).exists()
        # The typeface nothing names is the pruner's, not repair's, to remove.
        assert after.prune_fonts() == 1
        assert after.repair() == dict(report, staging_removed=[])


def test_a_crash_after_a_new_entrys_file_and_before_its_rows(root, monkeypatch):
    first = store.Store(data_dir=root)
    first.add_draft(a_draft(), doc("the entry"), {})
    crash_at(monkeypatch, first, "_commit")
    with pytest.raises(Crash):
        first.publish(ID_A, fields(), at(5))
    first.close()
    assert (root / "published" / "first-entry").is_dir()

    with reopened(root) as after:
        report = after.repair()
        assert report["published_removed"] == ["first-entry"]
        assert report["entries_missing"] == [] and report["drafts_removed"] == []
        assert not (root / "published" / "first-entry").exists()
        # Nothing was lost: the draft is still waiting, whole.
        assert after.entries() == []
        assert after.draft_html(ID_A) == doc("the entry")
        assert after.publish(ID_A, fields(), at(6))["published_at"] == stored(6)
        assert leftovers(root) == []


def test_a_crash_after_an_entrys_rows_and_before_its_staged_folder_goes(root, monkeypatch):
    first = store.Store(data_dir=root)
    first.add_draft(a_draft(), doc("the entry"), {})
    monkeypatch.setattr(first, "_drop", lambda folder: True)         # dies before the tidy-up
    assert first.publish(ID_A, fields(), at(5)) is not None
    first.close()
    assert (root / "staging" / ID_A).is_dir()

    with reopened(root) as after:
        report = after.repair()
        assert report["staging_removed"] == [ID_A]
        assert not (root / "staging" / ID_A).exists()
        assert after.entry_html("first-entry") == doc("the entry")


def test_a_crash_while_a_revision_is_staged_changes_nothing_public(root, monkeypatch):
    """The revision's document is on disk BESIDE the published one and the rows
    have not changed. The published entry must still be the old one, in every
    respect, and the revision must still be waiting."""
    first = store.Store(data_dir=root)
    first.add_draft(a_draft(ID_A), doc("old words"), {})
    old = first.publish(ID_A, fields(), at(5))
    first.add_draft(a_draft(ID_B, revises="first-entry"), doc("new words"), {})
    crash_at(monkeypatch, first, "_commit")
    with pytest.raises(Crash):
        first.publish(ID_B, fields(title="Reworded"), at(9))
    first.close()
    assert leftovers(root) == ["published/first-entry/entry.html.next"]

    with reopened(root) as after:
        assert after.entry_html("first-entry") == doc("old words")   # before any repair
        report = after.repair()
        assert report["ok"] is True and report["temp_removed"] == 1
        assert report["entries_missing"] == [] and report["settled"] == 0
        assert after.entry("first-entry") == old
        assert after.entry_html("first-entry") == doc("old words")
        assert (root / "published" / "first-entry" / "entry.html").read_bytes() == \
            doc("old words").encode("utf-8")
        assert after.draft_html(ID_B) == doc("new words")
        assert leftovers(root) == []


def test_a_crash_after_a_revisions_rows_and_before_its_file_moves(root, monkeypatch):
    """The commit is the decision. Once the rows say the revision is published,
    the store reads the NEW document even though the old one still holds the
    name, and repair finishes the move."""
    first = store.Store(data_dir=root)
    first.add_draft(a_draft(ID_A), doc("old words"), {})
    first.publish(ID_A, fields(), at(5))
    first.add_draft(a_draft(ID_B, revises="first-entry"), doc("new words"), {})
    crash_at(monkeypatch, first, "_settle")
    with pytest.raises(Crash):
        first.publish(ID_B, fields(title="Reworded"), at(9))
    first.close()
    published = root / "published" / "first-entry"
    assert (published / "entry.html").read_bytes() == doc("old words").encode("utf-8")

    with reopened(root) as after:
        assert after.entry("first-entry")["title"] == "Reworded"
        assert after.entry_html("first-entry") == doc("new words")   # before any repair
        report = after.repair()
        assert report["settled"] == 1 and report["entries_missing"] == []
        assert report["staging_removed"] == [ID_B]
        assert (published / "entry.html").read_bytes() == doc("new words").encode("utf-8")
        assert after.entry_html("first-entry") == doc("new words")
        assert after.drafts() == [] and leftovers(root) == []


def test_a_crash_while_a_drafts_replacement_is_staged(root, monkeypatch):
    old_font, new_font = font(b"old"), font(b"new")
    first = store.Store(data_dir=root)
    first.add_draft(a_draft(), doc("before"), dict([old_font]))
    was = first.draft(ID_A)
    crash_at(monkeypatch, first, "_commit")
    with pytest.raises(Crash):
        first.replace_draft(ID_A, a_draft(title="After"), doc("after"), dict([new_font]))
    first.close()

    with reopened(root) as after:
        assert after.draft_html(ID_A) == doc("before")
        report = after.repair()
        assert report["temp_removed"] == 1 and report["drafts_removed"] == []
        assert after.draft(ID_A) == was
        assert after.draft_html(ID_A) == doc("before")
        # The old document's typeface is still named, so it survives a prune.
        assert after.prune_fonts() == 1
        assert (root / "fonts" / old_font[0]).is_file()
        assert leftovers(root) == []


def test_a_crash_after_a_replacements_row_and_before_its_file_moves(root, monkeypatch):
    """The other half. The row now names the NEW document's typefaces, so the
    document that goes with it must be the new one - a prune here would
    otherwise strip the typefaces from whichever document was left showing."""
    old_font, new_font = font(b"old"), font(b"new")
    first = store.Store(data_dir=root)
    first.add_draft(a_draft(), doc("before"), dict([old_font]))
    crash_at(monkeypatch, first, "_settle")
    with pytest.raises(Crash):
        first.replace_draft(ID_A, a_draft(title="After"), doc("after"), dict([new_font]))
    first.close()

    with reopened(root) as after:
        assert after.draft(ID_A)["title"] == "After"
        assert after.draft_html(ID_A) == doc("after")                # before any repair
        assert after.repair()["settled"] == 1
        staged = root / "staging" / ID_A / "entry.html"
        assert staged.read_bytes() == doc("after").encode("utf-8")
        assert after.draft(ID_A)["fonts"] == [new_font[0]]
        assert after.prune_fonts() == 1 and (root / "fonts" / new_font[0]).is_file()
        assert leftovers(root) == []


def test_a_replacement_whose_row_fails_puts_nothing_in_the_way(st, root, monkeypatch):
    st.add_draft(a_draft(), doc("before"), {})
    was = st.draft(ID_A)

    with failing_commit(monkeypatch, st), pytest.raises(sqlite3.OperationalError):
        st.replace_draft(ID_A, a_draft(title="After"), doc("after"), {})

    assert st.draft(ID_A) == was and st.draft_html(ID_A) == doc("before")
    assert leftovers(root) == []


def test_a_revision_whose_rows_fail_leaves_the_entry_as_it_was(st, root, monkeypatch):
    st.add_draft(a_draft(ID_A), doc("old words"), {})
    old = st.publish(ID_A, fields(), at(5))
    st.add_draft(a_draft(ID_B, revises="first-entry"), doc("new words"), {})

    with failing_commit(monkeypatch, st), pytest.raises(sqlite3.OperationalError):
        st.publish(ID_B, fields(), at(9))

    assert st.entry("first-entry") == old and st.entry_html("first-entry") == doc("old words")
    assert st.draft_html(ID_B) == doc("new words")
    assert leftovers(root) == []


def test_a_new_entry_whose_rows_fail_leaves_no_published_folder(st, root, monkeypatch):
    st.add_draft(a_draft(), doc(), {})

    with failing_commit(monkeypatch, st), pytest.raises(sqlite3.OperationalError):
        st.publish(ID_A, fields(), at(5))

    assert st.entries() == [] and not (root / "published" / "first-entry").exists()
    assert st.draft_html(ID_A) == doc()


def test_a_move_that_fails_after_the_commit_is_counted_and_the_store_carries_on(
        st, root, monkeypatch):
    """Not a crash: the rename itself is refused (on Windows, a file someone
    has open). The publish HAS happened - the rows say so - so the call
    answers with the entry, the store serves the right document, and the
    failure is counted for /health rather than raised into the command."""
    st.add_draft(a_draft(ID_A), doc("old words"), {})
    st.publish(ID_A, fields(), at(5))
    st.add_draft(a_draft(ID_B, revises="first-entry"), doc("new words"), {})
    real_replace = os.replace

    def refuse_the_document(src, dst, *a, **k):
        if str(dst).endswith("entry.html"):
            raise PermissionError(13, "in use")
        return real_replace(src, dst, *a, **k)
    counted = _degrade.counts().get("blog.store", 0)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", refuse_the_document)
        entry = st.publish(ID_B, fields(title="Reworded"), at(9))
        assert entry is not None and entry["title"] == "Reworded"
        assert _degrade.counts().get("blog.store", 0) == counted + 1
        assert st.entry_html("first-entry") == doc("new words")

    assert st.repair()["settled"] == 1
    assert (root / "published" / "first-entry" / "entry.html").read_bytes() == \
        doc("new words").encode("utf-8")
    assert leftovers(root) == []


def test_repair_removes_a_staged_folder_no_draft_names(st, root):
    st.add_draft(a_draft(ID_A), doc("kept"), {})
    st.add_draft(a_draft(ID_B), doc("orphan"), {})
    run_sql(root, "DELETE FROM drafts WHERE id=?", ID_B)

    report = st.repair()

    assert report["staging_removed"] == [ID_B]
    assert not (root / "staging" / ID_B).exists()
    assert st.draft_html(ID_A) == doc("kept")


@pytest.mark.parametrize("damage", ["folder", "file", "changed"])
def test_repair_removes_a_draft_whose_document_is_gone(st, root, damage, caplog):
    """A draft nobody can preview is not a draft. "Gone" includes a file that
    is there and is not the one the row describes: what would be published is
    not what was cleaned."""
    st.add_draft(a_draft(ID_A), doc("kept"), {})
    st.add_draft(a_draft(ID_B), doc("SECRET-MARK"), {})
    folder = root / "staging" / ID_B
    if damage == "folder":
        (folder / "entry.html").unlink()
        folder.rmdir()
    elif damage == "file":
        (folder / "entry.html").unlink()
    else:
        (folder / "entry.html").write_bytes(b"<p>SECRET-MARK swapped</p>")

    with caplog.at_level("WARNING"):
        report = st.repair()

    assert report["drafts_removed"] == [ID_B] and report["ok"] is True
    assert st.draft(ID_B) is None and [d["id"] for d in st.drafts()] == [ID_A]
    assert not folder.exists()
    assert ID_B in caplog.text, "a draft that is dropped is logged by its id"
    assert "SECRET-MARK" not in caplog.text


def test_repair_removes_a_published_folder_no_entry_names(st, root):
    st.add_draft(a_draft(ID_A, slug="kept"), doc("kept"), {})
    st.publish(ID_A, fields(slug="kept"), at(1))
    stray = root / "published" / "nobody-published-this"
    stray.mkdir()
    (stray / "entry.html").write_bytes(b"<p>stray</p>")

    report = st.repair()

    assert report["published_removed"] == ["nobody-published-this"]
    assert not stray.exists()
    assert st.entry_html("kept") == doc("kept")


@pytest.mark.parametrize("damage", ["folder", "file", "changed"])
def test_repair_keeps_and_reports_an_entry_whose_document_is_gone(st, root, damage):
    """Losing a published entry without a word is worse than a broken page.
    The row stays, and its slug comes back in a list for the service to say."""
    st.add_draft(a_draft(ID_A, slug="whole"), doc("whole"), {})
    st.publish(ID_A, fields(slug="whole"), at(1))
    st.add_draft(a_draft(ID_B, slug="broken"), doc("broken"), {})
    st.publish(ID_B, fields(slug="broken"), at(2))
    folder = root / "published" / "broken"
    if damage == "folder":
        (folder / "entry.html").unlink()
        folder.rmdir()
    elif damage == "file":
        (folder / "entry.html").unlink()
    else:
        (folder / "entry.html").write_bytes(b"<p>swapped</p>")

    report = st.repair()

    assert report["entries_missing"] == ["broken"] and report["ok"] is True
    assert report["published_removed"] == []
    assert [e["slug"] for e in st.entries()] == ["broken", "whole"]
    assert st.entry("broken") is not None and st.entry_html("broken") is None
    assert st.entry_html("whole") == doc("whole")
    # ...and a revision is how it is mended.
    st.add_draft(a_draft(ID_C, slug="broken", revises="broken"), doc("mended"), {})
    assert st.publish(ID_C, fields(slug="broken"), at(3))["published_at"] == stored(2)
    assert st.entry_html("broken") == doc("mended")
    assert st.repair()["entries_missing"] == []


def test_repair_leaves_alone_what_the_store_could_not_have_made(st, root):
    """It deletes only folders named like a draft id or an address. Anything
    else under ``staging/`` or ``published/`` was put there by someone, and a
    path is never built from a name read off the disk unchecked."""
    st.add_draft(a_draft(), doc(), {})
    (root / "staging" / "Not_An_Id").mkdir()
    (root / "staging" / "notes.txt").write_bytes(b"mine")
    (root / "published").mkdir()
    (root / "published" / "Not_A_Slug").mkdir()
    (root / "published" / "fonts").mkdir()             # reserved: never an entry's folder
    (root / "published" / "README").write_bytes(b"mine")

    report = st.repair()

    assert report["left_alone"] == ["published/Not_A_Slug", "published/README",
                                    "published/fonts", "staging/Not_An_Id",
                                    "staging/notes.txt"]
    for kept in report["left_alone"]:
        assert (root / kept).exists()
    assert report["staging_removed"] == [] and report["published_removed"] == []


def test_repair_clears_half_written_files_and_only_those(st, root):
    name, data = font(b"x")
    st.add_draft(a_draft(), doc(), {name: data})
    cut_short = [root / "fonts" / f".{name}.0123456789ab.tmp",
                 root / "staging" / ID_A / ".entry.html.0123456789ab.tmp",
                 root / "staging" / ID_A / ".entry.html.next.0123456789ab.tmp"]
    for path in cut_short:
        path.write_bytes(b"half")
    (root / "fonts" / "notes.tmp").write_bytes(b"someone else's")

    report = st.repair()

    assert report["temp_removed"] == 3
    assert all(not path.exists() for path in cut_short)
    assert (root / "fonts" / "notes.tmp").exists()
    assert st.draft_html(ID_A) == doc() and (root / "fonts" / name).read_bytes() == data


def test_every_file_is_written_under_a_name_repair_would_clear(st, root, monkeypatch):
    """A crash in the middle of a write leaves the temporary file. ``repair``
    deletes only names of one pattern, so every temporary name the store
    really uses has to BE of that pattern - for a typeface, a document and a
    replacement alike."""
    renamed, real_replace = [], os.replace

    def watch(src, dst, *a, **k):
        renamed.append((pathlib.Path(src).name, pathlib.Path(dst).name))
        return real_replace(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", watch)
    name, data = font(b"x")

    st.add_draft(a_draft(), doc("one"), {name: data})
    st.replace_draft(ID_A, a_draft(), doc("two"), {})

    temporary = [(src, dst) for src, dst in renamed if src != "entry.html.next"]
    assert sorted(dst for _, dst in temporary) == sorted([name, "entry.html", "entry.html.next"])
    for src, dst in temporary:
        assert store._TMP_RE.match(src), src
        assert src.startswith(f".{dst}.")
    assert ("entry.html.next", "entry.html") in renamed
    for final in (name, "entry.html", "entry.html.next", "notes.tmp", ".hidden.tmp",
                  ".entry.html.0123456789AB.tmp", ".entry.html.0123456789ab.tmp\n"):
        assert not store._TMP_RE.match(final), final


def test_a_write_that_fails_leaves_no_temporary_file(st, root, monkeypatch):
    def refuse(src, dst, *a, **k):
        raise PermissionError(13, "in use")
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", refuse)
        with pytest.raises(PermissionError):
            st.add_draft(a_draft(), doc(), {})
    assert leftovers(root) == [] and not (root / "staging" / ID_A).exists()
    assert st.drafts() == []


def test_replacing_a_draft_puts_back_a_document_that_went_missing(st, root):
    st.add_draft(a_draft(), doc("before"), {})
    (root / "staging" / ID_A / "entry.html").unlink()
    assert st.draft_html(ID_A) is None
    assert st.replace_draft(ID_A, a_draft(), doc("after"), {}) is True
    assert st.draft_html(ID_A) == doc("after") and leftovers(root) == []


def test_repair_reports_a_typeface_that_is_named_and_not_there(st, root):
    name, data = font(b"x")
    st.add_draft(a_draft(), doc(), {name: data})
    (root / "fonts" / name).unlink()
    assert st.repair()["fonts_missing"] == [name]


def test_repair_on_a_store_with_nothing_in_it(st, root):
    assert st.repair() == {
        "ok": True, "staging_removed": [], "drafts_removed": [], "published_removed": [],
        "entries_missing": [], "fonts_missing": [], "left_alone": [],
        "settled": 0, "temp_removed": 0}
    assert sorted(p.name for p in root.iterdir() if "blog.db" not in p.name) == []


def test_repair_on_a_healthy_store_changes_nothing(st, root):
    name, data = font(b"x")
    st.add_draft(a_draft(ID_A), doc("published"), {name: data})
    st.publish(ID_A, fields(), at(1))
    st.add_draft(a_draft(ID_B, slug="waiting"), doc("waiting"), {})
    before = tree(root)

    report = st.repair()

    assert report == {
        "ok": True, "staging_removed": [], "drafts_removed": [], "published_removed": [],
        "entries_missing": [], "fonts_missing": [], "left_alone": [],
        "settled": 0, "temp_removed": 0}
    assert tree(root) == before


def test_repair_never_raises(st, root, monkeypatch):
    """It runs at service start. Whatever is wrong with the folder, the service
    must still come up and say so on /health."""
    st.add_draft(a_draft(), doc(), {})

    def boom(*_args, **_kwargs):
        raise OSError("the disk is not well")
    counted = _degrade.counts().get("blog.store", 0)

    with monkeypatch.context() as patch:
        patch.setattr(st, "_mend", boom)
        report = st.repair()

    assert report["ok"] is False
    assert _degrade.counts().get("blog.store", 0) == counted + 1
    assert st.draft_html(ID_A) == doc()                 # no transaction left open
    assert st.repair()["ok"] is True


# ── a document is the one its row describes, or it is not there ─────────────

def test_a_document_changed_on_disk_is_not_served(st, root):
    """The row records a hash of the document that was cleaned. A file that no
    longer matches it is not that document, and what the operator previews has
    to be what was cleaned."""
    st.add_draft(a_draft(), doc("as cleaned"), {})
    (root / "staging" / ID_A / "entry.html").write_bytes(
        doc("<script>edited on disk</script>").encode("utf-8"))
    assert st.draft_html(ID_A) is None
    assert st.publish(ID_A, fields(), at(1)) is None
    assert st.entries() == [] and not (root / "published" / "first-entry").exists()


def test_non_ascii_text_round_trips_byte_for_byte(st, root):
    """Bytes in, the same bytes on disk, the same text back. No newline is
    translated (Windows would turn a line feed into two bytes in text mode) and
    nothing is normalised: the combining accent stays a separate character."""
    body = ("caf" + E_ACUTE + " " + CJK_MIDDLE + " " + GRINNING_FACE + " e" + COMBINING_ACUTE
            + " line one\nline two\r\nline three\rtab\there")
    html = doc(body)
    assert html.encode("utf-8") != html.encode("ascii", "replace")

    st.add_draft(a_draft(title="Caf" + E_ACUTE, tags=[CJK_MIDDLE, GRINNING_FACE]), html, {})

    assert (root / "staging" / ID_A / "entry.html").read_bytes() == html.encode("utf-8")
    assert st.draft_html(ID_A) == html
    assert st.draft(ID_A)["bytes"] == len(html.encode("utf-8")) > len(html)
    assert st.draft(ID_A)["title"] == "Caf" + E_ACUTE
    assert st.draft(ID_A)["tags"] == [CJK_MIDDLE, GRINNING_FACE]

    entry = st.publish(ID_A, fields(title="Caf" + E_ACUTE + " " + GRINNING_FACE,
                                    tags=(CJK_MIDDLE,)), at(1))
    assert entry["title"] == "Caf" + E_ACUTE + " " + GRINNING_FACE
    assert (root / "published" / "first-entry" / "entry.html").read_bytes() == html.encode("utf-8")
    assert st.entry_html("first-entry") == html


def test_a_two_megabyte_document(st, root):
    html = doc("x" * (2 * 1024 * 1024))
    assert len(html.encode("utf-8")) > 2 * 1024 * 1024
    st.add_draft(a_draft(), html, {})
    assert st.draft_html(ID_A) == html and st.draft(ID_A)["bytes"] == len(html)
    st.publish(ID_A, fields(), at(1))
    assert st.entry_html("first-entry") == html
    assert (root / "published" / "first-entry" / "entry.html").stat().st_size == len(html)


def test_a_document_that_cannot_be_encoded_is_refused_without_quoting_it(st, root):
    """A lone surrogate cannot be written as UTF-8. Refused before the disk is
    touched, and the error names neither the document nor any part of it."""
    html = doc("SECRET-MARK " + LONE_SURROGATE)
    before = tree(root)
    with pytest.raises(ValueError) as caught:
        st.add_draft(a_draft(), html, {})
    assert tree(root) == before
    told = repr(caught.value) + str(caught.value.__cause__) + str(caught.value.__context__)
    assert "SECRET-MARK" not in told and LONE_SURROGATE not in told


@pytest.mark.parametrize("field", ["title", "summary", "font_note"])
def test_a_field_that_cannot_be_encoded_is_refused_before_the_disk(st, root, field):
    before = tree(root)
    with pytest.raises(ValueError):
        st.add_draft(a_draft(**{field: "x" + LONE_SURROGATE}), doc(), {})
    assert tree(root) == before


def test_no_refusal_quotes_the_document(st, root):
    """Every ``ValueError`` this module raises is one of its own sentences."""
    st.add_draft(a_draft(ID_A), doc("SECRET-MARK"), {})
    name, _ = font(b"x")
    for call in (lambda: st.add_draft(a_draft(ID_A), doc("SECRET-MARK"), {}),
                 lambda: st.add_draft(a_draft(ID_B, slug="../x"), doc("SECRET-MARK"), {}),
                 lambda: st.add_draft(a_draft(ID_B), doc("SECRET-MARK"), {name: b"SECRET-MARK"}),
                 lambda: st.add_draft(a_draft(ID_B, title=None), doc("SECRET-MARK"), {}),
                 lambda: st.replace_draft(ID_A, a_draft(slug="A"), doc("SECRET-MARK"), {}),
                 lambda: st.add_draft(a_draft(ID_B), doc("SECRET-MARK").encode(), {})):
        with pytest.raises(ValueError) as caught:
            call()
        assert "SECRET-MARK" not in repr(caught.value)


# ── more than one thread ─────────────────────────────────────────────────────

def _race(first, second, rounds, root):
    """``rounds`` times: two drafts, two threads, ONE new address. Returns
    nothing; asserts after every round."""
    for n in range(rounds):
        id_one, id_two, slug = f"{n:015x}1", f"{n:015x}2", f"contested-{n}"
        first.add_draft(a_draft(id_one, slug=slug), doc(f"one {n}"), {})
        second.add_draft(a_draft(id_two, slug=slug), doc(f"two {n}"), {})
        gate, results, errors = threading.Barrier(2), {}, []

        def publish(which, draft_id):
            try:
                gate.wait(10)
                results[draft_id] = which.publish(draft_id, fields(slug=slug), at(n % 60))
            except BaseException as exc:             # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=publish, args=(first, id_one)),
                   threading.Thread(target=publish, args=(second, id_two))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)

        assert errors == []
        won = [i for i, entry in results.items() if entry is not None]
        lost = [i for i, entry in results.items() if entry is None]
        assert len(won) == 1 and len(lost) == 1, results
        text = doc(f"one {n}") if won[0] == id_one else doc(f"two {n}")
        other = doc(f"two {n}") if won[0] == id_one else doc(f"one {n}")
        assert first.entry_html(slug) == text
        assert (root / "published" / slug / "entry.html").read_bytes() == text.encode("utf-8")
        assert sorted(p.name for p in (root / "published" / slug).iterdir()) == ["entry.html"]
        # The winner's draft is gone, folder and all; the loser's is whole.
        assert first.draft(won[0]) is None and not (root / "staging" / won[0]).exists()
        assert first.draft_html(lost[0]) == other
        assert first.discard(lost[0]) is True
    assert len(first.entries()) == rounds
    assert sorted(p.name for p in (root / "published").iterdir()) == sorted(
        f"contested-{n}" for n in range(rounds))
    assert list((root / "staging").iterdir()) == []
    assert leftovers(root) == []
    report = first.repair()
    assert report["published_removed"] == [] and report["entries_missing"] == []


def test_two_threads_publishing_to_one_new_address_have_one_winner(st, root):
    _race(st, st, 25, root)


def test_two_stores_publishing_to_one_new_address_have_one_winner(root):
    one, two = store.Store(data_dir=root), store.Store(data_dir=root)
    try:
        _race(one, two, 25, root)
    finally:
        one.close()
        two.close()


def test_many_threads_filing_and_pruning_lose_no_typeface(st, root):
    """Drafts arriving on one stream while the other prunes. Every draft that
    was stored must still have every typeface it names."""
    errors = []

    def file_drafts(start):
        try:
            for n in range(start, start + 20):
                name, data = font(b"face %d" % n)
                st.add_draft(a_draft(f"{n:016x}", slug=f"entry-{n}"), doc(str(n)), {name: data})
        except BaseException as exc:                 # noqa: BLE001 - reported below
            errors.append(exc)

    def prune():
        try:
            for _ in range(40):
                st.prune_fonts()
        except BaseException as exc:                 # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=file_drafts, args=(0,)),
               threading.Thread(target=file_drafts, args=(100,)),
               threading.Thread(target=prune), threading.Thread(target=prune)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    assert errors == []
    assert len(st.drafts()) == 40
    on_disk = {p.name for p in (root / "fonts").iterdir()}
    assert st.fonts_in_use() == on_disk and len(on_disk) == 40
    assert st.repair()["fonts_missing"] == []


# ── an address at the ceiling, after the limit is lowered ───────────────────

def test_an_entry_keeps_its_address_when_the_limit_is_lowered(st, root, monkeypatch):
    """An entry published while ``[limits] slug_chars`` was 120 has a 120
    character address. Lowering the setting must not strand it: it can still be
    read, revised and unpublished, though nothing NEW may take an address that
    long."""
    ceiling = blog_inbox.SLUG_CHARS_CEILING
    long_slug = "a" * ceiling
    monkeypatch.setattr(blog_inbox, "load", lambda: {"limits": {"slug_chars": ceiling}})
    st.add_draft(a_draft(ID_A, slug=long_slug), doc("long"), {})
    assert st.publish(ID_A, fields(slug=long_slug), at(1))["slug"] == long_slug

    monkeypatch.setattr(blog_inbox, "load", lambda: {"limits": {"slug_chars": 40}})
    assert blog_inbox.clean_slug(long_slug) is None, "the limit really was lowered"

    assert st.entry(long_slug)["slug"] == long_slug
    assert st.entry_html(long_slug) == doc("long")
    assert st.repair()["published_removed"] == []      # not mistaken for a stray folder
    assert st.entry_html(long_slug) == doc("long")

    # Nothing new at that length: not a draft's own address, not a publish.
    before = tree(root)
    with pytest.raises(ValueError):
        st.add_draft(a_draft(ID_B, slug=long_slug), doc(), {})
    assert tree(root) == before
    st.add_draft(a_draft(ID_B, slug="short"), doc("b"), {})
    with pytest.raises(ValueError):
        st.publish(ID_B, fields(slug="b" * ceiling), at(2))
    with pytest.raises(ValueError):
        st.replace_draft(ID_B, a_draft(ID_B, slug="b" * ceiling), doc("b"), {})
    assert st.draft(ID_B)["slug"] == "short" and len(st.entries()) == 1

    # A revision of it is still filed, replaced and published under it.
    st.add_draft(a_draft(ID_C, slug=long_slug, revises=long_slug), doc("revised"), {})
    assert st.replace_draft(ID_C, a_draft(slug=long_slug), doc("revised twice"), {}) is True
    entry = st.publish(ID_C, fields(slug=long_slug), at(3))
    assert entry["published_at"] == stored(1) and entry["updated_at"] == stored(3)
    assert st.entry_html(long_slug) == doc("revised twice")

    assert st.unpublish(long_slug) is True
    assert st.entries() == [] and not (root / "published" / long_slug).exists()


def test_one_past_the_ceiling_is_never_an_address(st, root, monkeypatch):
    monkeypatch.setattr(blog_inbox, "load",
                        lambda: {"limits": {"slug_chars": blog_inbox.SLUG_CHARS_CEILING}})
    too_long = "a" * (blog_inbox.SLUG_CHARS_CEILING + 1)
    before = tree(root)
    for call in (lambda: st.entry(too_long), lambda: st.unpublish(too_long),
                 lambda: st.add_draft(a_draft(slug=too_long), doc(), {})):
        with pytest.raises(ValueError):
            call()
    assert tree(root) == before


# ── the submission log ───────────────────────────────────────────────────────

def test_submissions_are_counted_from_a_moment_on(st):
    for minute in (0, 10, 20, 30):
        st.note_submission(at(minute))
    assert st.count_submissions_since(at(0)) == 4
    assert st.count_submissions_since(at(10)) == 3          # the moment itself counts
    assert st.count_submissions_since(at(11)) == 2
    assert st.count_submissions_since(at(31)) == 0


def test_submissions_are_compared_as_moments_not_as_text(st):
    """One instant written three ways must count as one instant. As text,
    ``+00:00`` and ``-05:00`` and a fraction sort in the wrong places."""
    st.note_submission("2026-10-06T07:00:00-05:00")         # 12:00 UTC
    st.note_submission("2026-10-06T12:00:00.500000+00:00")
    st.note_submission("2026-10-06T13:30:00+01:00")         # 12:30 UTC
    assert st.count_submissions_since("2026-10-06T12:00:00+00:00") == 3
    assert st.count_submissions_since("2026-10-06T12:00:00.250+00:00") == 2
    assert st.count_submissions_since("2026-10-06T08:00:00-04:00") == 3
    assert st.count_submissions_since("2026-10-06T12:29:59Z") == 1


def test_old_submissions_can_be_pruned(st, root):
    for minute in (0, 10, 20):
        st.note_submission(at(minute))
    assert st.prune_submissions(at(10)) == 1                # strictly before
    assert st.count_submissions_since(at(0)) == 2
    assert rows(root, "SELECT COUNT(*) FROM submissions") == [(2,)]


def test_noting_a_submission_clears_what_is_two_days_old(st, root):
    """The table backs an HOURLY limit. Without this it grows by a row for
    every draft the connector ever files."""
    st.note_submission("2026-10-01T00:00:00+00:00")
    st.note_submission("2026-10-04T12:00:01+00:00")         # 47 h 59 m 59 s before
    st.note_submission("2026-10-04T12:00:00+00:00")         # 48 h before, to the second
    st.note_submission("2026-10-04T11:59:59+00:00")         # a second more
    # The first is already gone: it was more than two days before the second.
    assert rows(root, "SELECT COUNT(*) FROM submissions") == [(3,)]

    st.note_submission("2026-10-06T12:00:00+00:00")

    kept = sorted(r[0] for r in rows(root, "SELECT at FROM submissions"))
    assert kept == ["2026-10-04T12:00:00.000000+00:00", "2026-10-04T12:00:01.000000+00:00",
                    "2026-10-06T12:00:00.000000+00:00"]
    assert store.SUBMISSIONS_KEEP_HOURS == 48


@pytest.mark.parametrize("bad", ["2026-10-06T12:00:00", "", None, 0, "soon"])
def test_the_submission_log_takes_only_a_time_with_its_timezone(st, root, bad):
    for call in (lambda: st.note_submission(bad), lambda: st.count_submissions_since(bad),
                 lambda: st.prune_submissions(bad)):
        with pytest.raises(ValueError):
            call()
    assert rows(root, "SELECT COUNT(*) FROM submissions") == [(0,)]


# ── the key-value table ──────────────────────────────────────────────────────

def test_a_value_round_trips_as_json(st):
    assert st.get_kv("connection") is None
    assert st.get_kv("connection", {"epoch": 0}) == {"epoch": 0}
    for value in ({"epoch": 3, "since": at(1), "clients": ["a", "b"]}, [1, 2.5, None],
                  "text " + E_ACUTE + GRINNING_FACE, 7, False, None):
        st.set_kv("connection", value)
        assert st.get_kv("connection", "default") == value
    st.set_kv("other_key_2", 1)
    assert st.get_kv("connection") is None and st.get_kv("other_key_2") == 1


def test_a_stored_none_is_not_the_default(st):
    st.set_kv("flag", None)
    assert st.get_kv("flag", "default") is None


@pytest.mark.parametrize("bad", ["", "A", "Epoch", "1st", "_x", "a-b", "a b", "a.b", "a" * 42,
                                 "key\n", None, 7, "drafts; DROP TABLE kv"])
def test_a_key_is_a_short_plain_name(st, root, bad):
    with pytest.raises(ValueError):
        st.set_kv(bad, 1)
    with pytest.raises(ValueError):
        st.get_kv(bad)
    assert rows(root, "SELECT COUNT(*) FROM kv") == [(0,)]


def test_the_longest_key_is_forty_one_characters(st):
    st.set_kv("a" * 41, 1)
    assert st.get_kv("a" * 41) == 1
    assert store.KV_KEY_RE.match("a" * 41) and not store.KV_KEY_RE.match("a" * 42)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {1, 2}, b"bytes", object()])
def test_a_value_that_is_not_json_is_refused(st, root, bad):
    st.set_kv("kept", {"epoch": 1})
    with pytest.raises(ValueError):
        st.set_kv("kept", bad)
    assert st.get_kv("kept") == {"epoch": 1}


def test_a_value_someone_damaged_reads_as_the_default(st, root):
    st.set_kv("connection", {"epoch": 1})
    run_sql(root, "UPDATE kv SET value=? WHERE key=?", "{not json", "connection")
    assert st.get_kv("connection", "default") == "default"


# ── the module itself ────────────────────────────────────────────────────────

def test_the_store_reads_no_clock():
    """Every time is the caller's. A store that stamped its own could not be
    tested at a chosen moment, and two of its rows could disagree about now."""
    source = pathlib.Path(store.__file__).read_text(encoding="utf-8")
    for clock in ("datetime.now", "utcnow", "time.time", "date.today", "'now'", '"now"',
                  "CURRENT_TIMESTAMP"):
        assert clock not in source, clock


def test_every_write_begins_immediate():
    """One way to open a transaction, so two stores on one file serialise."""
    source = pathlib.Path(store.__file__).read_text(encoding="utf-8")
    assert source.count('"BEGIN IMMEDIATE"') == 1
    assert "isolation_level=None" in source


def test_a_closed_store_can_be_made_again(root):
    with store.Store(data_dir=root) as one:
        one.add_draft(a_draft(), doc("kept across a restart"), {})
    with store.Store(data_dir=root) as two:
        assert two.draft_html(ID_A) == doc("kept across a restart")
