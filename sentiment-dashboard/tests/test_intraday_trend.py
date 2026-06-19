from scoring.intraday_trend import TrendSub, _clamp


def test_clamp_bounds():
    assert _clamp(150, 0, 100) == 100
    assert _clamp(-5, 0, 100) == 0
    assert _clamp(42.0, 0, 100) == 42.0


def test_trendsub_is_frozen():
    s = TrendSub(score=72.5, confidence=0.8, interp="x")
    assert s.score == 72.5 and s.confidence == 0.8
    import dataclasses, pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.score = 1.0
