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


# Every test here that reads a date pins `as_of`, so `_CSV` stays LITERAL and
# is stable forever. A relative fixture would break those pinned expectations.
# The default-`as_of` path is covered separately by
# TestStore::test_the_DEFAULT_as_of_is_today, which builds its own row.
_AS_OF = dt.date(2026, 8, 22)

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
        assert ec.lookup(conn, "NVDA", as_of=_AS_OF)["report_date"] == "2026-08-26"

    def test_lookup_is_case_insensitive_and_missing_is_none(self, conn):
        ec.store_calendar(conn, ec.parse_calendar(_CSV))
        assert ec.lookup(conn, "nvda", as_of=_AS_OF) is not None
        assert ec.lookup(conn, "NOPE", as_of=_AS_OF) is None

    def test_the_DEFAULT_as_of_is_today(self, conn):
        """The two tests above pin `as_of`, like the rest of this file, so they
        are stable forever. This one deliberately exercises the DEFAULT — and
        must therefore build its own row RELATIVE to today.

        ⚠ Do not fold this into the pinned tests by giving `_CSV` relative
        dates. `_CSV` is shared with every `as_of=`-pinned test here, which
        expects its literal 2026-08-26; making it relative breaks those instead.
        The two shapes need separate data, which is the whole point of this
        test being separate."""
        soon = (dt.date.today() + dt.timedelta(days=14)).isoformat()
        ec.store_calendar(conn, [
            {"symbol": "FUT", "report_date": soon,
             "fiscal_date_ending": "", "estimate": None}])
        assert ec.lookup(conn, "FUT")["report_date"] == soon

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


class TestCoverageIsDistinctFromAbsence:
    """Measured live 2026-08-22 with a real key: the 12-month horizon returns
    1,814 symbols and coverage COLLAPSES with distance — 1,032 rows in October,
    40 in December, 11 in March. It is genuinely patchy, not merely limited to
    announced dates: AAPL and GOOGL are listed at 67-68 days out while MSFT,
    AMZN and META — the same late-October cycle — are absent entirely.

    So `days_to_earnings is None` means TWO different things, and conflating
    them makes the gate fail OPEN silently: "no report scheduled" and "this
    symbol is not in the calendar at all" must be distinguishable, or a trade
    walks into an unlisted earnings date under the appearance of protection.
    That is the same absence-reads-as-a-confident-value trap this whole program
    exists to close."""

    @pytest.fixture
    def conn(self, tmp_path):
        c = ec.init_db(tmp_path / "ec.db")
        ec.store_calendar(c, [
            {"symbol": "NVDA", "report_date": "2026-08-26",
             "fiscal_date_ending": "", "estimate": None},
            {"symbol": "OLDCO", "report_date": "2026-01-05",
             "fiscal_date_ending": "", "estimate": None},
        ])
        yield c
        ec.close_db(c)

    def test_a_listed_symbol_with_an_upcoming_date_is_covered(self, conn):
        assert ec.coverage(conn, "NVDA", as_of=dt.date(2026, 8, 22)) == "upcoming"

    def test_a_listed_symbol_whose_dates_are_all_past_is_still_COVERED(self, conn):
        """The vendor knows this symbol; it simply has nothing scheduled ahead.
        That is a real 'no earnings in the window', and it is trustworthy."""
        assert ec.coverage(conn, "OLDCO", as_of=dt.date(2026, 8, 22)) == "none_scheduled"

    def test_a_symbol_the_vendor_never_lists_is_UNKNOWN_not_clear(self, conn):
        assert ec.coverage(conn, "MSFT", as_of=dt.date(2026, 8, 22)) == "not_listed"

    def test_an_empty_calendar_reports_unknown_for_everything(self, tmp_path):
        c = ec.init_db(tmp_path / "empty.db")
        try:
            assert ec.coverage(c, "AAPL") == "not_listed"
        finally:
            ec.close_db(c)


class TestTheReadPathDoesNotDependOnTheCALLERSConnection:
    """Found in prod 2026-08-23. `lookup` used `conn.execute`, which inherits
    the CONNECTION's row factory, and the readers index the result by column
    name. A caller who opened the store with a plain `sqlite3.connect` — no
    row factory — made `row["report_date"]` raise TypeError, which
    `days_to_earnings` then swallowed into None.

    The failure is silent and total: every symbol reports "no earnings", which
    is indistinguishable from a calendar that simply has nothing scheduled. It
    briefly convinced me a working 1,808-row calendar was empty.

    `init_db` does set the factory, so production was never affected — but a
    read path that only works when the caller configured the connection
    correctly is a trap, and the guard turned that trap into a plausible
    answer instead of an error.
    """

    @pytest.fixture()
    def db_path(self, tmp_path):
        c = ec.init_db(tmp_path / "ec.db")
        ec.store_calendar(c, ec.parse_calendar(_CSV))
        c.close()
        return tmp_path / "ec.db"

    def _raw(self, db_path):
        """A connection opened the way a caller reasonably might — and the way
        I did, which is what exposed this."""
        import sqlite3
        return sqlite3.connect(str(db_path))

    def test_days_to_earnings_works_without_a_row_factory(self, db_path):
        conn = self._raw(db_path)
        got = ec.days_to_earnings(conn, "NVDA", as_of=dt.date(2026, 8, 23))
        assert got == 3

    def test_lookup_works_without_a_row_factory(self, db_path):
        row = ec.lookup(self._raw(db_path), "NVDA", as_of=dt.date(2026, 8, 23))
        assert row is not None
        assert row["report_date"] == "2026-08-26"

    def test_coverage_works_without_a_row_factory(self, db_path):
        assert ec.coverage(self._raw(db_path), "NVDA",
                           as_of=dt.date(2026, 8, 23)) == "upcoming"

    def test_it_agrees_with_a_properly_configured_connection(self, db_path):
        """The two must not disagree — that is the whole point."""
        good = ec.init_db(db_path)
        raw = self._raw(db_path)
        for sym in ("NVDA", "AAPL", "CHWY", "ZZZZ"):
            assert (ec.days_to_earnings(good, sym, as_of=dt.date(2026, 8, 23))
                    == ec.days_to_earnings(raw, sym, as_of=dt.date(2026, 8, 23)))

    def test_a_genuinely_absent_symbol_is_still_None(self, db_path):
        """The fix must not turn "no idea" into a number."""
        assert ec.days_to_earnings(self._raw(db_path), "ZZZZ",
                                   as_of=dt.date(2026, 8, 23)) is None

    def test_a_report_TODAY_is_still_zero_not_None(self, db_path):
        assert ec.days_to_earnings(self._raw(db_path), "NVDA",
                                   as_of=dt.date(2026, 8, 26)) == 0
