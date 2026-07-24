from services.options_svc import market_snapshot as ms


# --- Task 1: gauge SVG ---

def test_gauge_svg_marker_position_scales_with_value():
    lo = ms.gauge_svg(0, vmin=0, vmax=100, bands=[(30, "#e05252"), (70, "#e0c452"), (100, "#3fb36b")], value_label="0", caption="Bear")
    hi = ms.gauge_svg(100, vmin=0, vmax=100, bands=[(30, "#e05252"), (70, "#e0c452"), (100, "#3fb36b")], value_label="100", caption="Bull")
    assert lo.startswith("<svg") and hi.startswith("<svg")
    assert "0" in lo and "100" in hi and "Bull" in hi
    # needle angle differs across the range (value drives the transform)
    assert lo != hi


def test_gauge_svg_clamps_out_of_range():
    # value below vmin / above vmax must not crash or overshoot the arc
    ms.gauge_svg(-20, vmin=0, vmax=100, bands=[(100, "#3fb36b")], value_label="—", caption="x")
    ms.gauge_svg(999, vmin=0, vmax=100, bands=[(100, "#3fb36b")], value_label="—", caption="x")


# --- Task 2: sparkline + regime-mix SVG ---

def test_sparkline_svg_empty_points_is_placeholder():
    out = ms.sparkline_svg([], key="trend", vmin=0, vmax=100, bands=[(30, "#e05252"), (70, "#e0c452"), (100, "#3fb36b")])
    assert out.startswith("<svg") and "no data" in out.lower()


def test_sparkline_svg_draws_polyline_over_points():
    pts = [{"trend": 40}, {"trend": 55}, {"trend": 62}]
    out = ms.sparkline_svg(pts, key="trend", vmin=0, vmax=100, bands=[(30, "#e05252"), (70, "#e0c452"), (100, "#3fb36b")])
    assert out.count("<polyline") >= 1 or out.count("<path") >= 1


def test_regime_mix_svg_empty_is_placeholder():
    assert "no data" in ms.regime_mix_svg([]).lower()


def test_regime_mix_svg_stacks_membership_bands():
    pts = [{"memberships": {"mean_reversion": 0.6, "trending": 0.2, "breakout": 0.1, "choppy": 0.05, "crisis": 0.05}}]
    out = ms.regime_mix_svg(pts)
    assert out.startswith("<svg") and "<rect" in out


def test_regime_mix_svg_tolerates_non_numeric_membership():
    # a malformed membership value must degrade, not raise
    pts = [{"memberships": {"mean_reversion": "oops", "trending": 0.6, "breakout": 0.1, "choppy": 0.05, "crisis": 0.05}}]
    out = ms.regime_mix_svg(pts)
    assert out.startswith("<svg")


def test_regime_mix_svg_skips_non_dict_points():
    pts = [None, {"memberships": {"mean_reversion": 0.6, "trending": 0.2, "breakout": 0.1, "choppy": 0.05, "crisis": 0.05}}]
    out = ms.regime_mix_svg(pts)
    assert out.startswith("<svg") and "<rect" in out


def test_sparkline_svg_skips_non_dict_points():
    out = ms.sparkline_svg([None, {"trend": 55}], key="trend", vmin=0, vmax=100, bands=[(100, "#3fb36b")])
    assert out.startswith("<svg")


# --- Task 3: dashboard tile-grid HTML ---

def test_dashboard_grid_html_frames_and_tiles():
    cats = [{"category": "Volatility", "tiles": [
        {"display": "VIX", "description": "CBOE VIX", "last": 14.2, "change_pct": 3.6,
         "color_state": "risk_off_strong", "value_only": False}]}]
    out = ms.dashboard_grid_html(cats)
    assert "Volatility" in out and "VIX" in out and "risk_off_strong" not in out  # class mapped, not raw
    assert "3.6" in out


def test_dashboard_grid_html_empty():
    assert "no data" in ms.dashboard_grid_html([]).lower()


def test_dashboard_grid_html_skips_non_dict_elements():
    cats = [None, {"category": "Volatility", "tiles": [
        None, {"display": "VIX", "last": 14.2, "change_pct": 3.6, "color_state": "risk_off_strong"}]}]
    out = ms.dashboard_grid_html(cats)
    assert "Volatility" in out and "VIX" in out


# --- Task 4: panels + full doc ---

def test_trend_panel_shows_label_explainer_and_live_read():
    out = ms.trend_panel_html({"score": 64, "label": "Bull", "description": "Trending up",
                               "evidence": ["ADX 64 rising"]}, [{"trend": 64}])
    assert "Bull" in out and "ADX 64 rising" in out and "direction" in out.lower()


def test_sentiment_panel_handles_missing():
    out = ms.sentiment_panel_html({}, [])
    assert out.startswith("<div") and "Sentiment" in out


def test_sentiment_panel_coerces_numeric_string_score():
    # sentiment_svc publishes total_score as a formatted STRING (e.g. "7.80")
    out = ms.sentiment_panel_html({"total_score": "7.80", "bias": "Bullish"}, [])
    assert "7.8" in out and ">—<" not in out


def test_sentiment_panel_bad_score_is_placeholder():
    for bad in ("n/a", True, None):
        out = ms.sentiment_panel_html({"total_score": bad}, [])
        assert out.startswith("<div")  # no raise
    assert "—" in ms.sentiment_panel_html({"total_score": "n/a"}, [])


def test_regime_panel_shows_transition_when_present():
    out = ms.regime_panel_html(
        {"label": "Trending", "committed_label": "trending", "confidence": 0.6,
         "memberships": {"mean_reversion": 0.2, "trending": 0.6, "breakout": 0.1, "choppy": 0.05, "crisis": 0.05},
         "transition": {"from": "mean_reversion", "to": "trending", "progress": 0.6}}, [])
    assert "Trending" in out and ("→" in out or "-&gt;" in out or "to" in out.lower())


def test_market_snapshot_doc_is_self_contained():
    doc = ms.market_snapshot_doc({"categories": []}, {}, {}, {}, {"points": []}, {"points": []}, subtitle="09:00 CT")
    assert doc.lstrip().lower().startswith("<!doctype") or "<html" in doc.lower()
    assert "Market Read" in doc and "09:00 CT" in doc


def test_market_snapshot_doc_is_titled_market_snapshot_not_gamma():
    # Regression: the doc reuses the gamma dark-doc CSS but must be titled
    # "Market Snapshot", never "Gamma Analysis" (that header is gamma-specific).
    doc = ms.market_snapshot_doc({"categories": []}, {}, {}, {}, {"points": []}, {"points": []}, subtitle="x")
    assert "<title>Market Snapshot</title>" in doc
    assert 'class="ga-title">Market Snapshot<' in doc
    assert "Gamma Analysis" not in doc
