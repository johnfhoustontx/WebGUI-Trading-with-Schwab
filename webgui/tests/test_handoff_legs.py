# webgui/tests/test_handoff_legs.py
from pages.options import handoff


def test_calculator_legs_stash_is_one_shot_and_separate_from_signal():
    legs_payload = {"symbol": "QQQ", "legs": []}
    handoff.set_pending_calculator_legs(legs_payload)
    # the scanner-signal calculator stash is a DIFFERENT slot and stays empty
    assert handoff.take_pending_calculator() is None
    assert handoff.take_pending_calculator_legs() == legs_payload
    assert handoff.take_pending_calculator_legs() is None
