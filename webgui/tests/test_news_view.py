import ast
import datetime as dt
import pathlib
import sys

from pages import news_view as nv

NOW = dt.datetime(2026, 9, 26, 1, 0, tzinfo=dt.timezone.utc)


def _it(i, *, tickers=(), source="MarketWatch", sources=None, hours_ago=0.5, public=True):
    ts = (NOW - dt.timedelta(hours=hours_ago)).isoformat()
    return {"id": f"i{i}", "source": source, "sources": sources or [source], "title": f"T{i}",
            "teaser": "", "url": f"https://x/{i}", "published_at": ts, "first_seen": ts,
            "tickers": list(tickers), "kind": "rss", "topics": [], "detail": {}, "public": public}


def test_rows_are_shaped_for_display_in_ct():
    # Central time, the app's convention (2026-09-26 coordinator decision; the
    # plan's value here was the ET "8:30 PM").
    r = nv.rows({"items": [_it(1, tickers=["NVDA"])]}, now=NOW)[0]
    assert r["time"] == "7:30 PM"          # 00:30 UTC → 19:30 CDT
    assert r["tickers"] == ["NVDA"] and r["sources"] == ["MarketWatch"]


def test_trending_counts_mentions_in_the_window():
    items = [_it(1, tickers=["NVDA"]), _it(2, tickers=["NVDA", "AAPL"]), _it(3, tickers=["AAPL"], hours_ago=9)]
    assert nv.trending({"items": items}, now=NOW, window_h=6) == [("NVDA", 2), ("AAPL", 1)]


def test_filters_by_source_and_symbol():
    items = [_it(1, tickers=["NVDA"]), _it(2, source="CNBC"), _it(3, tickers=["AAPL"])]
    rows = nv.rows({"items": items}, now=NOW)
    assert [r["id"] for r in nv.filter_rows(rows, sources={"CNBC"}, symbol=None)] == ["i2"]
    assert [r["id"] for r in nv.filter_rows(rows, sources=None, symbol="nvda")] == ["i1"]
    assert [r["id"] for r in nv.filter_rows(rows, sources=None, symbol=None, watchlist={"AAPL"})] == ["i3"]


def test_source_chips_come_from_the_rows_not_the_config():
    rows = nv.rows({"items": [_it(1), _it(2, source="CNBC")]}, now=NOW)
    assert nv.sources_present(rows) == ["CNBC", "MarketWatch"]


def test_unseen_count_and_cold_feed():
    assert nv.unseen({"items": [_it(1), _it(2, hours_ago=3)]}, since=(NOW - dt.timedelta(hours=1)).isoformat()) == 1
    assert nv.rows(None, now=NOW) == []


# ---- beyond the plan ------------------------------------------------------


def test_morning_time_has_no_leading_zero_and_carries_the_day():
    it = _it(1)
    it["published_at"] = "2026-09-25T13:05:00+00:00"      # 08:05 CT
    r = nv.rows({"items": [it]}, now=NOW)[0]
    assert r["time"] == "8:05 AM" and r["day"] == "Sep 25"


def test_naive_published_at_is_read_as_utc():
    it = _it(1)
    it["published_at"] = "2026-09-26T00:30:00"
    r = nv.rows({"items": [it]}, now=NOW)[0]
    assert r["time"] == "7:30 PM"
    assert r["age_min"] == 30


def test_unparseable_published_at_has_no_time_and_no_age():
    for junk in ("not a date", "", None, 12345, ["x"]):
        it = _it(1)
        it["published_at"] = junk
        r = nv.rows({"items": [it]}, now=NOW)[0]
        assert r["time"] == "" and r["day"] == "" and r["age_min"] is None


def test_undated_rows_never_trend():
    it = _it(1, tickers=["NVDA"])
    it["published_at"] = "bogus"
    assert nv.trending({"items": [it]}, now=NOW, window_h=6) == []


def test_ticker_counted_once_per_item():
    items = [_it(1, tickers=["NVDA", "nvda", "NVDA"])]
    assert nv.trending({"items": items}, now=NOW, window_h=6) == [("NVDA", 1)]


def test_unseen_compares_instants_not_strings():
    since = "2026-09-26T00:00:00+00:00"
    later = _it(1)
    later["first_seen"] = "2026-09-26T00:00:00.500000+00:00"
    same = _it(2)
    same["first_seen"] = since
    assert nv.unseen({"items": [later, same]}, since=since) == 1
    # Offset form of the SAME instant is not newer; a later instant in another
    # offset is (string order would say the opposite of both).
    offset_same = _it(3)
    offset_same["first_seen"] = "2026-09-25T20:00:00-04:00"
    offset_later = _it(4)
    offset_later["first_seen"] = "2026-09-25T20:00:01-04:00"
    assert nv.unseen({"items": [offset_same, offset_later]}, since=since) == 1


def test_unseen_ignores_unparseable_first_seen_and_since():
    it = _it(1)
    it["first_seen"] = "zzzz"                      # sorts after any ISO string
    assert nv.unseen({"items": [it]}, since="2026-09-26T00:00:00+00:00") == 0
    assert nv.unseen({"items": [_it(2)]}, since="garbage") == 0
    assert nv.unseen({"items": [_it(2)]}, since=None) == 0


def test_junk_payloads_never_raise():
    for junk in (None, [], "x", 3, {"items": None}, {"items": "abc"}, {"items": {"a": 1}}, {}):
        assert nv.rows(junk, now=NOW) == []
        assert nv.trending(junk, now=NOW, window_h=6) == []
        assert nv.unseen(junk, since=NOW.isoformat()) == 0
        assert nv.for_symbol(junk, "NVDA", now=NOW) == []


def test_junk_items_are_skipped_and_fields_cleaned():
    good = _it(9, tickers=["NVDA"])
    odd = _it(8)
    odd["tickers"] = ["AAPL", 7, None, "not a ticker!", {"x": 1}, "msft"]
    odd["sources"] = ["CNBC", 3, None, "", "Reuters"]
    odd["topics"] = "not a list"
    odd["detail"] = ["not", "a", "dict"]
    odd["title"] = None
    also_odd = _it(7)
    also_odd["tickers"] = "NVDA"                   # a string is not a list
    also_odd["sources"] = "CNBC"
    also_odd["source"] = "Yahoo"
    out = nv.rows({"items": ["str", 5, None, good, odd, also_odd]}, now=NOW)
    assert [r["id"] for r in out] == ["i9", "i8", "i7"]
    assert out[1]["tickers"] == ["AAPL", "MSFT"]
    assert out[1]["sources"] == ["CNBC", "Reuters"]
    assert out[1]["topics"] == [] and out[1]["detail"] == {} and out[1]["title"] == ""
    assert out[2]["tickers"] == [] and out[2]["sources"] == ["Yahoo"]


def test_source_falls_back_when_sources_missing():
    it = _it(1)
    del it["sources"]
    assert nv.rows({"items": [it]}, now=NOW)[0]["sources"] == ["MarketWatch"]
    it["source"] = None
    assert nv.rows({"items": [it]}, now=NOW)[0]["sources"] == []


def test_a_symbol_that_does_not_clean_matches_nothing():
    rows = nv.rows({"items": [_it(1, tickers=["NVDA"]), _it(2)]}, now=NOW)
    assert nv.filter_rows(rows, sources=None, symbol="not a ticker!") == []
    assert nv.filter_rows(rows, sources=None, symbol="  nvda ") == [rows[0]]
    assert nv.for_symbol({"items": [_it(1, tickers=["NVDA"])]}, "$$$$$$$$$$", now=NOW) == []


def test_symbol_match_cleans_the_row_side_too():
    raw_rows = [{"id": "a", "tickers": ["nvda"], "sources": ["X"]},
                {"id": "b", "tickers": "NVDA", "sources": ["X"]}]
    assert [r["id"] for r in nv.filter_rows(raw_rows, sources=None, symbol="NVDA")] == ["a"]


def test_for_symbol_default_limit_and_desk_limit():
    assert nv.DESK_LIMIT == 5 and nv.SYMBOL_LIMIT == 8
    items = [_it(i, tickers=["NVDA"]) for i in range(12)]
    assert len(nv.for_symbol({"items": items}, "NVDA", now=NOW)) == nv.SYMBOL_LIMIT
    assert len(nv.for_symbol({"items": items}, "NVDA", now=NOW, limit=3)) == 3


FORM4_DETAIL = {
    "symbol": "LEN", "company": "Lennar Corp", "insider": "Eisner Jonathan",
    "insiders": ["Eisner Jonathan"], "relationship": "Director",
    "groups": [{"code": "P", "security": "Class A Common Stock", "shares": 184_336.0,
                "price": 82.2, "value": 15_153_117.0, "date": "2026-09-23"}] * 9,
    "total_value": 136_382_789.45, "transaction_date": "2026-09-23",
}


def test_detail_line_for_a_form4_purchase():
    it = _it(1, tickers=["LEN"], source="SEC")
    it.update(kind="edgar_form4", detail=FORM4_DETAIL)
    r = nv.rows({"items": [it]}, now=NOW)[0]
    assert nv.detail_line(r) == "9 purchases · $136.4M · 2026-09-23"


def test_detail_line_single_purchase_and_missing_parts():
    d = {"groups": [{"value": 5000.0}], "total_value": 5000.0}
    assert nv.detail_line({"kind": "edgar_form4", "detail": d}) == "1 purchase · $5K"
    d = {"groups": "junk", "total_value": float("nan"), "transaction_date": "2026-09-23"}
    assert nv.detail_line({"kind": "edgar_form4", "detail": d}) == "2026-09-23"
    d = {"groups": [], "total_value": True, "transaction_date": 7}
    assert nv.detail_line({"kind": "edgar_form4", "detail": d}) == ""


def test_detail_line_for_a_filing():
    r = {"kind": "edgar_filings", "detail": {"form": "S-3", "cik": "1", "accession": "a"}}
    assert nv.detail_line(r) == "Form S-3"
    assert nv.detail_line({"kind": "edgar_filings", "detail": {"form": ""}}) == ""


def test_detail_line_is_empty_for_plain_rows_and_junk():
    assert nv.detail_line(nv.rows({"items": [_it(1)]}, now=NOW)[0]) == ""
    for junk in (None, "x", {}, {"kind": "edgar_form4"}, {"kind": "edgar_form4", "detail": "x"}):
        assert nv.detail_line(junk) == ""


# ---- review fixes (bea3639) -----------------------------------------------

EXTREME_DATES = ("0001-01-01T00:00:00", "0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00")


def test_extreme_published_at_is_undated_not_a_crash():
    for extreme in EXTREME_DATES:
        it = _it(1, tickers=["NVDA"])
        it["published_at"] = extreme
        payload = {"items": [it, _it(2, tickers=["NVDA"])]}
        out = nv.rows(payload, now=NOW)
        assert [r["id"] for r in out] == ["i1", "i2"], extreme
        r = out[0]
        assert r["time"] == "" and r["day"] == "" and r["age_min"] is None, extreme
        assert r["when"] == "" and r["today"] is False, extreme
        assert nv.trending(payload, now=NOW, window_h=6) == [("NVDA", 1)], extreme
        assert [r["id"] for r in nv.for_symbol(payload, "NVDA", now=NOW)] == ["i1", "i2"], extreme


def test_extreme_first_seen_or_since_never_counts_and_never_raises():
    for extreme in EXTREME_DATES:
        it = _it(1)
        it["first_seen"] = extreme
        assert nv.unseen({"items": [it]}, since="2026-09-26T00:00:00+00:00") == 0, extreme
        assert nv.unseen({"items": [_it(2)]}, since=extreme) == 0, extreme


def test_a_naive_now_is_central_time():
    # 20:00 on the Central clock is 01:00 UTC the next day - NOW.
    naive_now = dt.datetime(2026, 9, 25, 20, 0)
    it = _it(1)
    it["published_at"] = "2026-09-26T00:30:00+00:00"
    r = nv.rows({"items": [it]}, now=naive_now)[0]
    assert r["age_min"] == 30
    assert r["today"] is True and r["when"] == "7:30 PM"


def test_today_item_shows_the_time_alone():
    r = nv.rows({"items": [_it(1)]}, now=NOW)[0]
    assert r["today"] is True and r["when"] == "7:30 PM" and r["day"] == "Sep 25"


def test_an_item_three_hours_old_can_be_yesterday_in_ct():
    now = dt.datetime(2026, 9, 26, 6, 0, tzinfo=dt.timezone.utc)   # 01:00 CT Sep 26
    it = _it(1)
    it["published_at"] = "2026-09-26T03:00:00+00:00"                # 22:00 CT Sep 25
    r = nv.rows({"items": [it]}, now=now)[0]
    assert r["age_min"] == 180
    assert r["today"] is False
    assert r["time"] == "10:00 PM" and r["day"] == "Sep 25"
    assert r["when"] == "Sep 25 10:00 PM"


def test_day_is_not_zero_padded():
    it = _it(1)
    it["published_at"] = "2026-09-05T13:05:00+00:00"                # 08:05 CT Sep 5
    r = nv.rows({"items": [it]}, now=NOW)[0]
    assert r["day"] == "Sep 5" and r["today"] is False and r["when"] == "Sep 5 8:05 AM"


def test_undated_row_is_not_today_and_has_no_when():
    it = _it(1)
    it["published_at"] = "bogus"
    r = nv.rows({"items": [it]}, now=NOW)[0]
    assert r["today"] is False and r["when"] == ""


def test_money_steps_up_a_unit_when_rounding_reaches_1000():
    assert nv._money(999_600) == "$1.0M"
    assert nv._money(999_960_000) == "$1.0B"
    assert nv._money(-999_600) == "-$1.0M"
    assert nv._money(999_400) == "$999K"
    assert nv._money(999_940_000) == "$999.9M"
    assert nv._money(136_382_789.45) == "$136.4M"
    assert nv._money(5000.0) == "$5K"
    assert nv._money(2.5e12) == "$2500.0B"                           # no unit above B


def test_detail_line_total_just_under_a_million_reads_one_million():
    d = {"groups": [{"value": 1.0}], "total_value": 999_600.0}
    assert nv.detail_line({"kind": "edgar_form4", "detail": d}) == "1 purchase · $1.0M"


def test_a_bare_string_source_is_one_source_name():
    items = [_it(1), _it(2, source="CNBC"), _it(3, source="C")]
    rows = nv.rows({"items": items}, now=NOW)
    assert [r["id"] for r in nv.filter_rows(rows, sources="CNBC", symbol=None)] == ["i2"]


def test_news_view_imports_only_stdlib_and_shared_symbols():
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages" / "news_view.py").read_text(
        encoding="utf-8")
    found = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            found.add(node.module)
    bad = {m for m in found
           if m != "shared.symbols" and m.split(".")[0] not in sys.stdlib_module_names}
    assert not bad, bad
    assert "shared.symbols" in found


def test_safe_href_passes_only_an_absolute_web_address():
    """``ui.link`` escapes its text, not its href: a feed's ``javascript:`` or
    ``data:`` URL would run on click. Only http(s) with a host is a link."""
    assert nv.safe_href(" https://a.example/x?y=1 ") == "https://a.example/x?y=1"
    assert nv.safe_href("HTTP://A.EXAMPLE") == "HTTP://A.EXAMPLE"
    for bad in ("javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x",
                "https://", "http:///path", "//a.example/x", "/relative", "ftp://a.b/c",
                "vbscript:x", "", "   ", None, 7, b"https://a.b", "http://[::1"):
        assert nv.safe_href(bad) is None, bad


# ---- review fixes (b013d39) -----------------------------------------------


def test_today_is_the_central_date_not_the_eastern_one():
    """23:30 CT is 00:30 ET the next day. The item and ``now`` share a Central
    date, so the row is today and shows its time alone."""
    now = dt.datetime(2026, 9, 26, 4, 45, tzinfo=dt.timezone.utc)   # 23:45 CT Sep 25
    it = _it(1)
    it["published_at"] = "2026-09-26T04:30:00+00:00"                # 23:30 CT Sep 25
    r = nv.rows({"items": [it]}, now=now)[0]
    assert r["today"] is True and r["when"] == "11:30 PM" and r["day"] == "Sep 25"
    # And the Central midnight, not the Eastern one, starts a new day.
    now = dt.datetime(2026, 9, 26, 5, 15, tzinfo=dt.timezone.utc)   # 00:15 CT Sep 26
    r = nv.rows({"items": [it]}, now=now)[0]
    assert r["today"] is False and r["when"] == "Sep 25 11:30 PM"


def test_trending_skips_yahoo_per_ticker_items():
    """A Yahoo per-ticker item carries the ticker it was FETCHED for, so counting
    it would make every watchlist name trend on volume of polling, not of news.
    Trending counts tickers named in the general feeds only."""
    yahoo = [_it(i, tickers=["AAPL"]) for i in range(5)]
    for it in yahoo:
        it["kind"] = "yahoo_ticker"
    items = yahoo + [_it(10, tickers=["NVDA"]), _it(11, tickers=["NVDA", "AAPL"])]
    assert nv.trending({"items": items}, now=NOW, window_h=6) == [("NVDA", 2), ("AAPL", 1)]
    # The rows themselves are untouched: a Yahoo item still lists and filters.
    rows = nv.rows({"items": items}, now=NOW)
    assert len(nv.filter_rows(rows, sources=None, symbol="AAPL")) == 6
