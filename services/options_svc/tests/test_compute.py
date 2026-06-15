"""Tests for the options service compute module (Task 2.2).

``compute.run_scan`` is a thin wrapper over ``scanner_engine.run_full_scan``
called with the shared schwab-py-compatible proxy client. We monkeypatch the
eagerly-imported ``run_full_scan`` name so nothing touches a live proxy.

Also asserts the ``options_scoring()`` collision guard (used in the GUI's
``webgui/pages/options/scanner.py``) was intentionally NOT ported: this service
process loads no sentiment code, so ``import scoring`` resolves to
options-scanner's unambiguously and the guard is unnecessary.
"""
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


# ── Swing scan (moved from webgui/pages/options/swing.py) ────────────────────
def test_assign_ids_adds_unique_ids():
    out = compute.assign_ids([{"symbol": "MU"}, {"symbol": "MU"}], "MU")
    ids = [s["id"] for s in out]
    assert len(set(ids)) == 2
    assert all(i.startswith("MU") for i in ids)


def test_assign_ids_preserves_existing():
    assert compute.assign_ids([{"symbol": "MU", "id": "keep"}], "MU")[0]["id"] == "keep"


def test_swing_scan_pipeline_wiring(monkeypatch):
    """The swing pipeline calls each engine step with the right clients/args and
    scores directly (no ``options_scoring`` guard)."""
    calls = {}

    chain = {"underlyingPrice": 540.0}
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None: (
                            calls.__setitem__("chain_client", client), chain)[1])
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda symbol: (calls.__setitem__("quote_symbol", symbol),
                                        {"last": 541.0})[1])
    monkeypatch.setattr(compute.se, "fetch_price_history",
                        lambda client, symbol: {"hist": True})
    monkeypatch.setattr(compute.se, "calc_technicals", lambda hist: {"rsi": 50})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda client, symbol, price=None, hist=None, chain=None: (
                            calls.__setitem__("iv_price", price),
                            {"expected_moves": {"daily": {"move_dollars": 3.2}}})[1])

    def _screen(chain, symbol, dte_min, dte_max, put_d_min, put_d_max,
                call_d_min, call_d_max, min_cr, kind, spot=None, daily_expected_move=None):
        calls["screen"] = dict(min_cr=min_cr, kind=kind, spot=spot,
                               dem=daily_expected_move)
        return [{"symbol": symbol, "type": "PCS", "short_strike": 530}]

    monkeypatch.setattr(compute.se, "screen_spreads", _screen)
    monkeypatch.setattr(compute.se, "build_iron_condors", lambda spreads: [])

    # ``scoring`` is imported lazily inside swing_scan; patch the module object in
    # sys.modules so the in-function ``import scoring`` resolves to this fake (no
    # collision-guard ceremony — the service binds options-scanner's scoring).
    import sys as _sys
    import types as _types
    fake_scoring = _types.SimpleNamespace(
        score_all_signals=lambda signals, ivs, techs: calls.__setitem__("scored", True))
    monkeypatch.setitem(_sys.modules, "scoring", fake_scoring)

    out = compute.swing_scan("SPY", 5, 30, -0.20, -0.10, 0.10, 0.20, 0.10)

    assert calls["chain_client"] is compute._proxy.schwab_py_client
    assert calls["quote_symbol"] == "SPY"
    assert calls["iv_price"] == 541.0          # quote.last wins over chain price
    assert calls["screen"]["min_cr"] == 0.10   # passed as a fraction, not %
    assert calls["screen"]["kind"] == "SWING"
    assert calls["screen"]["spot"] == 541.0
    assert calls["screen"]["dem"] == 3.2
    assert calls["scored"] is True
    # assign_ids ran -> signal has a unique id.
    assert out and out[0]["id"].startswith("SPY")


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
    assert out == {"trade_id": "T1", "symbol": "SPY", "action": "CLOSE"}


def test_analyze_paper_defensive_on_missing_verdict(monkeypatch):
    import sys as _sys
    import types as _types

    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(get_all_trades=lambda: []))
    monkeypatch.setitem(_sys.modules, "trade_analyzer",
                        _types.SimpleNamespace(analyze_trade=lambda c, t, i: None))

    out = compute.analyze_paper("gone")
    assert out == {"trade_id": "gone", "symbol": None, "action": "—"}


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
               "recommendation": "HOLD", "recommendation_code": "HOLD"},
        "X2": {"unrealized_pnl": -30.0, "current_score": 40, "score_drift": -20,
               "recommendation": "CLOSE", "recommendation_code": "money_stop"},
    }
    monkeypatch.setitem(_sys.modules, "signal_recommender",
                        _types.SimpleNamespace(build_mark=lambda r, rep, now: marks[r["signal_id"]]))

    out = compute.reprice_captured()
    by_id = {s["signal_id"]: s for s in out["signals"]}
    # Mark display fields merged into the rows.
    assert by_id["X1"]["unrealized_pnl"] == 12.0
    assert by_id["X1"]["current_score"] == 68
    assert by_id["X2"]["score_drift"] == -20
    assert by_id["X2"]["recommendation"] == "CLOSE"
    # Only the stop/target code is flagged (case-insensitive).
    assert out["flags"] == [{"symbol": "QQQ", "code": "MONEY_STOP"}]


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

    fake_gt = _types.SimpleNamespace(
        GammaEngine=_FakeEngine,
        get_gex_walls=lambda data, top_n=5: (walls or [5400.0]),
        get_dex_walls=lambda data, top_n=5: (walls or [5400.0]))
    fake_gh = _types.SimpleNamespace(
        connect=lambda read_only=False: object(),
        load_today_with_grid=lambda conn, symbol, view: (history or []))
    monkeypatch.setitem(_sys.modules, "gamma_tool", fake_gt)
    monkeypatch.setitem(_sys.modules, "gex_history_db", fake_gh)
    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain",
                        lambda *a, **k: _FakeChainResp(
                            chain if chain is not None else {"underlyingPrice": 5400.0}))


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
    assert gexv["walls"] == [5400.0]
    assert gexv["flip"] == 5399.5
    assert gexv["history"] and gexv["history"][0][6] == {5400.0: {"net": 1}}
    # DEX carries the hedge tiles.
    assert snap["views"]["DEX"]["hedge"] == {
        "net_delta_0dte": 10.0, "projected_net_delta_close": 5.0,
        "hedge_pressure": -5.0}
    assert snap["term"] == {"expirations": ["2026-06-18"], "cells": {}}


def test_gamma_snapshot_none_when_chain_fetch_fails(monkeypatch):
    _patch_gamma(monkeypatch)

    class _Bad:
        status_code = 500

        def json(self):
            return {}

    monkeypatch.setattr(compute._proxy.schwab_py_client, "get_option_chain",
                        lambda *a, **k: _Bad())
    assert compute.gamma_snapshot("$SPX") is None


def test_gamma_explain_returns_body(monkeypatch):
    import sys as _sys
    import types as _types

    _patch_gamma(monkeypatch)
    # gamma_tool needs the explain text builder + snapshot_summary staticmethod.
    fake_gt = _sys.modules["gamma_tool"]
    _FakeEngine.snapshot_summary = staticmethod(lambda data, view: {"spot": 5400.0})
    fake_gt.build_explain_html_text = lambda ctx: "EXPLAIN TEXT"
    monkeypatch.setitem(_sys.modules, "html_render", _types.SimpleNamespace(
        pinch_section_html=lambda s: "",
        explain_to_html=lambda t: f"<p>{t}</p>",
        linkify=lambda h: h))
    monkeypatch.setitem(_sys.modules, "regime_filter",
                        _types.SimpleNamespace(evaluate_regime=lambda: {"active": False}))

    out = compute.gamma_explain("$SPX")
    assert out["symbol"] == "$SPX"
    assert "EXPLAIN TEXT" in out["body"]
    # Restore the instance-method snapshot_summary for other tests.
    del _FakeEngine.snapshot_summary


def test_gamma_analyze_bundles_three_symbols(monkeypatch):
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

    out = compute.gamma_analyze()
    assert out == {"prompt": "BUNDLED PROMPT"}
    # All three symbol bundles built (non-None).
    assert all(b is not None for b in seen["args"])
    del _FakeEngine.calc_expected_move_from_chain


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
