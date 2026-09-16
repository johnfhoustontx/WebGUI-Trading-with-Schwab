from shared.contracts.market import MarketSummary


def test_defaults_are_empty_so_no_report_draws_nothing():
    m = MarketSummary()
    assert m.headline == "" and m.highlights == [] and m.report_url == ""


def test_round_trip():
    m = MarketSummary(headline="A rotation, not a rout",
                      highlights=["Chips broke", "Software ripped"],
                      slot="close", slot_label="Market close",
                      report_date="2026-09-14", as_of="16:20 CT",
                      report_url="https://neuralstrike.co/report.html")
    back = MarketSummary.from_json(m.to_json())
    assert back == m


def test_an_older_claude_payload_still_validates():
    """A cache written before 2026-09-16 carries ``narrative``/``inputs``; the
    service's first publish replaces it, but a reader must not choke first."""
    m = MarketSummary(**{"narrative": "old", "inputs": {}, "as_of": "x"})
    assert m.highlights == []
