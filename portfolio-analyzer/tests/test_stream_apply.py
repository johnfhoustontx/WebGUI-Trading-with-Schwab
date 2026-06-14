"""Unit tests for the pure live-update logic in ``src.live``.

These exercise ``apply_tick`` (and ``parse_sse_line``) without any network or
GUI — the SSE consumer loop and Tk wiring are manually verified, not tested here.
"""
from __future__ import annotations

import copy

from src.live import apply_tick, parse_sse_line, stream_symbols, MULTIPLIER


def _equity_model():
    return {
        "holdings": [
            {
                "symbol": "AAPL",
                "asset_type": "EQUITY",
                "sector": "Technology",
                "sector_etf": "XLK",
                "quantity": 10,
                "avg_price": 150.0,
                "market_value": 1500.0,
                "day_pl": 0.0,
                "total_pl": 0.0,
                "vs_sector_rs": {"1M": 110.0},
                "since_purchase_excess": 0.05,
            },
            {
                "symbol": "JPM",
                "asset_type": "EQUITY",
                "sector": "Financials",
                "sector_etf": "XLF",
                "quantity": 5,
                "avg_price": 100.0,
                "market_value": 500.0,
                "day_pl": 0.0,
                "total_pl": 0.0,
                "vs_sector_rs": {"1M": 90.0},
                "since_purchase_excess": -0.02,
            },
        ],
        "sectors": [
            {"sector": "Technology", "weight": 0.75, "benchmark_delta": 0.1,
             "tailwind": {"score": 112, "rank": 1}},
            {"sector": "Financials", "weight": 0.25, "benchmark_delta": -0.05,
             "tailwind": None},
        ],
    }


# --- MULTIPLIER ------------------------------------------------------------

def test_multiplier_option_is_100():
    assert MULTIPLIER("OPTION") == 100


def test_multiplier_equity_is_1():
    assert MULTIPLIER("EQUITY") == 1


def test_multiplier_future_is_1():
    # TODO: futures point-value multiplier — currently 1.
    assert MULTIPLIER("FUTURE") == 1


# --- apply_tick: price-driven fields ---------------------------------------

def test_apply_tick_updates_market_value_and_total_pl_equity():
    model = _equity_model()
    out = apply_tick(model, {"symbol": "AAPL", "last": 160.0, "net_change": None})
    aapl = next(h for h in out["holdings"] if h["symbol"] == "AAPL")
    assert aapl["last"] == 160.0
    assert aapl["market_value"] == 1600.0
    assert aapl["total_pl"] == 100.0  # 1600 - 10*150*1


def test_apply_tick_option_multiplier():
    model = {
        "holdings": [{
            "symbol": "AAPL  240119C00150000",
            "asset_type": "OPTION",
            "sector": "Technology",
            "sector_etf": "XLK",
            "quantity": 2,
            "avg_price": 5.0,
            "market_value": 1000.0,
            "day_pl": 0.0,
            "total_pl": 0.0,
            "vs_sector_rs": None,
            "since_purchase_excess": None,
        }],
        "sectors": [{"sector": "Technology", "weight": 1.0,
                     "benchmark_delta": None, "tailwind": None}],
    }
    out = apply_tick(model, {"symbol": "AAPL  240119C00150000",
                             "last": 6.0, "net_change": None})
    opt = out["holdings"][0]
    assert opt["market_value"] == 1200.0  # 2 * 6 * 100
    assert opt["total_pl"] == 200.0       # 1200 - 2*5*100


def test_apply_tick_day_pl_from_net_change_equity():
    model = _equity_model()
    out = apply_tick(model, {"symbol": "AAPL", "last": None, "net_change": 1.5})
    aapl = next(h for h in out["holdings"] if h["symbol"] == "AAPL")
    assert aapl["day_pl"] == 15.0  # 10 * 1.5 * 1


def test_apply_tick_last_none_leaves_price_fields_unchanged():
    model = _equity_model()
    out = apply_tick(model, {"symbol": "AAPL", "last": None, "net_change": 2.0})
    aapl = next(h for h in out["holdings"] if h["symbol"] == "AAPL")
    # price-derived fields untouched
    assert aapl["market_value"] == 1500.0
    assert aapl["total_pl"] == 0.0
    assert "last" not in aapl
    # but day_pl still updates from net_change
    assert aapl["day_pl"] == 20.0


# --- apply_tick: isolation / purity ---------------------------------------

def test_apply_tick_non_matching_holding_unchanged():
    model = _equity_model()
    out = apply_tick(model, {"symbol": "AAPL", "last": 160.0, "net_change": 1.0})
    jpm = next(h for h in out["holdings"] if h["symbol"] == "JPM")
    assert jpm["market_value"] == 500.0
    assert jpm["total_pl"] == 0.0
    assert jpm["day_pl"] == 0.0


def test_apply_tick_does_not_mutate_caller():
    model = _equity_model()
    before = copy.deepcopy(model)
    apply_tick(model, {"symbol": "AAPL", "last": 160.0, "net_change": 1.5})
    assert model == before


def test_apply_tick_preserves_vs_sector_and_since_purchase():
    model = _equity_model()
    out = apply_tick(model, {"symbol": "AAPL", "last": 160.0, "net_change": 1.0})
    aapl = next(h for h in out["holdings"] if h["symbol"] == "AAPL")
    assert aapl["vs_sector_rs"] == {"1M": 110.0}
    assert aapl["since_purchase_excess"] == 0.05


# --- apply_tick: sector weights recomputed ---------------------------------

def test_apply_tick_recomputes_sector_weights():
    model = _equity_model()
    # AAPL 1500 -> 3000 (last 300). Tech now 3000 of 3500 total abs.
    out = apply_tick(model, {"symbol": "AAPL", "last": 300.0, "net_change": None})
    weights = {s["sector"]: s["weight"] for s in out["sectors"]}
    assert abs(weights["Technology"] - 3000 / 3500) < 1e-9
    assert abs(weights["Financials"] - 500 / 3500) < 1e-9


def test_apply_tick_preserves_sector_benchmark_and_tailwind():
    model = _equity_model()
    out = apply_tick(model, {"symbol": "AAPL", "last": 300.0, "net_change": None})
    tech = next(s for s in out["sectors"] if s["sector"] == "Technology")
    assert tech["benchmark_delta"] == 0.1
    assert tech["tailwind"] == {"score": 112, "rank": 1}


def test_apply_tick_unknown_symbol_is_noop_on_holdings():
    model = _equity_model()
    out = apply_tick(model, {"symbol": "ZZZZ", "last": 99.0, "net_change": 9.0})
    assert out["holdings"][0]["market_value"] == 1500.0
    assert out["holdings"][1]["market_value"] == 500.0


# --- stream_symbols --------------------------------------------------------

def test_stream_symbols_includes_symbol_and_sector_etf():
    model = {"holdings": [
        {"symbol": "AAPL", "sector_etf": "XLK"},
    ]}
    assert stream_symbols(model) == ["AAPL", "XLK", "SPY"]


def test_stream_symbols_dedups_repeated_etf():
    # Two Tech holdings share XLK — it should appear once.
    model = {"holdings": [
        {"symbol": "AAPL", "sector_etf": "XLK"},
        {"symbol": "MSFT", "sector_etf": "XLK"},
    ]}
    assert stream_symbols(model) == ["AAPL", "XLK", "MSFT", "SPY"]


def test_stream_symbols_preserves_order():
    model = {"holdings": [
        {"symbol": "JPM", "sector_etf": "XLF"},
        {"symbol": "AAPL", "sector_etf": "XLK"},
    ]}
    assert stream_symbols(model) == ["JPM", "XLF", "AAPL", "XLK", "SPY"]


def test_stream_symbols_drops_none_sector_etf_for_futures():
    # A future has sector_etf None — only its symbol is included.
    model = {"holdings": [
        {"symbol": "AAPL", "sector_etf": "XLK"},
        {"symbol": "/ES", "sector_etf": None},
    ]}
    assert stream_symbols(model) == ["AAPL", "XLK", "/ES", "SPY"]


def test_stream_symbols_drops_falsy_symbol():
    model = {"holdings": [
        {"symbol": "", "sector_etf": "XLK"},
        {"symbol": "AAPL", "sector_etf": ""},
    ]}
    assert stream_symbols(model) == ["XLK", "AAPL", "SPY"]


def test_stream_symbols_spy_appended_once_when_already_held():
    # SPY held directly (sector_etf SPY too) — must not duplicate the extra.
    model = {"holdings": [
        {"symbol": "SPY", "sector_etf": "SPY"},
    ]}
    assert stream_symbols(model) == ["SPY"]


def test_stream_symbols_empty_model_is_just_extras():
    assert stream_symbols({"holdings": []}) == ["SPY"]
    assert stream_symbols({}) == ["SPY"]


# --- parse_sse_line --------------------------------------------------------

def test_parse_sse_line_data():
    line = 'data: {"symbol": "AAPL", "last": 160.0, "net_change": 1.5}'
    assert parse_sse_line(line) == {"symbol": "AAPL", "last": 160.0,
                                    "net_change": 1.5}


def test_parse_sse_line_blank_returns_none():
    assert parse_sse_line("") is None
    assert parse_sse_line("   ") is None


def test_parse_sse_line_comment_returns_none():
    assert parse_sse_line(": keep-alive") is None


def test_parse_sse_line_non_data_returns_none():
    assert parse_sse_line("event: ping") is None


def test_parse_sse_line_bad_json_returns_none():
    assert parse_sse_line("data: not-json") is None
