from pages.options import handoff


def test_signal_to_em_payload_pcs():
    sig = {"type": "PCS", "symbol": "SPY", "expiration": "2026-07-18",
           "short_strike": 540, "long_strike": 535}
    out = handoff.signal_to_em_payload(sig)
    assert out["symbol"] == "SPY" and out["expiry"] == "2026-07-18"
    assert out["legs"] == [
        {"strike": 540.0, "option_type": "put", "side": "short"},
        {"strike": 535.0, "option_type": "put", "side": "long"},
    ]


def test_signal_to_em_payload_iron_condor():
    sig = {"type": "IC", "symbol": "QQQ", "expiration": "2026-07-18",
           "short_strike": 470, "long_strike": 465,
           "call_short": 490, "call_long": 495}
    legs = handoff.signal_to_em_payload(sig)["legs"]
    assert {"strike": 470.0, "option_type": "put", "side": "short"} in legs
    assert {"strike": 495.0, "option_type": "call", "side": "long"} in legs
    assert len(legs) == 4


def test_signal_to_em_payload_strips_dollar_symbol():
    sig = {"type": "LONG_CALL", "symbol": "$SPX", "expiration": "2026-07-18",
           "long_strike": 5400}
    out = handoff.signal_to_em_payload(sig)
    assert out["symbol"] == "SPX"
    assert out["legs"] == [{"strike": 5400.0, "option_type": "call", "side": "long"}]
