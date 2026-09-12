"""B8: the driver's caps are enforced against its LIVE equity, on both paths.

Design: docs/plans/2026-09-12-position-awareness-design.md.

⚠ **Both paths or neither.** If the decision path offered a budget the open path
then refused, the result would be the documented "Executed but nothing opened"
symptom — the driver logs an enqueue, the sizer zeroes it, and the only trace is a
log line. So ``run_cycle``'s limits and ``_driver_open_capacity_reason``'s are
scaled against the same quantity: the live ``equity`` off the same account
snapshot.

Measured on the live driver book: equity **$13,347** after a 46.6% drawdown from
$25,000, where `per_trade_max_risk` $3,000 (documented *"~12% of the book"*) was
really **22.5%** and `daily_risk_budget` $12,000 (*"~half the book"*) was
**89.9%**. Scaled, the effective caps there are $1,602 and $6,406.
"""
import pytest

from services.options_svc import compute


def _sig(max_loss=4.0):
    """A defined-risk PCS. ``max_loss`` is PER SHARE — ``driver_policy``'s
    ``max_loss_dollars`` multiplies by the 100-share contract, so 70.0 here is
    **$7,000** of risk for one contract: over the equity-scaled $6,406 budget and
    comfortably under the unscaled $12,000 one. That gap is what the tests below
    discriminate on, and nothing else about the signal matters here.
    """
    return {"type": "PCS", "symbol": "SPY", "width": 5.0, "credit": 1.0,
            "max_loss": max_loss, "short_strike": 500.0, "long_strike": 495.0}


def test_the_budget_is_measured_against_live_equity(monkeypatch):
    """$13,347 x 0.48 = $6,406, so $7,000 of incoming risk is refused where the
    unscaled $12,000 budget would have allowed it."""
    monkeypatch.setattr(compute, "_driver_open_positions", lambda: [])
    monkeypatch.setattr(compute, "_driver_equity", lambda: 13346.80)
    assert compute._driver_open_capacity_reason(_sig(max_loss=70.0), qty=1) == \
        compute.REJECT_RISK_BUDGET


def test_the_same_trade_passes_on_the_book_the_caps_were_written_for(monkeypatch):
    """At $25,000 the percentages reproduce the dollar caps, so nothing changes —
    the evidence that this restores intent rather than retuning appetite."""
    monkeypatch.setattr(compute, "_driver_open_positions", lambda: [])
    monkeypatch.setattr(compute, "_driver_equity", lambda: 25000.0)
    assert compute._driver_open_capacity_reason(_sig(max_loss=70.0), qty=1) is None


def test_an_unreadable_equity_falls_back_to_the_dollar_cap(monkeypatch):
    """A fraction of an unknown cannot be enforced. Refusing every trade because
    the account row could not be read would read as a broken driver — so the
    unscaled $12,000 still applies, and still refuses $13,000."""
    monkeypatch.setattr(compute, "_driver_open_positions", lambda: [])
    monkeypatch.setattr(compute, "_driver_equity", lambda: None)
    assert compute._driver_open_capacity_reason(_sig(max_loss=70.0), qty=1) is None
    assert compute._driver_open_capacity_reason(_sig(max_loss=130.0), qty=1) == \
        compute.REJECT_RISK_BUDGET


def test_a_grown_book_never_loosens_the_budget(monkeypatch):
    """``min`` in one direction only: a book up 140% must not authorise $13,000
    of open risk. Raising appetite is a decision this change may not make."""
    monkeypatch.setattr(compute, "_driver_open_positions", lambda: [])
    monkeypatch.setattr(compute, "_driver_equity", lambda: 60000.0)
    assert compute._driver_open_capacity_reason(_sig(max_loss=130.0), qty=1) == \
        compute.REJECT_RISK_BUDGET


def test_max_concurrent_is_untouched_by_the_scaling(monkeypatch):
    """Only the two DOLLAR caps scale; a slot count has no equity basis."""
    monkeypatch.setattr(compute, "_driver_open_positions",
                        lambda: [{"max_loss_total": 1.0}] * 10)
    monkeypatch.setattr(compute, "_driver_equity", lambda: 13346.80)
    assert compute._driver_open_capacity_reason(_sig(), qty=1) == \
        compute.REJECT_MAX_CONCURRENT


def test_the_per_trade_cap_the_sizer_uses_is_resolved_at_CALL_time(monkeypatch):
    """It was a module constant bound at import, so it could never follow equity —
    the same trap ``paper_account_db`` documents about a ``def``-time default."""
    monkeypatch.setattr(compute, "_driver_equity", lambda: 13346.80)
    assert compute._driver_per_trade_cap() == pytest.approx(1601.616)
    monkeypatch.setattr(compute, "_driver_equity", lambda: 25000.0)
    assert compute._driver_per_trade_cap() == pytest.approx(3000.0)


def test_the_equity_reader_degrades_to_None_rather_than_raising(monkeypatch):
    """It opens a SQLite store on the open path, which must never raise."""
    import paper_engine
    monkeypatch.setattr(paper_engine, "account_snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert compute._driver_equity() is None


def test_a_snapshot_with_no_equity_reads_as_None_not_zero(monkeypatch):
    """A 0 denominator would refuse every trade forever — the documented
    "never treat a missing reading as zero" rule."""
    import paper_engine
    monkeypatch.setattr(paper_engine, "account_snapshot", lambda *a, **k: {})
    assert compute._driver_equity() is None
    monkeypatch.setattr(paper_engine, "account_snapshot", lambda *a, **k: {"equity": 0})
    assert compute._driver_equity() is None
