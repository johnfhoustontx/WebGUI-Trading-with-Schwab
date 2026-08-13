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


def test_should_open_tab():
    # open only when a request is pending AND the cache version advanced past the
    # baseline captured at click time (so a page-load with a stale result never opens).
    assert trade.should_open_tab(pending=True, version=5, baseline=4) is True
    assert trade.should_open_tab(pending=True, version=4, baseline=4) is False
    assert trade.should_open_tab(pending=False, version=9, baseline=4) is False
    assert trade.should_open_tab(pending=True, version=None, baseline=None) is False


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


def test_swing_tilt_maps_verdict_to_ranked_tilt():
    # The validated model's BUY/SELL/HOLD renders as a cross-sectional RANK plus a
    # mild directional tilt — never a bold trade call (the measured edge is thin).
    assert trade.swing_tilt(_SM) == ("90th percentile · slight bullish tilt", "pos")
    assert trade.swing_tilt({"verdict": "SELL", "percentile": 8}) == (
        "8th percentile · slight bearish tilt", "neg")
    assert trade.swing_tilt({"verdict": "HOLD", "percentile": 50}) == (
        "50th percentile · no clear edge", "neutral")
    # lowercase verdicts normalize the same way
    assert trade.swing_tilt({"verdict": "buy", "percentile": 90})[1] == "pos"


def test_swing_tilt_unranked_without_percentile():
    # A missing percentile degrades to "unranked" — the tilt still reads.
    assert trade.swing_tilt({"verdict": "BUY"}) == ("unranked · slight bullish tilt", "pos")
    assert trade.swing_tilt({}) == ("unranked · no clear edge", "neutral")
    assert trade.swing_tilt(None) == ("unranked · no clear edge", "neutral")


def test_tilt_text_class_maps_tones():
    assert trade.tilt_text_class("pos") == "text-[#2e7d32]"
    assert trade.tilt_text_class("neg") == "text-[#c62828]"
    assert trade.tilt_text_class("neutral") == "text-[#f9a825]"
    assert trade.tilt_text_class("nonsense") == "text-[#f9a825]"  # default amber
    assert trade.tilt_text_class(None) == "text-[#f9a825]"


def test_swing_headline_tilt_and_line():
    head = trade.swing_headline(_SM)
    # the percentile lives in `tilt` (the ranked read), NOT in `line`
    assert head["tilt"] == "90th percentile · slight bullish tilt"
    assert head["tone"] == "pos"
    assert "+1.4% excess / 20 days" in head["line"]
    assert "52% beat-SPY" in head["line"]


def test_swing_headline_partial_fields():
    # Missing optional fields are simply omitted from the line (no crash); the
    # ranked tilt still renders.
    head = trade.swing_headline({"verdict": "HOLD", "percentile": 50})
    assert head["tilt"] == "50th percentile · no clear edge"
    assert head["tone"] == "neutral"
    assert head["line"] == ""


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


def test_model_staleness_warns_when_old():
    import datetime as dt
    today = dt.date(2026, 9, 1)
    # 2026-06-28 fit is 65 days before 2026-09-01 → stale
    warn = trade.model_staleness("2026-06-28", today=today, threshold_days=60)
    assert "65 days old" in warn and "fit_swing_model.py" in warn


def test_model_staleness_silent_when_fresh_or_unparseable():
    import datetime as dt
    today = dt.date(2026, 7, 10)
    assert trade.model_staleness("2026-06-28", today=today, threshold_days=60) == ""  # 12 days
    assert trade.model_staleness("?", today=today) == ""       # unparseable → no false warn
    assert trade.model_staleness(None, today=today) == ""


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
