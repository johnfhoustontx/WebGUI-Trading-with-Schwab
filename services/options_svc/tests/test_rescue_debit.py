"""Debit-vertical rescue engine — heat model + advisory candidate economics.

Covers ``rescue.assess_debit_risk`` and ``rescue.debit_candidates`` (Phase 1b:
VERT_CALL_DEBIT = bull call / VERT_PUT_DEBIT = bear put). Pure — no I/O.
Advisory-only. See docs/plans/2026-06-24-rescue-debit-verticals-design.md.
"""
from services.options_svc import rescue


def _pos(**kw):
    # bull call: long lower call (100) + short higher call (105); debit paid.
    p = {
        "symbol": "AAPL",
        "strategy": "VERT_CALL_DEBIT",
        "long_strike": 100.0,       # the LONG leg (directional)
        "short_strike": 105.0,      # the SHORT leg
        "expiration": "2099-07-31",
        "entry_credit": -2.00,      # SIGNED: paid a $2.00/share debit
        "quantity": 1,
    }
    p.update(kw)
    return p


def _mark(**kw):
    m = {
        "current_underlying": 101.0,
        "current_value": 2.00,      # per-share spread value (long mid - short mid)
        "unrealized_pnl": 0.0,
        "current_short_delta": None,
        "dte": 14,
    }
    m.update(kw)
    return m


def _flat_pricer(*a, **k):
    return 1.00


def _dead_pricer(*a, **k):
    return None


# --------------------------------------------------------------------------- #
# assess_debit_risk
# --------------------------------------------------------------------------- #
def test_bull_call_far_below_long_strike_big_loss_is_critical():
    # deep against it: und 80 vs long_strike 100 -> 20% depth + near-total loss.
    pos = _pos(strategy="VERT_CALL_DEBIT", long_strike=100.0, short_strike=105.0,
               entry_credit=-2.00, quantity=1)
    mark = _mark(current_underlying=80.0, current_value=0.05,
                 unrealized_pnl=-195.0, dte=4)
    r = rescue.assess_debit_risk(pos, mark)
    assert r["state"] == "critical"
    assert r["heat"] >= 75


def test_bull_call_comfortably_itm_small_loss_is_low():
    pos = _pos(strategy="VERT_CALL_DEBIT", long_strike=100.0, short_strike=105.0,
               entry_credit=-2.00, quantity=1)
    # und above the short strike -> otm_depth 0; only a small loss.
    mark = _mark(current_underlying=108.0, current_value=4.50,
                 unrealized_pnl=-15.0, dte=30)
    r = rescue.assess_debit_risk(pos, mark)
    assert r["state"] in ("ok", "watch")
    assert r["heat"] < 50


def test_bear_put_otm_depth_uses_upside():
    # bear put: long higher put (100), losing when underlying is ABOVE long_strike.
    pos = _pos(strategy="VERT_PUT_DEBIT", long_strike=100.0, short_strike=95.0,
               entry_credit=-2.00, quantity=1)
    mark = _mark(current_underlying=120.0, current_value=0.10,
                 unrealized_pnl=-190.0, dte=3)
    r = rescue.assess_debit_risk(pos, mark)
    assert r["state"] == "critical"


def test_assess_debit_defensive_on_none_underlying():
    pos = _pos(strategy="VERT_CALL_DEBIT")
    mark = _mark(current_underlying=None, current_value=None,
                 unrealized_pnl=None, current_short_delta=None)
    r = rescue.assess_debit_risk(pos, mark)          # must not raise
    assert r["state"] in ("ok", "watch", "tested", "critical")
    assert isinstance(r["heat"], float)


# --------------------------------------------------------------------------- #
# debit_candidates
# --------------------------------------------------------------------------- #
def test_bull_call_candidates_are_close_rollout_butterfly_all_advisory():
    pos = _pos(strategy="VERT_CALL_DEBIT", long_strike=100.0, short_strike=105.0,
               entry_credit=-2.00, quantity=2)
    cands = rescue.debit_candidates(pos, _mark(current_value=2.00), _flat_pricer)
    actions = [c["action"] for c in cands]
    assert actions[0] == "close"
    assert set(actions) == {"close", "roll_out", "convert_to_butterfly"}
    assert all(c["apply_kind"] == "advisory" for c in cands)


def test_bull_call_close_is_a_credit_and_zeroes_max_loss():
    pos = _pos(strategy="VERT_CALL_DEBIT", entry_credit=-2.00, quantity=2)
    cands = rescue.debit_candidates(pos, _mark(current_value=1.50), _flat_pricer)
    close = next(c for c in cands if c["action"] == "close")
    assert close["gross_cash"] == 1.50 * 100 * 2      # sell to close -> credit (+)
    assert close["gross_cash"] > 0
    assert close["new_max_loss"] == 0.0


def _decreasing_pricer(sym, expiry, right, strike):
    # farther-OTM (higher-strike) calls are cheaper -> the near leg (S) is richer
    # than the far leg (S+w), so the butterfly spread collects a real credit.
    return max(0.10, 20.0 - 0.10 * float(strike))


def test_bull_call_convert_butterfly_reduces_max_loss_below_debit():
    pos = _pos(strategy="VERT_CALL_DEBIT", long_strike=100.0, short_strike=105.0,
               entry_credit=-2.00, quantity=1)
    cands = rescue.debit_candidates(pos, _mark(current_value=2.00), _decreasing_pricer)
    fly = next(c for c in cands if c["action"] == "convert_to_butterfly")
    debit_dollars = abs(-2.00) * 100 * 1              # 200
    assert fly["gross_cash"] > 0                      # credit collected
    assert fly["new_max_loss"] < debit_dollars


def test_debit_roll_out_present_with_live_pricer():
    pos = _pos(strategy="VERT_CALL_DEBIT", quantity=1)
    cands = rescue.debit_candidates(pos, _mark(current_value=2.00), _flat_pricer)
    roll = next(c for c in cands if c["action"] == "roll_out")
    assert roll["new_expiry"] is not None
    assert roll["apply_kind"] == "advisory"


def test_debit_skips_priced_repairs_when_leg_unpriceable_keeps_close():
    pos = _pos(strategy="VERT_CALL_DEBIT", entry_credit=-2.00)
    cands = rescue.debit_candidates(pos, _mark(current_value=2.00), _dead_pricer)
    actions = [c["action"] for c in cands]
    assert "close" in actions                          # close uses current_value
    assert "roll_out" not in actions
    assert "convert_to_butterfly" not in actions


def test_bear_put_candidates_present_and_advisory():
    pos = _pos(strategy="VERT_PUT_DEBIT", long_strike=100.0, short_strike=95.0,
               entry_credit=-2.00, quantity=1)
    cands = rescue.debit_candidates(pos, _mark(current_value=2.00), _flat_pricer)
    actions = {c["action"] for c in cands}
    assert actions == {"close", "roll_out", "convert_to_butterfly"}
    assert all(c["apply_kind"] == "advisory" for c in cands)


def test_debit_candidates_empty_on_missing_strikes():
    pos = _pos(long_strike=None)
    cands = rescue.debit_candidates(pos, _mark(), _flat_pricer)
    assert cands == []
