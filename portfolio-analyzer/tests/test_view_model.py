"""Unit tests for the pure presentation helpers in ``src.view_model``.

These cover the human-facing formatting only — no GUI, no network.
"""
from __future__ import annotations

from src.view_model import (
    HOLDINGS_COLUMNS,
    PERFORMANCE_COLUMNS,
    SECTOR_COLUMNS,
    format_currency,
    format_signed_currency,
    format_signed_pct,
    format_weight,
    format_vs_sector,
    format_holdings_rows,
    format_performance_rows,
    format_sector_rows,
)


# --- scalar formatters -----------------------------------------------------

def test_format_currency():
    assert format_currency(1750.0) == "$1,750.00"
    assert format_currency(0) == "$0.00"
    assert format_currency(None) == "—"


def test_format_signed_currency():
    assert format_signed_currency(120.5) == "+$120.50"
    assert format_signed_currency(-50.0) == "-$50.00"
    assert format_signed_currency(0) == "+$0.00"
    assert format_signed_currency(None) == "—"


def test_format_signed_pct_takes_a_fraction():
    assert format_signed_pct(0.08) == "+8.00%"
    assert format_signed_pct(-0.02) == "-2.00%"
    assert format_signed_pct(0.0) == "+0.00%"
    assert format_signed_pct(None) == "—"


def test_format_weight_takes_a_fraction():
    assert format_weight(0.30) == "30.00%"
    assert format_weight(0.0) == "0.00%"
    assert format_weight(None) == "—"


def test_format_vs_sector():
    assert format_vs_sector({"1M": 110.0, "3M": 95.0}) == "1M 110.00 / 3M 95.00"
    assert format_vs_sector(None) == "—"
    assert format_vs_sector({}) == "—"


def test_format_vs_sector_none_value_renders_dash():
    # A None value for a window must not raise; it renders the DASH placeholder.
    assert format_vs_sector({"1M": 110.0, "3M": None}) == "1M 110.00 / 3M —"


# --- holdings rows ---------------------------------------------------------

def _holding(**overrides):
    base = {
        "symbol": "AAPL",
        "asset_type": "EQUITY",
        "sector": "Technology",
        "sector_etf": "XLK",
        "quantity": 10,
        "avg_price": 150.0,
        "market_value": 1750.0,
        "day_pl": 25.0,
        "total_pl": 250.0,
        "vs_sector_rs": {"1M": 110.0, "3M": 95.0},
        "since_purchase_excess": 0.08,
    }
    base.update(overrides)
    return base


def test_format_holdings_rows_full():
    model = {"holdings": [_holding()], "sectors": []}
    rows = format_holdings_rows(model)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "AAPL"
    assert row["sector"] == "Technology"
    assert row["quantity"] == "10"
    assert row["market_value"] == "$1,750.00"
    assert row["day_pl"] == "+$25.00"
    assert row["total_pl"] == "+$250.00"
    assert row["vs_sector"] == "1M 110.00 / 3M 95.00"
    assert row["since_purchase"] == "+8.00%"


def test_format_holdings_rows_none_comparisons_show_dash():
    model = {
        "holdings": [_holding(vs_sector_rs=None, since_purchase_excess=None)],
        "sectors": [],
    }
    row = format_holdings_rows(model)[0]
    assert row["vs_sector"] == "—"
    assert row["since_purchase"] == "—"


def test_format_holdings_rows_robust_to_missing_keys():
    model = {"holdings": [{"symbol": "MSFT"}]}
    row = format_holdings_rows(model)[0]
    assert row["symbol"] == "MSFT"
    assert row["market_value"] == "—"
    assert row["vs_sector"] == "—"


def test_format_holdings_rows_empty_model():
    assert format_holdings_rows({}) == []
    assert format_holdings_rows({"holdings": []}) == []


def test_holdings_columns_match_row_keys():
    model = {"holdings": [_holding()]}
    row = format_holdings_rows(model)[0]
    # Every column header maps to a row key.
    for col in HOLDINGS_COLUMNS:
        assert col["key"] in row


# --- sector rows -----------------------------------------------------------

def _sector(**overrides):
    base = {
        "sector": "Technology",
        "weight": 0.30,
        "benchmark_delta": 0.05,
    }
    base.update(overrides)
    return base


def test_format_sector_rows_full():
    model = {"holdings": [], "sectors": [_sector()]}
    rows = format_sector_rows(model)
    assert len(rows) == 1
    row = rows[0]
    assert row["sector"] == "Technology"
    assert row["weight"] == "30.00%"
    assert row["benchmark_delta"] == "+5.00%"


def test_format_sector_rows_none_values_show_dash():
    model = {"sectors": [_sector(benchmark_delta=None)]}
    row = format_sector_rows(model)[0]
    assert row["benchmark_delta"] == "—"


def test_format_sector_rows_negative_delta():
    model = {"sectors": [_sector(benchmark_delta=-0.02)]}
    row = format_sector_rows(model)[0]
    assert row["benchmark_delta"] == "-2.00%"


def test_format_sector_rows_empty_model():
    assert format_sector_rows({}) == []
    assert format_sector_rows({"sectors": []}) == []


def test_sector_columns_match_row_keys():
    model = {"sectors": [_sector()]}
    row = format_sector_rows(model)[0]
    for col in SECTOR_COLUMNS:
        assert col["key"] in row


# --- performance rows --------------------------------------------------------

def test_performance_columns_contract():
    keys = [c["key"] for c in PERFORMANCE_COLUMNS]
    assert keys == ["symbol", "grade_return", "grade_capital", "grade_risk",
                    "grade_execution", "composite", "ann_return", "vs_sector",
                    "drawdown", "top_action"]


def test_format_performance_rows_full_card():
    cards = {"ABC": {
        "symbol": "ABC", "ann_return": 0.40, "vs_sector": 0.06,
        "drawdown": 0.02, "composite": 3.7,
        "grades": {"return": 4.0, "capital": 3.2, "risk": 4.0,
                   "execution": 3.2}}}
    sugs = {"ABC": [{"action": "HOLD", "severity": "low", "reason": "x"}]}
    rows = format_performance_rows(cards, sugs)
    assert rows == [{
        "symbol": "ABC", "grade_return": "A", "grade_capital": "B",
        "grade_risk": "A", "grade_execution": "B", "composite": "3.7 (A)",
        "ann_return": "+40.00%", "vs_sector": "+6.00%", "drawdown": "2.00%",
        "top_action": "HOLD"}]


def test_format_performance_rows_none_robust():
    cards = {"ABC": {"symbol": "ABC", "ann_return": None, "vs_sector": None,
                     "drawdown": None, "composite": None,
                     "grades": {"return": None, "capital": None,
                                "risk": None, "execution": None}}}
    rows = format_performance_rows(cards, {})
    row = rows[0]
    assert row["grade_return"] == "—" and row["composite"] == "—"
    assert row["top_action"] == "—"


def test_rows_sorted_worst_composite_first():
    cards = {
        "GOOD": {"symbol": "GOOD", "composite": 3.7, "grades": {},
                 "ann_return": None, "vs_sector": None, "drawdown": None},
        "BAD": {"symbol": "BAD", "composite": 0.5, "grades": {},
                "ann_return": None, "vs_sector": None, "drawdown": None},
    }
    rows = format_performance_rows(cards, {})
    assert [r["symbol"] for r in rows] == ["BAD", "GOOD"]


# --- the Holdings tab by account: P/L sign and the expiration day ------------

from src.view_model import format_account_groups  # noqa: E402


def _acct_model(positions):
    return {"holdings": [], "accounts": [{"label": "A", "positions": positions}]}


def _pos(symbol, asset_type="EQUITY", qty=1, mv=100.0, day=0.0, total=0.0, avg=1.0):
    return {"symbol": symbol, "underlying": symbol.split()[0], "asset_type": asset_type,
            "quantity": qty, "avg_price": avg, "market_value": mv,
            "day_pl": day, "total_pl": total}


def test_each_pl_cell_carries_its_sign():
    g = format_account_groups(_acct_model([
        _pos("AAPL", day=12.0, total=-30.0),
        _pos("MSFT", day=0.0, total=0.004),
    ]), today="2026-11-24")[0]
    aapl, msft = g["shares_rows"]
    assert (aapl["day_pl_tone"], aapl["total_pl_tone"]) == ("pos", "neg")
    # under half a cent prints +$0.00 and is no gain or loss
    assert (msft["day_pl_tone"], msft["total_pl_tone"]) == ("flat", "flat")


def test_the_account_totals_carry_their_sign():
    g = format_account_groups(_acct_model([_pos("AAPL", day=-5.0, total=40.0)]),
                              today="2026-11-24")[0]
    assert (g["day_pl_tone"], g["total_pl_tone"]) == ("neg", "pos")


def test_an_option_spread_row_carries_the_sum_of_its_legs_sign():
    g = format_account_groups(_acct_model([
        _pos("SPY   261218P00600000", "OPTION", qty=-1, mv=-300.0, day=10.0, total=50.0),
        _pos("SPY   261218P00595000", "OPTION", qty=1, mv=200.0, day=-4.0, total=-80.0),
    ]), today="2026-11-24")[0]
    row = g["option_rows"][0]
    assert (row["day_pl_tone"], row["total_pl_tone"]) == ("pos", "neg")


def test_an_option_expiring_today_is_flagged():
    g = format_account_groups(_acct_model([
        _pos("SPY   261124P00600000", "OPTION", qty=-1, mv=-30.0),
        _pos("SPY   261125P00600000", "OPTION", qty=-1, mv=-90.0),
    ]), today="2026-11-24")[0]
    by_expiry = {r["expiry"]: r["expires_today"] for r in g["option_rows"]}
    assert by_expiry == {"2026-11-24": True, "2026-11-25": False}


def test_without_a_date_nothing_is_flagged():
    g = format_account_groups(_acct_model([
        _pos("SPY   261124P00600000", "OPTION", qty=-1, mv=-30.0)]))[0]
    assert g["option_rows"][0]["expires_today"] is False


# --- the account's cash and net liquidity ------------------------------------

def test_net_liquidity_is_free_cash_plus_market_value():
    model = {"holdings": [], "accounts": [{"label": "A", "cash": 5000.0, "positions": [
        _pos("AAPL", mv=1750.0),
        _pos("SPY   261218P00600000", "OPTION", qty=-1, mv=-300.0),
    ]}]}
    g = format_account_groups(model, today="2026-11-24")[0]
    assert g["free_cash"] == "$5,000.00"
    assert g["net_liq"] == "$6,450.00"


def test_unknown_cash_is_a_dash_and_so_is_net_liquidity():
    for cash in (None, float("nan"), "junk"):
        model = {"holdings": [], "accounts": [
            {"label": "A", "cash": cash, "positions": [_pos("AAPL", mv=1750.0)]}]}
        g = format_account_groups(model)[0]
        # never the market value alone passed off as the account's worth
        assert (g["free_cash"], g["net_liq"]) == ("—", "—")


def test_negative_cash_keeps_its_sign():
    model = {"holdings": [], "accounts": [
        {"label": "A", "cash": -250.0, "positions": [_pos("AAPL", mv=100.0)]}]}
    g = format_account_groups(model)[0]
    assert g["free_cash"].startswith("-")
    assert g["net_liq"].startswith("-")


def test_the_merged_fallback_has_no_cash():
    g = format_account_groups({"holdings": [_pos("AAPL", mv=100.0)]})[0]
    assert (g["account"], g["free_cash"], g["net_liq"]) == ("All accounts", "—", "—")
