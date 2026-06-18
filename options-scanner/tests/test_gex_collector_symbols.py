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
