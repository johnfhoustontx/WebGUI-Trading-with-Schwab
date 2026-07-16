from shared import symbols as sym


def test_canonical_sets():
    assert sym.SPX == "$SPX"
    assert sym.VIX == "$VIX"
    assert sym.SCAN_BASE == ("$SPX", "SPY", "QQQ")
    assert sym.COLLECTION_BASE == ("$SPX", "$VIX", "SPY", "QQQ")
    assert sym.INDEX_SYMBOLS == frozenset({"$SPX", "SPY", "QQQ"})


def test_index_roots_and_classification():
    assert "NDX" in sym.INDEX_ROOTS
    assert sym.is_index_symbol("$SPX") is True
    assert sym.is_index_symbol("spx") is True
    assert sym.is_index_symbol("AAPL") is False
    assert sym.is_index_symbol("") is False
    assert sym.is_index_symbol(None) is False
