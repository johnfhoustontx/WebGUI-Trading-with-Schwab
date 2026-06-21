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
    cands = rescue.rescue_candidates(_pos(symbol="AAPL"), _mark(), _flat_pricer)
    # equity -> assignment_risk True -> at least one candidate carries the note
    assert any(any("assignment" in w.lower() for w in c.get("warnings", []))
               for c in cands)


def test_a_failing_builder_is_dropped_not_raised():
    # dead pricer -> builders needing prices return None; close (uses cv) still present
    def dead(*a, **k):
        return None
    cands = rescue.rescue_candidates(_pos(), _mark(), dead)
    assert any(c["action"] == "close" for c in cands)   # close needs no price_leg
    assert all(c is not None for c in cands)
