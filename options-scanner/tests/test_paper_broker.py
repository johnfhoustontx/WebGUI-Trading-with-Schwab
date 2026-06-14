import paper_broker as pb


def _chain(puts=None, calls=None):
    return {"putExpDateMap": puts or {}, "callExpDateMap": calls or {},
            "underlying": {"last": 500.0}}


class _FakeOptions:
    # _fetch_chain reads client.Options.ContractType.ALL when building the request
    class ContractType:
        ALL = "ALL"


def _legs(strike_bid_ask):
    # {strike: (bid, ask)} -> Schwab leg map
    return {"2026-06-03:0": {f"{float(k):.1f}": [{"bid": b, "ask": a, "delta": -0.2}]
                             for k, (b, a) in strike_bid_ask.items()}}


def test_sell_to_open_pcs_fills_at_realistic_limit():
    chain = _chain(puts=_legs({500: (1.10, 1.20), 499: (0.60, 0.70)}))
    price = pb.simulate_fill_price(chain, side="SELL_TO_OPEN", strategy="PCS",
                                   short_strike=500, long_strike=499)
    assert price == 0.48   # net market 0.40x0.60 -> 0.40 + 0.40*0.20


def test_buy_to_close_pcs_fills_at_realistic_limit():
    chain = _chain(puts=_legs({500: (1.10, 1.20), 499: (0.60, 0.70)}))
    price = pb.simulate_fill_price(chain, side="BUY_TO_CLOSE", strategy="PCS",
                                   short_strike=500, long_strike=499)
    assert price == 0.52   # net market 0.40x0.60 -> 0.60 - 0.40*0.20


def test_unquoted_leg_raises_fillerror():
    chain = _chain(puts=_legs({500: (1.10, 1.20)}))  # 499 missing
    import pytest
    with pytest.raises(pb.FillError):
        pb.simulate_fill_price(chain, side="SELL_TO_OPEN", strategy="PCS",
                               short_strike=500, long_strike=499)


def test_iron_condor_sums_both_verticals():
    chain = _chain(
        puts=_legs({495: (0.50, 0.60), 494: (0.30, 0.40)}),
        calls=_legs({505: (0.50, 0.60), 506: (0.30, 0.40)}))
    price = pb.simulate_fill_price(chain, side="SELL_TO_OPEN", strategy="IC",
                                   short_strike=495, long_strike=494,
                                   call_short=505, call_long=506)
    assert price == 0.36   # per wing: net 0.10x0.30 -> 0.18; two wings


def test_build_response_filled_shape():
    resp = pb.build_order_response(order_id=100001, status="FILLED",
        side="SELL_TO_OPEN", strategy="PCS", quantity=3, price=0.40,
        legs=[{"symbol": "SPY...P500", "instruction": "SELL_TO_OPEN"},
              {"symbol": "SPY...P499", "instruction": "BUY_TO_OPEN"}],
        entered_time="t", reason=None)
    assert resp["orderId"] == 100001
    assert resp["status"] == "FILLED"
    assert resp["orderType"] == "NET_CREDIT"
    assert resp["quantity"] == 3 and resp["filledQuantity"] == 3
    assert resp["price"] == 0.40
    assert resp["complexOrderStrategyType"] == "VERTICAL"
    assert len(resp["orderLegCollection"]) == 2
    assert resp["statusDescription"] is None


def test_build_response_rejected_zero_fill():
    resp = pb.build_order_response(order_id=0, status="REJECTED",
        side="SELL_TO_OPEN", strategy="PCS", quantity=3, price=None,
        legs=[], entered_time="t", reason="INSUFFICIENT_BUYING_POWER")
    assert resp["status"] == "REJECTED"
    assert resp["filledQuantity"] == 0
    assert resp["statusDescription"] == "INSUFFICIENT_BUYING_POWER"


def test_iron_condor_complex_strategy_type():
    resp = pb.build_order_response(order_id=1, status="FILLED",
        side="SELL_TO_OPEN", strategy="IC", quantity=1, price=0.20,
        legs=[], entered_time="t", reason=None)
    assert resp["complexOrderStrategyType"] == "IRON_CONDOR"


def test_submit_order_never_calls_trader_endpoint():
    class Boom:
        Options = _FakeOptions
        def place_order(self, *a, **k):
            raise AssertionError("trader endpoint must not be called in PAPER_MODE")
        def get_option_chain(self, *a, **k):
            class R:
                status_code = 200
                def json(self):
                    return {"putExpDateMap": {"e": {
                        "500.0": [{"bid": 1.10, "ask": 1.20, "delta": -0.2}],
                        "499.0": [{"bid": 0.60, "ask": 0.70, "delta": -0.1}]}},
                        "callExpDateMap": {}, "underlying": {"last": 500.0}}
            return R()
    pb.signal_repricer.clear_chain_cache()   # isolate from other live-chain tests
    order = {"signal_id": "s1", "symbol": "SPY", "side": "SELL_TO_OPEN",
             "strategy": "PCS", "short_strike": 500, "long_strike": 499,
             "expiration": "2026-06-03", "quantity": 5}
    resp = pb.submit_order(order, client=Boom())
    assert resp["status"] == "FILLED"
    assert resp["price"] == 0.48   # realistic: net 0.40x0.60 -> 0.40 + 0.40*0.20


def test_submit_order_rejects_when_paper_mode_off(monkeypatch):
    pb.signal_repricer.clear_chain_cache()   # isolate from other live-chain tests
    monkeypatch.setattr(pb.config_paper, "PAPER_MODE", False)
    resp = pb.submit_order({"side": "SELL_TO_OPEN", "strategy": "PCS",
                            "symbol": "SPY", "short_strike": 500,
                            "long_strike": 499, "expiration": "2026-06-03",
                            "quantity": 1}, client=None)
    assert resp["status"] == "REJECTED"
    assert resp["statusDescription"] == "PAPER_MODE_OFF"


def test_submit_order_rejects_unquoted_legs():
    class OneSided:
        Options = _FakeOptions
        def get_option_chain(self, *a, **k):
            class R:
                status_code = 200
                def json(self):
                    return {"putExpDateMap": {"e": {
                        "500.0": [{"bid": 1.10, "ask": 1.20, "delta": -0.2}]}},
                        "callExpDateMap": {}, "underlying": {"last": 500.0}}
            return R()
    pb.signal_repricer.clear_chain_cache()   # isolate from other live-chain tests
    order = {"signal_id": "s1", "symbol": "SPY", "side": "SELL_TO_OPEN",
             "strategy": "PCS", "short_strike": 500, "long_strike": 499,
             "expiration": "2026-06-03", "quantity": 5}
    resp = pb.submit_order(order, client=OneSided())
    assert resp["status"] == "REJECTED"
    assert resp["statusDescription"] == "UNQUOTED_LEGS"
