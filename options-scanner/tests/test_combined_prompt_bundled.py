"""Tests for build_combined_prompt_bundled - the multi-symbol prompt builder."""
from gamma_tool import build_combined_prompt_bundled, build_summary_prompt_bundled


def _stub_view_dict(symbol, spot, view_label="GEX"):
    """Minimal analysis dict that build_block can consume."""
    return {
        "view": view_label,
        "symbol": symbol,
        "spot": spot,
        "dte": 0,
        "expected_move": None,
        "em_upper": None,
        "em_lower": None,
        "timestamp": "10:00 AM CT",
        "hours_to_close": 5.0,
        "top_positive": [{"strike": spot + 5, "value": 1.0e9}],
        "top_negative": [{"strike": spot - 5, "value": -1.0e9}],
        "tail_summary": {"count_pos": 0, "count_neg": 0, "sum_pos": 0, "sum_neg": 0},
        "delta_change": {},
        "value_at_open": {},
        "pressure_panel": None,
        "flip_point": spot,
        "net_by_zone": {"above_0_2pct": 0, "below_0_2pct": 0, "below_2_5pct": 0},
        "atm_breakdown": [{"strike": spot, "call": 0, "put": 0, "net": 0}],
        "grouping": 1,
    }


def _stub_blocks(symbol, spot):
    return {
        "gex":   _stub_view_dict(symbol, spot, "GEX"),
        "charm": _stub_view_dict(symbol, spot, "Charm"),
        "dex":   _stub_view_dict(symbol, spot, "DEX"),
    }


def test_intraday_bundled_three_symbols():
    out = build_combined_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=False,
    )
    assert "=== SPX ===" in out
    assert "=== SPY ===" in out
    assert "=== QQQ ===" in out
    assert "yesterday's closing" not in out
    # No PNG references
    assert "chart attached" not in out and "PNG" not in out


def test_premarket_bundle_has_carryover_headers():
    out = build_combined_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=True,
    )
    assert "=== SPX (yesterday's closing gamma profile) ===" in out
    assert "=== SPY (yesterday's closing gamma profile) ===" in out
    assert "=== QQQ (yesterday's closing gamma profile) ===" in out
    assert "overnight" in out.lower()
    # Premarket ASK should mention scheduled events / calendar
    assert "scheduled" in out.lower() or "calendar" in out.lower() or "events" in out.lower()


def test_failed_symbol_omitted_with_note():
    out = build_combined_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        None,
        _stub_blocks("QQQ", 500.0),
        premarket=False,
    )
    assert "=== SPX ===" in out
    assert "SPY: fetch failed" in out
    assert "=== QQQ ===" in out


def test_all_three_failed_returns_minimal_or_raises():
    """If all symbols failed, behavior should be defined.

    Either raises ValueError (preferred - caller shouldn't bundle nothing),
    or returns a prompt that flags the catastrophic failure.
    """
    import pytest
    with pytest.raises(ValueError):
        build_combined_prompt_bundled(None, None, None, premarket=False)


def test_summary_bundled_intraday():
    out = build_summary_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=False,
    )
    assert "SPX" in out and "SPY" in out and "QQQ" in out
    # Summary is shorter than the deep-dive bundled prompt.
    deep = build_combined_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=False,
    )
    assert len(out) < len(deep)


def test_summary_bundled_premarket():
    out = build_summary_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=True,
    )
    assert "overnight" in out.lower()
    assert "yesterday's closing" in out


def test_summary_bundled_failed_symbol():
    out = build_summary_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        None,
        _stub_blocks("QQQ", 500.0),
        premarket=False,
    )
    assert "SPY: fetch failed" in out


def test_summary_bundled_all_failed_raises():
    import pytest
    with pytest.raises(ValueError):
        build_summary_prompt_bundled(None, None, None, premarket=False)


def test_bundled_prompt_renders_internals_when_provided():
    out = build_combined_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=False,
        internals={"cpce": 0.78, "advn": 1450, "decn": 1602, "skew": 138.4},
    )
    assert "=== MARKET INTERNALS ===" in out
    assert "0.78" in out


def test_bundled_prompt_renders_eod_probs_per_symbol():
    spx = _stub_blocks("SPX", 5800.0)
    for view in ("gex", "charm", "dex"):
        spx[view]["eod_probabilities"] = {
            "touch_em_upper": 0.42, "touch_em_lower": 0.38,
            "reach_pos_wall": 0.55, "reach_neg_wall": 0.31}
    out = build_combined_prompt_bundled(spx, None, None, premarket=False)
    assert "EOD probability" in out
    assert "42%" in out


def test_eod_probs_rendered_once_per_symbol():
    spx = _stub_blocks("SPX", 5800.0)
    for view in ("gex", "charm", "dex"):
        spx[view]["eod_probabilities"] = {
            "touch_em_upper": 0.42, "touch_em_lower": 0.38,
            "reach_pos_wall": 0.55, "reach_neg_wall": 0.31}
    out = build_combined_prompt_bundled(spx, None, None, premarket=False)
    # The header line should appear exactly once for SPX
    assert out.count("EOD probability of touching:") == 1


def test_bundled_summary_accepts_internals():
    out = build_summary_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=False,
        internals={"cpce": 0.78, "advn": 1450, "decn": 1602, "skew": 138.4},
    )
    assert "MARKET INTERNALS" in out


def test_intraday_ask_uses_plain_english_structure():
    out = build_combined_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=False,
    )
    assert "BIG PICTURE" in out
    assert "KEY LEVELS" in out
    assert "WHAT IF" in out
    assert "WHY IS THIS HAPPENING" in out
    assert "RED FLAGS" in out and "GREEN FLAGS" in out
    assert ("regular investor" in out.lower() or "plain english" in out.lower())
    assert "microstructure" not in out
    assert "Greeks" not in out


def test_premarket_ask_uses_plain_english_structure():
    out = build_combined_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=True,
    )
    assert "BIG PICTURE" in out
    assert "overnight" in out.lower()
    assert "microstructure" not in out


def test_summary_ask_shorter_with_plain_english():
    detail = build_combined_prompt_bundled(
        _stub_blocks("SPX", 5800.0), _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0), premarket=False)
    summary = build_summary_prompt_bundled(
        _stub_blocks("SPX", 5800.0), _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0), premarket=False)
    assert len(summary) < len(detail)
    assert "BIG PICTURE" in summary
    assert "KEY LEVELS" in summary


def test_1500_prompt_includes_retrospective():
    path_block = "=== TODAY'S PATH ===\nSPX: spot trail: 5790.00 (08:20) -> 5814.00 (1500)\n"
    out = build_combined_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=False,
        slot_tag="1500",
        todays_path_block=path_block,
    )
    assert "TODAY'S PATH" in out
    assert "ANALYSIS REVIEW" in out
    assert "spot trail" in out


def test_non_1500_prompt_omits_retrospective():
    out = build_combined_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=False,
        slot_tag="1000",
    )
    assert "TODAY'S PATH" not in out
    assert "ANALYSIS REVIEW" not in out


def test_hedge_pressure_line_rendered_per_symbol():
    spx = _stub_blocks("SPX", 5800.0)
    # Inject pressure_panel into the DEX view
    spx["dex"]["pressure_panel"] = {
        "delta_now": 1.0e9,
        "projected_close": 1.2e9,
        "hedge_pressure": 800_000_000.0,
        "hedge_direction": "buying",
        "projected_flip": 5810.0,
    }
    spy = _stub_blocks("SPY", 580.0)
    spy["dex"]["pressure_panel"] = {
        "delta_now": -5.0e8,
        "projected_close": -6.0e8,
        "hedge_pressure": -300_000_000.0,
        "hedge_direction": "selling",
        "projected_flip": 580.0,
    }
    out = build_combined_prompt_bundled(
        spx, spy, _stub_blocks("QQQ", 500.0), premarket=False)
    # SPX hedge pressure line present with interpretation
    assert "Hedge pressure:" in out
    # Plain-English interpretations for both directions
    assert "buy" in out.lower()
    assert "sell" in out.lower()


def test_hedge_pressure_interpretation_buying_is_bullish():
    from gamma_tool import _hedge_pressure_interpretation
    text = _hedge_pressure_interpretation(800_000_000.0, "buying")
    assert "buy" in text.lower()
    # Should reference upward / bullish flavor in plain English
    assert any(w in text.lower() for w in ("upward", "bullish", "supportive", "lift"))


def test_hedge_pressure_interpretation_selling_is_bearish():
    from gamma_tool import _hedge_pressure_interpretation
    text = _hedge_pressure_interpretation(-300_000_000.0, "selling")
    assert "sell" in text.lower()
    assert any(w in text.lower() for w in ("downward", "bearish", "weigh", "drag"))


def test_hedge_pressure_omitted_when_no_pressure_panel():
    """Symbols with no pressure_panel (DEX failed) should not crash or
    emit a stray Hedge pressure line."""
    spx = _stub_blocks("SPX", 5800.0)
    # No pressure_panel set on dex
    spx["dex"]["pressure_panel"] = None
    out = build_combined_prompt_bundled(spx, None, None, premarket=False)
    # The line should be absent for SPX (no fake "Hedge pressure: None")
    # Other symbols are None entirely, so they're "fetch failed - section omitted"
    assert "Hedge pressure: None" not in out
    assert "Hedge pressure: n/a" not in out


def test_1500_without_path_block_still_includes_review_ask():
    """If we're in the 1500 slot but no earlier JSONs exist, the path
    block is None but the ASK should still include the review section
    so the AI can talk about what little we have."""
    out = build_combined_prompt_bundled(
        _stub_blocks("SPX", 5800.0), None, None,
        premarket=False, slot_tag="1500", todays_path_block=None,
    )
    assert "ANALYSIS REVIEW" in out
    assert "TODAY'S PATH" not in out


# ── Dealer-positioning metrics (FlashAlpha quick wins in the AI prompt) ──

def _enrich_with_metrics(blocks):
    """Attach the six symbol-level metric blocks to every view dict."""
    metrics = {
        "max_pain": {"max_pain": 5800.0, "pin_risk": 0.62,
                     "magnet": {"level": 5800.0, "agree": True, "confidence": 0.8}},
        "walls": {"gex": {"call_wall": 5850.0, "put_wall": 5750.0},
                  "oi": {"call_wall": 5860.0, "put_wall": 5740.0}},
        "pc_ratios": {"pc_oi": 1.35, "pc_volume": 0.92},
        "oi_concentration": {"hhi": 0.21, "n_strikes": 14},
        "hedge_shares": -48000.0,
        "gamma_acceleration": {"ratio": 2.7, "dte_near": 0, "dte_far": 7},
    }
    for v in blocks.values():
        v.update(metrics)
    return blocks


def test_combined_prompt_includes_dealer_positioning():
    spx = _enrich_with_metrics(_stub_blocks("SPX", 5800.0))
    out = build_combined_prompt_bundled(spx, None, None, premarket=False)
    assert "DEALER POSITIONING" in out
    assert "Max pain" in out
    assert "Call wall" in out and "Put wall" in out
    assert "P/C OI" in out
    assert "Gamma accel" in out


def test_combined_prompt_omits_block_when_no_metrics():
    # Bare stub has none of the metric keys -> no DEALER POSITIONING header.
    out = build_combined_prompt_bundled(_stub_blocks("SPX", 5800.0), None, None,
                                        premarket=False)
    assert "DEALER POSITIONING" not in out


def test_summary_prompt_includes_compact_positioning():
    spx = _enrich_with_metrics(_stub_blocks("SPX", 5800.0))
    out = build_summary_prompt_bundled(spx, None, None, premarket=False)
    assert "Max pain" in out
    assert "5,850" in out  # call wall surfaced in the compact line


# ── Dealer Pinch block in the AI prompt ──

def _pinch_state(armed=True, regime="PIN"):
    return {
        "symbol": "SPX", "armed": armed, "confidence": 72.0, "regime": regime,
        "conditions": {"c1": True, "c2": True, "c3a": True, "c3b": armed},
        "node": {"strike": 5800.0, "dist_pts": 1.0, "dist_pct": 0.0002},
        "node_dominance": 0.41, "secondary_node": 5750.0,
        "pin_risk": 0.8, "iv_pctile": 85.0,
        "rv_trend": {"value": 11.0, "falling": True},
        "forced_hedge_dir": "down",
        "levels": {"pin_target": 5800.0, "break_trigger": 5781.0,
                   "invalidation": "IV %ile < 60, or spot > 1% from node"},
        "time_to_resolve": {"dte": 2, "hours_to_close": 3.0},
        "playbook": "PIN: fade the edges and sell premium centered on the node.",
        "reason": "All 4 conditions met — pinch armed.",
    }


def test_combined_prompt_includes_dealer_pinch_when_present():
    spx = _stub_blocks("SPX", 5800.0)
    spx["gex"]["dealer_pinch"] = _pinch_state()
    out = build_combined_prompt_bundled(spx, None, None, premarket=False)
    assert "DEALER PINCH" in out
    assert "PIN" in out
    assert "5,800" in out          # node
    assert "Pin target" in out or "pin_target" in out or "5,781" in out


def test_combined_prompt_omits_dealer_pinch_when_absent():
    out = build_combined_prompt_bundled(_stub_blocks("SPX", 5800.0), None, None,
                                        premarket=False)
    assert "DEALER PINCH" not in out
