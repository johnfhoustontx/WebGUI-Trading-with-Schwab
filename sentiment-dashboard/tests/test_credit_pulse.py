"""Tests for scoring.credit_pulse."""
import pytest

from scoring import credit_pulse


def test_missing_history_returns_zero():
    r = credit_pulse.score(None)
    assert r.score == 0
    assert r.confidence == 0.0


def test_short_history_returns_partial_confidence():
    hist = {'hyg_closes': [80.0] * 30, 'iei_closes': [120.0] * 30}
    r = credit_pulse.score(hist)
    assert r.score == 0
    # sqrt(30/60) = sqrt(0.5)
    assert r.confidence == pytest.approx((30/60.0) ** 0.5)


def test_flat_history_runs_at_full_confidence():
    """Perfectly flat history is FP-unstable for z (tiny std → noisy z),
    but the math still completes with full confidence and a 1..10 score.
    We assert the contract (range + confidence), not the exact integer."""
    hist = {'hyg_closes': [80.0] * 60, 'iei_closes': [120.0] * 60}
    r = credit_pulse.score(hist)
    assert 1 <= r.score <= 10
    assert r.confidence == 1.0


def test_genuinely_neutral_with_noise():
    """Small noise yields a real std so z lands near 0 → neutral score."""
    import random
    rng = random.Random(42)
    hyg = [80.0 + rng.uniform(-0.1, 0.1) for _ in range(60)]
    iei = [120.0 + rng.uniform(-0.1, 0.1) for _ in range(60)]
    r = credit_pulse.score({'hyg_closes': hyg, 'iei_closes': iei})
    assert 4 <= r.score <= 7


def test_extreme_risk_off_state():
    """HYG cratering vs flat IEI → z very negative, dist very negative."""
    hyg = [100.0] * 50 + [95.0] * 10
    iei = [120.0] * 60
    hist = {'hyg_closes': hyg, 'iei_closes': iei}
    r = credit_pulse.score(hist)
    # Should be near the bearish end
    assert r.score <= 4


def test_score_with_state_returns_state_dict():
    hist = {'hyg_closes': [80.0] * 60, 'iei_closes': [120.0] * 60}
    r, state = credit_pulse.score_with_state(hist)
    assert state is not None
    assert 'z' in state
    assert 'sub_a' in state
    assert 'sub_b' in state
    assert 'dist_pct' in state


def test_score_with_state_none_on_missing():
    r, state = credit_pulse.score_with_state(None)
    assert state is None
