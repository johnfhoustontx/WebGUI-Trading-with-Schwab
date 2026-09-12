"""C3: the scan records a daily volatility snapshot, at no Schwab cost.

Design: docs/plans/2026-09-12-iv-history-capture-design.md.

``run_full_scan`` already fetches a **+20..+45 DTE** chain per symbol and hands it
to ``run_iv_analysis``. That window brackets 30 DTE for every symbol measured, so
the constant-maturity reading the store needs is computable from a chain already
in hand. The collector's ``today..+7`` chain would clamp every time, which is why
the snapshot does not ride on the collector.

Three things these tests hold down:

* the write happens, per symbol, from the chain the scan already has;
* a **clamped** reading is refused rather than stored — one 7-day reading filed as
  a 30-day one pins the bottom of the ranked range for a year;
* the whole thing is inert under failure. A volatility snapshot must never be able
  to break a scan, and it must never make a Schwab call of its own.
"""
import datetime as _dt

import pytest

import iv_analysis
import scanner_engine

from shared import iv_history as ivh

from tests.test_scanner_engine import fake_client  # noqa: F401  (fixture)

SYMBOLS = ["SPY", "QQQ"]


@pytest.fixture
def iv_window_chain(monkeypatch):
    """Give the +20..+45 DTE request a real ladder.

    ⚠ The shared ``fake_client`` fixture populates only the 0-DTE (1 DTE) and
    swing (7 DTE) buckets — its own docstring says so — and returns an EMPTY chain
    for the IV window. That is fine for the ~40 tests that share it and useless
    here, so this substitutes a ladder for that one window rather than widening a
    fixture those tests depend on.

    The ladder is 20 / 27 / 34 DTE, which is what live chains actually carry in
    this window (measured 2026-09-12 across ten symbols) — so it brackets 30 and
    exercises the *interpolated* basis, the case production hits.
    """
    real = scanner_engine.fetch_option_chain
    spots = {"SPY": 500.0, "QQQ": 430.0}

    def _chain_for(symbol):
        spot = spots.get(symbol, 100.0)
        puts, calls = {}, {}
        for dte, iv in ((20, 25.0), (27, 26.0), (34, 27.0)):
            key = f"2026-10-0{dte % 9 + 1}:{dte}"
            puts[key] = {f"{spot:.1f}": [{"volatility": iv, "bid": 1.0, "ask": 1.1}]}
            calls[key] = {f"{spot:.1f}": [{"volatility": iv, "bid": 1.0, "ask": 1.1}]}
        return {"underlyingPrice": spot, "putExpDateMap": puts,
                "callExpDateMap": calls, "status": "SUCCESS"}

    def _patched(client, symbol, from_date=None, to_date=None, **kw):
        today = _dt.date.today()
        if from_date and (from_date - today).days >= 20:
            return _chain_for(symbol)
        return real(client, symbol, from_date=from_date, to_date=to_date, **kw)

    monkeypatch.setattr(scanner_engine, "fetch_option_chain", _patched)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the snapshot at a temp DB and hand back a live connection.

    ⚠ The repo-root conftest refuses a ``sqlite3.connect`` into a live data
    directory, so a test that forgot this would fail loudly rather than write into
    the real store — which is exactly the guard that exists because this suite
    once leaked 24 synthetic signals into both environments.
    """
    db = tmp_path / "iv.db"
    monkeypatch.setattr(scanner_engine, "IV_HISTORY_DB", db)
    yield db


def _rows(db):
    conn = ivh.init_db(db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT symbol, snapshot_date, spot, cm30_iv, front_iv, front_dte "
            "FROM iv_snapshots ORDER BY symbol")]
    finally:
        ivh.close_db(conn)


def test_a_scan_records_one_snapshot_per_symbol(fake_client, store,  # noqa: F811
                                               iv_window_chain):
    scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    got = _rows(store)
    assert {r["symbol"] for r in got} == {"SPY", "QQQ"}


def test_the_snapshot_carries_a_constant_maturity_iv_and_a_spot(fake_client,  # noqa: F811
                                                                store,
                                                                iv_window_chain):
    scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    for row in _rows(store):
        assert row["cm30_iv"] and row["cm30_iv"] > 0, row
        assert row["spot"] and row["spot"] > 0, row


def test_a_second_scan_the_same_day_upserts_rather_than_duplicating(fake_client,  # noqa: F811
                                                                    store,
                                                                    iv_window_chain):
    """The scan writes on every pass, so the day's LAST reading is kept — a
    consistent late-session basis, and what an intraday reader wants."""
    scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    assert len(_rows(store)) == 2


def test_a_CLAMPED_reading_is_refused(fake_client, store, monkeypatch):  # noqa: F811
    """The load-bearing rule. A ladder that does not bracket 30 DTE yields the
    nearest tenor's IV wearing a 30-day name; storing it would corrupt the range
    the rank is measured against, invisibly."""
    monkeypatch.setattr(ivh, "cm30_from_chain", lambda *a, **k: (36.0, "clamped"))
    scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    assert _rows(store) == []


def test_an_INTERPOLATED_reading_is_accepted(fake_client, store,  # noqa: F811
                                             iv_window_chain, monkeypatch):
    monkeypatch.setattr(ivh, "cm30_from_chain", lambda *a, **k: (26.0, "interpolated"))
    scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    assert all(r["cm30_iv"] == pytest.approx(26.0) for r in _rows(store))


def test_an_EXACT_reading_is_accepted(fake_client, store,  # noqa: F811
                                      iv_window_chain, monkeypatch):
    monkeypatch.setattr(ivh, "cm30_from_chain", lambda *a, **k: (26.0, "exact"))
    scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    assert _rows(store)


def test_no_reading_at_all_writes_nothing(fake_client, store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ivh, "cm30_from_chain", lambda *a, **k: (None, None))
    scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    assert _rows(store) == []


def test_a_failing_store_does_not_break_the_scan(fake_client, store, monkeypatch):  # noqa: F811
    """The whole point of the guard. A scan is the app's core product; a
    volatility snapshot is bookkeeping."""
    def _boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ivh, "record_snapshot", _boom)
    res = scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    assert "signals_0dte" in res and "iv_data" in res


def test_a_failing_LADDER_does_not_break_the_scan(fake_client, store, monkeypatch):  # noqa: F811
    def _boom(*a, **k):
        raise RuntimeError("bad chain")

    monkeypatch.setattr(ivh, "cm30_from_chain", _boom)
    res = scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    assert "signals_0dte" in res


def test_the_snapshot_makes_no_chain_fetch_of_its_own(fake_client, store,  # noqa: F811
                                                      monkeypatch):
    """⚠ Zero Schwab cost is the design's central claim, and the cheapest way to
    break it is a well-meaning refetch for a wider window. Counted at the engine's
    own fetch seam, with the snapshot on and off."""
    calls = []
    real = scanner_engine.fetch_option_chain

    def _counted(client, symbol, from_date=None, to_date=None, **kw):
        calls.append((symbol, from_date, to_date))
        return real(client, symbol, from_date=from_date, to_date=to_date, **kw)

    monkeypatch.setattr(scanner_engine, "fetch_option_chain", _counted)
    scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    with_snapshot = len(calls)

    calls.clear()
    monkeypatch.setattr(ivh, "cm30_from_chain", lambda *a, **k: (None, None))
    scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    assert with_snapshot == len(calls)


def test_the_realized_vol_series_is_backfilled_from_the_scans_own_history(
        fake_client, store, iv_window_chain):  # noqa: F811
    """``run_full_scan`` already fetches a year of daily bars per symbol, so the
    RV series — which needs no waiting, unlike IV — costs nothing either. It is
    what makes ``rv_rank`` and a derived VRP work TODAY rather than in a year."""
    scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    conn = ivh.init_db(store)
    try:
        n = conn.execute("SELECT COUNT(*) FROM rv_history").fetchone()[0]
    finally:
        ivh.close_db(conn)
    assert n > 0


def test_the_store_path_is_the_repo_constant():
    """So prod, dev and a test all resolve the same way, and a stray relative
    default cannot reappear."""
    from repo_paths import IV_HISTORY_DB
    assert scanner_engine.IV_HISTORY_DB == IV_HISTORY_DB


def test_iv_analysis_is_untouched_by_this(fake_client, store):  # noqa: F811
    """⚠ The scan's own ``iv_rank`` is the HV-based VRP proxy and STAYS the
    selection input. This change records a series; it does not feed one. Acting on
    7 samples would be the unmeasured change this audit keeps catching."""
    res = scanner_engine.run_full_scan(fake_client, symbols=SYMBOLS)
    for sym in SYMBOLS:
        assert sym in res["iv_data"]
    assert hasattr(iv_analysis, "calc_iv_rank_percentile")
