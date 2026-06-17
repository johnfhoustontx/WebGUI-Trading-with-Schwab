"""Tests for the portfolio-domain contract (Task #19).

``PortfolioModel`` is the single cache view the portfolio service publishes
(``cache:portfolio:positions``): display-ready holdings / sector / performance
rows (already formatted by the engine ``view_model`` in Tier 2), the per-symbol
advisory ``suggestions``, and a little live/health meta. Like the other domain
contracts it validates the *envelope* shape (the lists/dicts are the right
container types) as a gate against gross drift BEFORE caching — it does NOT
over-specify each row (rows are sparse display dicts that tolerate missing keys).
"""
import pytest

from shared.contracts.portfolio import PortfolioModel


def test_model_roundtrip():
    m = PortfolioModel(
        holdings_rows=[{"symbol": "AAPL", "sector": "Technology",
                        "market_value": "$1,000.00", "day_pl": "+$10.00"}],
        sector_rows=[{"sector": "Technology", "weight": "30.0%",
                      "benchmark_delta": "+2.0%"}],
        performance_rows=[{"symbol": "AAPL", "composite": "3.0 (B)",
                           "top_action": "HOLD"}],
        suggestions={"AAPL": [{"action": "HOLD", "severity": "low",
                               "reason": "performing in line."}]},
        proxy_up=True,
        streaming=True,
    )
    back = PortfolioModel.from_json(m.to_json())
    assert back.holdings_rows[0]["symbol"] == "AAPL"
    assert back.sector_rows[0]["weight"] == "30.0%"
    assert back.performance_rows[0]["composite"] == "3.0 (B)"
    assert back.suggestions["AAPL"][0]["action"] == "HOLD"
    assert back.proxy_up is True and back.streaming is True


def test_model_defaults_empty():
    m = PortfolioModel()
    assert m.holdings_rows == [] and m.sector_rows == []
    assert m.performance_rows == [] and m.suggestions == {}
    assert m.proxy_up is False and m.streaming is False
    assert m.errors == [] and m.timestamp is None


def test_model_rejects_wrong_type():
    # The display-row lists / suggestions map must be the right container types —
    # a gross drift must raise before it can be cached.
    with pytest.raises(Exception):
        PortfolioModel.from_json('{"holdings_rows": "nope"}')
    with pytest.raises(Exception):
        PortfolioModel.from_json('{"suggestions": "nope"}')
