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
# NOTE (C10 + C1): payoff_metrics now reports per-CONTRACT dollars (x100) net of
# round-trip commission ($0.65/leg x n_legs x 2). Equity rate assumed (no symbol).
def test_payoff_long_call_unbounded_profit_capped_loss():
    legs = [_leg("call", "long", 450.0, 6.0)]
    m = ss.payoff_metrics(legs, spot=450.0)
    # net_debit = 6.0 x 100 = 600; 1-leg round-trip commission = 0.65x1x2 = 1.30
    assert m["net_debit"] == 600.0 and m["net_credit"] is None
    assert abs(m["max_loss"] - 601.30) < 0.01   # 600 debit + 1.30 commission
    assert m["commission"] == 1.30
    assert m["unbounded"] is True
    assert abs(m["breakevens"][0] - 456.0) < 0.5   # breakeven = price level, unshifted


def test_payoff_bull_call_debit_spread_bounded():
    legs = [_leg("call", "long", 450.0, 6.0), _leg("call", "short", 455.0, 3.5)]
    m = ss.payoff_metrics(legs, spot=450.0)
    # net_debit 2.5 -> 250; commission 2 legs x 0.65 x 2 = 2.60
    assert abs(m["net_debit"] - 250.0) < 1e-6
    assert abs(m["max_loss"] - 252.60) < 0.05    # 250 + 2.60 commission
    assert abs(m["max_profit"] - 247.40) < 0.1   # 250 - 2.60 commission
    assert m["unbounded"] is False
    assert abs(m["breakevens"][0] - 452.5) < 0.2


def test_payoff_put_credit_spread_max_loss_width_minus_credit():
    legs = [_leg("put", "short", 445.0, 3.5), _leg("put", "long", 440.0, 1.8)]
    m = ss.payoff_metrics(legs, spot=450.0)
    # net_credit 1.7 -> 170; commission 2.60
    assert abs(m["net_credit"] - 170.0) < 1e-6
    assert abs(m["max_profit"] - 167.40) < 0.05  # 170 - 2.60 commission
    assert abs(m["max_loss"] - 332.60) < 0.1     # 330 + 2.60 commission


def test_payoff_naked_short_call_unbounded_loss():
    legs = [_leg("call", "short", 455.0, 3.5)]
    m = ss.payoff_metrics(legs, spot=450.0)
    assert abs(m["net_credit"] - 350.0) < 1e-6 and m["net_debit"] is None
    assert m["unbounded"] is True
    assert abs(m["max_profit"] - 348.70) < 0.05   # 350 credit - 1.30 commission
    assert m["capital"] > 0                        # margin proxy (x100) + commission


def test_payoff_naked_short_put_bounded_loss():
    legs = [_leg("put", "short", 445.0, 3.5)]
    m = ss.payoff_metrics(legs, spot=450.0)
    assert m["unbounded"] is False
    assert abs(m["net_credit"] - 350.0) < 1e-6
    # (strike 445 - credit 3.5) x 100 = 44150, + 1.30 commission
    assert abs(m["max_loss"] - 44151.30) < 0.5
    assert abs(m["max_profit"] - 348.70) < 0.05   # 350 - 1.30 commission


# ---- Task 4 ----
def test_pop_long_call_is_low_side_probability():
    legs = [_leg("call", "long", 450.0, 6.0)]
    pop = ss.pop_from_payoff(legs, spot=450.0, atm_iv=0.18, dte=10)
    assert 5 < pop < 45


def test_pop_put_credit_spread_is_high():
    legs = [_leg("put", "short", 445.0, 3.5), _leg("put", "long", 440.0, 1.8)]
    pop = ss.pop_from_payoff(legs, spot=450.0, atm_iv=0.18, dte=10)
    assert pop > 55


# ---- E1 Task 1: liquidity fields carried onto normalized legs ----
def test_build_directional_legs_carry_liquidity_fields():
    lc = next(s for s in ss.build_directional(_chain(), "SPY", 450.0, 0.18, 5, 30)
              if s["type"] == "LONG_CALL")
    leg = lc["legs"][0]
    assert "bid" in leg and "ask" in leg and leg["ask"] > leg["bid"]
    assert "volume" in leg and "oi" in leg


def test_adapt_credit_spread_short_leg_carries_source_liquidity():
    pcs = {"id": "SPY_PCS", "symbol": "SPY", "type": "PCS",
           "expiration": "2026-07-10", "dte": 10, "short_strike": 445.0,
           "long_strike": 440.0, "short_mark": 3.5, "long_mark": 1.8,
           "credit": 1.7, "max_loss": 3.3, "underlying_price": 450.0,
           "short_delta": -0.32, "bid": 1.65, "ask": 1.75, "volume": 320}
    n = ss.adapt_credit_spread(pcs)
    short_leg = n["legs"][0]
    assert short_leg["bid"] == 1.65 and short_leg["ask"] == 1.75
    assert short_leg["volume"] == 320
    # long leg / missing values stay absent so norm_liquidity degrades to 50
    long_leg = n["legs"][1]
    assert "bid" not in long_leg and "ask" not in long_leg


def test_adapt_iron_condor_short_legs_carry_source_liquidity():
    # Source IC carries put-side liquidity at top-level bid/ask/volume and
    # call-side liquidity at call_bid/call_ask/call_volume; BOTH short legs must
    # carry them so the IC liquidity gate checks both sides.
    ic = {"id": "SPY_IC", "symbol": "SPY", "type": "IC",
          "expiration": "2026-07-10", "dte": 10,
          "short_strike": 445.0, "long_strike": 440.0, "short_mark": 3.5, "long_mark": 1.8,
          "call_short": 455.0, "call_long": 460.0, "call_short_mark": 3.2, "call_long_mark": 1.6,
          "credit": 3.3, "max_loss": 1.7, "underlying_price": 450.0,
          "bid": 1.68, "ask": 1.72, "volume": 400,
          "call_bid": 1.58, "call_ask": 1.62, "call_volume": 350}
    n = ss.adapt_iron_condor(ic)
    put_short = next(l for l in n["legs"] if l["kind"] == "put" and l["side"] == "short")
    call_short = next(l for l in n["legs"] if l["kind"] == "call" and l["side"] == "short")
    assert put_short["bid"] == 1.68 and put_short["ask"] == 1.72 and put_short["volume"] == 400
    assert call_short["bid"] == 1.58 and call_short["ask"] == 1.62 and call_short["volume"] == 350
    # long legs stay absent (no fabrication)
    for l in n["legs"]:
        if l["side"] == "long":
            assert "bid" not in l and "ask" not in l


def test_adapt_iron_condor_call_short_liquidity_absent_when_source_lacks_it():
    # If the source IC has no call-side bid/ask, the call-short leg leaves them
    # absent (norm_liquidity degrades to 50) — no fabrication.
    ic = {"id": "SPY_IC", "symbol": "SPY", "type": "IC",
          "expiration": "2026-07-10", "dte": 10,
          "short_strike": 445.0, "long_strike": 440.0, "short_mark": 3.5, "long_mark": 1.8,
          "call_short": 455.0, "call_long": 460.0, "call_short_mark": 3.2, "call_long_mark": 1.6,
          "credit": 3.3, "max_loss": 1.7, "underlying_price": 450.0,
          "bid": 1.68, "ask": 1.72}
    n = ss.adapt_iron_condor(ic)
    call_short = next(l for l in n["legs"] if l["kind"] == "call" and l["side"] == "short")
    assert "bid" not in call_short and "ask" not in call_short


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
    # per-contract dollars net of commission (2 legs x 0.65 x 2 = 2.60):
    # net_credit 1.7 -> 170; max_loss 3.3 -> 330 + 2.60 = 332.60
    assert n["net_credit"] == 170.0 and abs(n["max_loss"] - 332.60) < 0.01
    assert abs(n["max_profit"] - 167.40) < 0.01    # 170 - 2.60 commission
    assert n["commission"] == 2.60
    assert [l["side"] for l in n["legs"]] == ["short", "long"]
    assert n["legs"][0]["kind"] == "put"
    # full normalized shape: structural keys populated, source greeks preserved
    assert isinstance(n["breakevens"], list) and n["breakevens"]
    assert abs(n["breakevens"][0] - 443.3) < 0.3   # short_strike - credit = 445 - 1.7
    assert abs(n["capital"] - n["max_loss"]) < 0.01   # capital == dollar max_loss
    assert n["rr"] is not None
    assert n["net_delta"] is not None
    assert n["timestamp"] is not None
    assert n["net_theta"] == 0.04 and n["net_vega"] == -0.02  # source greeks win
    assert n["net_debit"] is None and n["unbounded"] is False


def test_adapt_credit_spread_ccs_to_normalized():
    ccs = {"id": "SPY_CCS_2026-07-10_455.0_460.0", "symbol": "SPY", "type": "CCS",
           "expiration": "2026-07-10", "dte": 10, "short_strike": 455.0,
           "long_strike": 460.0, "short_mark": 3.5, "long_mark": 1.8,
           "credit": 1.7, "max_loss": 3.3, "pop_pct": 68.0, "underlying_price": 450.0,
           "short_delta": 0.32}
    n = ss.adapt_credit_spread(ccs)
    assert n["family"] == "VERTICAL" and n["bias"] == "bearish"
    assert n["strategy_label"] == "Call Credit Spread"
    # net_credit 1.7 -> 170; max_profit = 170 - 2.60 commission = 167.40
    assert n["net_credit"] == 170.0 and abs(n["max_profit"] - 167.40) < 0.01
    assert [l["kind"] for l in n["legs"]] == ["call", "call"]
    assert [l["side"] for l in n["legs"]] == ["short", "long"]


def test_adapt_iron_condor_to_normalized():
    # Production shape: build_iron_condors always carries the four leg marks +
    # underlying_price. Put short 445/3.5, put long 440/1.8 -> put credit 1.7;
    # call short 455/3.2, call long 460/1.6 -> call credit 1.6; total credit 3.3,
    # 5-wide wings -> max_loss 5 - 3.3 = 1.7.
    ic = {"id": "SPY_IC_2026-07-10", "symbol": "SPY", "type": "IC",
          "expiration": "2026-07-10", "dte": 10,
          "short_strike": 445.0, "long_strike": 440.0, "short_mark": 3.5, "long_mark": 1.8,
          "call_short": 455.0, "call_long": 460.0, "call_short_mark": 3.2, "call_long_mark": 1.6,
          "credit": 3.3, "max_loss": 1.7, "pop_pct": 65.0, "underlying_price": 450.0}
    n = ss.adapt_iron_condor(ic)
    assert n["family"] == "NEUTRAL" and n["bias"] == "neutral"
    assert n["strategy_label"] == "Iron Condor"
    # 4-leg IC: commission = 4 x 0.65 x 2 = $5.20 (the finding's example).
    # net_credit 3.3 -> 330; max_profit = 330 - 5.20 = 324.80; the $5.20 is a real
    # ~1.6% haircut on a $330 credit, enough to move a gate.
    assert n["net_credit"] == 330.0 and abs(n["max_profit"] - 324.80) < 0.01
    assert n["commission"] == 5.20
    assert len(n["legs"]) == 4
    kinds = {l["kind"] for l in n["legs"]}
    assert kinds == {"put", "call"}
    # full normalized shape with REAL breakeven values (marks present)
    bes = sorted(n["breakevens"])
    assert len(bes) == 2
    assert abs(bes[0] - (445.0 - 3.3)) < 0.3   # put_short - credit = 441.7
    assert abs(bes[1] - (455.0 + 3.3)) < 0.3   # call_short + credit = 458.3
    # max_loss 1.7 -> 170 + 5.20 = 175.20; capital == dollar max_loss
    assert abs(n["max_loss"] - 175.20) < 0.05
    assert abs(n["capital"] - 175.20) < 0.05
    assert abs(n["rr"] - (324.80 / 175.20)) < 0.02   # net max_profit / net max_loss
    assert n["net_delta"] is not None and n["net_gamma"] is not None
    assert n["timestamp"] is not None
    assert n["net_debit"] is None and n["unbounded"] is False


def test_adapt_iron_condor_marks_absent_falls_back_to_source_breakevens():
    # Latent landmine guard: if leg marks are missing, payoff_metrics sees a
    # zero-cost IC and would compute wrong economics. The adapter must fall back
    # to source-derived breakevens / capital / rr instead.
    ic = {"id": "SPY_IC_2026-07-10", "symbol": "SPY", "type": "IC",
          "expiration": "2026-07-10", "dte": 10,
          "short_strike": 445.0, "long_strike": 440.0,
          "call_short": 455.0, "call_long": 460.0,
          "credit": 3.3, "max_loss": 1.7, "pop_pct": 65.0, "underlying_price": 450.0}
    n = ss.adapt_iron_condor(ic)
    bes = sorted(n["breakevens"])
    assert len(bes) == 2
    assert abs(bes[0] - (445.0 - 3.3)) < 0.05   # put_short - credit = 441.7
    assert abs(bes[1] - (455.0 + 3.3)) < 0.05   # call_short + credit = 458.3
    # source-derived economics still x100 + commission (5.20) in the marks-absent path
    assert abs(n["capital"] - 175.20) < 0.05    # (1.7 x 100) + 5.20, == dollar max_loss
    assert abs(n["rr"] - (324.80 / 175.20)) < 0.02


def test_adapt_credit_spread_marks_absent_falls_back():
    pcs = {"id": "SPY_PCS", "symbol": "SPY", "type": "PCS",
           "expiration": "2026-07-10", "dte": 10, "short_strike": 445.0,
           "long_strike": 440.0, "credit": 1.7, "max_loss": 3.3,
           "underlying_price": 450.0}
    n = ss.adapt_credit_spread(pcs)
    assert len(n["breakevens"]) == 1
    assert abs(n["breakevens"][0] - (445.0 - 1.7)) < 0.05   # short - credit = 443.3
    # 2-leg PCS commission 2.60: capital = (3.3 x 100) + 2.60 = 332.60, == max_loss
    assert abs(n["capital"] - 332.60) < 0.05
    assert abs(n["rr"] - (167.40 / 332.60)) < 0.02   # net max_profit / net max_loss


# ---- C10: cross-family unit consistency (per-CONTRACT dollars everywhere) ----
def test_cross_family_max_loss_same_scale():
    # A directional long put and an adapted put-credit-spread of comparable risk
    # must report max_loss on the SAME (x100 per-contract) scale — before the fix
    # the directional was ~1/100th of the credit adapter's scale.
    lp = ss.payoff_metrics([_leg("put", "long", 445.0, 3.3)], spot=450.0)
    pcs = ss.adapt_credit_spread(
        {"id": "X", "symbol": "SPY", "type": "PCS", "expiration": "2026-07-10",
         "dte": 10, "short_strike": 445.0, "long_strike": 440.0,
         "short_mark": 3.5, "long_mark": 1.8, "credit": 1.7, "max_loss": 3.3,
         "underlying_price": 450.0})
    # both in the hundreds, not one ~3 and the other ~330
    assert lp["max_loss"] > 100 and pcs["max_loss"] > 100
    # same order of magnitude (ratio within ~3x), not ~100x apart
    assert 0.3 < (lp["max_loss"] / pcs["max_loss"]) < 3.0


# ---- E1 code-review fix: build_iron_condors forwards liquidity end-to-end ----
def _spread(side, short_k, long_k, short_delta, bid, ask, volume):
    return {"symbol": "TEST", "type": side, "expiration": "2026-07-10", "dte": 10,
            "short_strike": short_k, "long_strike": long_k,
            "short_mark": 1.5, "long_mark": 0.5, "width": 5,
            "credit": 1.0, "max_loss": 4.0, "rr_pct": 25.0, "pop_pct": 85.0,
            "short_delta": short_delta, "net_theta": -0.03,
            "breakeven": (short_k - 1.0) if side == "PCS" else (short_k + 1.0),
            "trade_type": "SWING", "underlying_price": 450.0,
            "bid": bid, "ask": ask, "volume": volume}


def test_build_iron_condors_forwards_short_leg_liquidity():
    from scanner_engine import build_iron_condors
    pcs = _spread("PCS", 445.0, 440.0, -0.15, bid=1.48, ask=1.52, volume=400)
    ccs = _spread("CCS", 455.0, 460.0, 0.15, bid=1.18, ask=1.22, volume=350)
    ic = build_iron_condors([pcs, ccs], max_n=1)[0]
    # additive liquidity keys forwarded from the two source spreads
    assert ic["bid"] == 1.48 and ic["ask"] == 1.52 and ic["volume"] == 400
    assert ic["call_bid"] == 1.18 and ic["call_ask"] == 1.22 and ic["call_volume"] == 350


def test_iron_condor_liquidity_gate_lit_up_end_to_end():
    # A liquid IC passes the NEUTRAL liq gate; an illiquid one (wide short spreads)
    # now FAILS it — proving the gate is no longer inert on real build output.
    import scanner_engine as se
    import strategy_scoring as sc

    liquid_pcs = _spread("PCS", 445.0, 440.0, -0.15, bid=1.48, ask=1.52, volume=400)
    liquid_ccs = _spread("CCS", 455.0, 460.0, 0.15, bid=1.18, ask=1.22, volume=350)
    liq_ic = ss.adapt_iron_condor(se.build_iron_condors([liquid_pcs, liquid_ccs], 1)[0])
    liq_gates = sc.evaluate_gates(liq_ic)
    assert "liquidity" not in liq_gates["reasons"]

    # wide spreads (spread ~ 40% of mark) -> norm_liquidity ~ 0 on both shorts
    wide_pcs = _spread("PCS", 445.0, 440.0, -0.15, bid=1.0, ask=1.6, volume=400)
    wide_ccs = _spread("CCS", 455.0, 460.0, 0.15, bid=1.0, ask=1.6, volume=350)
    wide_ic = ss.adapt_iron_condor(se.build_iron_condors([wide_pcs, wide_ccs], 1)[0])
    wide_gates = sc.evaluate_gates(wide_ic)
    assert "liquidity" in wide_gates["reasons"] and not wide_gates["passed_min"]
