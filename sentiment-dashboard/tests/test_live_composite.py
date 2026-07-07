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


def test_cap_weighted_pcr_known():
    import pytest
    # (0.9*2 + 0.6*1)/(2+1) = 2.4/3 = 0.8
    pcr = {"XLK": 0.9, "XLF": 0.6}
    weights = {"XLK": 2.0, "XLF": 1.0}
    assert L.cap_weighted_pcr(pcr, weights) == pytest.approx(0.8)


def test_cap_weighted_pcr_empty_is_none():
    assert L.cap_weighted_pcr({}, {}) is None
    assert L.cap_weighted_pcr({"XLK": 0.9}, {}) is None       # no usable weight
    assert L.cap_weighted_pcr({}, {"XLK": 2.0}) is None       # no pcr


def test_cap_weighted_pcr_partial_weights():
    # only sectors with a non-zero weight AND a pcr contribute
    pcr = {"XLK": 1.0, "XLF": 0.5, "XLE": 0.3}
    weights = {"XLK": 3.0, "XLF": 0.0, "XLE": 1.0}   # XLF weight 0 -> excluded
    # (1.0*3 + 0.3*1)/(3+1) = 3.3/4 = 0.825
    import pytest
    assert L.cap_weighted_pcr(pcr, weights) == pytest.approx(0.825)


def test_cap_weighted_pcr_single_sector():
    assert L.cap_weighted_pcr({"XLK": 0.7}, {"XLK": 2.0}) == 0.7


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


def test_bridge_trend_regime_additive_fields():
    """The trend block carries the new additive trend_score/sub_scores while
    keeping the daily structural sma_* fields (additive-only contract)."""
    trend = {
        "state": "bull_trend", "label": "Bull Trend", "description": "x",
        "raw_state": "bull_trend", "spy_close": 500.0, "sma_50": 480.0,
        "sma_200": 450.0, "sma_200_slope_pct": 0.3, "drawdown_pct": -1.0,
        "confidence": 0.9, "trend_score": 84.0, "sub_scores": {"price": 88},
    }
    p = L.build_bridge_payload(_snap(6.81), [6.0], [], "x", trend=trend)
    tr = p["trend_regime"]
    assert tr["state"] == "bull_trend"
    assert tr["trend_score"] == 84.0
    assert tr["sub_scores"] == {"price": 88}
    # back-compat structural field still present
    assert tr["sma_50"] == 480.0
    assert tr["confidence"] == 0.9


def test_bridge_trend_regime_without_new_fields_is_valid():
    """A daily-only trend dict (no trend_score/sub_scores) still yields a valid
    trend_regime block — the new keys degrade to None, no KeyError."""
    trend = {
        "state": "range", "label": "Range", "description": "x",
        "raw_state": "range", "spy_close": 500.0, "sma_50": 480.0,
        "sma_200": 450.0, "sma_200_slope_pct": 0.1, "drawdown_pct": -2.0,
        "confidence": 0.5,
    }
    p = L.build_bridge_payload(_snap(5.0), [5.0], [], "x", trend=trend)
    tr = p["trend_regime"]
    assert tr["state"] == "range"
    assert tr["trend_score"] is None
    assert tr["sub_scores"] is None
    assert tr["sma_50"] == 480.0


class _FakeDF:
    def __init__(self, closes): self._c = closes
    def __getitem__(self, k): return self
    def tolist(self): return self._c

class _FakeClient:
    def get_quote(self, s): return {"last": 18.0} if s == "$VIX" else {"last": 1.5}
    def get_quotes(self, syms): return {s: {"last": 17.0, "change_pct": 0.5} for s in syms}
    def get_daily_history(self, s, months=12):
        return _FakeDF([100.0 + i for i in range(80)])
    def _request(self, ep, params=None): return None

def test_compute_live_smoke():
    import sectors_ref
    sd = sectors_ref.load_sectors_data()
    snap = L.compute_live(_FakeClient(), sd)
    assert 0 <= float(snap["composite"]["total_score"]) <= 10
    assert set(["vix_complex","put_call","breadth","rotation","sector_perf"]) \
        <= set(snap["component_scores"])
