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


def test_trade_accepts_payload_without_markov():
    t = TradeAnalysis(symbol="AAPL")
    assert t.markov is None


def test_trade_accepts_payload_with_markov():
    t = TradeAnalysis(symbol="AAPL", markov={
        "current_band": 3, "band_labels": ["a"], "transition_row": [0.2] * 5,
        "horizons": [{"n": 10, "dist": [0.2] * 5, "p_buy": 0.3, "p_sell": 0.1,
                      "e_score": 12.0}],
        "drift": 8.0, "tilt": 6.0, "markov_adjusted_score": 46.0,
        "confidence": 0.7, "prior_version": "2026-06-21",
    })
    assert t.markov["current_band"] == 3


def test_trade_accepts_swing_model_block():
    t = TradeAnalysis(symbol="AAPL", swing_model={
        "verdict": "BUY", "score": 1.4, "percentile": 88, "expected_fwd": 0.0135,
        "hit_rate": 0.523, "horizon_days": 20,
        "contributions": [{"factor": "mom_12_1", "z": 1.2, "weight": 0.211,
                           "contribution": 0.25, "ic": 0.041}],
        "model_version": "2026-06-28", "oos_ic": 0.0367, "source": "validated"})
    assert t.swing_model["verdict"] == "BUY"


def test_trade_swing_model_optional():
    assert TradeAnalysis(symbol="AAPL").swing_model is None
