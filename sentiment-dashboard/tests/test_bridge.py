"""Round-trip tests for bridge.write_bridge.

All tests redirect both CANONICAL_PATH and LEGACY_PATH onto ``tmp_path``
so they never touch the real on-disk location
(``repo_paths.BRIDGE_PATH`` under ``shared/``).
"""
import json

import pytest

import bridge


@pytest.fixture
def tmp_bridge_paths(tmp_path, monkeypatch):
    """Patch bridge.CANONICAL_PATH and LEGACY_PATH onto tmp_path."""
    canonical = tmp_path / "shared" / "sentiment_bridge.json"
    legacy = tmp_path / "pkg" / "sentiment_bridge.json"
    monkeypatch.setattr(bridge, "CANONICAL_PATH", canonical)
    monkeypatch.setattr(bridge, "LEGACY_PATH", legacy)
    return canonical, legacy


def test_bridge_schema_version_constant():
    assert bridge.BRIDGE_SCHEMA_VERSION == "4.2"


def test_write_bridge_round_trip(tmp_path):
    """Write a known payload to an explicit path and load it back."""
    payload = {
        "source": "SentimentDashboard",
        "version": "3.9.1",
        "composite_score": 6.42,
        "regime": "bullish",
        "bias": "long",
        "component_scores": {"vix_term": 7, "put_call": 6, "breadth": 8},
        "component_confidence": {"vix_term": 1.0, "put_call": 0.71},
    }
    out = tmp_path / "sentiment_bridge.json"
    written = bridge.write_bridge(payload, out)
    assert written == out
    assert out.exists()

    loaded = json.loads(out.read_text(encoding="utf-8"))
    # Required schema fields
    assert loaded["source"] == "SentimentDashboard"
    assert loaded["composite_score"] == 6.42
    assert loaded["regime"] == "bullish"
    assert loaded["component_scores"]["vix_term"] == 7
    # schema_version auto-added when missing
    assert loaded["schema_version"] == "4.2"
    # Explicit-path writes do NOT add the deprecated_path flag.
    assert "deprecated_path" not in loaded


def test_write_bridge_preserves_existing_schema_version(tmp_path):
    payload = {"schema_version": "4.0", "composite_score": 1.0}
    out = tmp_path / "sentiment_bridge.json"
    bridge.write_bridge(payload, out)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "4.0"


def test_write_bridge_creates_parent_dir(tmp_path):
    nested = tmp_path / "nested" / "deeper" / "bridge.json"
    bridge.write_bridge({"composite_score": 0.0}, nested)
    assert nested.exists()


def test_write_bridge_default_writes_canonical_and_legacy(tmp_bridge_paths):
    """With no path arg, bridge writes both canonical and legacy paths."""
    canonical, legacy = tmp_bridge_paths
    payload = {"composite_score": 5.5, "regime": "neutral"}
    returned = bridge.write_bridge(payload)
    assert returned == canonical
    assert canonical.exists()
    assert legacy.exists()

    canon = json.loads(canonical.read_text(encoding="utf-8"))
    leg = json.loads(legacy.read_text(encoding="utf-8"))

    # Canonical copy: clean payload + auto schema_version, no deprecated flag.
    assert canon["composite_score"] == 5.5
    assert canon["schema_version"] == "4.2"
    assert "deprecated_path" not in canon
    # Legacy copy: same data + deprecated_path marker.
    assert leg["composite_score"] == 5.5
    assert leg["deprecated_path"] is True


def test_payload_includes_trend_regime_block(tmp_path):
    """The trend_regime block is additive and stable for Options Scanner."""
    payload = {
        "composite_score": 5.0,
        "trend_regime": {
            "state": "bull_trend",
            "label": "Bull Trend",
            "description": "Healthy uptrend — favor long entries, full size.",
            "raw_state": "bull_trend",
            "days_in_state": 3,
            "spy_close": 612.34,
            "sma_50": 595.10,
            "sma_200": 568.20,
            "sma_200_slope_pct": 0.18,
            "drawdown_pct": -2.10,
            "confidence": 1.0,
            "published_at": "2026-05-21T13:00:00Z",
        },
    }
    out = tmp_path / "bridge.json"
    bridge.write_bridge(payload, out)
    round_trip = json.loads(out.read_text(encoding="utf-8"))
    tr = round_trip["trend_regime"]
    assert set(tr) >= {
        "state", "label", "description", "raw_state",
        "days_in_state", "spy_close", "sma_50", "sma_200",
        "sma_200_slope_pct", "drawdown_pct", "confidence", "published_at",
    }
    assert tr["state"] in {
        "bull_trend", "pullback_in_bull", "range",
        "bear_rally", "bear_trend",
    }
    assert round_trip["schema_version"] == "4.2"
