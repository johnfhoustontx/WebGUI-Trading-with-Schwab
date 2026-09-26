"""Nasdaq IPO calendar adapter - pure, over the REAL response body.

``nasdaq_ipo_2026_09.json`` is a real ``api/ipo/calendar?date=2026-09`` body
fetched 2026-09-26, trimmed to a subset of its own rows (order kept);
``nasdaq_ipo_empty.json`` is the real, unedited ``?date=2026-10`` body (rows
``null`` plus "No record found" messages under rCode 200).
``nasdaq_ipo_rcode_400.json`` is hand-written: a failure code under HTTP 200.
"""
import json
import pathlib

import pytest

from services.news_svc.adapters import nasdaq_ipo

FIX = pathlib.Path(__file__).parent / "fixtures"
BODY = (FIX / "nasdaq_ipo_2026_09.json").read_bytes()
EMPTY = (FIX / "nasdaq_ipo_empty.json").read_bytes()
RCODE_400 = (FIX / "nasdaq_ipo_rcode_400.json").read_bytes()


def test_upcoming_and_priced_rows_with_numbers_from_strings():
    rows = nasdaq_ipo.parse(BODY)
    oura = next(r for r in rows if r["symbol"] == "OURA")
    assert oura == {"symbol": "OURA", "company": "Oura Inc.", "status": "upcoming",
                    "date": "2026-09-30", "price": "40.00-44.00", "offer_usd": 2_530_000_000,
                    "exchange": "NASDAQ Global Select", "id": oura["id"]}
    assert next(r for r in rows if r["status"] == "priced")["offer_usd"] == 240_000_000


def test_filed_and_withdrawn_are_not_calendar_rows():
    assert {r["status"] for r in nasdaq_ipo.parse(BODY)} <= {"upcoming", "priced"}


def test_every_priced_and_upcoming_row_is_kept_with_a_distinct_id():
    rows = nasdaq_ipo.parse(BODY)
    assert sorted(r["symbol"] for r in rows) == ["ACCV", "ETRA", "OIG", "OURA", "PTT"]
    ids = [r["id"] for r in rows]
    assert all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids)


def test_money_with_cents_and_a_missing_price_or_amount():
    rows = {r["symbol"]: r for r in nasdaq_ipo.parse(BODY)}
    assert rows["ACCV"]["offer_usd"] == 828_000_000            # "$828,000,000.00"
    assert rows["PTT"]["price"] is None and rows["PTT"]["offer_usd"] is None
    assert rows["OIG"]["date"] == "2026-09-18" and rows["OIG"]["price"] == "12.00"


def test_empty_month_is_empty_not_error():
    assert nasdaq_ipo.parse(EMPTY) == []


def test_rcode_failure_raises_even_under_http_200():
    with pytest.raises(ValueError):
        nasdaq_ipo.parse(RCODE_400)


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b'{"status": {"rCode": 200}}',
                                  b"\xff\xfe", b'{"data": [], "status": {"rCode": 200}}'])
def test_a_body_that_is_not_a_calendar_is_a_failure_not_an_empty_month(body):
    with pytest.raises(ValueError):
        nasdaq_ipo.parse(body)


def _one(row, table="priced"):
    doc = {"data": {"priced": {"rows": None}, "upcoming": {"upcomingTable": {"rows": None}}},
           "status": {"rCode": 200}}
    if table == "priced":
        doc["data"]["priced"]["rows"] = [row]
    else:
        doc["data"]["upcoming"]["upcomingTable"]["rows"] = [row]
    return json.dumps(doc).encode()


def test_null_or_refused_ticker_keeps_the_row_with_symbol_none():
    for ticker in (None, "", "BAD TICKER!", 42):
        rows = nasdaq_ipo.parse(_one({"dealID": "1-2", "proposedTickerSymbol": ticker,
                                      "companyName": "Acme &amp; Co", "pricedDate": "9/03/2026"}))
        assert len(rows) == 1 and rows[0]["symbol"] is None
        assert rows[0]["company"] == "Acme & Co"
        assert rows[0]["date"] == "2026-09-03"


@pytest.mark.parametrize("date", ["2026-09-30", "13/01/2026", "2/30/2026", "", None, 20260930])
def test_an_unparseable_date_skips_the_row(date):
    assert nasdaq_ipo.parse(_one({"dealID": "1", "proposedTickerSymbol": "ABC",
                                  "companyName": "A", "expectedPriceDate": date},
                                 table="upcoming")) == []


def test_malformed_rows_are_skipped_not_raised():
    doc = {"data": {"priced": {"rows": ["x", None, 5, {"pricedDate": "9/1/2026"}]},
                    "upcoming": "nonsense"}, "status": {"rCode": 200}}
    rows = nasdaq_ipo.parse(json.dumps(doc).encode())
    assert [r["date"] for r in rows] == ["2026-09-01"]
    assert rows[0]["company"] is None and rows[0]["id"]


def test_url_names_the_month():
    assert nasdaq_ipo.url(2026, 9) == "https://api.nasdaq.com/api/ipo/calendar?date=2026-09"


@pytest.mark.parametrize("raw,want", [("$240,000,000.00", 240_000_000), ("", None),
                                      (None, None), ("n/a", None), ("$1e9", None)])
def test_money(raw, want):
    assert nasdaq_ipo._money(raw) == want


@pytest.mark.parametrize("raw", ["$2,530,000,000", "2530000000", " $2,530,000,000 "])
def test_money_accepts_the_shapes_nasdaq_sends(raw):
    assert nasdaq_ipo._money(raw) == 2_530_000_000


@pytest.mark.parametrize("raw", ["$nan", "inf", "-$5", "$1,00,000", True, 3.5, "$0"])
def test_money_refuses_anything_else(raw):
    assert nasdaq_ipo._money(raw) is None


# --- review fixes: recursion, per-deal ids + dedupe, fallback ids, ASCII only ---

def test_deep_nesting_is_a_valueerror_not_a_recursionerror():
    body = b"[" * 200_000 + b"]" * 200_000
    with pytest.raises(ValueError):
        nasdaq_ipo.parse(body)
    deep = b'{"status": {"rCode": 200}, "data": ' + b"[" * 200_000 + b"]" * 200_000 + b"}"
    with pytest.raises(ValueError):
        nasdaq_ipo.parse(deep)


def test_id_is_per_deal_not_per_status():
    up = nasdaq_ipo.parse(_one({"dealID": "9-9", "proposedTickerSymbol": "ABC",
                                "companyName": "A", "expectedPriceDate": "9/30/2026"}, table="upcoming"))
    pr = nasdaq_ipo.parse(_one({"dealID": "9-9", "proposedTickerSymbol": "ABC",
                                "companyName": "A", "pricedDate": "10/01/2026"}))
    assert up[0]["id"] == pr[0]["id"] == "nasdaq_ipo:9-9"


def test_dedupe_keeps_one_row_per_id_preferring_priced():
    up = {"id": "nasdaq_ipo:1", "status": "upcoming", "date": "2026-09-30"}
    pr = {"id": "nasdaq_ipo:1", "status": "priced", "date": "2026-10-01"}
    other = {"id": "nasdaq_ipo:2", "status": "upcoming", "date": "2026-09-29"}
    assert nasdaq_ipo.dedupe([up, other, pr]) == [pr, other]
    assert nasdaq_ipo.dedupe([pr, other, up]) == [pr, other]


def test_dedupe_preference_order():
    rank = ["priced", "upcoming", "filed", "withdrawn"]
    for i, better in enumerate(rank):
        for worse in rank[i + 1:]:
            a = {"id": "x", "status": worse}
            b = {"id": "x", "status": better}
            assert nasdaq_ipo.dedupe([a, b]) == [b]
            assert nasdaq_ipo.dedupe([b, a]) == [b]


def test_dedupe_first_seen_wins_a_tie_and_empty_is_empty():
    a = {"id": "x", "status": "upcoming", "date": "2026-09-01"}
    b = {"id": "x", "status": "upcoming", "date": "2026-09-02"}
    assert nasdaq_ipo.dedupe([a, b]) == [a]
    assert nasdaq_ipo.dedupe([]) == []


def test_dedupe_over_two_real_months_pages():
    rows = nasdaq_ipo.parse(BODY)
    assert nasdaq_ipo.dedupe(rows + rows) == rows


def test_fallback_ids_without_a_deal_id_do_not_collide():
    doc = {"data": {"priced": {"rows": [
        {"proposedTickerSymbol": None, "companyName": None, "pricedDate": "9/1/2026"},
        {"proposedTickerSymbol": None, "companyName": None, "pricedDate": "9/1/2026"},
        {"proposedTickerSymbol": "ABC", "companyName": "Acme", "pricedDate": "9/1/2026"},
        {"proposedTickerSymbol": "ABC", "companyName": "Other Co", "pricedDate": "9/1/2026"},
    ]}}, "status": {"rCode": 200}}
    rows = nasdaq_ipo.parse(json.dumps(doc).encode())
    ids = [r["id"] for r in rows]
    assert len(rows) == 4 and len(set(ids)) == 4
    assert all(i.startswith("nasdaq_ipo:") for i in ids)
    assert "Acme" in ids[2] and "ABC" in ids[2] and "2026-09-01" in ids[2]


@pytest.mark.parametrize("raw", ["$２,530,000,000", "２５３０", "$240,000,000.０", "٣٠٠"])
def test_money_refuses_non_ascii_digits(raw):
    assert nasdaq_ipo._money(raw) is None


@pytest.mark.parametrize("raw", ["９/30/2026", "9/３0/2026", "9/30/２０２６", "٩/30/2026"])
def test_date_refuses_non_ascii_digits(raw):
    assert nasdaq_ipo._iso_date(raw) is None


def _anon(rows, table="priced"):
    doc = {"data": {"priced": {"rows": None}, "upcoming": {"upcomingTable": {"rows": None}}},
           "status": {"rCode": 200}}
    if table == "priced":
        doc["data"]["priced"]["rows"] = rows
    else:
        doc["data"]["upcoming"]["upcomingTable"]["rows"] = rows
    return json.dumps(doc).encode()


def _r(sym, co, day="9/1/2026"):
    return {"proposedTickerSymbol": sym, "companyName": co, "pricedDate": day}


def _ids_by_key(rows):
    return {(r["symbol"], r["company"], r["date"]): r["id"] for r in rows}


def test_fallback_ids_survive_reordering_and_a_new_row_at_the_top():
    a, b, c = _r("ABC", "Acme"), _r("XYZ", "Xyz Co"), _r(None, "Anon Inc", "9/2/2026")
    base = _ids_by_key(nasdaq_ipo.parse(_anon([a, b, c])))
    assert _ids_by_key(nasdaq_ipo.parse(_anon([c, a, b]))) == base
    grown = _ids_by_key(nasdaq_ipo.parse(_anon([_r("NEW", "Newco"), a, b, c])))
    assert {k: grown[k] for k in base} == base


def test_exact_duplicate_tuples_still_get_distinct_ids():
    rows = nasdaq_ipo.parse(_anon([_r("ABC", "Acme"), _r("ABC", "Acme"), _r("ABC", "Acme")]))
    ids = [r["id"] for r in rows]
    assert len(rows) == 3 and len(set(ids)) == 3


def test_a_company_holding_separators_cannot_collide():
    rows = nasdaq_ipo.parse(_anon([
        {"proposedTickerSymbol": None, "companyName": "A|B", "pricedDate": "9/1/2026"},
        {"proposedTickerSymbol": None, "companyName": "A", "pricedDate": "9/1/2026"},
        {"proposedTickerSymbol": None, "companyName": 'A", "B', "pricedDate": "9/1/2026"},
        {"proposedTickerSymbol": None, "companyName": "A|B|2026-09-01|0", "pricedDate": "9/1/2026"},
        {"proposedTickerSymbol": None, "companyName": "A#2", "pricedDate": "9/1/2026"},
        {"proposedTickerSymbol": None, "companyName": "A", "pricedDate": "9/1/2026"},
    ]))
    ids = [r["id"] for r in rows]
    assert len(rows) == 6 and len(set(ids)) == 6


def test_parse_ids_are_unique_within_one_call_and_priced_wins():
    doc = {"data": {"priced": {"rows": [
        {"dealID": "7-7", "proposedTickerSymbol": "ABC", "companyName": "A", "pricedDate": "9/2/2026"}]},
        "upcoming": {"upcomingTable": {"rows": [
            {"dealID": "7-7", "proposedTickerSymbol": "ABC", "companyName": "A",
             "expectedPriceDate": "9/1/2026"},
            {"dealID": "8-8", "proposedTickerSymbol": "DEF", "companyName": "D",
             "expectedPriceDate": "9/3/2026"},
            {"dealID": "8-8", "proposedTickerSymbol": "DEF", "companyName": "D",
             "expectedPriceDate": "9/3/2026"}]}}},
        "status": {"rCode": 200}}
    rows = nasdaq_ipo.parse(json.dumps(doc).encode())
    assert [(r["id"], r["status"]) for r in rows] == [
        ("nasdaq_ipo:7-7", "priced"), ("nasdaq_ipo:8-8", "upcoming")]


def test_dedupe_passes_rows_without_an_id_through():
    a = {"status": "upcoming", "symbol": "A"}
    b = {"id": None, "status": "upcoming", "symbol": "B"}
    c = {"id": "x", "status": "upcoming"}
    d = {"id": None, "status": "priced", "symbol": "D"}
    assert nasdaq_ipo.dedupe([a, b, c, d, c]) == [a, b, c, d]


def test_dedupe_converts_an_unhashable_id_with_str():
    a = {"id": ["x"], "status": "upcoming"}
    b = {"id": ["x"], "status": "priced"}
    c = {"id": {"k": 1}, "status": "upcoming"}
    assert nasdaq_ipo.dedupe([a, c, b]) == [b, c]
