from services.options_svc import rescue


def _c(**kw):
    base = dict(action="roll_down_out", net_cash=10.0, new_max_loss=300.0,
                new_short_delta=0.15, new_breakeven=495.0, commission=2.6)
    base.update(kw); return base


def test_max_loss_reduction_outranks_small_credit():
    big_cut = rescue.score_candidate(_c(new_max_loss=100.0), old_max_loss=400.0,
                                     old_short_delta=0.40, ctx={})
    small_cut = rescue.score_candidate(_c(new_max_loss=380.0), old_max_loss=400.0,
                                       old_short_delta=0.40, ctx={})
    assert big_cut > small_cut


def test_debit_is_penalized():
    credit = rescue.score_candidate(_c(net_cash=15.0), old_max_loss=400.0,
                                    old_short_delta=0.40, ctx={})
    debit = rescue.score_candidate(_c(net_cash=-15.0), old_max_loss=400.0,
                                   old_short_delta=0.40, ctx={})
    assert credit > debit


def test_roll_penalized_in_negative_gamma():
    normal = rescue.score_candidate(_c(action="roll_down_out"), old_max_loss=400.0,
                                    old_short_delta=0.40, ctx={"negative_gamma": False})
    risky = rescue.score_candidate(_c(action="roll_down_out"), old_max_loss=400.0,
                                   old_short_delta=0.40, ctx={"negative_gamma": True})
    assert risky < normal
