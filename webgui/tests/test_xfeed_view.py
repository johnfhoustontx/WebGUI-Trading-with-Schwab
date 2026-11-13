import ast
import datetime as dt
import pathlib

from pages import xfeed_view as xv

CT = dt.timezone(dt.timedelta(hours=-6))   # 13 Nov is CST


def ms(y, mo, d, h, mi, s=0):
    return int(dt.datetime(y, mo, d, h, mi, s, tzinfo=CT).timestamp() * 1000)


def post(**kw):
    base = {"kind": "post", "account_handle": "zerohedge", "status_id": "100", "first_seen_at": 0,
            "created_at": 0, "sort_at": ms(2026, 11, 13, 9, 5), "origin": "catchup", "type": "post",
            "text": "Hello", "author_name": "ZH", "author_handle": "zerohedge", "url": "https://x.com/a/status/1",
            "cashtags": [], "media": [], "quote": None, "counts": {}}
    base.update(kw)
    return base


def test_view_names():
    assert xv.VIEW_LATEST == "xfeed:latest" and xv.VIEW_ACCOUNTS == "xfeed:accounts"
    assert xv.answer_view("abc") == "xfeed:answer:abc"


def test_row_line_basic():
    r = xv.row_line(post(cashtags=["NVDA", "AAPL", "TSLA"], media=[{"type": "photo"}, {"type": "video"}, {"type": "photo"}],
                         quote={"text": "q"}), now_ms=0, new_highlight_s=60)
    assert r["time"] == "09:05" and r["type_word"] == "POST" and r["text"] == "Hello"
    assert r["photos"] == 2 and r["videos"] == 1 and r["has_quote"] is True
    assert r["chips"] == ["NVDA", "AAPL"] and r["more"] == 1 and r["handle"] == "@zerohedge"


def test_row_line_type_words_and_fallbacks():
    assert xv.row_line(post(type="repost"), now_ms=0, new_highlight_s=60)["type_word"] == "RT"
    assert xv.row_line(post(type="reply"), now_ms=0, new_highlight_s=60)["type_word"] == "REPLY"
    assert xv.row_line(post(text="", media=[{"type": "photo"}]), now_ms=0, new_highlight_s=60)["text"] == "(media)"
    assert xv.row_line(post(text="", quote={"text": "x"}), now_ms=0, new_highlight_s=60)["text"] == "(quote)"
    assert xv.row_line(post(text="  \n "), now_ms=0, new_highlight_s=60)["text"] == "(no text)"


def test_row_line_first_nonblank_line():
    assert xv.row_line(post(text="\n\n  first line\nsecond"), now_ms=0, new_highlight_s=60)["text"] == "first line"


def test_row_line_truncates_and_strips_newlines():
    text = "\n".join(["abcdefghij" * 20] * 10)
    t = xv.row_line(post(text=text), now_ms=0, new_highlight_s=60)["text"]
    assert "\n" not in t and len(t) <= 241
    t2 = xv.row_line(post(text="x" * 2000), now_ms=0, new_highlight_s=60)["text"]
    assert len(t2) == 241 and t2.endswith("…")


def test_new_only_for_live_within_window():
    now = 10_000_000
    assert xv.row_line(post(origin="live", first_seen_at=now - 30_000), now_ms=now, new_highlight_s=60)["is_new"]
    assert not xv.row_line(post(origin="live", first_seen_at=now - 90_000), now_ms=now, new_highlight_s=60)["is_new"]
    assert not xv.row_line(post(origin="catchup", first_seen_at=now - 1_000), now_ms=now, new_highlight_s=60)["is_new"]


def test_with_dividers_on_ct_date_change():
    a = post(status_id="1", sort_at=ms(2026, 11, 13, 23, 30))
    g = {"kind": "gap", "id": 1, "account_handle": "x", "from_at": 0, "to_at": 0, "sort_at": ms(2026, 11, 13, 10, 0)}
    b = post(status_id="2", sort_at=ms(2026, 11, 12, 8, 0))
    out = xv.with_dividers([a, g, b])
    assert out == [("day", "Fri 13 Nov"), ("row", a), ("gap", g), ("day", "Thu 12 Nov"), ("row", b)]
    assert xv.with_dividers([]) == []


def test_gap_text():
    g = {"kind": "gap", "account_handle": "reuters", "from_at": ms(2026, 11, 13, 8, 5), "to_at": ms(2026, 11, 13, 14, 30)}
    assert xv.gap_text(g) == "Some posts from @reuters between 08:05 and 14:30 may be missing"


def test_split_text_links():
    segs = xv.split_text("Buy $NVDA via @elon see https://www.x.com/a now")
    assert segs == [
        {"kind": "text", "text": "Buy ", "href": None},
        {"kind": "cashtag", "text": "$NVDA", "href": None},
        {"kind": "text", "text": " via ", "href": None},
        {"kind": "mention", "text": "@elon", "href": "https://x.com/elon"},
        {"kind": "text", "text": " see ", "href": None},
        {"kind": "url", "text": "x.com/a", "href": "https://www.x.com/a"},
        {"kind": "text", "text": " now", "href": None},
    ]
    assert xv.split_text("") == []


def test_split_text_never_links_unsafe_urls():
    segs = xv.split_text("<script>x</script> javascript:alert(1) https://ok.example/a")
    urls = [s for s in segs if s["kind"] == "url"]
    assert len(urls) == 1 and urls[0]["href"] == "https://ok.example/a"
    assert all(s["href"] is None for s in segs if s["kind"] == "text")
    assert "".join(s["text"] for s in segs if s["kind"] == "text").startswith("<script>x</script> javascript:alert(1)")


def test_split_text_url_without_host_is_text():
    segs = xv.split_text("see http://")
    assert [s["kind"] for s in segs] == ["text"] and segs[0]["text"] == "see http://"


def test_symbol_href():
    assert xv.symbol_href("nvda") == "/symbol?symbol=NVDA"


def test_query_args():
    assert xv.query_args({"group": None}, limit=300) == {"limit": 300}
    assert xv.query_args({"group": ""}, limit=300) == {"ungrouped": True, "limit": 300}
    assert xv.query_args({"group": "Macro", "account": "zh", "type": "repost"}, limit=50) == \
        {"group": "Macro", "account": "zh", "type": "repost", "limit": 50}
    row = post(status_id="77", account_handle="reuters", sort_at=12345)
    a = xv.query_args({"q": "oil"}, before_row=row, limit=10)
    assert a == {"q": "oil", "before": 12345, "before_key": "77:reuters", "limit": 10}


def test_dollar_search_becomes_ticker():
    a = xv.query_args({"q": "$nvda"}, limit=10)
    assert a == {"ticker": "NVDA", "limit": 10} and "q" not in a
    assert xv.query_args({"ticker": "$aapl"}, limit=10)["ticker"] == "AAPL"
    assert xv.query_args({"q": "$5 off"}, limit=10)["q"] == "$5 off"


def test_is_filtered():
    assert not xv.is_filtered({"group": "Macro"})
    assert not xv.is_filtered({})
    for k, v in (("account", "a"), ("type", "post"), ("q", "x"), ("ticker", "NVDA")):
        assert xv.is_filtered({k: v})


def _acc(handle, group=None, **kw):
    return {"handle": handle, "group_name": group, **kw}


def test_group_tabs():
    view = {"accounts": [_acc("a", "Macro"), _acc("b", "Tech"), _acc("c", "Macro"), _acc("d")],
            "counts": {"a": 5, "b": 2, "c": 1, "d": 4}}
    assert xv.group_tabs(view) == [
        {"key": None, "label": "All", "count": 12},
        {"key": "Macro", "label": "Macro", "count": 6},
        {"key": "Tech", "label": "Tech", "count": 2},
        {"key": "", "label": "Ungrouped", "count": 4},
    ]
    assert xv.group_tabs({"accounts": [_acc("a")], "counts": {}}) == []
    assert xv.group_tabs({}) == []
    all_grouped = xv.group_tabs({"accounts": [_acc("a", "G")], "counts": {"a": 1}})
    assert [t["key"] for t in all_grouped] == [None, "G"]


def test_accounts_by_group():
    view = {"accounts": [_acc("a", "Macro"), _acc("d"), _acc("c", "Macro")]}
    out = xv.accounts_by_group(view)
    assert [(t, [x["handle"] for x in lst]) for t, lst in out] == [("Macro", ["a", "c"]), ("Ungrouped", ["d"])]
    flat = xv.accounts_by_group({"accounts": [_acc("a")]})
    assert [(t, len(lst)) for t, lst in flat] == [("", 1)]
    assert xv.accounts_by_group({}) == []


def test_checked_text():
    assert xv.checked_text({"last_checked_at": 88_000}, 100_000) == ("checked 12s ago", "ok")
    assert xv.checked_text({"last_checked_at": 100_000 - 180_000}, 100_000) == ("checked 3m ago", "ok")
    assert xv.checked_text({"last_checked_at": None}, 100_000) == ("checked never", "ok")
    assert xv.checked_text({"last_error": "not found", "last_checked_at": 5}, 100_000) == ("not found", "error")


def test_status_text():
    assert xv.status_text({"interval_s": 60}, stale=False) == ("Live · checking every 60s", "ok")
    assert xv.status_text({"interval_s": 60}, stale=True) == ("X feed service not answering", "error")


def test_junk_tolerated():
    assert xv.group_tabs(None) == [] and xv.accounts_by_group(None) == []
    assert xv.status_text(None, stale=False)[1] == "ok"


def test_imports_are_tier1():
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "xfeed_view.py").read_text(encoding="utf-8")
    mods = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert mods <= {"datetime", "re", "zoneinfo", "pages"}
