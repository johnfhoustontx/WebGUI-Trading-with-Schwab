from shared.contracts.sentiment import IntradayHistory


def test_intraday_history_accepts_points():
    h = IntradayHistory(points=[{"ts": 1, "sentiment": 6.0, "trend": 70.0}])
    assert h.points[0]["sentiment"] == 6.0


def test_intraday_history_defaults_empty():
    assert IntradayHistory().points == []


def _valid_vec():
    return {"mean_reversion": 0.5, "trending": 0.3, "breakout": 0.05,
            "choppy": 0.1, "crisis": 0.05}


def test_regime_state_roundtrip():
    from shared.contracts.sentiment import RegimeState
    s = RegimeState(ts="2026-07-23T10:05:00-05:00", as_of="2026-07-23 10:05 CT",
                    memberships=_valid_vec(), raw=_valid_vec(), confidence=0.6,
                    unclear=False, label="Mean Reversion",
                    committed_label="mean_reversion",
                    transition={"from": "mean_reversion", "to": "trending", "progress": 0.4},
                    evidence=["ADX 24", "VWAP held 78%"])
    back = RegimeState.from_json(s.to_json())
    assert back.memberships["trending"] == 0.3
    assert back.transition["to"] == "trending"


def test_regime_state_transition_optional():
    from shared.contracts.sentiment import RegimeState
    s = RegimeState(ts="t", memberships=_valid_vec(), raw=_valid_vec(),
                    confidence=0.1, unclear=True)
    assert s.transition is None and s.evidence == []


def test_regime_state_rejects_wrong_membership_keys():
    import pytest
    from pydantic import ValidationError
    from shared.contracts.sentiment import RegimeState
    with pytest.raises(ValidationError):
        RegimeState(ts="t", memberships={"trending": 1.0}, raw=_valid_vec(), confidence=1.0)
    with pytest.raises(ValidationError):
        RegimeState(ts="t", memberships=dict(_valid_vec(), extra=0.1), raw=_valid_vec(), confidence=1.0)


def test_regime_state_rejects_nonnumeric_membership_values():
    import pytest
    from pydantic import ValidationError
    from shared.contracts.sentiment import RegimeState
    with pytest.raises(ValidationError):
        RegimeState(ts="t", memberships=dict(_valid_vec(), trending="high"),
                    raw=_valid_vec(), confidence=1.0)


def test_regime_state_transition_requires_keys():
    import pytest
    from pydantic import ValidationError
    from shared.contracts.sentiment import RegimeState
    with pytest.raises(ValidationError):
        RegimeState(ts="t", memberships=_valid_vec(), raw=_valid_vec(), confidence=1.0,
                    transition={"to": "trending"})   # missing 'from'/'progress'
