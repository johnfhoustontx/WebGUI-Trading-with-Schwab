from shared.contracts.market import MarketSummary


def test_defaults_and_round_trip():
    assert MarketSummary().narrative == ""
    m = MarketSummary(narrative="Cautious tape.")
    assert MarketSummary.from_json(m.to_json()).narrative == "Cautious tape."


def test_inputs_and_as_of_are_additive_with_empty_defaults():
    """An older payload (narrative only) must still validate - the fields are
    additive, and the Desk treats empty ones as 'no provenance'."""
    m = MarketSummary(narrative="Quiet tape.")
    assert m.inputs == {} and m.as_of == ""
    full = MarketSummary(narrative="x", inputs={"bias": "Cautious"},
                         as_of="2026-09-10T15:42:00+00:00")
    back = MarketSummary.from_json(full.to_json())
    assert back.inputs == {"bias": "Cautious"}
    assert back.as_of == "2026-09-10T15:42:00+00:00"
