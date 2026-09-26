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


def test_rows_are_shaped_for_display_in_et():
    r = nv.rows({"items": [_it(1, tickers=["NVDA"])]}, now=NOW)[0]
    assert r["time"] == "8:30 PM"          # 01:00 UTC → 21:00 ET
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
    it["published_at"] = "2026-09-25T13:05:00+00:00"      # 09:05 ET
    r = nv.rows({"items": [it]}, now=NOW)[0]
    assert r["time"] == "9:05 AM" and r["day"] == "Sep 25"


def test_naive_published_at_is_read_as_utc():
    it = _it(1)
    it["published_at"] = "2026-09-26T00:30:00"
    r = nv.rows({"items": [it]}, now=NOW)[0]
    assert r["time"] == "8:30 PM"
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
