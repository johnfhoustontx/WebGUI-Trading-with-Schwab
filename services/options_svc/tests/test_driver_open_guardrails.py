"""The OPEN path re-checks the driver's envelope; it is not merely downstream of it.

Every risk rule used to live only in ``driver_svc/guardrails.apply_guardrails``,
on the DECISION path. The path that actually opens a position is
``compute.open_driver_position``, reached by the ``driver_paper_create`` command
on ``cmd:options`` - a different service, which cannot import driver_svc. It
checked only the halt flag, per-trade sizing, min fill and buying power.

So anything that enqueued that command directly reached the paper book without a
structure check or a capacity check: a Redis stream replay (consumer groups start
at id 0, so a fresh group re-delivers the backlog), or any local process, since
Memurai is unauthenticated. The driver's own docstring calls guardrails "the
single safety boundary in front of paper execution" - which was true of the
decision path and false of the open path.

These tests drive ``open_driver_position`` DIRECTLY, the way a replayed command
does, and require it to refuse on its own.
"""
import pytest

from services.options_svc import compute


def _spread(**over):
    """A well-formed, allowlisted PCS the open path would otherwise accept."""
    sig = {
        "signal_id": "sig-1", "symbol": "SPY", "type": "PCS", "strategy": "PCS",
        "short_strike": 500.0, "long_strike": 495.0, "width": 5.0,
        "expiration": "2026-09-19", "entry_credit": 1.00, "max_loss": 4.00,
        "dte_at_entry": 21,
    }
    sig.update(over)
    return sig


class _Broker:
    """Fills at the requested credit, so nothing else rejects the trade."""

    def __init__(self):
        self.calls = []

    def submit_order(self, order, client):
        """Mirrors paper_broker.build_order_response, so a trade that PASSES the
        new gates proceeds through the real record/insert path rather than
        failing on a missing field (which would look like a gate rejection)."""
        self.calls.append(order)
        qty = order.get("quantity", 0)
        return {
            "orderId": 1, "status": "FILLED", "orderType": "NET_CREDIT",
            "quantity": qty, "filledQuantity": qty,
            "price": order["limit_price"],
            "enteredTime": "2026-08-29T09:30:00-05:00",
            "closeTime": "2026-08-29T09:30:00-05:00",
            "orderStrategyType": "SINGLE", "complexOrderStrategyType": "VERTICAL",
            "orderLegCollection": [], "statusDescription": "paper fill (test)",
        }


@pytest.fixture
def broker():
    return _Broker()


@pytest.fixture(autouse=True)
def _tmp_driver_book(tmp_path, monkeypatch):
    """Point the driver's paper book at a temp DB.

    The repo-root conftest refuses any sqlite connect into a live data directory
    (it exists because a prior test run wrote 24 synthetic signals and 21 rejected
    orders into BOTH environments). These tests exercise the real
    paper_account_db layer, so they need their own file rather than a stub.
    """
    db = tmp_path / "paper_account_driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", str(db))
    compute.ensure_driver_account()
    return db


# --- structure allowlist ----------------------------------------------------

@pytest.mark.parametrize("bad_structure", ["NAKED_PUT", "DEBIT", "LONG_CALL",
                                           "STRANGLE", "", None])
def test_a_non_allowlisted_structure_is_refused_at_the_open_path(broker, bad_structure):
    """PCS/CCS/IC only. A replayed or hand-enqueued command must not be able to
    open a naked or debit structure into the driver's book just because the
    decision-path allowlist was never consulted."""
    res = compute.open_driver_position(
        _spread(type=bad_structure, strategy=bad_structure), qty=1, broker=broker)
    assert res["status"] == "rejected"
    assert res["reason"] == compute.REJECT_NOT_ALLOWED
    assert broker.calls == [], "must refuse BEFORE submitting an order"


@pytest.mark.parametrize("bad_loss", [0, -1.0])
def test_an_explicit_non_positive_max_loss_is_refused(broker, bad_loss):
    """An explicit 0/negative max_loss is a STATEMENT: no risk, no real position.
    It is not treated as a missing field, so it is not derived around."""
    res = compute.open_driver_position(_spread(max_loss=bad_loss), qty=1, broker=broker)
    assert res["status"] == "rejected"
    assert res["reason"] == compute.REJECT_NOT_ALLOWED
    assert broker.calls == []


@pytest.mark.parametrize("bad_loss", [None, float("nan"), float("inf"), "x"])
def test_undefined_risk_with_nothing_to_derive_from_is_refused(broker, bad_loss):
    """'Defined risk' is the whole premise of what the driver may trade. With no
    usable max_loss AND no width/credit to derive one, the downside is unknown."""
    sig = _spread(max_loss=bad_loss)
    sig.pop("width", None)
    sig.pop("entry_credit", None)
    res = compute.open_driver_position(sig, qty=1, broker=broker)
    assert res["status"] == "rejected"
    assert res["reason"] == compute.REJECT_NOT_ALLOWED
    assert broker.calls == []


@pytest.mark.parametrize("bad_loss", [None, float("nan"), "x"])
def test_risk_is_DERIVED_from_width_and_credit_when_max_loss_is_unusable(bad_loss):
    """A credit spread's risk is width - credit whether or not the producer
    spelled it out. Menu signals carry ``max_loss``; the raw signals that reach
    the OPEN path carry width + entry_credit and leave it to the sizer.

    Requiring the explicit field rejected a perfectly well-defined spread - the
    e2e test caught it, which is why this gate needed an end-to-end test and not
    only unit tests over synthetic signals.
    """
    from shared import driver_policy

    sig = _spread(max_loss=bad_loss)          # width 5.0, credit 1.00
    assert driver_policy.defined_risk_per_share(sig) == 4.0
    assert driver_policy.is_allowed(sig) is True


def test_an_allowlisted_spread_still_opens(broker, monkeypatch):
    """Power check: the new gates must not block the legitimate path."""
    monkeypatch.setattr(compute, "_driver_open_capacity_reason", lambda *a, **k: None)
    res = compute.open_driver_position(_spread(), qty=1, broker=broker)
    assert res["status"] != "rejected" or res["reason"] not in (
        compute.REJECT_NOT_ALLOWED, compute.REJECT_MAX_CONCURRENT,
        compute.REJECT_RISK_BUDGET)


# --- book capacity ----------------------------------------------------------

def test_max_concurrent_is_enforced_on_the_open_path(broker, monkeypatch):
    """The decision path counts slots across a cycle; a direct enqueue skipped
    that entirely, so the book could grow past max_concurrent without limit."""
    from shared import driver_limits

    limit = driver_limits.risk()["max_concurrent"]
    monkeypatch.setattr(compute, "_driver_open_positions",
                        lambda: [{"max_loss_total": 10.0}] * limit)
    res = compute.open_driver_position(_spread(), qty=1, broker=broker)
    assert res["status"] == "rejected"
    assert res["reason"] == compute.REJECT_MAX_CONCURRENT
    assert broker.calls == []


def test_the_daily_risk_budget_counts_ALREADY_OPEN_positions(broker, monkeypatch):
    """The cycle-level budget resets every checkpoint and never subtracted risk
    already deployed, so the real aggregate cap was max_concurrent x per_trade
    (~2x the documented 'half the book'). Measured against the BOOK, the budget
    means what its name says."""
    from shared import driver_limits

    budget = driver_limits.risk()["daily_risk_budget"]
    monkeypatch.setattr(compute, "_driver_open_positions",
                        lambda: [{"max_loss_total": budget}])
    res = compute.open_driver_position(_spread(), qty=1, broker=broker)
    assert res["status"] == "rejected"
    assert res["reason"] == compute.REJECT_RISK_BUDGET
    assert broker.calls == []


def test_a_book_within_budget_is_not_blocked(broker, monkeypatch):
    from shared import driver_limits

    budget = driver_limits.risk()["daily_risk_budget"]
    monkeypatch.setattr(compute, "_driver_open_positions",
                        lambda: [{"max_loss_total": budget * 0.1}])
    res = compute.open_driver_position(_spread(), qty=1, broker=broker)
    # A trade that PASSES has no "reason" key at all - .get, not [].
    assert res.get("reason") != compute.REJECT_RISK_BUDGET


def test_a_corrupt_position_row_does_not_disable_the_cap(broker, monkeypatch):
    """A NaN in the deployed-risk sum would make every '>' comparison False and
    silently switch the budget check off - the documented pins-the-bound class,
    one layer up."""
    from shared import driver_limits

    budget = driver_limits.risk()["daily_risk_budget"]
    monkeypatch.setattr(compute, "_driver_open_positions", lambda: [
        {"max_loss_total": float("nan")}, {"max_loss_total": budget}])
    res = compute.open_driver_position(_spread(), qty=1, broker=broker)
    assert res["status"] == "rejected"
    assert res["reason"] == compute.REJECT_RISK_BUDGET
