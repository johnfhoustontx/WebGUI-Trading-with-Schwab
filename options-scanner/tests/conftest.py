"""Shared test fixtures for the options-scanner suite.

Production-DB isolation
-----------------------
⚠ This file used to claim it guaranteed "the real `data/signals.db` is never
touched by the suite". **It did not, and had never done so.** The fixture below
patched `signal_db.DEFAULT_DB_PATH`, but every function in that module is
declared ``def f(..., db_path=DEFAULT_DB_PATH)`` and Python binds a default at
``def`` time — measured 2026-08-28, all 13 signal_db functions still resolved to
the live path after the patch. `paper_account_db` was never patched at all.

The cost: on 2026-07-16 a run wrote 24 synthetic signals (SPY @ 500.00,
QQQ @ 430.00, deltas on an exact 32nd ladder) and 21 rejected paper orders into
BOTH environments' production stores, where they sat for six weeks feeding
backtests. The inert patch has been removed rather than left to imply cover.

**The real guarantee now lives in the repo-root `conftest.py`**, which refuses
any ``sqlite3.connect`` resolving into a live data directory. That sits at a
chokepoint every store shares, so it cannot be defeated by a module nobody
remembered to patch. See that file for the reasoning and the
``@pytest.mark.allow_live_db`` escape hatch.

What survives here is the `record_signals` redirect, which DOES work — it wraps
the function rather than reassigning a module attribute. Without it,
`run_full_scan` tests would hit the root guard and fail; with it they write to a
per-test temp DB. Explicit `db_path` arguments are still honoured.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_production_signal_db(tmp_path, monkeypatch):
    import signal_recorder

    test_db = tmp_path / "isolated_signals.db"
    real_record = signal_recorder.record_signals

    def _redirected(signals, scanner_type, db_path=None, **kwargs):
        # db_path is None only when the caller relied on the production default
        # (the leak path). Send those to the per-test DB; honour explicit paths.
        # **kwargs passes the rest through untouched (e.g. the recorder's `now`),
        # so widening record_signals' signature cannot silently break the suite
        # here with a TypeError that looks nothing like the real change.
        return real_record(signals, scanner_type, db_path=db_path or test_db, **kwargs)

    monkeypatch.setattr(signal_recorder, "record_signals", _redirected)

    # The tracked-structure recorder resolves its default path when CALLED, so
    # the same redirect covers it: a scan under test writes to the per-test DB.
    # ``record_tracked`` is a thin wrapper over this one, so it is covered too.
    real_tracked = signal_recorder.record_tracked_scan

    def _redirected_tracked(by_type, db_path=None, **kwargs):
        return real_tracked(by_type, db_path=db_path or test_db, **kwargs)

    # The one test of the function's OWN default path reaches the real one here.
    _redirected_tracked.__wrapped__ = real_tracked

    monkeypatch.setattr(signal_recorder, "record_tracked_scan", _redirected_tracked)
    yield


@pytest.fixture(autouse=True)
def _scan_inside_the_regular_session(monkeypatch):
    """Pin the clock ``run_full_scan``'s regular-hours gate reads to a weekday
    noon, so a scan test asserts the same thing at 06:00 as at 10:00.

    Without it every test that expects 0-DTE or Swing signals off the fake chain
    would pass during the session and fail outside it — a red baseline that
    depends on the hour, which is the trap this suite's history warns about.
    ``TestSignalsOnlyInRegularHours`` moves the clock itself to test the gate.
    """
    from datetime import datetime

    import scanner_engine

    noon = datetime(2026, 10, 1, 12, 0, tzinfo=scanner_engine.TZ)
    monkeypatch.setattr(scanner_engine, "_signal_clock", lambda: noon)
    yield


@pytest.fixture(autouse=True)
def _no_resting_symbols_between_tests():
    """``gex_collector`` remembers symbols whose chain listed nothing and leaves
    them out of later polls. That memory is module state; one test's empty chain
    must not make another test's poll skip a symbol."""
    try:
        import gex_collector
    except ImportError:          # the engine folder is not on the path yet
        yield
        return
    gex_collector.reset_nothing_listed()
    yield
    gex_collector.reset_nothing_listed()

