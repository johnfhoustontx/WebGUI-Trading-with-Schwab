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
    yield
