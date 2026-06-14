"""Shared test fixtures + safety guards for the options-scanner suite.

Production-DB isolation
-----------------------
`scanner_engine.run_full_scan` persists results via
`signal_recorder.record_signals(...)` with **no** db_path, so it falls back to
`signal_db.DEFAULT_DB_PATH` — the real `data/signals.db`. Tests that exercise
`run_full_scan` (e.g. test_directional_screening's `test_run_full_scan_*`,
which scan SPY at a synthetic spot of 500) therefore leaked fixture signals
(SPY spreads around strike ~500) straight into the production database on every
run.

This autouse fixture redirects every *default-path* `record_signals` call to a
per-test temp DB while still honouring any explicit `db_path` a test passes, so
no test can write to the production store. It is defense-in-depth: individual
tests should still isolate their own state, but this guarantees the real
`data/signals.db` is never touched by the suite.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_production_signal_db(tmp_path, monkeypatch):
    import signal_db
    import signal_recorder

    test_db = tmp_path / "isolated_signals.db"
    monkeypatch.setattr(signal_db, "DEFAULT_DB_PATH", test_db)

    real_record = signal_recorder.record_signals

    def _redirected(signals, scanner_type, db_path=None):
        # db_path is None only when the caller relied on the production default
        # (the leak path). Send those to the per-test DB; honour explicit paths.
        return real_record(signals, scanner_type, db_path=db_path or test_db)

    monkeypatch.setattr(signal_recorder, "record_signals", _redirected)
    yield
