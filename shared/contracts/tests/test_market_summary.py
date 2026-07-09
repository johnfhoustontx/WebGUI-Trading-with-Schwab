from shared.contracts.market import MarketSummary


def test_defaults_and_round_trip():
    assert MarketSummary().narrative == ""
    m = MarketSummary(narrative="Cautious tape.")
    assert MarketSummary.from_json(m.to_json()).narrative == "Cautious tape."
