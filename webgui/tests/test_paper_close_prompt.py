"""D3: the Close dialog asked a debit position for an "Exit debit".

You do not pay a debit to close a long call — you RECEIVE a credit, and the
number the engine wants is the position's value. The label said the opposite, and
behind it ``close_paper_trade`` booked the P&L with the credit formula (fixed in
``paper_trader``), so the wrong word was sitting on top of a wrong number.

⚠ The rule the repo's copy pass set: a label names what the number is FOR, and a
``Credit`` column is wrong wherever the book holds debits. Same defect, one layer
up.
"""
from pages.options import paper


def _credit_row():
    return {"trade_id": "c1", "strategy": "PCS", "symbol": "SPY",
            "entry_credit": 0.60}


def _debit_row():
    return {"trade_id": "d1", "strategy": "LONG_CALL", "symbol": "SPY",
            "direction": "DEBIT", "entry_credit": -2.00, "entry_debit": 200.0}


def test_a_credit_spread_is_asked_for_the_DEBIT_it_costs_to_close():
    label = paper.close_prompt_label(_credit_row())
    assert "debit" in label.lower()
    assert "credit" not in label.lower()


def test_a_debit_position_is_asked_for_the_CREDIT_it_pays_to_close():
    label = paper.close_prompt_label(_debit_row())
    assert "credit" in label.lower()
    assert "debit" not in label.lower()


def test_both_labels_name_the_UNIT_so_a_per_contract_figure_is_not_typed_in():
    """⚠ The ledger stores per-SHARE prices; typing 200 instead of 2.00 books a
    100x result, and nothing downstream would flag it."""
    for row in (_credit_row(), _debit_row()):
        assert "per spread" in paper.close_prompt_label(row).lower()


def test_a_row_with_no_direction_reads_as_a_CREDIT_spread():
    """Every pre-D3 ledger row: absent means credit, which is what it was."""
    assert "debit" in paper.close_prompt_label({"strategy": "PCS"}).lower()


def test_it_never_raises_on_a_junk_row():
    for bad in (None, {}, {"direction": None}, {"direction": 7}):
        assert isinstance(paper.close_prompt_label(bad), str)
