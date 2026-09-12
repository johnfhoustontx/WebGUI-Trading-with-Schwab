"""C4: the book's net Greeks, summed across its open positions.

Design: docs/plans/2026-09-12-book-greeks-design.md.

The stored values are PER CONTRACT and signed by side, so a credit book reads
positive theta. This aggregate multiplies by quantity and reports how much of the
book it actually covered.

⚠ The absence rules carry the weight. A greek of ``None`` means **no position had
a reading**, never 0.0 — a flat book and an unpriced book are different facts, and
the second rendering as the first is this repo's costliest bug class.
"""
import pytest

from services.options_svc import compute


def _pos(qty=1, **greeks):
    base = {"net_delta": 0.12, "net_gamma": -0.004, "net_theta": 0.02,
            "net_vega": -0.04, "quantity": qty}
    base.update(greeks)
    return base


def test_the_sums_are_greek_times_quantity():
    got = compute.book_greeks([_pos(qty=2), _pos(qty=3)])
    assert got["net_delta"] == pytest.approx(0.12 * 5)
    assert got["net_theta"] == pytest.approx(0.02 * 5)


def test_a_credit_book_reads_POSITIVE_theta_and_NEGATIVE_vega():
    """The sanity check a sign error would fail: premium sellers earn time and are
    short volatility."""
    got = compute.book_greeks([_pos(), _pos()])
    assert got["net_theta"] > 0
    assert got["net_vega"] < 0
    assert got["net_gamma"] < 0


def test_opposing_positions_can_sum_to_a_flat_delta():
    """And that zero is a REAL reading, which is why absence cannot also be 0."""
    got = compute.book_greeks([_pos(net_delta=0.12), _pos(net_delta=-0.12)])
    assert got["net_delta"] == pytest.approx(0.0)
    assert got["positions_priced"] == 2


def test_it_reports_how_much_of_the_book_it_covered():
    """A partial book is normal on the first cycle after a restart, and a total
    that silently omits positions is worse than one that says so."""
    got = compute.book_greeks([_pos(), {"quantity": 1}, {"quantity": 2}])
    assert got["positions_total"] == 3
    assert got["positions_priced"] == 1


def test_an_unpriced_book_reports_None_not_zero():
    got = compute.book_greeks([{"quantity": 1}, {"quantity": 2}])
    assert got["net_delta"] is None and got["net_theta"] is None
    assert got["positions_total"] == 2 and got["positions_priced"] == 0


def test_an_empty_book_reports_None_not_zero():
    got = compute.book_greeks([])
    assert all(got[g] is None for g in
               ("net_delta", "net_gamma", "net_theta", "net_vega"))
    assert got["positions_total"] == 0


@pytest.mark.parametrize("bad", [None, "nope", 7, [None, "x", 3]])
def test_junk_input_degrades_rather_than_raising(bad):
    got = compute.book_greeks(bad)
    assert got["positions_total"] == 0 or got["positions_priced"] == 0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "0.2", True])
def test_a_non_finite_or_non_numeric_greek_is_skipped(bad):
    """⚠ A NaN in the sum would make every later comparison against the total
    False — the documented pins-the-bound trap."""
    got = compute.book_greeks([_pos(net_delta=bad), _pos(net_delta=0.10)])
    assert got["net_delta"] == pytest.approx(0.10)


def test_a_missing_quantity_counts_as_one_contract():
    got = compute.book_greeks([{"net_delta": 0.12}])
    assert got["net_delta"] == pytest.approx(0.12)


@pytest.mark.parametrize("qty", [0, -3, None, "2", 1.5])
def test_an_unusable_quantity_counts_as_one_rather_than_zeroing_the_row(qty):
    """A zero multiplier would silently drop a real position from the book's risk."""
    got = compute.book_greeks([_pos(qty=qty)])
    assert got["net_delta"] == pytest.approx(0.12)


def test_one_greek_can_be_present_while_another_is_absent():
    """Partial chain data is normal off-hours; a usable direction must survive a
    missing vega."""
    got = compute.book_greeks([{"net_delta": 0.12, "quantity": 1}])
    assert got["net_delta"] == pytest.approx(0.12)
    assert got["net_vega"] is None


def test_the_paper_account_view_publishes_the_book_greeks(monkeypatch):
    """Driven from the PRODUCER: the page reads this key off the cache view."""
    import paper_account_db
    import paper_engine
    monkeypatch.setattr(paper_engine, "account_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(paper_account_db, "fetch_open_positions",
                        lambda *a, **k: [_pos(qty=2)])
    monkeypatch.setattr(paper_account_db, "fetch_orders", lambda *a, **k: [])
    monkeypatch.setattr(paper_account_db, "fetch_open_lots", lambda *a, **k: [])
    monkeypatch.setattr(paper_account_db, "get_account", lambda *a, **k: {"cash": 1})
    monkeypatch.setattr(paper_account_db, "fetch_all_positions", lambda *a, **k: [])

    view = compute.paper_account_view()
    assert view["greeks"]["net_delta"] == pytest.approx(0.24)
    assert view["greeks"]["positions_total"] == 1
