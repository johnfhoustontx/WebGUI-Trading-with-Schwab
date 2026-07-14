"""Single-type range rescue engine — heat model + advisory candidate economics.

Covers ``rescue.assess_range_risk`` and ``rescue.range_candidates`` (Phase 1c:
CONDOR_CALL/PUT + BUTTERFLY_CALL/PUT — long, defined-risk DEBIT range structures).
Pure — no I/O. Advisory-only. See
docs/plans/2026-07-14-rescue-condor-butterfly-design.md.
"""
from services.options_svc import rescue


def _condor_pos(**kw):
    # long call condor: long 95, short 100, short 105, long 110 (per-unit qty 1).
    p = {
        "symbol": "AAPL",
        "strategy": "CONDOR_CALL",
        "legs": [
            {"right": "CALL", "side": "long", "strike": 95.0, "qty": 1},
            {"right": "CALL", "side": "short", "strike": 100.0, "qty": 1},
            {"right": "CALL", "side": "short", "strike": 105.0, "qty": 1},
            {"right": "CALL", "side": "long", "strike": 110.0, "qty": 1},
        ],
        "expiration": "2099-07-31",
        "entry_credit": -2.00,      # SIGNED: paid a $2.00/share debit
        "quantity": 1,
    }
    p.update(kw)
    return p


def _fly_pos(**kw):
    # long put butterfly: long 110, short 2x 100, long 90 (per-unit).
    p = {
        "symbol": "AAPL",
        "strategy": "BUTTERFLY_PUT",
        "legs": [
            {"right": "PUT", "side": "long", "strike": 110.0, "qty": 1},
            {"right": "PUT", "side": "short", "strike": 100.0, "qty": 2},
            {"right": "PUT", "side": "long", "strike": 90.0, "qty": 1},
        ],
        "expiration": "2099-07-31",
        "entry_credit": -1.50,
        "quantity": 1,
    }
    p.update(kw)
    return p


def _mark(**kw):
    m = {
        "current_underlying": 102.5,   # center of the condor short strikes
        "current_value": 2.00,
        "unrealized_pnl": 0.0,
        "dte": 20,
    }
    m.update(kw)
    return m


def _flat_pricer(*a, **k):
    return 1.00


def _dead_pricer(*a, **k):
    return None


# --------------------------------------------------------------------------- #
# _range_center_halfwidth
# --------------------------------------------------------------------------- #
def test_center_halfwidth_condor():
    c, hw = rescue._range_center_halfwidth(_condor_pos()["legs"])
    assert c == 102.5                # midpoint of shorts 100/105
    assert hw == 7.5                 # 102.5 -> nearest wing 95 or 110


def test_center_halfwidth_butterfly():
    c, hw = rescue._range_center_halfwidth(_fly_pos()["legs"])
    assert c == 100.0                # both shorts at 100
    assert hw == 10.0                # 100 -> 90/110


def test_center_halfwidth_degenerate():
    assert rescue._range_center_halfwidth([]) == (None, None)
    assert rescue._range_center_halfwidth(
        [{"side": "short", "strike": 100.0}]) == (None, None)  # no wing


# --------------------------------------------------------------------------- #
# assess_range_risk
# --------------------------------------------------------------------------- #
def test_condor_at_center_low_heat():
    r = rescue.assess_range_risk(_condor_pos(), _mark(current_underlying=102.5,
                                                      unrealized_pnl=0.0))
    assert r["state"] == "ok"
    assert r["heat"] < 25


def test_condor_past_wing_big_loss_critical():
    # und 118 is well beyond the 110 wing; near-total loss of the $2 debit.
    pos = _condor_pos()
    mark = _mark(current_underlying=118.0, current_value=0.05,
                 unrealized_pnl=-195.0, dte=3)
    r = rescue.assess_range_risk(pos, mark)
    assert r["state"] == "critical"
    assert r["heat"] >= 75


def test_butterfly_at_wing_is_tested_or_worse():
    # und at the wing (90) -> range_frac ~1; moderate loss.
    pos = _fly_pos()
    mark = _mark(current_underlying=90.0, current_value=0.40,
                 unrealized_pnl=-110.0, dte=10)
    r = rescue.assess_range_risk(pos, mark)
    assert r["heat"] >= 50
    assert r["state"] in ("tested", "critical")


def test_assess_range_defensive_on_none_underlying():
    r = rescue.assess_range_risk(_condor_pos(), _mark(current_underlying=None,
                                                      unrealized_pnl=0.0))
    assert r["state"] == "ok"
    assert 0.0 <= r["heat"] <= 100.0


def test_assess_range_defensive_on_no_legs():
    r = rescue.assess_range_risk({"strategy": "CONDOR_CALL", "quantity": 1,
                                  "entry_credit": -2.0, "legs": []}, _mark())
    assert 0.0 <= r["heat"] <= 100.0


# --------------------------------------------------------------------------- #
# range_candidates
# --------------------------------------------------------------------------- #
def test_condor_close_is_a_credit_and_zeroes_max_loss():
    cands = rescue.range_candidates(_condor_pos(), _mark(current_value=2.00),
                                    _flat_pricer)
    close = next(c for c in cands if c["action"] == "close")
    assert close["apply_kind"] == "advisory"
    assert close["gross_cash"] == 200.0        # +cv*100*qty
    assert close["new_max_loss"] == 0.0
    # 4 legs, 1 contract -> 4 * 0.65 = 2.60
    assert close["commission"] == 2.60


def test_condor_roll_out_is_a_debit_and_present():
    # flat pricer: every leg = 1.00 -> cv_new = (short 1 + short 1 - long - long)*1 = 0
    # gross = (cv - 0)*100 = +cv*100 ... to force a debit, price later legs richer.
    def _pricer(sym, exp, right, strike):
        return 1.50 if "2099-08" in str(exp) else 1.00  # later expiry richer
    cands = rescue.range_candidates(_condor_pos(), _mark(current_value=0.00),
                                    _pricer)
    roll = next((c for c in cands if c["action"] == "roll_out"), None)
    assert roll is not None
    assert roll["apply_kind"] == "advisory"
    assert roll["new_expiry"] is not None
    # later structure costs more -> new_cv negative -> gross = (0 - new_cv) ... check debit
    # condor net at 1.50 flat = short2 - long2 = 0 -> new_cv 0 too; economics tested elsewhere.
    assert roll["dte_after"] == (_mark()["dte"]) + 30


def test_condor_roll_out_gross_is_a_debit_when_later_structure_richer():
    # A long call condor: value = +long −short. Price the later expiry so the
    # structure is worth MORE later (new_cv > cv) → rolling out costs a debit.
    def _pricer(sym, exp, right, strike):
        later = "2099-08" in str(exp)
        base = {95.0: 13.0, 100.0: 6.0, 105.0: 2.0, 110.0: 0.5}[float(strike)]
        # later expiry: boost the LONG wings (95/110) hard, leave the shorts flat.
        return base + (5.0 if (later and strike in (95.0, 110.0)) else 0.0)
    # cv (now) = +longs −shorts = (13+0.5) − (6+2) = 5.5 (given via mark)
    # new_cv    = (18+5.5) − (6+2) = 15.5  → gross = (5.5 − 15.5)*100 = −1000 (debit)
    cands = rescue.range_candidates(_condor_pos(), _mark(current_value=5.5),
                                    _pricer)
    roll = next((c for c in cands if c["action"] == "roll_out"), None)
    assert roll is not None
    assert roll["apply_kind"] == "advisory"
    assert roll["gross_cash"] == -1000.0     # a debit to roll into the richer expiry
    assert roll["new_expiry"] is not None


def test_butterfly_close_recovers_value():
    cands = rescue.range_candidates(_fly_pos(), _mark(current_value=1.20),
                                    _flat_pricer)
    close = next(c for c in cands if c["action"] == "close")
    assert close["gross_cash"] == 120.0
    assert close["new_max_loss"] == 0.0
    # 3 legs -> 3 * 0.65 = 1.95
    assert close["commission"] == 1.95


def test_roll_out_skipped_when_leg_unpriceable_but_close_survives():
    # cv provided via mark, but the pricer is dead -> roll_out can't price, close stays.
    cands = rescue.range_candidates(_condor_pos(), _mark(current_value=1.00),
                                    _dead_pricer)
    actions = {c["action"] for c in cands}
    assert "close" in actions
    assert "roll_out" not in actions


def test_range_candidates_empty_on_no_legs():
    pos = {"symbol": "AAPL", "strategy": "CONDOR_CALL", "quantity": 1,
           "entry_credit": -2.0, "legs": [], "expiration": "2099-07-31"}
    assert rescue.range_candidates(pos, _mark(), _flat_pricer) == []


def test_all_range_candidates_are_advisory():
    cands = rescue.range_candidates(_fly_pos(), _mark(current_value=1.0),
                                    _flat_pricer)
    assert cands
    assert all(c["apply_kind"] == "advisory" for c in cands)
