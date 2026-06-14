"""Tests that the scanners source their default symbols from the watchlist."""
import scanner_engine


def test_run_full_scan_defaults_to_watchlist(monkeypatch):
    called = {}
    monkeypatch.setattr(scanner_engine, "get_scan_symbols",
                        lambda: ["$SPX", "SPY", "QQQ", "NVDA"])

    def fake_fetch_quotes(client, syms):
        called["syms"] = syms
        return {}  # empty -> run_full_scan returns early

    monkeypatch.setattr(scanner_engine, "fetch_quotes", fake_fetch_quotes)
    scanner_engine.run_full_scan(client=None)
    assert "NVDA" in called["syms"]
    assert called["syms"][:3] == ["$SPX", "SPY", "QQQ"]


def _quote(price):
    return {"quote": {"lastPrice": price}}


def test_no_data_sets_error(monkeypatch):
    monkeypatch.setattr(scanner_engine, "get_scan_symbols",
                        lambda: ["$SPX", "SPY", "QQQ"])
    monkeypatch.setattr(scanner_engine, "get_load_error", lambda: None)
    monkeypatch.setattr(scanner_engine, "fetch_quotes",
                        lambda client, syms: {})
    res = scanner_engine.run_full_scan(client=None)
    assert res["errors"]
    assert res["signals_0dte"] == []


def test_dropped_symbol_sets_warning(monkeypatch):
    monkeypatch.setattr(scanner_engine, "get_scan_symbols",
                        lambda: ["$SPX", "SPY", "QQQ", "KEEL"])
    monkeypatch.setattr(scanner_engine, "get_load_error", lambda: None)

    def fake_quotes(client, syms):
        # KEEL omitted -> dropped; VIX present so scan proceeds past quotes
        return {"$SPX": _quote(7400), "SPY": _quote(740), "QQQ": _quote(710),
                "$VIX": _quote(15)}

    monkeypatch.setattr(scanner_engine, "fetch_quotes", fake_quotes)
    # Make per-symbol fetch a no-op so the scan returns quickly.
    monkeypatch.setattr(scanner_engine, "fetch_price_history",
                        lambda *a, **k: [])
    monkeypatch.setattr(scanner_engine, "fetch_option_chain",
                        lambda *a, **k: {})
    res = scanner_engine.run_full_scan(client=None)
    assert any("KEEL" in w for w in res["warnings"])


def test_watchlist_load_error_sets_warning(monkeypatch):
    monkeypatch.setattr(scanner_engine, "get_scan_symbols",
                        lambda: ["$SPX", "SPY", "QQQ"])
    monkeypatch.setattr(scanner_engine, "get_load_error",
                        lambda: "Watchlist file not found: Top 20.xlsx")
    monkeypatch.setattr(scanner_engine, "fetch_quotes",
                        lambda client, syms: {})
    res = scanner_engine.run_full_scan(client=None)
    assert any("Watchlist" in w for w in res["warnings"])
