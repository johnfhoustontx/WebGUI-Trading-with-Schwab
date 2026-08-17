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
                               "evidence": ["ADX 64 rising"]},
                              {"trend": {"score": 64, "confidence": 1.0}},
                              [{"trend": 64}])
    assert "Bull" in out and "ADX 64 rising" in out and "direction" in out.lower()


def test_sentiment_panel_handles_missing():
    out = ms.sentiment_panel_html({}, [], [])
    assert out.startswith("<div") and "Sentiment" in out


def test_sentiment_panel_coerces_numeric_string_score():
    # sentiment_svc publishes total_score as a formatted STRING (e.g. "7.80")
    out = ms.sentiment_panel_html({"total_score": "7.80", "bias": "Bullish"}, [], [])
    # 7.8 is the DAY centre. Week/Month legitimately read an em-dash here — the
    # call passes no history — so the old blanket ">—<" guard would now fire on
    # correct output. Assert the centre carries the score instead.
    assert "7.8" in out and "Bullish" in out


def test_sentiment_panel_bad_score_is_placeholder():
    for bad in ("n/a", True, None):
        out = ms.sentiment_panel_html({"total_score": bad}, [], [])
        assert out.startswith("<div")  # no raise
    assert "—" in ms.sentiment_panel_html({"total_score": "n/a"}, [], [])


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


def test_regime_panel_transition_renders_display_labels_not_raw_keys():
    """The pushed snapshot showed the internal keys ("mean_reversion -> trending")
    where every other surface shows words."""
    out = ms.regime_panel_html(
        {"label": "Trending", "committed_label": "trending", "confidence": 0.6,
         "transition": {"from": "mean_reversion", "to": "trending", "progress": 0.6}}, [])
    assert "mean_reversion" not in out
    assert "Balanced" in out


def test_regime_panel_transition_carries_the_direction():
    out = ms.regime_panel_html(
        {"label": "Rallying", "committed_label": "trending", "confidence": 0.6,
         "direction": 1, "direction_strong": True,
         "transition": {"from": "mean_reversion", "to": "trending", "progress": 0.6}}, [])
    assert "Balanced" in out and "Rallying" in out


def test_regime_panel_unknown_transition_key_still_renders():
    out = ms.regime_panel_html(
        {"label": "Trending", "confidence": 0.6,
         "transition": {"from": "nonsense", "to": "trending", "progress": 0.6}}, [])
    assert "nonsense" in out and "Trending" in out


# ── the 2026-08-16 redesign: rings, ranked regime rows, board-accurate tiles ──

def test_ring_draws_track_only_for_a_horizon_with_no_reading():
    """The whole point of replacing the semicircular gauge: a horizon with no
    usable reading must be SAYABLE. A needle can only ever point somewhere."""
    svg = ms.ring_svg([{"value": 64, "caption": "DAY"},
                       {"value": None, "caption": "WEEK"},
                       {"value": 53, "caption": "MONTH"}], ms._TREND_BANDS)
    assert svg.count(f'stroke="{ms.RING_TRACK}"') == 3      # three tracks always
    assert "—" in svg                                        # the empty horizon
    assert ">64<" in svg and ">53<" in svg


def test_trend_arcs_treat_zero_confidence_as_no_reading():
    """sentiment_svc publishes a zero-confidence neutral 50 after a fetch
    failure. Drawing that as a real 50 is precisely the confident-wrong-number
    the ring design exists to avoid."""
    arcs = ms.trend_arcs({"trend": {"score": 64, "confidence": 1.0},
                          "trend_7d": {"score": 50, "confidence": 0},
                          "trend_30d_ago": {"score": 89, "confidence": 0.8}})
    assert [a["value"] for a in arcs] == [64.0, None, 89.0]


def test_sentiment_arcs_use_five_sessions_for_week_and_all_for_month():
    snaps = [{"composite": {"total_score": v}} for v in (1, 2, 3, 4, 5, 6, 7)]
    arcs = ms.sentiment_arcs({"total_score": 8.0}, snaps)
    assert arcs[0]["value"] == 80.0                          # day
    assert arcs[1]["value"] == 50.0                          # mean(3,4,5,6,7)
    assert arcs[2]["value"] == 40.0                          # mean(1..7)


def test_regime_rows_rank_by_share_and_report_change_since_open():
    pts = [{"memberships": {"trending": 0.5, "choppy": 0.3, "crisis": 0.2}},
           {"memberships": {"trending": 0.2, "choppy": 0.5, "crisis": 0.3}}]
    rows = ms.regime_rows(pts)
    assert [r[0] for r in rows] == ["choppy", "crisis", "trending"]
    assert rows[0][1] == 0.5 and abs(rows[0][2] - 0.2) < 1e-9


def test_lead_margin_reports_now_and_the_tightest_of_the_session():
    """`unclear` measures evidence strength, not how close the top two are — so
    without this the headline can be a coin toss and nothing says so."""
    pts = [{"memberships": {"a": 0.40, "b": 0.39}},      # 1pp — tightest
           {"memberships": {"a": 0.50, "b": 0.30}}]      # 20pp — now
    now, tightest = ms.lead_margin(pts)
    assert abs(now - 0.20) < 1e-9 and abs(tightest - 0.01) < 1e-9


def test_regime_panel_never_prints_raw_contract_keys():
    out = ms.regime_panel_html(
        {"label": "Whipsaw", "confidence": 0.56},
        [{"memberships": {"choppy": 0.4, "mean_reversion": 0.3, "trending": 0.3}}])
    assert "Whipsaw" in out and "Balanced" in out
    for raw in ("mean_reversion", "choppy", "crisis"):
        assert raw not in out


def test_tile_lines_match_the_board_for_the_valueless_special_tiles():
    """Net Prem and BIG10 carry NO `last`. Reading `last` naively renders them
    blank — which is what the pre-redesign snapshot did."""
    assert ms._tile_lines({"net_prem": True, "skew_pct": 35.7, "net_m": 2438.49}) == (
        "Call 36%", "+$2.44B", "DOLLAR-WEIGHTED CALL/PUT")
    assert ms._tile_lines({"basket": True, "avg_pct": 0.0568,
                           "breadth_text": "5/10 up", "prem_skew_pct": 42.6}) == (
        "+0.06%", "5/10 up", "Call 43%")


def test_every_tile_is_the_same_size_in_the_pushed_image():
    """Matches the board's fixed 152x94. A min-width would let tiles stretch to
    fill their row, which is the defect this mirrors the fix for."""
    assert "width:152px" in ms._MS_CSS and "height:94px" in ms._MS_CSS
    assert "flex:0 0 152px" in ms._MS_CSS
