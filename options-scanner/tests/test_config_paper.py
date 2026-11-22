import config_paper as cp


def test_paper_mode_is_a_bool():
    assert isinstance(cp.PAPER_MODE, bool)


def test_risk_constants_present_and_sane():
    assert cp.STARTING_BALANCE == 25_000.0
    assert cp.MAX_RISK_PER_TRADE == 1500.0
    assert cp.LEDGER_MAX_RISK_PER_TRADE == 750.0
    assert cp.MAX_RISK_PER_SYMBOL == 1500.0
    assert cp.MAX_RISK_PER_SECTOR == 3000.0
    assert cp.LEDGER_MAX_RISK_PER_SYMBOL == 750.0
    assert cp.LEDGER_MAX_RISK_PER_SECTOR == 1500.0
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


def test_the_symbol_and_sector_caps_are_read_from_paper_toml(monkeypatch):
    """Same discriminating shape: patch each accessor, reload, read it back."""
    import importlib

    from shared import paper_limits

    monkeypatch.setattr(paper_limits, "max_risk_per_symbol", lambda: 333.0)
    monkeypatch.setattr(paper_limits, "max_risk_per_sector", lambda: 444.0)
    monkeypatch.setattr(paper_limits, "ledger_max_risk_per_symbol", lambda: 555.0)
    monkeypatch.setattr(paper_limits, "ledger_max_risk_per_sector", lambda: 666.0)
    try:
        importlib.reload(cp)
        assert cp.MAX_RISK_PER_SYMBOL == 333.0
        assert cp.MAX_RISK_PER_SECTOR == 444.0
        assert cp.LEDGER_MAX_RISK_PER_SYMBOL == 555.0
        assert cp.LEDGER_MAX_RISK_PER_SECTOR == 666.0
    finally:
        monkeypatch.undo()
        importlib.reload(cp)


def test_each_book_gets_its_own_risk_caps():
    """The Account's policy and the Ledger's differ only in the three risk caps."""
    import paper_concentration as pc

    account, ledger = pc.default_limits(), pc.ledger_limits()
    assert account["max_risk_per_symbol"] == cp.MAX_RISK_PER_SYMBOL
    assert account["max_risk_per_sector"] == cp.MAX_RISK_PER_SECTOR
    assert ledger["max_risk_per_trade"] == cp.LEDGER_MAX_RISK_PER_TRADE
    assert ledger["max_risk_per_symbol"] == cp.LEDGER_MAX_RISK_PER_SYMBOL
    assert ledger["max_risk_per_sector"] == cp.LEDGER_MAX_RISK_PER_SECTOR
    shared_keys = set(account) - {"max_risk_per_symbol", "max_risk_per_sector"}
    assert {k: account[k] for k in shared_keys} == {k: ledger[k] for k in shared_keys}
