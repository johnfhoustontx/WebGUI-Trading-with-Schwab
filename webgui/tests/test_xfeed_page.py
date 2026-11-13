"""The /news/x page (X Feed): route and tab group, help, the enqueue gate, the
import boundary, and the page's behaviour on a fake bus.

The page reads ``cache:xfeed:latest`` and ``cache:xfeed:accounts`` and sends
``cmd:xfeed`` commands, each answered on its own ``cache:xfeed:answer:{rid}``.
These tests stand in for the bus with monkeypatched ``bus_client`` functions and
drive the page through its widgets' own handlers and timers.
"""
import ast
import asyncio
import datetime as dt
import pathlib
import re
from zoneinfo import ZoneInfo

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"
_CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 11, 13, 15, 0, tzinfo=_CT)
NOW_MS = int(NOW.timestamp() * 1000)
_RID = re.compile(r"^[0-9a-f]{32}$")


def _page_sources():
    return [p for p in sorted(PAGES.glob("xfeed*.py")) if p.name != "xfeed_view.py"]


# ── route, tab group, help ───────────────────────────────────────────────────
def test_route_is_in_market_news_group():
    import main
    from nicegui import Client
    assert ("/news/x", "X Feed", "alternate_email") in main.MARKET_NEWS_CHILDREN
    assert main.MARKET_NEWS_CHILDREN[0] == ("/news", "Headlines", "newspaper")
    assert "/news" not in [r for r, _l, _i in main.OPTIONS_RAIL]
    assert ("Market News", "newspaper", main.MARKET_NEWS_CHILDREN) in main._NAV_GROUPS
    assert main._group_children("/news/x") == main.MARKET_NEWS_CHILDREN
    assert main.breadcrumb_trail("/news/x") == ["Markets", "Market News", "X Feed"]
    assert "/news/x" in set(Client.page_routes.values())


def test_x_feed_has_its_own_tab_colour():
    import main
    mine = main._TAB_COLOR["/news/x"]
    assert [r for r, c in main._TAB_COLOR.items() if c == mine] == ["/news/x"]


def test_help_exists():
    import page_help
    assert "/news/x" in page_help.HELP_MD
    assert "**" in page_help.HELP_MD["/news/x"]


# ── source guards ────────────────────────────────────────────────────────────
def _gated(tree):
    """Every ``*.request(...)`` call, and whether an enclosing ``if`` tests ``_may_enqueue``."""
    out = []

    def mentions(test):
        return any((isinstance(n, ast.Name) and n.id == "_may_enqueue")
                   or (isinstance(n, ast.Attribute) and n.attr == "_may_enqueue")
                   for n in ast.walk(test))

    def walk(node, gated):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "request":
            out.append(gated)
        if isinstance(node, ast.If) and mentions(node.test):
            for child in node.body:
                walk(child, True)
            for child in node.orelse:
                walk(child, gated)
            return
        for child in ast.iter_child_nodes(node):
            walk(child, gated)

    walk(tree, False)
    return out


def test_every_request_is_gated_by_may_enqueue():
    src = (PAGES / "xfeed.py").read_text(encoding="utf-8")
    calls = _gated(ast.parse(src))
    assert calls, "the page sends commands, so it must have a request call"
    assert all(calls), "a bus_client.request call sits outside an `if _may_enqueue` block"
    assert "_may_enqueue = _shell.may_enqueue()" in src
    for p in _page_sources():
        if p.name != "xfeed.py":
            assert not _gated(ast.parse(p.read_text(encoding="utf-8"))), p.name


def test_no_ui_html():
    """Post text, names and URLs are third-party: never ``ui.html``."""
    for p in _page_sources():
        src = p.read_text(encoding="utf-8")
        assert "ui.html(" not in src, p.name
        html_calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Attribute) and n.func.attr == "html"]
        assert html_calls == [], p.name


_DENIED = ("sqlite3", "requests", "redis", "httpx", "urllib.request", "services",
           "shared.bus", "main", "live_main")


def test_imports_stay_on_allow_list():
    """Tier 1: no store, no HTTP, no service module, no ``main`` (ruling R3: a
    deny-list, so a harmless stdlib import does not break the guard)."""
    for p in _page_sources():
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not any(name == d or name.startswith(d + ".") for d in _DENIED), \
                    f"{p.name} imports {name}"


# ── fixtures ─────────────────────────────────────────────────────────────────
def _post(i, *, handle="reuters", minutes_ago=5, text=None, type_="post", origin="poll",
          seen_s_ago=600, cashtags=(), media=(), quote=None, url=None):
    at = NOW_MS - minutes_ago * 60_000
    return {"kind": "post", "account_handle": handle, "status_id": str(1000 + i),
            "first_seen_at": NOW_MS - seen_s_ago * 1000, "created_at": at, "sort_at": at,
            "origin": origin, "type": type_, "text": text if text is not None else f"Post number {i}",
            "author_name": handle.title(), "author_handle": handle, "author_avatar": None,
            "url": url or f"https://x.com/{handle}/status/{1000 + i}",
            "cashtags": list(cashtags), "media": list(media), "quote": quote,
            "counts": {"replies": 1, "reposts": 2, "likes": 1500, "views": 12_000}}


def _gap(i, *, handle="reuters", minutes_ago=30):
    at = NOW_MS - minutes_ago * 60_000
    return {"kind": "gap", "id": i, "account_handle": handle, "from_at": at - 3_600_000,
            "to_at": at, "sort_at": at}


def _account(handle, group=None, *, error=None):
    return {"handle": handle, "display_name": handle.title(), "avatar_url": None,
            "active": True, "added_at": 1, "baseline_at": 1, "last_checked_at": NOW_MS - 30_000,
            "last_ok_at": NOW_MS - 30_000, "last_error": error, "fail_count": 0,
            "not_found_streak": 0, "next_check_at": 0, "group_name": group}


def _accounts_view(*accounts, counts=None):
    accounts = accounts or (_account("reuters", "News"), _account("zerohedge", "Finance"))
    return {"accounts": list(accounts),
            "counts": counts if counts is not None else {a["handle"]: 3 for a in accounts},
            "interval_s": 60,
            "page": {"new_highlight_s": 60, "answer_poll_s": 0.25, "answer_timeout_s": 20}}


def _latest(rows, tickers=(), limit=None):
    out = {"rows": list(rows), "tickers": list(tickers), "total": len(rows)}
    if limit is not None:
        out["limit"] = limit
    return out


def _new_elements(before):
    from nicegui import ui
    return [e for k, e in ui.context.client.elements.items() if k not in before]


def _click(el):
    for lst in list(el._event_listeners.values()):
        if lst.type.split(".")[0] == "click":
            lst.handler(None)
            return
    raise AssertionError(f"{el!r} has no click listener")


class _Bus:
    """The views the fake bus holds, the commands sent, and the watch callbacks."""

    def __init__(self, views):
        self.views = dict(views)
        self.sent = []
        self.watched = {}
        self.meta_ts = NOW.astimezone(dt.timezone.utc).isoformat()

    def answer(self, command, **extra):
        rid = command["args"]["request_id"]
        from pages import xfeed_view as xv
        payload = {"request_id": rid, "command": command["type"],
                   "ok": extra.pop("ok", True), "error": extra.pop("error", "")}
        payload.update(extra)
        self.views[xv.answer_view(rid)] = payload


def _render(monkeypatch, views, *, may_enqueue=True, meta_ts=None):
    import bus_client
    import shell
    from nicegui import ui

    import pages.view_watch as vw
    from pages import xfeed
    bus = _Bus(views)
    if meta_ts is not None:
        bus.meta_ts = meta_ts
    monkeypatch.setattr(bus_client, "read", lambda v: bus.views.get(v))
    monkeypatch.setattr(bus_client, "read_version",
                        lambda v: 1 if bus.views.get(v) is not None else None)
    monkeypatch.setattr(bus_client, "read_meta", lambda v: (1, bus.meta_ts))
    monkeypatch.setattr(bus_client, "request",
                        lambda d, c: bus.sent.append((d, c)) or "1-0")
    monkeypatch.setattr(vw, "watch_view",
                        lambda view, fn, **k: bus.watched.__setitem__(view, fn))
    monkeypatch.setattr(shell, "may_enqueue", lambda: may_enqueue)
    monkeypatch.setattr(xfeed, "now_ms", lambda: NOW_MS)
    before = set(ui.context.client.elements)
    bus.page = xfeed.render()
    bus.before = before
    return bus


def _texts(bus):
    from nicegui import ui
    return [e.text for e in _new_elements(bus.before) if isinstance(e, (ui.label, ui.link))]


def _labelled(bus, text, kind=None):
    from nicegui import ui
    kind = kind or (ui.label, ui.link, ui.button)
    return [e for e in _new_elements(bus.before)
            if isinstance(e, kind) and getattr(e, "text", None) == text]


def _fire_answer_timers(bus):
    """Run every pending answer timer once (they read the answer key)."""
    from nicegui import ui
    for t in [e for e in _new_elements(bus.before) if isinstance(e, ui.timer)]:
        if getattr(t, "_xfeed_answer", False) and t.callback is not None:
            asyncio.run(t.callback())


def _views(rows, accounts=None, tickers=()):
    from pages import xfeed_view as xv
    return {xv.VIEW_LATEST: _latest(rows, tickers),
            xv.VIEW_ACCOUNTS: accounts or _accounts_view()}


# ── the live view ────────────────────────────────────────────────────────────
def test_unfiltered_page_draws_the_latest_rows_and_gaps(monkeypatch):
    from pages import xfeed_view as xv
    gap = _gap(1)
    bus = _render(monkeypatch, _views([_post(1, text="Fed holds rates\nmore"), gap]))
    texts = _texts(bus)
    assert "Fed holds rates" in texts
    assert xv.gap_text(gap) in texts
    assert bus.sent == []                       # nothing filtered, nothing asked


def test_a_new_live_row_is_marked_and_an_old_one_is_not(monkeypatch):
    from pages import xfeed
    rows = [_post(1, text="Fresh one", origin="live", seen_s_ago=10),
            _post(2, text="Stale one", origin="live", seen_s_ago=600)]
    bus = _render(monkeypatch, _views(rows))
    assert _labelled(bus, "NEW")
    accented = [e for e in _new_elements(bus.before)
                if xfeed.NEW_ACCENT.split()[0] in e._classes]
    assert len(accented) == 1


def test_no_accounts_says_so(monkeypatch):
    from pages import xfeed
    bus = _render(monkeypatch, _views([], accounts=_accounts_view(counts={}) | {"accounts": []}))
    assert xfeed.NO_ACCOUNTS in _texts(bus)
    assert xfeed.NO_ACCOUNTS == "No accounts yet — add one in the panel."


def test_status_line_reads_live_or_not_answering(monkeypatch):
    bus = _render(monkeypatch, _views([_post(1)]))
    assert "Live · checking every 60s" in _texts(bus)
    old = (NOW - dt.timedelta(minutes=10)).astimezone(dt.timezone.utc).isoformat()
    bus2 = _render(monkeypatch, _views([_post(1)]), meta_ts=old)
    assert "Live · checking every 60s" not in _texts(bus2)
    assert "X feed service not answering" in _texts(bus2)


def test_settings_link_names_the_interval(monkeypatch):
    from nicegui import ui
    bus = _render(monkeypatch, _views([_post(1)]))
    links = [e for e in _new_elements(bus.before) if isinstance(e, ui.link)
             and e.props.get("href") == "/settings"]
    assert links and "Checking every 60s" in links[0].text


# ── filters send a query and draw its answer ─────────────────────────────────
def test_a_type_filter_sends_a_query_and_draws_the_answer(monkeypatch):
    from pages import xfeed
    bus = _render(monkeypatch, _views([_post(1)]))
    (opt,) = [e for e in _new_elements(bus.before)
              if getattr(e, "_xfeed_type", None) == "repost"]
    _click(opt)
    ((domain, cmd),) = bus.sent
    assert domain == "xfeed" and cmd["type"] == "query"
    assert _RID.match(cmd["args"]["request_id"])
    assert cmd["args"]["type"] == "repost" and cmd["args"]["limit"] == xfeed.PAGE
    bus.answer(cmd, rows=[], tickers=[], counts={})
    _fire_answer_timers(bus)
    assert xfeed.NO_MATCH in _texts(bus)
    assert xfeed.NO_MATCH == "No posts match these filters."


def test_a_query_answer_replaces_the_rows(monkeypatch):
    bus = _render(monkeypatch, _views([_post(1, text="Live row")]))
    (opt,) = [e for e in _new_elements(bus.before) if getattr(e, "_xfeed_type", None) == "post"]
    _click(opt)
    ((_d, cmd),) = bus.sent
    bus.answer(cmd, rows=[_post(9, text="Answered row")], tickers=[{"tag": "NVDA", "n": 1}],
               counts={})
    _fire_answer_timers(bus)
    texts = _texts(bus)
    assert "Answered row" in texts


def test_an_unanswered_command_times_out_with_the_retry_line(monkeypatch):
    from pages import xfeed
    bus = _render(monkeypatch, _views([_post(1)]))
    (opt,) = [e for e in _new_elements(bus.before) if getattr(e, "_xfeed_type", None) == "reply"]
    clock = [1000.0]
    monkeypatch.setattr(xfeed, "monotonic", lambda: clock[0])
    _click(opt)
    clock[0] += 21
    _fire_answer_timers(bus)
    assert xfeed.TIMEOUT == "The X feed service did not answer - try again."
    assert any(xfeed.TIMEOUT in t for t in _texts(bus))


def test_a_newer_latest_re_sends_the_current_query_while_filtered(monkeypatch):
    from pages import xfeed_view as xv
    bus = _render(monkeypatch, _views([_post(1)]))
    (opt,) = [e for e in _new_elements(bus.before) if getattr(e, "_xfeed_type", None) == "post"]
    _click(opt)
    assert len(bus.sent) == 1
    asyncio.run(_maybe(bus.watched[xv.VIEW_LATEST]))
    assert len(bus.sent) >= 2
    assert all(c["type"] == "query" and c["args"]["type"] == "post" for _d, c in bus.sent)


async def _maybe(fn):
    r = fn()
    if asyncio.iscoroutine(r):
        await r


def test_load_older_sends_before_from_the_last_post(monkeypatch):
    from pages import xfeed
    rows = [_post(i, minutes_ago=i) for i in range(xfeed.PAGE)]
    bus = _render(monkeypatch, _views(rows))
    (more,) = _labelled(bus, xfeed.LOAD_OLDER)
    _click(more)
    ((_d, cmd),) = bus.sent
    last = rows[-1]
    assert cmd["type"] == "query"
    assert cmd["args"]["before"] == last["sort_at"]
    assert cmd["args"]["before_key"] == f"{last['status_id']}:{last['account_handle']}"
    bus.answer(cmd, rows=[_post(5000, minutes_ago=5000, text="An older post")],
               tickers=[], counts={})
    _fire_answer_timers(bus)
    texts = _texts(bus)
    assert "An older post" in texts and "Post number 0" in texts   # appended, not replaced


def _type_option(bus, key):
    (opt,) = [e for e in _new_elements(bus.before) if getattr(e, "_xfeed_type", None) == key]
    return opt


def _more_shown(bus):
    return bus.page.more_row.visible


def test_a_failed_filter_query_drops_the_old_list_and_can_be_retried(monkeypatch):
    """A failed query must not leave the previous filter's rows (and their
    "Load older" cursor) live under the new filter's chips."""
    from pages import xfeed
    rows = [_post(i, minutes_ago=i) for i in range(xfeed.PAGE)]
    bus = _render(monkeypatch, _views(rows))
    assert _more_shown(bus)
    _click(_type_option(bus, "repost"))
    ((_d, cmd),) = bus.sent
    bus.answer(cmd, ok=False, error="database is locked")
    _fire_answer_timers(bus)
    texts = _texts(bus)
    assert "Could not load the feed: database is locked" in texts
    assert "Post number 0" not in texts             # the old filter's list is gone
    assert not _more_shown(bus) and bus.page.count.text == ""
    bus.page._more()                                # the sentinel firing late
    assert len(bus.sent) == 1                       # no stale-cursor query
    (retry,) = _labelled(bus, xfeed.RETRY)
    _click(retry)
    assert len(bus.sent) == 2
    _d, again = bus.sent[1]
    assert again["args"]["type"] == "repost" and "before" not in again["args"]
    bus.answer(again, rows=[_post(7, text="A repost")], tickers=[], counts={}, limit=xfeed.PAGE)
    _fire_answer_timers(bus)
    assert "A repost" in _texts(bus)


def test_after_a_failure_choosing_the_same_filter_again_retries(monkeypatch):
    bus = _render(monkeypatch, _views([_post(1)]))
    _click(_type_option(bus, "reply"))
    ((_d, cmd),) = bus.sent
    bus.answer(cmd, ok=False, error="boom")
    _fire_answer_timers(bus)
    _click(_type_option(bus, "reply"))              # the same option, re-clicked
    assert len(bus.sent) == 2 and bus.sent[1][1]["args"]["type"] == "reply"


def test_load_older_waits_while_a_new_filter_is_loading(monkeypatch):
    from pages import xfeed
    rows = [_post(i, minutes_ago=i) for i in range(xfeed.PAGE)]
    bus = _render(monkeypatch, _views(rows))
    _click(_type_option(bus, "post"))
    assert len(bus.sent) == 1
    assert not _more_shown(bus)
    bus.page._more()
    assert len(bus.sent) == 1                       # the old list's cursor is not sent


def test_load_older_shows_when_the_latest_view_is_smaller_than_a_page(monkeypatch):
    """views.latest_rows (Settings) can be below the page's ask: the latest view
    names its own limit and a full view still offers older posts."""
    from pages import xfeed
    from pages import xfeed_view as xv
    rows = [_post(i, minutes_ago=i) for i in range(50)]
    views = _views(rows)
    views[xv.VIEW_LATEST] = _latest(rows, limit=50)
    bus = _render(monkeypatch, views)
    assert _more_shown(bus)
    (more,) = _labelled(bus, xfeed.LOAD_OLDER)
    _click(more)
    assert len(bus.sent) == 1
    bus2 = _render(monkeypatch, {**views, xv.VIEW_LATEST: _latest(rows[:49], limit=50)})
    assert not _more_shown(bus2)                    # fewer than its limit: that is all


def test_query_pages_below_the_page_size_keep_paging(monkeypatch):
    """views.query_limit_max (Settings) can clamp a query below PAGE: the answer
    names the limit used, and a full answer still offers older posts."""
    from pages import xfeed
    bus = _render(monkeypatch, _views([_post(1)]))
    _click(_type_option(bus, "post"))
    ((_d, cmd),) = bus.sent
    page = [_post(i, minutes_ago=i) for i in range(20)]
    bus.answer(cmd, rows=page, tickers=[], counts={}, limit=20)
    _fire_answer_timers(bus)
    assert _more_shown(bus)
    (more,) = [e for e in _labelled(bus, xfeed.LOAD_OLDER)]
    _click(more)
    assert len(bus.sent) == 2
    _d, older = bus.sent[1]
    assert older["args"]["before"] == page[-1]["sort_at"]
    bus.answer(older, rows=[_post(100 + i, minutes_ago=100 + i) for i in range(20)],
               tickers=[], counts={}, limit=20)
    _fire_answer_timers(bus)
    assert _more_shown(bus)                         # still a full page: still more
    _click(more)
    _d, last = bus.sent[2]
    bus.answer(last, rows=[_post(500, minutes_ago=500)], tickers=[], counts={}, limit=20)
    _fire_answer_timers(bus)
    assert not _more_shown(bus)


# ── an expanded row ──────────────────────────────────────────────────────────
def test_a_row_expands_to_its_text_links_and_open_on_x(monkeypatch):
    from nicegui import ui
    text = "Watch $NVDA now https://example.com/a and javascript:alert(1) @fed"
    row = _post(1, text=text, cashtags=["NVDA"],
                media=[{"type": "photo", "url": "https://pbs.twimg.com/media/a.jpg?name=orig",
                        "thumb": "https://pbs.twimg.com/media/a.jpg?name=orig"}],
                quote={"author_name": "Q", "author_handle": "q", "text": "quoted text",
                       "url": "https://x.com/q/status/9"})
    bus = _render(monkeypatch, _views([row]))
    (line,) = [e for e in _new_elements(bus.before) if getattr(e, "_xfeed_key", None)]
    _click(line)
    new = _new_elements(bus.before)
    links = {e.text: e.props.get("href") for e in new if isinstance(e, ui.link)}
    assert links["Open on X ↗"] == row["url"]
    assert links["example.com/a"] == "https://example.com/a"
    assert links["@fed"] == "https://x.com/fed"
    assert "/symbol?symbol=NVDA" in links.values()
    assert not [h for h in links.values() if h and h.startswith("javascript")]
    assert "quoted text" in _texts(bus)
    assert any(t.startswith("Posted ") for t in _texts(bus))
    assert [e for e in new if isinstance(e, ui.image)]


# ── accounts ─────────────────────────────────────────────────────────────────
def test_adding_an_account_sends_add_account_and_shows_an_error_inline(monkeypatch):
    from nicegui import ui
    bus = _render(monkeypatch, _views([_post(1)]))
    (handle_in,) = [e for e in _new_elements(bus.before) if getattr(e, "_xfeed_add", None) == "handle"]
    (group_in,) = [e for e in _new_elements(bus.before) if getattr(e, "_xfeed_add", None) == "group"]
    handle_in.value = "@nobody"
    group_in.value = "News"
    (add,) = [e for e in _labelled(bus, "Add", ui.button)]
    _click(add)
    ((_d, cmd),) = bus.sent
    assert cmd["type"] == "add_account"
    assert cmd["args"]["handle"] == "@nobody" and cmd["args"]["group"] == "News"
    bus.answer(cmd, ok=False, error="Account not found")
    _fire_answer_timers(bus)
    assert "@nobody: Account not found" in _texts(bus)


def test_an_account_error_is_softened_as_the_old_page_did(monkeypatch):
    accounts = _accounts_view(_account("gone", "News", error="not found"),
                              _account("flaky", "News", error="HTTP 500"))
    bus = _render(monkeypatch, _views([_post(1)], accounts=accounts))
    texts = _texts(bus)
    assert "account not found" in texts and "error: HTTP 500" in texts


def test_accounts_are_grouped_under_their_group_names(monkeypatch):
    accounts = _accounts_view(_account("reuters", "News"), _account("zerohedge", "Finance"),
                              _account("loner"))
    bus = _render(monkeypatch, _views([_post(1)], accounts=accounts))
    texts = _texts(bus)
    for word in ("News", "Finance", "Ungrouped", "@reuters", "@zerohedge", "@loner"):
        assert word in texts, word


def test_nothing_that_sends_is_drawn_where_this_process_may_not_enqueue(monkeypatch):
    from nicegui import ui
    from pages import xfeed
    rows = [_post(i, minutes_ago=i) for i in range(xfeed.PAGE)]
    bus = _render(monkeypatch, _views(rows), may_enqueue=False)
    assert not _labelled(bus, "Add", ui.button)
    assert not _labelled(bus, xfeed.LOAD_OLDER)
    assert not [e for e in _new_elements(bus.before) if getattr(e, "_xfeed_type", None)]
    assert bus.sent == []
