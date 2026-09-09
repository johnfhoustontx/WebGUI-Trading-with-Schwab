"""Tests for the shared earnings-calendar READ path.

The WRITE path (Alpha Vantage fetch/parse/store/refresh) stays in
``services/trade_svc/earnings_calendar.py`` and keeps its own suite. This file
covers only what moved here — opening the store, and the three readers over it
— plus the one property the move exists to protect.

⚠ Every test opens a store under ``tmp_path``. The repo-root ``conftest.py``
refuses ``sqlite3.connect`` into a live data directory and
``services/trade_svc/data`` — where ``EARNINGS_CALENDAR_DB`` lives — is on that
list. The fix for a refusal is a ``tmp_path`` store, never a redirected module
default: that is the approach which had never worked and leaked 24 synthetic
signals into both live environments.
"""
import datetime as dt
import inspect
import sqlite3

import pytest

from shared import earnings


# Pinned rather than relative: every expectation below is a literal date, so a
# fixture keyed off `today` would rot the assertions instead of the data.
_AS_OF = dt.date(2026, 8, 22)


def _seed(conn, rows):
    """Insert (symbol, report_date) pairs directly.

    Deliberately NOT via `trade_svc.store_calendar`: the point of this module
    is that the read path stands on its own store, with no dependency on the
    service that writes it.
    """
    conn.executemany(
        "INSERT INTO earnings (symbol, report_date) VALUES (?, ?)", rows)
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = earnings.init_db(tmp_path / "earnings.db")
    _seed(c, [("NVDA", "2026-08-26"),      # upcoming
              ("OLDCO", "2026-01-05"),     # listed, nothing ahead
              ("X", "2026-05-01"), ("X", "2026-11-04"), ("X", "2026-09-02")])
    yield c
    earnings.close_db(c)


class TestLookup:
    def test_the_nearest_FUTURE_date_wins(self, conn):
        assert conn and earnings.lookup(
            conn, "X", as_of=_AS_OF)["report_date"] == "2026-09-02"

    def test_a_date_already_past_is_never_upcoming(self, conn):
        assert earnings.lookup(conn, "OLDCO", as_of=_AS_OF) is None

    def test_symbol_matching_is_case_and_space_insensitive(self, conn):
        assert earnings.lookup(conn, " nvda ", as_of=_AS_OF) is not None

    def test_an_unlisted_symbol_is_none(self, conn):
        assert earnings.lookup(conn, "NOSUCH", as_of=_AS_OF) is None

    def test_the_default_as_of_is_today(self, conn):
        """Built relative to today on purpose — this is the one test that
        exercises the default, so it cannot use the pinned fixture dates."""
        soon = (dt.date.today() + dt.timedelta(days=14)).isoformat()
        _seed(conn, [("FUT", soon)])
        assert earnings.lookup(conn, "FUT")["report_date"] == soon


class TestCoverageIsNotAbsence:
    """The property the whole split exists to preserve.

    ``days_to_earnings is None`` means two different things — "the vendor
    lists this symbol and it has nothing scheduled ahead" and "the vendor has
    never heard of this symbol" — and only the first is a real "no earnings in
    the window". A gate that reads the second as clear fails OPEN, silently, on
    exactly the names most likely to be traded: measured live 2026-08-22, the
    12-month horizon carried AAPL and GOOGL at 67-68 days out while MSFT, AMZN
    and META — the same late-October cycle — were absent entirely.

    Same failure shape as `LIQUIDITY_THRESHOLDS` answering True for an unknown
    trade type, `min(hi, nan)` returning `hi`, and `signal_band` publishing
    "Strong Bear" for six different ways of having no data.
    """

    def test_a_listed_symbol_with_a_date_ahead_is_upcoming(self, conn):
        assert earnings.coverage(conn, "NVDA", as_of=_AS_OF) == "upcoming"

    def test_a_listed_symbol_whose_dates_are_all_past_is_none_scheduled(self, conn):
        """Trustworthy: the vendor knows OLDCO and has nothing ahead for it."""
        assert earnings.coverage(conn, "OLDCO", as_of=_AS_OF) == "none_scheduled"

    def test_absence_from_the_calendar_is_UNKNOWN_not_clear(self, conn):
        """The load-bearing assertion.

        ⚠ This must stay a THREE-valued comparison. Anyone tempted to
        "simplify" `coverage` to a boolean — or to fold `not_listed` into
        `none_scheduled` because both leave `days_to_earnings` None — breaks
        here, which is the entire reason the test exists. `not_listed` and
        `none_scheduled` are different answers and must never compare equal.
        """
        assert earnings.coverage(conn, "NOSUCH", as_of=_AS_OF) == "not_listed"
        assert (earnings.coverage(conn, "NOSUCH", as_of=_AS_OF)
                != earnings.coverage(conn, "OLDCO", as_of=_AS_OF))

    def test_an_empty_calendar_reports_unknown_for_everything(self, tmp_path):
        """An empty store must not read as a market where nobody reports."""
        c = earnings.init_db(tmp_path / "empty.db")
        try:
            assert earnings.coverage(c, "AAPL") == "not_listed"
            assert earnings.coverage(c, "NVDA") == "not_listed"
        finally:
            earnings.close_db(c)

    def test_a_broken_store_degrades_to_not_listed_not_to_clear(self, conn):
        """"not_listed" is the honest answer for a store that cannot be read:
        it already means "we do NOT know". The alternative — reporting
        `none_scheduled` — would describe a broken store as a clear symbol."""
        conn.close()
        assert earnings.coverage(conn, "NVDA", as_of=_AS_OF) == "not_listed"


class TestDaysToEarnings:
    def test_counts_calendar_days_to_the_next_report(self, conn):
        assert earnings.days_to_earnings(conn, "NVDA", as_of=_AS_OF) == 4

    def test_a_report_today_is_zero_not_none(self, conn):
        """Zero is the most gate-worthy value there is — it must not collapse
        into the same None that means "we have no idea"."""
        assert earnings.days_to_earnings(
            conn, "NVDA", as_of=dt.date(2026, 8, 26)) == 0

    def test_an_unlisted_symbol_is_none(self, conn):
        assert earnings.days_to_earnings(conn, "NOSUCH", as_of=_AS_OF) is None


class TestTheReadPathDoesNotDependOnTheCallersConnection:
    """Found in prod 2026-08-23 and carried over with the move.

    The readers index rows by column name, so a caller who opened the store
    with a plain `sqlite3.connect` — no row factory — used to get TypeError,
    which `days_to_earnings` swallowed into None. Every symbol then reported
    "no earnings", which is indistinguishable from an empty calendar. Now that
    a SECOND service reads this store, the odds of such a caller went up, so
    the guard matters more here than it did in `trade_svc`.
    """

    @pytest.fixture
    def db_path(self, tmp_path):
        c = earnings.init_db(tmp_path / "earnings.db")
        _seed(c, [("NVDA", "2026-08-26")])
        c.close()
        return tmp_path / "earnings.db"

    def _raw(self, db_path):
        return sqlite3.connect(str(db_path))

    def test_lookup_works_without_a_row_factory(self, db_path):
        row = earnings.lookup(self._raw(db_path), "NVDA", as_of=_AS_OF)
        assert row is not None and row["report_date"] == "2026-08-26"

    def test_days_to_earnings_works_without_a_row_factory(self, db_path):
        assert earnings.days_to_earnings(
            self._raw(db_path), "NVDA", as_of=_AS_OF) == 4

    def test_coverage_works_without_a_row_factory(self, db_path):
        assert earnings.coverage(
            self._raw(db_path), "NVDA", as_of=_AS_OF) == "upcoming"


class TestTheStoreHelper:
    def test_init_db_resolves_its_default_at_CALL_time(self):
        """`db_path=None`, not `db_path=DEFAULT_DB_PATH`.

        Python binds a default at `def` time, so the second shape makes the
        path unpatchable — which is exactly how `signal_db`'s protection had
        never worked. A source-level check because the behavioural one would
        have to open the live store to observe it, which the root conftest
        (rightly) refuses."""
        assert inspect.signature(
            earnings.init_db).parameters["db_path"].default is None

    def test_init_db_creates_the_schema_on_a_missing_file(self, tmp_path):
        """A reader arriving before the nightly write must get an empty
        calendar, not an exception — and "empty" then reads as `not_listed`."""
        c = earnings.init_db(tmp_path / "sub" / "new.db")
        try:
            assert c.execute("SELECT COUNT(*) FROM earnings").fetchone()[0] == 0
        finally:
            earnings.close_db(c)

    def test_close_db_never_raises(self, tmp_path):
        c = earnings.init_db(tmp_path / "e.db")
        earnings.close_db(c)
        earnings.close_db(c)          # already closed — still no raise


class TestThereIsExactlyOneImplementation:
    """`trade_svc` re-exports rather than keeping a copy.

    Two implementations of a safety property is how the three-valued
    `coverage` contract would quietly become two-valued on one side only.
    """

    def test_trade_svc_re_exports_the_same_objects(self):
        from services.trade_svc import earnings_calendar as ec
        for name in ("lookup", "coverage", "days_to_earnings",
                     "init_db", "close_db"):
            assert getattr(ec, name) is getattr(earnings, name), name
