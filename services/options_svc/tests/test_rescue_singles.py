"""Single-option rescue engine — heat model + advisory candidate economics.

Covers ``rescue.assess_single_risk`` and ``rescue.single_candidates`` (Phase 1:
LONG_CALL / LONG_PUT / NAKED_CALL / NAKED_PUT). Pure — no I/O. See
docs/plans/2026-06-23-rescue-singles-design.md.
"""
from services.options_svc import rescue


def _pos(**kw):
    p = {
        "symbol": "AAPL",
        "strategy": "LONG_CALL",
        "short_strike": 100.0,      # the single strike
        "expiration": "2099-07-31",
        "entry_credit": -2.50,      # SIGNED: long paid a $2.50 debit
        "quantity": 1,
    }
    p.update(kw)
    return p


def _mark(**kw):
    m = {
        "current_underlying": 101.0,
        "current_value": 1.00,      # per-share option mark
        "unrealized_pnl": -50.0,
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
# assess_single_risk — LONG
# --------------------------------------------------------------------------- #
def test_long_call_far_otm_and_down_big_is_critical():
    # deep OTM (und 80 vs strike 100 -> 20% depth) + near-total loss on the debit.
    pos = _pos(strategy="LONG_CALL", short_strike=100.0, entry_credit=-2.50)
    mark = _mark(current_underlying=80.0, current_value=0.05,
                 unrealized_pnl=-245.0, dte=4)
    r = rescue.assess_single_risk(pos, mark)
    assert r["state"] == "critical"
    assert r["heat"] >= 75


def test_long_call_itm_slightly_down_is_low():
    pos = _pos(strategy="LONG_CALL", short_strike=100.0, entry_credit=-2.50)
    # ITM (und above strike) -> otm_depth 0; only a small loss.
    mark = _mark(current_underlying=104.0, current_value=2.30,
                 unrealized_pnl=-20.0, dte=30)
    r = rescue.assess_single_risk(pos, mark)
    assert r["state"] in ("ok", "watch")
    assert r["heat"] < 50


def test_long_put_otm_depth_uses_upside():
    # a long put is OTM when underlying is ABOVE the strike.
    pos = _pos(strategy="LONG_PUT", short_strike=100.0, entry_credit=-2.50)
    mark = _mark(current_underlying=120.0, current_value=0.10,
                 unrealized_pnl=-240.0, dte=3)
    r = rescue.assess_single_risk(pos, mark)
    assert r["state"] == "critical"


# --------------------------------------------------------------------------- #
# assess_single_risk — NAKED
# --------------------------------------------------------------------------- #
def test_naked_put_underlying_through_strike_is_critical():
    pos = _pos(strategy="NAKED_PUT", short_strike=100.0, entry_credit=1.50)
    mark = _mark(current_underlying=97.0, current_value=3.50,
                 unrealized_pnl=-200.0)
    r = rescue.assess_single_risk(pos, mark)
    assert r["state"] == "critical"


def test_naked_call_underlying_through_strike_is_critical():
    pos = _pos(strategy="NAKED_CALL", short_strike=100.0, entry_credit=1.50)
    mark = _mark(current_underlying=103.0, current_value=3.50,
                 unrealized_pnl=-200.0)
    r = rescue.assess_single_risk(pos, mark)
    assert r["state"] == "critical"


def test_benign_naked_put_is_ok():
    pos = _pos(strategy="NAKED_PUT", short_strike=100.0, entry_credit=1.50)
    # far above the strike, tiny value, small delta -> ok (only base bump).
    mark = _mark(current_underlying=130.0, current_value=0.10,
                 unrealized_pnl=140.0, current_short_delta=0.05)
    r = rescue.assess_single_risk(pos, mark)
    assert r["state"] == "ok"
    assert r["heat"] < 25


def test_assess_single_defensive_on_none_underlying():
    pos = _pos(strategy="NAKED_CALL", short_strike=100.0, entry_credit=1.50)
    mark = _mark(current_underlying=None, current_value=None,
                 unrealized_pnl=None, current_short_delta=None)
    r = rescue.assess_single_risk(pos, mark)          # must not raise
    assert r["state"] == "ok"
    assert isinstance(r["heat"], float)


# --------------------------------------------------------------------------- #
# single_candidates — LONG
# --------------------------------------------------------------------------- #
def test_long_call_candidates_are_close_rollout_convert_all_advisory():
    pos = _pos(strategy="LONG_CALL", short_strike=100.0, entry_credit=-2.50,
               quantity=2)
    mark = _mark(current_value=1.00)
    cands = rescue.single_candidates(pos, mark, _flat_pricer)
    actions = [c["action"] for c in cands]
    assert actions[0] == "close"
    assert set(actions) == {"close", "roll_out", "convert_to_vertical"}
    assert all(c["apply_kind"] == "advisory" for c in cands)


def test_long_call_close_is_a_credit():
    pos = _pos(strategy="LONG_CALL", entry_credit=-2.50, quantity=2)
    cands = rescue.single_candidates(pos, _mark(current_value=1.00), _flat_pricer)
    close = next(c for c in cands if c["action"] == "close")
    assert close["gross_cash"] == 1.00 * 100 * 2      # sell to close -> credit (+)
    assert close["gross_cash"] > 0
    assert close["new_max_loss"] == 0.0


def test_long_call_convert_to_vertical_reduces_max_loss():
    pos = _pos(strategy="LONG_CALL", short_strike=100.0, entry_credit=-2.50,
               quantity=1)
    cands = rescue.single_candidates(pos, _mark(current_value=1.00), _flat_pricer)
    conv = next(c for c in cands if c["action"] == "convert_to_vertical")
    debit_dollars = abs(-2.50) * 100 * 1              # 250
    assert conv["new_max_loss"] < debit_dollars
    assert conv["gross_cash"] > 0                     # sold a call -> credit


def test_long_call_skips_convert_when_leg_unpriceable():
    pos = _pos(strategy="LONG_CALL", entry_credit=-2.50)
    cands = rescue.single_candidates(pos, _mark(current_value=1.00), _dead_pricer)
    # close still builds (uses current_value), the priced repairs drop out.
    actions = [c["action"] for c in cands]
    assert "close" in actions
    assert "convert_to_vertical" not in actions
    assert "roll_out" not in actions


# --------------------------------------------------------------------------- #
# single_candidates — NAKED
# --------------------------------------------------------------------------- #
def test_naked_put_candidates_are_close_roll_protect_all_advisory():
    pos = _pos(strategy="NAKED_PUT", short_strike=100.0, entry_credit=1.50,
               quantity=1)
    cands = rescue.single_candidates(pos, _mark(current_value=2.00), _flat_pricer)
    actions = [c["action"] for c in cands]
    assert actions[0] == "close"
    assert set(actions) == {"close", "roll", "buy_protection"}
    assert all(c["apply_kind"] == "advisory" for c in cands)


def test_naked_put_close_is_a_debit():
    pos = _pos(strategy="NAKED_PUT", short_strike=100.0, entry_credit=1.50,
               quantity=2)
    cands = rescue.single_candidates(pos, _mark(current_value=2.00), _flat_pricer)
    close = next(c for c in cands if c["action"] == "close")
    assert close["gross_cash"] == -(2.00 * 100 * 2)   # buy to close -> debit (−)
    assert close["gross_cash"] < 0
    assert close["new_max_loss"] == 0.0


def test_naked_put_buy_protection_defines_risk_no_undefined_warning():
    pos = _pos(strategy="NAKED_PUT", short_strike=100.0, entry_credit=1.50,
               quantity=1)
    cands = rescue.single_candidates(pos, _mark(current_value=2.00), _flat_pricer)
    prot = next(c for c in cands if c["action"] == "buy_protection")
    assert prot["new_max_loss"] is not None            # NOW defined
    assert prot["new_max_loss"] >= 0.0
    assert prot["gross_cash"] < 0                      # bought protection -> debit
    assert not any("undefined" in w.lower() for w in prot["warnings"])


def test_naked_put_roll_carries_undefined_risk_warning():
    pos = _pos(strategy="NAKED_PUT", short_strike=100.0, entry_credit=1.50)
    cands = rescue.single_candidates(pos, _mark(current_value=2.00), _flat_pricer)
    roll = next(c for c in cands if c["action"] == "roll")
    assert any("undefined" in w.lower() for w in roll["warnings"])


def test_naked_call_candidates_present_and_advisory():
    pos = _pos(strategy="NAKED_CALL", short_strike=100.0, entry_credit=1.50)
    cands = rescue.single_candidates(pos, _mark(current_value=2.00), _flat_pricer)
    actions = {c["action"] for c in cands}
    assert actions == {"close", "roll", "buy_protection"}
    assert all(c["apply_kind"] == "advisory" for c in cands)


# ── the three ad-hoc advisory builders share one assembly core (2026-08-20) ──
# _advisory_from_single/_debit/_range were structural clones: 30-line tails
# differing by exactly TWO lines (which candidates fn, which risk fn), plus a
# byte-identical _flt validator defined three times. Consolidated so the next
# strategy family is added in one place.

def test_assemble_advisory_uses_the_injected_candidate_and_risk_functions():
    from services.options_svc import compute
    seen = {}

    def _cands(pos, mark, price_leg, gex, regime):
        seen["cands"] = True
        return [{"action": "close", "label": "Close", "apply_kind": "execute",
                 "gross_cash": -100.0, "commission": 1.3, "net_cash": -101.3,
                 "new_max_loss": 0.0, "new_short_delta": 0.0, "new_width": None,
                 "new_expiry": None, "dte_after": 3, "est_fill_legs": [],
                 "rationale": ["r"], "warnings": [], "context": ["ctx"]}]

    def _risk(pos, mark, gex, regime):
        seen["risk"] = True
        return {"state": "critical", "heat": 77.0}

    mark = {"underlying": 100.0, "current_value": 2.0, "unrealized_pnl": -50.0,
            "short_delta": None, "dte": 3}
    out = compute._assemble_advisory(
        {"symbol": "ABC", "strategy": "LONG_CALL"}, mark, dict(mark),
        price_leg=lambda *a: 1.0, gex=None,
        candidates_fn=_cands, risk_fn=_risk,
        position_id="adhoc", source="adhoc", symbol="ABC", strategy="LONG_CALL")
    assert seen == {"cands": True, "risk": True}
    assert out["state"] == "critical" and out["heat"] == 77.0
    assert out["symbol"] == "ABC" and out["position_id"] == "adhoc"


def test_assemble_advisory_forces_every_candidate_to_advisory():
    """Ad-hoc boards are advice only — no Apply button — regardless of what the
    candidate builder set."""
    from services.options_svc import compute
    cand = {"action": "roll_out", "label": "Roll", "apply_kind": "execute",
            "gross_cash": 10.0, "commission": 2.6, "net_cash": 7.4,
            "new_max_loss": 100.0, "new_short_delta": None, "new_width": 5.0,
            "new_expiry": "2026-09-18", "dte_after": 33, "est_fill_legs": [],
            "rationale": ["r"], "warnings": []}
    mark = {"underlying": 100.0, "current_value": 2.0, "unrealized_pnl": -50.0,
            "short_delta": None, "dte": 3}
    out = compute._assemble_advisory(
        {"symbol": "ABC", "strategy": "PCS"}, mark, dict(mark),
        price_leg=lambda *a: 1.0, gex=None,
        candidates_fn=lambda *a: [cand], risk_fn=lambda *a: {"state": "ok", "heat": 1.0},
        position_id="adhoc", source="adhoc", symbol="ABC", strategy="PCS")
    assert out["candidates"][0]["apply_kind"] == "advisory"
    assert out["candidates"][0]["applies"] is False


def test_assemble_advisory_degrades_to_an_error_dict_never_raises():
    from services.options_svc import compute

    def _boom(*a, **k):
        raise RuntimeError("engine exploded")

    mark = {"underlying": 100.0, "current_value": 2.0, "unrealized_pnl": -50.0,
            "short_delta": None, "dte": 3}
    out = compute._assemble_advisory(
        {"symbol": "ABC", "strategy": "PCS"}, mark, dict(mark),
        price_leg=lambda *a: 1.0, gex=None,
        candidates_fn=_boom, risk_fn=lambda *a: {"state": "ok", "heat": 0.0},
        position_id="adhoc", source="adhoc", symbol="ABC", strategy="PCS")
    assert "error" in out and "engine exploded" in out["error"]


def test_the_three_builders_all_route_through_the_shared_core():
    import inspect

    from services.options_svc import compute
    for name in ("_advisory_from_single", "_advisory_from_debit",
                 "_advisory_from_range"):
        src = inspect.getsource(getattr(compute, name))
        assert "_assemble_advisory(" in src, name
        # the duplicated tail is gone
        assert "RescueAdvisory(" not in src, name


def test_the_float_field_validator_is_defined_once():
    import inspect

    from services.options_svc import compute
    src = inspect.getsource(compute)
    assert src.count("def _flt(") == 0, "_flt should be a module-level helper now"
    assert hasattr(compute, "_spec_float")
