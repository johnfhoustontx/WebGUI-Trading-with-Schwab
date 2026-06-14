"""Tests for live_composite pure helpers."""
import json
import live_composite as L


def _snap(total, **comp):
    base = {"vix_complex": 4, "put_call": 8, "breadth": 7, "rotation": 5, "sector_perf": 8}
    base.update(comp)
    return {
        "date": "2026-06-14",
        "composite": {"total_score": f"{total:.2f}"},
        "component_scores": base,
        "component_confidence": {k: 1.0 for k in base},
        "volatility": {"interpretation": "term flat"},
        "options": {"pc_equity": "0.86"},
        "breadth": {"interpretation": "Advancing"},
        "rotation": {"interpretation": "Cyc leads"},
    }


def test_signal_band():
    assert L.signal_band(9.5) == ("1.25x", "Long", "Strong Bull")
    assert L.signal_band(7.0) == ("1.10x", "Long", "Bullish")
    assert L.signal_band(5.0) == ("1.00x", "Neutral", "Neutral")
    assert L.signal_band(3.0) == ("0.85x", "Cautious", "Bearish")
    assert L.signal_band(1.0) == ("0.70x", "Short", "Strong Bear")


def test_build_bridge_payload_core():
    p = L.build_bridge_payload(_snap(6.81), history_scores=[6.0, 6.5, 6.8],
                               spy_closes=[], generated_at="2026-06-14T20:00:00+00:00")
    assert p["composite_score"] == 6.81
    assert p["regime"] == "bullish"
    assert p["bias"] == "neutral"
    assert p["position_size_modifier"] == "1.00x"
    assert p["generated_at"] == "2026-06-14T20:00:00+00:00"
    assert p["date"] == "2026-06-14"
    assert set(["vix_complex", "put_call", "breadth", "rotation", "sector_perf"]) \
        <= set(p["component_scores"])
    assert "aggregate_confidence" in p and "velocity" in p and "weights" in p


def test_build_bridge_payload_regime_bands():
    band = lambda t: L.build_bridge_payload(_snap(t), [], [], "x")["regime"]
    assert band(8.5) == "strong_bullish"
    assert band(7.0) == "bullish"
    assert band(5.5) == "neutral"
    assert band(4.0) == "bearish"
    assert band(2.0) == "strong_bearish"


def test_build_bridge_payload_roundtrips_through_bridge(tmp_path):
    import bridge
    p = L.build_bridge_payload(_snap(6.0), [6.0], [], "2026-06-14T20:00:00+00:00")
    out = tmp_path / "b.json"
    bridge.write_bridge(p, path=out)
    reread = json.loads(out.read_text())
    assert reread["composite_score"] == 6.0
    assert reread["schema_version"]
