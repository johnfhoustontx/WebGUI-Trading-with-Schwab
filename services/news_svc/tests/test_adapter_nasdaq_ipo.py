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
