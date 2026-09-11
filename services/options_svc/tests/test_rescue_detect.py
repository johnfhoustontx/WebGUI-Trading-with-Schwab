from services.options_svc import rescue


def _pos(**kw):
    base = dict(position_id=1, symbol="SPY", strategy="PCS",
                short_strike=500.0, long_strike=495.0, width=5.0,
                expiration="2026-07-31", entry_credit=1.00,
                current_short_delta=0.18, quantity=1)
    base.update(kw); return base


def _mark(**kw):
    base = dict(current_underlying=520.0, unrealized_pnl=20.0,
                current_short_delta=0.18, current_value=0.80, dte=40)
    base.update(kw); return base


def test_far_otm_is_ok():
    r = rescue.assess_position_risk(_pos(), _mark(), gex=None, regime=None)
    assert r["state"] == "ok"
    assert r["heat"] < 25


def test_underlying_through_short_strike_is_critical():
    r = rescue.assess_position_risk(
        _pos(), _mark(current_underlying=498.0, current_short_delta=0.55,
                      unrealized_pnl=-250.0, dte=10),
        gex=None, regime=None)
    assert r["state"] == "critical"
    assert r["heat"] >= 75


def test_money_stop_breach_marks_tested():
    r = rescue.assess_position_risk(
        _pos(), _mark(unrealized_pnl=-200.0, current_short_delta=0.30),
        gex=None, regime=None)
    assert r["state"] in ("tested", "critical")


def test_gex_below_flip_raises_heat():
    base = rescue.assess_position_risk(
        _pos(), _mark(current_underlying=501.0, current_short_delta=0.28),
        gex=None, regime=None)
    hot = rescue.assess_position_risk(
        _pos(), _mark(current_underlying=501.0, current_short_delta=0.28),
        gex={"flip": 505.0, "put_wall": 490.0}, regime=None)
    assert hot["heat"] > base["heat"]


# ── Single-leg short puts: the Income Window's cash-secured put ──────────────
# The side test listed only PCS and IC, so a SHORT_PUT was scored with the
# CALL-side formula: a put falling toward its strike looked safe, and one well
# clear of it read as already breached. The deltas below sit under delta_warn and
# the P&L above money_warn on purpose, so PROXIMITY alone decides the state.


def _csp(**kw):
    base = dict(strategy="SHORT_PUT", short_strike=500.0, long_strike=None,
                width=None, entry_credit=1.70)
    base.update(kw)
    return _pos(**base)


def test_a_cash_secured_put_below_its_strike_is_critical():
    r = rescue.assess_position_risk(
        _csp(), _mark(current_underlying=498.0, current_short_delta=-0.20,
                      unrealized_pnl=-50.0, dte=20),
        gex=None, regime=None)
    assert r["state"] == "critical"


def test_a_cash_secured_put_well_above_its_strike_is_ok():
    r = rescue.assess_position_risk(
        _csp(), _mark(current_underlying=540.0, current_short_delta=-0.08,
                      unrealized_pnl=30.0, dte=30),
        gex=None, regime=None)
    assert r["state"] == "ok"


def test_the_naked_put_spelling_is_also_put_side():
    """Two spellings for one structure: SHORT_PUT on the scan side, NAKED_PUT on
    the Calculator/rescue side. Both can sit in the paper book."""
    r = rescue.assess_position_risk(
        _csp(strategy="NAKED_PUT"),
        _mark(current_underlying=498.0, current_short_delta=-0.20,
              unrealized_pnl=-50.0, dte=20),
        gex=None, regime=None)
    assert r["state"] == "critical"


def test_closing_a_single_leg_income_position_is_billed_as_one_leg():
    """Lives beside the side test because it is the same class of defect: Rescue
    assuming every position it can close is a spread. ``build_close`` returned
    None for these until they could be marked, so the two-leg default never
    mattered; now it would overcharge every close by $0.65 a contract and make
    closing look dearer than the alternatives it is ranked against."""
    assert rescue._close_legs({"strategy": "SHORT_PUT"}) == 1
    assert rescue._close_legs({"strategy": "NAKED_PUT"}) == 1
    assert rescue._close_legs({"strategy": "COVERED_CALL"}) == 1
    assert rescue._close_legs({"strategy": "PCS"}) == 2
    assert rescue._close_legs({"strategy": "IC"}) == 4


def test_call_side_through_short_strike_is_critical():
    # CCS: danger is the underlying rising THROUGH the short call.
    r = rescue.assess_position_risk(
        _pos(strategy="CCS", short_strike=500.0, long_strike=505.0),
        _mark(current_underlying=502.0, current_short_delta=0.50,
              unrealized_pnl=-250.0, dte=10),
        gex=None, regime=None)
    assert r["state"] == "critical"
    assert r["heat"] >= 75


def test_short_resting_on_put_wall_lowers_heat():
    # Same setup, but the short strike sits on the put wall -> bounce more likely.
    near = rescue.assess_position_risk(
        _pos(short_strike=500.0),
        _mark(current_underlying=501.0, current_short_delta=0.28),
        gex={"flip": 495.0, "put_wall": 500.0}, regime=None)  # wall == short, no below-flip bonus
    off = rescue.assess_position_risk(
        _pos(short_strike=500.0),
        _mark(current_underlying=501.0, current_short_delta=0.28),
        gex={"flip": 495.0, "put_wall": 480.0}, regime=None)  # wall far away
    assert near["heat"] < off["heat"]
