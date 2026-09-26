"""The PUBLIC Market News screen (``live.neuralstrike.co/news``).

The public origin's Redis user reads ``~cache:*``, so it CAN read the private
``cache:news:feed`` - a wildcard grant cannot exclude one key. What keeps a
private feed's items off this origin is code: this page reads
``news_view.VIEW_PUBLIC`` and nothing else. These tests pin that, at the source
and by rendering the page against a bus that serves a different item under each
key.
"""
import ast
import datetime as dt
import pathlib

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"
SRC = PAGES / "news_live.py"


def _src():
    return SRC.read_text(encoding="utf-8")


def _tree():
    return ast.parse(_src())


# ── the plan's tests ─────────────────────────────────────────────────────────

def test_news_is_a_published_tools_screen():
    import live_screens
    s = {x.slug: x for x in live_screens.SCREENS}["news"]
    assert (s.route, s.module, s.private_route, s.tile, s.kwargs) == \
        ("/news", "news", "/news", False, {"public": True})


def test_the_public_page_reads_only_the_public_view_and_never_writes():
    src = _src()
    assert "news_view.VIEW_PUBLIC" in src or "VIEW_PUBLIC" in src
    assert '"news:feed"' not in src
    assert ".request(" not in src and "app_settings" not in src


# ── beyond the plan ──────────────────────────────────────────────────────────

def test_the_private_feed_is_never_named():
    """``news:feed`` appears only as the prefix of ``news:feed_public`` - and
    this module spells neither literally: it names the view through
    ``nv.VIEW_PUBLIC``, so a rename in news_view cannot leave it reading a key
    the service no longer writes."""
    src = _src()
    assert "news:feed" not in src.replace("news:feed_public", "")
    names = {n.attr for n in ast.walk(_tree()) if isinstance(n, ast.Attribute)}
    assert "VIEW_PUBLIC" in names
    assert not names & {"VIEW", "VIEW_STATUS", "NEWS_VIEW"}, \
        "the public page names a view other than VIEW_PUBLIC"


def test_every_bus_read_names_the_public_view():
    """Each ``bus_client.<read>(...)`` / ``watch_view(...)`` / ``kit.header(view=)``
    passes ``nv.VIEW_PUBLIC`` - never a string, never a variable that could be
    something else."""
    def _is_public(node):
        return isinstance(node, ast.Attribute) and node.attr == "VIEW_PUBLIC"

    reads = 0
    for n in ast.walk(_tree()):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                and f.value.id == "bus_client":
            assert n.args and _is_public(n.args[0]), \
                f"bus_client.{f.attr} at line {n.lineno} reads another view"
            reads += 1
        elif name == "io_bound" and n.args and isinstance(n.args[0], ast.Attribute) \
                and isinstance(n.args[0].value, ast.Name) \
                and n.args[0].value.id == "bus_client":
            assert len(n.args) > 1 and _is_public(n.args[1]), \
                f"io_bound(bus_client.{n.args[0].attr}) at line {n.lineno} reads another view"
            reads += 1
        elif name == "watch_view":
            assert n.args and _is_public(n.args[0]), \
                f"watch_view at line {n.lineno} watches another view"
            reads += 1
        elif name == "header":
            views = [k.value for k in n.keywords if k.arg == "view"]
            assert views and all(_is_public(v) for v in views)
    assert reads >= 3, "the walk found no bus read - it would pass vacuously"


def test_no_import_of_main_app_settings_or_a_writer():
    for n in ast.walk(_tree()):
        if isinstance(n, ast.Import):
            mods = {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods = {n.module or ""} | {a.name for a in n.names}
        else:
            continue
        assert not mods & {"main", "app_settings", "live_main"}, mods
    calls = {n.func.attr for n in ast.walk(_tree())
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not {c for c in calls if c.startswith("request")}, calls
    assert "may_enqueue" not in _src()   # nothing here to gate: it sends nothing


def test_rows_are_drawn_by_the_private_pages_own_painter():
    """The two origins cannot drift: this page draws rows with
    ``news.draw_rows`` and has no row builder of its own."""
    calls = [n for n in ast.walk(_tree()) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "draw_rows"]
    assert calls
    for c in calls:
        assert isinstance(c.func.value, ast.Name) and c.func.value.id == "news"
        linked = [k.value for k in c.keywords if k.arg == "linked"]
        assert linked and isinstance(linked[0], ast.Constant) \
            and linked[0].value is False, "a public row must not link a dossier"
    defs = {n.name for n in ast.walk(_tree()) if isinstance(n, ast.FunctionDef)}
    assert "draw_rows" not in defs


def test_feed_text_never_reaches_ui_html():
    html_calls = [n for n in ast.walk(_tree()) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute) and n.func.attr == "html"]
    assert html_calls == []


def test_the_private_page_hands_off_to_this_module():
    import pages.news as news
    import pages.news_live as news_live
    called = []
    orig = news_live.render
    try:
        news_live.render = lambda: called.append(True)
        news.render(public=True)
    finally:
        news_live.render = orig
    assert called == [True]


# ── the query-string seed ────────────────────────────────────────────────────

def test_the_symbol_seed_goes_through_the_allow_list():
    from pages import news_live
    assert news_live.seed_symbol("nvda") == "NVDA"
    assert news_live.seed_symbol(" SPY ") == "SPY"
    assert news_live.seed_symbol("$SPX") == "$SPX"
    for bad in (None, "", "<script>", "A" * 40, "SPY;DROP", 7, ["SPY"]):
        assert news_live.seed_symbol(bad) == "", bad


# ── rendered ─────────────────────────────────────────────────────────────────

NOW = dt.datetime.now(dt.timezone.utc)


def _item(i, *, title, tickers=(), source="MarketWatch", hours_ago=0.5):
    ts = (NOW - dt.timedelta(hours=hours_ago)).isoformat()
    return {"id": f"i{i}", "source": source, "sources": [source],
            "title": title, "teaser": "", "url": f"https://example.com/{i}",
            "published_at": ts, "first_seen": ts, "tickers": list(tickers),
            "kind": "rss", "topics": [], "detail": {}}


PUBLIC = {"items": [_item(1, title="Public NVDA story", tickers=["NVDA"]),
                    _item(2, title="Public AAPL story", tickers=["AAPL"],
                          source="Yahoo")]}
PRIVATE = {"items": [_item(9, title="PRIVATE feed only", tickers=["NVDA"],
                           source="Owner feed")] + PUBLIC["items"]}


def _new(before):
    from nicegui import ui
    return [e for k, e in ui.context.client.elements.items() if k not in before]


def _render(monkeypatch, *, query=None, public=PUBLIC):
    import bus_client
    from nicegui import ui

    from pages import news_live, news_view as nv
    feeds = {nv.VIEW: PRIVATE, nv.VIEW_PUBLIC: public}
    asked = []

    def _read(v):
        asked.append(v)
        return feeds.get(v)

    monkeypatch.setattr(bus_client, "read", _read)
    monkeypatch.setattr(bus_client, "read_full",
                        lambda v: (asked.append(v) or feeds.get(v), 1))
    monkeypatch.setattr(bus_client, "read_version",
                        lambda v: asked.append(v) or 1)

    def _no_write(*a, **k):
        raise AssertionError("the public page tried to write")
    for w in ("request", "request_public_scan", "request_public_tool",
              "request_public_math", "request_public_gamma"):
        if hasattr(bus_client, w):
            monkeypatch.setattr(bus_client, w, _no_write)
    monkeypatch.setattr(news_live, "_query_symbol", lambda: query)
    before = set(ui.context.client.elements)
    news_live.render()
    _LAST_BEFORE[:] = [before]
    return _new(before), asked


_LAST_BEFORE: list = []


def _texts(elements):
    return [getattr(e, "text", None) for e in elements]


def test_it_draws_the_public_feed_and_never_the_private_one(monkeypatch):
    from pages import news_view as nv
    new, asked = _render(monkeypatch)
    texts = _texts(new)
    assert "Public NVDA story" in texts and "Public AAPL story" in texts
    assert "PRIVATE feed only" not in texts
    assert "Owner feed" not in texts
    assert nv.VIEW not in asked and nv.VIEW_STATUS not in asked
    assert nv.VIEW_PUBLIC in asked


def test_it_builds_no_refresh_no_watchlist_and_no_dossier_link(monkeypatch):
    from nicegui import ui
    new, _ = _render(monkeypatch)
    texts = {t for t in _texts(new) if isinstance(t, str)}
    assert "Refresh" not in texts
    assert not [e for e in new if isinstance(e, ui.switch)]
    assert not [e for e in new if isinstance(e, ui.link)
                and "/symbol" in str(e.props.get("href", ""))]


def test_the_query_symbol_seeds_the_filter(monkeypatch):
    new, _ = _render(monkeypatch, query="aapl")
    texts = _texts(new)
    assert "Public AAPL story" in texts
    assert "Public NVDA story" not in texts


def test_a_refused_query_symbol_filters_nothing(monkeypatch):
    new, _ = _render(monkeypatch, query="<script>alert(1)</script>")
    texts = _texts(new)
    assert "Public AAPL story" in texts and "Public NVDA story" in texts


def test_a_ticker_chip_filters_the_page(monkeypatch):
    new, _ = _render(monkeypatch)
    chips = [e for e in new if getattr(e, "text", None) == "AAPL"
             and any(lst.type == "click" for lst in e._event_listeners.values())]
    assert chips
    for lst in list(chips[0]._event_listeners.values()):
        if lst.type == "click":
            lst.handler(None)
    from nicegui import ui
    live = [e.text for e in _new(_LAST_BEFORE[0]) if isinstance(e, ui.link)]
    assert "Public AAPL story" in live
    assert "Public NVDA story" not in live


def test_a_cold_public_feed_says_nothing_has_been_published(monkeypatch):
    from pages import copy as shared_copy
    new, _ = _render(monkeypatch, public=None)
    assert shared_copy.WAITING_NEWS in _texts(new)


def test_trending_reads_the_public_payload(monkeypatch):
    """A ticker only the private feed carries never trends here."""
    new, _ = _render(monkeypatch, public={"items": [
        _item(1, title="a", tickers=["AAPL"]), _item(2, title="b", tickers=["AAPL"])]})
    texts = [t for t in _texts(new) if isinstance(t, str)]
    assert "AAPL 2" in texts
    assert not [t for t in texts if t.startswith("NVDA ")]


def test_the_empty_feed_line_is_the_private_pages_own():
    """The public page draws the private page's empty-feed line, never a copy."""
    from pages import news, news_live
    assert news_live.EMPTY_FEED is news.EMPTY_FEED
    assert "carries no items" not in _src()
