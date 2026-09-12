"""Tests for the rescue_candidates orchestrator (pure ranking)."""
from services.options_svc import rescue


def _pos(**kw):
    base = dict(position_id=1, symbol="SPY", strategy="PCS",
                short_strike=500.0, long_strike=495.0, width=5.0,
                expiration="2026-07-31", entry_credit=1.00, quantity=2,
                max_loss_total=800.0)
    base.update(kw)
    return base


def _mark(**kw):
    base = dict(current_underlying=501.0, current_value=2.50,
                unrealized_pnl=-300.0, current_short_delta=0.35, dte=30)
    base.update(kw)
    return base


def _flat_pricer(*a, **k):
    return 1.00


def test_orchestrator_ranks_and_filters():
    cands = rescue.rescue_candidates(_pos(), _mark(), _flat_pricer, gex=None, regime=None)
    assert cands and cands[0]["score"] >= cands[-1]["score"]   # sorted desc
    assert any(c["action"] == "close" for c in cands)
    assert all(c is not None for c in cands)
    # every candidate has a score and inherited context notes
    assert all("score" in c and "context" in c for c in cands)


def test_debit_candidates_get_debit_warning():
    cands = rescue.rescue_candidates(_pos(), _mark(), _flat_pricer)
    debit = [c for c in cands if c.get("net_cash", 0) < 0]
    assert debit, "expected at least one debit candidate (e.g. narrow)"
    assert all(any("debit" in w.lower() for w in c["warnings"]) for c in debit)


def test_equity_assignment_warning_present():
    """An ITM equity short's assignment note reaches the candidates' warnings.

    ⚠ The fixture moved from underlying 501 to 498, and the reason is the point of
    the test. With a short put at 500 and the underlying at 501 the short is OUT
    of the money and cannot be assigned — this used to pass anyway because
    ``strategic_context`` set ``assignment_risk`` unconditionally True for every
    equity short, which is assessment defect 13. So the old assertion pinned the
    defect. The SUBJECT of the test is the plumbing (a context flag reaching a
    candidate's warnings), and that is preserved exactly by making the short
    genuinely ITM; the converse is now asserted below.
    """
    cands = rescue.rescue_candidates(_pos(symbol="AAPL"),
                                     _mark(current_underlying=498.0), _flat_pricer)
    assert any(any("assignment" in w.lower() for w in c.get("warnings", []))
               for c in cands)


def test_an_OUT_of_the_money_equity_short_carries_no_assignment_warning():
    """The other half of the same fix (gap assessment B5): a flag that is always
    on carries no information, so an OTM short must not raise it."""
    cands = rescue.rescue_candidates(_pos(symbol="AAPL"),
                                     _mark(current_underlying=520.0), _flat_pricer)
    assert cands, "fixture produced no candidates"
    assert not any(any("assignment" in w.lower() for w in c.get("warnings", []))
                   for c in cands)


def test_a_failing_builder_is_dropped_not_raised():
    # dead pricer -> builders needing prices return None; close (uses cv) still present
    def dead(*a, **k):
        return None
    cands = rescue.rescue_candidates(_pos(), _mark(), dead)
    assert any(c["action"] == "close" for c in cands)   # close needs no price_leg
    assert all(c is not None for c in cands)
