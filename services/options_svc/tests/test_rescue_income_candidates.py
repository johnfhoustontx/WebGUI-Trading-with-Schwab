"""The Rescue board's menu for the Income Window's single-leg positions.

``build_roll_down``, ``build_roll_out`` and ``build_roll_down_out`` all
early-return for anything outside PCS/CCS/IC, so a cash-secured put's whole menu
was one row: "Close now (systematic stop)". The playbook's rule for a tested
short put is to roll down, out, or down-and-out FOR A CREDIT — or to accept
assignment and keep turning the wheel — and none of that was on the board.

The builders already existed (``single_candidates``, written for the ad-hoc naked
shorts) and were simply never wired to the paper book. See
docs/plans/2026-09-11-income-exit-rules-design.md §4.

They stay ADVISORY: ``paper_adjust.apply_roll`` partitions ``est_fill_legs`` into
a closing PAIR and a reopening PAIR and books a spread reopen, so executing a
single-leg roll is a change to the money path and its own commit.
"""
from services.options_svc import rescue


def _csp(**kw):
    p = {
        "position_id": 7,
        "symbol": "AAPL",
        "strategy": "SHORT_PUT",
        "short_strike": 100.0,
        "long_strike": None,
        "call_short": None,
        "width": None,
        "expiration": "2099-07-31",
        "entry_credit": 1.70,
        "max_loss_total": 10_000.0,     # the strike notional — cash-secured
        "quantity": 1,
    }
    p.update(kw)
    return p


def _covered_call(**kw):
    return _csp(strategy="COVERED_CALL", short_strike=110.0, call_short=110.0,
                entry_credit=0.80, max_loss_total=0.0, **kw)


def _spread(**kw):
    return _csp(strategy="PCS", short_strike=100.0, long_strike=95.0, width=5.0,
                entry_credit=1.00, max_loss_total=400.0, **kw)


def _mark(**kw):
    m = {"current_underlying": 99.0, "current_value": 2.40,
         "unrealized_pnl": -70.0, "current_short_delta": -0.45, "dte": 20}
    m.update(kw)
    return m


def _pricer(*_a, **_k):
    return 1.10


def _actions(position, mark=None, pricer=_pricer):
    return [c["action"] for c in
            rescue.rescue_candidates(position, mark or _mark(), pricer)]


# --- the board finally offers the repair ------------------------------------

def test_a_tested_cash_secured_put_is_offered_a_roll():
    assert "roll" in _actions(_csp())


def test_a_cash_secured_put_rolls_DOWN_and_out():
    """Down, because the danger is the underlying falling through the strike.
    A roll to a HIGHER strike would collect more premium and take more risk."""
    cands = rescue.rescue_candidates(_csp(), _mark(), _pricer)
    roll = next(c for c in cands if c["action"] == "roll")
    sell = next(lg for lg in roll["est_fill_legs"] if lg["side"] == "SELL")
    assert sell["right"] == "PUT"
    assert sell["strike"] < 100.0
    assert roll["dte_after"] > 20


def test_a_covered_call_rolls_UP_and_out_on_the_call_chain():
    """The mirror, and the one the old side test got wrong: a covered call
    matched neither PCS nor IC, so every side-dependent decision fell to the
    call branch by accident rather than by reasoning."""
    cands = rescue.rescue_candidates(_covered_call(),
                                     _mark(current_underlying=112.0,
                                           current_short_delta=0.55), _pricer)
    roll = next(c for c in cands if c["action"] == "roll")
    sell = next(lg for lg in roll["est_fill_legs"] if lg["side"] == "SELL")
    assert sell["right"] == "CALL"
    assert sell["strike"] > 110.0


def test_the_close_is_billed_as_one_leg():
    """``commission_for(1, ...)``, not the spread's two. A close that looks
    dearer than it is loses to the alternatives it is ranked against."""
    cands = rescue.rescue_candidates(_csp(), _mark(), _pricer)
    close = next(c for c in cands if c["action"] == "close")
    spread = rescue.rescue_candidates(_spread(), _mark(), _pricer)
    spread_close = next(c for c in spread if c["action"] == "close")
    assert close["commission"] == round(spread_close["commission"] / 2, 2)


# --- the wheel is a real choice, so the board has to be able to say it ------

def test_a_cash_secured_put_can_be_left_to_assign():
    """B1's rule table names this as the tested cash-secured put's alternative to
    rolling, and it is the reason the structure has no delta or time stop. A menu
    that cannot express "do nothing and take the shares" is arguing for a repair
    the plan never called for."""
    cands = rescue.rescue_candidates(_csp(), _mark(), _pricer)
    wheel = next(c for c in cands if c["action"] == "accept_assignment")
    assert wheel["apply_kind"] == "advisory"
    assert wheel["gross_cash"] == 0.0
    assert wheel["commission"] == 0.0
    assert any("shares" in r.lower() for r in wheel["rationale"])


def test_a_covered_call_can_be_left_to_call_away():
    cands = rescue.rescue_candidates(_covered_call(),
                                     _mark(current_underlying=112.0), _pricer)
    wheel = next(c for c in cands if c["action"] == "let_called_away")
    assert wheel["apply_kind"] == "advisory"
    assert wheel["net_cash"] == 0.0


def test_a_spread_is_offered_neither_wheel_action():
    """Both are properties of a single-leg income position, not of any position
    the board can reach."""
    actions = _actions(_spread())
    assert "accept_assignment" not in actions
    assert "let_called_away" not in actions


def test_a_cash_secured_put_is_not_offered_the_call_away():
    actions = _actions(_csp())
    assert "let_called_away" not in actions


# --- routing ---------------------------------------------------------------

def test_every_single_leg_candidate_is_advisory():
    """``apply_roll`` books a spread reopen, so nothing here may claim it can be
    executed. The GUI reads ``apply_kind`` to decide whether to draw Apply."""
    for position in (_csp(), _covered_call()):
        cands = rescue.rescue_candidates(position, _mark(), _pricer)
        assert cands, position["strategy"]
        assert all(c["apply_kind"] == "advisory" for c in cands), position["strategy"]


def test_the_spread_menu_is_untouched_and_still_executable():
    """The routing must not swallow the structures it was not written for."""
    cands = rescue.rescue_candidates(_spread(), _mark(), _pricer)
    actions = {c["action"] for c in cands}
    assert {"close", "roll_out"} <= actions
    close = next(c for c in cands if c["action"] == "close")
    assert close["apply_kind"] == "execute"


def test_income_candidates_carry_the_strategic_context():
    """The advisory's ``context`` line is read off ``candidates[0]``, so a
    delegated menu that drops the engine notes leaves the board's explanation
    blank."""
    cands = rescue.rescue_candidates(
        _csp(), _mark(), _pricer,
        gex={"flip": 105.0, "put_wall": 95.0}, regime=None)
    assert cands[0]["context"]


def test_candidates_are_ranked_best_first():
    cands = rescue.rescue_candidates(_csp(), _mark(), _pricer)
    scores = [c["score"] for c in cands]
    assert scores == sorted(scores, reverse=True)


def test_an_unpriceable_chain_still_leaves_the_close_and_the_wheel():
    """A roll needs a live quote for the new strike; closing needs only the mark,
    and letting it assign needs nothing at all — so a quote gap must not empty
    the board."""
    actions = _actions(_csp(), pricer=lambda *a, **k: None)
    assert "close" in actions
    assert "accept_assignment" in actions
    assert "roll" not in actions
