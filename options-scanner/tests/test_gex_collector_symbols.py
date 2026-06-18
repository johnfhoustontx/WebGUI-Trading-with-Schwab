"""Tests for gex_collector.collection_symbols (dynamic watchlist union)."""
import gex_collector as gc


def test_collection_symbols_unions_base_and_watchlist(monkeypatch):
    monkeypatch.setattr(gc, "SYMBOLS", ["$SPX", "$VIX", "SPY", "QQQ"])
    import watchlist
    monkeypatch.setattr(watchlist, "get_scan_symbols",
                        lambda: ["$SPX", "SPY", "QQQ", "NVDA", "TSLA"])
    out = gc.collection_symbols()
    assert out == ["$SPX", "$VIX", "SPY", "QQQ", "NVDA", "TSLA"]


def test_collection_symbols_falls_back_to_base_on_error(monkeypatch):
    monkeypatch.setattr(gc, "SYMBOLS", ["$SPX", "$VIX", "SPY", "QQQ"])
    import watchlist

    def _boom():
        raise RuntimeError("watchlist unreadable")

    monkeypatch.setattr(watchlist, "get_scan_symbols", _boom)
    assert gc.collection_symbols() == ["$SPX", "$VIX", "SPY", "QQQ"]


def test_poll_once_defaults_to_collection_symbols(monkeypatch):
    seen = []

    class _Client:
        class Options:
            class ContractType:
                ALL = "ALL"

        def get_option_chain(self, symbol, **kw):
            seen.append(symbol)
            class _R:
                status_code = 500
                def json(self):
                    return None
            return _R()

    monkeypatch.setattr(gc, "collection_symbols",
                        lambda: ["$SPX", "NVDA", "TSLA"])
    monkeypatch.setattr(gc, "poll_term_once", lambda *a, **k: None)

    class _Conn:
        def commit(self):
            pass

    gc.poll_once(_Client(), object(), _Conn())
    assert seen == ["$SPX", "NVDA", "TSLA"]


def test_poll_once_honors_explicit_symbols(monkeypatch):
    seen = []

    class _Client:
        class Options:
            class ContractType:
                ALL = "ALL"

        def get_option_chain(self, symbol, **kw):
            seen.append(symbol)
            class _R:
                status_code = 500
                def json(self):
                    return None
            return _R()

    monkeypatch.setattr(gc, "poll_term_once", lambda *a, **k: None)
    monkeypatch.setattr(gc, "collection_symbols",
                        lambda: (_ for _ in ()).throw(AssertionError("should not be called")))

    class _Conn:
        def commit(self):
            pass

    gc.poll_once(_Client(), object(), _Conn(), symbols=["SPY"])
    assert seen == ["SPY"]
