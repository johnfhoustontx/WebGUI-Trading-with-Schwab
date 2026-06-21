from shared.contracts.options import RescueAdvisory, RescueCandidate


def test_candidate_defaults():
    cand = RescueCandidate(action="close", label="Close now")
    assert cand.apply_kind == "execute"
    assert cand.warnings == []
    assert cand.net_cash == 0.0


def test_advisory_validates_envelope():
    adv = RescueAdvisory(
        position_id=7, symbol="SPY", strategy="PCS",
        state="tested", heat=72.0,
        candidates=[{"action": "close", "label": "Close now"}],
    )
    assert adv.candidates[0].action == "close"
    assert adv.state == "tested"


def test_advisory_roundtrips_model_dump():
    adv = RescueAdvisory(position_id=1, symbol="SPY", strategy="PCS",
                         state="ok", heat=0.0)
    dumped = adv.model_dump()
    assert RescueAdvisory(**dumped).symbol == "SPY"
