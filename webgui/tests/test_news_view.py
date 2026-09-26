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


# ---- v2: impact, the SEC panel and calendar tiles (plan Task 10) -----------

import pytest  # noqa: E402

ITEM = _it(1, tickers=["NVDA"])


def test_rows_carry_the_impact_band_and_reasons():
    r = nv.rows({"items": [{**ITEM, "impact": {"band": "high", "score": 7,
                                              "reasons": ["kw:tier1:FOMC"]}}]}, now=NOW)[0]
    assert (r["band"], r["reasons"]) == ("high", ["kw:tier1:FOMC"])


def test_a_missing_or_junk_impact_reads_as_no_band_never_low():
    for imp in (None, {}, {"band": "extreme"}, "high"):
        assert nv.rows({"items": [{**ITEM, "impact": imp}]}, now=NOW)[0]["band"] is None


def test_junk_reasons_are_cleaned_to_strings():
    imp = {"band": "med", "reasons": ["a", 3, None, "b"]}
    assert nv.rows({"items": [{**ITEM, "impact": imp}]}, now=NOW)[0]["reasons"] == ["a", "b"]
    imp = {"band": "med", "reasons": "a"}
    assert nv.rows({"items": [{**ITEM, "impact": imp}]}, now=NOW)[0]["reasons"] == []


def test_min_band_filter():
    rs = [{"band": "high", "tickers": []}, {"band": "med", "tickers": []},
          {"band": "low", "tickers": []}, {"band": None, "tickers": []}]
    assert [r["band"] for r in nv.filter_rows(rs, sources=None, symbol=None, min_band="med")] == ["high", "med"]
    assert [r["band"] for r in nv.filter_rows(rs, sources=None, symbol=None, min_band="high")] == ["high"]
    assert len(nv.filter_rows(rs, sources=None, symbol=None, min_band=None)) == 4


def test_band_classes_are_a_fixed_finite_map():
    assert set(nv.BAND_CLASSES) == {"high", "med", "low", None}
    assert all("[" not in c or "#" in c for c in nv.BAND_CLASSES.values())   # no runtime values
    assert nv.BAND_LETTER == {"high": "H", "med": "M", "low": "L"}


def test_view_names():
    assert (nv.VIEW_SEC, nv.VIEW_SEC_PUBLIC) == ("news:sec", "news:sec_public")
    assert (nv.VIEW_CAL, nv.VIEW_CAL_PUBLIC, nv.VIEW_CAL_STATUS) == (
        "news:calendar", "news:calendar_public", "news:calendar_status")


FORM4_ITEM = {**_it(9, tickers=["ACME"], source="SEC"), "kind": "edgar_form4",
              "detail": {**FORM4_DETAIL, "symbol": "ACME"}}


def test_sec_rows_join_headline_and_detail():
    r = nv.sec_rows({"items": [FORM4_ITEM]}, now=NOW)[0]
    assert r["symbol"] == "ACME" and r["details"] == "9 purchases · $136.4M · 2026-09-23"


def test_sec_row_symbol_falls_back_to_the_filer_and_else_empty():
    it = {**FORM4_ITEM, "tickers": []}
    assert nv.sec_rows({"items": [it]}, now=NOW)[0]["symbol"] == "ACME"
    it = {**FORM4_ITEM, "tickers": [], "detail": {"symbol": "not a symbol!"}}
    assert nv.sec_rows({"items": [it]}, now=NOW)[0]["symbol"] == ""
    assert nv.sec_rows(None, now=NOW) == []


RELEASE_AT = dt.datetime(2026, 10, 14, 12, 30, tzinfo=dt.timezone.utc)
PREV_RELEASE = "2026-09-11T12:30:00+00:00"
CALCFG = {"release_watch_min": 60, "actual_fresh_h": 24}


def _obs(date, value, first_seen, bootstrap=False):
    return {"obs_date": date, "value": value, "first_seen": first_seen, "bootstrap": bootstrap}


IND = {"key": "cpi", "label": "CPI", "tile": "CPI", "unit": "pct_mom",
       "next_release_at": RELEASE_AT.isoformat(), "next_date": "2026-10-14",
       "last_release_at": PREV_RELEASE,
       "latest": _obs("2026-08-01", 0.2, "2026-09-11T12:40:00+00:00"),
       "prior": _obs("2026-07-01", 0.1, "2026-08-12T12:40:00+00:00")}
IND_WITH_NEW_OBS = {**IND, "last_release_at": RELEASE_AT.isoformat(),
                    "next_release_at": "2026-11-13T13:30:00+00:00", "next_date": "2026-11-13",
                    "latest": _obs("2026-09-01", 0.4, (RELEASE_AT + dt.timedelta(minutes=8)).isoformat()),
                    "prior": IND["latest"]}
IND_BOOTSTRAP = {**IND_WITH_NEW_OBS,
                 "latest": _obs("2026-09-01", 0.4, (RELEASE_AT + dt.timedelta(minutes=1)).isoformat(),
                                bootstrap=True)}
IND_NO_NEXT = {**IND, "next_release_at": None, "next_date": None}


@pytest.mark.parametrize("unit,value,text", [("pct_mom", 0.4, "+0.4% m/m"), ("change_k", 162.0, "+162K"),
    ("level_pct", 4.1, "4.1%"), ("level_k", 197.0, "197K"), ("pct_saar", 1.5, "1.5% SAAR"),
    ("pct_mom", None, "—"), ("pct_mom", float("nan"), "—")])
def test_indicator_values(unit, value, text):
    assert nv.fmt_indicator(value, unit) == text


def test_indicator_values_signs_and_junk():
    assert nv.fmt_indicator(-0.1, "pct_mom") == "-0.1% m/m"
    assert nv.fmt_indicator(0.0, "pct_mom") == "0.0% m/m"
    assert nv.fmt_indicator(-33.0, "change_k") == "-33K"
    assert nv.fmt_indicator(True, "pct_mom") == "—"
    assert nv.fmt_indicator("0.4", "pct_mom") == "—"
    assert nv.fmt_indicator(float("inf"), "level_k") == "—"
    assert nv.fmt_indicator(1.25, "mystery") == "1.2"   # an unknown unit prints the bare number


def test_actual_before_and_after_release():
    before = nv.indicator_state(IND, now=RELEASE_AT - dt.timedelta(minutes=5), cfg=CALCFG)
    assert (before["actual"], before["prior"]) == ("—", "+0.2% m/m")
    after = nv.indicator_state(IND_WITH_NEW_OBS, now=RELEASE_AT + dt.timedelta(minutes=9), cfg=CALCFG)
    assert (after["actual"], after["prior"], after["state"]) == ("+0.4% m/m", "+0.2% m/m", "released")


def test_released_ends_after_actual_fresh_h():
    later = nv.indicator_state(IND_WITH_NEW_OBS, now=RELEASE_AT + dt.timedelta(hours=25), cfg=CALCFG)
    assert (later["actual"], later["prior"], later["state"]) == ("—", "+0.4% m/m", "upcoming")


def test_a_release_passed_with_no_new_value_is_awaiting():
    ind = {**IND, "last_release_at": RELEASE_AT.isoformat(), "next_release_at": None}
    s = nv.indicator_state(ind, now=RELEASE_AT + dt.timedelta(minutes=3), cfg=CALCFG)
    assert (s["state"], s["actual"], s["prior"]) == ("awaiting", "—", "+0.2% m/m")


def test_a_stale_payload_whose_next_release_has_passed_is_awaiting():
    # the payload predates the release; the page's clock has moved past it
    s = nv.indicator_state(IND, now=RELEASE_AT + dt.timedelta(minutes=2), cfg=CALCFG)
    assert (s["state"], s["actual"]) == ("awaiting", "—")
    assert s["next"] == "Next date not yet published"


def test_bootstrap_value_inside_the_watch_window_is_awaiting_not_actual():
    s = nv.indicator_state(IND_BOOTSTRAP, now=RELEASE_AT + dt.timedelta(minutes=3), cfg=CALCFG)
    assert s["state"] == "awaiting" and s["actual"] == "—"


def test_bootstrap_value_after_the_watch_window_is_the_actual():
    s = nv.indicator_state(IND_BOOTSTRAP, now=RELEASE_AT + dt.timedelta(minutes=61), cfg=CALCFG)
    assert (s["state"], s["actual"], s["prior"]) == ("released", "+0.4% m/m", "+0.2% m/m")


def test_no_future_release_says_so():
    assert nv.indicator_state(IND_NO_NEXT, now=NOW, cfg=CALCFG)["next"] == "Next date not yet published"


def test_next_release_renders_central_or_date_only():
    s = nv.indicator_state(IND, now=NOW, cfg=CALCFG)
    assert s["next"] == "Wed Oct 14 · 7:30 AM CT"
    s = nv.indicator_state({**IND, "next_release_at": None, "next_date": "2026-10-05"}, now=NOW, cfg=CALCFG)
    assert s["next"] == "Mon Oct 5"


def test_a_released_indicator_says_when_it_was_released():
    s = nv.indicator_state(IND_WITH_NEW_OBS, now=RELEASE_AT + dt.timedelta(minutes=9), cfg=CALCFG)
    assert s["state"] == "released" and s["status"] == "Released 7:30 AM CT"


def test_a_release_on_an_earlier_day_names_the_day():
    # 7:30 AM CT on Wed Oct 14, read at 6:00 AM CT the next morning
    s = nv.indicator_state(IND_WITH_NEW_OBS, now=RELEASE_AT + dt.timedelta(hours=22, minutes=30),
                           cfg=CALCFG)
    assert s["state"] == "released" and s["status"] == "Released Wed Oct 14 · 7:30 AM CT"


def test_awaiting_and_upcoming_status_lines():
    ind = {**IND, "last_release_at": RELEASE_AT.isoformat(), "next_release_at": None}
    s = nv.indicator_state(ind, now=RELEASE_AT + dt.timedelta(minutes=3), cfg=CALCFG)
    assert s["status"] == "Awaiting the release"
    assert nv.indicator_state(IND, now=NOW, cfg=CALCFG)["status"] == ""


def test_indicator_state_tolerates_junk():
    for ind in (None, {}, {"latest": "x", "prior": 5, "last_release_at": "bogus", "unit": 3},
                {**IND, "last_release_at": "9999-12-31T23:59:59-12:00"}):
        s = nv.indicator_state(ind, now=NOW, cfg=None)
        assert s["actual"] == "—" and isinstance(s["prior"], str) and isinstance(s["next"], str)
        assert isinstance(s["status"], str)


EVENT = {"id": "fed:1", "title": "FOMC statement", "at": "2026-10-28T18:00:00+00:00",
         "date": "2026-10-28", "source": "fed"}
DATE_ONLY_EVENT = {"id": "fed:2", "title": "Beige Book", "at": None, "date": "2026-10-05",
                   "source": "fed"}
DIVIDEND = {"symbol": "JPM", "ex_date": "2026-10-05", "pay_date": "2026-10-31", "amount": 1.4}
IPO = {"symbol": "ACME", "company": "Acme Corp", "date": "2026-10-02",
       "price_range": "40.00-44.00", "offer_usd": 2_530_000_000, "status": "upcoming"}
CAL_PAYLOAD = {"events": [EVENT, DATE_ONLY_EVENT], "data": [IND], "dividends": [DIVIDEND],
               "ipos": [IPO], "sources": {"fed": "ok", "bls": "ok"}, "settings": CALCFG}


def test_calendar_group_headers_and_order():
    gs = nv.calendar_groups(CAL_PAYLOAD, now=NOW)
    assert [g["title"] for g in gs] == ["Economic news/Calendar", "Dividend / IPO",
                                       "Economic data (CPI, PPI etc)"]


def test_calendar_times_render_central():
    t = nv.calendar_groups(CAL_PAYLOAD, now=NOW)[0]["tiles"][0]
    assert t["when"] == "Wed Oct 28 · 1:00 PM CT"          # 2:00 p.m. ET
    assert t["title"] == "FOMC statement"


def test_a_date_only_event_prints_no_time():
    t = nv.calendar_groups(CAL_PAYLOAD, now=NOW)[0]["tiles"][1]
    assert t["when"] == "Mon Oct 5"


def test_dividend_and_ipo_tiles():
    tiles = nv.calendar_groups(CAL_PAYLOAD, now=NOW)[1]["tiles"]
    div, ipo = tiles
    assert div["title"] == "JPM dividend" and div["when"] == "Ex-div Mon Oct 5"
    assert div["lines"] == ["$1.40 a share", "Pays Sat Oct 31"]
    assert ipo["title"] == "ACME · Acme Corp IPO" and ipo["when"] == "Fri Oct 2"
    assert ipo["lines"] == ["$40.00-44.00", "$2.5B offer"]


def test_ipo_with_no_ticker_and_no_money_prints_no_zero():
    t = nv.calendar_groups({"ipos": [{"company": "Blank Co", "date": "2026-10-02",
                                      "offer_usd": 0}]}, now=NOW)[1]["tiles"][0]
    assert t["title"] == "Blank Co IPO" and t["lines"] == []


def test_data_tiles_group_indicators_by_tile_in_order():
    core = {**IND, "key": "core_cpi", "label": "Core CPI"}
    ppi = {**IND, "key": "ppi", "label": "PPI", "tile": "PPI"}
    g = nv.calendar_groups({"data": [IND, ppi, core], "settings": CALCFG}, now=NOW)[2]
    assert [t["title"] for t in g["tiles"]] == ["CPI", "PPI"]
    cpi = g["tiles"][0]
    assert [i["label"] for i in cpi["indicators"]] == ["CPI", "Core CPI"]
    assert cpi["when"] == "Wed Oct 14 · 7:30 AM CT"
    assert cpi["indicators"][0]["prior"] == "+0.2% m/m"


def test_calendar_reads_settings_from_the_payload():
    # a 5-minute watch window: at +9 min a bootstrap value is attributed
    p = {"data": [IND_BOOTSTRAP], "settings": {"release_watch_min": 5, "actual_fresh_h": 24}}
    ind = nv.calendar_groups(p, now=RELEASE_AT + dt.timedelta(minutes=9))[2]["tiles"][0]["indicators"][0]
    assert ind["state"] == "released"
    p = {"data": [IND_BOOTSTRAP], "settings": "junk"}   # defaults: 60-minute window
    ind = nv.calendar_groups(p, now=RELEASE_AT + dt.timedelta(minutes=9))[2]["tiles"][0]["indicators"][0]
    assert ind["state"] == "awaiting"


def test_stale_and_never_sources_grey_their_group():
    gs = nv.calendar_groups({**CAL_PAYLOAD, "sources": {"fed": "stale"}}, now=NOW)
    assert gs[0]["note"] == "Source unavailable — showing the last good reading"
    assert gs[1]["note"] is None and gs[2]["note"] is None
    gs = nv.calendar_groups({"sources": {"fed": "never", "bls": "never", "bea": "off"}}, now=NOW)
    assert gs[0]["note"] == "Not published yet"
    gs = nv.calendar_groups({**CAL_PAYLOAD, "sources": {"fed": "stale", "bls": "ok"}}, now=NOW)
    assert gs[0]["note"] is None                     # one good source: not the whole group


def test_empty_groups_say_so_and_junk_never_raises():
    for junk in (None, "x", {}, {"events": 5, "data": [1, None], "dividends": {"a": 1},
                                  "ipos": ["x"], "sources": [1], "settings": 3}):
        gs = nv.calendar_groups(junk, now=NOW)
        assert [len(g["tiles"]) for g in gs] == [0, 0, 0]
        assert all(g["empty"] for g in gs)
    assert nv.calendar_groups(CAL_PAYLOAD, now=NOW)[0]["empty"] is None


def test_a_junk_event_time_falls_back_to_its_date():
    ev = {**EVENT, "at": "garbage"}
    assert nv.calendar_groups({"events": [ev]}, now=NOW)[0]["tiles"][0]["when"] == "Wed Oct 28"
    ev = {"title": "No date at all"}
    assert nv.calendar_groups({"events": [ev]}, now=NOW)[0]["tiles"][0]["when"] == ""


# ---- review fixes: junk settings, bootstrap fail-closed, blank and past tiles -

@pytest.mark.parametrize("settings", [
    {"actual_fresh_h": 1e12}, {"release_watch_min": 1e308},
    {"actual_fresh_h": 8761}, {"release_watch_min": 10081},
    {"actual_fresh_h": 0}, {"release_watch_min": -5},
    {"actual_fresh_h": float("inf")}, {"release_watch_min": float("nan")},
])
def test_junk_settings_fall_back_to_the_defaults_and_never_raise(settings):
    now = RELEASE_AT + dt.timedelta(minutes=9)
    s = nv.indicator_state(IND_BOOTSTRAP, now=now, cfg=settings)
    assert s["state"] == "awaiting"            # the default 60-minute watch window
    p = {"data": [IND_BOOTSTRAP], "settings": settings}
    ind = nv.calendar_groups(p, now=now)[2]["tiles"][0]["indicators"][0]
    assert ind["state"] == "awaiting"
    later = nv.indicator_state(IND_WITH_NEW_OBS, now=RELEASE_AT + dt.timedelta(hours=25), cfg=settings)
    assert later["state"] == "upcoming"         # the default 24-hour freshness


def test_settings_at_the_bounds_are_honoured():
    now = RELEASE_AT + dt.timedelta(hours=100)
    s = nv.indicator_state(IND_WITH_NEW_OBS, now=now, cfg={"actual_fresh_h": 8760})
    assert s["state"] == "released"
    s = nv.indicator_state(IND_BOOTSTRAP, now=RELEASE_AT + dt.timedelta(minutes=9),
                           cfg={"release_watch_min": 10080, "actual_fresh_h": 24})
    assert s["state"] == "awaiting"


@pytest.mark.parametrize("boot", ["missing", "true", "false", None, 1.0, [1]])
def test_an_unclear_bootstrap_flag_is_not_a_fresh_actual(boot):
    latest = dict(IND_BOOTSTRAP["latest"])
    if boot == "missing":
        latest.pop("bootstrap")
    else:
        latest["bootstrap"] = boot
    ind = {**IND_BOOTSTRAP, "latest": latest}
    s = nv.indicator_state(ind, now=RELEASE_AT + dt.timedelta(minutes=3), cfg=CALCFG)
    assert s["state"] == "awaiting" and s["actual"] == "—"


@pytest.mark.parametrize("boot", [False, 0])
def test_only_an_explicit_false_bootstrap_is_a_fresh_actual(boot):
    latest = {**IND_BOOTSTRAP["latest"], "bootstrap": boot}
    ind = {**IND_BOOTSTRAP, "latest": latest}
    s = nv.indicator_state(ind, now=RELEASE_AT + dt.timedelta(minutes=3), cfg=CALCFG)
    assert (s["state"], s["actual"]) == ("released", "+0.4% m/m")


def test_blank_tiles_are_skipped():
    p = {"events": [{**EVENT, "title": ""}, {**EVENT, "title": None}, {**EVENT, "title": "  "},
                    EVENT],
         "dividends": [{**DIVIDEND, "symbol": None}, {**DIVIDEND, "symbol": "$$$$$$$$$$"},
                       {**DIVIDEND, "ex_date": None}, {**DIVIDEND, "ex_date": "garbage"},
                       DIVIDEND],
         "ipos": [{"date": "2026-10-02"}, {"symbol": "", "company": " ", "date": "2026-10-02"},
                  {**IPO, "date": None}, {**IPO, "date": "garbage"}, IPO],
         "data": [{**IND, "tile": "", "label": ""}, {**IND, "tile": None, "label": None}, IND]}
    gs = nv.calendar_groups(p, now=NOW)
    assert [t["title"] for t in gs[0]["tiles"]] == ["FOMC statement"]
    assert [t["title"] for t in gs[1]["tiles"]] == ["JPM dividend", "ACME · Acme Corp IPO"]
    assert [t["title"] for t in gs[2]["tiles"]] == ["CPI"]


def test_an_ipo_with_only_a_symbol_is_kept():
    t = nv.calendar_groups({"ipos": [{"symbol": "ACME", "date": "2026-10-02"}]}, now=NOW)[1]["tiles"]
    assert [x["title"] for x in t] == ["ACME IPO"]


def test_past_calendar_items_are_dropped():
    # NOW is Fri Sep 25, 20:00 CT
    past_at = {**EVENT, "title": "Past", "at": "2026-09-26T00:30:00+00:00", "date": "2026-09-25"}
    later_today = {**EVENT, "title": "Later", "at": "2026-09-26T01:30:00+00:00", "date": "2026-09-25"}
    past_date = {**DATE_ONLY_EVENT, "title": "Yesterday", "date": "2026-09-24"}
    today_date = {**DATE_ONLY_EVENT, "title": "Today", "date": "2026-09-25"}
    p = {"events": [past_at, later_today, past_date, today_date],
         "dividends": [{**DIVIDEND, "symbol": "OLD", "ex_date": "2026-09-24"},
                       {**DIVIDEND, "symbol": "NOW", "ex_date": "2026-09-25"}],
         "ipos": [{**IPO, "symbol": "GONE", "date": "2026-09-24"},
                  {**IPO, "symbol": "TODAY", "date": "2026-09-25"}]}
    gs = nv.calendar_groups(p, now=NOW)
    assert [t["title"] for t in gs[0]["tiles"]] == ["Later", "Today"]
    # Dividends and IPOs are NOT dropped by date here: the producer
    # (services/news_svc/econ.py) owns their window - [calendar.ipo] and
    # [calendar.dividends] lookback_days - so a priced IPO stays on the page for
    # the week it is meant to. Only past EVENTS are dropped by ``now``.
    assert [t["title"] for t in gs[1]["tiles"]] == [
        "OLD dividend", "NOW dividend", "GONE · Acme Corp IPO", "TODAY · Acme Corp IPO"]


def test_past_items_use_the_central_date_of_a_naive_now():
    naive_now = dt.datetime(2026, 9, 25, 20, 0)      # Central wall clock == NOW
    p = {"events": [{**DATE_ONLY_EVENT, "date": "2026-09-24"}],
         "dividends": [{**DIVIDEND, "ex_date": "2026-09-25"}]}
    gs = nv.calendar_groups(p, now=naive_now)
    # the past EVENT is dropped; the dividend is kept (the producer owns its window)
    assert gs[0]["tiles"] == [] and len(gs[1]["tiles"]) == 1


# ── review round: readable reasons, the producer-owned calendar window ──────

def test_reason_text_maps_codes_to_reader_phrases():
    assert nv.reason_text(["kw:tier1:FOMC"]) == "mentions FOMC"
    assert nv.reason_text(["source:Reuters", "sources:3"]) == \
        "from Reuters, reported by 3 sources"
    assert nv.reason_text(["watchlist"]) == "a followed ticker"
    assert nv.reason_text(["form4:$136.4M", "officer"]) == \
        "insider buy of $136.4M, bought by an officer"
    assert nv.reason_text(["filing:S-3"]) == "S-3 filing"
    assert nv.reason_text(["stale"]) == "older news, so shown one level lower"


def test_reason_text_shows_unknown_codes_as_is_and_skips_junk():
    assert nv.reason_text(["mystery:7", "kw:tier1:", 5, None, ""]) == "mystery:7, kw:tier1:"
    assert nv.reason_text(None) == "" and nv.reason_text("stale") == ""
    assert nv.reason_text([]) == ""


def test_a_past_priced_ipo_reads_priced_on_its_day():
    p = {"ipos": [{"symbol": "NEWCO", "company": "NewCo", "date": "2026-09-21",
                   "price": 18.0, "offer_usd": 400_000_000}]}
    (t,) = nv.calendar_groups(p, now=NOW)[1]["tiles"]
    assert t["when"] == "Priced Mon Sep 21"
    assert t["lines"] == ["$18.00 a share", "$400.0M offer"]
    # an upcoming deal keeps its date and range
    (u,) = nv.calendar_groups({"ipos": [IPO]}, now=NOW)[1]["tiles"]
    assert u["when"] == "Fri Oct 2" and u["lines"][0] == "$40.00-44.00"


def test_a_past_dividend_is_kept_with_its_ex_date():
    (t,) = nv.calendar_groups({"dividends": [{**DIVIDEND, "ex_date": "2026-09-21"}]},
                              now=NOW)[1]["tiles"]
    assert t["when"] == "Ex-div Mon Sep 21"


def test_sec_href_takes_only_https_sec_gov():
    ok = "https://www.sec.gov/Archives/edgar/data/1/x.htm"
    assert nv.sec_href(ok) == ok
    assert nv.sec_href("https://sec.gov/x") == "https://sec.gov/x"
    for bad in ("http://www.sec.gov/x", "https://example.com/x",
                "https://sec.gov.evil.com/x", "https://notsec.gov/x",
                "javascript:alert(1)", None, 7, ""):
        assert nv.sec_href(bad) is None, bad


# ── high-impact tiles (2026-09-26) ───────────────────────────────────────────

def test_a_tile_is_high_only_when_the_producer_said_true():
    p = {"events": [{**EVENT, "high": True}, {**DATE_ONLY_EVENT, "high": False}],
         "dividends": [{**DIVIDEND, "high": True}], "ipos": [IPO],
         "data": [{**IND, "high": True}], "settings": CALCFG}
    ev, other, data = nv.calendar_groups(p, now=NOW)
    assert [t["high"] for t in ev["tiles"]] == [True, False]
    assert [t["high"] for t in other["tiles"]] == [True, False]   # the producer decides
    assert data["tiles"][0]["high"] is True


def test_a_missing_or_non_bool_high_is_not_high():
    for bad in (None, 1, "true", [True]):
        p = {"events": [{**EVENT, "high": bad}], "data": [{**IND, "high": bad}],
             "settings": CALCFG}
        ev, _, data = nv.calendar_groups(p, now=NOW)
        assert ev["tiles"][0]["high"] is False, bad
        assert data["tiles"][0]["high"] is False, bad
    ev, other, data = nv.calendar_groups(CAL_PAYLOAD, now=NOW)       # no flag at all
    assert not any(t["high"] for g in (ev, other, data) for t in g["tiles"])


def test_a_data_tile_is_high_when_any_of_its_indicators_is():
    core = {**IND, "key": "core_cpi", "label": "Core CPI", "high": False}
    p = {"data": [{**IND, "high": False}, {**core, "high": True}], "settings": CALCFG}
    (tile,) = nv.calendar_groups(p, now=NOW)[2]["tiles"]
    assert len(tile["indicators"]) == 2 and tile["high"] is True


# ── the 2026-09-26 redesign: filters, counts, SEC display, next-up, agenda ──

def test_rows_carry_a_compact_24_hour_stamp():
    today = _it(1)                                           # 00:30 UTC = 19:30 CT
    older = {**_it(2), "published_at": "2026-09-22T21:22:00+00:00"}   # Tue 16:22 CT
    oldest = {**_it(3), "published_at": "2026-09-10T15:00:00+00:00"}
    none = {**_it(4), "published_at": "junk"}
    rs = nv.rows({"items": [today, older, oldest, none]}, now=NOW)
    assert [r["stamp"] for r in rs] == ["19:30", "Tue 16:22", "Sep 10", ""]
    morning = {**_it(5), "published_at": "2026-09-25T14:05:00+00:00"}  # 9:05 CT
    assert nv.rows({"items": [morning]}, now=NOW)[0]["stamp"] == "9:05"


def test_the_query_matches_the_headline_or_a_ticker_case_insensitively():
    rs = [{"title": "Fed holds  RATES", "tickers": ["SPY"]},
          {"title": "Chip rally", "tickers": ["NVDA", "AMD"]},
          {"title": "Oil edges up", "tickers": []}]
    q = lambda s: [r["title"] for r in nv.filter_rows(rs, sources=None, symbol=None, query=s)]
    assert q("holds rates") == ["Fed holds  RATES"]
    assert q("nvd") == ["Chip rally"]
    assert q("  ") == q(None) == q("") == [r["title"] for r in rs]
    assert q("zzz") == []


def test_the_band_picker_keeps_exactly_one_band():
    rs = [{"band": "high", "tickers": []}, {"band": "med", "tickers": []},
          {"band": "low", "tickers": []}, {"band": None, "tickers": []}]
    for b in ("high", "med", "low"):
        assert [r["band"] for r in nv.filter_rows(rs, sources=None, symbol=None, band=b)] == [b]
    for b in (None, "all", "extreme", 3):
        assert len(nv.filter_rows(rs, sources=None, symbol=None, band=b)) == 4


def test_source_counts_count_every_listed_source_alphabetically():
    rs = nv.rows({"items": [_it(1, sources=["WSJ", "Reuters"]), _it(2, source="WSJ"),
                            _it(3, source="bloomberg")]}, now=NOW)
    assert nv.source_counts(rs) == [("bloomberg", 1), ("Reuters", 1), ("WSJ", 2)]
    assert nv.source_counts(None) == [] and nv.source_counts(["junk"]) == []


def test_story_count():
    assert nv.story_count(17, 17) == "17 of 17 stories"
    assert nv.story_count(1, 1) == "1 of 1 story"
    assert nv.story_count(0, 0) == ""


FILING = {**_it(20, tickers=["KD"], source="SEC"), "kind": "edgar_filings",
          "title": "Kyndryl Holdings files 424B5 (prospectus supplement (offering))",
          "detail": {"form": "424B5"}}
S3 = {**FILING, "title": "CytoDyn files S-3ASR (automatic shelf registration)",
      "detail": {"form": "S-3ASR"}}
FORM4 = {**_it(21, tickers=["LEN"], source="SEC"), "kind": "edgar_form4",
         "title": "LEN — Berkshire Hathaway Inc +1 (10% Owner) bought $136.4M",
         "detail": {"total_value": 136_400_000.0}}


def test_sec_display_fields():
    rs = nv.sec_rows({"items": [FORM4, FILING, S3]}, now=NOW)
    assert [r["sec_kind"] for r in rs] == ["form4", "offering", "registration"]
    assert [r["form"] for r in rs] == ["FORM 4", "424B5", "S-3ASR"]
    assert [r["name"] for r in rs] == ["Berkshire Hathaway Inc +1",
                                      "Kyndryl Holdings — prospectus supplement (offering)",
                                      "CytoDyn — automatic shelf registration"]
    assert [r["value"] for r in rs] == ["$136.4M", "", ""]


def test_sec_display_falls_back_and_never_prints_a_zero():
    odd = {**FORM4, "title": "Something else", "detail": {"total_value": 0}}
    r = nv.sec_rows({"items": [odd]}, now=NOW)[0]
    assert (r["name"], r["value"]) == ("Something else", "")
    for form, kind in (("S-1/A", "registration"), ("F-1", "registration"),
                       ("424B3", "offering"), ("8-K", "other"), ("", "other")):
        assert nv.sec_kind({"kind": "edgar_filings", "detail": {"form": form}}) == kind, form
    assert nv.sec_kind(None) == "other"


def test_filter_sec_by_kind():
    rs = nv.sec_rows({"items": [FORM4, FILING, S3]}, now=NOW)
    assert [r["form"] for r in nv.filter_sec(rs, "form4")] == ["FORM 4"]
    assert [r["form"] for r in nv.filter_sec(rs, "offering")] == ["424B5"]
    assert [r["form"] for r in nv.filter_sec(rs, "registration")] == ["S-3ASR"]
    assert len(nv.filter_sec(rs, "all")) == len(nv.filter_sec(rs, "nope")) == 3
    assert list(nv.SEC_KINDS) == ["all", "form4", "offering", "registration"]


def test_speaker_parsing_is_only_for_a_known_shape():
    assert nv.speaker("Speech - Vice Chair for Supervision Michelle W. Bowman") == {
        "name": "Michelle W. Bowman", "role": "Vice Chair for Supervision", "what": "speech"}
    assert nv.speaker("Discussion - Governor Lisa D. Cook ")["what"] == "discussion"
    assert nv.speaker("Testimony - Chairman Kevin Warsh")["role"] == "Chairman"
    for t in ("FOMC statement", "Speech - Someone Unknown", "Speech - Governor", None, 3):
        assert nv.speaker(t) is None, t


def test_event_badges():
    assert nv.event_badge("Speech - Governor Lisa D. Cook") == "SPEECH"
    assert nv.event_badge("Discussion - Governor Lisa D. Cook") == "SPEECH"
    assert nv.event_badge("Testimony - Chairman Kevin Warsh") == "TESTIMONY"
    assert nv.event_badge("FOMC minutes") == "FOMC"
    assert nv.event_badge("Employment Situation") == "EVENT"
    assert set(nv.BADGES) >= {"FOMC", "SPEECH", "TESTIMONY", "DATA", "DIVIDEND", "IPO"}


def test_countdown_words():
    assert nv.countdown(30) == "now"
    assert nv.countdown(45 * 60 + 10) == "in 45m"
    assert nv.countdown(3 * 3600 + 5 * 60) == "in 3h 5m"
    assert nv.countdown(3 * 3600) == "in 3h"
    assert nv.countdown(86400 + 19 * 3600 + 59) == "in 1d 19h"
    assert nv.countdown(2 * 86400) == "in 2d"
    for junk in (-5, None, float("nan"), True, "10"):
        assert nv.countdown(junk) == "", junk


def test_next_up_is_the_earliest_timed_item_ahead():
    # NOW = Fri Sep 25 20:00 CT
    cal = {"events": [
        {"title": "FOMC statement", "at": "2026-10-28T18:00:00+00:00", "high": True},
        {"title": "Speech - Vice Chair for Supervision Michelle W. Bowman",
         "at": "2026-09-28T12:15:00+00:00"},
        {"title": "Past", "at": "2026-09-25T12:00:00+00:00"},
        {"title": "Date only", "date": "2026-09-26"}],
        "data": [{"tile": "JOLTS", "next_release_at": "2026-09-29T14:00:00+00:00"}]}
    n = nv.next_up(cal, now=NOW)
    assert n["title"] == "Vice Chair for Supervision Michelle W. Bowman — speech"
    assert (n["badge"], n["when"], n["countdown"]) == ("SPEECH", "Mon Sep 28 · 7:15 AM CT",
                                                       "in 2d 11h")
    only_data = {"data": cal["data"]}
    assert nv.next_up(only_data, now=NOW)["title"] == "JOLTS"
    assert nv.next_up({"events": [cal["events"][2], cal["events"][3]]}, now=NOW) is None
    assert nv.next_up(None, now=NOW) is None


def test_agenda_groups_every_kind_by_day_in_time_order():
    cal = {"events": [
        {"title": "Discussion - Vice Chair for Supervision Michelle W. Bowman",
         "at": "2026-09-28T12:15:00+00:00", "date": "2026-09-28"},
        {"title": "Speech - Governor Lisa D. Cook", "at": "2026-09-28T17:25:00+00:00",
         "date": "2026-09-28"},
        {"title": "FOMC statement", "at": "2026-10-28T18:00:00+00:00", "date": "2026-10-28",
         "high": True},
        {"title": "Beige Book", "date": "2026-09-28"}],
        "dividends": [{"symbol": "JPM", "ex_date": "2026-09-28", "amount": 1.4,
                       "pay_date": "2026-10-31"}],
        "ipos": [{"symbol": "NEWC", "company": "NewCo", "date": "2026-09-29",
                  "price_range": "18-20"}],
        "data": [{**IND, "tile": "JOLTS", "label": "Job openings",
                  "next_release_at": "2026-09-29T14:00:00+00:00"}],
        "sources": {"fed": "ok", "dividends": "stale", "nasdaq_ipo": "stale"},
        "settings": CALCFG}
    a = nv.agenda(cal, now=NOW)
    assert [d["head"] for d in a["days"]] == ["MON · SEP 28", "TUE · SEP 29",
                                             "WED · OCT 28"]
    mon = a["days"][0]["items"]
    # date-only items (no time) lead the day, then by time
    assert [(i["time"], i["badge"], i["title"]) for i in mon] == [
        ("", "EVENT", "Beige Book"), ("", "DIVIDEND", "JPM dividend"),
        ("7:15", "SPEECH", "Michelle W. Bowman — discussion"),
        ("12:25", "SPEECH", "Lisa D. Cook")]
    assert mon[2]["sub"] == "Vice Chair for Supervision" and mon[3]["sub"] == "Governor"
    assert mon[1]["sub"] == "Ex-dividend · $1.40 a share · Pays Sat Oct 31"
    tue = a["days"][1]["items"]
    assert [(i["time"], i["badge"], i["title"]) for i in tue] == [
        ("", "IPO", "NEWC · NewCo IPO"), ("9:00", "DATA", "JOLTS")]
    assert tue[0]["sub"] == "$18-20" and tue[1]["sub"] == "Prior +0.2% m/m"
    assert a["days"][2]["items"][0]["high"] is True
    assert a["notes"] == ["Dividend / IPO: Source unavailable — showing the last good reading"]
    assert a["empty"] == [] and a["undated"] == []


def test_agenda_places_a_fresh_release_at_its_release_and_says_so():
    a = nv.agenda({"data": [IND_WITH_NEW_OBS], "settings": CALCFG},
                  now=RELEASE_AT + dt.timedelta(minutes=9))
    (day,) = a["days"]
    assert day["head"] == "TODAY · WED · OCT 14"
    (item,) = day["items"]
    assert item["time"] == "7:30" and item["badge"] == "DATA"
    assert item["sub"] == "Actual +0.4% m/m · Prior +0.2% m/m · Released 7:30 AM CT"
    waiting = {**IND, "last_release_at": RELEASE_AT.isoformat(), "next_release_at": None}
    a = nv.agenda({"data": [waiting], "settings": CALCFG},
                  now=RELEASE_AT + dt.timedelta(minutes=3))
    assert a["days"][0]["items"][0]["sub"] == "Awaiting the release · Prior +0.2% m/m"


def test_agenda_multi_indicator_tiles_line_per_indicator_and_undated_tiles():
    two = [{**IND, "label": "CPI m/m"}, {**IND, "key": "core", "label": "Core CPI m/m",
                                        "high": True}]
    a = nv.agenda({"data": two, "settings": CALCFG}, now=NOW)
    (item,) = a["days"][0]["items"]
    assert item["sub"] == "" and item["high"] is True
    assert item["lines"] == ["CPI m/m: Prior +0.2% m/m", "Core CPI m/m: Prior +0.2% m/m"]
    a = nv.agenda({"data": [IND_NO_NEXT], "settings": CALCFG}, now=NOW)
    assert a["days"] == [] and len(a["undated"]) == 1
    assert a["undated"][0]["sub"].startswith(nv.NO_NEXT)


def test_agenda_says_which_kinds_are_empty_and_never_raises_on_junk():
    a = nv.agenda({"events": [], "dividends": [], "ipos": [], "data": []}, now=NOW)
    assert a["days"] == [] and a["empty"] == ["No scheduled events ahead",
                                              "No dividends or IPOs ahead",
                                              "No indicators to show"]
    for junk in (None, "x", {"events": "x", "data": [None, 3, {}]}, {"dividends": [{}]}):
        a = nv.agenda(junk, now=NOW)
        assert a["days"] == [] and isinstance(a["notes"], list)


def test_sec_tone_is_a_finite_family_per_form():
    tone = lambda form: nv.sec_tone({"kind": "edgar_filings", "detail": {"form": form}})
    assert [tone(f) for f in ("S-1", "S-1/A", "F-1", "S-3", "S-3ASR", "F-3", "424B5", "8-K")] \
        == ["ipo", "ipo", "ipo", "shelf", "shelf", "shelf", "offering", "other"]
    assert nv.sec_tone(FORM4) == "form4" and nv.sec_tone(None) == "other"
