"""C2: the captured manage cycle supplies the ladder and the peak.

Design: docs/plans/2026-09-12-profit-lock-ladder-design.md.

``build_mark`` has accepted ``trail_ladder`` and ``peak_pnl_frac`` as keyword
arguments since it was written, and the captured cycle never passed them — so the
profit-lock ladder was inert on the one book that actually arms a break-even stop
today. The peak comes from ``signal_db.peak_unrealized``: one
``MAX(unrealized_pnl)`` over the marks this cycle is already writing.
"""
import pytest

from services.options_svc import compute  # noqa: F401  (sys.path glue)

import signal_db  # noqa: E402
import signal_recommender  # noqa: E402

from shared import trade_mgmt as tm  # noqa: E402


def test_peak_unrealized_returns_the_best_mark(tmp_path):
    """The marks are written directly: ``signal_marks`` carries a FOREIGN KEY to
    ``signals``, and this test is about the aggregate, not the parent row."""
    db = tmp_path / "signals.db"
    signal_db.init_db(db)
    conn = signal_db.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        for i, pnl in enumerate((10.0, 85.0, -5.0, 40.0)):
            conn.execute(
                "INSERT INTO signal_marks (signal_id, mark_ts, unrealized_pnl) "
                "VALUES (?, ?, ?)", ("abc", "2026-09-12T10:0%d:00" % i, pnl))
        conn.commit()
    finally:
        conn.close()
    assert signal_db.peak_unrealized("abc", db_path=db) == pytest.approx(85.0)


def test_peak_unrealized_is_None_for_a_signal_with_no_marks(tmp_path):
    """Which keeps today's plain break-even behaviour — every signal captured
    before this change has no peak on its first managed cycle."""
    db = tmp_path / "signals.db"
    signal_db.init_db(db)
    assert signal_db.peak_unrealized("nope", db_path=db) is None


def test_peak_unrealized_never_raises_on_a_missing_store(tmp_path):
    """It runs inside a manage cycle that must not die over a read."""
    assert signal_db.peak_unrealized("x", db_path=tmp_path / "absent.db") is None


def test_the_captured_cycle_threads_both_into_build_mark(monkeypatch):
    """Driven from the PRODUCER: asserting on ``build_mark``'s signature proves
    nothing, since it has always accepted these."""
    seen = {}
    real = signal_recommender.build_mark

    def _spy(row, rep, now, **kw):
        seen.update(kw)
        return real(row, rep, now, **kw)

    monkeypatch.setattr(signal_recommender, "build_mark", _spy)
    monkeypatch.setattr(signal_db, "peak_unrealized", lambda *a, **k: 85.0)
    _drive_captured(monkeypatch)

    assert seen.get("trail_ladder") == tm.active_trail_ladder()
    assert seen.get("peak_pnl_frac") == pytest.approx(0.85)


def test_a_signal_with_no_peak_passes_None_rather_than_zero(monkeypatch):
    """0.0 would clear the ladder's first rung's peak test at lock 0.0 — harmless
    today, and wrong the moment a rung locks a positive fraction at peak 0."""
    seen = {}
    real = signal_recommender.build_mark

    def _spy(row, rep, now, **kw):
        seen.update(kw)
        return real(row, rep, now, **kw)

    monkeypatch.setattr(signal_recommender, "build_mark", _spy)
    monkeypatch.setattr(signal_db, "peak_unrealized", lambda *a, **k: None)
    _drive_captured(monkeypatch)
    assert seen.get("peak_pnl_frac") is None


def test_a_zero_credit_signal_yields_no_peak_fraction(monkeypatch):
    seen = {}
    real = signal_recommender.build_mark

    def _spy(row, rep, now, **kw):
        seen.update(kw)
        return real(row, rep, now, **kw)

    monkeypatch.setattr(signal_recommender, "build_mark", _spy)
    monkeypatch.setattr(signal_db, "peak_unrealized", lambda *a, **k: 85.0)
    _drive_captured(monkeypatch, entry_credit=0.0)
    assert seen.get("peak_pnl_frac") is None


def _drive_captured(monkeypatch, entry_credit=1.00):
    """One pass of the captured manage cycle over a single open signal."""
    import signal_repricer

    row = {"signal_id": "abc", "symbol": "SPY", "strategy": "PCS",
           "short_strike": 500.0, "long_strike": 495.0, "width": 5.0,
           "expiration": "2099-12-31", "entry_credit": entry_credit,
           "entry_max_loss": 4.0, "be_armed": 1, "dte_at_entry": 30,
           "scanner_type": "SWING", "entry_score": 60}
    monkeypatch.setattr(signal_db, "get_open_signals_with_latest_mark",
                        lambda *a, **k: [dict(row)])
    monkeypatch.setattr(signal_db, "insert_mark", lambda *a, **k: None)
    monkeypatch.setattr(signal_db, "set_be_armed", lambda *a, **k: None)
    monkeypatch.setattr(signal_repricer, "reprice_swing",
                        lambda *a, **k: {"unrealized_pnl": 40.0,
                                         "current_value": 0.6,
                                         "current_underlying": 505.0,
                                         "current_short_delta": -0.20,
                                         "error": None})
    compute.run_captured_manage_cycle()


def test_a_FAILING_peak_read_does_not_cost_the_signal_its_exit(monkeypatch):
    """⚠ The robustness bug this guard closes, found by three existing tests.

    The peak read sits inside the cycle's per-signal ``try``, so any failure there
    — a renamed helper, a locked store — aborted the rest of that signal's
    management for the cycle, **including its exits**. The ladder is an
    enhancement; losing it must never cost a position its stop.
    """
    seen = {}
    real = signal_recommender.build_mark

    def _spy(row, rep, now, **kw):
        seen.update(kw)
        return real(row, rep, now, **kw)

    def _boom(*a, **k):
        raise AttributeError("peak_unrealized went away")

    monkeypatch.setattr(signal_recommender, "build_mark", _spy)
    monkeypatch.setattr(signal_db, "peak_unrealized", _boom)
    _drive_captured(monkeypatch)

    # The mark was still built — management continued — with no lock applied.
    assert seen.get("trail_ladder") == tm.active_trail_ladder()
    assert seen.get("peak_pnl_frac") is None
