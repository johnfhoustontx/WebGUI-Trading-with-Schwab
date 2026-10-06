"""The blog scheduler: repair and rebuild at start, then keep the views true.

Schedulers do not run under pytest, so the two passes are called as the plain
functions they are, on the real store and the real site writer (both on this
test's own folders). ``loop`` itself is driven with a fake clock and a fake
sleep that cancels it after a set number of wakes.
"""
import ast
import asyncio
import inspect
import logging
import textwrap

import pytest

from services import _degrade
from services.blog_svc import handlers, scheduler, sitewriter
from services.blog_svc import store as store_mod
from shared import blog_inbox
from shared.bus import Bus

MARKER = "MARKER-in-an-exception-9172"
DOC = "<!doctype html><html lang=\"en\"><head><title>T</title></head><body><p>Hello</p></body></html>"
AT = "2026-10-06T12:00:00+00:00"


def an_entry(slug="first-entry", title="A first entry") -> None:
    """One published entry, put there through the store alone: nothing has
    written the site or the views yet, which is the state at service start."""
    draft_id = blog_inbox.new_id()
    with store_mod.Store() as st:
        st.add_draft({"id": draft_id, "source": "upload", "revises": None, "slug": slug,
                      "title": title, "summary": "What it says.", "tags": ["one"],
                      "removed": {}, "font_links": [], "font_note": "",
                      "received_at": AT}, DOC, {})
        assert st.publish(draft_id, {"title": title, "summary": "What it says.",
                                     "tags": ["one"], "slug": slug}, AT) is not None


def view(bus, name):
    env = bus.cache_get(f"cache:{name}")
    return None if env is None else env.payload


def site_files() -> set:
    site = sitewriter.SITE_ROOT
    return {path.relative_to(site).as_posix() for path in site.rglob("*") if path.is_file()}


def degrades(area) -> int:
    return _degrade.counts().get(area, 0)


# ── the two passes ───────────────────────────────────────────────────────────

def test_the_first_pass_repairs_rebuilds_and_publishes(monkeypatch):
    an_entry()
    order = []
    real_repair, real_rebuild, real_views = (store_mod.Store.repair, sitewriter.rebuild,
                                             handlers.publish_views)
    monkeypatch.setattr(store_mod.Store, "repair",
                        lambda self: order.append("repair") or real_repair(self))
    monkeypatch.setattr(sitewriter, "rebuild",
                        lambda *args: order.append("rebuild") or real_rebuild(*args))
    monkeypatch.setattr(handlers, "publish_views",
                        lambda *args: order.append("views") or real_views(*args))
    bus = Bus()

    scheduler.first_pass(bus)

    assert order == ["repair", "rebuild", "views"]
    assert site_files() == {"blog.html", "blog.json", "blog/sitemap.txt",
                            "blog/first-entry/index.html", "blog/first-entry/entry.html"}
    assert [row["slug"] for row in view(bus, blog_inbox.VIEW_POSTS)["entries"]] == ["first-entry"]
    assert view(bus, blog_inbox.VIEW_DRAFTS) == {"drafts": []}


def test_the_first_pass_rewrites_entry_pages_when_the_menu_changed():
    """Why the rebuild runs at every start: a promote that changes the site
    menu must change the entry pages, which carry a copy of it."""
    an_entry()
    scheduler.first_pass(Bus())
    page = sitewriter.SITE_ROOT / "blog" / "first-entry" / "index.html"
    assert b"A-new-menu-item" not in page.read_bytes()
    tracked = sitewriter.SITE_ROOT / "blog.html"
    tracked.write_bytes(tracked.read_bytes().replace(b">Glossary</a>", b">A-new-menu-item</a>"))

    scheduler.first_pass(Bus())

    assert b"A-new-menu-item" in page.read_bytes()


@pytest.mark.parametrize("report,said", [
    ({"ok": True, "entries_missing": ["lost-entry"]}, "lost-entry"),
    ({"ok": False, "entries_missing": []}, "DID NOT FINISH"),
    ({"ok": False, "failed": ["rename", "fonts"]}, "rename, fonts"),
])
def test_a_repair_that_found_a_lost_entry_or_did_not_finish_is_a_warning_and_counted(
        monkeypatch, caplog, report, said):
    full = {"ok": True, "staging_removed": [], "drafts_removed": [], "published_removed": [],
            "entries_missing": [], "fonts_missing": [], "left_alone": [], "failed": [],
            "settled": 0, "temp_removed": 0}
    full.update(report)
    monkeypatch.setattr(store_mod.Store, "repair", lambda self: full)
    before = degrades("blog.repair")

    with caplog.at_level(logging.INFO, logger="blog_svc.scheduler"):
        scheduler.first_pass(Bus())

    warnings = [rec for rec in caplog.records
                if rec.levelno == logging.WARNING and rec.name == "blog_svc.scheduler"]
    assert len(warnings) == 1 and said in warnings[0].getMessage()
    assert degrades("blog.repair") == before + 1


def test_the_real_stores_repair_report_is_one_the_scheduler_reads():
    """The keys the note is written from, on the store as it is - not on the
    dict the test above typed out."""
    with store_mod.Store() as st:
        report = st.repair()
    assert report["ok"] is True and report["entries_missing"] == []
    assert isinstance(report.get("failed", []), list)


def test_a_clean_repair_is_not_a_warning(caplog):
    before = degrades("blog.repair")
    with caplog.at_level(logging.INFO, logger="blog_svc.scheduler"):
        scheduler.first_pass(Bus())
    assert [rec for rec in caplog.records if rec.levelno >= logging.WARNING] == []
    assert any("repair" in rec.getMessage() for rec in caplog.records)
    assert degrades("blog.repair") == before


def test_a_later_pass_publishes_the_views_and_leaves_a_good_site_alone(monkeypatch):
    an_entry()
    scheduler.first_pass(Bus())
    assert sitewriter.pending() is False
    rebuilt = []
    monkeypatch.setattr(sitewriter, "rebuild", lambda *args: rebuilt.append(args))
    from shared.bus.client import reset_fake_bus
    reset_fake_bus()                               # a flushed Redis
    bus = Bus()
    assert view(bus, blog_inbox.VIEW_POSTS) is None

    scheduler.later_pass(bus)

    assert rebuilt == []
    assert [row["slug"] for row in view(bus, blog_inbox.VIEW_POSTS)["entries"]] == ["first-entry"]
    assert view(bus, blog_inbox.VIEW_DRAFTS) == {"drafts": []}


def test_a_later_pass_rebuilds_while_the_last_rebuild_fell_short(monkeypatch):
    an_entry()
    with monkeypatch.context() as patch:
        def refuse(path, payload):
            raise OSError(MARKER)
        patch.setattr(sitewriter, "_write", refuse)
        scheduler.first_pass(Bus())
    assert sitewriter.pending() is True
    assert site_files() == {"blog.html"}

    scheduler.later_pass(Bus())

    assert sitewriter.pending() is False
    assert "blog/first-entry/index.html" in site_files()
    # And it stops once the site is right.
    rebuilt = []
    monkeypatch.setattr(sitewriter, "rebuild", lambda *args: rebuilt.append(args))
    scheduler.later_pass(Bus())
    assert rebuilt == []


def test_the_interval_is_the_configured_one(monkeypatch):
    assert scheduler.interval_s() == blog_inbox.DEFAULTS["site"]["republish_min"] * 60
    monkeypatch.setattr(blog_inbox, "site", lambda: {"enabled": True, "republish_min": 5})
    assert scheduler.interval_s() == 300


# ── the loop ─────────────────────────────────────────────────────────────────

def test_the_heartbeat_is_inside_the_while_loop():
    """Mirrors services/tests/test_scaffold.py's per-service guard: a beat
    outside the loop reports one tick at startup and then a growing age."""
    fn = ast.parse(textwrap.dedent(inspect.getsource(scheduler.loop))).body[0]
    beats = [call for loop in ast.walk(fn) if isinstance(loop, ast.While)
             for call in ast.walk(loop)
             if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
             and call.func.attr == "tick"
             and isinstance(call.func.value, ast.Name) and call.func.value.id == "_heartbeat"]
    assert beats


class Harness:
    """``scheduler.loop`` on a fake clock: every sleep moves it on by what was
    asked for, and the loop is cancelled after ``wakes`` sleeps."""

    def __init__(self, monkeypatch, wakes, first=None, later=None):
        self.clock = 5000.0
        self.ran = []                # ("first" | "later", clock) for each pass STARTED
        self.slept = []
        self.beats = 0
        self._first, self._later = first, later
        left = {"n": wakes}

        async def inline(fn, *args):
            return fn(*args)

        async def fake_sleep(seconds):
            self.slept.append(seconds)
            self.clock += seconds
            left["n"] -= 1
            if left["n"] <= 0:
                raise asyncio.CancelledError

        def beat():
            self.beats += 1

        monkeypatch.setattr(scheduler, "_to_thread", inline)
        monkeypatch.setattr(scheduler, "_sleep", fake_sleep)
        monkeypatch.setattr(scheduler, "_monotonic", lambda: self.clock)
        monkeypatch.setattr(scheduler._heartbeat, "tick", beat)
        monkeypatch.setattr(scheduler, "first_pass", lambda bus: self._pass("first", first, bus))
        monkeypatch.setattr(scheduler, "later_pass", lambda bus: self._pass("later", later, bus))

    def _pass(self, name, body, bus):
        self.ran.append((name, self.clock))
        if body is not None:
            body(bus)

    def run(self):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(scheduler.loop(object()))
        return self


def test_the_loop_runs_the_first_pass_once_then_later_passes_on_the_interval(monkeypatch):
    monkeypatch.setattr(blog_inbox, "site", lambda: {"enabled": True, "republish_min": 2})
    wakes = 9                                       # 9 x 30 s = four and a half minutes
    h = Harness(monkeypatch, wakes).run()

    assert h.slept == [scheduler.TICK_S] * wakes
    assert h.beats == wakes                         # one beat a wake, pass or no pass
    assert h.ran == [("first", 5000.0), ("later", 5120.0), ("later", 5240.0)]


def test_a_failed_first_pass_stays_the_first_pass_and_is_tried_at_the_next_wake(
        monkeypatch, caplog):
    attempts = []

    def first(bus):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError(MARKER)

    before = degrades("blog.scheduler")
    with caplog.at_level("DEBUG"):
        h = Harness(monkeypatch, wakes=5, first=first).run()

    assert [name for name, _ in h.ran] == ["first", "first", "first"]
    assert [clock for _, clock in h.ran] == [5000.0, 5030.0, 5060.0]
    assert degrades("blog.scheduler") == before + 2
    assert MARKER not in caplog.text
    assert h.beats == 5                              # the loop went on beating


def test_a_failed_later_pass_does_not_end_the_loop(monkeypatch):
    monkeypatch.setattr(blog_inbox, "site", lambda: {"enabled": True, "republish_min": 1})

    def later(bus):
        raise OSError(MARKER)

    before = degrades("blog.scheduler")
    h = Harness(monkeypatch, wakes=5, later=later).run()

    # First at 5000; nothing due at 5030; later due at 5060, fails, and is
    # tried again at each wake after it.
    assert h.ran == [("first", 5000.0), ("later", 5060.0), ("later", 5090.0),
                     ("later", 5120.0)]
    assert degrades("blog.scheduler") == before + 3
    assert h.beats == 5


def test_cancelling_a_pass_cancels_the_loop(monkeypatch):
    def first(bus):
        raise asyncio.CancelledError

    h = Harness(monkeypatch, wakes=50, first=first).run()
    assert h.ran == [("first", 5000.0)] and h.slept == []
