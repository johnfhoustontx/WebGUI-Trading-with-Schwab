"""Tests for build_combined_prompt_bundled - the multi-symbol prompt builder."""
from gamma_tool import build_summary_prompt_bundled


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


def test_bundled_summary_accepts_internals():
    out = build_summary_prompt_bundled(
        _stub_blocks("SPX", 5800.0),
        _stub_blocks("SPY", 580.0),
        _stub_blocks("QQQ", 500.0),
        premarket=False,
        internals={"cpce": 0.78, "advn": 1450, "decn": 1602, "skew": 138.4},
    )
    assert "MARKET INTERNALS" in out


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
