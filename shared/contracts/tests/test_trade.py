import pytest
from shared.contracts.trade import TradeAnalysis


def test_tradeanalysis_roundtrip():
    ta = TradeAnalysis(
        symbol="AAPL",
        price=212.4,
        bias="BULLISH",
        ema_alignment={"alignment_percentage": 60.0, "bias": "BULLISH", "timeframes": []},
        momentum={"rsi": 55.0, "adx": 22.0, "macd_hist": 0.4, "vwap": 211.0,
                  "relative_volume": 1.3},
        volume_profile={"poc": 210.0, "vah": 213.0, "val": 208.0},
        sector={"etf": "XLK", "name": "Technology", "strength": {"score": 40}},
        position_verdict={"verdict": "BUY", "score": 25, "breakdown": [],
                          "top_reasons": ["ema_alignment (+12)"], "gates_triggered": []},
        investor_verdict={"verdict": "HOLD", "score": 0, "breakdown": [],
                          "top_reasons": ["Insufficient fundamental data"],
                          "gates_triggered": ["No fundamentals"]},
        fundamentals_available=False,
        timestamp="2026-06-16T12:00:00Z",
        errors=[],
    )
    back = TradeAnalysis.from_json(ta.to_json())
    assert back.symbol == "AAPL"
    assert back.position_verdict["verdict"] == "BUY"
    assert back.investor_verdict["verdict"] == "HOLD"
    assert back.sector["etf"] == "XLK"
    assert back.fundamentals_available is False


def test_tradeanalysis_defaults_empty():
    ta = TradeAnalysis(symbol="SPY")
    assert ta.price is None
    assert ta.ema_alignment == {} and ta.momentum == {}
    assert ta.position_verdict == {} and ta.investor_verdict == {}
    assert ta.fundamentals_available is False
    assert ta.errors == []


def test_tradeanalysis_requires_symbol():
    with pytest.raises(Exception):
        TradeAnalysis.from_json('{"price": 1.0}')


def test_tradeanalysis_rejects_wrong_type():
    with pytest.raises(Exception):
        TradeAnalysis.from_json('{"symbol": "SPY", "ema_alignment": "not-a-dict"}')
