"""Tests for the options service compute module (Task 2.2).

``compute.run_scan`` is a thin wrapper over ``scanner_engine.run_full_scan``
called with the shared schwab-py-compatible proxy client. We monkeypatch the
eagerly-imported ``run_full_scan`` name so nothing touches a live proxy.

Also asserts the ``options_scoring()`` collision guard (used in the GUI's
``webgui/pages/options/scanner.py``) was intentionally NOT ported: this service
process loads no sentiment code, so ``import scoring`` resolves to
options-scanner's unambiguously and the guard is unnecessary.
"""
import pytest

from services import _proxy
from services.options_svc import compute


def test_run_scan_calls_engine(monkeypatch):
    sentinel = {"signals_0dte": [], "signals_swing": []}
    seen = {"args": None}

    def _rec(client, *a, **k):
        seen["args"] = client
        return sentinel

    monkeypatch.setattr(compute, "run_full_scan", _rec)

    out = compute.run_scan()
    assert out is sentinel
    assert seen["args"] is _proxy.schwab_py_client


def test_compute_no_scoring_guard():
    # The options_scoring() collision guard must NOT be ported here.
    assert not hasattr(compute, "options_scoring")


# ── R6: reconcile buying power for both books ────────────────────────────────
def test_reconcile_paper_buying_power_both_books(monkeypatch):
    """``reconcile_paper_buying_power`` reconciles BOTH the manual (default DB)
    and the driver (DRIVER_PAPER_DB) accounts, returning the per-book drift."""
    import paper_account_db

    seen = []

    def _fake_reconcile(db_path):
        seen.append(db_path)
        return 200.0 if db_path is None else 50.0

    monkeypatch.setattr(paper_account_db, "reconcile_buying_power", _fake_reconcile)

    out = compute.reconcile_paper_buying_power()

    assert out == {"manual": 200.0, "driver": 50.0}
    # None (default DB) for manual, DRIVER_PAPER_DB for the driver book.
    assert None in seen and compute.DRIVER_PAPER_DB in seen


def test_reconcile_paper_buying_power_defensive(monkeypatch, caplog):
    """A reconcile failure on one book is logged + degrades to 0.0, never raises."""
    import paper_account_db

    def _boom(db_path):
        raise RuntimeError("db locked")

    monkeypatch.setattr(paper_account_db, "reconcile_buying_power", _boom)

    with caplog.at_level("ERROR"):
        out = compute.reconcile_paper_buying_power()

    assert out == {"manual": 0.0, "driver": 0.0}
    assert any("reconcile degraded" in r.message for r in caplog.records)


# ── R3b: silent degradation now logs ─────────────────────────────────────────
def test_paper_trades_view_read_failure_logs(monkeypatch, caplog):
    """A ledger-read failure is log.exception'd (not silently swallowed) and
    degrades to an empty ledger."""
    import paper_trader

    def _boom():
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(paper_trader, "get_all_trades", _boom)

    with caplog.at_level("ERROR"):
        out = compute.paper_trades_view(reprice=False)

    assert out == {"trades": []}
    assert any("paper_trades_view read degraded" in r.message for r in caplog.records)


# ── Swing scan (moved from webgui/pages/options/swing.py) ────────────────────
def test_assign_ids_adds_unique_ids():
    out = compute.assign_ids([{"symbol": "MU"}, {"symbol": "MU"}], "MU")
    ids = [s["id"] for s in out]
    assert len(set(ids)) == 2
    assert all(i.startswith("MU") for i in ids)


def test_assign_ids_preserves_existing():
    assert compute.assign_ids([{"symbol": "MU", "id": "keep"}], "MU")[0]["id"] == "keep"


def _swing_chain(exp_str="2026-07-15", dte=15):
    """A minimal multi-strategy chain: a few call + put strikes (each with the
    greeks ``strategy_scanner.extract_options`` needs) within the DTE window so
    ``build_directional`` / ``build_debit_verticals`` produce real signals."""
    def leg(delta, mark):
        return [{"delta": delta, "mark": mark, "bid": mark - 0.05, "ask": mark + 0.05,
                 "theta": -0.03, "vega": 0.10, "gamma": 0.01, "volatility": 22.0,
                 "totalVolume": 1000, "openInterest": 5000}]
    key = f"{exp_str}:{dte}"
    return {
        "underlyingPrice": 540.0,
        "callExpDateMap": {key: {
            "535.0": leg(0.60, 8.0), "545.0": leg(0.40, 4.0),
            "555.0": leg(0.28, 2.0),
        }},
        "putExpDateMap": {key: {
            "545.0": leg(-0.60, 8.0), "535.0": leg(-0.40, 4.0),
            "525.0": leg(-0.28, 2.0),
        }},
    }


def test_swing_scan_multistrategy_pipeline(monkeypatch):
    """The new multi-strategy pipeline: infer a view, build DIRECTIONAL + VERTICAL
    (incl. adapted PCS) + NEUTRAL candidates from the REAL strategy_scanner, score
    them with the REAL strategy_scoring, and return ``{"signals", "view"}``."""
    calls = {}

    chain = _swing_chain()
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None: (
                            calls.__setitem__("chain_client", client), chain)[1])
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda symbol: (calls.__setitem__("quote_symbol", symbol),
                                        {"last": 540.0})[1])
    monkeypatch.setattr(compute.se, "fetch_price_history",
                        lambda client, symbol: {"hist": True})
    monkeypatch.setattr(compute.se, "calc_technicals",
                        lambda hist: {"trend": "BULLISH", "rsi14": 60,
                                      "price": 540.0, "sma20": 530.0})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda client, symbol, price=None, hist=None, chain=None: (
                            calls.__setitem__("iv_price", price),
                            {"iv_rank": 50.0,
                             "expected_moves": {"daily": {"move_dollars": 5.0}}})[1])

    def _screen(chain, symbol, dte_min, dte_max, put_d_min, put_d_max,
                call_d_min, call_d_max, min_cr, kind, spot=None, daily_expected_move=None):
        calls["screen"] = dict(min_cr=min_cr, kind=kind, spot=spot,
                               dem=daily_expected_move)
        return [{"symbol": symbol, "type": "PCS", "short_strike": 530.0,
                 "long_strike": 525.0, "short_mark": 1.2, "long_mark": 0.6,
                 "credit": 0.6, "max_loss": 4.4, "expiration": "2026-07-15",
                 "underlying_price": 540.0}]

    monkeypatch.setattr(compute.se, "screen_spreads", _screen)
    monkeypatch.setattr(compute.se, "build_iron_condors", lambda spreads: [])

    out = compute.swing_scan("SPY", 5, 30, -0.20, -0.10, 0.10, 0.20, 0.10)

    # Return shape: a dict with signals (list) + view (dict).
    assert isinstance(out, dict)
    assert isinstance(out["signals"], list)
    assert isinstance(out["view"], dict)

    # Clients / fetch wiring preserved.
    assert calls["chain_client"] is compute._proxy.schwab_py_client
    assert calls["quote_symbol"] == "SPY"
    assert calls["iv_price"] == 540.0

    # screen_spreads still called with SWING + spot + the daily EM.
    assert calls["screen"]["kind"] == "SWING"
    assert calls["screen"]["spot"] == 540.0
    assert calls["screen"]["dem"] == 5.0

    # The inferred view came from the technicals (bullish trend).
    assert out["view"]["direction"] == "bullish"

    types = {s["type"] for s in out["signals"]}
    # A DIRECTIONAL family member (e.g. LONG_CALL) AND the adapted VERTICAL (PCS).
    assert "LONG_CALL" in types
    assert "PCS" in types
    # Every signal was scored.
    assert out["signals"] and all("composite_score" in s for s in out["signals"])
    # ids assigned.
    assert all(s.get("id") for s in out["signals"])


def test_swing_scan_families_filter(monkeypatch):
    """``families`` restricts which candidate families are built (NEUTRAL-only ->
    screen_spreads still runs for the IC feed, and ONLY a NEUTRAL signal results).

    ``build_iron_condors`` returns ONE IC so the signal list is non-empty — that
    makes the exclusion assertions actually bite (a broken filter that let a
    DIRECTIONAL/VERTICAL signal through would fail here, not pass vacuously)."""
    chain = _swing_chain()
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None: chain)
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda symbol: {"last": 540.0})
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda client, symbol: {"h": 1})
    monkeypatch.setattr(compute.se, "calc_technicals", lambda hist: {"trend": "NEUTRAL"})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda client, symbol, price=None, hist=None, chain=None:
                        {"expected_moves": {"daily": {"move_dollars": 5.0}}})

    seen = {"screened": False}

    def _screen(*a, **k):
        seen["screened"] = True
        return [{"type": "PCS", "short_strike": 530.0, "long_strike": 525.0,
                 "short_mark": 1.2, "long_mark": 0.6, "credit": 0.6, "max_loss": 4.4,
                 "expiration": "2026-07-15", "underlying_price": 540.0}]

    ic = {"type": "IC", "symbol": "SPY", "short_strike": 525.0, "long_strike": 520.0,
          "short_mark": 1.1, "long_mark": 0.5, "call_short": 555.0, "call_long": 560.0,
          "call_short_mark": 1.1, "call_long_mark": 0.5, "credit": 1.2, "max_loss": 3.8,
          "expiration": "2026-07-15", "underlying_price": 540.0}

    monkeypatch.setattr(compute.se, "screen_spreads", _screen)
    monkeypatch.setattr(compute.se, "build_iron_condors", lambda spreads: [ic])

    out = compute.swing_scan("SPY", 5, 30, -0.20, -0.10, 0.10, 0.20, 0.10,
                             families=["NEUTRAL"])
    # NEUTRAL requested -> screen_spreads ran (feeds the IC builder).
    assert seen["screened"] is True
    # Non-empty: the one adapted iron condor (NEUTRAL family) is present.
    assert out["signals"]
    assert any(s.get("family") == "NEUTRAL" for s in out["signals"])
    # No DIRECTIONAL / VERTICAL signals leaked through the filter.
    assert all(s.get("family") not in ("DIRECTIONAL", "VERTICAL") for s in out["signals"])
    assert not any(s["type"] in ("LONG_CALL", "LONG_PUT", "SHORT_CALL", "SHORT_PUT",
                                 "BULL_CALL", "BEAR_PUT", "PCS", "CCS")
                   for s in out["signals"])


def test_swing_scan_empty_when_no_chain(monkeypatch):
    """A missing chain (off-hours/weekend -> fetch returns None) degrades to an
    explicit empty result instead of raising on the new chain.get/extract_options
    consumers."""
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None: None)
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda symbol: {"last": 540.0})

    out = compute.swing_scan("SPY", 5, 30, -0.20, -0.10, 0.10, 0.20, 0.10)
    assert out == {"signals": [], "view": {}}


def test_swing_scan_empty_when_no_spot(monkeypatch):
    """A chain present but no resolvable spot (off-hours quote miss + a chain
    lacking ``underlyingPrice``) degrades to an explicit empty result instead of
    raising in the builders (spot=None -> spot*0.20 / spot*atm_iv TypeError)."""
    # Chain present but WITHOUT underlyingPrice, and the quote returns no last.
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None:
                        {"callExpDateMap": {}, "putExpDateMap": {}})
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda symbol: {})

    out = compute.swing_scan("SPY", 5, 30, -0.20, -0.10, 0.10, 0.20, 0.10)
    assert out == {"signals": [], "view": {}}


def _swing_scan_market_state_env(monkeypatch):
    """Shared fixture wiring for the market-state tilt tests: a bullish view + one
    adapted PCS candidate (which carries a non-zero `lack_of_bearishness` tilt)."""
    chain = _swing_chain()
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None: chain)
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda symbol: {"last": 540.0})
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda client, symbol: {"h": 1})
    monkeypatch.setattr(compute.se, "calc_technicals",
                        lambda hist: {"trend": "BULLISH", "rsi14": 60,
                                      "price": 540.0, "sma20": 530.0})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda client, symbol, price=None, hist=None, chain=None:
                        {"iv_rank": 50.0,
                         "expected_moves": {"daily": {"move_dollars": 5.0}}})
    monkeypatch.setattr(compute.se, "screen_spreads",
                        lambda *a, **k: [{"symbol": "SPY", "type": "PCS",
                                          "short_strike": 530.0, "long_strike": 525.0,
                                          "short_mark": 1.2, "long_mark": 0.6,
                                          "credit": 0.6, "max_loss": 4.4,
                                          "expiration": "2026-07-15",
                                          "underlying_price": 540.0}])
    monkeypatch.setattr(compute.se, "build_iron_condors", lambda spreads: [])


def test_swing_scan_threads_market_state_tilt(monkeypatch):
    """A live committed market state threads into score_all -> the PCS signal
    carries a non-zero family-tilt (`lack_of_bearishness` favors put credit)."""
    _swing_scan_market_state_env(monkeypatch)
    out = compute.swing_scan("SPY", 5, 30, -0.20, -0.10, 0.10, 0.20, 0.10,
                             market_state="lack_of_bearishness")
    pcs = [s for s in out["signals"] if s["type"] == "PCS"]
    assert pcs and pcs[0]["state_tilt"] != 0.0


def test_swing_scan_no_market_state_no_tilt(monkeypatch):
    """Absent market state (default None) -> the PCS signal carries a 0.0 tilt."""
    _swing_scan_market_state_env(monkeypatch)
    out = compute.swing_scan("SPY", 5, 30, -0.20, -0.10, 0.10, 0.20, 0.10)
    pcs = [s for s in out["signals"] if s["type"] == "PCS"]
    assert pcs and pcs[0]["state_tilt"] == 0.0


# ── Paper account (moved from webgui/pages/options/portfolio.py) ────────────
def test_paper_account_view_shape(monkeypatch):
    """``paper_account_view`` assembles snapshot + positions + orders + flag from
    the lazily-imported paper modules, with the right args."""
    import sys as _sys
    import types as _types

    seen = {}
    fake_engine = _types.SimpleNamespace(
        account_snapshot=lambda: {"equity": 25100.0})
    fake_db = _types.SimpleNamespace(
        fetch_open_positions=lambda db: (seen.__setitem__("pos_db", db),
                                         [{"position_id": 1}])[1],
        fetch_orders=lambda db, limit=None, status=None: (
            seen.__setitem__("ord", (db, limit, status)), [{"order_id": 10}])[1],
        get_account=lambda: {"id": 1})
    monkeypatch.setitem(_sys.modules, "paper_engine", fake_engine)
    monkeypatch.setitem(_sys.modules, "paper_account_db", fake_db)

    out = compute.paper_account_view()
    assert out["snapshot"] == {"equity": 25100.0}
    assert out["positions"] == [{"position_id": 1}]
    assert out["orders"] == [{"order_id": 10}]
    assert out["has_account"] is True
    assert seen["pos_db"] is None
    assert seen["ord"] == (None, 100, "FILLED")  # fetch_orders(None, limit=100, status="FILLED")


def test_paper_account_view_defensive_on_failure(monkeypatch):
    """Each sub-read failure degrades gracefully: snapshot→None, lists→[], flag→False."""
    import sys as _sys
    import types as _types

    def _boom(*a, **k):
        raise RuntimeError("db cold")

    fake_engine = _types.SimpleNamespace(account_snapshot=_boom)
    fake_db = _types.SimpleNamespace(
        fetch_open_positions=_boom, fetch_orders=_boom, get_account=_boom)
    monkeypatch.setitem(_sys.modules, "paper_engine", fake_engine)
    monkeypatch.setitem(_sys.modules, "paper_account_db", fake_db)

    out = compute.paper_account_view()
    assert out == {"snapshot": None, "positions": [], "orders": [],
                   "has_account": False}


def test_run_entry_cycle_calls_engine_with_signals(monkeypatch):
    import sys as _sys
    import types as _types

    seen = {}
    fake_engine = _types.SimpleNamespace(
        run_entry_cycle=lambda client, date_iso, signals: seen.update(
            client=client, date=date_iso, signals=signals))
    fake_signal_db = _types.SimpleNamespace(
        get_open_signals_with_latest_mark=lambda: [{"id": "s1"}])
    monkeypatch.setitem(_sys.modules, "paper_engine", fake_engine)
    monkeypatch.setitem(_sys.modules, "signal_db", fake_signal_db)

    compute.run_entry_cycle()
    assert seen["client"] is compute._proxy.schwab_py_client
    assert seen["signals"] == [{"id": "s1"}]
    assert isinstance(seen["date"], str) and len(seen["date"]) == 10  # YYYY-MM-DD


def test_run_manage_cycle_calls_engine(monkeypatch):
    import sys as _sys
    import types as _types

    seen = {}
    fake_engine = _types.SimpleNamespace(
        run_manage_cycle=lambda client, date_iso: seen.update(
            client=client, date=date_iso))
    monkeypatch.setitem(_sys.modules, "paper_engine", fake_engine)

    compute.run_manage_cycle()
    assert seen["client"] is compute._proxy.schwab_py_client
    assert isinstance(seen["date"], str) and len(seen["date"]) == 10


def test_reset_and_has_account(monkeypatch):
    import sys as _sys
    import types as _types

    seen = {}
    fake_db = _types.SimpleNamespace(
        reset_account=lambda starting_balance=None: seen.__setitem__(
            "bal", starting_balance),
        get_account=lambda: None)
    monkeypatch.setitem(_sys.modules, "paper_account_db", fake_db)

    compute.reset_paper_account(50000.0)
    assert seen["bal"] == 50000.0
    assert compute.has_paper_account() is False


# ── Paper trades ledger (moved from webgui/pages/options/paper.py) ──────────
def test_paper_trades_view_shape(monkeypatch):
    import sys as _sys
    import types as _types

    trades = [{"trade_id": "T1", "symbol": "SPY"}]
    fake_pt = _types.SimpleNamespace(get_all_trades=lambda: trades)
    monkeypatch.setitem(_sys.modules, "paper_trader", fake_pt)

    assert compute.paper_trades_view() == {"trades": trades}


def test_paper_trades_view_defensive_on_failure(monkeypatch):
    import sys as _sys
    import types as _types

    def _boom():
        raise RuntimeError("db cold")

    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=_boom))
    assert compute.paper_trades_view() == {"trades": []}


def test_find_trade_matches_by_id(monkeypatch):
    import sys as _sys
    import types as _types

    trades = [{"trade_id": "T1"}, {"trade_id": "T2"}]
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: trades))
    assert compute._find_trade("T2") == {"trade_id": "T2"}
    assert compute._find_trade("nope") is None


def test_create_paper_trade_creates_then_adds(monkeypatch):
    """create_paper_trade builds the trade via paper_trader.create_paper_trade,
    persists it via add_trade (in that order), and returns the created trade."""
    import sys as _sys
    import types as _types

    order = []
    trade = {"trade_id": "T9", "symbol": "SPY"}
    signal = {"symbol": "SPY", "type": "PCS"}

    def _create(sig, qty):
        order.append(("create", sig, qty))
        return trade

    def _add(t):
        order.append(("add", t))

    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(create_paper_trade=_create, add_trade=_add))

    out = compute.create_paper_trade(signal, 2)
    assert out is trade
    # create runs before add, with the signal + qty; add gets the created trade.
    assert order == [("create", signal, 2), ("add", trade)]


def test_close_paper_persists_closed_dict(monkeypatch):
    import sys as _sys
    import types as _types

    seen = {}
    trade = {"trade_id": "T1", "symbol": "SPY"}
    fake_pt = _types.SimpleNamespace(
        get_all_trades=lambda: [trade],
        close_paper_trade=lambda t, debit, reason: (
            seen.__setitem__("close", (t, debit, reason)), {"closed": True})[1],
        update_trade=lambda tid, closed: seen.__setitem__("update", (tid, closed)))
    monkeypatch.setitem(_sys.modules, "paper_trader", fake_pt)

    compute.close_paper("T1", 0.45)
    assert seen["close"] == (trade, 0.45, "MANUAL_CLOSE")
    assert seen["update"] == ("T1", {"closed": True})


def test_close_paper_noop_when_trade_missing(monkeypatch):
    import sys as _sys
    import types as _types

    seen = {"update": 0}
    fake_pt = _types.SimpleNamespace(
        get_all_trades=lambda: [],
        close_paper_trade=lambda *a: {},
        update_trade=lambda *a: seen.__setitem__("update", seen["update"] + 1))
    monkeypatch.setitem(_sys.modules, "paper_trader", fake_pt)

    compute.close_paper("missing", 1.0)
    assert seen["update"] == 0


def test_delete_and_delete_closed(monkeypatch):
    import sys as _sys
    import types as _types

    seen = {}
    fake_pt = _types.SimpleNamespace(
        delete_trade=lambda tid: seen.__setitem__("delete", tid),
        delete_closed_trades=lambda: seen.__setitem__("delete_closed", True))
    monkeypatch.setitem(_sys.modules, "paper_trader", fake_pt)

    compute.delete_paper("T9")
    compute.delete_closed_paper()
    assert seen["delete"] == "T9"
    assert seen["delete_closed"] is True


def test_analyze_paper_extracts_verdict_action(monkeypatch):
    import sys as _sys
    import types as _types

    trade = {"trade_id": "T1", "symbol": "SPY"}
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: [trade]))
    fake_ta = _types.SimpleNamespace(
        analyze_trade=lambda client, t, iv: {"verdict": {"action": "CLOSE"}})
    monkeypatch.setitem(_sys.modules, "trade_analyzer", fake_ta)

    out = compute.analyze_paper("T1")
    assert out["trade_id"] == "T1" and out["symbol"] == "SPY"
    assert out["action"] == "CLOSE"


def test_analyze_paper_maps_live_detail(monkeypatch):
    """The live analyze output is mapped to the detail-panel field names."""
    import sys as _sys
    import types as _types

    trade = {"trade_id": "T1", "symbol": "SPY"}
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: [trade]))
    result = {
        "verdict": {"action": "HOLD"},
        "greeks": {"current": {"delta": -0.30, "theta": -0.05, "vega": 0.12}},
        "market": {"atm_iv": 22.5, "iv_rank_now": 40},
        "profit_target": {"breakeven": 448.0},
        "position": {"underlying_now": 452.1, "dte_remaining": 3,
                     "unrealized_pnl": 12.0},
    }
    monkeypatch.setitem(_sys.modules, "trade_analyzer",
                        _types.SimpleNamespace(analyze_trade=lambda c, t, i: result))

    out = compute.analyze_paper("T1")
    d = out["detail"]
    assert d["short_delta"] == -0.30 and d["net_theta"] == -0.05 and d["net_vega"] == 0.12
    assert d["short_iv"] == 22.5 and d["current_iv"] == 22.5 and d["iv_rank"] == 40
    assert d["breakeven"] == 448.0
    assert d["underlying_price"] == 452.1 and d["dte"] == 3
    assert d["unrealized_pnl"] == 12.0
    assert d["pop_pct"] == 70.0          # (1 - |−0.30|) * 100


def test_analyze_paper_defensive_on_missing_verdict(monkeypatch):
    import sys as _sys
    import types as _types

    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: []))
    monkeypatch.setitem(_sys.modules, "trade_analyzer",
                        _types.SimpleNamespace(analyze_trade=lambda c, t, i: None))

    out = compute.analyze_paper("gone")
    assert out["trade_id"] == "gone" and out["symbol"] is None
    assert out["action"] == "—" and out["detail"] is None


def test_analyze_paper_guards_runtimeerror_no_live_data(monkeypatch):
    """analyze_trade raises when live data can't be fetched -> graceful empty."""
    import sys as _sys
    import types as _types

    trade = {"trade_id": "T2", "symbol": "QQQ"}
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: [trade]))

    def _boom(c, t, i):
        raise RuntimeError("live data cannot be fetched")

    monkeypatch.setitem(_sys.modules, "trade_analyzer",
                        _types.SimpleNamespace(analyze_trade=_boom))

    out = compute.analyze_paper("T2")
    assert out["trade_id"] == "T2" and out["symbol"] == "QQQ"
    assert out["action"] == "—" and out["detail"] is None
    assert "live data cannot be fetched" in (out.get("note") or "")


def test_analyze_paper_expired_trade_skips_engine_with_note(monkeypatch):
    """An already-expired option has no live chain -> EXPIRED + note, no engine call."""
    import sys as _sys
    import types as _types

    trade = {"trade_id": "T3", "symbol": "MRVL", "expiration": "2020-01-01"}
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: [trade]))

    def _should_not_run(c, t, i):
        raise AssertionError("analyze_trade must not be called for expired trades")

    monkeypatch.setitem(_sys.modules, "trade_analyzer",
                        _types.SimpleNamespace(analyze_trade=_should_not_run))

    out = compute.analyze_paper("T3")
    assert out["action"] == "EXPIRED" and out["detail"] is None
    assert "Expired 2020-01-01" in out["note"]


def test_expire_ledger_trades_settles_past_and_close_day(monkeypatch):
    """Open ledger trades that are past-expiry (or 0-DTE at/after 15:00 CT) settle
    via expire_paper_trade; a future / pre-close trade is left OPEN."""
    import datetime as _dt
    import sys as _sys
    import types as _types
    from zoneinfo import ZoneInfo

    now_ct = _dt.datetime(2026, 6, 3, 15, 30, tzinfo=ZoneInfo("America/Chicago"))
    trades = [
        {"trade_id": "past", "status": "OPEN", "symbol": "SPY", "strategy": "PCS",
         "expiration": "2026-06-01", "short_strike": 500, "long_strike": 499,
         "entry_credit": 0.5, "quantity": 1},
        {"trade_id": "today", "status": "OPEN", "symbol": "QQQ", "strategy": "PCS",
         "expiration": "2026-06-03", "short_strike": 400, "long_strike": 399,
         "entry_credit": 0.4, "quantity": 1},
        {"trade_id": "future", "status": "OPEN", "symbol": "IWM", "strategy": "PCS",
         "expiration": "2026-07-01", "short_strike": 200, "long_strike": 199,
         "entry_credit": 0.3, "quantity": 1},
        {"trade_id": "closed", "status": "CLOSED", "symbol": "AMD", "strategy": "PCS",
         "expiration": "2026-06-01", "entry_credit": 0.3, "quantity": 1},
    ]
    settled = []
    fake_pt = _types.SimpleNamespace(
        get_all_trades=lambda: trades,
        expire_paper_trade=lambda t, sp: {**t, "status": "EXPIRED"},
        update_trade=lambda tid, closed: settled.append(tid))

    def _should_settle(exp, today, nc):   # the real gate logic, inlined
        e = _dt.date.fromisoformat(exp); t = _dt.date.fromisoformat(today)
        return e < t or (e == t and nc.hour >= 15)

    fake_pe = _types.SimpleNamespace(
        should_settle=_should_settle,
        underlying_last=lambda client, sym: 505.0)
    monkeypatch.setitem(_sys.modules, "paper_trader", fake_pt)
    monkeypatch.setitem(_sys.modules, "paper_engine", fake_pe)

    n = compute.expire_ledger_trades(now_ct=now_ct)
    assert n == 2
    assert set(settled) == {"past", "today"}   # future + closed left alone


def test_expire_ledger_trades_defers_when_no_underlying(monkeypatch):
    import datetime as _dt
    import sys as _sys
    import types as _types
    from zoneinfo import ZoneInfo

    now_ct = _dt.datetime(2026, 6, 3, 15, 30, tzinfo=ZoneInfo("America/Chicago"))
    trades = [{"trade_id": "past", "status": "OPEN", "symbol": "SPY", "strategy": "PCS",
               "expiration": "2026-06-01", "short_strike": 500, "long_strike": 499,
               "entry_credit": 0.5, "quantity": 1}]
    settled = []
    monkeypatch.setitem(_sys.modules, "paper_trader", _types.SimpleNamespace(
        get_all_trades=lambda: trades,
        expire_paper_trade=lambda t, sp: t,
        update_trade=lambda tid, c: settled.append(tid)))
    monkeypatch.setitem(_sys.modules, "paper_engine", _types.SimpleNamespace(
        should_settle=lambda exp, today, nc: True,
        underlying_last=lambda client, sym: None))   # no quote -> defer

    assert compute.expire_ledger_trades(now_ct=now_ct) == 0
    assert settled == []


def test_collect_action_items_all_four_categories(monkeypatch):
    """collect_action_items gathers captured actions, expiring-today, at-risk,
    and near-stop/target across the ledger + account books."""
    import datetime as _dt
    import sys as _sys
    import types as _types
    from zoneinfo import ZoneInfo

    now_ct = _dt.datetime(2026, 6, 3, 13, 0, tzinfo=ZoneInfo("America/Chicago"))

    # 1) captured recommending action
    monkeypatch.setattr(compute, "reprice_captured", lambda: {"signals": [
        {"symbol": "MU", "strategy": "PCS", "recommendation": "CUT",
         "recommendation_reason": "2x credit stop"},
        {"symbol": "SPY", "strategy": "CCS", "recommendation": "HOLD"},   # ignored
    ], "flags": []})

    # 2) ledger trade expiring today
    monkeypatch.setitem(_sys.modules, "paper_trader", _types.SimpleNamespace(
        get_all_trades=lambda: [
            {"trade_id": "l1", "status": "OPEN", "symbol": "IWM", "strategy": "PCS",
             "expiration": "2026-06-03"},
            {"trade_id": "l2", "status": "OPEN", "symbol": "DIA", "strategy": "PCS",
             "expiration": "2026-07-01"},   # future -> ignored
        ]))

    # account positions: one expiring today + tested + near-target
    positions = [
        {"position_id": 1, "symbol": "QQQ", "strategy": "PCS", "expiration": "2026-06-03",
         "entry_credit": 1.0, "quantity": 1, "unrealized_pnl": 45.0,
         "current_underlying": 400, "current_value": 0.55, "current_short_delta": -0.2},
    ]
    monkeypatch.setattr(compute, "_load_open_positions", lambda: positions)
    monkeypatch.setattr(compute, "_rescue_dte", lambda exp: 0)
    monkeypatch.setattr(compute, "_assess_position_risk",
                        lambda pos, mark, gex=None, regime=None: {"state": "tested", "heat": 70.0})

    out = compute.collect_action_items(now_ct=now_ct)
    assert [r["symbol"] for r in out["captured_action"]] == ["MU"]
    assert {(r["symbol"], r["book"]) for r in out["expiring_today"]} == {("IWM", "ledger"), ("QQQ", "account")}
    assert out["at_risk"][0]["symbol"] == "QQQ" and out["at_risk"][0]["rescue_state"] == "tested"
    # capture = 45 / (1.0*1*100) *100 = 45% -> near target (40..50)
    assert out["account_near"][0]["symbol"] == "QQQ" and "target" in out["account_near"][0]["note"]


def test_collect_action_items_defensive_empty(monkeypatch):
    import datetime as _dt
    import sys as _sys
    import types as _types
    from zoneinfo import ZoneInfo

    now_ct = _dt.datetime(2026, 6, 3, 13, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr(compute, "reprice_captured", lambda: {"signals": [], "flags": []})
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: []))
    monkeypatch.setattr(compute, "_load_open_positions", lambda: [])
    out = compute.collect_action_items(now_ct=now_ct)
    assert out == {"captured_action": [], "expiring_today": [], "at_risk": [], "account_near": []}


# ── EOD summary (collect_eod_summary + _eod_book_summary) ────────────────────
def test_eod_book_summary_pure_counts_closed_today():
    snap = {"session_pnl": 120.0, "equity": 25120.0, "open_count": 3, "halted": False}
    positions = [
        {"status": "CLOSED", "exit_ts": "2026-07-13T15:01:00", "realized_pnl": 200.0},
        {"status": "EXPIRED", "exit_ts": "2026-07-13T15:00:00", "realized_pnl": -20.0},
        {"status": "CLOSED", "exit_ts": "2026-07-12T15:00:00", "realized_pnl": 999.0},  # not today
        {"status": "OPEN", "exit_ts": None, "realized_pnl": None},                       # open
        "junk",                                                                          # sparse row
    ]
    b = compute._eod_book_summary(snap, positions, has_account=True,
                                  today="2026-07-13", label="Manual")
    assert b["label"] == "Manual" and b["has_account"] is True
    assert b["day_pnl"] == 120.0 and b["equity"] == 25120.0 and b["open_count"] == 3
    assert b["closed_today"] == 2 and b["wins"] == 1 and b["losses"] == 1
    assert b["realized_today"] == 180.0


def test_eod_book_summary_no_account_defensive():
    b = compute._eod_book_summary(None, None, has_account=False,
                                  today="2026-07-13", label="Driver")
    assert b["has_account"] is False and b["day_pnl"] is None
    assert b["closed_today"] == 0 and b["realized_today"] == 0.0 and b["open_count"] == 0


def test_collect_eod_summary_two_books(monkeypatch):
    import datetime as _dt
    import sys as _sys
    import types as _types
    from zoneinfo import ZoneInfo

    now_ct = _dt.datetime(2026, 7, 13, 15, 10, tzinfo=ZoneInfo("America/Chicago"))
    snap = {"session_pnl": 50.0, "equity": 25050.0, "open_count": 1, "halted": False}
    pos = [{"status": "CLOSED", "exit_ts": "2026-07-13T15:00:00", "realized_pnl": 50.0}]
    monkeypatch.setitem(_sys.modules, "paper_account_db", _types.SimpleNamespace(
        fetch_all_positions=lambda p=None: pos, get_account=lambda p=None: {"cash": 1}))
    monkeypatch.setitem(_sys.modules, "paper_engine", _types.SimpleNamespace(
        account_snapshot=lambda p=None: snap))
    out = compute.collect_eod_summary(now_ct=now_ct)
    assert out["date"] == "2026-07-13" and set(out["books"]) == {"manual", "driver"}
    assert out["books"]["manual"]["day_pnl"] == 50.0
    assert out["books"]["manual"]["closed_today"] == 1
    assert out["books"]["driver"]["has_account"] is True


def test_collect_eod_summary_defensive_on_read_failure(monkeypatch):
    import datetime as _dt
    import sys as _sys
    import types as _types
    from zoneinfo import ZoneInfo

    now_ct = _dt.datetime(2026, 7, 13, 15, 10, tzinfo=ZoneInfo("America/Chicago"))

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setitem(_sys.modules, "paper_account_db", _types.SimpleNamespace(
        fetch_all_positions=_boom, get_account=_boom))
    monkeypatch.setitem(_sys.modules, "paper_engine", _types.SimpleNamespace(
        account_snapshot=_boom))
    out = compute.collect_eod_summary(now_ct=now_ct)   # must not raise
    assert out["books"]["manual"]["has_account"] is False
    assert out["books"]["driver"]["day_pnl"] is None


def test_analyze_paper_note_none_on_success(monkeypatch):
    import sys as _sys
    import types as _types

    trade = {"trade_id": "T1", "symbol": "SPY"}
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: [trade]))
    monkeypatch.setitem(_sys.modules, "trade_analyzer",
                        _types.SimpleNamespace(
                            analyze_trade=lambda c, t, i: {"verdict": {"action": "HOLD"}}))

    out = compute.analyze_paper("T1")
    assert out["action"] == "HOLD" and out["note"] is None


def test_analyze_paper_includes_rationale_and_metrics(monkeypatch):
    """The enriched result carries the verdict rationale + a metrics block so the
    Paper Trades Analyze popup can be descriptive (not a one-word toast)."""
    import sys as _sys
    import types as _types

    trade = {"trade_id": "T1", "symbol": "AMD", "expiration": "2090-01-01",
             "short_strike": 500, "strategy": "PCS"}
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: [trade]))
    result = {
        "verdict": {"action": "TAKE PROFIT", "rationale": "80% captured — take the win"},
        "position": {"unrealized_pnl": 1680.0, "unrealized_pnl_pct": 65.0,
                     "underlying_now": 522.36, "dte_remaining": 6},
        "profit_target": {"target_pct": 65.0, "breakeven": 495.5},
        "greeks": {"current": {}}, "market": {},
    }
    monkeypatch.setitem(_sys.modules, "trade_analyzer",
                        _types.SimpleNamespace(analyze_trade=lambda c, t, i: result))

    out = compute.analyze_paper("T1")
    assert out["action"] == "TAKE PROFIT"
    assert out["rationale"] == "80% captured — take the win"
    m = out["metrics"]
    assert m["unrealized_pnl"] == 1680.0 and m["unrealized_pnl_pct"] == 65.0
    assert m["underlying_now"] == 522.36 and m["dte_remaining"] == 6
    assert m["target_pct"] == 65.0 and m["breakeven"] == 495.5


# ── Paper-trade ledger live P&L reprice ─────────────────────────────────────
def test_paper_trades_view_reprices_open_total_pnl(monkeypatch):
    """reprice=True (market open) attaches a live unrealized_pnl = per-spread × qty
    to OPEN trades only; closed trades are untouched."""
    import sys as _sys
    import types as _types

    from services.options_svc import scheduler

    trades = [
        {"trade_id": "o", "symbol": "SPY", "status": "OPEN", "quantity": 10},
        {"trade_id": "c", "symbol": "QQQ", "status": "CLOSED", "quantity": 5},
    ]
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: trades))
    monkeypatch.setitem(_sys.modules, "signal_repricer", _types.SimpleNamespace(
        clear_chain_cache=lambda: None,
        reprice_swing=lambda t, client: {"unrealized_pnl": 3.0}))  # per-spread $
    monkeypatch.setattr(scheduler, "_is_trading_day", lambda now: True)
    monkeypatch.setattr(scheduler, "_is_market_hours", lambda now: True)

    by_id = {t["trade_id"]: t for t in compute.paper_trades_view(reprice=True)["trades"]}
    assert by_id["o"]["unrealized_pnl"] == 30.0          # 3.0 × qty 10
    assert "unrealized_pnl" not in by_id["c"]            # closed untouched


def test_paper_trades_view_routes_debit_to_reprice_legs(monkeypatch):
    """A DEBIT/legs trade reprices via reprice_legs; a credit trade via reprice_swing."""
    import sys as _sys
    import types as _types

    from services.options_svc import scheduler

    trades = [
        {"trade_id": "d", "symbol": "SPY", "status": "OPEN", "quantity": 2,
         "direction": "DEBIT", "legs": [{"kind": "call", "side": "long", "strike": 100}]},
        {"trade_id": "c", "symbol": "QQQ", "status": "OPEN", "quantity": 1},   # credit
    ]
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: trades))
    monkeypatch.setitem(_sys.modules, "signal_repricer", _types.SimpleNamespace(
        clear_chain_cache=lambda: None,
        reprice_legs=lambda t, client: {"unrealized_pnl": 50.0},     # per contract
        reprice_swing=lambda t, client: {"unrealized_pnl": 7.0}))
    monkeypatch.setattr(scheduler, "_is_trading_day", lambda now: True)
    monkeypatch.setattr(scheduler, "_is_market_hours", lambda now: True)

    by_id = {t["trade_id"]: t for t in compute.paper_trades_view(reprice=True)["trades"]}
    assert by_id["d"]["unrealized_pnl"] == 100.0     # reprice_legs 50 × qty 2
    assert by_id["c"]["unrealized_pnl"] == 7.0       # reprice_swing (credit path)


def test_paper_trades_view_skips_reprice_off_hours(monkeypatch):
    """Off-hours, no reprice is attempted even with reprice=True (no proxy churn)."""
    import sys as _sys
    import types as _types

    from services.options_svc import scheduler

    trades = [{"trade_id": "o", "symbol": "SPY", "status": "OPEN", "quantity": 1}]
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: trades))

    def _boom(*a, **k):
        raise AssertionError("reprice_swing must not run off-hours")

    monkeypatch.setitem(_sys.modules, "signal_repricer", _types.SimpleNamespace(
        clear_chain_cache=lambda: None, reprice_swing=_boom))
    monkeypatch.setattr(scheduler, "_is_trading_day", lambda now: True)
    monkeypatch.setattr(scheduler, "_is_market_hours", lambda now: False)

    out = compute.paper_trades_view(reprice=True)
    assert "unrealized_pnl" not in out["trades"][0]


def test_paper_trades_view_default_does_not_reprice(monkeypatch):
    """Default reprice=False never imports/calls the repricer (cheap publish)."""
    import sys as _sys
    import types as _types

    trades = [{"trade_id": "o", "symbol": "SPY", "status": "OPEN", "quantity": 1}]
    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: trades))
    out = compute.paper_trades_view()
    assert "unrealized_pnl" not in out["trades"][0]


# ── Captured signals (moved from webgui/pages/options/captured.py) ──────────
def test_captured_view_shape(monkeypatch):
    import sys as _sys
    import types as _types

    sigs = [{"signal_id": "X1", "symbol": "SPY"}]
    monkeypatch.setitem(_sys.modules, "signal_db",
                        _types.SimpleNamespace(get_open_signals_with_latest_mark=lambda: sigs))
    assert compute.captured_view() == {"signals": sigs}


def test_captured_view_defensive_on_failure(monkeypatch):
    import sys as _sys
    import types as _types

    def _boom():
        raise RuntimeError("db cold")

    monkeypatch.setitem(_sys.modules, "signal_db",
                        _types.SimpleNamespace(get_open_signals_with_latest_mark=_boom))
    assert compute.captured_view() == {"signals": []}


def test_reprice_captured_merges_marks_and_flags(monkeypatch):
    """reprice_captured reprices each open signal, merges the mark's display
    fields into the row, and flags the four stop/target codes."""
    import sys as _sys
    import types as _types

    sigs = [
        {"signal_id": "X1", "symbol": "SPY", "recommendation": "HOLD"},
        {"signal_id": "X2", "symbol": "QQQ", "recommendation": "HOLD"},
    ]
    monkeypatch.setitem(_sys.modules, "signal_db",
                        _types.SimpleNamespace(get_open_signals_with_latest_mark=lambda: sigs))
    monkeypatch.setitem(_sys.modules, "signal_repricer",
                        _types.SimpleNamespace(reprice_swing=lambda r, c: {"rep": r["signal_id"]}))

    marks = {
        "X1": {"unrealized_pnl": 12.0, "current_score": 68, "score_drift": -4,
               "current_value": 0.20,
               "recommendation": "HOLD", "recommendation_code": "HOLD"},
        "X2": {"unrealized_pnl": -30.0, "current_score": 40, "score_drift": -20,
               "current_value": 6.00,
               "recommendation": "CLOSE", "recommendation_code": "money_stop"},
    }
    monkeypatch.setitem(_sys.modules, "signal_recommender",
                        _types.SimpleNamespace(build_mark=lambda r, rep, now: marks[r["signal_id"]]))

    out = compute.reprice_captured()
    by_id = {s["signal_id"]: s for s in out["signals"]}
    # Mark display fields merged into the rows.
    assert by_id["X1"]["unrealized_pnl"] == 12.0
    assert by_id["X1"]["current_score"] == 68
    assert by_id["X1"]["current_value"] == 0.20    # current option price surfaced
    assert by_id["X2"]["score_drift"] == -20
    assert by_id["X2"]["current_value"] == 6.00
    assert by_id["X2"]["recommendation"] == "CLOSE"
    # Only the stop/target code is flagged (case-insensitive).
    assert out["flags"] == [{"symbol": "QQQ", "code": "MONEY_STOP"}]


def test_reprice_captured_clears_chain_cache_first(monkeypatch):
    """reprice_captured must clear the repricer's per-(symbol,expiration) chain
    cache before repricing — otherwise captured-signal marks (and the 3x/day
    action-alert reprice that reuses this path) are priced off whichever chains
    the last clearing caller fetched (up to ~5 min stale during RTH)."""
    import sys as _sys
    import types as _types

    order = []
    sigs = [{"signal_id": "X1", "symbol": "SPY", "recommendation": "HOLD"}]
    monkeypatch.setitem(_sys.modules, "signal_db",
                        _types.SimpleNamespace(get_open_signals_with_latest_mark=lambda: sigs))
    monkeypatch.setitem(_sys.modules, "signal_repricer", _types.SimpleNamespace(
        clear_chain_cache=lambda: order.append("clear"),
        reprice_swing=lambda r, c: order.append("reprice") or {"rep": 1}))
    monkeypatch.setitem(_sys.modules, "signal_recommender", _types.SimpleNamespace(
        build_mark=lambda r, rep, now: {"unrealized_pnl": 0.0, "recommendation": "HOLD",
                                        "recommendation_code": "HOLD"}))
    compute.reprice_captured()
    assert order and order[0] == "clear"      # cleared BEFORE the first reprice
    assert "reprice" in order


def test_reprice_captured_skips_failed_signal(monkeypatch):
    """A per-signal reprice failure is skipped (continue), not fatal."""
    import sys as _sys
    import types as _types

    sigs = [{"signal_id": "X1", "symbol": "SPY"}, {"signal_id": "X2", "symbol": "QQQ"}]
    monkeypatch.setitem(_sys.modules, "signal_db",
                        _types.SimpleNamespace(get_open_signals_with_latest_mark=lambda: sigs))

    def _reprice(r, c):
        if r["signal_id"] == "X1":
            raise RuntimeError("no chain")
        return {"ok": True}

    monkeypatch.setitem(_sys.modules, "signal_repricer",
                        _types.SimpleNamespace(reprice_swing=_reprice))
    monkeypatch.setitem(_sys.modules, "signal_recommender",
                        _types.SimpleNamespace(build_mark=lambda r, rep, now: {
                            "unrealized_pnl": 1.0, "recommendation_code": "TARGET_HIT"}))

    out = compute.reprice_captured()
    # Both signals returned, but only X2 was repriced + flagged.
    assert {s["signal_id"] for s in out["signals"]} == {"X1", "X2"}
    assert out["flags"] == [{"symbol": "QQQ", "code": "TARGET_HIT"}]


# ── captured rescue detection (C2) ──────────────────────────────────────────
def _patch_reprice_seams(monkeypatch, sigs, marks):
    """Install signal_db / signal_repricer / signal_recommender stubs so
    reprice_captured runs offline. ``marks`` is keyed by signal_id."""
    import sys as _sys
    import types as _types
    monkeypatch.setitem(_sys.modules, "signal_db",
                        _types.SimpleNamespace(
                            get_open_signals_with_latest_mark=lambda: sigs))
    monkeypatch.setitem(_sys.modules, "signal_repricer",
                        _types.SimpleNamespace(
                            reprice_swing=lambda r, c: {
                                "current_underlying": 500.5,
                                "current_value": 2.5,
                                "current_short_delta": 0.40,
                                "error": None,
                            }))
    monkeypatch.setitem(_sys.modules, "signal_recommender",
                        _types.SimpleNamespace(
                            build_mark=lambda r, rep, now: marks[r["signal_id"]]))


def test_reprice_captured_tags_rescue_state_and_heat(monkeypatch):
    """Every repriced signal gets rescue_state + heat attached."""
    sigs = [{"signal_id": "X1", "symbol": "SPY", "strategy": "PCS",
             "short_strike": 500.0, "long_strike": 495.0,
             "expiration": "2099-07-31", "entry_credit": 1.0, "recommendation": "HOLD"}]
    marks = {"X1": {"unrealized_pnl": 5.0, "recommendation": "HOLD",
                    "recommendation_code": "HOLD"}}
    _patch_reprice_seams(monkeypatch, sigs, marks)
    out = compute.reprice_captured()
    row = out["signals"][0]
    assert "rescue_state" in row
    assert "heat" in row
    assert isinstance(row["heat"], float)
    # The live short-leg delta is surfaced on the row so the Rescue board's
    # "Δ short" column has data for captured signals.
    assert row["current_short_delta"] == 0.40


def test_reprice_captured_escalates_cut_to_tested(monkeypatch):
    """A CUT recommendation escalates rescue_state to at least 'tested' and
    floors heat at 60, even when the raw assessment would be milder."""
    sigs = [{"signal_id": "X1", "symbol": "SPY", "strategy": "PCS",
             "short_strike": 500.0, "long_strike": 495.0,
             "expiration": "2099-07-31", "entry_credit": 1.0,
             "recommendation": "HOLD"}]
    # Mark says CUT via the recommendation field (loss stop).
    marks = {"X1": {"unrealized_pnl": -50.0, "recommendation": "CUT",
                    "recommendation_code": "TIME_STOP"}}
    _patch_reprice_seams(monkeypatch, sigs, marks)
    out = compute.reprice_captured()
    row = out["signals"][0]
    assert row["rescue_state"] == "tested"
    assert row["heat"] >= 60.0


def test_reprice_captured_escalates_on_loss_stop_code(monkeypatch):
    """A loss-stop recommendation_code (no explicit CUT label) still escalates."""
    sigs = [{"signal_id": "X1", "symbol": "QQQ", "strategy": "IC",
             "short_strike": 400.0, "long_strike": 395.0,
             "expiration": "2099-07-31", "entry_credit": 0.8,
             "recommendation": "HOLD"}]
    marks = {"X1": {"unrealized_pnl": -90.0, "recommendation": "HOLD",
                    "recommendation_code": "MONEY_STOP"}}
    _patch_reprice_seams(monkeypatch, sigs, marks)
    out = compute.reprice_captured()
    row = out["signals"][0]
    assert row["rescue_state"] == "tested"
    assert row["heat"] >= 60.0


def test_reprice_captured_hold_not_escalated(monkeypatch):
    """A benign HOLD signal is NOT escalated to tested by the CUT logic."""
    sigs = [{"signal_id": "X1", "symbol": "SPY", "strategy": "PCS",
             "short_strike": 480.0, "long_strike": 475.0,
             "expiration": "2099-07-31", "entry_credit": 1.0,
             "recommendation": "HOLD"}]
    # Far OTM, tiny delta, profit -> assessment stays ok/watch.
    marks = {"X1": {"unrealized_pnl": 20.0, "recommendation": "HOLD",
                    "recommendation_code": "HOLD"}}

    def _reprice(r, c):
        return {"current_underlying": 510.0, "current_value": 0.3,
                "current_short_delta": 0.05, "error": None}

    import sys as _sys
    import types as _types
    monkeypatch.setitem(_sys.modules, "signal_db",
                        _types.SimpleNamespace(
                            get_open_signals_with_latest_mark=lambda: sigs))
    monkeypatch.setitem(_sys.modules, "signal_repricer",
                        _types.SimpleNamespace(reprice_swing=_reprice))
    monkeypatch.setitem(_sys.modules, "signal_recommender",
                        _types.SimpleNamespace(build_mark=lambda r, rep, now: marks[r["signal_id"]]))
    out = compute.reprice_captured()
    row = out["signals"][0]
    assert row["rescue_state"] != "tested"


def test_reprice_captured_detection_defensive(monkeypatch):
    """If the rescue assessment raises, the row is still returned (untagged) and
    the reprice loop is not broken."""
    sigs = [{"signal_id": "X1", "symbol": "SPY", "strategy": "PCS",
             "short_strike": 500.0, "expiration": "2099-07-31",
             "entry_credit": 1.0, "recommendation": "CUT"}]
    marks = {"X1": {"unrealized_pnl": -50.0, "recommendation": "CUT",
                    "recommendation_code": "TIME_STOP"}}
    _patch_reprice_seams(monkeypatch, sigs, marks)

    def _boom(*a, **k):
        raise RuntimeError("assess exploded")

    monkeypatch.setattr(compute, "_assess_position_risk", _boom)
    out = compute.reprice_captured()
    # Row still returned; not crashed.
    assert out["signals"][0]["signal_id"] == "X1"


def test_close_captured_calls_close_signal_manually(monkeypatch):
    import sys as _sys
    import types as _types

    seen = {}
    monkeypatch.setitem(_sys.modules, "signal_db", _types.SimpleNamespace(
        close_signal_manually=lambda sid, ev, rsn: seen.__setitem__("args", (sid, ev, rsn))))

    compute.close_captured("X1", "0.45", "")
    # exit_val coerced to float; blank reason -> MANUAL_CLOSE default.
    assert seen["args"] == ("X1", 0.45, "MANUAL_CLOSE")


# ── Gamma (moved from webgui/pages/options/gamma.py) ─────────────────────────
class _FakeChainResp:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _FakeEngine:
    """Stand-in for GammaEngine: returns canned view dicts (key under "gex")."""
    _last_dte = 0

    def calc_all_from_chain(self, chain):
        gex = {"spot": 5400.0, "gex": {5400.0: {"call": 1, "put": -1, "net": 0.5}},
               "strike_count": 1}
        charm = {"spot": 5400.0, "gex": {5400.0: {"net": 0.2}}, "strike_count": 1}
        dex = {"spot": 5400.0, "gex": {5400.0: {"net": 0.3}}, "strike_count": 1,
               "net_delta_0dte": 10.0, "projected_net_delta_close": 5.0,
               "hedge_pressure": -5.0}
        vanna = {"spot": 5400.0, "gex": {5400.0: {"net": 0.1}}, "strike_count": 1}
        return gex, charm, dex, vanna

    def snapshot_summary(self, data, view):
        return {"spot": data.get("spot"), "flip": 5399.5, "net_total": 1.0}

    def compute_term_grid(self, chain):
        return {"expirations": ["2026-06-18"], "cells": {}}


def _patch_gamma(monkeypatch, *, chain=None, walls=None, history=None):
    import sys as _sys
    import types as _types

    def _fake_directional_walls(gex_data, spot):
        grid = (gex_data or {}).get("gex") or {}
        above = [(s, v.get("call", 0.0)) for s, v in grid.items() if s > spot]
        below = [(s, v.get("put", 0.0)) for s, v in grid.items() if s < spot]
        out = {"call_wall": None, "put_wall": None}
        if above:
            out["call_wall"] = max(above, key=lambda sv: sv[1])[0]
        if below:
            out["put_wall"] = min(below, key=lambda sv: sv[1])[0]
        return out

    fake_gt = _types.SimpleNamespace(
        GammaEngine=_FakeEngine,
        get_directional_walls=_fake_directional_walls)

    class _CountingConn:
        n_open = 0
        n_close = 0

        def close(self):
            _CountingConn.n_close += 1

    def _connect(read_only=False):
        _CountingConn.n_open += 1
        return _CountingConn()

    def _load_date_with_grid(conn, symbol, view, date=None, since_ts=None):
        rows = history or []
        if since_ts is not None:  # emulate the DB's strict ts > since_ts filter
            rows = [r for r in rows if r[0] > since_ts]
        return list(rows)

    fake_gh = _types.SimpleNamespace(
        connect=_connect,
        _ConnCls=_CountingConn,
        load_date_with_grid=_load_date_with_grid,
        load_today_with_grid=lambda conn, symbol, view: (history or []))
    monkeypatch.setitem(_sys.modules, "gamma_tool", fake_gt)
    monkeypatch.setitem(_sys.modules, "gex_history_db", fake_gh)
    # The session-history memo + tick-chain stash are module-level state — reset
    # per test.
    compute.reset_gamma_history_memo()
    compute.reset_tick_chain()
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain",
                        lambda *a, **k: _FakeChainResp(
                            chain if chain is not None else {"underlyingPrice": 5400.0}))
    # Persistence is time-dependent — pin the active session date so tests are
    # deterministic (the display shows this session's data).
    import datetime as _dtm
    from services.options_svc import scheduler as _sched
    monkeypatch.setattr(_sched, "active_session_date", lambda now=None: _dtm.date(2026, 6, 18))


def test_light_gex_context_is_gex_only(monkeypatch):
    """Rescue advisories need only flip/walls/spot — _light_gex_context computes
    a GEX-only context (single chain fetch + calc_all) WITHOUT the full
    gamma_snapshot's projection band / term grid / flow series / history decode.
    It's shaped like a snapshot so _gex_from_snapshot consumes it unchanged."""
    _patch_gamma(monkeypatch, chain={"underlyingPrice": 5400.0},
                 history=[(1, 5400.0, 3, 4, 5, 6, {5400.0: {"net": 1}})])
    # If the heavy projection machinery runs, fail loudly.
    monkeypatch.setattr(compute, "project_gex_grid",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("light context must not build projection")))
    ctx = compute._light_gex_context("$SPX")
    assert ctx["spot"] == 5400.0
    assert "GEX" in ctx["views"] and "term" not in ctx
    gex = compute._gex_from_snapshot(ctx)
    assert gex is not None and gex["flip"] == 5399.5


def test_rescue_advisories_use_light_gex_context():
    """The four rescue-advisory builders must use the light GEX context, not the
    full gamma_snapshot (which discards ~95% of its work here)."""
    import inspect
    for fn in (compute._advisory_from_position, compute._advisory_from_single,
               compute._advisory_from_debit, compute._advisory_from_range):
        src = inspect.getsource(fn)
        assert "_light_gex_context(symbol)" in src
        assert "gamma_snapshot(symbol)" not in src


def test_gamma_snapshot_builds_views_and_term(monkeypatch):
    _patch_gamma(monkeypatch, history=[(1, 2, 3, 4, 5, 6, {5400.0: {"net": 1}})])

    snap = compute.gamma_snapshot("$SPX")
    assert snap["symbol"] == "$SPX"
    assert snap["spot"] == 5400.0
    assert snap["dte"] == 0
    # All four views present, each keyed with its strike dict under data["gex"].
    assert set(snap["views"]) == {"GEX", "Charm", "DEX", "Vanna"}
    gexv = snap["views"]["GEX"]
    assert gexv["data"]["gex"] == {5400.0: {"call": 1, "put": -1, "net": 0.5}}
    # One-each walls now come from get_directional_walls; the canned single
    # at-spot strike has no strike above/below spot -> no directional walls.
    assert gexv["walls"] == []
    assert gexv["flip"] == 5399.5
    assert gexv["history"] and gexv["history"][0][6] == {5400.0: {"net": 1}}
    # DEX carries the hedge tiles.
    assert snap["views"]["DEX"]["hedge"] == {
        "net_delta_0dte": 10.0, "projected_net_delta_close": 5.0,
        "hedge_pressure": -5.0}
    assert snap["term"] == {"expirations": ["2026-06-18"], "cells": {}}


class _WideEngine(_FakeEngine):
    """Like _FakeEngine but returns a WIDE per-strike grid (spot ±60 strikes) so
    cropping to ±20 is observable, and reports far-out extreme strikes for
    flip/walls so we can prove flip/walls come from the FULL grid pre-crop."""

    def calc_all_from_chain(self, chain):
        spot = 5400.0
        # 1-wide strikes from 5340..5460 (121 strikes; ±60 around spot).
        grid = {}
        for i in range(-60, 61):
            k = float(spot + i)
            # Make the biggest call GEX far above the ±20 window (5455) and the
            # most-negative put far below (5345) so directional walls land OUTSIDE
            # the crop window — proving they're computed on the full grid.
            call = 100.0 if k == 5455.0 else 1.0
            put = -100.0 if k == 5345.0 else -1.0
            grid[k] = {"call": call, "put": put, "net": call + put}
        gex = {"spot": spot, "gex": grid, "strike_count": len(grid)}
        charm = {"spot": spot, "gex": dict(grid), "strike_count": len(grid)}
        dex = {"spot": spot, "gex": dict(grid), "strike_count": len(grid),
               "net_delta_0dte": 10.0, "projected_net_delta_close": 5.0,
               "hedge_pressure": -5.0}
        vanna = {"spot": spot, "gex": dict(grid), "strike_count": len(grid)}
        return gex, charm, dex, vanna


def test_gamma_snapshot_crops_grids_to_window(monkeypatch):
    """Each view's current grid + history grids are cropped to ±GAMMA_N_SIDE
    strikes around spot, while flip/walls (full-grid fields) are unchanged."""
    # A wide history-row grid at the same spot (5340..5460); should crop to window.
    hist_grid = {float(5400 + i): {"net": 1.0} for i in range(-60, 61)}
    _patch_gamma(monkeypatch, history=[(1, 5400.0, 3, 4, 5, 6, hist_grid)])
    import sys as _sys
    _sys.modules["gamma_tool"].GammaEngine = _WideEngine

    snap = compute.gamma_snapshot("$SPX")
    gexv = snap["views"]["GEX"]
    # Cropped current grid: exactly the ±20 window = 20 below + at-spot + 20 above.
    kept = sorted(gexv["data"]["gex"].keys())
    assert kept == [float(5400 + i) for i in range(-20, 21)]
    # History grid cropped identically.
    hist_kept = sorted(gexv["history"][0][6].keys())
    assert hist_kept == [float(5400 + i) for i in range(-20, 21)]
    # Walls come from the FULL grid (call wall 5455, put wall 5345) — OUTSIDE the
    # ±20 crop window, proving they were computed pre-crop.
    assert gexv["walls"] == [5345.0, 5455.0]
    assert gexv["flip"] == 5399.5


def test_gamma_snapshot_crop_widens_for_history_spot_drift(monkeypatch):
    """The crop window is the union across every history-row spot, so a strike in
    an earlier row's near-spot window is kept even if far from the current spot."""
    # Two history rows at different spots; a strike near the earlier spot (5300)
    # must survive even though it's >20 strikes from the current spot (5400).
    early_grid = {5300.0: {"net": 5.0}, 5400.0: {"net": 1.0}}
    late_grid = {5400.0: {"net": 2.0}}
    _patch_gamma(monkeypatch, history=[(1, 5300.0, 3, 4, 5, 6, early_grid),
                                       (2, 5400.0, 3, 4, 5, 6, late_grid)])
    import sys as _sys
    _sys.modules["gamma_tool"].GammaEngine = _WideEngine

    snap = compute.gamma_snapshot("$SPX")
    gexv = snap["views"]["GEX"]
    # 5300 is within ±20 of the earlier spot 5300 → kept in the union window.
    assert 5300.0 in gexv["history"][0][6]


def test_tick_chain_stash_consume_once():
    """The per-tick chain stash hands the poll's chain to the SAME tick's
    gamma refresh exactly once — a second take (e.g. a page-timer refresh
    moments later) fetches fresh."""
    compute.reset_tick_chain()
    compute._stash_tick_chain("$SPX", {"c": 1})
    assert compute._take_tick_chain("$SPX") == {"c": 1}
    assert compute._take_tick_chain("$SPX") is None      # consume-once
    compute._stash_tick_chain("$SPX", {"c": 2})
    assert compute._take_tick_chain("SPY") is None       # symbol mismatch
    compute._stash_tick_chain("$SPX", {"c": 3})
    compute._TICK_CHAIN["ts"] -= compute.TICK_CHAIN_TTL_SEC + 1
    assert compute._take_tick_chain("$SPX") is None      # expired


def test_gamma_snapshot_uses_stashed_tick_chain(monkeypatch):
    """gamma_snapshot must consume the tick's stashed chain instead of paying a
    second chain fetch for the symbol the collector fetched seconds earlier."""
    chain = {"underlyingPrice": 5400.0}
    _patch_gamma(monkeypatch, chain=chain,
                 history=[(1, 5400.0, 3, 4, 5, 6, {5400.0: {"net": 1}})])
    fetches = {"n": 0}
    orig_fetch = compute._gamma_fetch_chain

    def counting_fetch(symbol):
        fetches["n"] += 1
        return orig_fetch(symbol)

    monkeypatch.setattr(compute, "_gamma_fetch_chain", counting_fetch)
    compute.reset_tick_chain()
    compute._stash_tick_chain("$SPX", chain)
    snap = compute.gamma_snapshot("$SPX")
    assert snap is not None and snap["spot"] == 5400.0
    assert fetches["n"] == 0        # reused the stash — no refetch
    compute.gamma_snapshot("$SPX")
    assert fetches["n"] == 1        # stash consumed — next snapshot fetches


def test_history_rows_incremental_appends_only_new_rows():
    """The per-(symbol,view,date) memo full-loads once, then loads ONLY rows with
    ts > last-seen and appends — the whole-session re-decode every minute was the
    service's largest recurring CPU burn."""
    import datetime as _dtm
    compute.reset_gamma_history_memo()
    calls = []
    rows1 = [(100, 1.0, None, None, None, 6, {5400.0: {"net": 1}})]
    rows2 = [(160, 2.0, None, None, None, 6, {5401.0: {"net": 2}})]

    class FakeGh:
        def load_date_with_grid(self, conn, symbol, view, date=None, since_ts=None):
            calls.append(since_ts)
            return list(rows1) if since_ts is None else list(rows2)

    d = _dtm.date(2026, 6, 18)
    out1 = compute._history_rows_incremental(FakeGh(), "conn", "$SPX", "gex", d)
    assert out1 == rows1 and calls == [None]           # cold: full load
    out2 = compute._history_rows_incremental(FakeGh(), "conn", "$SPX", "gex", d)
    assert out2 == rows1 + rows2                        # appended, not reloaded
    assert calls == [None, 100]                         # incremental: since last ts


def test_history_rows_incremental_resets_on_new_session_date():
    import datetime as _dtm
    compute.reset_gamma_history_memo()
    calls = []

    class FakeGh:
        def load_date_with_grid(self, conn, symbol, view, date=None, since_ts=None):
            calls.append((date, since_ts))
            return [(100, 1.0, None, None, None, 6, {})]

    compute._history_rows_incremental(FakeGh(), "c", "$SPX", "gex", _dtm.date(2026, 6, 18))
    compute._history_rows_incremental(FakeGh(), "c", "$SPX", "gex", _dtm.date(2026, 6, 19))
    # A new session date invalidates the memo -> full load again (since_ts None).
    assert calls == [(_dtm.date(2026, 6, 18), None), (_dtm.date(2026, 6, 19), None)]


def test_gamma_snapshot_second_call_loads_history_incrementally(monkeypatch):
    """End-to-end through gamma_snapshot: the second snapshot in the same session
    must ask the DB only for rows NEWER than the memoized ones."""
    _patch_gamma(monkeypatch, history=[(1, 5400.0, 3, 4, 5, 6, {5400.0: {"net": 1}})])
    import sys as _sys
    seen = []
    fake_gh = _sys.modules["gex_history_db"]
    orig = fake_gh.load_date_with_grid

    def counting(conn, symbol, view, date=None, since_ts=None):
        seen.append(since_ts)
        return orig(conn, symbol, view, date=date, since_ts=since_ts)

    fake_gh.load_date_with_grid = counting
    compute.gamma_snapshot("$SPX")
    assert set(seen) == {None}                     # cold: full loads (4 views)
    seen.clear()
    snap = compute.gamma_snapshot("$SPX")
    assert seen and all(s == 1 for s in seen)      # warm: only ts > 1 requested
    assert snap["views"]["GEX"]["history"]         # memoized rows still served


def test_gamma_snapshot_reuses_one_history_connection(monkeypatch):
    """The four view history loads share ONE read-only connection (was 4 opens),
    closed after the snapshot is built."""
    _patch_gamma(monkeypatch, history=[(1, 5400.0, 3, 4, 5, 6, {5400.0: {"net": 1}})])
    import sys as _sys
    cc = _sys.modules["gex_history_db"]._ConnCls
    cc.n_open = cc.n_close = 0
    compute.gamma_snapshot("$SPX")
    assert cc.n_open == 1     # one connection for all four views
    assert cc.n_close == 1     # and it's closed


def test_gamma_snapshot_shows_data_premarket(monkeypatch):
    # Pre-market (and post-market) the display shows the most-recent session: the
    # by-strike charts compute from the chain and the heatmap loads the active
    # (prior) session — no overnight blanking. So the snapshot is NOT None.
    _patch_gamma(monkeypatch, history=[(1, 5400.0, 3, 4, 5, 6, {5400.0: {"net": 1}})])
    snap = compute.gamma_snapshot("$SPX")
    assert snap is not None
    assert snap["views"]["GEX"]["history"]   # prior session's heatmap rows shown


def test_gamma_snapshot_history_uses_active_session_date(monkeypatch):
    # The heatmap history loads the ACTIVE SESSION DATE (e.g. Friday over a weekend),
    # not "today", so it persists off-hours.
    seen = {}
    _patch_gamma(monkeypatch, history=[(1, 2, 3, 4, 5, 6, {5400.0: {"net": 1}})])
    import datetime as _dtm
    import sys as _sys
    from services.options_svc import scheduler as _sched
    monkeypatch.setattr(_sched, "active_session_date", lambda now=None: _dtm.date(2026, 6, 26))

    def _capture(conn, symbol, view, date=None, since_ts=None):
        seen["date"] = date
        return [(1, 2, 3, 4, 5, 6, {5400.0: {"net": 1}})]
    _sys.modules["gex_history_db"].load_date_with_grid = _capture
    compute.gamma_snapshot("$SPX")
    assert seen["date"] == _dtm.date(2026, 6, 26)


def _chain_with_exps(*dates):
    """Minimal chain whose call/put maps carry the given expiration dates."""
    cm = {f"{d}:7": {"100.0": [{}]} for d in dates}
    return {"underlyingPrice": 100.0, "callExpDateMap": cm, "putExpDateMap": cm}


def test_count_expirations_counts_distinct_dates():
    chain = _chain_with_exps("2026-06-26", "2026-07-02", "2026-06-26")
    assert compute._count_expirations(chain) == 2
    assert compute._count_expirations({}) == 0
    assert compute._count_expirations(None) == 0


def test_term_chain_reuses_base_when_it_already_has_enough(monkeypatch):
    base = _chain_with_exps("2026-06-23", "2026-06-24", "2026-06-25",
                            "2026-06-26", "2026-06-29")  # index: 5 daily expiries
    calls = []
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain",
                        lambda *a, **k: calls.append(1) or _FakeChainResp({}))
    out = compute._term_chain("$SPX", base, n_exp=5)
    assert out is base          # reused — no widening
    assert calls == []          # and no extra fetch


def test_term_chain_widens_for_weekly_or_monthly_names(monkeypatch):
    base = _chain_with_exps("2026-06-26")     # only 1 weekly in the 7-day window
    wide = _chain_with_exps("2026-06-26", "2026-07-31", "2026-08-31",
                            "2026-09-30", "2026-10-31")   # 5 monthlies once widened
    windows = []

    def _fake(symbol, contract_type=None, from_date=None, to_date=None, **k):
        windows.append((to_date - from_date).days)
        return _FakeChainResp(wide)

    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain", _fake)
    out = compute._term_chain("PLTR", base, n_exp=5)
    assert compute._count_expirations(out) >= 5   # got the 5 expirations
    assert windows                                # widened beyond the base window


def test_gamma_snapshot_none_when_chain_fetch_fails(monkeypatch):
    _patch_gamma(monkeypatch)

    class _Bad:
        status_code = 500

        def json(self):
            return {}

    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain",
                        lambda *a, **k: _Bad())
    assert compute.gamma_snapshot("$SPX") is None


def test_gamma_snapshot_gex_carries_projection(monkeypatch):
    """The GEX view carries a forward `projection` block (times/grid/cone/spot),
    cropped to the display window; other views do not."""
    import datetime as _dtm
    from zoneinfo import ZoneInfo
    from services.options_svc import scheduler as _sched

    exp = "2026-06-18:0"
    def _leg(k, oi):
        return [{"strike": k, "gamma": 0.05, "openInterest": oi,
                 "volatility": 20.0, "delta": 0.5, "daysToExpiration": 0}]
    wide = {float(5400 + i): (5000 if i == 0 else 500) for i in range(-60, 61)}
    chain = {"underlyingPrice": 5400.0,
             "callExpDateMap": {exp: {f"{k:.1f}": _leg(k, oi) for k, oi in wide.items()}},
             "putExpDateMap": {exp: {f"{k:.1f}": _leg(k, max(oi - 200, 100)) for k, oi in wide.items()}}}

    class _ProjEngine(_WideEngine):
        @staticmethod
        def _find_nearest_exp_key(exp_map, today):
            return (next(iter(exp_map)), 0) if exp_map else (None, None)

    _patch_gamma(monkeypatch, chain=chain,
                 history=[(1, 5400.0, 3, 4, 5, 6, {5400.0: {"net": 1}})])
    import sys as _sys
    _sys.modules["gamma_tool"].GammaEngine = _ProjEngine
    monkeypatch.setattr(_sched, "_market_now",
                        lambda: _dtm.datetime(2026, 6, 18, 13, 0, tzinfo=ZoneInfo("America/Chicago")))

    snap = compute.gamma_snapshot("$SPX")
    proj = snap["views"]["GEX"]["projection"]
    assert set(proj) >= {"times", "grid", "cone", "spot"}
    assert proj["times"] and proj["times"][-1] == "15:00"
    assert proj["grid"]                                     # populated
    assert len(next(iter(proj["grid"].values()))) == len(proj["times"])
    assert all(5380.0 <= float(k) <= 5420.0 for k in proj["grid"])   # cropped to +-20 window
    assert set(proj["cone"]) == {"mid", "up", "down"}
    assert "projection" not in snap["views"]["Charm"]
    assert "projection" not in snap["views"]["DEX"]


def test_build_gamma_read_maps_and_falls_back():
    read = compute.build_gamma_read(
        "$SPX", spot=5400.0,
        gex_summary={"flip": 5395.0},
        charm_summary={"flip": 5402.0, "top_pos_strike": 5380.0, "top_neg_strike": 5450.0},
        dex_summary={"net_total": 1.6e9},
        vanna_summary={"net_total": 0.85e9},
        walls={"call_wall": 5470.0, "put_wall": None},  # None -> spot fallback
        regime={"active": True, "composite_score": 7, "bias": "bull_trend",
                "aggregate_confidence": 80})
    assert read.spot == 5400.0
    assert read.call_wall == 5470.0
    assert read.put_wall == 5400.0          # missing wall falls back to spot
    assert read.gamma_flip == 5395.0
    assert read.charm_flip == 5402.0
    assert (read.charm_max_pos, read.charm_max_neg) == (5380.0, 5450.0)
    assert read.dex_flow_usd == 1.6e9
    assert read.vex_notional_usd == 0.85e9
    assert read.sentiment_score == 7
    assert read.sentiment_trend == "bull_trend"
    assert read.sentiment_confidence == 80


def test_build_gamma_read_defaults_when_inactive():
    read = compute.build_gamma_read("$SPX", 5400.0, {}, {}, {}, {}, {}, {"active": False})
    assert read.sentiment_score == 6          # neutral default
    assert read.vex_notional_usd is None      # no vanna net -> 'awaiting data'
    assert read.gamma_flip == 5400.0          # missing flip -> spot


def test_gamma_explain_returns_infographic_html(monkeypatch):
    import sys as _sys
    import types as _types

    _patch_gamma(monkeypatch)
    _FakeEngine.snapshot_summary = staticmethod(lambda data, view: {
        "spot": 5400.0, "flip": 5399.5,
        "top_pos_strike": 5380.0, "top_neg_strike": 5450.0, "net_total": 1.0})
    monkeypatch.setitem(_sys.modules, "regime_filter",
                        _types.SimpleNamespace(evaluate_regime=lambda: {"active": False}))

    out = compute.gamma_explain("$SPX")
    assert out["symbol"] == "$SPX"
    assert "body" not in out                       # no longer the prose body
    assert out["html"].lstrip().startswith("<!DOCTYPE html")
    assert "SPX" in out["html"]                    # rendered the infographic doc
    del _FakeEngine.snapshot_summary


class _FakeAnthropic:
    """Minimal stand-in for anthropic.Anthropic — records the create() kwargs and
    returns a response with a single ``submit_analysis`` tool_use block (``tool_input``)
    or, when ``tool_input`` is None, a plain text block (the no-tool-use path)."""

    def __init__(self, tool_input=None, text=""):
        self._tool_input = tool_input
        self._text = text
        self.kwargs = None
        outer = self

        class _Msgs:
            def create(self, **kw):
                outer.kwargs = kw
                if outer._tool_input is not None:
                    block = type("B", (), {"type": "tool_use", "name": "submit_analysis",
                                           "input": outer._tool_input})()
                else:
                    block = type("B", (), {"type": "text", "text": outer._text})()
                return type("R", (), {"content": [block]})()

        self.messages = _Msgs()


_SAMPLE_ANALYSIS = {
    "regime": "Short gamma below spot",
    "bias": -25,
    "bias_label": "Mildly bearish",
    "headline": "SPX pinned into the close.",
    "narrative": "Dealers are **short gamma** below spot, so dips can amplify.",
    "why": "A hot CPI print this morning lifted yields and pressured the tape.",
    "indices": [{
        "symbol": "$SPX", "spot": 7354, "gamma_flip": 7348, "call_wall": 7370,
        "put_wall": 7335, "max_pain": 7350, "expected_move": 11, "pc_ratio": 0.83,
        "note": "Pinned near the flip.",
        "what_if": {"rally": "Reclaim 7370 and dealers chase higher.",
                    "selloff": "Lose 7348 and gamma flips short toward 7335.",
                    "chop": "Hold 7348-7370 and grind sideways."},
    }],
}


def _patch_analyze_bundle(monkeypatch):
    """Wire the gamma fakes so gamma_analyze builds a real (non-fallback) prompt."""
    import sys as _sys

    _patch_gamma(monkeypatch)
    fake_gt = _sys.modules["gamma_tool"]
    _FakeEngine.calc_expected_move_from_chain = lambda self, chain: 12.0
    fake_gt.build_analysis_dict = lambda snap, view, symbol, dte, **k: {
        "view": view, "symbol": symbol}
    seen = {"args": None}

    def _bundle(spx, spy, qqq, **k):
        seen["args"] = (spx, spy, qqq)
        return "BUNDLED PROMPT"

    fake_gt.build_summary_prompt_bundled = _bundle
    return seen


def test_gamma_analyze_calls_api_and_renders_infographic(monkeypatch):
    seen = _patch_analyze_bundle(monkeypatch)
    client = _FakeAnthropic(tool_input=_SAMPLE_ANALYSIS)

    out = compute.gamma_analyze(client=client)

    # All three symbol bundles built (non-None) and fed to the model verbatim, with
    # the submit_analysis tool forced + thinking disabled.
    assert all(b is not None for b in seen["args"])
    assert client.kwargs["model"] == compute._ANALYZE_MODEL
    assert client.kwargs["thinking"] == {"type": "disabled"}
    assert client.kwargs["tool_choice"]["name"] == "submit_analysis"
    assert client.kwargs["messages"][0]["content"] == "BUNDLED PROMPT"
    # Output is a standalone HTML infographic built from the structured data.
    html = out["html"]
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "Short gamma below spot" in html          # regime banner
    assert "Mildly bearish" in html                  # bias meter label
    assert "<svg" in html and "Call wall" in html     # price-level ladder
    assert "7,370" in html                            # call wall in a tile/ladder
    assert "short gamma" in html.lower() and "<strong>" in html  # narrative (markdown)
    # Per-index what-if (rally / sell-off / chop) + bottom "Why is this happening".
    assert "What if" in html and "Rally" in html and "Sell-off" in html and "Chop" in html
    assert "Reclaim 7370" in html and "gamma flips short" in html
    assert "Why is this happening" in html and "hot CPI print" in html
    assert out["prompt"] == "BUNDLED PROMPT"
    assert out["analysis"]["bias"] == -25
    del _FakeEngine.calc_expected_move_from_chain


def test_gamma_analyze_overrides_em_with_authoritative(monkeypatch):
    # The model's copied expected_move is replaced by the code-computed 1-day EM
    # (matched by symbol), so the displayed value is authoritative, not AI-echoed.
    _patch_analyze_bundle(monkeypatch)
    monkeypatch.setattr(compute, "_session_expected_move", lambda chain: 46.0)
    client = _FakeAnthropic(tool_input=_SAMPLE_ANALYSIS)  # sample SPX expected_move = 11

    out = compute.gamma_analyze(client=client)
    spx = next(i for i in out["analysis"]["indices"] if i["symbol"] == "$SPX")
    assert spx["expected_move"] == 46.0          # overridden, not the model's 11
    assert "46.0" in out["html"]
    del _FakeEngine.calc_expected_move_from_chain


def test_session_expected_move_defensive():
    assert compute._session_expected_move(None) is None
    assert compute._session_expected_move({}) is None
    assert compute._session_expected_move({"underlyingPrice": 0}) is None


def test_gamma_analyze_no_tool_use_degrades(monkeypatch):
    _patch_analyze_bundle(monkeypatch)
    client = _FakeAnthropic(tool_input=None, text="(no tool call)")  # text-only reply

    out = compute.gamma_analyze(client=client)
    assert out["html"].lstrip().startswith("<!DOCTYPE html>")
    assert "no usable analysis" in out["html"].lower()
    del _FakeEngine.calc_expected_move_from_chain


def test_parse_analysis_defensive():
    assert compute._parse_analysis(None) is None
    assert compute._parse_analysis({}) is None  # nothing renderable
    out = compute._parse_analysis({
        "headline": "x", "bias": "not-a-number",
        "indices": [{"symbol": "SPY", "spot": "abc", "call_wall": 700}, "junk"]})
    assert out["bias"] is None and out["indices"][0]["spot"] is None
    assert out["indices"][0]["call_wall"] == 700.0 and len(out["indices"]) == 1


def test_analyze_infographic_html_handles_missing_fields():
    # A sparse index (only a spot) still renders tiles ('—' for missing) + a ladder.
    html = compute.analyze_infographic_html(
        {"regime": "R", "bias": 10, "headline": "H", "narrative": "",
         "indices": [{"symbol": "QQQ", "spot": 500}]})
    assert "QQQ" in html and "—" in html and "<svg" in html


def test_analyze_history_doc_combines_stored_briefings():
    rows = [
        {"date": "2026-07-02", "slot": "open",
         "generated_at": "2026-07-02T08:48:00-05:00", "analysis": _SAMPLE_ANALYSIS},
        {"date": "2026-07-02", "slot": "midday",
         "generated_at": "2026-07-02T11:30:00-05:00",
         "analysis": {**_SAMPLE_ANALYSIS, "headline": "Midday read"}},
    ]
    html = compute.analyze_history_doc(rows, title="My Report")
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "My Report" in html and "2 briefing(s)" in html
    assert "open" in html and "midday" in html                 # per-briefing section headers
    assert "Short gamma below spot" in html                    # regime re-rendered from data
    assert html.count("ga-sec") >= 2                           # one section per briefing


def test_analyze_history_doc_skips_rows_without_analysis():
    html = compute.analyze_history_doc([{"date": "x", "slot": "y", "analysis": None}])
    assert "0 briefing(s)" in html and "No briefings found" in html


def test_gamma_analyze_no_key_returns_config_message(monkeypatch):
    _patch_analyze_bundle(monkeypatch)
    monkeypatch.setattr(compute, "_make_analyze_client", lambda: None)

    out = compute.gamma_analyze()  # no injected client + no key → graceful HTML

    assert out["html"].lstrip().startswith("<!DOCTYPE html>")
    assert "ANTHROPIC_API_KEY" in out["html"]
    del _FakeEngine.calc_expected_move_from_chain


def test_gamma_analyze_api_error_returns_html(monkeypatch):
    _patch_analyze_bundle(monkeypatch)

    class _Boom:
        class _Msgs:
            def create(self, **kw):
                raise RuntimeError("network down")
        messages = _Msgs()

    out = compute.gamma_analyze(client=_Boom())
    assert out["html"].lstrip().startswith("<!DOCTYPE html>")
    assert "failed" in out["html"].lower()
    del _FakeEngine.calc_expected_move_from_chain


def test_gamma_analyze_degrades_when_no_chains(monkeypatch):
    # Weekend / off-hours: all chain fetches fail → build_summary_prompt_bundled
    # raises. gamma_analyze must still return a readable HTML page (never raise) so
    # the new tab opens with feedback instead of the button silently doing nothing.
    _patch_gamma(monkeypatch)

    class _Bad:
        status_code = 500
        def json(self):  # noqa: E704
            return None
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain",
                        lambda *a, **k: _Bad())
    out = compute.gamma_analyze()
    assert isinstance(out.get("html"), str) and out["html"]
    assert "could not fetch" in out["html"].lower()


# ── Header helpers (moved from webgui/tests/test_options_header.py) ──────────
def test_sentiment_dot_no_data_when_inactive():
    assert compute.sentiment_dot({"active": False})[1] == "No data"
    assert compute.sentiment_dot(None)[1] == "No data"


def test_sentiment_dot_bullish_when_ccs_blocked():
    assert compute.sentiment_dot(
        {"active": True, "allow_ccs": False, "allow_pcs": True})[1] == "Bullish"


def test_sentiment_dot_bearish_when_pcs_blocked():
    assert compute.sentiment_dot(
        {"active": True, "allow_ccs": True, "allow_pcs": False})[1] == "Bearish"


def test_sentiment_dot_neutral_when_both_allowed():
    assert compute.sentiment_dot(
        {"active": True, "allow_ccs": True, "allow_pcs": True})[1] == "Neutral"


def test_quote_last_extracts_last_price():
    raw = {"SPY": {"quote": {"lastPrice": 742.36}}}
    assert compute.quote_last(raw, "SPY") == 742.36


def test_quote_last_missing_returns_none():
    assert compute.quote_last({}, "SPY") is None
    assert compute.quote_last(None, "SPY") is None


class _FakeQuotesResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_refresh_header_shape(monkeypatch):
    raw = {
        "$SPX": {"quote": {"lastPrice": 5400.12}},
        "SPY": {"quote": {"lastPrice": 742.36}},
        "QQQ": {"quote": {"lastPrice": 480.0}},
        "$VIX": {"quote": {"lastPrice": 14.2}},
    }
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_quotes",
                        lambda syms: _FakeQuotesResp(raw))
    monkeypatch.setattr(compute, "vix_regime",
                        lambda v: {"label": "Calm", "color": "#1D9E75"})
    monkeypatch.setattr(compute, "evaluate_regime",
                        lambda: {"active": True, "allow_ccs": True, "allow_pcs": True})

    out = compute.refresh_header()
    assert out["prices"] == {"$SPX": 5400.12, "SPY": 742.36, "QQQ": 480.0}
    assert out["vix"] == 14.2
    assert out["vix_regime"] == {"label": "Calm", "color": "#1D9E75"}
    assert out["sentiment"] == {"color": "#EFC347", "label": "Neutral"}  # neutral dot


def test_refresh_header_quotes_failure_is_blank(monkeypatch):
    def _boom(syms):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_quotes", _boom)
    monkeypatch.setattr(compute, "evaluate_regime", lambda: None)

    out = compute.refresh_header()
    assert out["prices"] == {"$SPX": None, "SPY": None, "QQQ": None}
    assert out["vix"] is None
    assert out["vix_regime"] == {}
    # No active regime -> no-data dot.
    assert out["sentiment"] == {"color": "#666666", "label": "No data"}


# ── Simulator (moved from webgui/pages/options/simulator.py) ─────────────────
class _SimRow:
    def __init__(self, expiry, kind, strike):
        self.expiry, self.kind, self.strike = expiry, kind, strike


class _SimSnap:
    def __init__(self, symbol, spot, contracts):
        self.symbol, self.spot, self.contracts = symbol, spot, contracts


def test_expiries_of_dedupes_sorted():
    snap = _SimSnap("SPY", 450.0, [
        _SimRow("2026-06-19", "call", 450), _SimRow("2026-06-18", "call", 455),
        _SimRow("2026-06-19", "put", 445)])
    assert compute.expiries_of(snap) == ["2026-06-18", "2026-06-19"]


def test_strikes_of_filters_by_expiry_and_kind():
    snap = _SimSnap("SPY", 450.0, [
        _SimRow("2026-06-19", "call", 450), _SimRow("2026-06-19", "put", 445),
        _SimRow("2026-06-18", "call", 460)])
    assert compute.strikes_of(snap, "2026-06-19", "call") == [450]
    assert compute.strikes_of(snap, "2026-06-19", "put") == [445]


def test_find_contract_matches_by_triple():
    c = _SimRow("2026-06-19", "call", 450)
    snap = _SimSnap("SPY", 450.0, [c, _SimRow("2026-06-19", "put", 445)])
    assert compute.find_contract(snap, "2026-06-19", "call", 450) is c
    assert compute.find_contract(snap, "2026-06-19", "call", 999) is None


def _patch_sim(monkeypatch, snap):
    """Stub the lazily-imported ``options_simulator.data``/``.engine`` modules."""
    import sys as _sys
    import types as _types

    fake_data = _types.SimpleNamespace(
        fetch_snapshot=lambda client, symbol: snap)

    class _FakeDF:
        def __init__(self, rows):
            self._rows = rows

        def to_dict(self, orient):
            return self._rows

    class _WhatIf:
        def __init__(self, s):
            pass

        def sweep(self, c, s_range, t_days):
            return _FakeDF([{"S": float(s), "theo_price": float(s) - 100.0}
                            for s in s_range])

    class _Shock:
        def __init__(self, s):
            pass

        def sweep(self, c, mults):
            return _FakeDF([{"theo_price": 1.0 * m, "delta": 0.5, "gamma": 0.02,
                             "theta": -0.1, "vega": 0.3} for m in mults])

    def _from_legs(legs, label):
        # Mirror the engine's Position.from_legs: wrap each (contract, sign, ratio)
        # in a leg namespace so aggregate_position can reach .legs[*].contract.
        return _types.SimpleNamespace(
            legs=[_types.SimpleNamespace(contract=c, sign=int(s), ratio=int(r))
                  for c, s, r in legs],
            label=label)

    def _aggregate(pos, fn):
        # Robust to both the legacy single() tuple and the from_legs namespace:
        # apply per_leg_fn to the first leg's contract (single-leg shape is what
        # the existing assertions pin; the new multileg test only checks shape).
        if hasattr(pos, "legs"):
            return fn(pos.legs[0].contract)
        return fn(pos[1])

    fake_engine = _types.SimpleNamespace(
        Position=_types.SimpleNamespace(
            single=lambda contract, direction, symbol: ("pos", contract, direction, symbol),
            from_legs=_from_legs),
        WhatIfEngine=_WhatIf,
        IVShockEngine=_Shock,
        aggregate_position=_aggregate)
    monkeypatch.setitem(_sys.modules, "options_simulator", _types.ModuleType("options_simulator"))
    monkeypatch.setitem(_sys.modules, "options_simulator.data", fake_data)
    monkeypatch.setitem(_sys.modules, "options_simulator.engine", fake_engine)


def test_sim_fetch_stores_snapshot_and_returns_meta(monkeypatch):
    snap = _SimSnap("SPY", 450.0, [
        _SimRow("2026-06-19", "call", 450), _SimRow("2026-06-19", "put", 445),
        _SimRow("2026-06-18", "call", 460)])
    _patch_sim(monkeypatch, snap)
    compute._SIM_SNAPSHOTS.clear()

    meta = compute.sim_fetch("SPY")
    assert meta["symbol"] == "SPY"
    assert meta["spot"] == 450.0
    assert meta["n_contracts"] == 3
    assert meta["expiries"] == ["2026-06-18", "2026-06-19"]
    assert meta["strikes"]["2026-06-19"] == {"call": [450], "put": [445]}
    assert meta["strikes"]["2026-06-18"] == {"call": [460], "put": []}
    # Snapshot stashed in-process for sim_run.
    assert compute._SIM_SNAPSHOTS["SPY"] is snap


def test_sim_run_returns_whatif_and_ivshock(monkeypatch):
    snap = _SimSnap("SPY", 450.0, [_SimRow("2026-06-19", "call", 450)])
    _patch_sim(monkeypatch, snap)
    compute._SIM_SNAPSHOTS.clear()
    compute._SIM_SNAPSHOTS["SPY"] = snap

    out = compute.sim_run("SPY", "2026-06-19", "call", 450, "buy", 5, 1.5)
    assert out["spot"] == 450.0
    # What-if: 81-point sweep, each a plain dict with S + theo_price.
    assert len(out["whatif_rows"]) == 81
    assert out["whatif_rows"][0]["S"] == 450.0 * 0.8
    assert out["whatif_rows"][-1]["S"] == 450.0 * 1.2
    # IV-shock: base (×1.0) + shock (×1.5).
    assert out["ivshock"]["base"]["theo_price"] == 1.0
    assert out["ivshock"]["shock"]["theo_price"] == 1.5


def test_sim_run_whatif_dollars_and_entry_baseline(monkeypatch):
    """What-if rows carry DOLLAR position values (the ×100 contract multiplier the
    old path dropped) and sim_run returns ``whatif_baseline`` — the position's $
    value at (spot, NOW). The page subtracts that baseline so the payoff is measured
    from trade entry (matches the Calculator), not from the forward-time spot value."""
    snap = _SimSnap("SPY", 450.0, [_SimRow("2026-06-19", "call", 450)])
    _patch_sim(monkeypatch, snap)
    compute._SIM_SNAPSHOTS.clear()
    compute._SIM_SNAPSHOTS["SPY"] = snap

    out = compute.sim_run("SPY", "2026-06-19", "call", 450, "buy", 5, 1.5)
    # Fake sweep theo = S − 100 (per-share × qty); ×100 → dollars.
    assert out["whatif_rows"][0]["theo_price"] == (450.0 * 0.8 - 100.0) * 100
    assert out["whatif_rows"][-1]["theo_price"] == (450.0 * 1.2 - 100.0) * 100
    # Entry baseline = position value at (spot, now) = (spot − 100) × 100.
    assert out["whatif_baseline"] == (450.0 - 100.0) * 100


def test_sim_run_empty_when_no_snapshot(monkeypatch):
    _patch_sim(monkeypatch, _SimSnap("SPY", 450.0, []))
    compute._SIM_SNAPSHOTS.clear()
    # No snapshot stashed for QQQ -> empty (page prompts a re-fetch).
    assert compute.sim_run("QQQ", "2026-06-19", "call", 450, "buy", 5, 1.5) == {}


def test_sim_run_empty_when_contract_missing(monkeypatch):
    snap = _SimSnap("SPY", 450.0, [_SimRow("2026-06-19", "call", 450)])
    _patch_sim(monkeypatch, snap)
    compute._SIM_SNAPSHOTS.clear()
    compute._SIM_SNAPSHOTS["SPY"] = snap
    # Strike not in the snapshot -> empty (page prompts a selection).
    assert compute.sim_run("SPY", "2026-06-19", "call", 999, "buy", 5, 1.5) == {}


def test_sim_run_multileg_put_spread(monkeypatch):
    """The new ``legs=`` path builds a multi-leg Position (short 95P / long 90P)
    and returns the same What-if + IV-shock shape as the single-leg path."""
    from datetime import date, timedelta

    exp = (date.today() + timedelta(days=10)).isoformat()
    snap = _SimSnap("TEST", 100.0,
                    [_SimRow(exp, "put", 95), _SimRow(exp, "put", 90)])
    _patch_sim(monkeypatch, snap)
    compute._SIM_SNAPSHOTS.clear()
    compute._SIM_SNAPSHOTS["TEST"] = snap

    out = compute.sim_run(
        "TEST",
        legs=[{"kind": "put", "strike": 95, "expiry": exp, "side": "short", "qty": 1},
              {"kind": "put", "strike": 90, "expiry": exp, "side": "long", "qty": 1}],
        dt=0.0, mult=1.5)
    assert out["spot"] == 100.0
    assert len(out["whatif_rows"]) == 81
    assert "S" in out["whatif_rows"][0]
    assert out["ivshock"] and "base" in out["ivshock"] and "shock" in out["ivshock"]


def _real_replay_snapshot(symbol, iv=0.20, index=None, prices=None, contracts=None):
    """Build a REAL ChainSnapshot using the engine classes (its ``price_history``
    is now ignored by ``sim_replay`` — the history is fetched from the proxy — but
    the snapshot still supplies the contract/spot/r).

    By default the snapshot carries a single 450-call ContractRow (existing
    single-leg callers rely on this). Pass ``contracts`` (a list of real
    ``ContractRow``s) to override it for the multi-leg path."""
    import sys as _sys, datetime as _dt
    from repo_paths import OPTIONS_SCANNER
    _sys.path.insert(0, str(OPTIONS_SCANNER))
    from options_simulator import engine as seng
    import pandas as pd

    if index is None:
        index = ["2026-06-18 09:30", "2026-06-18 09:31", "2026-06-18 09:32"]
    if prices is None:
        prices = [450.0, 451.0, 452.0]
    if contracts is None:
        contracts = [seng.ContractRow(strike=450.0, kind="call", bid=1.0, ask=1.2,
                                      mid=1.1, iv=iv, expiry=_dt.date(2026, 6, 26))]
    return seng.ChainSnapshot(spot=prices[-1],
                              as_of=_dt.datetime(2026, 6, 18, 9, 32), r=0.04,
                              symbol=symbol, contracts=contracts,
                              price_history=pd.Series(dtype=float))


def _patch_replay_history(monkeypatch, index, prices, calls=None):
    """Monkeypatch ``compute._proxy.schwab_client`` to return the given path from
    BOTH intraday + daily fetches, so ``sim_replay`` gets a deterministic history
    regardless of which DTE tier today's date lands on."""
    import pandas as pd, types
    dframe = pd.DataFrame({"datetime": pd.to_datetime(index), "close": list(prices)})

    def _intraday(symbol, minutes, days):
        if calls is not None:
            calls["intraday"] = (symbol, minutes, days)
        return dframe

    def _daily(symbol, months):
        if calls is not None:
            calls["daily"] = (symbol, months)
        return dframe

    monkeypatch.setattr(compute._proxy, "schwab_client",
                        types.SimpleNamespace(get_intraday_history=_intraday,
                                              get_daily_history=_daily))


def test_sim_replay_builds_jsonsafe_trace(monkeypatch):
    import json
    compute._SIM_SNAPSHOTS.clear()
    compute._SIM_SNAPSHOTS["SPY"] = _real_replay_snapshot("SPY")
    _patch_replay_history(
        monkeypatch,
        ["2026-06-18 09:30", "2026-06-18 09:31", "2026-06-18 09:32"],
        [450.0, 451.0, 452.0])

    out = compute.sim_replay("SPY", "2026-06-26", "call", 450.0, "buy")
    assert out["spot"] == 452.0
    assert out["timestamps"] == ["2026-06-18T09:30:00", "2026-06-18T09:31:00",
                                 "2026-06-18T09:32:00"]
    assert out["prices"] == [450.0, 451.0, 452.0]
    assert set(out["greeks"]) == {"delta", "gamma", "theta", "vega", "rho"}
    assert len(out["greeks"]["delta"]) == 3
    assert out["x"] == [0, 1, 2]
    assert out["gaps"] == []                       # single session, no overnight gap
    assert len(out["sessions"]) == 1
    assert out["sessions"][0]["date"] == "2026-06-18"
    assert out["lookback"]["key"] == "auto"
    json.dumps(out)                                # JSON-serializable end to end


def test_sim_replay_detects_overnight_gap(monkeypatch):
    import pandas as pd
    compute._SIM_SNAPSHOTS.clear()
    # Two realistic 1-min sessions a day apart: the median bar spacing is ~60s,
    # so the overnight break (>1h) registers as a single gap between them.
    s1 = pd.date_range("2026-06-18 09:30", periods=5, freq="1min")
    s2 = pd.date_range("2026-06-19 09:30", periods=5, freq="1min")
    idx = list(s1) + list(s2)
    prices = [450.0 + i for i in range(len(idx))]
    compute._SIM_SNAPSHOTS["SPY"] = _real_replay_snapshot("SPY")
    _patch_replay_history(monkeypatch, idx, prices)
    out = compute.sim_replay("SPY", "2026-06-26", "call", 450.0, "buy")
    assert out["gaps"] == [5]                       # boundary before the 6th bar
    assert [s["date"] for s in out["sessions"]] == ["2026-06-18", "2026-06-19"]


def test_sim_replay_override_uses_fixed_window(monkeypatch):
    compute._SIM_SNAPSHOTS.clear()
    compute._SIM_SNAPSHOTS["SPY"] = _real_replay_snapshot("SPY")
    calls = {}
    _patch_replay_history(
        monkeypatch,
        ["2026-06-18 09:30", "2026-06-18 09:35", "2026-06-18 09:40"],
        [450.0, 451.0, 452.0], calls=calls)
    out = compute.sim_replay("SPY", "2026-06-26", "call", 450.0, "buy",
                             lookback="15m_10d")
    assert calls["intraday"][1] == 15              # minutes from the override
    assert calls["intraday"][2] == 10              # days from the override
    assert out["lookback"]["label"] == "15-min · 10d"
    assert out["lookback"]["key"] == "15m_10d"


def test_replay_lookback_spec_auto_tiers():
    assert compute.replay_lookback_spec(0)["minutes"] == 1
    assert compute.replay_lookback_spec(0)["days"] == 1
    assert compute.replay_lookback_spec(3)["minutes"] == 5
    assert compute.replay_lookback_spec(3)["days"] == 3
    assert compute.replay_lookback_spec(15)["days"] == 5
    big = compute.replay_lookback_spec(30)
    assert big["freq_type"] == "daily" and big["bars"] == 15


def test_replay_lookback_spec_override_keys():
    assert compute.replay_lookback_spec(99, "1m_1d")["minutes"] == 1
    assert compute.replay_lookback_spec(0, "15m_10d")["minutes"] == 15
    assert compute.replay_lookback_spec(0, "1d_20d")["freq_type"] == "daily"
    # Unknown override falls back to auto.
    assert compute.replay_lookback_spec(0, "bogus") == compute.replay_lookback_spec(0)


def test_sim_replay_missing_snapshot_returns_empty():
    compute._SIM_SNAPSHOTS.pop("NOPE", None)
    assert compute.sim_replay("NOPE", "2026-06-26", "call", 1.0, "buy") == {}


def test_sim_replay_zero_iv_degrades():
    compute._SIM_SNAPSHOTS.clear()
    compute._SIM_SNAPSHOTS["ZIV"] = _real_replay_snapshot("ZIV", iv=0.0)
    out = compute.sim_replay("ZIV", "2026-06-26", "call", 450.0, "buy")
    assert out.get("error")


def test_sim_replay_multileg_put_spread(monkeypatch):
    """The new ``legs=`` path re-prices a multi-leg position (short 95P /
    long 90P) along the underlying's path through the REAL engine, netting the
    two legs into one trace with the same JSON-safe shape as the single-leg
    path."""
    import datetime as _dt
    from repo_paths import OPTIONS_SCANNER
    import sys as _sys
    _sys.path.insert(0, str(OPTIONS_SCANNER))
    from options_simulator import engine as seng

    exp = _dt.date(2026, 7, 17)
    contracts = [
        seng.ContractRow(strike=95.0, kind="put", bid=1.0, ask=1.2, mid=1.1,
                         iv=0.25, expiry=exp),
        seng.ContractRow(strike=90.0, kind="put", bid=0.4, ask=0.6, mid=0.5,
                         iv=0.30, expiry=exp),
    ]
    compute._SIM_SNAPSHOTS.clear()
    compute._SIM_SNAPSHOTS["SPY"] = _real_replay_snapshot(
        "SPY", prices=[100.0, 99.0, 98.0], contracts=contracts)
    _patch_replay_history(
        monkeypatch,
        ["2026-06-18 09:30", "2026-06-18 09:31", "2026-06-18 09:32"],
        [100.0, 99.0, 98.0])

    out = compute.sim_replay("SPY", legs=[
        {"kind": "put", "strike": 95, "expiry": "2026-07-17", "side": "short", "qty": 1},
        {"kind": "put", "strike": 90, "expiry": "2026-07-17", "side": "long", "qty": 1}])
    assert out.get("x") and out.get("prices")
    assert len(out["greeks"]["delta"]) == len(out["prices"])
    assert "error" not in out


# ── Calculator (moved from webgui/pages/options/calculator.py) ───────────────
class _FakeCalcResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


def _patch_calc_oc(monkeypatch, **fns):
    """Stub the lazily-imported ``options_calculator`` module."""
    import sys as _sys
    import types as _types

    fake_oc = _types.SimpleNamespace(**fns)
    monkeypatch.setitem(_sys.modules, "options_calculator", fake_oc)
    return fake_oc


def test_calc_load_returns_chain_price_range(monkeypatch):
    quote = {"SPY": {"quote": {"lastPrice": 450.0}}}
    chain = {"callExpDateMap": {"2026-06-19:4": {"450.0": [{"mark": 1.0}]}}}
    seen = {}

    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_quotes",
                        lambda syms: (seen.__setitem__("qsyms", syms),
                                      _FakeCalcResp(quote))[1])
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain",
                        lambda *a, **k: (seen.__setitem__("chain_args", (a, k)),
                                         _FakeCalcResp(chain))[1])
    _patch_calc_oc(monkeypatch, generate_price_range=lambda p: (p * 0.95, p * 1.05))

    out = compute.calc_load_symbol("spy")
    assert out["symbol"] == "spy"
    assert out["api"] == "SPY"
    assert out["price"] == 450.0
    assert out["range_lo"] == 450.0 * 0.95
    assert out["range_hi"] == 450.0 * 1.05
    assert out["chain"] == chain
    assert seen["qsyms"] == ["SPY"]  # quote fetched for the API symbol


def test_calc_load_maps_spx_and_handles_no_price(monkeypatch):
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_quotes",
                        lambda syms: _FakeCalcResp({}))  # no quote for $SPX
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain",
                        lambda *a, **k: _FakeCalcResp(None, status=500))  # chain failed
    called = {"range": 0}
    _patch_calc_oc(monkeypatch,
                   generate_price_range=lambda p: (called.__setitem__("range", 1), (0, 0))[1])

    out = compute.calc_load_symbol("SPX")
    assert out["api"] == "$SPX"
    assert out["price"] is None
    assert out["range_lo"] == 0.0 and out["range_hi"] == 0.0
    assert out["chain"] is None
    assert called["range"] == 0  # generate_price_range NOT called when no price


def test_calc_compute_returns_summary_grid_labels(monkeypatch):
    import datetime as dt

    seen = {}

    def _summary(legs, strategy, spot, r=None, iv=None, T=None):
        seen["summary"] = dict(strategy=strategy, spot=spot, r=r, iv=iv, T=T, legs=legs)
        return {"max_loss": 100.0, "max_profit": 50.0}

    def _eval_dates(today, expiry):
        seen["eval"] = (today, expiry)
        return [dt.date(2026, 6, 18), dt.date(2026, 6, 19)]

    def _spread_pnl(legs, spot, iv, r, eval_dates, price_range, expiry,
                    iv_adjustment=0.0, eval_times=None, per_leg_expiry=False,
                    rows_per_side=30, price_rows=None):
        seen["pnl"] = dict(iv=iv, r=r, price_range=price_range, iv_adj=iv_adjustment,
                           eval_times=eval_times, rows_per_side=rows_per_side,
                           price_rows=price_rows)
        return [{"price": 450.0, "pnl": [10, -5], "pnl_pct": [2.0, -1.0]}]

    _patch_calc_oc(monkeypatch, calc_summary=_summary,
                   generate_eval_dates=_eval_dates, calc_spread_pnl=_spread_pnl,
                   generate_price_range=lambda spot, pct=0.05: (spot * (1 - pct),
                                                                spot * (1 + pct)))

    legs = [{"strike": 445.0, "premium": 0.5, "option_type": "put",
             "side": "short", "qty": 1}]
    out = compute.calc_compute(
        strategy="PCS", spot=450.0, iv=0.18, rate=0.045, ivadj=0.0, qty=1,
        expiry="2026-06-19", legs=legs)

    assert out["summary"] == {"max_loss": 100.0, "max_profit": 50.0}
    # First column is the intraday "Now"; subsequent eval dates keep MM/DD labels.
    assert out["eval_labels"] == ["Now", "06/19"]
    assert out["pnl_data"][0]["pnl"] == [10, -5]
    # Intraday time-to-expiry threaded to the engine (one per column).
    assert seen["pnl"]["eval_times"] is not None
    assert len(seen["pnl"]["eval_times"]) == 2
    # Math wired through: rate->r, iv passed. No explicit price_rows ⇒ engine fallback
    # over ±num_strikes (default 24) rows.
    assert seen["summary"]["r"] == 0.045 and seen["summary"]["iv"] == 0.18
    assert seen["pnl"]["price_rows"] is None and seen["pnl"]["rows_per_side"] == 24
    assert seen["eval"][1] == dt.date(2026, 6, 19)


def test_calc_compute_uses_explicit_price_rows(monkeypatch):
    import datetime as dt

    seen = {}
    _patch_calc_oc(
        monkeypatch,
        calc_summary=lambda *a, **k: {},
        generate_eval_dates=lambda t, e: [dt.date(2026, 6, 19)],
        generate_price_range=lambda *a, **k: (0.0, 0.0),  # unused now
        calc_spread_pnl=lambda legs, spot, iv, r, ed, pr, exp, iv_adjustment=0.0,
        eval_times=None, per_leg_expiry=False, rows_per_side=30, price_rows=None:
        (seen.__setitem__("rows", price_rows), [])[1])

    # The page's explicit ±N real chain strikes are threaded straight to the engine.
    compute.calc_compute(strategy="PCS", spot=450.0, iv=0.18, rate=0.045, ivadj=0.0,
                         qty=1, expiry="2026-06-19", legs=[],
                         num_strikes=24, price_rows=[445.0, 450.0, 455.0])
    assert seen["rows"] == [445.0, 450.0, 455.0]


# ── intraday time-to-expiry + 0DTE current-P&L (the calculator bug fix) ───────
def _et(y, m, d, hh, mm=0):
    import datetime as dt
    from zoneinfo import ZoneInfo
    return dt.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/New_York"))


def test_time_to_expiry_years_intraday_and_after_close():
    import datetime as dt

    expiry = dt.date(2026, 6, 23)
    # 1:00pm ET on expiry day → 3 hours to the 4:00pm ET close.
    T = compute.time_to_expiry_years(_et(2026, 6, 23, 13), expiry)
    assert T == pytest.approx(3.0 / 24 / 365, rel=1e-6)
    # After the close on expiry day → expired (T = 0), never negative.
    assert compute.time_to_expiry_years(_et(2026, 6, 23, 16, 30), expiry) == 0.0
    # Multi-day: ~2 days + 3 hours of remaining time.
    T2 = compute.time_to_expiry_years(_et(2026, 6, 21, 13), expiry)
    assert T2 == pytest.approx((2 * 24 + 3) / 24 / 365, rel=1e-6)


def test_calc_iv_implies_iv_from_mark(monkeypatch):
    # Real options_calculator (no stub): QQQ 725C, mark 0.19, ~2.45h to 4pmET → ~38%.
    res = compute.calc_iv(spot=718.82, strike=725.0, option_type="call",
                          mark=0.19, expiry="2026-06-23", rate=0.045,
                          now=_et(2026, 6, 23, 13, 33))  # ≈2.45h to close
    assert res["error"] is None
    assert 36.0 < res["iv"] < 40.0          # ≈ ThinkorSwim's 38.3%
    assert res["strike"] == 725.0


def test_calc_iv_degrades_when_unsolvable():
    # After the close (T=0) there is no implied vol — return None + a reason.
    res = compute.calc_iv(spot=718.82, strike=725.0, option_type="call",
                          mark=0.19, expiry="2026-06-23", rate=0.045,
                          now=_et(2026, 6, 23, 17, 0))
    assert res["iv"] is None and res["error"]


def test_calc_compute_0dte_now_column_shows_current_pnl():
    """The regression: on expiration day the matrix must show CURRENT value in the
    'Now' column (time value intact), not the expiration payoff. Real engine."""
    import datetime as dt

    import options_calculator as oc

    today = dt.date.today()
    T_now = 6.0 / 24 / 365
    spot = 100.0
    prem = oc.bs_price(spot, 100.0, T_now, 0.045, 0.30, "call")  # ATM premium @ T_now
    legs = [{"strike": 100.0, "premium": prem, "option_type": "call",
             "side": "long", "qty": 1}]
    now = dt.datetime.combine(today, dt.time(10, 0),
                              tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    out = compute.calc_compute(
        strategy="LONG_CALL", spot=spot, iv=0.30, rate=0.045, ivadj=0.0, qty=1,
        expiry=today.isoformat(), legs=legs, now=now)

    # Columns: an intraday "Now" + the expiration payoff.
    assert out["eval_labels"][0] == "Now"
    assert out["eval_labels"][-1] == "Exp"
    spot_row = min(out["pnl_data"], key=lambda r: abs(r["price"] - spot))
    now_pnl, exp_pnl = spot_row["pnl"][0], spot_row["pnl"][-1]
    # 'Now' at spot ≈ break-even (still holds its premium); 'Exp' = full loss.
    assert abs(now_pnl) < 2.0
    assert exp_pnl == pytest.approx(-prem * 100, abs=0.5)
    assert now_pnl > exp_pnl   # today's value is NOT the expiration payoff


def test_calc_compute_multiday_builds_now_and_future_columns(monkeypatch):
    import datetime as dt

    seen = {}
    _patch_calc_oc(
        monkeypatch,
        calc_summary=lambda *a, **k: (seen.__setitem__("T", k.get("T")), {})[1],
        generate_eval_dates=lambda t, e: [t, t + dt.timedelta(days=2), e],
        generate_price_range=lambda *a, **k: (0.0, 0.0),
        calc_spread_pnl=lambda legs, spot, iv, r, ed, pr, exp, iv_adjustment=0.0,
        eval_times=None, per_leg_expiry=False, rows_per_side=30, price_rows=None:
        (seen.__setitem__("times", eval_times), [])[1])

    today = dt.date.today()
    expiry = today + dt.timedelta(days=4)
    now = dt.datetime.combine(today, dt.time(10, 0),
                              tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    out = compute.calc_compute(
        strategy="LONG_CALL", spot=100.0, iv=0.30, rate=0.045, ivadj=0.0, qty=1,
        expiry=expiry.isoformat(), legs=[{"strike": 100.0}], now=now)

    # "Now" replaces today's slot; future dates keep MM/DD; expiry column T == 0.
    assert out["eval_labels"][0] == "Now"
    assert out["eval_labels"][-1] == expiry.strftime("%m/%d")
    assert seen["times"][0] > 0 and seen["times"][-1] == 0.0
    # Summary priced at the intraday "Now" T (NOT the old 1/365 clamp).
    assert seen["T"] == pytest.approx(seen["times"][0], rel=1e-9)


def test_refresh_header_sentiment_failure_is_no_data(monkeypatch):
    raw = {"$VIX": {"quote": {"lastPrice": 22.0}}}
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_quotes",
                        lambda syms: _FakeQuotesResp(raw))
    monkeypatch.setattr(compute, "vix_regime", lambda v: {"label": "Elevated"})

    def _boom():
        raise RuntimeError("bridge missing")

    monkeypatch.setattr(compute, "evaluate_regime", _boom)

    out = compute.refresh_header()
    assert out["vix"] == 22.0
    assert out["sentiment"] == {"color": "#666666", "label": "No data"}


# ── Intraday GEX history collection ─────────────────────────────────────────
# collect_gex_snapshots reuses options-scanner's gex_collector.poll_once. We
# fake the lazily-imported gex_collector/gamma_tool/gex_history_db modules so
# nothing touches a live proxy or the on-disk DB.

def _fake_gex_modules(monkeypatch, *, lock_ok=True):
    import sys as _sys
    import types as _types

    calls = {"poll": False, "touched": False, "closed": False,
             "client": None, "engine": None, "conn": None}

    class _Conn:
        def close(self):
            calls["closed"] = True

    def _poll(client, engine, conn, lock=None, on_chain=None):
        calls.update(poll=True, client=client, engine=engine, conn=conn,
                     on_chain=on_chain)

    fake_gc = _types.SimpleNamespace(
        LOCK_PATH="LOCK", SYMBOLS=["$SPX", "SPY"],
        collection_symbols=lambda: ["$SPX", "SPY", "NVDA"],
        acquire_collector_lock=lambda path, **kw: lock_ok,
        touch_lock=lambda path, **kw: calls.update(touched=True),
        ensure_file_logging=lambda *a, **k: None,
        poll_once=_poll,
        log=_types.SimpleNamespace(info=lambda *a, **k: None),
    )
    def _purge(conn, keep_sessions=5):
        calls["purged"] = calls.get("purged", 0) + 1
        calls["keep_sessions"] = keep_sessions
        return 0

    fake_gh = _types.SimpleNamespace(connect=lambda: _Conn(),
                                     init_schema=lambda conn: None,
                                     purge_keep_sessions=_purge)
    fake_gt = _types.SimpleNamespace(GammaEngine=lambda: "ENGINE")
    monkeypatch.setitem(_sys.modules, "gex_collector", fake_gc)
    monkeypatch.setitem(_sys.modules, "gex_history_db", fake_gh)
    monkeypatch.setitem(_sys.modules, "gamma_tool", fake_gt)
    # Reset the once-per-day purge latch + the once-per-process schema latch so
    # each test starts fresh (module-level state otherwise leaks across tests).
    monkeypatch.setattr(compute, "_LAST_PURGE_DATE", None)
    monkeypatch.setattr(compute, "_GEX_SCHEMA_READY", False)
    return calls


def test_collect_gex_snapshots_polls_with_proxy_client(monkeypatch):
    calls = _fake_gex_modules(monkeypatch, lock_ok=True)
    n = compute.collect_gex_snapshots()
    assert calls["poll"] is True
    assert calls["client"] is _proxy.schwab_py_client   # shared proxy client
    assert calls["engine"] == "ENGINE"
    assert calls["closed"] is True                       # write conn always closed
    assert calls["touched"] is True                      # lock heartbeat refreshed
    assert n == 3                                         # len(collection_symbols())


def test_collect_gex_snapshots_inits_schema_once_per_process(monkeypatch):
    """init_schema is a per-DB-file property, not per-connection — running its
    executescript + PRAGMA + commit on every 1-min collect is a needless
    write-lock touch. It runs once per process (latched)."""
    calls = _fake_gex_modules(monkeypatch, lock_ok=True)
    inits = {"n": 0}
    import sys as _sys
    _sys.modules["gex_history_db"].init_schema = \
        lambda conn: inits.__setitem__("n", inits["n"] + 1)
    monkeypatch.setattr(compute, "_GEX_SCHEMA_READY", False)
    compute.collect_gex_snapshots()
    compute.collect_gex_snapshots()
    assert inits["n"] == 1


def test_collect_gex_snapshots_defers_when_lock_held(monkeypatch):
    calls = _fake_gex_modules(monkeypatch, lock_ok=False)
    n = compute.collect_gex_snapshots()
    assert calls["poll"] is False    # a fresh foreign collector owns the lock
    assert n == 0
    assert "purged" not in calls     # deferred → no purge either


def test_collect_gex_snapshots_purges_once_per_day(monkeypatch):
    """Retention runs on the live path but at most once per local date (not on
    every 2-min collect tick)."""
    calls = _fake_gex_modules(monkeypatch, lock_ok=True)
    compute.collect_gex_snapshots()
    assert calls.get("purged") == 1
    assert calls.get("keep_sessions") == compute.GEX_KEEP_SESSIONS
    # A second collect the SAME day must NOT purge again (gated).
    compute.collect_gex_snapshots()
    assert calls.get("purged") == 1


def test_collect_gex_snapshots_purge_failure_is_swallowed(monkeypatch):
    """A retention failure must never abort the collection round."""
    calls = _fake_gex_modules(monkeypatch, lock_ok=True)
    import sys as _sys

    def _boom(conn, keep_sessions=5):
        raise RuntimeError("db locked")
    _sys.modules["gex_history_db"].purge_keep_sessions = _boom
    n = compute.collect_gex_snapshots()
    assert calls["poll"] is True   # collection still happened
    assert n == 3


# ── GEX collector status view ───────────────────────────────────────────────
# gex_status_view reuses options-scanner's gex_status.classify_collector_status
# over the latest $SPX/gex snapshot age (read-only). We fake the lazily-imported
# gex_status + gex_history_db modules so nothing touches the on-disk DB.

def _fake_status_modules(monkeypatch, *, age=120, last_ts=1781530800,
                         label="OK", color="green"):
    import sys as _sys
    import types as _types

    fake_status = _types.SimpleNamespace(
        classify_collector_status=lambda a, now, has, lt: (label, color))
    fake_gh = _types.SimpleNamespace(
        connect=lambda read_only=False: object(),
        last_snapshot_age=lambda conn, symbol, view: (age, last_ts))
    monkeypatch.setitem(_sys.modules, "gex_status", fake_status)
    monkeypatch.setitem(_sys.modules, "gex_history_db", fake_gh)


def test_gex_status_view_in_window(monkeypatch):
    import datetime as _dt
    from zoneinfo import ZoneInfo

    _fake_status_modules(monkeypatch, age=120, last_ts=1781530800,
                         label="OK", color="green")
    # A weekday inside the 08:30–15:20 CT window.
    now = _dt.datetime(2026, 6, 15, 10, 2, tzinfo=ZoneInfo("America/Chicago"))

    out = compute.gex_status_view(now=now)
    assert set(out) == {"status_label", "status_color", "last_scan",
                        "next_scan", "age_seconds"}
    assert out["status_label"] == "OK"
    assert out["status_color"] == "green"
    assert out["age_seconds"] == 120
    # last_ts=1781530800 is 8:40 AM CT (formatted via _fmt_clock).
    assert out["last_scan"] == "8:40 AM"
    # Next 1-min boundary strictly after 10:02 within the window → 10:03.
    assert out["next_scan"] == "10:03 AM"


def test_gex_status_view_after_window_no_next(monkeypatch):
    import datetime as _dt
    from zoneinfo import ZoneInfo

    _fake_status_modules(monkeypatch, label="idle", color="gray")
    # After 15:20 CT → no next scan.
    now = _dt.datetime(2026, 6, 15, 15, 30, tzinfo=ZoneInfo("America/Chicago"))

    out = compute.gex_status_view(now=now)
    assert out["next_scan"] is None


def test_gex_next_scan_boundaries():
    import datetime as _dt
    from zoneinfo import ZoneInfo

    CT = ZoneInfo("America/Chicago")

    def ct(h, m):
        return _dt.datetime(2026, 6, 15, h, m, tzinfo=CT)

    # Before the window → the window-start slot (08:00) that day.
    before = compute._gex_next_scan(ct(7, 0))
    assert before is not None
    assert (before.hour, before.minute) == (8, 0)

    # Inside the window → next 1-min boundary strictly after now.
    inside = compute._gex_next_scan(ct(10, 2))
    assert inside is not None
    assert (inside.hour, inside.minute) == (10, 3)

    # Exactly at the stop boundary (15:20) → None.
    assert compute._gex_next_scan(ct(15, 20)) is None

    # Just before stop where the next boundary would be >= stop → None.
    assert compute._gex_next_scan(ct(15, 19)) is None


# ── flow_skew_view (per-index skew level + change since prior snapshot) ───────
# Reads gex_history_db.latest_skew_by_symbol (2 most-recent rows) per index
# symbol. We fake the lazily-imported gex_history_db so nothing touches the DB.

def _fake_skew_db(monkeypatch, rows_by_symbol):
    import sys as _sys
    import types as _types

    def _latest(conn, symbol, view="gex"):
        return list(rows_by_symbol.get(symbol, []))

    fake_gh = _types.SimpleNamespace(
        connect=lambda read_only=False: _types.SimpleNamespace(
            close=lambda: None),
        latest_skew_by_symbol=_latest,
    )
    monkeypatch.setitem(_sys.modules, "gex_history_db", fake_gh)


def test_flow_skew_view_builds_per_symbol_with_delta(monkeypatch):
    # $SPX has two snapshots -> rr_delta = latest.rr - prior.rr.
    _fake_skew_db(monkeypatch, {
        "$SPX": [(200, 4.0, 300, 310), (140, 1.5, 200, 210)],
        "SPY": [(200, -2.0, 50, 60)],   # only one snapshot -> rr_delta None
    })
    out = compute.flow_skew_view()
    assert out["$SPX"] == {"rr_25d": 4.0, "rr_delta": 2.5,
                           "call_vol": 300, "put_vol": 310, "ts": 200}
    assert out["SPY"] == {"rr_25d": -2.0, "rr_delta": None,
                          "call_vol": 50, "put_vol": 60, "ts": 200}
    # QQQ had no rows -> absent from the view.
    assert "QQQ" not in out


def test_flow_skew_view_delta_none_when_rr_missing(monkeypatch):
    # A None rr in either the latest or prior row -> rr_delta None.
    _fake_skew_db(monkeypatch, {
        "$SPX": [(200, None, 300, 310), (140, 1.5, 200, 210)],
    })
    out = compute.flow_skew_view()
    assert out["$SPX"]["rr_25d"] is None
    assert out["$SPX"]["rr_delta"] is None


def test_flow_skew_view_defensive_empty_on_failure(monkeypatch):
    import sys as _sys
    import types as _types

    def _boom(read_only=False):
        raise RuntimeError("db locked")

    monkeypatch.setitem(_sys.modules, "gex_history_db",
                        _types.SimpleNamespace(connect=_boom))
    assert compute.flow_skew_view() == {}


def test_gamma_walls_one_each_side_for_gex():
    data = {"spot": 450.0, "gex": {
        440.0: {"call": 10.0,  "put": -900.0, "net": -890.0},  # put wall (below)
        448.0: {"call": 50.0,  "put": -100.0, "net": -50.0},
        452.0: {"call": 700.0, "put": -20.0,  "net": 680.0},   # call wall (above)
        460.0: {"call": 120.0, "put": -5.0,   "net": 115.0},
    }}
    walls = compute.gamma_walls("GEX", data, 450.0)
    assert walls == [440.0, 452.0]            # [put_wall (<spot), call_wall (>=spot)]


def test_gamma_walls_dex_uses_dex_key():
    data = {"spot": 100.0, "dex": {
        95.0:  {"call": 1.0,   "put": -500.0, "net": -499.0},  # put wall
        105.0: {"call": 800.0, "put": -1.0,   "net": 799.0},   # call wall
    }}
    assert compute.gamma_walls("DEX", data, 100.0) == [95.0, 105.0]


def test_gamma_walls_single_side_and_empty():
    # Only strikes above spot -> just the call wall.
    above_only = {"spot": 450.0, "gex": {452.0: {"call": 9.0, "put": -1.0, "net": 8.0}}}
    assert compute.gamma_walls("GEX", above_only, 450.0) == [452.0]
    # Charm/Vanna never get walls; empty data -> [].
    assert compute.gamma_walls("Charm", above_only, 450.0) == []
    assert compute.gamma_walls("GEX", {"spot": 450.0, "gex": {}}, 450.0) == []


# ── Gamma dropdown symbol universe ──────────────────────────────────────────

def test_gamma_symbol_options_excludes_vix_spx_first(monkeypatch):
    import sys as _sys
    import types as _types
    fake_gc = _types.SimpleNamespace(
        collection_symbols=lambda: ["$SPX", "$VIX", "SPY", "QQQ", "NVDA"])
    monkeypatch.setitem(_sys.modules, "gex_collector", fake_gc)
    out = compute.gamma_symbol_options()
    assert out[0] == "$SPX"
    assert "$VIX" not in out
    assert out == ["$SPX", "SPY", "QQQ", "NVDA"]


def test_gamma_symbol_options_defensive(monkeypatch):
    import sys as _sys
    import types as _types
    fake_gc = _types.SimpleNamespace(
        collection_symbols=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setitem(_sys.modules, "gex_collector", fake_gc)
    assert compute.gamma_symbol_options() == ["$SPX", "SPY", "QQQ"]


def test_calc_compute_butterfly_uses_generic_summary():
    import datetime as dt
    exp = (dt.date.today() + dt.timedelta(days=20)).isoformat()
    legs = [
        {"strike": 95, "option_type": "call", "side": "long", "premium": 6.0, "qty": 1, "expiry": exp},
        {"strike": 100, "option_type": "call", "side": "short", "premium": 3.0, "qty": 2, "expiry": exp},
        {"strike": 105, "option_type": "call", "side": "long", "premium": 1.5, "qty": 1, "expiry": exp},
    ]
    out = compute.calc_compute(strategy="CUSTOM", spot=100, iv=0.25, rate=0.04,
                               ivadj=0.0, qty=1, expiry=exp, legs=legs)
    s = out["summary"]
    assert s["max_loss"] > 0 and s["max_profit"] > 0
    assert len(s["breakevens"]) == 2


def test_analyze_tool_has_close_outlook():
    props = (compute._ANALYZE_TOOL["input_schema"]["properties"]["indices"]
             ["items"]["properties"])
    assert "close_outlook" in props and props["close_outlook"]["type"] == "string"


def test_parse_analysis_carries_close_outlook():
    inp = {"regime": "r", "bias": 0, "headline": "h", "narrative": "n", "why": "w",
           "indices": [{"symbol": "$SPX", "close_outlook": "trim into the call wall"}]}
    out = compute._parse_analysis(inp)
    assert out["indices"][0]["close_outlook"] == "trim into the call wall"
    # absent -> empty string (defensive default)
    inp2 = {"headline": "h", "indices": [{"symbol": "SPY"}]}
    assert compute._parse_analysis(inp2)["indices"][0]["close_outlook"] == ""


def test_infographic_renders_close_outlook():
    data = {"regime": "r", "bias": 0, "headline": "h", "narrative": "", "why": "",
            "indices": [{"symbol": "$SPX", "close_outlook": "stay long above the flip"}]}
    html = compute.analyze_infographic_html(data)
    assert "Into the close" in html and "stay long above the flip" in html
    # absent -> no "Into the close" block
    data2 = {"indices": [{"symbol": "SPY"}], "headline": "h"}
    assert "Into the close" not in compute.analyze_infographic_html(data2)


def test_projection_brief_reader_line():
    import datetime as _dt
    from zoneinfo import ZoneInfo
    import gamma_tool as gt
    CT = ZoneInfo("America/Chicago")
    exp = "2026-07-11:0"
    def leg(k, oi):
        return [{"strike": k, "gamma": 0.05, "openInterest": oi, "volatility": 20.0,
                 "delta": 0.5, "daysToExpiration": 0}]
    wide = {float(100 + i): (4000 if i == 0 else 800) for i in range(-8, 9)}
    chain = {"underlyingPrice": 100.0,
             "callExpDateMap": {exp: {f"{k:.1f}": leg(k, oi) for k, oi in wide.items()}},
             "putExpDateMap": {exp: {f"{k:.1f}": leg(k, max(oi - 200, 100)) for k, oi in wide.items()}}}

    class _E(gt.GammaEngine):
        @staticmethod
        def _find_nearest_exp_key(m, today):
            return (next(iter(m)), 0) if m else (None, None)

    now = _dt.datetime(2026, 7, 11, 13, 0, tzinfo=CT)
    brief = compute._projection_brief(_E(), chain, 100.0, now)
    assert "Into the close" in brief and "15:00" in brief
    # after the close -> empty
    assert compute._projection_brief(_E(), chain, 100.0,
                                     _dt.datetime(2026, 7, 11, 15, 30, tzinfo=CT)) == ""


# ── Day-persistent scan union (merge_day_signals) ───────────────────────────

_LISTS = ("signals_0dte", "signals_swing", "signals_directional")


def _sig(sid, credit=1.0):
    return {"id": sid, "symbol": "SPY", "type": "PCS", "credit": credit}


def test_merge_day_signals_seeds_from_empty_prev():
    out = compute.merge_day_signals(None, {"signals_0dte": [_sig("a")]}, "2026-07-16")
    assert out["date"] == "2026-07-16"
    assert [s["id"] for s in out["signals_0dte"]] == ["a"]
    assert out["signals_0dte"][0]["live"] is True


def test_merge_day_signals_keeps_live_signal_fresh():
    """A still-qualifying signal takes the CURRENT scan's numbers, not the old ones."""
    prev = compute.merge_day_signals(None, {"signals_0dte": [_sig("a", credit=1.0)]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": [_sig("a", credit=2.5)]}, "2026-07-16")
    assert len(out["signals_0dte"]) == 1
    assert out["signals_0dte"][0]["credit"] == 2.5   # refreshed
    assert out["signals_0dte"][0]["live"] is True


def test_merge_day_signals_freezes_dropped_out_signal():
    prev = compute.merge_day_signals(None, {"signals_0dte": [_sig("a", credit=1.0)]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": []}, "2026-07-16")
    assert len(out["signals_0dte"]) == 1
    kept = out["signals_0dte"][0]
    assert kept["id"] == "a"
    assert kept["credit"] == 1.0        # frozen at last-seen
    assert kept["live"] is False
    assert kept["stale_since"]          # stamped


def test_merge_day_signals_accumulates_the_union():
    prev = compute.merge_day_signals(None, {"signals_0dte": [_sig("a")]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": [_sig("b")]}, "2026-07-16")
    assert {s["id"] for s in out["signals_0dte"]} == {"a", "b"}


def test_merge_day_signals_reappearing_signal_goes_live_again():
    """Dropped out, then came back -- it must be live with fresh numbers."""
    e = compute.merge_day_signals(None, {"signals_0dte": [_sig("a", 1.0)]}, "2026-07-16")
    e = compute.merge_day_signals(e, {"signals_0dte": []}, "2026-07-16")
    assert e["signals_0dte"][0]["live"] is False
    e = compute.merge_day_signals(e, {"signals_0dte": [_sig("a", 3.0)]}, "2026-07-16")
    assert e["signals_0dte"][0]["live"] is True
    assert e["signals_0dte"][0]["credit"] == 3.0
    assert e["signals_0dte"][0]["stale_since"] is None


def test_merge_day_signals_resets_on_date_roll():
    prev = compute.merge_day_signals(None, {"signals_0dte": [_sig("a")]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": [_sig("b")]}, "2026-07-17")
    assert out["date"] == "2026-07-17"
    assert {s["id"] for s in out["signals_0dte"]} == {"b"}   # yesterday dropped


def test_merge_day_signals_covers_all_three_lists():
    cur = {k: [_sig(f"{k}-1")] for k in _LISTS}
    out = compute.merge_day_signals(None, cur, "2026-07-16")
    for k in _LISTS:
        assert len(out[k]) == 1


def test_merge_day_signals_tolerates_malformed_prev():
    """A corrupt/foreign envelope must degrade to a fresh day, not raise."""
    # NOTE: a non-iterable prev list (5) is the case that makes the isinstance
    # guard load-bearing -- with a string ("nope") the per-signal isinstance
    # check absorbs it anyway, so a string alone cannot pin the guard.
    for bad in ({}, {"date": "2026-07-16"}, {"date": "2026-07-16", "signals_0dte": "nope"},
                {"date": "2026-07-16", "signals_0dte": 5},
                {"date": "2026-07-16", "signals_0dte": {"a": 1}},
                {"signals_0dte": [{"no_id": 1}]}):
        out = compute.merge_day_signals(bad, {"signals_0dte": [_sig("a")]}, "2026-07-16")
        assert [s["id"] for s in out["signals_0dte"]] == ["a"]


def test_merge_day_signals_skips_signals_without_an_id():
    out = compute.merge_day_signals(None, {"signals_0dte": [{"symbol": "SPY"}]}, "2026-07-16")
    assert out["signals_0dte"] == []


def test_merge_day_signals_does_not_mutate_inputs():
    cur = {"signals_0dte": [_sig("a")]}
    compute.merge_day_signals(None, cur, "2026-07-16")
    assert "live" not in cur["signals_0dte"][0]


def test_merge_day_signals_does_not_stamp_first_seen():
    """first_seen was dead (unread), untested and LIED on cold start (a service
    restarted at noon would stamp a 9am signal 'first_seen=12:00'). If a consumer
    ever needs it, it gets added honestly -- omitted, not fabricated, on cold start."""
    out = compute.merge_day_signals(None, {"signals_0dte": [_sig("a")]}, "2026-07-16")
    assert "first_seen" not in out["signals_0dte"][0]


def test_merge_day_signals_strips_dead_wall_fields_from_day_entries():
    """gex_walls/dex_walls have ZERO consumers outside options-scanner's own
    intra-scan scoring, and the day union multiplies them ~10-30x."""
    sig = dict(_sig("a"), gex_walls=[1.0, 2.0], dex_walls=[3.0, 4.0])
    out = compute.merge_day_signals(None, {"signals_0dte": [sig]}, "2026-07-16")
    kept = out["signals_0dte"][0]
    assert "gex_walls" not in kept
    assert "dex_walls" not in kept
    assert kept["credit"] == 1.0          # the rest of the signal survives


def test_merge_day_signals_strip_survives_carry_forward():
    """A carried-forward (frozen) entry must not resurrect the stripped fields."""
    sig = dict(_sig("a"), gex_walls=[1.0], dex_walls=[2.0])
    prev = compute.merge_day_signals(None, {"signals_0dte": [sig]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": []}, "2026-07-16")
    assert "gex_walls" not in out["signals_0dte"][0]
    assert out["signals_0dte"][0]["live"] is False


def test_merge_day_signals_caps_the_list_evicting_oldest_stale_first():
    prev = compute.merge_day_signals(
        None, {"signals_0dte": [_sig("a"), _sig("b"), _sig("c")]}, "2026-07-16")
    # all three drop out -> all stale; cap 2 -> the OLDEST ("a") is evicted.
    out = compute.merge_day_signals(prev, {"signals_0dte": []}, "2026-07-16",
                                    max_per_list=2)
    assert [s["id"] for s in out["signals_0dte"]] == ["b", "c"]


def test_merge_day_signals_cap_never_evicts_a_live_signal():
    """The cap must never break the feature's core promise.

    "a" is the OLDEST entry but is still live, so a live-blind eviction would
    drop it first. (An earlier version of this test put the stale entries at the
    front, where oldest-first and live-blind evict the same thing -- it passed
    under a mutation that evicted live signals.)
    """
    prev = compute.merge_day_signals(
        None, {"signals_0dte": [_sig("a"), _sig("b"), _sig("c")]}, "2026-07-16")
    out = compute.merge_day_signals(
        prev, {"signals_0dte": [_sig("a")]}, "2026-07-16", max_per_list=2)
    # a=live(oldest), b+c=stale, cap 2 -> evict the oldest STALE (b), keep a.
    assert [s["id"] for s in out["signals_0dte"]] == ["a", "c"]
    assert out["signals_0dte"][0]["live"] is True


def test_merge_day_signals_cap_is_exceeded_rather_than_evict_live():
    """When live alone exceeds the cap, the cap YIELDS -- live is never dropped."""
    out = compute.merge_day_signals(
        None, {"signals_0dte": [_sig("x"), _sig("y"), _sig("z")]}, "2026-07-16",
        max_per_list=1)
    assert [s["id"] for s in out["signals_0dte"]] == ["x", "y", "z"]


def test_merge_day_signals_cap_logs_what_it_dropped(caplog):
    """No silent caps -- silent truncation reads as 'covered everything' when it didn't."""
    prev = compute.merge_day_signals(
        None, {"signals_0dte": [_sig("a"), _sig("b"), _sig("c")]}, "2026-07-16")
    with caplog.at_level("WARNING"):
        compute.merge_day_signals(prev, {"signals_0dte": []}, "2026-07-16",
                                  max_per_list=2)
    assert any(r.levelname == "WARNING" and "signals_0dte" in r.getMessage()
               for r in caplog.records), "eviction must be logged"


def test_merge_day_signals_cap_does_not_fire_below_the_limit(caplog):
    prev = compute.merge_day_signals(None, {"signals_0dte": [_sig("a")]}, "2026-07-16")
    with caplog.at_level("WARNING"):
        out = compute.merge_day_signals(prev, {"signals_0dte": []}, "2026-07-16",
                                        max_per_list=2)
    assert len(out["signals_0dte"]) == 1
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_day_cap_default_exceeds_the_live_ceiling_with_headroom():
    """The cap must never fight the feature. Measured live ceiling per list is
    ~360 (45 watchlist symbols x 8 per symbol per list per scan)."""
    assert compute._DAY_MAX_PER_LIST >= 360 * 2
    # ...and it must actually BOUND the payload, or it is not a backstop at all.
    # 3 lists x cap x ~800 B measured per entry must stay well under the ~16 MB
    # that forced the documented cache:options:gamma crop.
    assert 3 * compute._DAY_MAX_PER_LIST * 800 < 6_000_000
    # ...and it must actually be the default (an unbounded default = no backstop).
    prev = compute.merge_day_signals(
        None, {"signals_0dte": [_sig("a"), _sig("b")]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": []}, "2026-07-16")
    assert len(out["signals_0dte"]) == 2      # default cap does not bind here


def test_merge_day_signals_reports_truncation_per_list():
    """A server-side log the user never sees IS a silent cap. The envelope must
    carry the drop counts so the page can say the day is incomplete."""
    prev = compute.merge_day_signals(
        None, {"signals_0dte": [_sig("a"), _sig("b"), _sig("c")],
               "signals_swing": [_sig("s1"), _sig("s2")]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": [], "signals_swing": []},
                                    "2026-07-16", max_per_list=1)
    # 3 stale in 0dte -> 2 dropped; 2 stale in swing -> 1 dropped.
    assert out["truncated"] == {"signals_0dte": 2, "signals_swing": 1}


def test_merge_day_signals_omits_truncated_when_nothing_dropped():
    """A flag that is always on is as useless as one that is always off."""
    prev = compute.merge_day_signals(None, {"signals_0dte": [_sig("a")]}, "2026-07-16")
    out = compute.merge_day_signals(prev, {"signals_0dte": [_sig("a")]}, "2026-07-16",
                                    max_per_list=50)
    assert "truncated" not in out


def test_merge_day_signals_truncated_names_only_the_lists_that_dropped():
    prev = compute.merge_day_signals(
        None, {"signals_0dte": [_sig("a"), _sig("b")],
               "signals_swing": [_sig("s1")]}, "2026-07-16")
    out = compute.merge_day_signals(
        prev, {"signals_0dte": [], "signals_swing": [_sig("s1")]}, "2026-07-16",
        max_per_list=1)
    assert out["truncated"] == {"signals_0dte": 1}      # swing untouched -> absent


def test_merge_day_signals_truncated_counts_stale_dropped_under_live_overflow():
    """Live is never evicted, but any stale dropped alongside it still counts."""
    prev = compute.merge_day_signals(
        None, {"signals_0dte": [_sig("old"), _sig("x"), _sig("y")]}, "2026-07-16")
    out = compute.merge_day_signals(
        prev, {"signals_0dte": [_sig("x"), _sig("y")]}, "2026-07-16", max_per_list=1)
    # x,y live (kept, over cap); "old" stale -> evicted and reported.
    assert [s["id"] for s in out["signals_0dte"]] == ["x", "y"]
    assert out["truncated"] == {"signals_0dte": 1}


# --- build_matrix orchestration + count helpers (Task 3) ---------------------

def test_count_helpers_group_by_symbol():
    from services.options_svc import compute
    sc = compute._count_scan_signals({"date": "2026-07-20",
            "signals_0dte": [{"id": 1, "symbol": "SPY"}, {"id": 2, "symbol": "SPY"}],
            "signals_swing": [{"id": 3, "symbol": "QQQ"}], "signals_directional": []},
            today="2026-07-20")
    assert sc == {"SPY": 2, "QQQ": 1}
    # Flow-alert counts come from the uncapped cooldown SEEN-MAP: each cid is one
    # distinct event; the prefix before the first '|' is the symbol.
    al = compute._count_flow_alerts({"date": "2026-07-20", "map": {
            "SPY|crossover": 1, "SPY|uoa|call|450|2026-07-20": 1,
            "QQQ|uoa|put|400|2026-07-20": 1}},
            today="2026-07-20")
    assert al == {"SPY": 2, "QQQ": 1}
    # a $-prefixed index symbol splits correctly, and a malformed key is skipped.
    al2 = compute._count_flow_alerts({"date": "2026-07-20", "map": {
            "$SPX|crossover": 1, "$SPX|uoa|call|7500|2026-07-20": 1, "junk": 1}},
            today="2026-07-20")
    assert al2 == {"$SPX": 2}


def test_count_scan_signals_gates_on_date():
    from services.options_svc import compute
    counts = compute._count_scan_signals({"date": "1999-01-01",
                 "signals_0dte": [{"id": "a", "symbol": "SPY"}], "signals_swing": [],
                 "signals_directional": []}, today="2026-07-20")
    assert counts == {}


def test_count_flow_alerts_gates_on_date():
    from services.options_svc import compute
    assert compute._count_flow_alerts({"date": "1999-01-01",
                 "map": {"SPY|crossover": 1}}, today="2026-07-20") == {}


class _RecConn:
    """A recording fake connection so the ``finally: conn.close()`` is actually
    exercised (not swallowed as an AttributeError on a bare object())."""
    def __init__(self): self.closed = False
    def close(self): self.closed = True


def _fake_gh(flow_series=None, flip=100.0, conn=None):
    series = flow_series if flow_series is not None else [
        (0, 100.0, 10, 5, 1_000_000.0, 400_000.0),
        (900, 100.7, 30, 8, 3_000_000.0, 800_000.0)]

    class FakeGH:
        @staticmethod
        def connect(read_only=False): return conn if conn is not None else object()
        @staticmethod
        def load_flow_series(c, symbol, d=None): return series
        @staticmethod
        def latest_flip(c, symbol, view="gex", date=None): return flip
    return FakeGH


def test_build_matrix_assembles_rows(monkeypatch):
    from services.options_svc import compute
    conn = _RecConn()
    monkeypatch.setattr(compute, "_matrix_symbols", lambda: ["SPY"])
    monkeypatch.setattr(compute, "_matrix_gh", lambda: _fake_gh(conn=conn))
    out = compute.build_matrix(
        scan_day={"date": "2026-07-20", "signals_0dte": [{"id": "a", "symbol": "SPY"}],
                  "signals_swing": [], "signals_directional": []},
        flow_cooldowns={"date": "2026-07-20", "map": {"SPY|crossover": 1}},
        today="2026-07-20", session_date="2026-07-20", now_ts=900)
    assert out["error"] is None
    assert len(out["rows"]) == 1
    r = out["rows"][0]
    assert r["symbol"] == "SPY" and r["n_signals"] == 1 and r["n_alerts"] == 1
    assert r["gex_regime"] == "above"
    assert conn.closed is True   # the finally-close ran


def test_build_matrix_counts_gate_on_session_date(monkeypatch):
    # Off-hours: today != session_date, and the persisted scan_day/flow_alerts are
    # dated to the DISPLAYED session — the counts must gate on session_date so they
    # still show (not zero out). The payload's top-level ``date`` stays == today.
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_matrix_symbols", lambda: ["SPY"])
    monkeypatch.setattr(compute, "_matrix_gh", lambda: _fake_gh())
    out = compute.build_matrix(
        scan_day={"date": "2026-07-19", "signals_0dte": [{"id": "a", "symbol": "SPY"}],
                  "signals_swing": [], "signals_directional": []},
        flow_cooldowns={"date": "2026-07-19", "map": {"SPY|crossover": 1}},
        today="2026-07-20", session_date="2026-07-19", now_ts=900)
    assert out["date"] == "2026-07-20"
    r = out["rows"][0]
    assert r["n_signals"] == 1 and r["n_alerts"] == 1


def test_build_matrix_counts_work_with_date_object_session_date(monkeypatch):
    import datetime
    from services.options_svc import compute
    seen = {}
    class FakeGH:
        @staticmethod
        def connect(read_only=False):
            class C:
                def close(self): pass
            return C()
        @staticmethod
        def load_flow_series(conn, symbol, d=None):
            seen["series_date_type"] = type(d).__name__
            assert hasattr(d, "year")   # DB reader needs a date object, not a string
            return [(0, 100.0, 10, 5, 1_000_000.0, 400_000.0),
                    (900, 100.7, 30, 8, 3_000_000.0, 800_000.0)]
        @staticmethod
        def latest_flip(conn, symbol, view="gex", date=None):
            assert hasattr(date, "year")
            return 100.0
    monkeypatch.setattr(compute, "_matrix_symbols", lambda: ["SPY"])
    monkeypatch.setattr(compute, "_matrix_gh", lambda: FakeGH)
    out = compute.build_matrix(
        scan_day={"date": "2026-07-20", "signals_0dte": [{"id": "a", "symbol": "SPY"}],
                  "signals_swing": [], "signals_directional": []},
        flow_cooldowns={"date": "2026-07-20", "map": {"SPY|crossover": 1}},
        today="2026-07-20",
        session_date=datetime.date(2026, 7, 20),   # the REAL active_session_date() return type
        now_ts=900)
    r = out["rows"][0]
    assert r["n_signals"] == 1 and r["n_alerts"] == 1        # counts work despite date-object input
    assert out["session_date"] == "2026-07-20"               # payload field normalized to string
    assert isinstance(out["session_date"], str)
    assert seen["series_date_type"] == "date"                # DB read still got the date object


def test_build_matrix_one_bad_symbol_cannot_sink_build(monkeypatch):
    from services.options_svc import compute

    class FakeGH:
        @staticmethod
        def connect(read_only=False): return object()
        @staticmethod
        def load_flow_series(c, symbol, d=None):
            if symbol == "BAD":
                raise RuntimeError("bad read")
            return [(0, 100.0, 10, 5, 1_000_000.0, 400_000.0),
                    (900, 100.7, 30, 8, 3_000_000.0, 800_000.0)]
        @staticmethod
        def latest_flip(c, symbol, view="gex", date=None): return 100.0
    monkeypatch.setattr(compute, "_matrix_symbols", lambda: ["SPY", "BAD"])
    monkeypatch.setattr(compute, "_matrix_gh", lambda: FakeGH)
    out = compute.build_matrix(scan_day={}, flow_cooldowns={}, today="2026-07-20",
                               session_date="2026-07-20", now_ts=900)
    assert out["error"] is None
    by_sym = {r["symbol"]: r for r in out["rows"]}
    assert set(by_sym) == {"SPY", "BAD"}
    assert by_sym["SPY"]["spot"] is not None            # real values
    assert by_sym["BAD"]["spot"] is None                # degraded row
    assert by_sym["BAD"]["signal"] == "neutral"


def test_build_matrix_degrades_when_db_unavailable(monkeypatch):
    from services.options_svc import compute
    def boom():
        raise RuntimeError("no db")
    monkeypatch.setattr(compute, "_matrix_symbols", lambda: ["SPY"])
    monkeypatch.setattr(compute, "_matrix_gh", boom)
    out = compute.build_matrix(scan_day={}, flow_cooldowns={}, today="2026-07-20",
                               session_date="2026-07-20", now_ts=0)
    assert out["rows"] == []
    assert out["error"]


# --- apply_live_spots overlay (Task 5) ---------------------------------------

def test_apply_live_spots_recomputes_daypct_from_open_spot():
    from services.options_svc import compute
    # open_spot 100.0, live last 101.0 → day% = +1.0 (vs session open), NOT netPercentChange
    view = {"rows": [{"symbol": "SPY", "spot": 100.5, "day_pct": 0.5, "_open_spot": 100.0,
                      "flip": 100.0, "gex_regime": "above", "signal": "buy", "n_signals": 2}]}
    quotes = {"SPY": {"quote": {"lastPrice": 101.0, "netPercentChange": 9.9}}}  # netPct ignored now
    out = compute.apply_live_spots(view, quotes)
    r = out["rows"][0]
    assert r["spot"] == 101.0
    assert r["day_pct"] == 1.0                 # (101-100)/100*100, NOT 9.9
    assert r["gex_regime"] == "above"          # live 101.0 >= flip 100.0
    assert r["signal"] == "buy" and r["n_signals"] == 2   # untouched fields preserved


def test_apply_live_spots_recomputes_regime_on_flip_cross():
    from services.options_svc import compute
    view = {"rows": [{"symbol": "SPY", "spot": 101.0, "day_pct": 1.0, "_open_spot": 100.0,
                      "flip": 100.0, "gex_regime": "above"}]}
    quotes = {"SPY": {"quote": {"lastPrice": 99.0}}}   # live spot drops below flip
    out = compute.apply_live_spots(view, quotes)
    assert out["rows"][0]["gex_regime"] == "below"     # recomputed from live spot vs flip


def test_apply_live_spots_missing_open_spot_keeps_daypct():
    from services.options_svc import compute
    view = {"rows": [{"symbol": "SPY", "spot": 100.0, "day_pct": 0.5}]}   # no _open_spot
    out = compute.apply_live_spots(view, {"SPY": {"quote": {"lastPrice": 101.0}}})
    assert out["rows"][0]["spot"] == 101.0
    assert out["rows"][0]["day_pct"] == 0.5    # unchanged (no baseline to recompute from)


def test_apply_live_spots_ignores_missing_symbol():
    from services.options_svc import compute
    view = {"rows": [{"symbol": "SPY", "spot": 100.0, "day_pct": 0.5, "_open_spot": 100.0}]}
    out = compute.apply_live_spots(view, {})
    assert out["rows"][0]["spot"] == 100.0 and out["rows"][0]["day_pct"] == 0.5


def test_apply_live_spots_missing_price_keeps_existing():
    """A quote present but with no lastPrice/pct must NOT null existing values."""
    from services.options_svc import compute
    view = {"rows": [{"symbol": "SPY", "spot": 100.0, "day_pct": 0.5, "_open_spot": 100.0}]}
    out = compute.apply_live_spots(view, {"SPY": {"quote": {}}})
    assert out["rows"][0]["spot"] == 100.0
    assert out["rows"][0]["day_pct"] == 0.5


def test_apply_live_spots_defensive_on_junk():
    from services.options_svc import compute
    assert compute.apply_live_spots(None, {}) is None
    assert compute.apply_live_spots({}, None) == {}


def test_matrix_quotes_batched_fetch(monkeypatch):
    from services.options_svc import compute

    seen = {}

    class _Resp:
        def json(self):
            return {"SPY": {"quote": {"lastPrice": 101.0}}}

    class _Client:
        def get_quotes(self, syms):
            seen["syms"] = syms
            return _Resp()

    monkeypatch.setattr(compute._proxy, "schwab_py_client", _Client())
    out = compute.matrix_quotes(["SPY", "QQQ"])
    assert out == {"SPY": {"quote": {"lastPrice": 101.0}}}
    assert seen["syms"] == ["SPY", "QQQ"]


def test_matrix_quotes_degrades_to_empty(monkeypatch):
    from services.options_svc import compute

    class _Client:
        def get_quotes(self, syms):
            raise RuntimeError("proxy down")

    monkeypatch.setattr(compute._proxy, "schwab_py_client", _Client())
    assert compute.matrix_quotes(["SPY"]) == {}


def test_notable_movers_prefers_dashboard_pct_and_sorts_by_magnitude():
    from services.options_svc import compute
    dashboard = {"categories": [
        {"category": "Top 10", "tiles": [
            {"display": "NVDA", "last": 100.0, "change_pct": -4.0, "category": "Top 10"},
            {"display": "AAPL", "last": 200.0, "change_pct": 1.0, "category": "Top 10"},
        ]},
        {"category": "Internals", "tiles": [
            {"display": "ADVN-DECN", "last": -465, "change_pct": 0, "value_only": True,
             "category": "Internals"},
        ]},
    ]}
    matrix = {"rows": [
        {"symbol": "NVDA", "spot": 100.0, "day_pct": -3.1, "n_alerts": 2},
        {"symbol": "MU", "spot": 50.0, "day_pct": 6.5, "n_alerts": 0},
    ]}
    alerts = {"alerts": [{"symbol": "NVDA", "type": "uoa", "side": "put"}]}
    out = compute._notable_movers(dashboard, matrix, alerts, limit=3)
    syms = [m["symbol"] for m in out]
    assert syms[0] == "MU"                    # |6.5| is the biggest move
    assert "NVDA" in syms
    assert "ADVN-DECN" not in syms            # value_only tile skipped
    nvda = next(m for m in out if m["symbol"] == "NVDA")
    assert nvda["day_pct"] == -4.0            # dashboard pct WINS over matrix day_pct
    assert nvda["basis"] == "prior_close"
    assert nvda["flow_alert_count"] == 1      # cross-referenced
    mu = next(m for m in out if m["symbol"] == "MU")
    assert mu["basis"] == "session"           # matrix-only → intraday basis


def test_notable_movers_defensive_on_garbage():
    from services.options_svc import compute
    assert compute._notable_movers(None, None, None) == []
    assert compute._notable_movers({"categories": "nope"}, {"rows": None}, {}) == []
    # a row with no usable pct is dropped, not raised on
    assert compute._notable_movers({}, {"rows": [{"symbol": "X", "day_pct": None}]}, {}) == []


def test_notable_movers_skips_macro_categories_even_with_a_real_pct():
    """A Cash Index tile ($SPX) has a legit change_pct but is not an individual
    stock — it must be excluded even though it isn't value_only. Uses the REAL
    market_svc category strings (verified against symbols.py CATEGORY_ORDER),
    not the design doc's placeholder names."""
    from services.options_svc import compute
    dashboard = {"categories": [
        {"category": "Cash Index", "tiles": [
            {"display": "$SPX", "last": 5000.0, "change_pct": -2.5, "category": "Cash Index"},
        ]},
        {"category": "Broad-Market ETF", "tiles": [
            {"display": "SPY", "last": 500.0, "change_pct": -2.4, "category": "Broad-Market ETF"},
        ]},
        {"category": "Top 10", "tiles": [
            {"display": "TSLA", "last": 300.0, "change_pct": 3.3, "category": "Top 10"},
        ]},
    ]}
    out = compute._notable_movers(dashboard, {}, {})
    syms = {m["symbol"] for m in out}
    assert syms == {"TSLA"}


def test_notable_movers_skips_basket_composite_tile():
    """The BIG10 composite (kind='basket', flagged basket=True by market_svc's
    build_dashboard) is an aggregate, not one stock's move — must be excluded
    even though it lives in the "Top 10" category alongside its members."""
    from services.options_svc import compute
    dashboard = {"categories": [
        {"category": "Top 10", "tiles": [
            {"display": "BIG10", "change_pct": -1.2, "basket": True, "category": "Top 10"},
            {"display": "AMD", "last": 80.0, "change_pct": -1.9, "category": "Top 10"},
        ]},
    ]}
    out = compute._notable_movers(dashboard, {}, {})
    syms = {m["symbol"] for m in out}
    assert syms == {"AMD"}


def test_movers_prompt_block_labels_basis_and_flow_alerts():
    from services.options_svc import compute
    movers = [
        {"symbol": "MU", "day_pct": 6.5, "basis": "session", "flow_alert_count": 0},
        {"symbol": "NVDA", "day_pct": -4.0, "basis": "prior_close", "flow_alert_count": 2},
    ]
    text = compute._movers_prompt_block(movers)
    assert "MU +6.50% (since the open)" in text
    assert "NVDA -4.00% (vs prior close)" in text
    assert "2 unusual-flow alert(s)" in text
    assert compute._movers_prompt_block([]) == ""
    assert compute._movers_prompt_block(None) == ""


# --- code-quality review follow-ups ------------------------------------


def test_notable_movers_matrix_indices_excluded_via_dashboard_categories():
    """CRITICAL fix: $SPX/SPY are in the options-matrix collection universe
    (gex_collector's index base) but have no category of their own there — the
    dashboard's OWN classification of them (Cash Index / Broad-Market ETF) must
    also filter the matrix fallback path, or an index posting a big move gets
    mislabeled as an "individual stock move" in the Claude prompt."""
    from services.options_svc import compute
    dashboard = {"categories": [
        {"category": "Cash Index", "tiles": [
            {"display": "$SPX", "change_pct": -2.5},
        ]},
        {"category": "Broad-Market ETF", "tiles": [
            {"display": "SPY", "change_pct": -2.4},
        ]},
        {"category": "Top 10", "tiles": [
            {"display": "AMD", "change_pct": -1.9},
        ]},
    ]}
    matrix = {"rows": [
        {"symbol": "$SPX", "spot": 5000.0, "day_pct": -9.9},   # would rank #1 by |move|
        {"symbol": "SPY", "spot": 500.0, "day_pct": -9.8},     # would rank #2
        {"symbol": "MU", "spot": 50.0, "day_pct": 6.5},
    ]}
    out = compute._notable_movers(dashboard, matrix, {})
    syms = {m["symbol"] for m in out}
    assert syms == {"AMD", "MU"}
    assert "$SPX" not in syms and "SPX" not in syms
    assert "SPY" not in syms


def test_notable_movers_matrix_indices_excluded_by_floor_when_dashboard_empty():
    """Same CRITICAL fix, backstop case: with the dashboard unavailable (proxy
    down → {} or None), the hardcoded _MOVER_INDEX_FLOOR must still keep
    $SPX/$VIX/SPY/QQQ out of the matrix-only fallback."""
    from services.options_svc import compute
    matrix = {"rows": [
        {"symbol": "$SPX", "spot": 5000.0, "day_pct": -9.9},
        {"symbol": "$VIX", "spot": 20.0, "day_pct": 9.9},
        {"symbol": "SPY", "spot": 500.0, "day_pct": -9.8},
        {"symbol": "QQQ", "spot": 400.0, "day_pct": -9.7},
        {"symbol": "MU", "spot": 50.0, "day_pct": 6.5},
    ]}
    assert {m["symbol"] for m in compute._notable_movers({}, matrix, {})} == {"MU"}
    assert {m["symbol"] for m in compute._notable_movers(None, matrix, {})} == {"MU"}


def test_notable_movers_new_dashboard_category_excluded_by_default():
    """The allow-list must fail CLOSED: a category market_svc has never used
    before is excluded automatically, with no skip-list to update."""
    from services.options_svc import compute
    dashboard = {"categories": [
        {"category": "Some Brand New Category", "tiles": [
            {"display": "ZZZ", "change_pct": 9.9},
        ]},
    ]}
    assert compute._notable_movers(dashboard, {}, {}) == []


def test_notable_movers_alerts_loop_tolerates_non_dict_items():
    """A non-dict element in the (untrusted) alerts list must not blow up the
    whole computation via the outer except (this loop runs before `out` is
    populated, so a crash here used to discard an otherwise-valid result)."""
    from services.options_svc import compute
    matrix = {"rows": [{"symbol": "MU", "spot": 50.0, "day_pct": 6.5}]}
    alerts = {"alerts": ["garbage", None, 42, {"symbol": "MU"}]}
    out = compute._notable_movers({}, matrix, alerts)
    assert [m["symbol"] for m in out] == ["MU"]


def test_notable_movers_prefers_row_n_alerts_over_capped_alerts_list():
    """A matrix row's own n_alerts (uncapped cooldown-map count) must win over
    the capped cache:options:flow_alerts list count for the SAME symbol."""
    from services.options_svc import compute
    matrix = {"rows": [{"symbol": "MU", "spot": 50.0, "day_pct": 6.5, "n_alerts": 5}]}
    alerts = {"alerts": [{"symbol": "MU"}]}   # the capped list undercounts vs n_alerts
    out = compute._notable_movers({}, matrix, alerts)
    assert out[0]["flow_alert_count"] == 5


def test_notable_movers_matrix_zero_n_alerts_is_not_treated_as_missing():
    """n_alerts=0 is a real count, not "absent" — must NOT fall back to the
    (possibly nonzero) capped alerts-list count."""
    from services.options_svc import compute
    matrix = {"rows": [{"symbol": "MU", "spot": 50.0, "day_pct": 6.5, "n_alerts": 0}]}
    alerts = {"alerts": [{"symbol": "MU"}, {"symbol": "MU"}]}
    out = compute._notable_movers({}, matrix, alerts)
    assert out[0]["flow_alert_count"] == 0


def test_notable_movers_alert_symbol_normalization_strips_before_dollar_strip():
    """A ' $MU' alert entry must still dedup against a plain 'MU' matrix row —
    .strip() must run BEFORE .lstrip('$') or the leading space hides the '$'."""
    from services.options_svc import compute
    matrix = {"rows": [{"symbol": "MU", "spot": 50.0, "day_pct": 6.5}]}
    alerts = {"alerts": [{"symbol": " $MU"}]}
    out = compute._notable_movers({}, matrix, alerts)
    assert out[0]["flow_alert_count"] == 1


def test_notable_movers_limit_guard_on_non_int():
    """A non-int limit degrades to the default rather than silently discarding
    already-computed work (e.g. slicing to [:0])."""
    from services.options_svc import compute
    matrix = {"rows": [{"symbol": "MU", "day_pct": 1.0}, {"symbol": "AMD", "day_pct": 2.0}]}
    out = compute._notable_movers({}, matrix, {}, limit="not-a-number")
    assert len(out) == 2


# ── _research_news — live macro drivers via the Claude web-search tool (Task 2) ──

class _FakeBlock:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeNewsClient:
    """Minimal stand-in for anthropic.Anthropic for the news phase."""
    def __init__(self, blocks=None, raise_exc=None):
        self._blocks, self._raise = blocks or [], raise_exc
        self.calls = []
        outer = self

        class _Messages:
            def create(self, **kw):
                outer.calls.append(kw)
                if outer._raise:
                    raise outer._raise
                return _FakeBlock(content=outer._blocks)
        self.messages = _Messages()


def test_research_news_returns_headline_lines():
    from services.options_svc import compute
    client = _FakeNewsClient(blocks=[
        _FakeBlock(type="text", text="- Fed held rates steady\n- CPI came in cool\n"),
    ])
    out = compute._research_news("close", "SPX closed -0.8%", client=client)
    assert out and any("Fed" in line for line in out)
    # The web-search tool must actually be offered to the model.
    assert client.calls and client.calls[0].get("tools")


def test_research_news_degrades_to_empty():
    from services.options_svc import compute
    # No client (no API key) → [] and NO exception. The repo-wide autouse fixture
    # in conftest.py neutralizes `_make_analyze_client` for the whole suite (this
    # dev box has a real shared/anthropic_key.txt — without it, client=None would
    # fall through to a REAL API call).
    assert compute._research_news("close", "ctx", client=None) == []
    # API error → []
    assert compute._research_news(
        "close", "ctx", client=_FakeNewsClient(raise_exc=RuntimeError("boom"))) == []
    # No text blocks → []
    assert compute._research_news("close", "ctx", client=_FakeNewsClient(blocks=[])) == []


def test_research_news_drops_result_when_search_itself_errored():
    """A GENUINE search-infrastructure failure returns HTTP 200 with an error
    block — the model then answers from memory. Returning that text would put
    FABRICATED headlines in a briefing, which is worse than no news. Must yield []."""
    from services.options_svc import compute
    client = _FakeNewsClient(blocks=[
        _FakeBlock(type="web_search_tool_result", tool_use_id="srvtoolu_1",
                   content={"type": "web_search_tool_result_error",
                            "error_code": "too_many_requests"}),
        _FakeBlock(type="text", text="- Stocks probably moved on rate expectations"),
    ])
    assert compute._research_news("close", "ctx", client=client) == []


def test_research_news_keeps_gathered_text_when_only_max_uses_exceeded():
    """`max_uses_exceeded` means WE hit our OWN configured `max_uses` cap — some
    searches in the same call already SUCCEEDED before the cap bit. That is not a
    search-infrastructure failure, so whatever driver text was already gathered
    must be returned, not discarded (unlike a genuine failure — see the sibling
    `..._drops_result_when_search_itself_errored` test above, still `too_many_requests`)."""
    from services.options_svc import compute
    client = _FakeNewsClient(blocks=[
        _FakeBlock(type="web_search_tool_result", tool_use_id="srvtoolu_1",
                   content={"type": "web_search_tool_result_error",
                            "error_code": "max_uses_exceeded"}),
        _FakeBlock(type="text", text="- Fed held rates steady\n- CPI came in cool"),
    ])
    out = compute._research_news("close", "ctx", client=client)
    assert out and any("Fed" in line for line in out)


def test_research_news_includes_todays_date_in_prompt():
    """A live probe caught the model self-contradicting on the date when it was
    left implicit ('results through Friday July 24' while writing 'TODAY'S ...
    Friday July 25') — for an end-of-day retrospective that can misdate the
    session or let stale news pass as today's. The date must be stated explicitly
    and verifiably in the outgoing prompt."""
    import datetime as _dt
    from services.options_svc import compute
    client = _FakeNewsClient(blocks=[_FakeBlock(type="text", text="- CPI cool")])
    fixed_now = _dt.datetime(2026, 7, 24, 15, 5)   # a known Friday

    compute._research_news("close", "ctx", client=client, now=fixed_now)

    prompt = client.calls[0]["messages"][0]["content"]
    assert "Friday, July 24, 2026" in prompt


def test_research_news_offers_tool_with_direct_caller():
    from services.options_svc import compute
    client = _FakeNewsClient(blocks=[_FakeBlock(type="text", text="- CPI cool")])
    compute._research_news("close", "ctx", client=client)
    tool = client.calls[0]["tools"][0]
    assert tool["type"].startswith("web_search_")
    assert tool["name"] == "web_search"
    # allowed_callers defaults to code_execution on v20260209+ — we call directly.
    assert tool["allowed_callers"] == ["direct"]
    assert "betas" not in client.calls[0]        # web search is GA


def test_news_prompt_block_empty_when_no_news():
    from services.options_svc import compute
    assert compute._news_prompt_block([]) == ""
    assert "DRIVERS" in compute._news_prompt_block(["Fed held rates"]).upper()


# ── junk-line filtering — the live probe's real observed failure mode ────────
# The probe returned 6 lines of which 2 were waste: a preamble "Note: ..." line
# and a bare section header ("TODAY'S TAPE DRIVERS — ..."); with
# `_NEWS_MAX_LINES = 6` that left only 4 real drivers, and the junk would have
# gone verbatim into the briefing prompt. The system prompt already banned
# preamble/headers/emphasis and the model ignored it, so filtering must NOT rely
# on the prompt alone.

def test_extract_driver_lines_requires_a_bullet_marker():
    """A plain (non-bulleted) paragraph sentence is not a driver, even if it's
    non-blank — this is what let the preamble/header sentences from the live
    probe through before this fix (the old parser accepted any non-blank line)."""
    from services.options_svc import compute
    text = ("Here is a summary of today's tape.\n"
            "- Fed held rates steady, calming the tape\n")
    out = compute._extract_driver_lines(text)
    assert out == ["Fed held rates steady, calming the tape"]


def test_extract_driver_lines_drops_meta_lines_and_strips_emphasis():
    """Covers each must-fix from the quality review, on lines that ARE bulleted
    (so this proves the SECOND filter layer — not just the bullet requirement —
    is doing real work, mirroring the actual probe output where the junk lines
    were themselves list items)."""
    from services.options_svc import compute
    text = (
        "- Note: results reflect the prior trading session.\n"
        "- TODAY'S TAPE DRIVERS — Friday July 25\n"
        "- **Iran rejects ceasefire** — oil surged 4% on the escalation\n"
        "- Fed: held rates steady, cooling inflation expectations\n"
    )
    out = compute._extract_driver_lines(text)
    # 'Note:' preamble dropped.
    assert not any(line.lower().startswith("note:") for line in out)
    # ALL-CAPS section header dropped.
    assert not any("TODAY'S TAPE DRIVERS" in line for line in out)
    # Markdown emphasis stripped from what's kept.
    assert any("Iran rejects ceasefire" in line for line in out)
    assert not any("**" in line for line in out)
    # A real driver containing a colon is KEPT verbatim (not mistaken for meta).
    assert "Fed: held rates steady, cooling inflation expectations" in out


def test_research_news_end_to_end_filters_junk_before_truncation(monkeypatch):
    """Integration check: with the live probe's actual junk shape reproduced,
    `_research_news` returns only real drivers, and the junk can't crowd out
    substance against `_NEWS_MAX_LINES`."""
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_NEWS_MAX_LINES", 2)
    client = _FakeNewsClient(blocks=[
        _FakeBlock(type="text", text=(
            "- Note: search results cover through the prior session.\n"
            "- TODAY'S TAPE DRIVERS — Friday July 25\n"
            "- **Iran rejects ceasefire** — oil surged 4%\n"
            "- Fed: held rates steady\n"
        )),
    ])
    out = compute._research_news("close", "ctx", client=client)
    assert out == ["Iran rejects ceasefire — oil surged 4%", "Fed: held rates steady"]


# ── Task 3: _eod_session_recap — today's path vs the key levels ──────────────
def test_session_path_from_series():
    from services.options_svc import compute
    series = [(1, 100.0, 0, 0, 0, 0), (2, 104.0, 0, 0, 0, 0),
              (3, 98.0, 0, 0, 0, 0), (4, 101.0, 0, 0, 0, 0)]
    p = compute._session_path(series)
    assert (p["open"], p["high"], p["low"], p["close"]) == (100.0, 104.0, 98.0, 101.0)
    assert p["day_pct"] == 1.0        # 100 -> 101
    assert compute._session_path([]) == {}
    assert compute._session_path(None) == {}


def test_level_verdict_held_vs_broke():
    from services.options_svc import compute
    # Closed above a flip it traded below at some point -> reclaimed.
    assert "reclaim" in compute._level_verdict(
        {"open": 99.0, "high": 105.0, "low": 98.0, "close": 104.0}, 100.0, "gamma flip").lower()
    # Never reached the level -> untested.
    assert "did not" in compute._level_verdict(
        {"open": 90.0, "high": 95.0, "low": 89.0, "close": 94.0}, 120.0, "call wall").lower()
    # Missing level -> empty string, no raise.
    assert compute._level_verdict({"open": 1.0}, None, "flip") == ""


def test_eod_recap_prompt_block_is_defensive():
    from services.options_svc import compute
    # No data at all -> empty string, never raises.
    assert compute._eod_recap_prompt_block({}) == ""
    block = compute._eod_recap_prompt_block({
        "$SPX": {"path": {"open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0,
                          "day_pct": 4.0},
                 "flip": 101.0, "call_wall": 106.0, "put_wall": 98.0},
    })
    assert "$SPX" in block and "104" in block


def test_eod_session_recap_passes_date_as_keyword_to_latest_flip(monkeypatch):
    """Regression: gex_history_db.latest_flip is (conn, symbol, view="gex", date=None).
    Passing the date positionally would silently land in `view` and always return None."""
    import sys, types
    from services.options_svc import compute
    seen = {}

    fake = types.SimpleNamespace(
        connect=lambda read_only=False: types.SimpleNamespace(close=lambda: None),
        load_flow_series=lambda conn, sym, d: [(1, 10.0, 0, 0, 0, 0), (2, 12.0, 0, 0, 0, 0)],
        latest_flip=lambda conn, sym, view="gex", date=None: seen.setdefault(
            "call", {"view": view, "date": date}) and 11.0 or 11.0,
    )
    monkeypatch.setitem(sys.modules, "gex_history_db", fake)
    out = compute._eod_session_recap({"$SPX": {"call_wall": 13.0, "put_wall": 9.0}})
    assert out["$SPX"]["path"]["close"] == 12.0
    assert out["$SPX"]["flip"] == 11.0
    assert seen["call"]["view"] == "gex"        # NOT the date
    assert seen["call"]["date"] is not None


# ── Task 4: submit_eod tool + _parse_eod + eod_briefing ─────────────────────
def test_parse_eod_is_total_over_garbage():
    from services.options_svc import compute
    assert compute._parse_eod(None) is None
    assert compute._parse_eod({}) is None
    d = compute._parse_eod({
        "regime": "Risk-off unwind", "bias": -30, "headline": "Sellers won the day",
        "narrative": "n", "why": "w",
        "macro_drivers": ["Fed held", 5],           # non-str dropped
        "movers": [{"symbol": "MU", "move": "+6.5%", "note": "squeeze"}, "junk"],
        "next_session": {"levels": "watch 5900", "posture": "cautious"},
        "indices": [{"symbol": "$SPX", "recap": "faded from the open"}],
    })
    assert d["regime"] == "Risk-off unwind" and d["bias"] == -30
    assert d["macro_drivers"] == ["Fed held"]
    assert len(d["movers"]) == 1 and d["movers"][0]["symbol"] == "MU"
    assert d["indices"][0]["symbol"] == "$SPX"
    assert d["next_session"]["posture"] == "cautious"


def test_eod_briefing_degrades_without_chains(monkeypatch):
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_gamma_fetch_chain", lambda s: None)
    res = compute.eod_briefing(client=object())
    assert "html" in res and "analysis" not in res       # degraded -> no push
    assert "could not fetch" in res["html"].lower() or "no " in res["html"].lower()


def test_eod_briefing_renders_and_overrides_em(monkeypatch):
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_gamma_fetch_chain",
                        lambda s: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute, "_gamma_blocks_for", lambda s, c: {"sym": s})
    monkeypatch.setattr(compute, "_session_expected_move", lambda c: 4.2)
    monkeypatch.setattr(compute, "_eod_session_recap", lambda lv: {})
    monkeypatch.setattr(compute, "_research_news", lambda *a, **k: ["Fed held rates"])
    monkeypatch.setattr(compute, "_notable_movers", lambda *a, **k: [])

    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                assert kw["tool_choice"]["name"] == "submit_eod"
                blk = type("B", (), {"type": "tool_use", "name": "submit_eod",
                                     "input": {"regime": "r", "bias": 0, "headline": "h",
                                               "narrative": "n", "why": "w",
                                               "indices": [{"symbol": "$SPX",
                                                            "expected_move": 999}]}})()
                return type("R", (), {"content": [blk]})()
    res = compute.eod_briefing(client=_C())
    assert res.get("analysis")
    assert res["analysis"]["indices"][0]["expected_move"] == 4.2   # code-authoritative


def test_eod_briefing_survives_recap_news_and_movers_failure(monkeypatch):
    """Every enrichment source is best-effort: the retrospective must still render."""
    from services.options_svc import compute

    def _boom(*a, **k):
        raise RuntimeError("source down")
    monkeypatch.setattr(compute, "_gamma_fetch_chain",
                        lambda s: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute, "_gamma_blocks_for", lambda s, c: {"sym": s})
    monkeypatch.setattr(compute, "_session_expected_move", lambda c: 1.0)
    monkeypatch.setattr(compute, "_eod_session_recap", _boom)
    monkeypatch.setattr(compute, "_research_news", _boom)
    monkeypatch.setattr(compute, "_notable_movers", _boom)

    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                blk = type("B", (), {"type": "tool_use", "name": "submit_eod",
                                     "input": {"regime": "r", "bias": 0, "headline": "h",
                                               "narrative": "n", "why": "w",
                                               "indices": []}})()
                return type("R", (), {"content": [blk]})()
    assert compute.eod_briefing(client=_C()).get("analysis")


def test_levels_from_blocks_reads_flip_and_walls():
    from services.options_svc import compute
    lv = compute._levels_from_blocks({"gex": {
        "flip_point": 101.0,
        "walls": {"gex": {"call_wall": 106.0, "put_wall": 98.0}},
    }})
    assert lv == {"flip": 101.0, "call_wall": 106.0, "put_wall": 98.0}
    # Defensive: a block shape without gex/walls must not raise.
    assert compute._levels_from_blocks({"sym": "$SPX"}) == {
        "flip": None, "call_wall": None, "put_wall": None}
    assert compute._levels_from_blocks(None)["flip"] is None


# ── Task 5: EOD infographic + shared movers/macro sections ──────────────────
def test_shared_sections_render_and_are_omitted_when_empty():
    from services.options_svc import compute
    assert compute._movers_html([]) == ""
    assert compute._macro_html([]) == ""
    h = compute._movers_html([{"symbol": "MU", "day_pct": 6.5, "basis": "session",
                               "flow_alerts": 2}])
    assert "MU" in h and "6.5" in h
    assert "Fed" in compute._macro_html(["Fed held rates"])


def test_movers_html_escapes_and_colors():
    from services.options_svc import compute
    h = compute._movers_html([{"symbol": "<b>X</b>", "day_pct": -3.0, "basis": "prior_close"}])
    assert "<b>X</b>" not in h and "&lt;b&gt;" in h      # escaped
    assert "-3.0" in h or "-3.00" in h


def test_movers_html_accepts_both_producer_and_model_shapes():
    """_notable_movers emits day_pct/flow_alert_count; the model's submit_* tool
    emits a `move` string. The renderer must handle both without raising."""
    from services.options_svc import compute
    h = compute._movers_html([
        {"symbol": "MU", "day_pct": 6.5, "basis": "session", "flow_alert_count": 3},
        {"symbol": "NVDA", "move": "+2.1%", "note": "earnings beat"},
    ])
    assert "MU" in h and "NVDA" in h and "+2.1%" in h and "earnings beat" in h


def test_eod_infographic_includes_recap_and_next_session():
    from services.options_svc import compute
    html = compute.eod_infographic_html({
        "regime": "Risk-off unwind", "bias": -40, "bias_label": "Bearish",
        "headline": "Sellers controlled the tape", "narrative": "n", "why": "w",
        "macro_drivers": ["Fed held rates"],
        "movers": [{"symbol": "MU", "day_pct": 6.5, "basis": "session"}],
        "indices": [{"symbol": "$SPX", "spot": 100.0, "gamma_flip": 101.0,
                     "recap": "lost the flip and closed below"}],
        "next_session": {"levels": "watch 5900", "posture": "cautious",
                         "catalysts": "CPI 7:30 CT", "expected_move_note": "±35"},
    }, "sub")
    for needle in ("Sellers controlled", "$SPX", "lost the flip", "Fed held rates",
                   "MU", "next session", "CPI"):
        assert needle.lower() in html.lower()


def test_eod_infographic_omits_intraday_playbook_fields():
    """The EOD card must NOT render what_if / close_outlook -- advice for a session
    that has already ended is the exact thing this briefing exists to remove."""
    from services.options_svc import compute
    html = compute.eod_infographic_html({
        "regime": "r", "bias": 0, "headline": "h", "narrative": "n", "why": "w",
        "indices": [{"symbol": "$SPX", "recap": "closed weak",
                     "close_outlook": "BUY DIPS INTO THE CLOSE",
                     "what_if": {"rally": "RIDE IT", "selloff": "s", "chop": "c"}}],
    }, "sub")
    assert "closed weak" in html
    assert "BUY DIPS INTO THE CLOSE" not in html and "RIDE IT" not in html


def test_analyze_infographic_still_renders_without_new_fields():
    from services.options_svc import compute
    html = compute.analyze_infographic_html(
        {"regime": "r", "bias": 0, "headline": "h", "narrative": "n", "why": "w",
         "indices": [{"symbol": "SPY"}]}, "sub")
    assert "SPY" in html      # no regression when macro_drivers/movers are absent


# ── Task 6: enrich the three intraday briefings ─────────────────────────────
def test_analyze_tool_accepts_optional_macro_and_movers():
    from services.options_svc import compute
    props = compute._ANALYZE_TOOL["input_schema"]["properties"]
    assert "macro_drivers" in props and "movers" in props
    # still NOT required -- a model reply without them must parse
    assert "macro_drivers" not in compute._ANALYZE_TOOL["input_schema"]["required"]
    assert "movers" not in compute._ANALYZE_TOOL["input_schema"]["required"]
    d = compute._parse_analysis({"regime": "r", "bias": 0, "headline": "h",
                                 "narrative": "n", "why": "w", "indices": [],
                                 "macro_drivers": ["CPI cool"], "movers": []})
    assert d["macro_drivers"] == ["CPI cool"]


def test_parse_analysis_keeps_intraday_playbook_intact():
    """Regression: the enrichment must not disturb what_if / close_outlook."""
    from services.options_svc import compute
    d = compute._parse_analysis({
        "regime": "r", "bias": 0, "headline": "h", "narrative": "n", "why": "w",
        "indices": [{"symbol": "SPY", "close_outlook": "trim into 600",
                     "what_if": {"rally": "ride", "selloff": "buy dip", "chop": "fade"}}],
    })
    idx = d["indices"][0]
    assert idx["close_outlook"] == "trim into 600"
    assert idx["what_if"] == {"rally": "ride", "selloff": "buy dip", "chop": "fade"}
    assert d["macro_drivers"] == [] and d["movers"] == []


def test_gamma_analyze_threads_news_and_movers_into_prompt(monkeypatch):
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_gamma_fetch_chain",
                        lambda s: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute, "_gamma_blocks_for", lambda s, c: {"sym": s})
    monkeypatch.setattr(compute, "_session_expected_move", lambda c: 1.0)
    monkeypatch.setattr(compute, "_research_news", lambda *a, **k: ["Fed held rates"])
    monkeypatch.setattr(compute, "_notable_movers",
                        lambda *a, **k: [{"symbol": "MU", "day_pct": 6.5,
                                          "basis": "session", "flow_alert_count": 0}])
    seen = {}

    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                seen["prompt"] = kw["messages"][0]["content"]
                blk = type("B", (), {"type": "tool_use", "name": "submit_analysis",
                                     "input": {"regime": "r", "bias": 0, "headline": "h",
                                               "narrative": "n", "why": "w",
                                               "indices": []}})()
                return type("R", (), {"content": [blk]})()
    compute.gamma_analyze(client=_C())
    assert "Fed held rates" in seen["prompt"]
    assert "MU" in seen["prompt"]


def test_gamma_analyze_survives_news_and_movers_failure(monkeypatch):
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_gamma_fetch_chain",
                        lambda s: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute, "_gamma_blocks_for", lambda s, c: {"sym": s})
    monkeypatch.setattr(compute, "_session_expected_move", lambda c: 1.0)

    def _boom(*a, **k):
        raise RuntimeError("news down")
    monkeypatch.setattr(compute, "_research_news", _boom)
    monkeypatch.setattr(compute, "_notable_movers", _boom)

    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                blk = type("B", (), {"type": "tool_use", "name": "submit_analysis",
                                     "input": {"regime": "r", "bias": 0, "headline": "h",
                                               "narrative": "n", "why": "w",
                                               "indices": []}})()
                return type("R", (), {"content": [blk]})()
    res = compute.gamma_analyze(client=_C())
    assert res.get("analysis")      # briefing still renders


def test_eod_tool_requires_next_session():
    """Live-probe regression: with next_session merely optional the model omitted
    it entirely, so the 'prepare for the next session' block -- the whole point of
    an EOD retrospective -- silently rendered as nothing."""
    from services.options_svc import compute
    schema = compute._EOD_TOOL["input_schema"]
    assert "next_session" in schema["required"]
    ns = schema["properties"]["next_session"]
    for key in ("levels", "expected_move_note", "catalysts", "posture"):
        assert key in ns["required"], f"{key} must be required inside next_session"


def test_eod_briefing_logs_on_truncation(monkeypatch, caplog):
    """A max_tokens stop drops trailing tool fields (next_session is emitted last).
    That must be visible in the log, not silent -- it looked exactly like the model
    choosing to omit the field during the live probe."""
    import logging
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_gamma_fetch_chain", lambda s: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute, "_gamma_blocks_for", lambda s, c: {"sym": s})
    monkeypatch.setattr(compute, "_session_expected_move", lambda c: 1.0)
    monkeypatch.setattr(compute, "_eod_session_recap", lambda lv: {})
    monkeypatch.setattr(compute, "_research_news", lambda *a, **k: [])
    monkeypatch.setattr(compute, "_notable_movers", lambda *a, **k: [])

    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                blk = type("B", (), {"type": "tool_use", "name": "submit_eod",
                                     "input": {"regime": "r", "bias": 0, "headline": "h",
                                               "narrative": "n", "why": "w",
                                               "indices": []}})()
                return type("R", (), {"content": [blk], "stop_reason": "max_tokens"})()
    with caplog.at_level(logging.WARNING):
        assert compute.eod_briefing(client=_C()).get("analysis")
    assert any("max_tokens" in r.message for r in caplog.records)


def test_briefing_token_budgets_have_headroom_for_the_enriched_reply():
    """Tripwire. Adding macro_drivers + movers to the tools pushed the intraday
    reply past the old 1500 cap, truncating `indices` to nothing in a live probe.
    Both budgets must keep real headroom over the ~1500-1800 a good run measures.
    Raising a cap costs nothing (billing is on actual output tokens) -- trimming
    one silently guts the briefing."""
    from services.options_svc import compute
    assert compute._ANALYZE_MAX_TOKENS >= 2400
    assert compute._EOD_MAX_TOKENS >= 2400


def test_gamma_analyze_logs_on_truncation(monkeypatch, caplog):
    import logging
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_gamma_fetch_chain", lambda s: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute, "_gamma_blocks_for", lambda s, c: {"sym": s})
    monkeypatch.setattr(compute, "_session_expected_move", lambda c: 1.0)
    monkeypatch.setattr(compute, "_research_news", lambda *a, **k: [])
    monkeypatch.setattr(compute, "_notable_movers", lambda *a, **k: [])

    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                blk = type("B", (), {"type": "tool_use", "name": "submit_analysis",
                                     "input": {"regime": "r", "bias": 0, "headline": "h",
                                               "narrative": "n", "why": "w",
                                               "indices": []}})()
                return type("R", (), {"content": [blk], "stop_reason": "max_tokens"})()
    with caplog.at_level(logging.WARNING):
        assert compute.gamma_analyze(client=_C()).get("analysis")
    assert any("max_tokens" in r.message for r in caplog.records)


def test_backfill_indices_builds_cards_when_the_model_omits_them():
    """The model intermittently omits `indices` despite it being required (observed
    ~1 run in 3 live). Every number on those cards is already code-computed, so the
    briefing must never lose them -- backfill deterministically, including a factual
    recap sentence built from the session path."""
    from services.options_svc import compute
    data = {"indices": []}
    out = compute._backfill_indices(
        data,
        levels_by_sym={"$SPX": {"flip": 101.0, "call_wall": 106.0, "put_wall": 98.0}},
        em_by_sym={"SPX": 36.5},
        recap={"$SPX": {"path": {"open": 100.0, "high": 105.0, "low": 99.0,
                                 "close": 104.0, "day_pct": 4.0},
                        "flip": 101.0, "call_wall": 106.0, "put_wall": 98.0}},
    )
    assert len(out["indices"]) == 1
    idx = out["indices"][0]
    assert idx["symbol"] == "$SPX"
    assert idx["gamma_flip"] == 101.0 and idx["call_wall"] == 106.0
    assert idx["expected_move"] == 36.5
    assert idx["spot"] == 104.0                     # the session close
    assert "104" in idx["recap"] and "reclaim" in idx["recap"].lower()


def test_backfill_indices_leaves_a_populated_reply_alone():
    from services.options_svc import compute
    data = {"indices": [{"symbol": "$SPX", "recap": "model wrote this"}]}
    out = compute._backfill_indices(data, {"$SPX": {"flip": 1.0}}, {"SPX": 2.0},
                                    {"$SPX": {"path": {"close": 3.0}}})
    assert out["indices"] == [{"symbol": "$SPX", "recap": "model wrote this"}]


def test_backfill_indices_is_defensive():
    from services.options_svc import compute
    assert compute._backfill_indices({"indices": []}, {}, {}, {})["indices"] == []
    assert compute._backfill_indices({}, None, None, None).get("indices") == []


def test_eod_document_is_titled_as_a_recap_not_gamma_analysis():
    """The EOD doc is what gets pushed and opened in a tab; titling it
    'Gamma Analysis' misnames it (the same trap the Market Snapshot work hit)."""
    from services.options_svc import compute
    doc = compute._analyze_doc("<p>x</p>", "sub", title="End-of-Day Recap")
    assert "<title>End-of-Day Recap" in doc
    assert 'class="ga-title">End-of-Day Recap<' in doc
    assert "Gamma Analysis" not in doc
    # Default is unchanged for the three intraday briefings.
    assert "Gamma Analysis" in compute._analyze_doc("<p>x</p>", "sub")


def test_eod_briefing_document_title(monkeypatch):
    from services.options_svc import compute
    monkeypatch.setattr(compute, "_gamma_fetch_chain", lambda s: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute, "_gamma_blocks_for", lambda s, c: {"sym": s})
    monkeypatch.setattr(compute, "_session_expected_move", lambda c: 1.0)
    monkeypatch.setattr(compute, "_eod_session_recap", lambda lv: {})
    monkeypatch.setattr(compute, "_research_news", lambda *a, **k: [])
    monkeypatch.setattr(compute, "_notable_movers", lambda *a, **k: [])

    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                blk = type("B", (), {"type": "tool_use", "name": "submit_eod",
                                     "input": {"regime": "r", "bias": 0, "headline": "h",
                                               "narrative": "n", "why": "w",
                                               "indices": [{"symbol": "$SPX"}]}})()
                return type("R", (), {"content": [blk]})()
    html = compute.eod_briefing(client=_C())["html"]
    assert "End-of-Day Recap" in html and "Gamma Analysis" not in html
