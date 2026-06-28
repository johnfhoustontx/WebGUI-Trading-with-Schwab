"""Tests for the Trade page pure display builders + render API.

The engine orchestration lives in ``services/trade_svc/compute``; this page is a
Tier-3 reader, so only its pure transforms (verdict coloring, momentum/breakdown/
alignment rows) and the ``render`` callable are exercised here.
"""
from pages import trade


def test_verdict_text_class_maps_states():
    assert trade.verdict_text_class("BUY") == "text-[#2e7d32]"
    assert trade.verdict_text_class("buy") == "text-[#2e7d32]"
    assert trade.verdict_text_class("SELL") == "text-[#c62828]"
    assert trade.verdict_text_class("HOLD") == "text-[#f9a825]"
    assert trade.verdict_text_class(None) == "text-[#f9a825]"  # default amber


def test_bias_text_class_maps_states():
    assert trade.bias_text_class("BULLISH") == "text-[#2e7d32]"
    assert trade.bias_text_class("BEARISH") == "text-[#c62828]"
    assert trade.bias_text_class("NEUTRAL") == "text-[#f9a825]"
    assert trade.bias_text_class("") == "text-[#f9a825]"


def test_markov_band_bg_class_maps_5_bands():
    # one assertion per band edge — exact hexes from _MK_BAND_COLORS
    assert trade.markov_band_bg_class(0) == "bg-[#c0392b]"
    assert trade.markov_band_bg_class(1) == "bg-[#e67e22]"
    assert trade.markov_band_bg_class(2) == "bg-[#7f8c8d]"
    assert trade.markov_band_bg_class(3) == "bg-[#27ae60]"
    assert trade.markov_band_bg_class(4) == "bg-[#1e8449]"


def test_markov_band_bg_class_out_of_range():
    # mirrors markov_band_chip's neutral fallback (#7f8c8d).
    assert trade.markov_band_bg_class(9) == "bg-[#7f8c8d]"
    assert trade.markov_band_bg_class(-1) == "bg-[#7f8c8d]"


def test_momentum_rows_formats_and_handles_missing():
    rows = trade.momentum_rows({"rsi": 55.0, "adx": 22.4, "macd_hist": 0.4321,
                                "vwap": 211.0, "relative_volume": 1.34})
    d = dict(rows)
    assert d["RSI"] == "55.0"
    assert d["MACD histogram"] == "0.432"  # 3 decimals
    assert d["VWAP"] == "211.00"
    assert d["Relative Volume"] == "1.34"


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
    assert rows[0] == {"factor": "EMA alignment", "weight": 20,
                       "raw_score": 60, "contribution": 12.0}
    assert rows[1]["factor"] == "ADX"  # snake_case key humanized
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
    assert rows["Revenue growth"] == "20.0%"
    assert rows["ROE"] == "141.0%"
    assert rows["Margins"] == "expanding"
    assert "Earnings in" not in rows  # None days-to-earnings omitted


def test_fundamentals_rows_missing_and_contracting():
    rows = dict(trade.fundamentals_rows({
        "pe_ratio": None, "margin_expanding": False, "days_to_earnings": 12,
    }))
    assert rows["P/E"] == "—"
    assert rows["Revenue growth"] == "—"
    assert rows["Margins"] == "contracting"
    assert rows["Earnings in"] == "12 days"


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
    r10 = next(r for r in rows if r["horizon"] == "10 days")
    assert r10["p_buy"] == "30%"


def test_markov_drift_row():
    r = trade.markov_drift_row(_MK)
    assert "+6" in r["tilt"] and "44" in r["adjusted"]
    assert r["persistence"] == "45%"


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
    assert fig["xAxis"]["categories"] == ["now", "5 days", "10 days", "20 days"]
    assert len(fig["series"]) == 5
    assert all(len(s["data"]) == 4 for s in fig["series"])


def test_position_headline_prefers_markov():
    pv = {"verdict": "HOLD", "score": 38}
    mk = {"markov_adjusted_score": 44.0, "tilt": 6.0}
    head = trade.position_headline(pv, mk)
    assert head["score"] == 44 and head["base"] == 38 and "+6" in head["tilt"]


def test_position_headline_no_markov():
    head = trade.position_headline({"verdict": "BUY", "score": 41}, None)
    assert head["score"] == 41 and head["base"] == 41 and head["tilt"] == ""


def test_markov_figure_uses_dense_trajectory():
    mk = dict(_MK, trajectory=[
        {"n": 1, "dist": [0.00, 0.00, 0.10, 0.60, 0.30]},
        {"n": 2, "dist": [0.00, 0.05, 0.15, 0.50, 0.30]},
        {"n": 3, "dist": [0.05, 0.05, 0.20, 0.45, 0.25]},
        {"n": 5, "dist": [0.05, 0.10, 0.20, 0.40, 0.25]},
        {"n": 10, "dist": [0.05, 0.10, 0.15, 0.40, 0.30]},
        {"n": 20, "dist": [0.05, 0.10, 0.15, 0.35, 0.35]},
    ])
    fig = trade.markov_forecast_figure(mk)
    assert fig["xAxis"]["categories"] == ["now", "1 day", "2 days", "3 days",
                                          "5 days", "10 days", "20 days"]
    assert all(len(s["data"]) == 7 for s in fig["series"])
    assert fig["chart"]["backgroundColor"] == "transparent"  # dark theme


def test_markov_figure_falls_back_to_horizons_without_trajectory():
    # Back-compat: an older block with only `horizons` still renders 5/10/20.
    fig = trade.markov_forecast_figure(_MK)
    assert fig["xAxis"]["categories"] == ["now", "5 days", "10 days", "20 days"]
    assert all(len(s["data"]) == 4 for s in fig["series"])


def test_seed_symbol():
    assert trade.seed_symbol({"symbol": "TSLA"}) == "TSLA"
    assert trade.seed_symbol(None) == "AAPL"
    assert trade.seed_symbol({}) == "AAPL"


def test_should_request():
    # changed symbol fires immediately; an identical repeat within the window is
    # deduped (collapses the blur-then-click double fire); a repeat after the
    # window is a deliberate refresh; empty never fires.
    assert trade.should_request("TSLA", "AAPL", 0.1) is True
    assert trade.should_request("tsla", "TSLA", 0.1) is False
    assert trade.should_request("TSLA", "TSLA", 2.0) is True
    assert trade.should_request("   ", "AAPL", 9.0) is False


_SM = {
    "verdict": "BUY", "score": 0.636, "percentile": 90,
    "expected_fwd": 0.0135, "hit_rate": 0.523, "horizon_days": 20,
    "contributions": [
        {"factor": "mom_12_1", "z": 1.63, "weight": 0.211,
         "contribution": 0.343, "ic": 0.041},
        {"factor": "low_vol", "z": -0.88, "weight": -0.15,
         "contribution": 0.132, "ic": -0.066},
        {"factor": "turnover", "z": 0.40, "weight": 0.10,
         "contribution": 0.040, "ic": None},
    ],
    "model_version": "2026-06-28", "oos_ic": 0.0367, "source": "validated",
}


def test_swing_headline_verdict_and_line():
    head = trade.swing_headline(_SM)
    assert head["verdict"] == "BUY"
    assert "90th percentile" in head["line"]
    assert "+1.4% excess / 20 days" in head["line"]
    assert "52% beat-SPY" in head["line"]


def test_swing_headline_partial_fields():
    # Missing optional fields are simply omitted from the line (no crash).
    head = trade.swing_headline({"verdict": "HOLD", "percentile": 50})
    assert head["verdict"] == "HOLD"
    assert head["line"] == "50th percentile"


def test_swing_headline_none_tolerant():
    # None/empty both degrade to None (the page renders legacy-only in that case).
    assert trade.swing_headline(None) is None
    assert trade.swing_headline({}) is None


def test_swing_contrib_rows_formats_signed_and_ic():
    rows = trade.swing_contrib_rows(_SM)
    assert rows[0] == {"factor": "12-1 momentum", "z": "+1.63", "weight": "+0.211",
                       "contribution": "+0.343", "ic": "+0.041"}
    assert rows[1]["z"] == "-0.88" and rows[1]["weight"] == "-0.150"
    assert rows[1]["ic"] == "-0.066"
    assert rows[2]["ic"] == "—"  # None IC renders as a dash


def test_swing_contrib_rows_empty():
    assert trade.swing_contrib_rows(None) == []
    assert trade.swing_contrib_rows({}) == []
    assert trade.swing_contrib_rows({"contributions": []}) == []


def test_swing_model_meta():
    meta = trade.swing_model_meta(_SM)
    assert meta["version"] == "2026-06-28"
    assert meta["oos_ic"] == "+0.0367"


def test_swing_model_meta_missing_oos():
    meta = trade.swing_model_meta({"model_version": "x"})
    assert meta["version"] == "x" and meta["oos_ic"] == "—"


def test_swing_model_meta_none_tolerant():
    assert trade.swing_model_meta(None) is None


def test_markov_figure_differs_by_current_band():
    # Regression guard for "looks the same regardless of score": a bullish vs a
    # bearish near-term trajectory must produce different early series data.
    labels = _MK["band_labels"]
    bull = trade.markov_forecast_figure(
        {"current_band": 4, "band_labels": labels,
         "trajectory": [{"n": 1, "dist": [0.0, 0.0, 0.1, 0.3, 0.6]}]})
    bear = trade.markov_forecast_figure(
        {"current_band": 0, "band_labels": labels,
         "trajectory": [{"n": 1, "dist": [0.6, 0.3, 0.1, 0.0, 0.0]}]})
    assert bull["series"][4]["data"][0] == 1.0   # now one-hot at band 4 (Strong-Bull)
    assert bear["series"][0]["data"][0] == 1.0   # now one-hot at band 0 (Strong-Bear)
    assert bull["series"][4]["data"] != bear["series"][4]["data"]


def test_days_whole_words():
    assert trade._days(1) == "1 day"      # singular
    assert trade._days(20) == "20 days"
    assert trade._days(0) == "0 days"


def test_humanize_factor_known_and_fallback():
    assert trade.humanize_factor("mom_12_1") == "12-1 momentum"
    assert trade.humanize_factor("rs_vs_sector") == "Relative strength vs sector"
    assert trade.humanize_factor("rsi") == "RSI"            # trader acronym kept verbatim
    assert trade.humanize_factor("growth_quality") == "Growth quality"
    assert trade.humanize_factor("some_new_key") == "Some new key"  # underscores→spaces fallback
    assert trade.humanize_factor("") == ""
    assert trade.humanize_factor(None) == ""


def test_humanize_reason_swaps_leading_key():
    assert trade.humanize_reason("growth_quality (+16)") == "Growth quality (+16)"
    assert trade.humanize_reason("rs_vs_sector (-10)") == "Relative strength vs sector (-10)"
    # a reason that is not "<key> (+score)" is returned unchanged
    assert trade.humanize_reason("Insufficient fundamental data") == "Insufficient fundamental data"
    assert trade.humanize_reason("") == ""
    assert trade.humanize_reason(None) is None
