"""C2: the manage cycle finally supplies what the ratchet has always needed.

Design: docs/plans/2026-09-12-profit-lock-ladder-design.md.

``_locked_profit_level`` has taken ``ctx["trail_ladder"]`` and
``ctx["peak_pnl_frac"]`` since it was written, and **nothing ever passed them**,
so the ladder was inert regardless of what the TOML said. These tests pin the two
inputs at the seam where they are produced.

⚠ The load-bearing detail is WHICH ``mfe``. The lifecycle branch must use the
value this cycle just computed, not ``pos["mfe"]`` from the fetched row — that one
is a cycle stale, and on the cycle where a trade peaks and then collapses the
difference is locking 50% of the credit versus locking nothing.
"""
import pytest

import paper_engine
import signal_recommender as sr


def test_the_recommender_knows_which_ladder_is_active():
    from shared import trade_mgmt as tm
    assert sr.ACTIVE_TRAIL_LADDER == tm.active_trail_ladder()


# ── the mechanism, driven directly ──────────────────────────────────────────

def _ctx(peak, ladder, credit=1.00, pnl=10.0, armed=True):
    return {"entry_credit": credit, "unrealized_pnl": pnl, "lifecycle": True,
            "be_armed": armed, "be_level": 0.0, "dte_remaining": 20,
            "strategy": "PCS", "trail_ladder": ladder, "peak_pnl_frac": peak}


def test_a_peak_past_the_second_rung_locks_a_quarter_of_the_credit():
    got = sr._locked_profit_level(_ctx(0.70, sr.ACTIVE_TRAIL_LADDER), 100.0)
    assert got == pytest.approx(25.0)


def test_a_peak_past_the_third_rung_locks_half():
    got = sr._locked_profit_level(_ctx(0.85, sr.ACTIVE_TRAIL_LADDER), 100.0)
    assert got == pytest.approx(50.0)


def test_no_peak_locks_nothing_so_an_old_position_keeps_todays_behaviour():
    """Every position opened before this change has no excursion history."""
    ctx = _ctx(None, sr.ACTIVE_TRAIL_LADDER)
    assert sr._locked_profit_level(ctx, 100.0) == 0.0


def test_the_lock_only_ever_RAISES_the_stop_above_break_even():
    """Rule 3 takes ``max(be_level, locked)``, which is why the ladder cannot
    increase loss exposure on any path — the structural half of C2's argument."""
    ctx = _ctx(0.85, sr.ACTIVE_TRAIL_LADDER, pnl=40.0)
    ctx["be_level"] = 5.0
    rec = sr.recommend(ctx)
    assert rec["action"] == "CUT"
    assert rec["code"] == "BREAKEVEN_STOP"


def test_a_position_above_the_lock_is_held():
    ctx = _ctx(0.85, sr.ACTIVE_TRAIL_LADDER, pnl=70.0)
    assert sr.recommend(ctx)["action"] != "CUT"


# ── the lifecycle branch supplies both inputs ───────────────────────────────

def test_the_lifecycle_ctx_carries_the_ladder_and_a_peak(monkeypatch, tmp_path):
    """Driven through ``run_manage_cycle`` rather than asserted on the source: a
    consumer-side guard proves nothing until a test shows the PRODUCER emits the
    shape — the lesson behind the `signal_band` incident."""
    seen = {}

    def _spy(ctx):
        seen.update(ctx)
        return {"action": "HOLD", "reason": "spy", "code": "HOLD"}

    _drive(monkeypatch, tmp_path, _spy, mfe=85.0, entry_credit=1.00, qty=1)
    assert seen.get("trail_ladder") == sr.ACTIVE_TRAIL_LADDER
    assert seen.get("peak_pnl_frac") == pytest.approx(0.85)


def test_the_peak_is_this_cycles_mfe_not_the_stale_row_value(monkeypatch, tmp_path):
    """⚠ The whole point. ``pos["mfe"]`` is the row as fetched, so it lags by one
    cycle; on the cycle where a trade peaks and collapses, using it locks nothing
    where the fresh value locks half the credit."""
    seen = {}

    def _spy(ctx):
        seen.update(ctx)
        return {"action": "HOLD", "reason": "spy", "code": "HOLD"}

    # Stored mfe is stale at 0; THIS cycle's mark is +$85 on a $100 credit.
    _drive(monkeypatch, tmp_path, _spy, mfe=0.0, entry_credit=1.00, qty=1, pnl=85.0)
    assert seen.get("peak_pnl_frac") == pytest.approx(0.85)


def test_a_NON_lifecycle_cycle_supplies_no_ladder(monkeypatch, tmp_path):
    """The driver passes ``lifecycle=False`` and the manual book defaults off, so
    the ratchet must not reach either until the lifecycle is enabled."""
    seen = {}

    def _spy(ctx):
        seen.update(ctx)
        return {"action": "HOLD", "reason": "spy", "code": "HOLD"}

    _drive(monkeypatch, tmp_path, _spy, mfe=85.0, entry_credit=1.00, qty=1, lifecycle=False)
    assert "trail_ladder" not in seen
    assert "peak_pnl_frac" not in seen


def test_a_zero_credit_position_yields_no_peak_rather_than_dividing(monkeypatch, tmp_path):
    """``peak / credit_total`` with a zero credit is the one arithmetic hazard
    here; no credit means no peak fraction, which locks nothing."""
    seen = {}

    def _spy(ctx):
        seen.update(ctx)
        return {"action": "HOLD", "reason": "spy", "code": "HOLD"}

    _drive(monkeypatch, tmp_path, _spy, mfe=85.0, entry_credit=0.0, qty=1)
    assert seen.get("peak_pnl_frac") is None


def _drive(monkeypatch, tmp_path, recommend_spy, *, mfe, entry_credit, qty,
           pnl=10.0, lifecycle=True):
    """Run one ``run_manage_cycle`` pass over a single synthetic open position.

    Everything the cycle touches is stubbed at its own seam, so this needs no
    broker, no proxy and no database.
    """
    import paper_account_db
    import signal_repricer

    pos = {"position_id": 1, "symbol": "SPY", "strategy": "PCS",
           "short_strike": 500.0, "long_strike": 495.0, "width": 5.0,
           "expiration": "2099-12-31", "quantity": qty,
           "entry_credit": entry_credit, "status": "OPEN", "mfe": mfe,
           "mae": 0.0, "be_armed": 1, "entry_short_delta": -0.20}
    monkeypatch.setattr(paper_account_db, "fetch_open_positions", lambda *a, **k: [pos])
    monkeypatch.setattr(paper_account_db, "roll_session_if_needed", lambda *a, **k: None)
    monkeypatch.setattr(paper_account_db, "update_position_mark", lambda *a, **k: None)
    monkeypatch.setattr(paper_account_db, "set_be_armed", lambda *a, **k: None)
    # The cycle ends by testing the session drawdown halt, which reads the account
    # row. Stubbed rather than created so the test stays about the ctx build.
    monkeypatch.setattr(paper_account_db, "should_halt", lambda *a, **k: False)
    monkeypatch.setattr(signal_repricer, "clear_chain_cache", lambda: None)
    monkeypatch.setattr(signal_repricer, "reprice_swing",
                        lambda *a, **k: {"unrealized_pnl": pnl,
                                         "current_value": 0.5,
                                         "current_underlying": 505.0,
                                         "current_short_delta": -0.20})
    monkeypatch.setattr(signal_recommender_module(), "recommend", recommend_spy)
    import datetime as dt
    # ⚠ A tmp_path DB, not ":memory:" — the repo-root conftest resolves a relative
    # sqlite path against the CWD and refuses anything inside a live data dir,
    # which is the guard that exists because this suite once leaked synthetic
    # signals into both environments.
    paper_engine.run_manage_cycle(object(), dt.date(2026, 9, 12),
                                  db_path=tmp_path / "paper.db",
                                  lifecycle=lifecycle)


def signal_recommender_module():
    return sr
