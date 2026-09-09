"""The live scan resolves a real earnings date per symbol.

``run_full_scan`` never passed an ``earnings_date`` to ``screen_spreads``, so the
gate at that call site was inert -- ``if earnings_date and ...`` is False when no
date is ever supplied. Every SWING signal the live scanner has ever emitted was
ungated, and the older ``options-scanner/data/earnings_cache.json`` it was
nominally fed from holds ``"date": null`` for all seventeen symbols in it and was
last written 2026-08-29.

Meanwhile ``services/trade_svc/data/earnings_calendar.db`` -- filled nightly and
already read by the income window through ``shared/earnings.py`` -- held
``('ORCL', '2026-09-10')`` from 2026-09-07, two sessions before sixteen ORCL
candidates expiring 2026-09-11 were captured.
"""
import datetime as dt

import pytest

import scanner_engine as se
from shared import earnings as _earn


@pytest.fixture
def calendar(tmp_path):
    """A real store: ORCL reports soon, MSFT's last report is in the past,
    AMD is absent from the vendor's coverage entirely."""
    path = tmp_path / "earnings_calendar.db"
    conn = _earn.init_db(path)
    today = dt.date.today()
    conn.executemany(
        "INSERT INTO earnings (symbol, report_date, fiscal_date_ending, "
        "estimate, recorded_at) VALUES (?,?,?,?,?)",
        [("ORCL", (today + dt.timedelta(days=2)).isoformat(), None, 1.4, "t"),
         ("MSFT", (today - dt.timedelta(days=30)).isoformat(), None, 3.1, "t")])
    conn.commit()
    conn.close()
    return str(path)


def test_an_upcoming_report_is_resolved(calendar):
    got = se.scan_earnings_dates(["ORCL"], db_path=calendar)
    assert got["ORCL"] == (dt.date.today() + dt.timedelta(days=2)).isoformat()


def test_a_symbol_whose_reports_are_all_past_resolves_to_none(calendar):
    assert se.scan_earnings_dates(["MSFT"], db_path=calendar)["MSFT"] is None


def test_a_symbol_the_vendor_does_not_carry_resolves_to_none(calendar):
    """Unknown is not "no earnings", but the gate can only act on a date. The
    honest handling of not_listed is the caller's, not this resolver's."""
    assert se.scan_earnings_dates(["AMD"], db_path=calendar)["AMD"] is None


def test_every_requested_symbol_appears_in_the_map(calendar):
    got = se.scan_earnings_dates(["ORCL", "MSFT", "AMD"], db_path=calendar)
    assert set(got) == {"ORCL", "MSFT", "AMD"}


def test_a_missing_store_degrades_to_no_dates_rather_than_raising(tmp_path):
    """A gate that raises costs the user the whole scan."""
    got = se.scan_earnings_dates(["ORCL"], db_path=str(tmp_path / "absent.db"))
    assert got == {"ORCL": None}


def test_an_unreadable_store_degrades_rather_than_raising(tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a sqlite database")
    assert se.scan_earnings_dates(["ORCL"], db_path=str(junk)) == {"ORCL": None}


def test_the_store_is_opened_once_for_the_whole_scan(calendar, monkeypatch):
    """A 45-symbol watchlist must not mean 45 opens."""
    opens = []
    real = _earn.init_db
    monkeypatch.setattr(_earn, "init_db",
                        lambda p=None: (opens.append(p), real(p))[1])
    se.scan_earnings_dates(["ORCL", "MSFT", "AMD"], db_path=calendar)
    assert len(opens) == 1


def test_no_symbols_opens_nothing(monkeypatch):
    monkeypatch.setattr(_earn, "init_db",
                        lambda p=None: pytest.fail("opened the store for nothing"))
    assert se.scan_earnings_dates([]) == {}


def test_the_suite_never_reaches_the_live_calendar(monkeypatch):
    """``db_path=None`` under pytest returns empty without opening anything.
    The repo-root conftest refuses a connect into a live data dir and
    ``init_db`` would CREATE the store besides -- the same guard
    ``compute._income_earnings`` carries."""
    monkeypatch.setattr(_earn, "init_db",
                        lambda p=None: pytest.fail("opened the live calendar"))
    assert se.scan_earnings_dates(["ORCL"]) == {"ORCL": None}


def test_the_default_path_is_resolved_at_call_time(monkeypatch, calendar):
    """Not bound as a ``def``-time default -- the trap that left signal_db's
    test isolation inert for weeks. Verified with the pytest guard lifted, since
    that guard short-circuits before the default is ever read."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(_earn, "DEFAULT_DB_PATH", calendar)
    got = se.scan_earnings_dates(["ORCL"], db_path=None)
    assert got["ORCL"] == (dt.date.today() + dt.timedelta(days=2)).isoformat()
