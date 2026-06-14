"""Tests for the gamma Explain popup text builder (pure, no Tk)."""
from gamma_tool import build_explain_text, _term_walls_from_rows

SECTIONS = [
    "WHAT YOU'RE LOOKING AT",
    "RIGHT NOW",
    "HOW TO PLAY IT",
    "GLOBAL SENTIMENT",
]


def _ctx(**over):
    base = {
        "symbol": "$SPX", "spot": 5800.0, "dte": 0,
        "vix_now": 14.0, "vix_delta": -0.3,
        "gex_summary": {"spot": 5800.0, "flip": 5790.0,
                        "top_pos_strike": 5850.0, "top_neg_strike": 5750.0,
                        "net_total": 1.2e9},
        "charm_summary": None, "dex_summary": None, "drift_panel": None,
        "sentiment": {"active": True, "composite_score": 7.0, "bias": "long",
                      "trend_state": "bull_trend", "trend_confidence": 0.8},
        "term_data": None,
    }
    base.update(over)
    return base


def test_all_five_sections_present_for_gex():
    txt = build_explain_text("gex", _ctx())
    for s in SECTIONS:
        assert s in txt


def test_what_section_is_view_specific():
    assert "dampen" in build_explain_text("gex", _ctx()).lower()


def test_right_now_gex_shows_walls_and_flip_and_bias():
    txt = build_explain_text("gex", _ctx())
    assert "5,850" in txt     # call wall
    assert "5,750" in txt     # put wall
    assert "5,790" in txt     # flip
    # spot 5800 > flip 5790 -> long-gamma / pinning bias headline
    assert "pin" in txt.lower()


def test_right_now_gex_no_data():
    txt = build_explain_text("gex", _ctx(gex_summary=None))
    assert "no gex data" in txt.lower() or "not available" in txt.lower()


def test_how_to_play_gex_has_posture_risk_and_sentiment_tag():
    txt = build_explain_text("gex", _ctx())          # long-gamma, sentiment bull 7/10
    low = txt.lower()
    assert "fade" in low or "mean-rever" in low       # posture
    assert "vix" in low and "risk" in low             # risk color
    # Long-gamma (above flip) is directionally neutral, so no strong verdict.
    assert "no strong agree/conflict" in low


def test_how_to_play_agree_tag_when_sentiment_confirms():
    # DEX is genuinely directional: negative hedge_pressure -> dealers selling
    # -> headwind -> _view_dir "bear". Pair with bearish sentiment -> AGREES.
    txt = build_explain_text("dex", _ctx_dex(
        dex_summary={"spot": 5800.0, "flip": None, "top_pos_strike": None,
                     "top_neg_strike": None, "net_total": 0.0,
                     "hedge_pressure": -7.5e8},  # bear view
        sentiment={"active": True, "composite_score": 3.0, "bias": "short",
                   "trend_state": "bear_trend", "trend_confidence": 0.8}))
    assert "AGREES" in txt          # bear view + bearish sentiment


def test_how_to_play_conflict_tag_when_sentiment_opposes():
    # DEX negative hedge_pressure -> bear view; pair with bullish sentiment.
    txt = build_explain_text("dex", _ctx_dex(
        dex_summary={"spot": 5800.0, "flip": None, "top_pos_strike": None,
                     "top_neg_strike": None, "net_total": 0.0,
                     "hedge_pressure": -7.5e8},  # bear view
        sentiment={"active": True, "composite_score": 8.0, "bias": "long",
                   "trend_state": "bull_trend", "trend_confidence": 0.8}))
    assert "CONFLICT" in txt


def test_gex_tag_is_neutral_regardless_of_sentiment():
    # GEX is a vol-regime axis, not directional -> it never asserts
    # agree/conflict against sentiment, in EITHER gamma regime.
    # (a) spot ABOVE flip (long-gamma) + bullish sentiment.
    above = build_explain_text("gex", _ctx(
        spot=5800.0,  # > flip 5790
        sentiment={"active": True, "composite_score": 8.0, "bias": "long",
                   "trend_state": "bull_trend", "trend_confidence": 0.8}))
    # (b) spot BELOW flip (short-gamma) + bearish sentiment.
    below = build_explain_text("gex", _ctx(
        spot=5780.0,  # < flip 5790
        sentiment={"active": True, "composite_score": 3.0, "bias": "short",
                   "trend_state": "bear_trend", "trend_confidence": 0.8}))
    for txt in (above, below):
        assert "no strong agree/conflict" in txt.lower()
        assert "CONFLICT" not in txt
        assert "AGREES" not in txt


def test_global_sentiment_describes_market_and_relates_to_view():
    txt = build_explain_text("gex", _ctx())
    low = txt.lower()
    assert "7/10" in txt or "7.0" in txt
    assert "bull" in low                         # trend state surfaced
    assert "overall market" in low               # explicitly labeled global


def test_global_sentiment_unavailable_when_bridge_inactive():
    txt = build_explain_text("gex", _ctx(sentiment={"active": False}))
    assert "unavailable" in txt.lower()


def test_global_sentiment_none_score_is_not_bearish():
    # active bridge but no composite score -> neutral mood, never "bearish"
    txt = build_explain_text(
        "gex", _ctx(sentiment={"active": True, "composite_score": None}))
    mood_line = next(l for l in txt.splitlines()
                     if "overall market sentiment" in l.lower())
    assert "bearish" not in mood_line.lower()
    assert "neutral" in mood_line.lower()


def test_footer_has_vix_and_sentiment():
    txt = build_explain_text("gex", _ctx())
    assert "VIX" in txt and "7/10" in txt


def _ctx_charm(**over):
    base = dict(_ctx(), charm_summary={"spot": 5800.0, "flip": 5795.0,
                "top_pos_strike": 5840.0, "top_neg_strike": 5760.0,
                "net_total": 2e8}, gex_summary=None)
    base.update(over); return base


def _ctx_dex(**over):
    base = dict(_ctx(), dex_summary={"spot": 5800.0, "flip": None,
                "top_pos_strike": None, "top_neg_strike": None,
                "net_total": 0.0, "hedge_pressure": 7.5e8}, gex_summary=None)
    base.update(over); return base


def test_charm_right_now_and_posture():
    txt = build_explain_text("charm", _ctx_charm())
    assert "5,795" in txt                      # charm flip
    assert "drift" in txt.lower() or "decay" in txt.lower()


def test_dex_right_now_shows_hedge_direction():
    txt = build_explain_text("dex", _ctx_dex())
    low = txt.lower()
    assert "buy" in low                       # positive hedge pressure -> dealers buy
    assert "support" in low or "tailwind" in low


def test_dex_hedge_flow_line_no_double_dollar_or_sign():
    # hedge_pressure 7.5e8 -> "$750M"; BUY word states direction, no leading sign.
    txt = build_explain_text("dex", _ctx_dex())
    assert "Net dealer hedge flow: BUY $750M" in txt
    assert "$+$" not in txt                    # no doubled dollar / stray "+"


def test_charm_how_to_play_no_flip_is_no_edge_not_downside():
    # flip undefined -> HOW TO PLAY must mirror RIGHT NOW (no directional lean).
    txt = build_explain_text("charm", _ctx_charm(
        charm_summary={"spot": 5800.0, "flip": None,
                       "top_pos_strike": None, "top_neg_strike": None,
                       "net_total": 0.0}))
    low = txt.lower()
    assert "downside" not in low
    assert "no clean" in low and "undefined" in low


def test_charm_right_now_no_data():
    txt = build_explain_text("charm", _ctx_charm(charm_summary=None))
    assert "No Charm data" in txt


def test_dex_right_now_hedge_pressure_none():
    txt = build_explain_text("dex", _ctx_dex(
        dex_summary={"spot": 5800.0, "flip": None, "top_pos_strike": None,
                     "top_neg_strike": None, "net_total": 0.0,
                     "hedge_pressure": None}))
    assert "DEX hedge pressure not available for this snapshot." in txt


def test_vanna_right_now_no_data():
    txt = build_explain_text("vanna", _ctx_vanna(drift_panel=None))
    assert "No Vanna" in txt


def _ctx_vanna(**over):
    base = dict(_ctx(), gex_summary=None, charm_summary={"spot": 5800.0,
                "flip": 5795.0, "top_pos_strike": 5840.0,
                "top_neg_strike": 5760.0, "net_total": 1e8},
                drift_panel={"net_vanna": 3.2e8, "net_charm": -1.1e8,
                "vix_now": 14.0, "vix_delta": -0.4, "charm_flip": 5795.0,
                "pair_state": "AGREE_UP", "confidence": 0.62,
                "confidence_band": "MED", "regime": "BALANCED",
                "regime_note": "both flows in play"})
    base.update(over); return base


def test_vanna_right_now_carries_all_four_panel_fields():
    txt = build_explain_text("vanna", _ctx_vanna())
    low = txt.lower()
    assert "vanna" in low and "charm" in low           # net vanna + net charm
    assert "vix" in low                                # vix line
    assert "agree" in low                              # pair state
    assert "62%" in txt or "med" in low                # confidence
    assert "balanced" in low and "both flows" in low   # regime + note


def _vanna_with_state(state):
    """_ctx_vanna with drift_panel.pair_state overridden to `state`."""
    base = _ctx_vanna()
    base["drift_panel"] = dict(base["drift_panel"], pair_state=state)
    return base


def test_vanna_how_to_play_posture_agree_up():
    txt = build_explain_text("vanna", _vanna_with_state("AGREE_UP"))
    assert "long" in txt.lower()


def test_vanna_how_to_play_posture_agree_down():
    txt = build_explain_text("vanna", _vanna_with_state("AGREE_DOWN"))
    low = txt.lower()
    assert "short" in low or "defensive" in low


def test_vanna_how_to_play_posture_conflict():
    txt = build_explain_text("vanna", _vanna_with_state("CONFLICT"))
    assert "stand aside" in txt.lower()


def test_vanna_how_to_play_posture_balanced():
    txt = build_explain_text("vanna", _vanna_with_state("FLAT"))
    assert "balanced" in txt.lower()


def test_term_minimal_and_no_sentiment_conflict_claim():
    txt = build_explain_text("term", dict(_ctx(), gex_summary=None,
                             term_data=None))
    low = txt.lower()
    assert "spxw" in low                       # term is SPXW-only
    assert "conflict" not in low               # structural -> no agree/conflict claim


def test_term_with_data_mentions_expirations():
    txt = build_explain_text("term", dict(_ctx(), gex_summary=None,
                             term_data={"near_wall": 5850.0, "far_wall": 5900.0}))
    assert "5,850" in txt


def _term_row(exp, strike, net):
    return {"expiration_date": exp, "strike": strike, "net_gex_usd": net,
            "underlying_price": 5800.0}


def test_term_walls_picks_dominant_strike_per_expiration():
    rows = [
        _term_row("2026-06-08", 5850.0, 3.0e9),   # near: dominant
        _term_row("2026-06-08", 5820.0, 1.0e9),
        _term_row("2026-06-12", 5900.0, -4.0e9),  # far: dominant by |magnitude|
        _term_row("2026-06-12", 5870.0, 2.0e9),
    ]
    walls = _term_walls_from_rows(rows)
    assert walls == {"near_wall": 5850.0, "far_wall": 5900.0}


def test_term_walls_none_when_no_rows():
    assert _term_walls_from_rows(None) is None
    assert _term_walls_from_rows([]) is None


def test_term_walls_single_expiration_has_no_far_wall():
    rows = [_term_row("2026-06-08", 5850.0, 3.0e9),
            _term_row("2026-06-08", 5820.0, 1.0e9)]
    walls = _term_walls_from_rows(rows)
    assert walls == {"near_wall": 5850.0, "far_wall": None}


def test_term_walls_feed_builder_render():
    rows = [_term_row("2026-06-08", 5850.0, 3.0e9),
            _term_row("2026-06-12", 5900.0, 2.0e9)]
    txt = build_explain_text("term", dict(_ctx(), gex_summary=None,
                             term_data=_term_walls_from_rows(rows)))
    assert "5,850" in txt and "5,900" in txt
    assert "no term data" not in txt.lower()   # has data -> not the no-data message


# ── Enriched WHAT-YOU'RE-LOOKING-AT primers (FlashAlpha interpretation) ──

def _what_section(view):
    """Return just the WHAT section text for a view."""
    txt = build_explain_text(view, _ctx(
        charm_summary={"spot": 5800.0, "flip": 5790.0,
                       "top_pos_strike": 5850.0, "top_neg_strike": 5750.0,
                       "net_total": 1.0e8},
        dex_summary={"spot": 5800.0, "flip": 5790.0, "top_pos_strike": 5850.0,
                     "top_neg_strike": 5750.0, "net_total": 1.0e8,
                     "hedge_pressure": 1.0e8},
    ))
    start = txt.index("WHAT YOU'RE LOOKING AT")
    end = txt.index("RIGHT NOW")
    return txt[start:end].lower()


def test_what_gex_describes_both_regimes():
    w = _what_section("gex")
    assert "mean-revert" in w
    assert "trend" in w


def test_what_dex_is_directional():
    assert "direction" in _what_section("dex")


def test_what_vanna_mentions_vol():
    assert "vol" in _what_section("vanna")


def test_what_charm_mentions_close():
    assert "close" in _what_section("charm")


# ── Combined all-views explain text (folded HTML source) ──

def test_build_explain_html_text_covers_all_views():
    from gamma_tool import build_explain_html_text
    txt = build_explain_html_text(_ctx())
    # All four exposure views appear as section headers.
    assert "GAMMA EXPOSURE" in txt
    assert "CHARM" in txt
    assert "DELTA EXPOSURE" in txt
    assert "VANNA" in txt
    # Single shared sentiment + footer.
    assert "GLOBAL SENTIMENT" in txt
    assert txt.count("GLOBAL SENTIMENT") == 1
    # Sub-headings use the light-bar marker for h3 conversion.
    assert "── Right now ──" in txt
