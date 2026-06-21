from services.options_svc import rescue


def _pos(**kw):
    base = dict(symbol="$SPX", strategy="PCS", short_strike=5980.0)
    base.update(kw); return base


def test_index_is_cash_settled_note():
    ctx = rescue.strategic_context(_pos(), gex=None, regime=None, underlying=5990.0)
    assert any("cash-settled" in s.lower() or "european" in s.lower() for s in ctx["notes"])
    assert ctx["assignment_risk"] is False


def test_futures_flag_assignment_risk():
    ctx = rescue.strategic_context(
        _pos(symbol="/ES", short_strike=5980.0),
        gex=None, regime=None, underlying=5950.0)  # deep below short
    assert ctx["assignment_risk"] is True
    assert any("assignment" in s.lower() for s in ctx["notes"])


def test_below_flip_flags_negative_gamma():
    ctx = rescue.strategic_context(
        _pos(), gex={"flip": 5995.0, "put_wall": 5950.0},
        regime=None, underlying=5985.0)
    assert ctx["negative_gamma"] is True
    assert any("flip" in s.lower() for s in ctx["notes"])
