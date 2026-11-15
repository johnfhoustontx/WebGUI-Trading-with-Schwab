"""The profit trail and the quiet windows in the rule engine.

Design: docs/plans/2026-11-15-profit-trail-design.md. Once a trade's best profit
reaches ``trail_start`` of its base it is closed when it falls ``trail_giveback``
of that base below the best, never under break-even; the protective stops wait
out the quiet windows, the hard stops do not.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import paper_engine
import signal_recommender as sr

CT = ZoneInfo("America/Chicago")
MIDDAY = dt.datetime(2026, 11, 16, 10, 0, tzinfo=CT)
OPENING = dt.datetime(2026, 11, 16, 8, 35, tzinfo=CT)
CONF = {"trail_start": 0.25, "trail_giveback": 0.10,
        "quiet_windows": [(dt.time(8, 30), dt.time(8, 45)),
                          (dt.time(14, 45), dt.time(15, 0))],
        "captured_at_target": "close", "captured": True, "account": True,
        "ledger": True, "ledger_self_managed": True}


@pytest.fixture(autouse=True)
def _conf(monkeypatch):
    """Pin the settings: a test of the rule must not move with the shipped TOML."""
    monkeypatch.setattr(sr._trade_mgmt, "protect", lambda: dict(CONF))


def _credit(pnl, peak, when=MIDDAY, **kw):
    """A $1.00-credit put spread ($100 a contract), far from any other stop."""
    return {"entry_credit": 1.00, "strategy": "PCS", "dte_remaining": 10,
            "current_short_delta": -0.10, "entry_short_delta": -0.15,
            "unrealized_pnl": pnl, "peak_pnl": peak, "mark_time": when, **kw}


# ── the credit side ─────────────────────────────────────────────────────────

def test_a_trade_that_gives_back_the_trail_is_closed_at_a_profit():
    rec = sr.recommend(_credit(pnl=19.0, peak=30.0))   # stop at 30 - 10 = 20
    assert rec["code"] == "TRAIL_STOP"
    assert rec["action"] == "TAKE_PROFIT"


def test_a_trade_above_the_trail_is_held():
    assert sr.recommend(_credit(pnl=21.0, peak=30.0))["code"] == "HOLD"


def test_the_trail_is_not_armed_below_its_start():
    """A best of 24% of the credit never reached the 25% start."""
    assert sr.recommend(_credit(pnl=5.0, peak=24.0))["code"] == "HOLD"


def test_the_target_still_takes_the_profit():
    rec = sr.recommend(_credit(pnl=52.0, peak=55.0))
    assert rec["code"] == "TARGET_HIT"


def test_the_trail_never_sits_below_break_even_plus_commissions(monkeypatch):
    """With a low start the best minus the give-back is under the commissions;
    be_level is the floor whatever the settings."""
    monkeypatch.setattr(sr._trade_mgmt, "protect",
                        lambda: dict(CONF, trail_start=0.05, trail_giveback=0.04))
    rec = sr.recommend(_credit(pnl=2.0, peak=6.0, be_level=2.6))
    assert rec["code"] == "TRAIL_STOP"
    assert rec["action"] == "TAKE_PROFIT"


def test_the_trail_waits_out_the_opening_window():
    """The measured reason: the opening marks are priced off wide quotes."""
    assert sr.recommend(_credit(pnl=19.0, peak=30.0, when=OPENING))["code"] == "HOLD"


def test_the_break_even_stop_waits_out_the_window_too():
    ctx = _credit(pnl=-1.0, peak=None, when=OPENING, be_armed=True, be_level=2.6)
    assert sr.recommend(ctx)["code"] == "HOLD"
    ctx["mark_time"] = MIDDAY
    assert sr.recommend(ctx)["code"] == "BREAKEVEN_STOP"


def test_the_money_stop_still_fires_in_a_quiet_window():
    """Only the PROTECTIVE stops wait; a real blow-up is not deferred."""
    rec = sr.recommend(_credit(pnl=-250.0, peak=30.0, when=OPENING))
    assert rec["code"] == "MONEY_STOP"


def test_a_mark_with_no_time_is_never_quiet():
    """A caller that does not say when gets the stops it always had."""
    rec = sr.recommend(_credit(pnl=19.0, peak=30.0, when=None))
    assert rec["code"] == "TRAIL_STOP"


def test_a_missing_pnl_is_never_read_as_zero_by_the_trail():
    """``recommend`` reads a missing P&L as 0 for its other rules; 0 sits under
    every armed trail level and would close a trade that was simply not marked."""
    assert sr.recommend(_credit(pnl=None, peak=30.0))["code"] != "TRAIL_STOP"


def test_no_peak_means_no_trail():
    """Every position before this change, and every caller whose book is off."""
    ctx = _credit(pnl=1.0, peak=None)
    assert sr.recommend(ctx)["code"] == "HOLD"
    del ctx["peak_pnl"], ctx["mark_time"]
    assert sr.recommend(ctx)["code"] == "HOLD"


def test_the_higher_of_the_trail_and_the_ladder_governs():
    """Riding past the target (be_armed) with a best of 85%: the ladder locks 50,
    the trail 75. A mark of 70 is under the trail, so it is the trail's exit."""
    ctx = _credit(pnl=70.0, peak=85.0, lifecycle=True, be_armed=True, be_level=2.6,
                  trail_ladder=sr.RATCHET_TRAIL_LADDER, peak_pnl_frac=0.85)
    assert sr.recommend(ctx)["code"] == "TRAIL_STOP"


def test_a_start_of_zero_turns_the_trail_off(monkeypatch):
    monkeypatch.setattr(sr._trade_mgmt, "protect",
                        lambda: dict(CONF, trail_start=0.0))
    assert sr.recommend(_credit(pnl=19.0, peak=30.0))["code"] == "HOLD"


# ── the debit side and protect_only ─────────────────────────────────────────

def _debit(pnl, peak, **kw):
    """A long call that cost $2.00 ($200 a contract) with no max profit."""
    return {"strategy": "LONG_CALL", "direction": "DEBIT", "entry_credit": -2.00,
            "entry_debit": 200.0, "max_profit": None, "dte_remaining": 30,
            "dte_at_entry": 30, "unrealized_pnl": pnl, "peak_pnl": peak,
            "mark_time": MIDDAY, **kw}


def test_a_debit_trail_is_measured_on_the_debit_paid():
    """Start 25% of $200 = $50; give-back 10% = $20 below the best."""
    assert sr.recommend(_debit(pnl=59.0, peak=80.0))["code"] == "TRAIL_STOP"
    assert sr.recommend(_debit(pnl=61.0, peak=80.0))["code"] == "HOLD"
    assert sr.recommend(_debit(pnl=10.0, peak=45.0))["code"] == "HOLD"


def test_protect_only_applies_the_trail_and_nothing_else():
    """The Ledger's own credit trades get the trail and no other automatic exit:
    a loss past the money stop is still HOLD here."""
    assert sr.protect_only(_credit(pnl=19.0, peak=30.0))["code"] == "TRAIL_STOP"
    assert sr.protect_only(_credit(pnl=-500.0, peak=10.0))["code"] == "HOLD"
    assert sr.protect_only(_credit(pnl=None, peak=30.0))["code"] == "HOLD"
    assert sr.protect_only(_debit(pnl=59.0, peak=80.0))["code"] == "TRAIL_STOP"


def test_protect_inputs_is_empty_for_a_book_that_is_off(monkeypatch):
    monkeypatch.setattr(sr._trade_mgmt, "protect", lambda: dict(CONF, account=False))
    assert sr.protect_inputs("account", 30.0, MIDDAY) == {}
    assert sr.protect_inputs("captured", 30.0, MIDDAY) == {"peak_pnl": 30.0,
                                                           "mark_time": MIDDAY}


def test_the_trail_is_a_close_code():
    assert sr.auto_close_reason("TRAIL_STOP") == "TRAIL_STOP"


# ── the paper Account passes the inputs in BOTH modes ───────────────────────

@pytest.mark.parametrize("lifecycle", [True, False])
def test_the_account_hands_the_trail_its_per_contract_best(monkeypatch, tmp_path,
                                                          lifecycle):
    """``mfe`` is position-level; the rule compares per contract, so a 3-lot's
    best of $90 is $30 a contract. Both modes: the trail protects a winner short
    of the target, which the plain take-profit never did."""
    import paper_account_db
    import signal_repricer
    seen = {}

    def _spy(ctx):
        seen.update(ctx)
        return {"action": "HOLD", "reason": "spy", "code": "HOLD"}

    pos = {"position_id": 1, "symbol": "SPY", "strategy": "PCS",
           "short_strike": 500.0, "long_strike": 495.0, "width": 5.0,
           "expiration": "2099-12-31", "quantity": 3, "entry_credit": 1.00,
           "status": "OPEN", "mfe": 90.0, "mae": 0.0, "be_armed": 0,
           "entry_short_delta": -0.20}
    monkeypatch.setattr(paper_account_db, "fetch_open_positions", lambda *a, **k: [pos])
    monkeypatch.setattr(paper_account_db, "roll_session_if_needed", lambda *a, **k: None)
    monkeypatch.setattr(paper_account_db, "update_position_mark", lambda *a, **k: None)
    monkeypatch.setattr(paper_account_db, "set_be_armed", lambda *a, **k: None)
    monkeypatch.setattr(paper_account_db, "should_halt", lambda *a, **k: False)
    monkeypatch.setattr(signal_repricer, "clear_chain_cache", lambda: None)
    monkeypatch.setattr(signal_repricer, "reprice_swing",
                        lambda *a, **k: {"unrealized_pnl": 10.0, "current_value": 0.9,
                                         "current_underlying": 505.0,
                                         "current_short_delta": -0.20})
    monkeypatch.setattr(sr, "recommend", _spy)
    paper_engine.run_manage_cycle(object(), dt.date(2026, 11, 16),
                                  db_path=tmp_path / "paper.db", now_ct=MIDDAY,
                                  lifecycle=lifecycle)
    assert seen.get("peak_pnl") == pytest.approx(30.0)
    assert seen.get("mark_time") == MIDDAY
