import config_paper as cp


def test_paper_mode_is_a_bool():
    assert isinstance(cp.PAPER_MODE, bool)


def test_risk_constants_present_and_sane():
    assert cp.STARTING_BALANCE == 25_000.0
    assert cp.MAX_RISK_PER_TRADE == 250.0
    assert cp.MAX_SESSION_DRAWDOWN == 2_500.0
    assert cp.MIN_ENTRY_SCORE == 60
    assert cp.SLIPPAGE_TICKS in (1, 2)
    assert cp.OPTION_TICK == 0.05
    assert 2 <= cp.ENTRY_CYCLE_MIN <= 5
    assert cp.MANAGE_CYCLE_MIN == 15
    assert cp.OPEN_BUFFER_MIN == 5
    assert cp.MIN_FILL_CREDIT == 0.10
