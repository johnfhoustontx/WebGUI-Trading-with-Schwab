"""Unit tests for the pure live-update logic in ``src.live``.

These exercise ``apply_tick`` (and ``parse_sse_line``) without any network or
GUI — the SSE consumer loop and Tk wiring are manually verified, not tested here.
"""
from __future__ import annotations

import copy

import pytest

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
            {"sector": "Technology", "weight": 0.75, "benchmark_delta": 0.1},
            {"sector": "Financials", "weight": 0.25, "benchmark_delta": -0.05},
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
                     "benchmark_delta": None}],
    }
    # An option is priced by its MARK on the option stream (apply_option_tick);
    # the equity stream cannot quote one (2026-11-24). Same multiplier.
    from src.live import apply_option_tick
    out = apply_option_tick(model, {"symbol": "AAPL  240119C00150000",
                                    "mark": 6.0})
    opt = out["holdings"][0]
    assert opt["market_value"] == 1200.0  # 2 * 6 * 100
    assert opt["total_pl"] == 200.0       # 0 at the rebuild + the 200 it moved


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


def test_apply_tick_preserves_sector_benchmark():
    model = _equity_model()
    out = apply_tick(model, {"symbol": "AAPL", "last": 300.0, "net_change": None})
    tech = next(s for s in out["sectors"] if s["sector"] == "Technology")
    assert tech["benchmark_delta"] == 0.1


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


# --- the per-account positions take the same tick --------------------------

def test_apply_tick_updates_each_accounts_positions_too():
    row = {"symbol": "AAPL", "asset_type": "EQUITY", "quantity": 10,
           "avg_price": 100.0, "market_value": 1000.0, "sector": "Technology"}
    model = {"holdings": [dict(row)], "sectors": [],
             "accounts": [{"label": "Trading account", "positions": [dict(row)]},
                          {"label": "IRA", "positions": []}]}
    before = copy.deepcopy(model)
    out = apply_tick(model, {"symbol": "AAPL", "last": 120.0, "net_change": 2.0})
    acct = out["accounts"][0]["positions"][0]
    assert acct["market_value"] == 1200.0
    assert acct["total_pl"] == 200.0
    assert acct["day_pl"] == 20.0
    assert out["accounts"][0]["label"] == "Trading account"
    assert out["accounts"][1]["positions"] == []
    assert model == before                      # the caller's model is untouched


def test_a_tick_keeps_each_accounts_cash_so_net_liquidity_moves_with_it():
    from src.view_model import format_account_groups
    row = {"symbol": "AAPL", "asset_type": "EQUITY", "quantity": 10,
           "avg_price": 100.0, "market_value": 1000.0, "sector": "Technology"}
    model = {"holdings": [dict(row)], "sectors": [],
             "accounts": [{"label": "A", "cash": 500.0, "positions": [dict(row)]}]}
    out = apply_tick(model, {"symbol": "AAPL", "last": 120.0, "net_change": 2.0})
    assert out["accounts"][0]["cash"] == 500.0
    g = format_account_groups(out)[0]
    assert (g["free_cash"], g["net_liq"]) == ("$500.00", "$1,700.00")


def test_apply_tick_adds_no_accounts_key_to_a_model_without_one():
    out = apply_tick({"holdings": [], "sectors": []}, {"symbol": "X", "last": 1.0})
    assert "accounts" not in out


# --- option legs and a zero price (2026-11-24, seen on prod) ------------------
# The proxy's /stream/quotes is Schwab's EQUITY stream. An option symbol sent
# there came back as last 0 / net change 0, and every option leg was set to a
# market value of 0 and a P/L of minus its whole cost: a Bear Put Spread entered
# at 7.80 x 2 showed Mark 0.00, Market Value $0.00 and Total P/L -$1,560.00.

def test_stream_symbols_leaves_option_legs_off_the_equity_stream():
    model = {"holdings": [
        {"symbol": "AAPL", "asset_type": "EQUITY", "sector_etf": "XLK"},
        {"symbol": "AVGO  261125P00312500", "asset_type": "OPTION",
         "sector_etf": "XLK"},
    ]}
    assert stream_symbols(model) == ["AAPL", "XLK", "SPY"]


def test_an_option_only_book_still_streams_its_sector_etf():
    model = {"holdings": [{"symbol": "AVGO  261125P00312500",
                           "asset_type": "OPTION", "sector_etf": "XLK"}]}
    assert stream_symbols(model) == ["XLK", "SPY"]


def _leg(qty, avg, mv):
    return {"symbol": "AVGO  261125P00312500", "asset_type": "OPTION",
            "quantity": qty, "avg_price": avg, "market_value": mv,
            "day_pl": 12.0, "total_pl": -300.0, "sector": "Technology"}


@pytest.mark.parametrize("last", [0.0, -1.0])
def test_a_tick_with_no_usable_price_changes_nothing(last):
    leg = _leg(2, 12.40, 1880.0)
    model = {"holdings": [dict(leg)], "sectors": [],
             "accounts": [{"label": "A", "positions": [dict(leg)]}]}
    out = apply_tick(model, {"symbol": leg["symbol"], "last": last,
                             "net_change": 0.0})
    assert out["holdings"][0] == leg
    assert out["accounts"][0]["positions"][0] == leg


def test_a_zero_price_on_a_stock_is_no_reading_either():
    row = {"symbol": "AAPL", "asset_type": "EQUITY", "quantity": 10,
           "avg_price": 100.0, "market_value": 1100.0, "day_pl": 5.0,
           "total_pl": 100.0, "sector": "Technology"}
    out = apply_tick({"holdings": [dict(row)], "sectors": []},
                     {"symbol": "AAPL", "last": 0.0, "net_change": 0.0})
    assert out["holdings"][0] == row


# --- live option marks (the proxy's /stream/options, Schwab's MARK) ----------

from src.live import apply_option_tick, option_stream_symbols  # noqa: E402

_PUT = "AVGO  261125P00312500"


def test_option_stream_symbols_are_the_option_legs_only():
    model = {"holdings": [
        {"symbol": "AAPL", "asset_type": "EQUITY"},
        {"symbol": _PUT, "asset_type": "OPTION"},
        {"symbol": _PUT, "asset_type": "OPTION"},
    ]}
    assert option_stream_symbols(model) == [_PUT]


def _long_put():
    # 2 contracts bought at 12.40, Schwab marked them 9.40 at the rebuild.
    return {"symbol": _PUT, "asset_type": "OPTION", "quantity": 2,
            "avg_price": 12.40, "market_value": 1880.0, "day_pl": -150.0,
            "total_pl": -600.0, "sector": "Technology"}


def test_a_mark_moves_value_and_both_pls_by_the_same_dollars():
    model = {"holdings": [_long_put()], "sectors": []}
    out = apply_option_tick(model, {"symbol": _PUT, "mark": 10.00})
    row = out["holdings"][0]
    assert row["market_value"] == pytest.approx(2000.0)      # 2 x 10.00 x 100
    assert row["day_pl"] == pytest.approx(-30.0)              # -150 + 120
    assert row["total_pl"] == pytest.approx(-480.0)           # -600 + 120


def test_marks_measure_from_the_rebuild_not_the_last_tick():
    model = {"holdings": [_long_put()], "sectors": []}
    once = apply_option_tick(model, {"symbol": _PUT, "mark": 10.00})
    twice = apply_option_tick(once, {"symbol": _PUT, "mark": 9.40})
    row = twice["holdings"][0]
    assert row["market_value"] == pytest.approx(1880.0)
    assert row["day_pl"] == pytest.approx(-150.0)
    assert row["total_pl"] == pytest.approx(-600.0)


def test_a_short_leg_loses_when_its_mark_rises():
    short = {"symbol": _PUT, "asset_type": "OPTION", "quantity": -2,
             "avg_price": 4.60, "market_value": -800.0, "day_pl": 20.0,
             "total_pl": 120.0, "sector": "Technology"}
    out = apply_option_tick({"holdings": [short], "sectors": []},
                            {"symbol": _PUT, "mark": 4.50})
    row = out["holdings"][0]
    assert row["market_value"] == pytest.approx(-900.0)
    assert row["day_pl"] == pytest.approx(-80.0)
    assert row["total_pl"] == pytest.approx(20.0)


@pytest.mark.parametrize("tick", [{"symbol": _PUT, "mark": None},
                                  {"symbol": _PUT, "mark": 0.0},
                                  {"symbol": _PUT, "bid": 9.0, "ask": 9.2}])
def test_an_option_tick_with_no_mark_changes_nothing(tick):
    model = {"holdings": [_long_put()], "sectors": []}
    assert apply_option_tick(model, tick) is model


def test_the_option_tick_reaches_each_accounts_leg_and_spares_the_caller():
    model = {"holdings": [_long_put()], "sectors": [],
             "accounts": [{"label": "A", "positions": [_long_put()]}]}
    before = copy.deepcopy(model)
    out = apply_option_tick(model, {"symbol": _PUT, "mark": 10.00})
    assert out["accounts"][0]["positions"][0]["market_value"] == pytest.approx(2000.0)
    assert model == before


def test_an_equity_tick_never_moves_an_option_leg():
    model = {"holdings": [_long_put()], "sectors": []}
    out = apply_tick(model, {"symbol": _PUT, "last": 5.0, "net_change": 1.0})
    assert out["holdings"][0] == _long_put()
