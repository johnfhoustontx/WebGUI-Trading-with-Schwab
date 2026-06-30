import strategy_scanner as ss


def _contract(strike, delta, mark, **kw):
    base = {"delta": delta, "mark": mark, "bid": mark - 0.05, "ask": mark + 0.05,
            "theta": -0.02, "vega": 0.10, "gamma": 0.01, "volatility": 18.0,
            "totalVolume": 500, "openInterest": 1000}
    base.update(kw)
    return base


def _chain():
    return {
        "underlyingPrice": 450.0,
        "callExpDateMap": {"2026-07-10:10": {
            "450.0": [_contract(450.0, 0.50, 6.0)],
            "455.0": [_contract(455.0, 0.32, 3.5)],
            "460.0": [_contract(460.0, 0.18, 1.8)]}},
        "putExpDateMap": {"2026-07-10:10": {
            "450.0": [_contract(450.0, -0.50, 6.0)],
            "445.0": [_contract(445.0, -0.32, 3.5)],
            "440.0": [_contract(440.0, -0.18, 1.8)]}},
    }


def _leg(kind, side, strike, mark, qty=1, **kw):
    g = {"delta": 0.5, "theta": -0.02, "vega": 0.1, "gamma": 0.01, "iv": 18.0}
    g.update(kw)
    return {"kind": kind, "side": side, "strike": strike, "expiration": "2026-07-10",
            "qty": qty, "mark": mark, **g}


# ---- Task 1 ----
def test_extract_options_groups_by_expiration():
    out = ss.extract_options(_chain(), "call", dte_min=5, dte_max=30)
    assert "2026-07-10" in out
    exp = out["2026-07-10"]
    assert exp["dte"] == 10
    assert set(exp["strikes"]) == {450.0, 455.0, 460.0}
    assert exp["strikes"][455.0]["delta"] == 0.32
    assert exp["strikes"][455.0]["mark"] == 3.5


def test_extract_options_filters_dte_window():
    chain = _chain()
    chain["callExpDateMap"]["2026-12-18:171"] = chain["callExpDateMap"]["2026-07-10:10"]
    out = ss.extract_options(chain, "call", dte_min=5, dte_max=30)
    assert list(out) == ["2026-07-10"]


# ---- Task 2 ----
def test_nearest_by_delta_picks_closest_abs_delta():
    strikes = ss.extract_options(_chain(), "call", 5, 30)["2026-07-10"]["strikes"]
    leg = ss.nearest_by_delta(strikes, 0.30)
    assert leg["strike"] == 455.0


def test_nearest_by_delta_empty_returns_none():
    assert ss.nearest_by_delta({}, 0.30) is None


# ---- Task 3 ----
def test_payoff_long_call_unbounded_profit_capped_loss():
    legs = [_leg("call", "long", 450.0, 6.0)]
    m = ss.payoff_metrics(legs, spot=450.0)
    assert m["net_debit"] == 6.0 and m["net_credit"] is None
    assert m["max_loss"] == 6.0
    assert m["unbounded"] is True
    assert abs(m["breakevens"][0] - 456.0) < 0.5


def test_payoff_bull_call_debit_spread_bounded():
    legs = [_leg("call", "long", 450.0, 6.0), _leg("call", "short", 455.0, 3.5)]
    m = ss.payoff_metrics(legs, spot=450.0)
    assert abs(m["net_debit"] - 2.5) < 1e-6
    assert abs(m["max_loss"] - 2.5) < 0.05
    assert abs(m["max_profit"] - 2.5) < 0.1
    assert m["unbounded"] is False
    assert abs(m["breakevens"][0] - 452.5) < 0.2


def test_payoff_put_credit_spread_max_loss_width_minus_credit():
    legs = [_leg("put", "short", 445.0, 3.5), _leg("put", "long", 440.0, 1.8)]
    m = ss.payoff_metrics(legs, spot=450.0)
    assert abs(m["net_credit"] - 1.7) < 1e-6
    assert abs(m["max_profit"] - 1.7) < 0.05
    assert abs(m["max_loss"] - 3.3) < 0.1


# ---- Task 4 ----
def test_pop_long_call_is_low_side_probability():
    legs = [_leg("call", "long", 450.0, 6.0)]
    pop = ss.pop_from_payoff(legs, spot=450.0, atm_iv=0.18, dte=10)
    assert 5 < pop < 45


def test_pop_put_credit_spread_is_high():
    legs = [_leg("put", "short", 445.0, 3.5), _leg("put", "long", 440.0, 1.8)]
    pop = ss.pop_from_payoff(legs, spot=450.0, atm_iv=0.18, dte=10)
    assert pop > 55


# ---- Task 5 ----
def test_build_directional_emits_long_and_naked_each_side():
    chain = _chain()
    sigs = ss.build_directional(chain, "SPY", spot=450.0, atm_iv=0.18,
                                dte_min=5, dte_max=30)
    types = {s["type"] for s in sigs}
    assert {"LONG_CALL", "LONG_PUT", "SHORT_CALL", "SHORT_PUT"} <= types
    lc = next(s for s in sigs if s["type"] == "LONG_CALL")
    assert lc["family"] == "DIRECTIONAL" and lc["bias"] == "bullish"
    assert len(lc["legs"]) == 1 and lc["legs"][0]["side"] == "long"
    assert lc["max_loss"] > 0 and lc["pop_pct"] is not None
    assert lc["id"].startswith("SPY_LONG_CALL_")


# ---- Task 6 ----
def test_build_debit_verticals_bull_call_and_bear_put():
    sigs = ss.build_debit_verticals(_chain(), "SPY", 450.0, 0.18, 5, 30)
    bc = next(s for s in sigs if s["type"] == "BULL_CALL")
    assert bc["family"] == "VERTICAL" and bc["bias"] == "bullish"
    assert len(bc["legs"]) == 2
    assert bc["net_debit"] and bc["max_profit"] and not bc["unbounded"]
    assert any(s["type"] == "BEAR_PUT" for s in sigs)


# ---- Task 7 ----
def test_adapt_credit_spread_pcs_to_normalized():
    pcs = {"id": "SPY_PCS_2026-07-10_445.0_440.0", "symbol": "SPY", "type": "PCS",
           "expiration": "2026-07-10", "dte": 10, "short_strike": 445.0,
           "long_strike": 440.0, "short_mark": 3.5, "long_mark": 1.8,
           "credit": 1.7, "max_loss": 3.3, "pop_pct": 68.0, "underlying_price": 450.0,
           "short_delta": -0.32, "net_theta": 0.04, "net_vega": -0.02}
    n = ss.adapt_credit_spread(pcs)
    assert n["family"] == "VERTICAL" and n["bias"] == "bullish"
    assert n["net_credit"] == 1.7 and n["max_loss"] == 3.3
    assert [l["side"] for l in n["legs"]] == ["short", "long"]
    assert n["legs"][0]["kind"] == "put"


def test_adapt_credit_spread_ccs_to_normalized():
    ccs = {"id": "SPY_CCS_2026-07-10_455.0_460.0", "symbol": "SPY", "type": "CCS",
           "expiration": "2026-07-10", "dte": 10, "short_strike": 455.0,
           "long_strike": 460.0, "short_mark": 3.5, "long_mark": 1.8,
           "credit": 1.7, "max_loss": 3.3, "pop_pct": 68.0, "underlying_price": 450.0,
           "short_delta": 0.32}
    n = ss.adapt_credit_spread(ccs)
    assert n["family"] == "VERTICAL" and n["bias"] == "bearish"
    assert n["strategy_label"] == "Call Credit Spread"
    assert n["net_credit"] == 1.7 and n["max_profit"] == 1.7
    assert [l["kind"] for l in n["legs"]] == ["call", "call"]
    assert [l["side"] for l in n["legs"]] == ["short", "long"]


def test_adapt_iron_condor_to_normalized():
    ic = {"id": "SPY_IC_2026-07-10", "symbol": "SPY", "type": "IC",
          "expiration": "2026-07-10", "dte": 10,
          "short_strike": 445.0, "long_strike": 440.0,
          "call_short": 455.0, "call_long": 460.0,
          "credit": 2.5, "max_loss": 2.5, "pop_pct": 65.0, "underlying_price": 450.0}
    n = ss.adapt_iron_condor(ic)
    assert n["family"] == "NEUTRAL" and n["bias"] == "neutral"
    assert n["strategy_label"] == "Iron Condor"
    assert n["net_credit"] == 2.5 and n["max_profit"] == 2.5
    assert len(n["legs"]) == 4
    kinds = {l["kind"] for l in n["legs"]}
    assert kinds == {"put", "call"}
