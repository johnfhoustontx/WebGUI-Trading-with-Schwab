"""Port of the behavioural tests of D:\\X-Posts\\test\\server.test.js as command tests.

The HTTP-only tests (static files, SSE, bad JSON, 415) have no command
equivalent; ``PUT /api/settings`` has none either (the interval is a config
value here, not a runtime setting).
"""
import itertools
import uuid

import pytest

from services import _degrade
from services.xfeed_svc import config, handlers, runtime
from services.xfeed_svc.poller import Poller
from services.xfeed_svc.store import Store
from shared.bus.client import Bus, reset_fake_bus
from shared.contracts.envelope import Command

_seq = itertools.count(9000)


def st(id=None, author="acct", created=0):
    sid = id if id is not None else str(next(_seq))
    return {
        "id": sid, "url": f"https://x.com/{author}/status/{sid}", "text": f"text {sid}",
        "author": {"screen_name": author, "name": "Acct", "avatar_url": None},
        "created_timestamp": created, "replying_to": None, "reposted_by": None, "media": None, "quote": None,
    }


def ok_page(results):
    return {"status": "ok", "results": results, "cursor_bottom": None}


class StubFx:
    """handle -> queue of responses (the last one repeats); unknown handle -> not_found."""

    def __init__(self, script):
        self.script = script

    def fetch_statuses(self, handle, *, cursor=None, with_replies=True):
        q = self.script.get(handle)
        if not q:
            return {"status": "not_found"}
        return q.pop(0) if len(q) > 1 else q[0]


@pytest.fixture
def bus():
    reset_fake_bus()
    return Bus(fake=True)


@pytest.fixture
def make(bus):
    def _make(script=None):
        store = Store(":memory:")
        fx = StubFx(script or {})
        rt = runtime.Runtime(store, fx, Poller(store, fx, sleep=lambda s: None))
        runtime.set_for_tests(rt)
        return rt
    yield _make
    runtime.set_for_tests(None)


def rid():
    return uuid.uuid4().hex


def send(bus, type_, **args):
    """Run one command and return its answer payload."""
    request_id = args.pop("request_id", None) or rid()
    handlers.handle_command(bus, Command(type=type_, args={"request_id": request_id, **args}))
    env = bus.cache_get(handlers.answer_key(request_id))
    return env.payload if env is not None else None


def payload(bus, key):
    env = bus.cache_get(key)
    return env.payload if env is not None else None


def post(handle, sid, *, tags=(), type_="post", sort_at=1):
    return {"status_id": sid, "account_handle": handle, "type": type_, "text": sid, "author_name": handle,
            "author_handle": handle, "url": "u", "created_at": 1, "cashtags": list(tags), "media": [],
            "quote": None, "counts": {}, "raw": None, "first_seen_at": 1, "sort_at": sort_at, "origin": "live"}


# -- add_account ----------------------------------------------------------

def test_add_account_adds_and_normalizes_handle(bus, make):
    make({"nasa": [ok_page([st(author="nasa")])]})
    r = send(bus, "add_account", handle="https://x.com/NASA")
    assert r["ok"] is True and r["error"] == "" and r["command"] == "add_account"
    assert r["account"]["handle"] == "nasa"
    view = payload(bus, handlers.CACHE_ACCOUNTS)
    assert [a["handle"] for a in view["accounts"]] == ["nasa"]
    assert view["interval_s"] == 60


def test_add_with_group_reuses_existing_spelling(bus, make):
    make({"a": [ok_page([])], "b": [ok_page([])], "c": [ok_page([])]})
    assert send(bus, "add_account", handle="a", group="  News  ")["account"]["group_name"] == "News"
    assert send(bus, "add_account", handle="b", group="news")["account"]["group_name"] == "News"
    assert send(bus, "add_account", handle="c", group="")["account"]["group_name"] is None
    r = send(bus, "add_account", handle="zz", group="x" * 31)
    assert r["ok"] is False and r["error"] == "Group names can be at most 30 characters"


def test_add_invalid_handle(bus, make):
    make()
    r = send(bus, "add_account", handle="not a handle")
    assert r["ok"] is False and r["error"] == "Invalid handle"


def test_add_unknown_account_is_not_stored(bus, make):
    rt = make()
    r = send(bus, "add_account", handle="ghost")
    assert r["ok"] is False and r["error"] == "Account not found"
    assert rt.store.get_account("ghost") is None


def test_add_upstream_error_carries_the_reason(bus, make):
    make({"acct": [{"status": "error", "error": "HTTP 503"}]})
    r = send(bus, "add_account", handle="acct")
    assert r["ok"] is False
    assert "HTTP 503" in r["error"] and r["error"].startswith("Could not reach FxEmbed (")


def test_add_duplicate(bus, make):
    make({"acct": [ok_page([st()])]})
    send(bus, "add_account", handle="acct")
    r = send(bus, "add_account", handle="@ACCT")
    assert r["ok"] is False and r["error"] == "Already watching @acct"


def test_readd_takes_a_new_baseline(bus, make):
    s1 = st(id="1")
    rt = make({"acct": [ok_page([s1])]})
    send(bus, "add_account", handle="acct")
    rt.store.insert_posts([post("acct", "2", sort_at=5)])
    assert send(bus, "remove_account", handle="acct")["ok"] is True
    rt.fx.script["acct"] = [ok_page([st(id="3"), s1])]
    assert send(bus, "add_account", handle="acct")["ok"] is True
    feed = send(bus, "query")
    assert [r["status_id"] for r in feed["rows"]] == ["2"]


# -- set_group / remove_account -------------------------------------------

def test_set_group_changes_or_clears(bus, make):
    make({"a": [ok_page([])]})
    send(bus, "add_account", handle="a", group="News")
    r = send(bus, "set_group", handle="a", group="Finance")
    assert r["ok"] is True and r["account"]["group_name"] == "Finance"
    assert send(bus, "set_group", handle="a", group=None)["account"]["group_name"] is None
    r = send(bus, "set_group", handle="nobody", group="X")
    assert r["ok"] is False and r["error"] == "Not watching that account"


def test_set_group_too_long(bus, make):
    make({"a": [ok_page([])]})
    send(bus, "add_account", handle="a")
    r = send(bus, "set_group", handle="a", group="y" * 31)
    assert r["ok"] is False and r["error"] == "Group names can be at most 30 characters"


def test_remove_hides_posts_and_unknown_handle_is_refused(bus, make):
    rt = make({"acct": [ok_page([])]})
    send(bus, "add_account", handle="acct")
    rt.store.insert_posts([post("acct", "7")])
    assert len(send(bus, "query")["rows"]) == 1
    r = send(bus, "remove_account", handle="acct")
    assert r["ok"] is True and r["error"] == ""
    assert send(bus, "query")["rows"] == []
    assert payload(bus, handlers.CACHE_ACCOUNTS)["accounts"] == []
    assert payload(bus, handlers.CACHE_LATEST)["rows"] == []
    r = send(bus, "remove_account", handle="nobody")
    assert r["ok"] is False and r["error"] == "Not watching that account"


# -- query ----------------------------------------------------------------

def test_query_filters_by_group_and_ungrouped(bus, make):
    rt = make({"a": [ok_page([])], "b": [ok_page([])]})
    send(bus, "add_account", handle="a", group="News")
    send(bus, "add_account", handle="b")
    rt.store.insert_posts([post("a", "a1"), post("b", "b1")])
    assert [r["account_handle"] for r in send(bus, "query", group="News")["rows"]] == ["a"]
    assert [r["account_handle"] for r in send(bus, "query", ungrouped=True)["rows"]] == ["b"]
    assert [r["account_handle"] for r in send(bus, "query", ungrouped="1")["rows"]] == ["b"]


def test_query_returns_counts_and_tickers_and_passes_filters(bus, make):
    rt = make({"acct": [ok_page([])]})
    send(bus, "add_account", handle="acct")
    rt.store.insert_posts([post("acct", "1", tags=["NVDA", "TSLA"], sort_at=1),
                           post("acct", "2", tags=["NVDA"], sort_at=2),
                           post("acct", "3", type_="repost", sort_at=3)])
    r = send(bus, "query")
    assert r["counts"] == {"acct": 3}
    assert r["tickers"] == [{"tag": "NVDA", "n": 2}, {"tag": "TSLA", "n": 1}]
    assert len(send(bus, "query", type="repost")["rows"]) == 1
    assert len(send(bus, "query", ticker="TSLA")["rows"]) == 1
    assert [x["status_id"] for x in send(bus, "query", before=3, limit=1)["rows"]] == ["2"]
    assert [x["status_id"] for x in send(bus, "query", before=3, before_key="3:acct")["rows"]] == ["2", "1"]
    assert [x["status_id"] for x in send(bus, "query", before=2, before_key="3:acct")["rows"]] == ["2", "1"]


def test_query_limit_is_clamped(bus, make):
    rt = make({"acct": [ok_page([])]})
    send(bus, "add_account", handle="acct")
    rt.store.insert_posts([post("acct", str(i), sort_at=i) for i in range(1, 4)])
    assert len(send(bus, "query", limit=0)["rows"]) == 3        # 0 -> default 300
    assert len(send(bus, "query", limit=-5)["rows"]) == 1       # clamped up to 1
    assert len(send(bus, "query", limit=10**9)["rows"]) == 3    # clamped down to query_limit_max


def _views_cfg(monkeypatch, **views):
    real = config.load()
    patched = {**real, "views": {**real["views"], **views}}
    monkeypatch.setattr(config, "load", lambda: patched)


def test_query_answer_names_the_limit_it_used(bus, make, monkeypatch):
    """The page decides "more to load" by comparing the posts it got with the
    size actually used - query_limit_max (a Settings tunable) may be below the
    page's ask."""
    rt = make({"acct": [ok_page([])]})
    send(bus, "add_account", handle="acct")
    rt.store.insert_posts([post("acct", str(i), sort_at=i) for i in range(1, 4)])
    assert send(bus, "query", limit=300)["limit"] == 300
    _views_cfg(monkeypatch, query_limit_max=2)
    r = send(bus, "query", limit=300)
    assert r["limit"] == 2 and len(r["rows"]) == 2


def test_latest_view_names_its_row_limit(bus, make, monkeypatch):
    rt = make()
    handlers.publish_latest(bus, rt)
    assert payload(bus, handlers.CACHE_LATEST)["limit"] == config.load()["views"]["latest_rows"]
    _views_cfg(monkeypatch, latest_rows=50)
    handlers.publish_latest(bus, rt)
    assert payload(bus, handlers.CACHE_LATEST)["limit"] == 50


def test_two_queries_get_separate_answer_keys(bus, make):
    rt = make({"a": [ok_page([])], "b": [ok_page([])]})
    send(bus, "add_account", handle="a", group="News")
    send(bus, "add_account", handle="b", group="Other")
    rt.store.insert_posts([post("a", "a1"), post("b", "b1")])
    r1, r2 = rid(), rid()
    handlers.handle_command(bus, Command(type="query", args={"request_id": r1, "group": "News"}))
    handlers.handle_command(bus, Command(type="query", args={"request_id": r2, "group": "Other"}))
    assert [r["account_handle"] for r in payload(bus, handlers.answer_key(r1))["rows"]] == ["a"]
    assert [r["account_handle"] for r in payload(bus, handlers.answer_key(r2))["rows"]] == ["b"]


# -- views, ids, drops ----------------------------------------------------

def test_empty_store_publishes_empty_views(bus, make):
    rt = make()
    handlers.publish_all(bus, rt)
    latest = payload(bus, handlers.CACHE_LATEST)
    assert latest["rows"] == [] and latest["tickers"] == [] and latest["total"] == 0
    acc = payload(bus, handlers.CACHE_ACCOUNTS)
    assert acc["accounts"] == [] and acc["counts"] == {}
    assert acc["page"] == config.load()["page"]


def test_publish_is_skip_unchanged(bus, make):
    rt = make()
    v = handlers.publish_latest(bus, rt)
    assert handlers.publish_latest(bus, rt) == v


def test_latest_view_after_add_holds_rows_and_tickers(bus, make):
    rt = make({"acct": [ok_page([])]})
    send(bus, "add_account", handle="acct")
    rt.store.insert_posts([post("acct", "1", tags=["NVDA"])])
    handlers.publish_all(bus, rt)
    latest = payload(bus, handlers.CACHE_LATEST)
    assert latest["total"] == 1 and latest["tickers"] == [{"tag": "NVDA", "n": 1}]


def test_dropped_command_answers_page(bus, make):
    make()
    request_id = rid()
    handlers.on_dropped(bus, Command(type="query", args={"request_id": request_id}), "restart")
    r = payload(bus, handlers.answer_key(request_id))
    assert r["ok"] is False and r["error"] == "The X feed service restarted - try again."
    assert r["request_id"] == request_id


def test_answers_expire(bus, make):
    make()
    request_id = rid()
    send(bus, "query", request_id=request_id)
    ttl = bus._r.ttl(handlers.answer_key(request_id))
    assert 0 < ttl <= config.load()["views"]["answer_ttl_s"]


def test_bad_request_id_is_ignored(bus, make):
    make()
    for bad in ("../x", "", "ABCDEF" * 6, "a" * 31, None, 5):
        handlers.handle_command(bus, Command(type="query", args={"request_id": bad}))
        handlers.on_dropped(bus, Command(type="query", args={"request_id": bad}), "expired")
    assert list(bus._r.scan_iter("cache:xfeed:answer:*")) == []


def test_unexpected_failure_is_answered_not_raised(bus, make):
    rt = make()

    def boom(**kw):
        raise RuntimeError("db gone")
    rt.store.query_feed = boom
    _degrade.reset()
    r = send(bus, "query")
    assert r["ok"] is False and r["error"] == "Server error"
    assert _degrade.counts() == {"xfeed.handlers": 1}


def test_fault_log_does_not_quote_the_exception(bus, make, caplog):
    rt = make()

    def boom(**kw):
        raise RuntimeError("secret-handle-text")
    rt.store.query_feed = boom
    caplog.set_level("DEBUG")
    send(bus, "query")
    assert "secret-handle-text" not in caplog.text


def test_unknown_command_with_valid_id_is_answered(bus, make):
    make()
    r = send(bus, "bogus" * 10)
    assert r["ok"] is False and r["error"] == "Unknown command"
    assert len(r["command"]) <= 20


def test_non_dict_args_do_not_raise(bus, make):
    make()
    _degrade.reset()
    cmd = Command(type="query", args={"request_id": rid()})
    object.__setattr__(cmd, "args", "oops")
    handlers.handle_command(bus, cmd)
    handlers.on_dropped(bus, cmd, "restart")
    assert list(bus._r.scan_iter("cache:xfeed:answer:*")) == []


def test_on_dropped_never_raises_and_counts_a_bus_fault(make):
    make()
    _degrade.reset()

    class DeadBus:
        def cache_set(self, *a, **k):
            raise ConnectionError("down")
    handlers.on_dropped(DeadBus(), Command(type="query", args={"request_id": rid()}), "restart")
    assert _degrade.counts() == {"xfeed.handlers": 1}


def test_top_tickers_orders_by_count_then_tag_and_skips_gaps():
    rows = [{"kind": "post", "cashtags": ["B", "A"]}, {"kind": "post", "cashtags": ["B"]},
            {"kind": "gap", "from_at": 1}]
    assert handlers.top_tickers(rows, 10) == [{"tag": "B", "n": 2}, {"tag": "A", "n": 1}]
    assert handlers.top_tickers(rows, 1) == [{"tag": "B", "n": 2}]


def test_resolve_group_edge_cases():
    s = Store(":memory:")
    assert handlers.resolve_group(s, None, 30) is None
    assert handlers.resolve_group(s, "   ", 30) is None
    assert handlers.resolve_group(s, " a   b ", 30) == "a b"
    with pytest.raises(ValueError, match="at most 30 characters"):
        handlers.resolve_group(s, "x" * 31, 30)


def test_constants():
    assert handlers.COMMANDS == ("add_account", "remove_account", "set_group", "query")
    assert handlers.SLOW == frozenset({"add_account"})
    assert handlers.answer_key("ab") == "cache:xfeed:answer:ab"
