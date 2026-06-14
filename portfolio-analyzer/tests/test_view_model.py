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
    format_tailwind,
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
    assert format_signed_pct(0.08) == "+8.0%"
    assert format_signed_pct(-0.02) == "-2.0%"
    assert format_signed_pct(0.0) == "+0.0%"
    assert format_signed_pct(None) == "—"


def test_format_weight_takes_a_fraction():
    assert format_weight(0.30) == "30.0%"
    assert format_weight(0.0) == "0.0%"
    assert format_weight(None) == "—"


def test_format_vs_sector():
    assert format_vs_sector({"1M": 110.0, "3M": 95.0}) == "1M 110.0 / 3M 95.0"
    assert format_vs_sector(None) == "—"
    assert format_vs_sector({}) == "—"


def test_format_vs_sector_none_value_renders_dash():
    # A None value for a window must not raise; it renders the DASH placeholder.
    assert format_vs_sector({"1M": 110.0, "3M": None}) == "1M 110.0 / 3M —"


def test_format_tailwind():
    assert format_tailwind({"score": 112, "rank": 1}) == "112 (#1)"
    assert format_tailwind(None) == "—"
    assert format_tailwind({}) == "—"


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
    assert row["vs_sector"] == "1M 110.0 / 3M 95.0"
    assert row["since_purchase"] == "+8.0%"


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
        "tailwind": {"score": 112, "rank": 1},
    }
    base.update(overrides)
    return base


def test_format_sector_rows_full():
    model = {"holdings": [], "sectors": [_sector()]}
    rows = format_sector_rows(model)
    assert len(rows) == 1
    row = rows[0]
    assert row["sector"] == "Technology"
    assert row["weight"] == "30.0%"
    assert row["benchmark_delta"] == "+5.0%"
    assert row["tailwind"] == "112 (#1)"


def test_format_sector_rows_none_values_show_dash():
    model = {"sectors": [_sector(benchmark_delta=None, tailwind=None)]}
    row = format_sector_rows(model)[0]
    assert row["benchmark_delta"] == "—"
    assert row["tailwind"] == "—"


def test_format_sector_rows_negative_delta():
    model = {"sectors": [_sector(benchmark_delta=-0.02)]}
    row = format_sector_rows(model)[0]
    assert row["benchmark_delta"] == "-2.0%"


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
        "ann_return": "+40.0%", "vs_sector": "+6.0%", "drawdown": "2.0%",
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
