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


_MK = {
    "current_band": 3, "band_labels": ["Strong-Bear", "Weak-Bear", "Neutral",
                                       "Weak-Bull", "Strong-Bull"],
    "transition_row": [0.05, 0.1, 0.2, 0.45, 0.2], "persistence": 0.45,
    "horizons": [
        {"n": 5, "dist": [0.05, 0.1, 0.2, 0.4, 0.25], "p_buy": 0.25, "p_sell": 0.05, "e_score": 22.0},
        {"n": 10, "dist": [0.05, 0.1, 0.15, 0.4, 0.3], "p_buy": 0.30, "p_sell": 0.05, "e_score": 28.0},
        {"n": 20, "dist": [0.05, 0.1, 0.15, 0.35, 0.35], "p_buy": 0.35, "p_sell": 0.05, "e_score": 32.0},
    ],
    "drift": 8.0, "tilt": 6.0, "confidence": 0.7,
    "markov_adjusted_score": 44.0, "composite_daily": 24.0, "prior_version": "2026-06-21",
}


def test_markov_band_chip():
    assert trade.markov_band_chip(_MK)["label"] == "Weak-Bull"


def test_markov_metric_rows():
    rows = trade.markov_metric_rows(_MK)
    r10 = next(r for r in rows if r["horizon"] == "10d")
    assert r10["p_buy"] == "30%"


def test_markov_drift_row():
    r = trade.markov_drift_row(_MK)
    assert "+6" in r["tilt"] and "44" in r["adjusted"]


def test_markov_forecast_figure_shape():
    fig = trade.markov_forecast_figure(_MK)
    assert fig["chart"]["type"] == "area"
    assert len(fig["series"]) == 5


def test_markov_builders_tolerate_none():
    assert trade.markov_band_chip(None) is None
    assert trade.markov_metric_rows(None) == []
    assert trade.markov_drift_row(None) is None
    assert trade.markov_forecast_figure(None)["series"] == []


def test_markov_empty_horizons():
    mk = {"current_band": 2, "band_labels": ["a", "b", "c", "d", "e"], "horizons": []}
    assert trade.markov_metric_rows(mk) == []
    fig = trade.markov_forecast_figure(mk)
    assert fig["xAxis"]["categories"] == ["now"]
    assert all(len(s["data"]) == 1 for s in fig["series"])


def test_markov_band_out_of_range():
    chip = trade.markov_band_chip({"current_band": 9,
                                   "band_labels": ["a", "b", "c", "d", "e"]})
    assert chip["label"] == "?"


def test_markov_figure_category_data_alignment():
    fig = trade.markov_forecast_figure(_MK)  # _MK is the existing fixture in this file
    assert fig["xAxis"]["categories"] == ["now", "5d", "10d", "20d"]
    assert len(fig["series"]) == 5
    assert all(len(s["data"]) == 4 for s in fig["series"])
