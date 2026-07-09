from shared.contracts.market import MarketSummary


def test_defaults_and_round_trip():
    assert MarketSummary().narrative == ""
    assert MarketSummary().generated_at is None
    m = MarketSummary(narrative="Cautious tape.", generated_at="2026-07-08T12:00:00Z")
    assert MarketSummary.from_json(m.to_json()).narrative == "Cautious tape."
