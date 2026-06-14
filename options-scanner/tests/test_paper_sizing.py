import paper_sizing


def test_sizes_off_defined_risk():
    # width 5.00, credit 1.00 -> max loss per = (5-1)*100 = 400
    # floor(250/400) == 0  -> too rich for ONE contract
    qty, mlp = paper_sizing.size_contracts(credit=1.00, width=5.00, max_risk=250)
    assert mlp == 400.0
    assert qty == 0


def test_sizes_multiple_contracts():
    # width 1.00, credit 0.50 -> max loss per = 50 ; floor(250/50) = 5
    qty, mlp = paper_sizing.size_contracts(credit=0.50, width=1.00, max_risk=250)
    assert mlp == 50.0
    assert qty == 5


def test_degenerate_credit_ge_width_rejects():
    qty, mlp = paper_sizing.size_contracts(credit=1.20, width=1.00, max_risk=250)
    assert qty == 0
    assert mlp <= 0
