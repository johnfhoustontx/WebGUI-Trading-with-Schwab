import signal_recommender as rec


def _ctx(credit=1.0, pnl=0.0, short_delta=0.10, dte_remaining=10,
         current_score=60, entry_score=55, entry_short_delta=0.10):
    return {
        "entry_credit": credit,
        "unrealized_pnl": pnl,
        "current_short_delta": short_delta,
        "entry_short_delta": entry_short_delta,
        "dte_remaining": dte_remaining,
        "current_score": current_score,
        "entry_score": entry_score,
    }


def test_take_profit_at_50pct():
    r = rec.recommend(_ctx(credit=1.0, pnl=50.0))
    assert r["action"] == "TAKE_PROFIT"


def test_take_profit_just_under_does_not_trigger():
    r = rec.recommend(_ctx(credit=1.0, pnl=49.99))
    assert r["action"] == "HOLD"


def test_take_profit_beyond_threshold():
    r = rec.recommend(_ctx(credit=1.0, pnl=80.0))
    assert r["action"] == "TAKE_PROFIT"


def test_cut_at_2x_credit_loss():
    r = rec.recommend(_ctx(credit=1.0, pnl=-200.0))
    assert r["action"] == "CUT"
    assert "stop" in r["reason"].lower() or "2x" in r["reason"].lower()


def test_cut_just_under_stop_is_hold():
    r = rec.recommend(_ctx(credit=1.0, pnl=-199.99))
    assert r["action"] == "HOLD"


def test_cut_on_delta_drift_positive():
    # entry 0.10, current 0.36 -> drift 0.26 >= DELTA_DRIFT (0.12) -> CUT
    r = rec.recommend(_ctx(short_delta=0.36))
    assert r["action"] == "CUT"
    assert "delta" in r["reason"].lower()


def test_cut_on_delta_drift_negative():
    r = rec.recommend(_ctx(short_delta=-0.36))
    assert r["action"] == "CUT"


def test_hold_when_drift_under_threshold():
    # entry 0.10, current 0.20 -> drift 0.10 < DELTA_DRIFT (0.12) -> HOLD
    r = rec.recommend(_ctx(short_delta=0.20))
    assert r["action"] == "HOLD"


def test_delta_stop_is_relative_to_entry():
    # entry 0.20, current 0.30 -> drift 0.10 < 0.12 -> not a delta cut
    r = rec.recommend(_ctx(short_delta=0.30, entry_short_delta=0.20))
    assert r["code"] != "DELTA_STOP"


def test_delta_stop_trips_on_drift_from_entry():
    # entry 0.20, current 0.33 -> drift 0.13 >= 0.12 -> DELTA_STOP
    r = rec.recommend(_ctx(short_delta=0.33, entry_short_delta=0.20))
    assert r["code"] == "DELTA_STOP"


def test_delta_hard_ceiling_trips_regardless_of_entry():
    # aggressive entry 0.40, current 0.46 -> drift only 0.06 but >= 0.45 ceiling
    r = rec.recommend(_ctx(short_delta=0.46, entry_short_delta=0.40))
    assert r["code"] == "DELTA_STOP"


def test_delta_absolute_fallback_when_entry_unknown():
    """Callers that omit entry_short_delta (e.g. the paper engine) keep the
    prior absolute 0.35 breach — 0.30 holds, 0.36 cuts."""
    ctx = _ctx(short_delta=0.30)
    ctx.pop("entry_short_delta")
    assert rec.recommend(ctx)["action"] == "HOLD"
    ctx2 = _ctx(short_delta=0.36)
    ctx2.pop("entry_short_delta")
    assert rec.recommend(ctx2)["code"] == "DELTA_STOP"


def test_cut_on_low_dte_and_underwater():
    r = rec.recommend(_ctx(dte_remaining=2, pnl=-10.0))
    assert r["action"] == "CUT"


def test_low_dte_profitable_is_hold():
    r = rec.recommend(_ctx(dte_remaining=2, pnl=10.0))
    assert r["action"] == "HOLD"


def test_hold_includes_score_drift_note():
    r = rec.recommend(_ctx(entry_score=55, current_score=62))
    assert r["action"] == "HOLD"
    assert "55" in r["reason"] and "62" in r["reason"] and "+7" in r["reason"]


def test_hold_when_current_score_missing_still_works():
    ctx = _ctx()
    ctx["current_score"] = None
    r = rec.recommend(ctx)
    assert r["action"] == "HOLD"


def test_take_profit_beats_delta_breach():
    r = rec.recommend(_ctx(credit=1.0, pnl=60.0, short_delta=-0.40))
    assert r["action"] == "TAKE_PROFIT"


def test_take_profit_beats_low_dte():
    r = rec.recommend(_ctx(credit=1.0, pnl=60.0, dte_remaining=1))
    assert r["action"] == "TAKE_PROFIT"


def test_short_delta_none_does_not_trigger_cut():
    ctx = _ctx()
    ctx["current_short_delta"] = None
    r = rec.recommend(ctx)
    assert r["action"] == "HOLD"


# ────────────────────────────────────────────────────────────────────────
# build_mark — shared mark-row composer used by dashboard + EOD pipeline
# ────────────────────────────────────────────────────────────────────────

from datetime import datetime
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Chicago")


def _signal_row(**overrides):
    base = {
        "signal_id": "abc123",
        "symbol": "SPY",
        "strategy": "PCS",
        "short_strike": 500,
        "long_strike": 495,
        "call_short": None,
        "call_long": None,
        "width": 5,
        "expiration": "2030-01-15",
        "dte_at_entry": 7,
        "entry_credit": 1.0,
        "entry_max_loss": 4.0,
        "entry_score": 60,
        "entry_grade": "B",
        "entry_short_delta": -0.15,
        "entry_net_theta": 0.5,
        "entry_iv_rank": 40,
        "entry_underlying": 500.0,
        "status": "OPEN",
    }
    base.update(overrides)
    return base


def _repricer_ok(**overrides):
    base = {
        "current_value": 0.4,
        "unrealized_pnl": 60.0,
        "pnl_pct_of_credit": 60.0,
        "current_underlying": 505.0,
        "current_short_delta": -0.10,
        "error": None,
    }
    base.update(overrides)
    return base


def test_build_mark_returns_none_on_repricer_error():
    bad = {"error": "repricing failed", "unrealized_pnl": None,
           "current_short_delta": None, "current_value": None,
           "pnl_pct_of_credit": None, "current_underlying": None}
    out = rec.build_mark(_signal_row(), bad, datetime.now(_TZ))
    assert out is None


def test_build_mark_returns_none_on_none_result():
    assert rec.build_mark(_signal_row(), None, datetime.now(_TZ)) is None


def test_build_mark_populates_core_fields():
    now = datetime(2030, 1, 10, 14, 30, tzinfo=_TZ)
    row = _signal_row()
    result = _repricer_ok()
    mark = rec.build_mark(row, result, now)
    assert mark is not None
    assert mark["signal_id"] == "abc123"
    assert mark["mark_ts"] == now.isoformat()
    assert mark["mark_date"] == "2030-01-10"
    assert mark["current_value"] == 0.4
    assert mark["unrealized_pnl"] == 60.0
    assert mark["current_underlying"] == 505.0
    assert mark["current_short_delta"] == -0.10


def test_build_mark_take_profit_when_pnl_exceeds_50pct():
    # entry_credit=1.0 → credit_total=100 → TP at >= 50
    mark = rec.build_mark(_signal_row(), _repricer_ok(unrealized_pnl=60.0),
                          datetime.now(_TZ))
    assert mark["recommendation"] == "TAKE_PROFIT"


def test_build_mark_cut_on_delta_breach():
    # entry -0.15, current -0.40 → drift 0.25 >= 0.12 → CUT
    mark = rec.build_mark(_signal_row(),
                          _repricer_ok(current_short_delta=-0.40,
                                       unrealized_pnl=-10.0),
                          datetime.now(_TZ))
    assert mark["recommendation"] == "CUT"


def test_build_mark_hold_default_with_score_drift_reason():
    mark = rec.build_mark(_signal_row(),
                          _repricer_ok(unrealized_pnl=10.0,
                                       current_short_delta=-0.10),
                          datetime.now(_TZ))
    assert mark["recommendation"] == "HOLD"
    # score_drift is current_score - entry_score; current_score may be None
    # if scoring import fails for any reason — that's a soft contract.
    if mark["current_score"] is not None:
        assert mark["score_drift"] == mark["current_score"] - 60


def test_build_mark_dte_remaining_drives_low_dte_cut():
    # expiration today + small unrealized loss → CUT (dte<=2 and underwater)
    now = datetime(2030, 1, 14, 14, 30, tzinfo=_TZ)
    row = _signal_row(expiration="2030-01-15")  # 1 day remaining
    mark = rec.build_mark(row, _repricer_ok(unrealized_pnl=-5.0,
                                            current_short_delta=-0.10),
                          now)
    assert mark["recommendation"] == "CUT"


def test_build_mark_score_drift_none_when_score_unavailable(monkeypatch):
    # Force the scoring path to fail → score remains None → drift None
    import signal_recommender
    monkeypatch.setattr(signal_recommender, "_recompute_score",
                        lambda *a, **kw: (None, None))
    mark = rec.build_mark(_signal_row(), _repricer_ok(), datetime.now(_TZ))
    assert mark["current_score"] is None
    assert mark["score_drift"] is None
    # Still produces a recommendation (with "holding" reason when no score)
    assert mark["recommendation"] in ("HOLD", "TAKE_PROFIT", "CUT")


def test_track_thresholds_vertical():
    # entry credit 1.50: target = close at <= 0.75 (50% captured),
    # stop = close at >= 1.50 * (1 + 2.0) = 4.50
    t = rec.track_thresholds(entry_credit=1.50)
    assert t["target_mid"] == 0.75
    assert t["stop_mid"] == 4.50


def test_auto_close_reason_passes_close_codes():
    assert rec.auto_close_reason("TARGET_HIT") == "TARGET_HIT"
    assert rec.auto_close_reason("MONEY_STOP") == "MONEY_STOP"
    assert rec.auto_close_reason("DELTA_STOP") == "DELTA_STOP"
    assert rec.auto_close_reason("TIME_STOP") == "TIME_STOP"


def test_auto_close_reason_hold_stays_open():
    assert rec.auto_close_reason("HOLD") is None


def test_auto_close_reason_unknown_or_none():
    assert rec.auto_close_reason("WAT") is None
    assert rec.auto_close_reason(None) is None


def test_recommend_code_target_hit():
    assert rec.recommend(_ctx(credit=1.0, pnl=60.0))["code"] == "TARGET_HIT"


def test_recommend_code_money_stop():
    # pnl <= -2x credit_total (credit_total = 1.0*100 = 100 -> stop at -200)
    assert rec.recommend(_ctx(credit=1.0, pnl=-250.0))["code"] == "MONEY_STOP"


def test_recommend_code_delta_stop():
    # not at TP/money-stop, but short delta drifted from entry (0.10 -> 0.40)
    r = rec.recommend(_ctx(credit=1.0, pnl=0.0, short_delta=0.40))
    assert r["action"] == "CUT" and r["code"] == "DELTA_STOP"


def test_recommend_code_time_stop():
    # low DTE and underwater, delta below breach
    r = rec.recommend(_ctx(credit=1.0, pnl=-5.0, short_delta=0.10, dte_remaining=1))
    assert r["action"] == "CUT" and r["code"] == "TIME_STOP"


def test_recommend_code_hold():
    assert rec.recommend(_ctx(credit=1.0, pnl=0.0, short_delta=0.10,
                              dte_remaining=10))["code"] == "HOLD"


def test_plan_auto_closes_selects_by_code():
    marks = [
        ("a", {"recommendation_code": "TARGET_HIT", "current_value": 0.40}),
        ("b", {"recommendation_code": "MONEY_STOP", "current_value": 3.10}),
        ("c", {"recommendation_code": "TIME_STOP",  "current_value": 1.05}),
        ("d", {"recommendation_code": "HOLD",       "current_value": 0.80}),
    ]
    out = rec.plan_auto_closes(marks)
    assert out == [
        ("a", 0.40, "TARGET_HIT"),
        ("b", 3.10, "MONEY_STOP"),
        ("c", 1.05, "TIME_STOP"),
    ]


def test_plan_auto_closes_skips_missing_value():
    marks = [("a", {"recommendation_code": "TARGET_HIT", "current_value": None})]
    assert rec.plan_auto_closes(marks) == []


def test_plan_auto_closes_empty():
    assert rec.plan_auto_closes([]) == []


def test_build_mark_carries_target_hit_code():
    mark = rec.build_mark(_signal_row(), _repricer_ok(unrealized_pnl=60.0),
                          datetime.now(_TZ))
    assert mark["recommendation_code"] == "TARGET_HIT"


def test_build_mark_carries_delta_stop_code():
    mark = rec.build_mark(_signal_row(),
                          _repricer_ok(unrealized_pnl=0.0, current_short_delta=0.40),
                          datetime.now(_TZ))
    assert mark["recommendation"] == "CUT"
    assert mark["recommendation_code"] == "DELTA_STOP"
