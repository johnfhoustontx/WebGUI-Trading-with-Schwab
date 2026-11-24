"""The PUBLIC X Feed screen (``live.neuralstrike.co/x-feed``).

The public origin's Redis user reads ``~cache:*``, so it CAN read the private
``cache:xfeed:latest`` / ``cache:xfeed:accounts`` - a wildcard grant cannot
exclude one key. What keeps the accounts the owner did not publish, and the
service's error text, off this origin is code: on the public origin the page
reads ``xfeed:latest_public`` and ``xfeed:accounts_public`` and nothing else.
These tests pin that by rendering the page against a bus that serves a
different post under each key, and pin that the page sends nothing.
"""
import ast
import datetime as dt
import pathlib

import pytest

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"
NOW_MS = int(dt.datetime(2026, 11, 24, 10, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _post(sid, handle, text):
    return {"kind": "post", "account_handle": handle, "status_id": sid, "first_seen_at": NOW_MS - 600_000,
            "created_at": NOW_MS - 300_000, "sort_at": NOW_MS - 300_000, "origin": "poll", "type": "post",
            "text": text, "author_name": handle.title(), "author_handle": handle, "author_avatar": None,
            "url": f"https://x.com/{handle}/status/{sid}", "cashtags": ["SPY"], "media": [], "quote": None,
            "counts": {}}


def _accounts(*handles, error=None):
    return {"accounts": [{"handle": h, "display_name": h.title(), "avatar_url": None, "group_name": "News",
                          "last_checked_at": NOW_MS - 30_000, "last_error": error} for h in handles],
            "counts": {h: 1 for h in handles}, "interval_s": 60, "page": {"new_highlight_s": 60}}


def _views():
    from pages import xfeed_view as xv
    return {
        xv.VIEW_LATEST: {"rows": [_post("9", "owneronly", "PRIVATE post only"),
                                  _post("1", "reuters", "Public post")], "tickers": [], "total": 2},
        xv.VIEW_ACCOUNTS: _accounts("owneronly", "reuters", error="HTTP 503 SECRET ERROR"),
        xv.VIEW_LATEST_PUBLIC: {"rows": [_post("1", "reuters", "Public post")], "tickers": [], "total": 1},
        xv.VIEW_ACCOUNTS_PUBLIC: _accounts("reuters"),
    }


@pytest.fixture
def public(monkeypatch):
    """This process as the public origin, serving the two news pages."""
    import shell
    shell.publish({"/news": "/news", "/news/x": "/x-feed"})
    yield
    shell.unpublish()


def _render(monkeypatch, views):
    import bus_client
    import shell
    from nicegui import ui

    import pages.view_watch as vw
    from pages import xfeed
    asked, sent, watched = [], [], []

    def read(v):
        asked.append(v)
        return views.get(v)

    def meta(v):
        asked.append(v)
        return (1, dt.datetime.fromtimestamp(NOW_MS / 1000, dt.timezone.utc).isoformat())

    monkeypatch.setattr(bus_client, "read", read)
    monkeypatch.setattr(bus_client, "read_meta", meta)
    monkeypatch.setattr(bus_client, "request", lambda *a, **k: sent.append(a) or "1-0")
    monkeypatch.setattr(vw, "watch_view", lambda view, fn, **k: watched.append(view))
    # Even were the process to allow enqueueing, the public page must not send.
    monkeypatch.setattr(shell, "may_enqueue", lambda: True)
    monkeypatch.setattr(xfeed, "now_ms", lambda: NOW_MS)
    before = set(ui.context.client.elements)
    page = xfeed.render()
    new = [e for k, e in ui.context.client.elements.items() if k not in before]
    texts = [getattr(e, "text", "") for e in new if isinstance(e, (ui.label, ui.link))]
    return page, new, texts, asked, sent, watched


# ── the screen table ─────────────────────────────────────────────────────────

def test_x_feed_is_a_published_tools_screen_with_no_pin():
    """The page decides from the PROCESS's origin, so the entry carries no
    ``public=True`` to forget."""
    import live_screens
    s = {x.slug: x for x in live_screens.SCREENS}["x-feed"]
    assert (s.route, s.title, s.module, s.private_route) == ("/x-feed", "X Feed", "xfeed", "/news/x")
    assert s.tile is False and s.parent == ""
    assert s.kwargs == {} and s.settings == {}
    assert live_screens.PUBLIC_ROUTES["/news/x"] == "/x-feed"


def test_the_strip_labels_are_the_private_groups():
    import main
    from pages import news_strip
    assert list(news_strip.PAGES) == [(r, label) for r, label, _i in main.MARKET_NEWS_CHILDREN]


# ── rendered on the public origin ───────────────────────────────────────────

def test_the_public_page_reads_only_the_public_pair(monkeypatch, public):
    from pages import xfeed_live, xfeed_view as xv
    page, _new, texts, asked, sent, watched = _render(monkeypatch, _views())
    assert isinstance(page, xfeed_live.XFeedPage) and page.public
    assert "Public post" in texts
    assert "PRIVATE post only" not in texts
    assert not any("owneronly" in t for t in texts)
    assert set(asked) == {xv.VIEW_LATEST_PUBLIC, xv.VIEW_ACCOUNTS_PUBLIC}, asked
    assert set(watched) == {xv.VIEW_LATEST_PUBLIC, xv.VIEW_ACCOUNTS_PUBLIC}
    assert sent == []


def test_the_public_page_draws_no_control_that_sends_or_leads_to_the_owner(monkeypatch, public):
    from nicegui import ui

    from pages import xfeed
    page, new, texts, *_ = _render(monkeypatch, _views())
    assert page._may_enqueue is False
    assert page.search is None and page.more is None and page.handle_in is None
    assert page.settings_link is None
    assert xfeed.LOAD_OLDER not in texts and "Add" not in texts
    hrefs = [e.props.get("href") for e in new if isinstance(e, ui.link)]
    assert not [h for h in hrefs if h and h.startswith("/") and not h.startswith("//")], hrefs


def test_an_opened_posts_cashtag_is_plain_text_with_no_menu(monkeypatch, public):
    """The public page has nothing to offer on a $TICKER (no filter, no Symbol
    page), so it must not draw a clickable chip that opens an empty menu."""
    from nicegui import ui

    from pages import xfeed_parts as xp
    from pages import xfeed_view as xv
    views = _views()
    views[xv.VIEW_LATEST_PUBLIC] = {"rows": [_post("1", "reuters", "Buying $SPY here")],
                                    "tickers": [], "total": 1}
    page, *_ = _render(monkeypatch, views)
    before = set(ui.context.client.elements)
    key = next(iter(page.lines))
    page._toggle(key)
    new = [e for k, e in ui.context.client.elements.items() if k not in before]
    tags = [e for e in new if isinstance(e, ui.label) and e.text == "$SPY"]
    assert tags and all("cursor-pointer" not in e._classes for e in tags)
    assert not [e for e in new if isinstance(e, ui.menu)]
    assert xp._CASHTAG_PLAIN.split()[0] in tags[0]._classes


def test_the_public_page_carries_the_news_strip(monkeypatch, public):
    from nicegui import ui
    _page, new, *_ = _render(monkeypatch, _views())
    tabs = [e for e in new if isinstance(e, ui.tab)]
    assert [t._props["name"] for t in tabs] == ["/news", "/news/x"]
    strips = [e for e in new if isinstance(e, ui.tabs)]
    assert strips and strips[0].value == "/news/x"


def test_no_published_accounts_says_so(monkeypatch, public):
    from pages import xfeed, xfeed_view as xv
    views = _views()
    views[xv.VIEW_ACCOUNTS_PUBLIC] = _accounts()
    views[xv.VIEW_LATEST_PUBLIC] = {"rows": [], "tickers": [], "total": 0}
    texts = _render(monkeypatch, views)[2]
    assert xfeed.PUBLIC_NONE in texts
    assert xfeed.NO_ACCOUNTS not in texts and xfeed.NO_ONE not in texts


# ── the private page is unchanged ───────────────────────────────────────────

def test_the_private_page_reads_the_private_pair_and_draws_no_strip(monkeypatch):
    from nicegui import ui

    from pages import xfeed_view as xv
    page, new, texts, asked, _sent, _w = _render(monkeypatch, _views())
    assert page.public is False
    assert set(asked) == {xv.VIEW_LATEST, xv.VIEW_ACCOUNTS}
    assert "PRIVATE post only" in texts
    assert not [e for e in new if isinstance(e, ui.tabs)]


# ── source guards ────────────────────────────────────────────────────────────

def test_the_live_module_sends_nothing_and_imports_no_writer():
    tree = ast.parse((PAGES / "xfeed_live.py").read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods = {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods = {n.module or ""} | {a.name for a in n.names}
        else:
            continue
        assert not mods & {"main", "app_settings", "live_main", "bus_client"}, mods
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "XFeedPage"]
    assert len(calls) == 1
    (c,) = calls
    assert isinstance(c.args[0], ast.Constant) and c.args[0].value is False
    kw = {k.arg: k.value for k in c.keywords}
    assert kw["public"].value is True and kw["symbol_linked"].value is False


def test_the_page_names_each_view_once_through_the_public_switch():
    """Every bus read in ``xfeed.py`` goes through ``self.v_latest`` /
    ``self.v_accounts``; the view constants are named only where those two are
    chosen, so a new read cannot bypass the public switch."""
    src = (PAGES / "xfeed.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    named = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
             and isinstance(n.value, ast.Name) and n.value.id == "xv" and n.attr.startswith("VIEW_")]
    assert sorted(named) == sorted(["VIEW_LATEST", "VIEW_LATEST_PUBLIC",
                                    "VIEW_ACCOUNTS", "VIEW_ACCOUNTS_PUBLIC"]), named
