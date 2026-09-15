from shared.bus import Bus
from shared.bus.client import reset_fake_bus

import bus_client
from pages.options import checks_feed


def _fresh(monkeypatch):
    """Module-level read_gated memos outlive a test; a fresh bus needs fresh memos."""
    reset_fake_bus()
    for memo in checks_feed._memos.values():
        memo.clear()


def test_context_indexes_the_board_by_symbol_and_reads_the_other_views(monkeypatch):
    _fresh(monkeypatch)
    bus = Bus(fake=True)
    monkeypatch.setattr(bus_client, "_bus", bus)
    bus.cache_set("cache:options:matrix", {"rows": [{"symbol": "ORCL", "spot": 110.0}]})
    bus.cache_set("cache:sentiment:regime", {"direction": 1})
    bus.cache_set("cache:options:ledger_caps", {"limits": {}})
    ctx = checks_feed.read_context()
    assert ctx["matrix"]["ORCL"]["spot"] == 110.0
    assert ctx["regime"]["direction"] == 1
    assert ctx["caps"] == {"limits": {}}
    # A cold view is None, never {}: checks marks None as a missing view, so the
    # summary reads "Partly checked" instead of "Clear" (Task 17 review).
    assert ctx["calibration"] is None


def test_a_cold_bus_yields_empty_context_not_a_raise(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(bus_client, "_bus", Bus(fake=True))
    ctx = checks_feed.read_context()
    assert ctx == {"matrix": None, "regime": None, "calibration": None, "caps": None}


def test_versions_lists_the_views_that_should_refresh_the_column():
    assert set(checks_feed.REFRESH_VIEWS) == {"options:ledger_caps", "sentiment:regime"}


def test_a_raising_bus_read_costs_only_that_view(monkeypatch):
    _fresh(monkeypatch)
    bus = Bus(fake=True)
    monkeypatch.setattr(bus_client, "_bus", bus)
    bus.cache_set("cache:sentiment:regime", {"direction": -1})
    real = bus_client.read_gated

    def flaky(view, memo):
        if view == checks_feed.MATRIX_VIEW:
            raise ConnectionError("down")
        return real(view, memo)
    monkeypatch.setattr(bus_client, "read_gated", flaky)
    ctx = checks_feed.read_context()
    assert ctx["matrix"] is None and ctx["regime"] == {"direction": -1}


def test_board_symbols_are_matched_case_insensitively(monkeypatch):
    _fresh(monkeypatch)
    bus = Bus(fake=True)
    monkeypatch.setattr(bus_client, "_bus", bus)
    bus.cache_set("cache:options:matrix", {"rows": [{"symbol": "orcl", "spot": 110.0}, "junk"]})
    ctx = checks_feed.read_context()
    assert set(ctx["matrix"]) == {"ORCL"}


def test_checks_for_passes_a_missing_board_row_as_none_so_the_summary_is_partly_checked(monkeypatch):
    from pages.options import checks
    row = {"id": "a", "symbol": "XOM", "type": "PCS", "trade_type": "SWING",
           "expiration": "2026-10-17", "dte": 12, "short_strike": 100.0, "long_strike": 97.5,
           "credit": 0.60, "iv_rank": 55.0, "iv_rank_known": True, "vol_floor": 30,
           "friction_pct": 8.0, "em_to_expiry": 6.0, "earnings_status": "none_scheduled",
           "earnings_date": None, "_allow_paper": False, "underlying_price": 110.0}
    ctx = {"matrix": {"ORCL": {"spot": 110.0}}, "regime": {"direction": 1},
           "calibration": None, "caps": None}
    out = checks_feed.checks_for(row, ctx)
    assert checks.summary(out)["text"].startswith("Partly checked")


def test_the_module_imports_no_ui_framework():
    import ast, inspect
    tree = ast.parse(inspect.getsource(checks_feed))
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
           {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in mods if m.split(".")[0] in {"nicegui", "services", "sqlite3"}}
