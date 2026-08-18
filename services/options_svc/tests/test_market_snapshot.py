"""Tests for the pushed Market Snapshot doc: the Macro Board grid, the
Day/Week/Month horizon values, and the assembled document.

The Market Read section's own builders live in ``market_console`` and are tested
in ``test_market_console.py``. The 2026-08-16 panel builders (semicircular gauge,
value sparkline, stacked regime-mix SVG, the three ``*_panel_html`` functions)
were DELETED when the Market Read was rebuilt as the Market Regime Console — the
tests that pinned them went with their subjects.
"""
from services.options_svc import market_snapshot as ms


# --- dashboard tile-grid HTML (unchanged by the console rebuild) ------------

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


# --- Day / Week / Month horizon values --------------------------------------

def test_trend_arcs_treat_zero_confidence_as_no_reading():
    """sentiment_svc publishes a zero-confidence neutral 50 after a fetch
    failure. Drawing that as a real 50 is precisely the confident-wrong-number
    this rule exists to avoid — and an absent-KEY guard would miss it, because
    the failure path publishes a fully shaped dict."""
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


def test_sentiment_arcs_are_all_on_the_zero_to_hundred_scale():
    """The bug this section was rebuilt to kill: the old ring put the 0-10
    composite ("5.0") in its centre while its own WEEK/MONTH legend read 0-100
    (57 / 53) — three numbers on one dial on two different scales."""
    snaps = [{"composite": {"total_score": 5.7}}, {"composite": {"total_score": 5.3}}]
    arcs = ms.sentiment_arcs({"total_score": "5.04"}, snaps)
    assert [round(a["value"], 1) for a in arcs] == [50.4, 55.0, 55.0]


def test_sentiment_arcs_coerce_the_string_score_sentiment_svc_publishes():
    assert ms.sentiment_arcs({"total_score": "7.80"}, [])[0]["value"] == 78.0


def test_sentiment_arcs_bad_score_reads_no_data_not_zero():
    for bad in ("n/a", True, None):
        assert ms.sentiment_arcs({"total_score": bad}, [])[0]["value"] is None


def test_prev_total_is_the_last_scored_session():
    snaps = [{"composite": {"total_score": 6.1}}, {"composite": {"total_score": 0}},
             {"composite": {"total_score": 5.16}}]
    assert ms.prev_total(snaps) == 5.16
    assert ms.prev_total([]) is None
    assert ms.prev_total([{"composite": {"total_score": 0}}]) is None


# --- the ctx handed to the console ------------------------------------------

def test_console_context_matches_the_pages_ctx_keys():
    """The ctx is deliberately the SAME SHAPE ``console_page.apply`` takes on
    Tier 1, so the two assemblies stay comparable key-for-key."""
    ctx = ms.console_context({}, {}, {}, {}, {}, [])
    for key in ("sent_arcs", "trend_arcs", "bias", "total", "confidence",
                "trend_short", "trend_verdict", "trend_guidance", "signal_rows",
                "velocity_values", "divergence_detail", "regime",
                "regime_points", "as_of"):
        assert key in ctx, key


def test_console_context_reads_the_live_payload_shapes():
    derived = {"trend": {"score": 57.9, "confidence": 0.72, "state": "neutral",
                         "label": "Neutral", "description": "Balance — sell premium."},
               "trend_7d": {"score": 79.8, "confidence": 0.35},
               "trend_30d_ago": {"score": 88.8, "confidence": 0.35},
               "size": "1.00x", "bias": "Neutral", "signal": "Neutral",
               "velocity": {"values": {"roc_3d": -0.69}},
               "divergence_detail": {"high": {"name": "VIX Complex", "score": 8.0},
                                     "low": {"name": "Market Breadth", "score": 2.0}}}
    sentiment = {"total_score": "5.04", "bias": "Neutral",
                 "aggregate_confidence": 0.9}
    ctx = ms.console_context({}, sentiment, {"label": "Trending"},
                             {"points": [{"memberships": {"trending": 0.4}}]},
                             derived, [{"composite": {"total_score": 5.16}}],
                             composite_at="2026-08-18T01:31:28+00:00")
    assert ctx["total"] == "5.04" and ctx["confidence"] == 0.9
    assert ctx["trend_short"] == "Neutral" and ctx["trend_verdict"] == "Neutral"
    assert [round(a["value"], 1) for a in ctx["trend_arcs"]] == [57.9, 79.8, 88.8]
    assert ctx["as_of"] == "2026-08-18T01:31:28+00:00"
    assert len(ctx["regime_points"]) == 1
    yest = {r["key"]: r["value"] for r in ctx["signal_rows"]}
    assert yest["yesterday"] == "5.16" and yest["change"] == "-0.12"


def test_console_context_shows_dashes_when_the_band_labels_are_absent():
    """size/bias/signal arrive together or not at all — a cold cache must show
    three em-dashes, not a half-populated row."""
    rows = {r["key"]: r["value"]
            for r in ms.console_context({}, {}, {}, {}, {}, [])["signal_rows"]}
    assert rows["bias"] == "—" and rows["signal"] == "—"


# --- the full document -------------------------------------------------------

def test_market_snapshot_doc_is_self_contained():
    doc = ms.market_snapshot_doc({"categories": []}, {}, {}, {}, {"points": []},
                                 {"points": []}, subtitle="09:00 CT")
    assert doc.lstrip().lower().startswith("<!doctype") or "<html" in doc.lower()
    assert "MARKET READ" in doc and "09:00 CT" in doc


def test_market_snapshot_doc_is_titled_market_snapshot_not_gamma():
    # Regression: the doc reuses the gamma dark-doc CSS but must be titled
    # "Market Snapshot", never "Gamma Analysis" (that header is gamma-specific).
    doc = ms.market_snapshot_doc({"categories": []}, {}, {}, {}, {"points": []},
                                 {"points": []}, subtitle="x")
    assert "<title>Market Snapshot</title>" in doc
    assert 'class="ga-title">Market Snapshot<' in doc
    assert "Gamma Analysis" not in doc


def test_market_snapshot_doc_widens_the_gamma_wrapper():
    """The doc inherits the gamma briefing's `.ga` cap of max-width:860px. Lose
    the override and every board frame wraps onto its own row, running the image
    ~7700px tall."""
    doc = ms.market_snapshot_doc({"categories": []}, {}, {}, {}, {}, {})
    assert ".ga{max-width:1380px}" in doc


def test_market_snapshot_doc_renders_the_three_console_sections():
    """The whole point of the rebuild: the Market Read is the console, not three
    ring-and-sparkline panels."""
    doc = ms.market_snapshot_doc(
        {"categories": []},
        {"score": 58, "confidence": 0.7, "label": "Neutral"},
        {"total_score": "5.04", "bias": "Neutral", "aggregate_confidence": 0.9},
        {"label": "Trending", "confidence": 0.62},
        {"points": []},
        {"points": [{"ts": 1, "memberships": {"trending": 0.4, "crisis": 0.2}},
                    {"ts": 2, "memberships": {"trending": 0.5, "crisis": 0.1}}]},
        subtitle="09:00 CT",
        derived={"trend": {"score": 58, "confidence": 0.7, "label": "Neutral",
                           "state": "neutral"}})
    for section in ("MARKET SENTIMENT", "MARKET TREND", "SIGNALS",
                    "REGIME IDENTIFIED", "REGIME SHARE", "DIAGNOSTIC TAGS",
                    "DOMINANT"):
        assert section in doc, section
    # …and the panels it replaced are gone.
    assert "ms-panel" not in doc and "Daily Market Sentiment" not in doc


def test_market_snapshot_doc_never_raises_on_junk_payloads():
    ms.market_snapshot_doc(None, None, None, None, None, None,
                           derived=None, snaps=None)
    ms.market_snapshot_doc({"categories": [None]}, "x", 3, [], {"points": None},
                           {"points": [None, "x"]}, derived="nope", snaps="nope")
