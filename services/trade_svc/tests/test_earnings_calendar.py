"""Tests for the Alpha Vantage earnings calendar (Phase 1, task 1.2's other half).

Schwab's `/instruments` carries no earnings date at all, so the gate that most
matters for a multi-week hold — is there a report inside the horizon? — has
never been able to fire. There is no OFFICIAL source: 8-K Item 2.02 is
retrospective and no exchange publishes a forward calendar, so every forward
date is a vendor product. Alpha Vantage's EARNINGS_CALENDAR is one BULK CSV for
the whole market, which is what makes a 25-request/day free tier sufficient:
one call per night, not one per symbol.

Network is stubbed throughout.
"""
import datetime as dt

import pytest

from services.trade_svc import earnings_calendar as ec


_CSV = (
    "symbol,name,reportDate,fiscalDateEnding,estimate,currency\r\n"
    "NVDA,NVIDIA CORP,2026-08-26,2026-07-31,2.01,USD\r\n"
    "AAPL,APPLE INC,2026-10-29,2026-09-30,1.55,USD\r\n"
    "CHWY,CHEWY INC,2026-09-09,2026-07-31,0.18,USD\r\n"
)


class TestParse:
    def test_parses_the_bulk_csv(self):
        rows = ec.parse_calendar(_CSV)
        assert len(rows) == 3
        nvda = next(r for r in rows if r["symbol"] == "NVDA")
        assert nvda["report_date"] == "2026-08-26"

    def test_a_row_without_a_usable_date_is_skipped(self):
        bad = _CSV + "ZZZZ,Bad Co,,2026-07-31,0.1,USD\r\n"
        assert {r["symbol"] for r in ec.parse_calendar(bad)} == {"NVDA", "AAPL", "CHWY"}

    def test_a_blank_body_is_empty_not_an_exception(self):
        assert ec.parse_calendar("") == []
        assert ec.parse_calendar("symbol,name,reportDate\r\n") == []

    def test_an_html_error_page_yields_nothing(self):
        """Alpha Vantage answers a bad key with 200 + a JSON/HTML note, not an
        HTTP error. Parsing that as CSV must produce no rows rather than
        garbage rows that would look like real earnings dates."""
        assert ec.parse_calendar('{"Information": "the demo key is for demo use"}') == []


class TestStore:
    @pytest.fixture
    def conn(self, tmp_path):
        c = ec.init_db(tmp_path / "ec.db")
        yield c
        ec.close_db(c)

    def test_round_trip(self, conn):
        ec.store_calendar(conn, ec.parse_calendar(_CSV))
        assert ec.lookup(conn, "NVDA")["report_date"] == "2026-08-26"

    def test_lookup_is_case_insensitive_and_missing_is_none(self, conn):
        ec.store_calendar(conn, ec.parse_calendar(_CSV))
        assert ec.lookup(conn, "nvda") is not None
        assert ec.lookup(conn, "NOPE") is None

    def test_the_nearest_FUTURE_date_wins(self, conn):
        """A symbol can carry several scheduled quarters. The gate cares about
        the next one, and a date already past must never be returned as
        'upcoming'."""
        ec.store_calendar(conn, [
            {"symbol": "X", "report_date": "2026-05-01", "fiscal_date_ending": "", "estimate": None},
            {"symbol": "X", "report_date": "2026-11-04", "fiscal_date_ending": "", "estimate": None},
            {"symbol": "X", "report_date": "2026-09-02", "fiscal_date_ending": "", "estimate": None},
        ])
        got = ec.lookup(conn, "X", as_of=dt.date(2026, 8, 22))
        assert got["report_date"] == "2026-09-02"

    def test_only_past_dates_reads_as_no_upcoming_report(self, conn):
        ec.store_calendar(conn, [
            {"symbol": "OLD", "report_date": "2026-01-05", "fiscal_date_ending": "", "estimate": None}])
        assert ec.lookup(conn, "OLD", as_of=dt.date(2026, 8, 22)) is None

    def test_store_never_raises(self, conn):
        conn.close()
        assert ec.store_calendar(conn, ec.parse_calendar(_CSV)) is False


class TestDaysToEarnings:
    @pytest.fixture
    def conn(self, tmp_path):
        c = ec.init_db(tmp_path / "ec.db")
        ec.store_calendar(c, ec.parse_calendar(_CSV))
        yield c
        ec.close_db(c)

    def test_counts_calendar_days_to_the_next_report(self, conn):
        assert ec.days_to_earnings(conn, "NVDA", as_of=dt.date(2026, 8, 22)) == 4

    def test_a_report_today_is_zero_not_none(self, conn):
        """Zero is the most gate-worthy value there is — it must not collapse
        into the same None that means 'we have no idea'."""
        assert ec.days_to_earnings(conn, "NVDA", as_of=dt.date(2026, 8, 26)) == 0

    def test_an_unknown_symbol_is_none(self, conn):
        assert ec.days_to_earnings(conn, "NOPE", as_of=dt.date(2026, 8, 22)) is None


class TestApiKey:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "  from-env  ")
        assert ec.api_key() == "from-env"

    def test_absent_key_is_none_not_an_exception(self, monkeypatch):
        monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
        monkeypatch.setattr(ec, "_KEY_FILE", "/nonexistent/nope.txt")
        assert ec.api_key() is None


class TestFetch:
    def test_no_key_means_no_request_at_all(self, monkeypatch):
        """Without a key the call would return an explanatory note that parses
        to zero rows — so skip it and say so, rather than burning a request and
        reporting an empty calendar as if it were real."""
        monkeypatch.setattr(ec, "api_key", lambda: None)
        called = []
        monkeypatch.setattr(ec, "_get", lambda url: called.append(url))
        assert ec.fetch_calendar() == []
        assert called == []

    def test_fetch_parses_what_the_endpoint_returns(self, monkeypatch):
        monkeypatch.setattr(ec, "api_key", lambda: "k")
        monkeypatch.setattr(ec, "_get", lambda url: _CSV)
        rows = ec.fetch_calendar()
        assert {r["symbol"] for r in rows} == {"NVDA", "AAPL", "CHWY"}

    def test_the_request_asks_for_the_full_horizon(self, monkeypatch):
        """3-month coverage was measured INCOMPLETE — mega-caps reporting
        inside the window were absent. The 12-month horizon is what the gate
        needs, so it must actually be requested."""
        monkeypatch.setattr(ec, "api_key", lambda: "k")
        seen = {}
        monkeypatch.setattr(ec, "_get", lambda url: seen.setdefault("url", url) and "" or "")
        ec.fetch_calendar()
        assert "horizon=12month" in seen["url"]
        assert "function=EARNINGS_CALENDAR" in seen["url"]

    def test_a_network_failure_yields_no_rows(self, monkeypatch):
        monkeypatch.setattr(ec, "api_key", lambda: "k")

        def boom(url):
            raise RuntimeError("offline")

        monkeypatch.setattr(ec, "_get", boom)
        assert ec.fetch_calendar() == []
