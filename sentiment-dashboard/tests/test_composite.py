"""Tests for scoring.composite — blend, velocity, divergence."""
import pytest

from scoring import composite


WEIGHTS_8 = {
    "vix_term":     0.15,
    "vix1d":        0.10,
    "term_slope":   0.05,
    "put_call":     0.15,
    "breadth":      0.15,
    "rotation":     0.15,
    "sector":       0.15,
    "credit_pulse": 0.10,
}


def test_blend_all_full_confidence_average():
    """All conf=1 → composite is straight weighted average; agg_conf = 1."""
    scores = {k: 7 for k in WEIGHTS_8}
    confs = {k: 1.0 for k in WEIGHTS_8}
    composite_score, agg_conf = composite.blend(scores, confs, WEIGHTS_8)
    assert composite_score == pytest.approx(7.0)
    assert agg_conf == pytest.approx(1.0)


def test_blend_zero_denominator_returns_zero():
    confs = {k: 0.0 for k in WEIGHTS_8}
    c, agg = composite.blend({k: 5 for k in WEIGHTS_8}, confs, WEIGHTS_8)
    assert c == 0.0
    assert agg == 0.0


def test_blend_low_confidence_downweights_component():
    """A 10-scoring component at 0.1 conf should pull less than its raw 15%."""
    scores = {k: 1 for k in WEIGHTS_8}
    scores["vix_term"] = 10
    confs = {k: 1.0 for k in WEIGHTS_8}
    confs["vix_term"] = 0.1
    c, _ = composite.blend(scores, confs, WEIGHTS_8)
    # Expected: (0.15*10*0.1 + Σ(other 0.85*1*1)) / (0.15*0.1 + 0.85)
    # = (0.15 + 0.85) / 0.865 = 1.0 / 0.865 ≈ 1.156
    assert c == pytest.approx(1.0 / 0.865, abs=1e-6)


# ── velocity ──────────────────────────────────────────────────────

def test_velocity_empty_history():
    v = composite.velocity([], today_score=5.0)
    assert v['roc_3d'] is None
    assert v['roc_5d'] is None
    assert v['z_20d'] is None
    assert v['regime_break'] is False


def test_velocity_3d_roc():
    history = [5.0, 5.5, 6.0]
    v = composite.velocity(history, today_score=7.0)
    assert v['roc_3d'] == pytest.approx(2.0)  # 7 - history[-3] = 7 - 5
    assert v['roc_5d'] is None


def test_velocity_z_and_regime_break_flag():
    """Constant 5.0 history → std=0 → z is None. With one outlier we get z."""
    history = [5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 6.0]
    v = composite.velocity(history, today_score=9.0)
    assert v['z_20d'] is not None
    # |z| should be > 1.5 with that jump → regime break
    if abs(v['z_20d']) > 1.5:
        assert v['regime_break'] is True


def test_velocity_no_regime_break_when_z_small():
    history = [5.0, 5.1, 4.9, 5.05, 4.95, 5.0, 5.0]
    v = composite.velocity(history, today_score=5.0)
    assert v['regime_break'] is False


# ── divergence ────────────────────────────────────────────────────

def test_divergence_below_threshold_returns_none():
    assert composite.divergence([("A", 5), ("B", 6), ("C", 7)]) is None


def test_divergence_4pt_spread_fires():
    flag = composite.divergence([("VIX", 9), ("Breadth", 5)])
    assert flag is not None
    assert "VIX" in flag and "Breadth" in flag
    assert "low conviction" in flag


def test_divergence_ignores_zero_scores():
    """Zero-score components are excluded (treated as no data)."""
    assert composite.divergence([("A", 0), ("B", 5)]) is None
    flag = composite.divergence([("A", 0), ("B", 9), ("C", 5)])
    assert flag is not None
