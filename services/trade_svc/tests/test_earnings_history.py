"""Tests for the Alpha Vantage EPS-surprise history.

Schwab publishes no earnings surprises, which is why `earnings_traj` scored a
permanent 0 for every symbol. Alpha Vantage's EARNINGS endpoint does — probed
live 2026-08-23, MU returned 122 quarters carrying `reportedEPS`,
`estimatedEPS`, `surprise` and `surprisePercentage`.

Two things this module has to get right, and both are easy to get wrong:

* **Order and units.** `score_earnings_surprise_streak` reads a CHRONOLOGICAL
  list of FRACTIONS (0.06 = a 6% beat). The vendor returns newest-first, in
  PERCENT units (18.6368). Getting either wrong scores a streak backwards or
  compares 18.64 against a 0.05 threshold.
* **The request budget.** The free tier allows 25 calls a day and the bulk
  earnings CALENDAR already takes one. That calendar feeds the earnings gate,
  which is the more important consumer, so this module must never be able to
  starve it.

Network is stubbed throughout.
"""
import datetime as dt
import json

import pytest

from services.trade_svc import earnings_history as eh


_JSON = json.dumps({
    "symbol": "MU",
    "quarterlyEarnings": [
        # vendor order: NEWEST first
        {"fiscalDateEnding": "2026-05-31", "reportedDate": "2026-06-24",
         "reportedEPS": "24.89", "estimatedEPS": "20.98",
         "surprise": "3.91", "surprisePercentage": "18.6368"},
        {"fiscalDateEnding": "2026-02-28", "reportedDate": "2026-03-18",
         "reportedEPS": "12.2", "estimatedEPS": "9.31",
         "surprise": "2.89", "surprisePercentage": "31.0419"},
        {"fiscalDateEnding": "2025-11-30", "reportedDate": "2025-12-17",
         "reportedEPS": "4.78", "estimatedEPS": "3.94",
         "surprise": "0.84", "surprisePercentage": "21.3198"},
        {"fiscalDateEnding": "2025-08-31", "reportedDate": "2025-09-23",
         "reportedEPS": "3.03", "estimatedEPS": "2.86",
         "surprise": "0.17", "surprisePercentage": "5.9441"},
        {"fiscalDateEnding": "2025-05-31", "reportedDate": "2025-06-25",
         "reportedEPS": "1.91", "estimatedEPS": "1.59",
         "surprise": "0.32", "surprisePercentage": "20.1258"},
    ],
})


class TestParse:
    def test_parses_the_quarterly_block(self):
        rows = eh.parse_earnings(_JSON)
        assert len(rows) == 5
        assert rows[0]["fiscal_date_ending"] == "2026-05-31"
        assert rows[0]["surprise_pct"] == pytest.approx(18.6368)

    def test_a_row_without_a_usable_surprise_is_skipped(self):
        """Alpha Vantage writes "None" as a STRING for quarters it has no
        estimate for. Parsed naively that becomes a float() explosion or, worse,
        a 0.0 that reads as "met expectations exactly"."""
        payload = json.dumps({"symbol": "X", "quarterlyEarnings": [
            {"fiscalDateEnding": "2026-05-31", "surprisePercentage": "None"},
            {"fiscalDateEnding": "2026-02-28", "surprisePercentage": ""},
            {"fiscalDateEnding": "2025-11-30", "surprisePercentage": "5.0"},
        ]})
        rows = eh.parse_earnings(payload)
        assert [r["fiscal_date_ending"] for r in rows] == ["2025-11-30"]

    def test_an_error_note_yields_nothing_rather_than_garbage(self):
        """A bad key answers 200 with a note, not an HTTP error."""
        assert eh.parse_earnings('{"Information": "rate limit reached"}') == []
        assert eh.parse_earnings("") == []
        assert eh.parse_earnings("<html>nope</html>") == []


class TestStoreAndRead:
    @pytest.fixture()
    def conn(self, tmp_path):
        c = eh.init_db(tmp_path / "eh.db")
        eh.store(c, "MU", eh.parse_earnings(_JSON))
        return c

    def test_surprises_come_back_CHRONOLOGICAL_as_FRACTIONS(self):
        """The two things the scorer's contract depends on. The vendor gives
        newest-first percentages; the scorer wants oldest-first fractions."""
        c = eh.init_db(":memory:")
        eh.store(c, "MU", eh.parse_earnings(_JSON))
        got = eh.surprise_fractions(c, "MU")
        assert got == sorted(got, key=lambda _: 0) or True     # order asserted below
        assert got[-1] == pytest.approx(0.186368)              # newest is LAST
        assert got[0] == pytest.approx(0.201258)               # oldest is FIRST
        assert all(abs(x) < 1.5 for x in got)                  # fractions, not percents

    def test_it_honours_the_requested_depth(self, conn):
        assert len(eh.surprise_fractions(conn, "MU", limit=3)) == 3
        # still the MOST RECENT three, still chronological
        assert eh.surprise_fractions(conn, "MU", limit=3)[-1] == pytest.approx(0.186368)

    def test_an_unknown_symbol_is_None_not_an_empty_list(self, conn):
        """[] would score as "no streak"; None means "we never asked"."""
        assert eh.surprise_fractions(conn, "ZZZZ") is None

    def test_storing_twice_does_not_duplicate_quarters(self, conn):
        eh.store(conn, "MU", eh.parse_earnings(_JSON))
        assert len(eh.surprise_fractions(conn, "MU")) == 5

    def test_a_read_works_without_a_row_factory(self, tmp_path):
        """The trap that bit the calendar module the same day."""
        import sqlite3
        p = tmp_path / "eh.db"
        c = eh.init_db(p)
        eh.store(c, "MU", eh.parse_earnings(_JSON))
        c.close()
        raw = sqlite3.connect(str(p))
        assert len(eh.surprise_fractions(raw, "MU")) == 5


class TestFreshness:
    @pytest.fixture()
    def conn(self, tmp_path):
        return eh.init_db(tmp_path / "eh.db")

    def test_a_symbol_never_fetched_is_due(self, conn):
        assert eh.is_due(conn, "MU") is True

    def test_a_symbol_fetched_today_is_not_due_again(self, conn):
        eh.store(conn, "MU", eh.parse_earnings(_JSON))
        assert eh.is_due(conn, "MU") is False

    def test_it_becomes_due_once_the_data_could_have_changed(self, conn):
        """Quarterly data. Re-asking daily would burn the whole budget for
        numbers that cannot move for three months."""
        eh.store(conn, "MU", eh.parse_earnings(_JSON))
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            days=eh.REFRESH_AFTER_DAYS + 1)
        assert eh.is_due(conn, "MU", now=later) is True

    def test_a_symbol_the_vendor_does_not_cover_is_not_retried_daily(self, conn):
        """An empty answer is a real answer and must be remembered, or every
        uncovered symbol costs a call on every single analysis."""
        eh.store(conn, "ZZZZ", [])
        assert eh.is_due(conn, "ZZZZ") is False


class TestTheRequestBudget:
    @pytest.fixture()
    def conn(self, tmp_path):
        return eh.init_db(tmp_path / "eh.db")

    def test_it_stops_before_exhausting_the_daily_allowance(self, conn):
        """The bulk calendar feeds the earnings GATE and takes one call a day.
        This module must never be the reason that call fails."""
        today = dt.date(2026, 8, 23)
        for i in range(eh.DAILY_BUDGET):
            assert eh.budget_left(conn, today=today) > 0
            eh.note_call(conn, today=today)
        assert eh.budget_left(conn, today=today) == 0

    def test_the_budget_resets_the_next_day(self, conn):
        today = dt.date(2026, 8, 23)
        for _ in range(eh.DAILY_BUDGET):
            eh.note_call(conn, today=today)
        assert eh.budget_left(conn, today=dt.date(2026, 8, 24)) == eh.DAILY_BUDGET

    def test_the_budget_leaves_room_for_the_calendar(self, conn):
        """25/day total, and `earnings_calendar` needs one of them."""
        assert eh.DAILY_BUDGET < 25

    def test_refresh_declines_when_the_budget_is_gone(self, conn, monkeypatch):
        called = []
        monkeypatch.setattr(eh, "api_key", lambda: "test-key")
        monkeypatch.setattr(eh, "_fetch", lambda s: called.append(s) or _JSON)
        today = dt.date(2026, 8, 23)
        for _ in range(eh.DAILY_BUDGET):
            eh.note_call(conn, today=today)
        assert eh.refresh(conn, "MU", today=today) is False
        assert called == []

    def test_refresh_spends_one_call_and_stores(self, conn, monkeypatch):
        monkeypatch.setattr(eh, "api_key", lambda: "test-key")
        monkeypatch.setattr(eh, "_fetch", lambda s: _JSON)
        today = dt.date(2026, 8, 23)
        before = eh.budget_left(conn, today=today)
        assert eh.refresh(conn, "MU", today=today) is True
        assert eh.budget_left(conn, today=today) == before - 1
        assert len(eh.surprise_fractions(conn, "MU")) == 5

    def test_a_failed_fetch_still_spends_the_call_it_made(self, conn, monkeypatch):
        """The vendor counted it even though we got nothing. Pretending
        otherwise is how a retry loop empties the allowance."""
        def boom(_s):
            raise RuntimeError("network")
        monkeypatch.setattr(eh, "api_key", lambda: "test-key")
        monkeypatch.setattr(eh, "_fetch", boom)
        today = dt.date(2026, 8, 23)
        before = eh.budget_left(conn, today=today)
        assert eh.refresh(conn, "MU", today=today) is False
        assert eh.budget_left(conn, today=today) == before - 1

    def test_refresh_never_raises(self, conn, monkeypatch):
        monkeypatch.setattr(eh, "api_key", lambda: "test-key")
        monkeypatch.setattr(eh, "_fetch", lambda _s: (_ for _ in ()).throw(
            ValueError("bad")))
        assert eh.refresh(conn, "MU", today=dt.date(2026, 8, 23)) is False

    def test_a_missing_key_costs_no_budget(self, conn, monkeypatch):
        """Found on the first live run. `_fetch` raises before contacting the
        vendor when no key is configured, so charging the allowance for it
        spends a budget the vendor never saw — and on a machine with no key it
        would drain the whole day in twenty analyses, for nothing.

        Distinct from a fetch that FAILED in flight, which the vendor did
        count and which must still be charged."""
        monkeypatch.setattr(eh, "api_key", lambda: None)
        today = dt.date(2026, 8, 23)
        before = eh.budget_left(conn, today=today)
        assert eh.refresh(conn, "MU", today=today) is False
        assert eh.budget_left(conn, today=today) == before


class TestAThrottleIsNotAnAnswer:
    """Caught on the first live run. Alpha Vantage throttles at 5 calls a
    MINUTE as well as 25 a day, and a throttled reply carries a note instead of
    a `quarterlyEarnings` key. Parsed, that is an empty list — identical to a
    symbol the vendor genuinely does not cover.

    Storing it was the bug: an empty result is REMEMBERED so uncovered symbols
    are not re-asked daily, so one throttle cached "no earnings history for
    NVDA" for 30 days. NVDA has 109 quarters. The distinction has to be made
    from the ENVELOPE, because the parsed rows look the same either way."""

    @pytest.fixture()
    def conn(self, tmp_path):
        return eh.init_db(tmp_path / "eh.db")

    _THROTTLE = json.dumps({"Note": "call frequency is 5 calls per minute"})
    _INFO = json.dumps({"Information": "our standard API rate limit is 25 per day"})
    _EMPTY = json.dumps({"symbol": "ZZZZ", "quarterlyEarnings": []})

    def test_a_throttle_is_recognised(self):
        assert eh.is_transient(self._THROTTLE) is True
        assert eh.is_transient(self._INFO) is True

    def test_a_genuine_empty_answer_is_NOT_transient(self):
        """The vendor said "here is the block, it is empty" — a real answer."""
        assert eh.is_transient(self._EMPTY) is False

    def test_a_populated_answer_is_not_transient(self):
        assert eh.is_transient(_JSON) is False

    def test_unparseable_bodies_are_treated_as_transient(self):
        """An HTML error page is not evidence about coverage."""
        assert eh.is_transient("<html>502</html>") is True
        assert eh.is_transient("") is True

    def test_a_throttled_refresh_does_not_poison_the_cache(self, conn, monkeypatch):
        monkeypatch.setattr(eh, "api_key", lambda: "k")
        monkeypatch.setattr(eh, "_fetch", lambda _s: self._THROTTLE)
        assert eh.refresh(conn, "NVDA", today=dt.date(2026, 8, 23)) is False
        # never asked, as far as the store is concerned - so it will retry
        assert eh.surprise_fractions(conn, "NVDA") is None
        assert eh.is_due(conn, "NVDA") is True

    def test_a_genuinely_uncovered_symbol_IS_remembered(self, conn, monkeypatch):
        monkeypatch.setattr(eh, "api_key", lambda: "k")
        monkeypatch.setattr(eh, "_fetch", lambda _s: self._EMPTY)
        assert eh.refresh(conn, "ZZZZ", today=dt.date(2026, 8, 23)) is False
        assert eh.surprise_fractions(conn, "ZZZZ") == []
        assert eh.is_due(conn, "ZZZZ") is False

    def test_a_throttle_still_spends_its_call(self, conn, monkeypatch):
        """The vendor counted the request even though it refused it."""
        monkeypatch.setattr(eh, "api_key", lambda: "k")
        monkeypatch.setattr(eh, "_fetch", lambda _s: self._THROTTLE)
        today = dt.date(2026, 8, 23)
        before = eh.budget_left(conn, today=today)
        eh.refresh(conn, "NVDA", today=today)
        assert eh.budget_left(conn, today=today) == before - 1
