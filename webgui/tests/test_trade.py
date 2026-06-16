"""Tests for the Trade page pure display builders + render API.

The engine orchestration lives in ``services/trade_svc/compute``; this page is a
Tier-3 reader, so only its pure transforms (verdict coloring, momentum/breakdown/
alignment rows) and the ``render`` callable are exercised here.
"""
from pages import trade


def test_verdict_color_buy_hold_sell():
    assert trade.verdict_color("BUY") == trade.BUY_COLOR
    assert trade.verdict_color("buy") == trade.BUY_COLOR
    assert trade.verdict_color("SELL") == trade.SELL_COLOR
    assert trade.verdict_color("HOLD") == trade.HOLD_COLOR
    assert trade.verdict_color(None) == trade.HOLD_COLOR  # default amber


def test_bias_color():
    assert trade.bias_color("BULLISH") == trade.BUY_COLOR
    assert trade.bias_color("BEARISH") == trade.SELL_COLOR
    assert trade.bias_color("NEUTRAL") == trade.HOLD_COLOR
    assert trade.bias_color("") == trade.HOLD_COLOR


def test_momentum_rows_formats_and_handles_missing():
    rows = trade.momentum_rows({"rsi": 55.0, "adx": 22.4, "macd_hist": 0.4321,
                                "vwap": 211.0, "relative_volume": 1.34})
    d = dict(rows)
    assert d["RSI"] == "55.0"
    assert d["MACD hist"] == "0.432"  # 3 decimals
    assert d["VWAP"] == "211.00"
    assert d["Rel Vol"] == "1.34"


def test_momentum_rows_missing_value_is_dash():
    rows = dict(trade.momentum_rows({"rsi": None, "adx": 10.0, "macd_hist": 0.0,
                                     "vwap": None, "relative_volume": 1.0}))
    assert rows["RSI"] == "—"
    assert rows["VWAP"] == "—"


def test_momentum_rows_empty():
    assert trade.momentum_rows({}) == []
    assert trade.momentum_rows(None) == []


def test_breakdown_rows():
    v = {"breakdown": [
        {"factor": "ema_alignment", "weight": 20, "raw_score": 60, "contribution": 12.0},
        {"factor": "adx", "weight": 10, "raw_score": 5, "contribution": 0.49},
    ]}
    rows = trade.breakdown_rows(v)
    assert rows[0] == {"factor": "ema_alignment", "weight": 20,
                       "raw_score": 60, "contribution": 12.0}
    assert rows[1]["contribution"] == 0.5  # rounded to 1 dp


def test_breakdown_rows_empty():
    assert trade.breakdown_rows({}) == []
    assert trade.breakdown_rows(None) == []


def test_alignment_rows():
    ema = {"timeframes": [
        {"timeframe": "daily", "status": "BULLISH", "ema12": 1},
        {"timeframe": "5min", "status": "MIXED"},
    ]}
    assert trade.alignment_rows(ema) == [
        {"timeframe": "daily", "status": "BULLISH"},
        {"timeframe": "5min", "status": "MIXED"},
    ]


def test_fundamentals_rows_formats_percents_and_margin():
    rows = dict(trade.fundamentals_rows({
        "pe_ratio": 28.0, "peg_ratio": 0.8, "rev_growth_ttm": 0.20,
        "eps_growth_ttm": 0.25, "roe": 1.41, "margin_expanding": True,
        "days_to_earnings": None,
    }))
    assert rows["P/E"] == "28.0"
    assert rows["PEG"] == "0.80"
    assert rows["Rev growth"] == "20.0%"
    assert rows["ROE"] == "141.0%"
    assert rows["Margins"] == "expanding"
    assert "Earnings in" not in rows  # None days-to-earnings omitted


def test_fundamentals_rows_missing_and_contracting():
    rows = dict(trade.fundamentals_rows({
        "pe_ratio": None, "margin_expanding": False, "days_to_earnings": 12,
    }))
    assert rows["P/E"] == "—"
    assert rows["Rev growth"] == "—"
    assert rows["Margins"] == "contracting"
    assert rows["Earnings in"] == "12d"


def test_fundamentals_rows_empty():
    assert trade.fundamentals_rows({}) == []
    assert trade.fundamentals_rows(None) == []


def test_render_is_callable():
    assert callable(trade.render)
