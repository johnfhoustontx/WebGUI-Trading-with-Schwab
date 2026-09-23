import config_paper as cp


def test_paper_mode_is_a_bool():
    assert isinstance(cp.PAPER_MODE, bool)


def test_risk_constants_present_and_sane():
    assert cp.STARTING_BALANCE == 25_000.0
    assert cp.MAX_RISK_PER_TRADE == 750.0
    assert cp.LEDGER_MAX_RISK_PER_TRADE == 750.0
    assert cp.MAX_SESSION_DRAWDOWN == 2_500.0
    assert cp.MIN_ENTRY_SCORE == 60
    assert cp.OPEN_BUFFER_MIN == 5
    assert cp.MIN_FILL_CREDIT == 0.10


def test_the_per_trade_caps_are_read_from_paper_toml(monkeypatch):
    """The discriminating test: a literal equal to the shipped value would pass an
    equality check, so patch the accessor and reload the consumer."""
    import importlib

    from shared import paper_limits

    monkeypatch.setattr(paper_limits, "max_risk_per_trade", lambda: 111.0)
    monkeypatch.setattr(paper_limits, "ledger_max_risk_per_trade", lambda: 222.0)
    try:
        importlib.reload(cp)
        assert cp.MAX_RISK_PER_TRADE == 111.0
        assert cp.LEDGER_MAX_RISK_PER_TRADE == 222.0
    finally:
        monkeypatch.undo()
        importlib.reload(cp)
