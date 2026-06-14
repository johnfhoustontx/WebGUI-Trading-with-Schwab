"""Tests for ``build_portfolio`` — the full portfolio model assembler.

Fully offline: every I/O dependency (data, classifier, ranker, benchmark) is
injected. ``holding_vs_sector`` is monkeypatched at the ``src.sectors`` module
global so no heavy ``shared/analysis_lib`` import is triggered.
"""
import pandas as pd
import pytest

import src.sectors
from src.portfolio import build_portfolio


def _daily(closes, start="2025-01-02"):
    dates = pd.date_range(start=start, periods=len(closes), freq="D")
    return pd.DataFrame({"datetime": dates, "close": list(closes)})


class FakeData:
    """Canned positions + price history for AAPL and XLK only."""

    def __init__(self):
        self._history = {
            "AAPL": _daily([150.0, 160.0, 170.0, 180.0]),
            "XLK": _daily([100.0, 102.0, 104.0, 106.0]),
        }

    def get_positions(self):
        return [
            {
                "symbol": "AAPL",
                "asset_type": "EQUITY",
                "underlying": "AAPL",
                "quantity": 10,
                "avg_price": 150.0,
                "market_value": 1800.0,
                "day_pl": 25.0,
                "total_pl": 300.0,
            },
            {
                "symbol": "AAPL  250620C00200000",
                "asset_type": "OPTION",
                "underlying": "AAPL",
                "quantity": 1,
                "avg_price": 5.0,
                "market_value": 600.0,
                "day_pl": 10.0,
                "total_pl": 100.0,
            },
            {
                "symbol": "/ES",
                "asset_type": "FUTURE",
                "underlying": "/ES",
                "quantity": 1,
                "avg_price": 5000.0,
                "market_value": 5000.0,
                "day_pl": 50.0,
                "total_pl": 200.0,
            },
        ]

    def get_daily_history(self, symbol, months=12):
        return self._history.get(symbol)


def fake_classify(underlying):
    table = {"AAPL": ("XLK", "Technology")}
    etf, name = table.get(underlying, ("SPY", "Unknown"))
    return {"sector_etf": etf, "sector": name}


class _Rank:
    def __init__(self, symbol, composite_rs, rank):
        self.symbol = symbol
        self.composite_rs = composite_rs
        self.rank = rank


class FakeRanker:
    def get_rankings(self):
        return [_Rank("XLK", 112.0, 1)]


TRADES = [
    {
        "symbol": "AAPL",
        "instruction": "BUY",
        "quantity": 10,
        "price": 150.0,
        "trade_date": "2025-01-02",
    }
]

BENCHMARK = {"Technology": 0.30}


@pytest.fixture(autouse=True)
def _fake_rs(monkeypatch):
    """Inject a fake RS so #1 never hits the heavy shared module."""
    monkeypatch.setattr(
        src.sectors,
        "calculate_stock_vs_sector_rs",
        lambda stock_df, sector_df, periods: {"1M": 110.0, "3M": 108.0},
    )


def _by_symbol(holdings, symbol):
    return next(h for h in holdings if h["symbol"] == symbol)


def _by_sector(sectors, sector):
    return next(s for s in sectors if s["sector"] == sector)


def test_model_has_holdings_and_sectors():
    model = build_portfolio(
        FakeData(), TRADES, classify=fake_classify, ranker=FakeRanker(), benchmark=BENCHMARK
    )
    assert "holdings" in model
    assert "sectors" in model


def test_equity_holding_has_rs_and_excess():
    model = build_portfolio(
        FakeData(), TRADES, classify=fake_classify, ranker=FakeRanker(), benchmark=BENCHMARK
    )
    aapl = _by_symbol(model["holdings"], "AAPL")
    assert aapl["asset_type"] == "EQUITY"
    assert aapl["sector"] == "Technology"
    assert aapl["sector_etf"] == "XLK"
    assert aapl["vs_sector_rs"] is not None
    assert aapl["vs_sector_rs"] == {"1M": 110.0, "3M": 108.0}
    assert isinstance(aapl["since_purchase_excess"], float)
    # AAPL +20% (150->180), XLK +6% (100->106) => excess ~0.14
    assert abs(aapl["since_purchase_excess"] - (0.20 - 0.06)) < 1e-9
    # Passthrough fields preserved.
    assert aapl["quantity"] == 10
    assert aapl["market_value"] == 1800.0
    assert aapl["day_pl"] == 25.0
    assert aapl["total_pl"] == 300.0


def test_option_rolls_into_underlying_sector_no_rs():
    model = build_portfolio(
        FakeData(), TRADES, classify=fake_classify, ranker=FakeRanker(), benchmark=BENCHMARK
    )
    opt = _by_symbol(model["holdings"], "AAPL  250620C00200000")
    assert opt["sector"] == "Technology"
    assert opt["vs_sector_rs"] is None
    assert opt["since_purchase_excess"] is None


def test_future_sector_and_no_rs():
    model = build_portfolio(
        FakeData(), TRADES, classify=fake_classify, ranker=FakeRanker(), benchmark=BENCHMARK
    )
    fut = _by_symbol(model["holdings"], "/ES")
    assert fut["sector"] == "Futures"
    assert fut["sector_etf"] is None
    assert fut["vs_sector_rs"] is None
    assert fut["since_purchase_excess"] is None


def test_sector_row_has_benchmark_delta_and_tailwind():
    model = build_portfolio(
        FakeData(), TRADES, classify=fake_classify, ranker=FakeRanker(), benchmark=BENCHMARK
    )
    tech = _by_sector(model["sectors"], "Technology")
    assert tech["benchmark_delta"] is not None
    # weight - 0.30
    assert abs(tech["benchmark_delta"] - (tech["weight"] - 0.30)) < 1e-9
    assert tech["tailwind"] == {"score": 112.0, "rank": 1}

    futures = _by_sector(model["sectors"], "Futures")
    assert futures["tailwind"] is None


def test_no_ranker_means_tailwind_none():
    model = build_portfolio(
        FakeData(), TRADES, classify=fake_classify, ranker=None, benchmark=BENCHMARK
    )
    for row in model["sectors"]:
        assert row["tailwind"] is None


def test_no_benchmark_means_delta_none():
    model = build_portfolio(
        FakeData(), TRADES, classify=fake_classify, ranker=FakeRanker(), benchmark=None
    )
    for row in model["sectors"]:
        assert row["benchmark_delta"] is None
