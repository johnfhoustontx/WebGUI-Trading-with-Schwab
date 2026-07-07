"""Tests for regime_filter — sentiment/trend gating of CCS/PCS signals."""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import regime_filter as rf


def test_trend_state_vote_matches_classifier_states():
    """_TREND_STATE_VOTE must key on exactly the 5 states the classifier emits
    (scoring/market_state.py STATE_LABELS): bullish, lack_of_bullishness,
    neutral, lack_of_bearishness, bearish — no dead keys. Confirmed direction
    states are hard votes; the two lack_of_* states are soft leans; neutral is
    no vote."""
    assert set(rf._TREND_STATE_VOTE) == {
        "bullish", "lack_of_bullishness", "neutral",
        "lack_of_bearishness", "bearish"}
    assert rf._TREND_STATE_VOTE["bullish"] == "bull"
    assert rf._TREND_STATE_VOTE["bearish"] == "bear"
    assert rf._TREND_STATE_VOTE["lack_of_bearishness"] == "lean_bull"
    assert rf._TREND_STATE_VOTE["lack_of_bullishness"] == "lean_bear"
    assert rf._TREND_STATE_VOTE["neutral"] is None
    for dead in ("bull_trend", "pullback_in_bull", "range",
                 "bear_rally", "bear_trend"):
        assert dead not in rf._TREND_STATE_VOTE


def test_lack_of_bullishness_with_bearish_sentiment_blocks_pcs():
    """lack_of_bullishness (lean_bear) + hard bearish sentiment agree → PCS blocked."""
    out = rf.evaluate_regime(
        bridge=_bridge(score=2.0, bias="short", trend_state="lack_of_bullishness"))
    assert out["allow_pcs"] is False
    assert out["allow_ccs"] is True


def test_neutral_trend_does_not_block_alone():
    """neutral casts no trend vote → sentiment alone can't tighten a side."""
    out = rf.evaluate_regime(
        bridge=_bridge(score=7.5, bias="long", trend_state="neutral"))
    assert out["allow_ccs"] is True and out["allow_pcs"] is True


def _bridge(score=5.0, bias="neutral", age_hours=0,
            trend_state="neutral", trend_confidence=1.0,
            aggregate_confidence=0.9, divergence_flag=None):
    ts = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    return {
        "composite_score": score,
        "bias": bias,
        "generated_at": ts,
        "regime": "test",
        "aggregate_confidence": aggregate_confidence,
        "divergence_flag": divergence_flag,
        "trend_regime": {
            "state": trend_state,
            "confidence": trend_confidence,
        },
    }


def test_inactive_when_no_bridge(tmp_path):
    out = rf.evaluate_regime(path=tmp_path / "missing.json")
    assert out["active"] is False
    assert out["allow_ccs"] is True
    assert out["allow_pcs"] is True


def test_stale_bridge_treated_as_inactive():
    out = rf.evaluate_regime(bridge=_bridge(score=8.0, age_hours=200))
    assert out["active"] is False


def test_evaluate_regime_reads_intraday_trend_fields():
    """The bridge's trend_regime block now carries the intraday model's additive
    trend_score/sub_scores fields alongside state/confidence/sma_*. Those extra
    fields must not break consumption — regime_filter still reads ``state`` and
    behaves exactly as for a confident bullish state (CCS blocked)."""
    bridge = _bridge(score=7.5, bias="long", trend_state="bullish")
    # overlay the new additive fields the sentiment service now publishes
    bridge["trend_regime"].update({
        "trend_score": 84.0,
        "sub_scores": {"price": 88, "breadth": 70, "sector": 65, "vix": 55},
        "label": "Bullish", "raw_state": "bullish",
        "spy_close": 500.0, "sma_50": 480.0, "sma_200": 450.0,
        "sma_200_slope_pct": 0.3, "drawdown_pct": -1.0,
    })
    out = rf.evaluate_regime(bridge=bridge)
    assert out["active"] is True
    assert out["trend_state"] == "bullish"
    assert out["allow_ccs"] is False
    assert out["allow_pcs"] is True


def test_bullish_score_and_bullish_trend_blocks_ccs():
    out = rf.evaluate_regime(bridge=_bridge(
        score=7.5, bias="long", trend_state="bullish"
    ))
    assert out["active"] is True
    assert out["allow_ccs"] is False
    assert out["allow_pcs"] is True
    assert out["trend_state"] == "bullish"


def test_bearish_score_and_bearish_trend_blocks_pcs():
    out = rf.evaluate_regime(bridge=_bridge(
        score=2.0, bias="short", trend_state="bearish"
    ))
    assert out["allow_ccs"] is True
    assert out["allow_pcs"] is False


def test_neutral_allows_both():
    out = rf.evaluate_regime(bridge=_bridge(score=5.0, bias="neutral"))
    assert out["allow_ccs"] is True
    assert out["allow_pcs"] is True


def test_sentiment_bullish_but_trend_bear_does_not_block():
    """Divergence: bridge says high score but trend is bearish → both sides allowed."""
    out = rf.evaluate_regime(bridge=_bridge(
        score=7.5, bias="long", trend_state="bearish"
    ))
    assert out["allow_ccs"] is True
    assert out["allow_pcs"] is True
    assert "divergence" in out["reason"]


def test_sentiment_alone_not_enough_when_trend_neutral():
    """Without trend agreement, sentiment alone can't block a side."""
    out = rf.evaluate_regime(bridge=_bridge(
        score=7.5, bias="long", trend_state="neutral"
    ))
    assert out["allow_ccs"] is True
    assert out["allow_pcs"] is True


def test_low_trend_confidence_does_not_block_alone():
    """A hard bullish state with low confidence becomes a lean — needs sentiment agreement."""
    out = rf.evaluate_regime(bridge=_bridge(
        score=5.0, bias="neutral",
        trend_state="bullish", trend_confidence=0.3,
    ))
    assert out["allow_ccs"] is True
    assert out["allow_pcs"] is True


def test_divergence_flag_suppresses_block_unless_hard_agreement():
    """When the bridge itself flags divergence, both hard votes are required.

    Both votes here are hard (score 7.5 + bullish), so the block stands.
    """
    out = rf.evaluate_regime(bridge=_bridge(
        score=7.5, bias="long", trend_state="bullish",
        divergence_flag="VIX vs Rotation disagreement",
    ))
    assert out["allow_ccs"] is False


def test_filter_drops_ccs_in_bullish_regime():
    regime = rf.evaluate_regime(bridge=_bridge(
        score=7.5, bias="long", trend_state="bullish"
    ))
    sigs = [
        {"type": "PCS", "symbol": "SPY", "id": "p1"},
        {"type": "CCS", "symbol": "SPY", "id": "c1"},
        {"type": "CCS", "symbol": "$SPX", "id": "c2"},
        {"type": "IC", "symbol": "QQQ", "id": "i1"},
    ]
    kept = rf.filter_signals(sigs, regime)
    ids = {s["id"] for s in kept}
    assert ids == {"p1", "i1"}


def test_filter_drops_pcs_in_bearish_regime():
    regime = rf.evaluate_regime(bridge=_bridge(
        score=2.0, bias="short", trend_state="bearish"
    ))
    sigs = [
        {"type": "PCS", "symbol": "SPY", "id": "p1"},
        {"type": "CCS", "symbol": "SPY", "id": "c1"},
        {"type": "IC", "symbol": "QQQ", "id": "i1"},
    ]
    kept = rf.filter_signals(sigs, regime)
    ids = {s["id"] for s in kept}
    assert ids == {"c1", "i1"}


def test_per_symbol_re_enables_ccs_when_symbol_trend_bearish():
    regime = rf.evaluate_regime(
        bridge=_bridge(score=7.5, bias="long", trend_state="bullish"),
        technicals_by_symbol={
            "SPY": {"trend": "WEAKENING"},
            "QQQ": {"trend": "BULLISH"},
        },
    )
    assert regime["per_symbol"]["SPY"]["allow_ccs"] is True
    assert regime["per_symbol"]["QQQ"]["allow_ccs"] is False

    sigs = [
        {"type": "CCS", "symbol": "SPY", "id": "spy_ccs"},
        {"type": "CCS", "symbol": "QQQ", "id": "qqq_ccs"},
    ]
    kept = rf.filter_signals(sigs, regime)
    ids = {s["id"] for s in kept}
    assert ids == {"spy_ccs"}


def test_neutral_score_with_bullish_trend_does_not_tighten():
    """Neutral score (6.3) means both sides allowed even with a bullish symbol trend."""
    regime = rf.evaluate_regime(
        bridge=_bridge(score=6.3, bias="neutral", trend_state="bullish"),
        technicals_by_symbol={"SPY": {"trend": "BULLISH"}},
    )
    assert regime["allow_ccs"] is True
    assert regime["per_symbol"]["SPY"]["allow_ccs"] is True
    assert regime["per_symbol"]["SPY"]["allow_pcs"] is True


def test_filter_inactive_passes_signals_through():
    sigs = [{"type": "CCS", "symbol": "SPY"}, {"type": "PCS", "symbol": "SPY"}]
    kept = rf.filter_signals(sigs, {"active": False})
    assert len(kept) == 2


def test_current_bridge_snapshot_blocks_ccs():
    """Sanity check with the actual live bridge values (2026-05-22 snapshot)."""
    out = rf.evaluate_regime(bridge=_bridge(
        score=6.84, bias="neutral", trend_state="bullish",
        trend_confidence=1.0, aggregate_confidence=0.862,
        divergence_flag="DIVERGENCE: VIX Complex 9 vs Rotation 3 — low conviction",
    ))
    # Both votes hard + agree (bull): block stands even with divergence_flag.
    assert out["active"] is True
    assert out["allow_ccs"] is False
    assert out["allow_pcs"] is True
    assert out["trend_state"] == "bullish"
