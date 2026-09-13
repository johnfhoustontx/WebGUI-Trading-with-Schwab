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


def test_payoff_metrics_distinguishes_unbounded_profit_from_unbounded_loss():
    """`unbounded` alone conflates a long call with a naked short call.

    A long call has unlimited PROFIT and a capped loss (the debit); a naked
    short call has a capped profit (the credit) and unlimited LOSS. Both set
    call_coeff != 0, so the display cannot tell them apart from `unbounded`.
    """
    long_call = ss.payoff_metrics([_leg("call", "long", 460.0, 2.0)], spot=450.0)
    assert long_call["unbounded_profit"] is True
    assert long_call["unbounded_loss"] is False
    assert long_call["max_profit"] is None          # genuinely unlimited

    short_call = ss.payoff_metrics([_leg("call", "short", 460.0, 2.0)], spot=450.0)
    assert short_call["unbounded_profit"] is False
    assert short_call["unbounded_loss"] is True
    assert short_call["max_profit"] is not None     # capped at the credit
    assert short_call["max_profit"] > 0


def test_payoff_metrics_bounded_structure_flags_neither():
    legs = [_leg("call", "long", 460.0, 2.0), _leg("call", "short", 465.0, 1.0)]
    m = ss.payoff_metrics(legs, spot=450.0)
    assert m["unbounded_profit"] is False
    assert m["unbounded_loss"] is False
    assert m["unbounded"] is False


def test_payoff_metrics_keeps_legacy_unbounded_flag():
    """`unbounded` stays as the OR of both, for back-compat with paper_trader."""
    for side in ("long", "short"):
        m = ss.payoff_metrics([_leg("call", side, 460.0, 2.0)], spot=450.0)
        assert m["unbounded"] is True


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


def test_adapt_credit_spread_without_credit_is_defined_risk_unknown_reward():
    """A credit spread with NO source `credit` must never look unbounded.

    _normalize_credit leaves max_profit None here (reward unknown), which is NOT
    a synonym for unlimited. The webgui's Max P cell decodes exactly this pair
    (`max_profit is None` + `unbounded` False) to render '—' rather than '∞' --
    a cross-process invariant enforced only by prose in strategy_table.py, so
    pin it HERE, at the producer. If any of these flip True, that page would
    claim unlimited profit on a defined-risk credit spread.
    """
    pcs = {"id": "SPY_PCS", "symbol": "SPY", "type": "PCS",
           "expiration": "2026-07-10", "dte": 10, "short_strike": 445.0,
           "long_strike": 440.0, "short_mark": 3.5, "long_mark": 1.8,
           "max_loss": 3.3, "underlying_price": 450.0}   # NOTE: no "credit"
    n = ss.adapt_credit_spread(pcs)
    assert n["max_profit"] is None          # reward unknown, not unlimited
    assert n["unbounded"] is False
    assert n["unbounded_profit"] is False
    assert n["unbounded_loss"] is False


def test_adapt_credit_spread_normalizes_all_three_unbounded_flags():
    """The adapter declares defined risk authoritatively, not via the legs.

    `unbounded` is force-set False here because the source economics are
    authoritative; the two side flags must be normalized in the SAME place or a
    leg-derived value could silently outrank it (_loss_is_unbounded checks
    unbounded_loss first).
    """
    pcs = {"id": "SPY_PCS", "symbol": "SPY", "type": "PCS",
           "expiration": "2026-07-10", "dte": 10, "short_strike": 445.0,
           "long_strike": 440.0, "short_mark": 3.5, "long_mark": 1.8,
           "credit": 1.7, "max_loss": 3.3, "underlying_price": 450.0}
    n = ss.adapt_credit_spread(pcs)
    assert n["unbounded"] is False
    assert n["unbounded_profit"] is False
    assert n["unbounded_loss"] is False


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


# ---- A4: the short legs honour the caller's delta band ----------------------
# build_directional took NO band and always targeted _SHORT_DELTA (0.28), while
# screen_spreads beside it in the same swing_scan call got the band the caller
# supplied. So the Income Window's documented 0.15-0.25 short-put band applied to
# its spreads and not to its cash-secured put.
#
# Measured on the live XOM 2026-10-16 ladder (2026-09-11), which is $5-wide and
# carries exactly three candidate strikes:
#     |delta| 0.131 @ 150   0.218 @ 155   0.328 @ 160
# Target 0.28 picks 160 at 0.328 - a third more assignment risk than the window
# documents. Target 0.20 (the band midpoint) picks 155 at 0.218. The fixture
# chain below is the same shape: puts at 0.18 / 0.32 / 0.50.


def test_a_short_put_with_no_band_keeps_the_legacy_target():
    """Back-compat. Every caller that passes no band must be untouched: 0.28 is
    nearest 0.32 on this ladder, which is what shipped."""
    sigs = ss.build_directional(_chain(), "SPY", 450.0, 0.18, 5, 30)
    sp = next(s for s in sigs if s["type"] == "SHORT_PUT")
    assert abs(sp["legs"][0]["delta"]) == 0.32


def test_a_band_moves_the_short_put_to_the_midpoint_strike():
    """The fix, on a miniature of the measured ladder: the 0.15-0.25 band targets
    0.20 and picks 0.18, not 0.32."""
    sigs = ss.build_directional(_chain(), "SPY", 450.0, 0.18, 5, 30,
                                put_band=(0.15, 0.25))
    sp = next(s for s in sigs if s["type"] == "SHORT_PUT")
    assert abs(sp["legs"][0]["delta"]) == 0.18
    assert sp["legs"][0]["strike"] == 440.0


def test_the_band_is_read_as_an_ABSOLUTE_delta_whichever_sign_it_arrives_in():
    """``compute.INCOME_PUT_DELTA`` is SIGNED (-0.25, -0.15) while
    ``nearest_by_delta`` works on ``abs``. A caller must not be able to get that
    wrong, so the band is normalised here."""
    signed = ss.build_directional(_chain(), "SPY", 450.0, 0.18, 5, 30,
                                  put_band=(-0.25, -0.15))
    unsigned = ss.build_directional(_chain(), "SPY", 450.0, 0.18, 5, 30,
                                    put_band=(0.15, 0.25))
    pick = lambda out: next(s for s in out if s["type"] == "SHORT_PUT")["legs"][0]["strike"]
    assert pick(signed) == pick(unsigned) == 440.0


def test_the_band_order_does_not_matter():
    out = ss.build_directional(_chain(), "SPY", 450.0, 0.18, 5, 30,
                               put_band=(0.25, 0.15))
    sp = next(s for s in out if s["type"] == "SHORT_PUT")
    assert abs(sp["legs"][0]["delta"]) == 0.18


def test_a_call_band_moves_the_short_call_and_not_the_put():
    sigs = ss.build_directional(_chain(), "SPY", 450.0, 0.18, 5, 30,
                                call_band=(0.15, 0.25))
    sc = next(s for s in sigs if s["type"] == "SHORT_CALL")
    assert abs(sc["legs"][0]["delta"]) == 0.18
    sp = next(s for s in sigs if s["type"] == "SHORT_PUT")
    assert abs(sp["legs"][0]["delta"]) == 0.32      # no put band -> legacy target


def test_the_LONG_legs_ignore_the_band_entirely():
    """The band says where you are willing to SELL premium and nothing about
    where you buy it. A 0.20-delta long call is a lottery ticket, not the 0.55
    directional bet the builder intends."""
    sigs = ss.build_directional(_chain(), "SPY", 450.0, 0.18, 5, 30,
                                put_band=(0.15, 0.25), call_band=(0.15, 0.25))
    lc = next(s for s in sigs if s["type"] == "LONG_CALL")
    lp = next(s for s in sigs if s["type"] == "LONG_PUT")
    assert abs(lc["legs"][0]["delta"]) == 0.50
    assert abs(lp["legs"][0]["delta"]) == 0.50


def _rich_only_chain():
    """A ladder whose every put is richer than a 0.25 ceiling - the coarse-ladder
    case the XOM measurement shows is real."""
    return {
        "underlyingPrice": 450.0,
        "callExpDateMap": {"2026-07-10:10": {
            "455.0": [_contract(455.0, 0.45, 9.0)],
            "450.0": [_contract(450.0, 0.60, 12.0)]}},
        "putExpDateMap": {"2026-07-10:10": {
            "445.0": [_contract(445.0, -0.45, 9.0)],
            "450.0": [_contract(450.0, -0.60, 12.0)]}},
    }


def test_a_short_richer_than_the_bands_ceiling_is_DROPPED():
    """The harm A4 names is "richer premium and more assignment than the window
    documents", so the ceiling is enforced rather than merely aimed at."""
    sigs = ss.build_directional(_rich_only_chain(), "SPY", 450.0, 0.18, 5, 30,
                                put_band=(0.15, 0.25), call_band=(0.15, 0.25))
    types = {s["type"] for s in sigs}
    assert "SHORT_PUT" not in types
    assert "SHORT_CALL" not in types
    # ...and the LONG side still builds, so this is a per-structure drop and not
    # an empty return.
    assert {"LONG_CALL", "LONG_PUT"} <= types


def test_a_short_CHEAPER_than_the_band_is_kept():
    """Deliberately asymmetric. Escaping the band DOWNWARD is a thin credit, and
    the delta-aware edge floor (credit/width >= |delta| + 0.02) plus the credit
    floor already refuse those - a second gate here could only empty the board
    for a reason something else already covers. Escaping UPWARD is the risk
    nothing else catches, which is why only the ceiling is enforced."""
    thin = {
        "underlyingPrice": 450.0,
        "callExpDateMap": {"2026-07-10:10": {"470.0": [_contract(470.0, 0.04, 0.2)]}},
        "putExpDateMap": {"2026-07-10:10": {"430.0": [_contract(430.0, -0.04, 0.2)]}},
    }
    sigs = ss.build_directional(thin, "SPY", 450.0, 0.18, 5, 30,
                                put_band=(0.15, 0.25), call_band=(0.15, 0.25))
    sp = next(s for s in sigs if s["type"] == "SHORT_PUT")
    assert abs(sp["legs"][0]["delta"]) == 0.04


# ---- Strategy Finder: every structure (2026-09-13) ----
import datetime as _dt


def _exp(days):
    return (_dt.date.today() + _dt.timedelta(days=days)).isoformat()


def _stock(spot, qty=1):
    return {"kind": "stock", "side": "long", "strike": None, "expiration": None,
            "qty": qty, "mark": spot, "delta": 1.0, "theta": 0.0, "vega": 0.0,
            "gamma": 0.0, "iv": 0.0}


def test_option_contracts_counts_qty_and_ignores_shares():
    fly = [_leg("call", "long", 445.0, 8.0), _leg("call", "short", 450.0, 6.0, qty=2),
           _leg("call", "long", 455.0, 3.5)]
    assert ss._option_contracts(fly) == 4
    assert ss._option_contracts([_stock(450.0), _leg("call", "short", 455.0, 3.5)]) == 1


def test_butterfly_commission_charges_four_contracts():
    fly = [_leg("call", "long", 445.0, 8.0), _leg("call", "short", 450.0, 6.0, qty=2),
           _leg("call", "long", 455.0, 3.5)]
    assert ss.payoff_metrics(fly, spot=450.0)["commission"] == round(4 * 0.65 * 2, 4)


def test_existing_qty_one_commission_is_unchanged():
    legs = [_leg("call", "long", 450.0, 6.0), _leg("call", "short", 455.0, 3.5)]
    assert ss.payoff_metrics(legs, spot=450.0)["commission"] == 2.60


def test_single_expiry_options_never_take_the_front_valuation_path(monkeypatch):
    """The existing nine structures must be byte-identical: prove the new path is
    not even entered for an all-options single-expiry set."""
    def _boom(*a, **k):
        raise AssertionError("front-expiry valuation used on a single-expiry set")
    monkeypatch.setattr(ss, "_front_value", _boom)
    legs = [_leg("put", "short", 445.0, 3.5), _leg("put", "long", 440.0, 1.8)]
    ss.payoff_metrics(legs, spot=450.0)
    ss.pop_from_payoff(legs, 450.0, 0.18, 10)


def _cal_legs(front_days=14, back_days=42, K=100.0):
    import options_calculator as oc
    f, b = front_days / 365, back_days / 365
    short = _leg("call", "short", K, oc.bs_price(100.0, K, f, oc.RISK_FREE_RATE, 0.28, "call"),
                 iv=28.0)
    long_ = _leg("call", "long", K, oc.bs_price(100.0, K, b, oc.RISK_FREE_RATE, 0.26, "call"),
                 iv=26.0)
    short["expiration"], long_["expiration"] = _exp(front_days), _exp(back_days)
    return short, long_


def test_calendar_max_loss_is_its_debit_plus_commission():
    short, long_ = _cal_legs()
    m = ss.payoff_metrics([short, long_], spot=100.0)
    debit = (long_["mark"] - short["mark"]) * 100
    assert m["net_debit"] == round(debit, 2)
    assert m["unbounded"] is False
    assert abs(m["max_loss"] - (debit + 2.60)) < 1.0
    assert m["max_profit"] > 0
    assert len(m["breakevens"]) == 2


def test_calendar_back_leg_is_not_valued_at_intrinsic():
    """At the strike the front call is worthless and the back call still has time
    value - intrinsic-only math would call the peak a loss of the whole debit."""
    short, long_ = _cal_legs()
    m = ss.payoff_metrics([short, long_], spot=100.0)
    assert m["max_profit"] > 100.0


def test_covered_call_max_loss_reaches_a_stock_price_of_zero():
    call = _leg("call", "short", 105.0, 1.0)
    call["expiration"] = _exp(30)
    m = ss.payoff_metrics([_stock(100.0), call], spot=100.0)
    # entry = 100 - 1 = 99/share; worst case the stock goes to zero.
    assert abs(m["max_loss"] - (99.0 * 100 + 1.30)) < 0.01
    assert abs(m["max_profit"] - (6.0 * 100 - 1.30)) < 0.01
    assert m["unbounded_profit"] is False and m["unbounded_loss"] is False
    assert m["commission"] == 1.30          # the share leg is not billed


def test_protective_put_is_unbounded_upside_not_capped_at_the_grid():
    put = _leg("put", "long", 95.0, 1.2)
    put["expiration"] = _exp(30)
    m = ss.payoff_metrics([_stock(100.0), put], spot=100.0)
    assert m["unbounded_profit"] is True and m["max_profit"] is None
    assert abs(m["max_loss"] - ((100.0 + 1.2 - 95.0) * 100 + 1.30)) < 0.01


def test_assemble_takes_dte_from_the_front_OPTION_leg_not_a_share_leg():
    call = _leg("call", "short", 105.0, 1.0)
    call["expiration"] = _exp(30)
    s = ss._assemble("COVERED_CALL", "DIRECTIONAL", "Covered Call", "bullish",
                     [_stock(100.0), call], "XYZ", 100.0, 0.28)
    assert s["expiration"] == _exp(30) and s["dte"] == 30
    assert s["pop_pct"] is not None


import math
import pytest


def _put_cal_legs(front_days=14, back_days=42, K=100.0, iv=20.0):
    import options_calculator as oc
    f, b = front_days / 365, back_days / 365
    short = _leg("put", "short", K,
                 oc.bs_price(100.0, K, f, oc.RISK_FREE_RATE, iv / 100, "put"), iv=iv)
    long_ = _leg("put", "long", K,
                 oc.bs_price(100.0, K, b, oc.RISK_FREE_RATE, iv / 100, "put"), iv=iv)
    short["expiration"], long_["expiration"] = _exp(front_days), _exp(back_days)
    return short, long_


def test_put_calendar_max_loss_is_its_debit_plus_commission():
    """A long put is American: deep ITM it is worth at least its intrinsic. European
    BS undershoots that, which would book a put calendar a loss beyond its debit."""
    short, long_ = _put_cal_legs()
    m = ss.payoff_metrics([short, long_], spot=100.0)
    debit = (long_["mark"] - short["mark"]) * 100
    assert m["net_debit"] == round(debit, 2)
    assert m["unbounded"] is False
    assert abs(m["max_loss"] - (debit + 2.60)) < 1.0


def test_call_diagonal_max_loss_is_bounded_by_the_width():
    short, long_ = _cal_legs()
    import options_calculator as oc
    long_["strike"] = 105.0
    long_["mark"] = oc.bs_price(100.0, 105.0, 42 / 365, oc.RISK_FREE_RATE, 0.26, "call")
    m = ss.payoff_metrics([short, long_], spot=100.0)
    entry = (long_["mark"] - short["mark"]) * 100       # signed: + debit / - credit
    assert m["max_loss"] is not None and math.isfinite(m["max_loss"])
    assert m["max_loss"] > 0
    assert m["max_loss"] <= entry + 5.0 * 100 + m["commission"] + 1e-6


@pytest.mark.parametrize("bad_iv", [-999.0, float("nan"), 0])
def test_calendar_with_an_unusable_back_leg_iv_raises(bad_iv):
    """Schwab's -999 sentinel, a NaN and a missing IV must not become a confident
    payoff: -999 would clamp to a 1% vol, NaN makes max() order-dependent, and 0
    used to invent 20%."""
    short, long_ = _cal_legs()
    long_["iv"] = bad_iv
    with pytest.raises(ValueError, match="unpriceable later leg"):
        ss.payoff_metrics([short, long_], spot=100.0)


def test_atm_strike_is_nearest_to_spot():
    assert ss._atm_strike({95.0: {}, 100.0: {}, 105.0: {}}, 101.0) == 100.0
    assert ss._atm_strike({}, 101.0) is None


def test_symmetric_wing_picks_the_common_distance_nearest_the_target():
    strikes = {90.0, 95.0, 100.0, 105.0, 110.0, 112.5}
    assert ss._symmetric_wing(strikes, 100.0, 4.0) == 5.0
    assert ss._symmetric_wing(strikes, 100.0, 9.0) == 10.0
    # 12.5 exists above but 87.5 does not below -> never asymmetric
    assert ss._symmetric_wing(strikes, 100.0, 12.4) == 10.0
    assert ss._symmetric_wing({100.0}, 100.0, 5.0) is None


def test_half_expected_move():
    import math
    assert abs(ss._half_em(100.0, 0.28, 30) - 100 * 0.28 * math.sqrt(30 / 365) / 2) < 1e-9
    assert ss._half_em(100.0, 0.28, 0) == ss._half_em(100.0, 0.28, 1)


def _ladder_chain(spot=100.0, days=(30,), step=5.0, n=6, iv=28.0):
    """A symmetric chain: strikes spot +/- n*step on every expiry in ``days``,
    Black-Scholes marks and deltas so strike selection behaves like a real chain."""
    import options_calculator as oc
    chain = {"underlyingPrice": spot, "callExpDateMap": {}, "putExpDateMap": {}}
    for d in days:
        key = f"{_exp(d)}:{d}"
        chain["callExpDateMap"][key], chain["putExpDateMap"][key] = {}, {}
        for i in range(-n, n + 1):
            K, T = spot + i * step, d / 365
            for kind, m in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
                mark = oc.bs_price(spot, K, T, oc.RISK_FREE_RATE, iv / 100, kind)
                delta = oc.bs_delta(spot, K, T, oc.RISK_FREE_RATE, iv / 100, kind)
                chain[m][key][f"{K:.1f}"] = [_contract(K, delta, round(max(mark, 0.01), 2),
                                                      volatility=iv)]
    return chain


def _by_type(sigs):
    return {s["type"]: s for s in sigs}


def test_straddles_sit_at_the_money_on_the_front_expiry():
    out = _by_type(ss.build_straddles_strangles(_ladder_chain(days=(30, 60)), "XYZ",
                                                100.0, 0.28, 5, 90))
    for t, side, family in (("LONG_STRADDLE", "long", "VOLATILITY"),
                            ("SHORT_STRADDLE", "short", "NEUTRAL")):
        legs = out[t]["legs"]
        assert {(l["kind"], l["side"], l["strike"]) for l in legs} == {
            ("call", side, 100.0), ("put", side, 100.0)}
        assert out[t]["expiration"] == _exp(30) and out[t]["family"] == family


def test_short_strangle_aims_at_the_band_midpoint_and_long_buys_thirty_delta_wings():
    out = _by_type(ss.build_straddles_strangles(
        _ladder_chain(), "XYZ", 100.0, 0.28, 5, 90,
        put_band=(-0.20, -0.10), call_band=(0.10, 0.20)))
    short = {l["kind"]: l for l in out["SHORT_STRANGLE"]["legs"]}
    long_ = {l["kind"]: l for l in out["LONG_STRANGLE"]["legs"]}
    assert short["call"]["strike"] > 100.0 and short["put"]["strike"] < 100.0
    assert abs(abs(short["call"]["delta"]) - 0.15) < 0.08
    assert all(abs(abs(l["delta"]) - 0.30) <= 0.10 for l in long_.values())


def test_a_far_ladder_keeps_both_short_structures():
    """On a step-20 ladder the only OTM strikes sit far out (|delta| ~0.015 call,
    ~0.002 put): under the band's ceiling and below its floor, so the ceiling never
    binds here - the short straddle is built and any short strangle's legs stay
    under the ceiling. The test below exercises the ceiling actually dropping a
    strangle."""
    rich = _ladder_chain(step=20.0, n=2)   # nothing between ATM (0.5) and far OTM
    out = _by_type(ss.build_straddles_strangles(
        rich, "XYZ", 100.0, 0.28, 5, 90, put_band=(-0.20, -0.30), call_band=(0.20, 0.30)))
    assert "SHORT_STRADDLE" in out
    for l in out.get("SHORT_STRANGLE", {"legs": []})["legs"]:
        assert abs(l["delta"]) <= 0.30


def test_the_band_ceiling_binds_when_the_only_otm_strike_is_richer_than_it():
    """The step-20 ladder above never exercises the ceiling: its far strikes sit
    at |delta| ~0.015 call / ~0.002 put, under the ceiling and below the floor.
    Here the only OTM strikes are 105 (call
    ~0.30) and 95 (put ~0.23), both richer than a 0.10 ceiling, so the short
    strangle must be dropped - while the straddle, which the band never binds,
    and the long strangle survive."""
    out = _by_type(ss.build_straddles_strangles(
        _ladder_chain(step=5.0, n=1), "XYZ", 100.0, 0.28, 5, 90,
        put_band=(-0.05, -0.10), call_band=(0.05, 0.10)))
    assert "SHORT_STRANGLE" not in out
    assert "SHORT_STRADDLE" in out
    assert "LONG_STRANGLE" in out


def test_call_butterfly_is_symmetric_with_a_two_lot_body():
    out = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))
    legs = sorted(out["BUTTERFLY_CALL"]["legs"], key=lambda l: l["strike"])
    assert [(l["side"], l["qty"]) for l in legs] == [("long", 1), ("short", 2), ("long", 1)]
    assert legs[1]["strike"] == 100.0
    assert legs[1]["strike"] - legs[0]["strike"] == legs[2]["strike"] - legs[1]["strike"]
    assert out["BUTTERFLY_CALL"]["family"] == "NEUTRAL"
    assert out["BUTTERFLY_CALL"]["net_debit"] is not None


def test_iron_butterfly_shorts_both_sides_at_the_money():
    out = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))
    legs = {(l["kind"], l["side"]): l["strike"] for l in out["IRON_BUTTERFLY"]["legs"]}
    assert legs[("call", "short")] == legs[("put", "short")] == 100.0
    assert legs[("call", "long")] - 100.0 == 100.0 - legs[("put", "long")]
    assert out["IRON_BUTTERFLY"]["net_credit"] is not None


def test_condor_has_four_symmetric_strikes_long_outside():
    out = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))
    for t in ("CONDOR_CALL", "CONDOR_PUT"):
        legs = sorted(out[t]["legs"], key=lambda l: l["strike"])
        assert [l["side"] for l in legs] == ["long", "short", "short", "long"]
        ks = [l["strike"] for l in legs]
        assert ks[1] - ks[0] == ks[3] - ks[2] and ks[1] < 100.0 < ks[2]


def test_no_wing_on_a_ladder_with_one_strike():
    one = _ladder_chain(n=0)
    assert ss.build_butterflies_condors(one, "XYZ", 100.0, 0.28, 5, 90) == []


def test_call_butterfly_max_profit_and_loss_match_its_wing_and_debit():
    """A long call fly risks its debit and earns the wing less the debit - both
    net of four contracts' round-trip commission (the body is a two-lot)."""
    import commissions as _cm
    fly = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))[
        "BUTTERFLY_CALL"]
    legs = sorted(fly["legs"], key=lambda l: l["strike"])
    wing = legs[1]["strike"] - legs[0]["strike"]
    comm = _cm.round_trip_commission(4, None, 1)
    assert abs(fly["max_loss"] - (fly["net_debit"] + comm)) < 1.0
    assert abs(fly["max_profit"] - (wing * 100 - fly["net_debit"] - comm)) < 1.0


def test_iron_butterfly_max_profit_and_loss_match_its_wing_and_credit():
    import commissions as _cm
    fly = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))[
        "IRON_BUTTERFLY"]
    k = {(l["kind"], l["side"]): l["strike"] for l in fly["legs"]}
    wing = k[("call", "long")] - k[("call", "short")]
    comm = _cm.round_trip_commission(4, None, 1)
    assert abs(fly["max_profit"] - (fly["net_credit"] - comm)) < 1.0
    assert abs(fly["max_loss"] - (wing * 100 - fly["net_credit"] + comm)) < 1.0


def test_a_fractional_strike_ladder_still_builds_a_butterfly():
    out = _by_type(ss.build_butterflies_condors(
        _ladder_chain(spot=437.5, step=2.5), "XYZ", 437.5, 0.28, 5, 90))
    legs = sorted(out["BUTTERFLY_CALL"]["legs"], key=lambda l: l["strike"])
    assert legs[1]["strike"] == 437.5
    assert legs[1]["strike"] - legs[0]["strike"] == legs[2]["strike"] - legs[1]["strike"]


def test_calendar_sells_the_front_and_buys_the_expiry_nearest_plus_28():
    chain = _ladder_chain(days=(7, 21, 35, 49))
    out = _by_type(ss.build_calendars(chain, "XYZ", 100.0, 0.28, 5, 60))
    legs = {l["side"]: l for l in out["CALENDAR_CALL"]["legs"]}
    assert legs["short"]["expiration"] == _exp(7) and legs["long"]["expiration"] == _exp(35)
    assert legs["short"]["strike"] == legs["long"]["strike"] == 100.0
    assert out["CALENDAR_CALL"]["expiration"] == _exp(7)
    assert out["CALENDAR_PUT"]["family"] == "NEUTRAL"


def test_diagonals_buy_in_the_money_and_never_cost_their_width():
    """The standard diagonal sells the out-of-the-money front and buys the back
    month in the money, both judged against spot: a DEBIT below the strike width.

    On the $5 ladder at spot 100 no diagonal is built: the nearest out-of-the-money
    front strikes are 105C (|delta| ~0.11) and 95P (~0.09), both below the short
    leg's 0.15-0.45 band - a token short, not the ~0.30 one a diagonal sells. On
    the $1 ladder both sides build, and neither costs its width."""
    coarse = _by_type(ss.build_calendars(_ladder_chain(days=(7, 35)), "XYZ", 100.0, 0.28, 5, 60))
    assert "DIAGONAL_CALL" not in coarse and "DIAGONAL_PUT" not in coarse
    fine = _by_type(ss.build_calendars(_ladder_chain(days=(7, 35), step=1.0, n=15),
                                       "XYZ", 100.0, 0.28, 5, 60))
    for t in ("DIAGONAL_CALL", "DIAGONAL_PUT"):
        assert fine[t]["net_debit"] is not None
        assert fine[t]["net_debit"] < _diag_width(fine[t]) * 100
        assert fine[t]["max_profit"] > 0

def test_no_calendar_without_two_expiries_seven_days_apart():
    assert ss.build_calendars(_ladder_chain(days=(7, 10)), "XYZ", 100.0, 0.28, 5, 60) == []
    assert ss.build_calendars(_ladder_chain(days=(30,)), "XYZ", 100.0, 0.28, 5, 60) == []


def test_a_back_leg_with_the_schwab_sentinel_iv_is_never_chosen():
    """-999 is Schwab's 'no IV' sentinel; _front_value raises on it. The builder
    must never pick such a leg - no crash, and no calendar or diagonal whose back
    leg carries it."""
    chain = _ladder_chain(days=(7, 35))
    back = [k for k in chain["callExpDateMap"] if k.endswith(":35")][0]
    chain["callExpDateMap"][back]["100.0"][0]["volatility"] = -999.0
    out = ss.build_calendars(chain, "XYZ", 100.0, 0.28, 5, 60)      # must not raise
    for s in out:
        for leg in s["legs"]:
            assert leg["iv"] != -999.0, (s["type"], leg)
    cal = _by_type(out).get("CALENDAR_CALL")
    assert cal is None or {l["strike"] for l in cal["legs"]} != {100.0}


def test_atm_call_calendar_risks_about_its_debit_and_profits_between_two_breakevens():
    """A long calendar's worst case at the front expiry is roughly the debit paid
    (both legs near worthless far below, near parity far above), net of two
    contracts' round-trip commission; it profits in a band around the strike."""
    import commissions as _cm
    cal = _by_type(ss.build_calendars(_ladder_chain(days=(7, 35)), "XYZ", 100.0, 0.28, 5, 60))[
        "CALENDAR_CALL"]
    comm = _cm.round_trip_commission(2, None, 1)
    assert cal["net_debit"] is not None
    assert abs(cal["max_loss"] - (cal["net_debit"] + comm)) < 1.50
    assert cal["max_profit"] > 0
    assert len(cal["breakevens"]) == 2
    assert cal["breakevens"][0] < 100.0 < cal["breakevens"][1]


# ---- Review follow-up: chain holes, ties and NaN ----
def _drop(chain, map_key, days, strike):
    exp = [k for k in chain[map_key] if k.endswith(f":{days}")][0]
    del chain[map_key][exp][strike]
    return chain


def test_a_hole_at_the_money_builds_no_straddle_fly_or_condor_but_keeps_strangles():
    """extract_options drops a strike with no delta, so the true ATM can be missing
    from ONE map. Recentring on the next common strike built a 105 'straddle' with
    a 0.30 call against a -0.70 put and a 95/105/115 'neutral' butterfly. The ATM
    is taken over the UNION of both maps; missing from either, those skip."""
    chain = _drop(_ladder_chain(), "putExpDateMap", 30, "100.0")
    straddles = _by_type(ss.build_straddles_strangles(chain, "XYZ", 100.0, 0.28, 5, 90))
    assert "LONG_STRADDLE" not in straddles and "SHORT_STRADDLE" not in straddles
    assert "LONG_STRANGLE" in straddles and "SHORT_STRANGLE" in straddles
    assert ss.build_butterflies_condors(chain, "XYZ", 100.0, 0.28, 5, 90) == []


def test_a_back_leg_hole_at_the_money_skips_that_kinds_calendar():
    """The sentinel chain: the back 100C has IV -999. A calendar recentred on 95 or
    105 is not an ATM calendar, so the call calendar is not built - and the put
    side, whose ATM is intact, is unaffected. A diagonal does not use the ATM
    strike, so it is still built, never on the hole."""
    chain = _ladder_chain(days=(7, 35))
    back = [k for k in chain["callExpDateMap"] if k.endswith(":35")][0]
    chain["callExpDateMap"][back]["100.0"][0]["volatility"] = -999.0
    out = _by_type(ss.build_calendars(chain, "XYZ", 100.0, 0.28, 5, 60))
    assert "CALENDAR_CALL" not in out
    # The 0.15-0.45 short band - not the hole - excludes the diagonal here; see
    # test_a_hole_on_the_diagonals_own_long_strike_is_never_picked.
    assert "DIAGONAL_CALL" not in out
    assert {l["strike"] for l in out["CALENDAR_PUT"]["legs"]} == {100.0}
    assert "DIAGONAL_PUT" not in out


def test_a_back_leg_missing_at_the_money_skips_the_calendar_too():
    chain = _drop(_ladder_chain(days=(7, 35)), "callExpDateMap", 35, "100.0")
    out = _by_type(ss.build_calendars(chain, "XYZ", 100.0, 0.28, 5, 60))
    assert "CALENDAR_CALL" not in out
    # The 0.15-0.45 short band - not the hole - excludes the diagonal here; see
    # test_a_hole_on_the_diagonals_own_long_strike_is_never_picked.
    assert "DIAGONAL_CALL" not in out
    assert "CALENDAR_PUT" in out


def test_atm_strike_breaks_an_exact_tie_toward_the_lower_strike():
    assert ss._atm_strike({100.0, 105.0}, 102.5) == 100.0
    assert ss._atm_strike({1000.0, 1005.0}, 1002.5) == 1000.0
    assert ss._atm_strike({105.0, 100.0}, 102.5) == 100.0


def test_half_expected_move_treats_a_nan_iv_as_zero():
    assert ss._half_em(100.0, float("nan"), 30) == 0.0
    assert ss._half_em(100.0, float("inf"), 30) == 0.0


def test_listed_tolerance_is_half_the_wing_rounding_step():
    """_symmetric_wing rounds a distance to 4 dp, so k +/- d can sit up to 5e-5 off
    a listed key on a 5-decimal ladder; _listed must still find it."""
    assert ss._listed({100.0, 105.0}, 100.00004) == 100.0
    assert ss._listed({100.0, 105.0}, 99.99996) == 100.0
    assert ss._listed({100.0, 105.0}, 100.0001) is None


def test_a_tenth_step_ladder_builds_a_call_butterfly():
    out = _by_type(ss.build_butterflies_condors(
        _ladder_chain(spot=10.3, step=0.1, n=10, iv=40.0), "XYZ", 10.3, 0.40, 5, 90))
    legs = sorted(out["BUTTERFLY_CALL"]["legs"], key=lambda l: l["strike"])
    assert abs(legs[1]["strike"] - 10.3) < 1e-9
    assert abs((legs[1]["strike"] - legs[0]["strike"])
               - (legs[2]["strike"] - legs[1]["strike"])) < 1e-9


def test_spot_on_a_strike_is_the_straddle_and_is_excluded_from_the_strangle():
    out = _by_type(ss.build_straddles_strangles(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))
    assert {l["strike"] for l in out["SHORT_STRADDLE"]["legs"]} == {100.0}
    for t in ("LONG_STRANGLE", "SHORT_STRANGLE"):
        k = {l["kind"]: l["strike"] for l in out[t]["legs"]}
        assert k["call"] > 100.0 and k["put"] < 100.0


def test_no_shared_expiry_builds_no_neutral_structure():
    chain = _ladder_chain(days=(30, 60))
    for map_key, days in (("callExpDateMap", 60), ("putExpDateMap", 30)):
        del chain[map_key][[k for k in chain[map_key] if k.endswith(f":{days}")][0]]
    assert ss.build_straddles_strangles(chain, "XYZ", 100.0, 0.28, 5, 90) == []
    assert ss.build_butterflies_condors(chain, "XYZ", 100.0, 0.28, 5, 90) == []


def test_a_condor_is_omitted_when_two_wings_out_are_unlisted():
    out = _by_type(ss.build_butterflies_condors(_ladder_chain(n=1), "XYZ", 100.0, 0.28, 5, 90))
    assert {"BUTTERFLY_CALL", "BUTTERFLY_PUT", "IRON_BUTTERFLY"} <= set(out)
    assert not any(t.startswith("CONDOR_") for t in out)


def test_put_butterfly_is_all_puts_symmetric_with_a_two_lot_body():
    out = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))
    legs = sorted(out["BUTTERFLY_PUT"]["legs"], key=lambda l: l["strike"])
    assert [(l["side"], l["qty"]) for l in legs] == [("long", 1), ("short", 2), ("long", 1)]
    assert all(l["kind"] == "put" for l in legs)
    assert legs[1]["strike"] == 100.0
    assert legs[1]["strike"] - legs[0]["strike"] == legs[2]["strike"] - legs[1]["strike"]


def test_condor_shorts_are_symmetric_about_the_body_and_kinds_match_the_type():
    out = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))
    for t, kind in (("CONDOR_CALL", "call"), ("CONDOR_PUT", "put")):
        legs = sorted(out[t]["legs"], key=lambda l: l["strike"])
        ks = [l["strike"] for l in legs]
        assert ks[2] - 100.0 == 100.0 - ks[1]
        assert all(l["kind"] == kind for l in legs)


def test_call_butterfly_is_near_delta_neutral():
    out = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))
    assert abs(out["BUTTERFLY_CALL"]["net_delta"]) < 0.05


# ---- Review follow-up: far tail, front floor, no unprofitable structure ----
def _put_diagonal_legs(iv_pct):
    import options_calculator as oc
    iv = iv_pct / 100
    short = _leg("put", "short", 100.0,
                 oc.bs_price(100.0, 100.0, 7 / 365, oc.RISK_FREE_RATE, iv, "put"), iv=iv_pct)
    long_ = _leg("put", "long", 105.0,
                 oc.bs_price(100.0, 105.0, 35 / 365, oc.RISK_FREE_RATE, iv, "put"), iv=iv_pct)
    short["expiration"], long_["expiration"] = _exp(7), _exp(35)
    return [short, long_]


@pytest.mark.parametrize("iv_pct", [150.0, 100.0])
def test_put_diagonal_max_loss_sees_the_plateau_as_the_stock_runs_away(iv_pct):
    """A put diagonal's worst case is S -> infinity: the front put is worthless and
    the long back put's time value decays to nothing, so it loses the whole debit.
    At a high IV the back put still carries value at 2x the top strike, so sampling
    stopped there understated the loss by ~$117 at IV 150."""
    legs = _put_diagonal_legs(iv_pct)
    m = ss.payoff_metrics(legs, spot=100.0)
    entry = legs[1]["mark"] - legs[0]["mark"]
    far = -ss._pl_at(legs, entry, 100 * 105.0, ss._front_expiration(legs)) * 100
    assert abs(far - m["net_debit"]) < 0.01          # the plateau IS the debit
    assert abs(m["max_loss"] - (far + m["commission"])) < 2.0


def test_calendar_front_leg_is_at_least_seven_days_out():
    """The default DTE window starts at 0, so the front was a 0-2 DTE expiry whose
    calendar can barely profit (measured R:R -0.004 at 0/28) and was always cut."""
    out = _by_type(ss.build_calendars(_ladder_chain(days=(1, 8, 36)), "XYZ", 100.0, 0.28, 0, 60))
    legs = {l["side"]: l for l in out["CALENDAR_CALL"]["legs"]}
    assert legs["short"]["expiration"] == _exp(8)
    assert legs["long"]["expiration"] == _exp(36)


def test_no_calendar_when_only_the_sub_seven_day_expiry_could_be_the_front():
    assert ss.build_calendars(_ladder_chain(days=(1, 14)), "XYZ", 100.0, 0.28, 0, 60) == []


def test_a_calendar_that_cannot_profit_is_never_emitted(monkeypatch):
    real = ss._assemble

    def _no_profit(*a, **k):
        s = real(*a, **k)
        s["max_profit"] = -1.29
        return s
    monkeypatch.setattr(ss, "_assemble", _no_profit)
    assert ss.build_calendars(_ladder_chain(days=(7, 35)), "XYZ", 100.0, 0.28, 5, 60) == []


def test_a_calendar_with_no_max_profit_figure_is_never_emitted(monkeypatch):
    real = ss._assemble

    def _none(*a, **k):
        s = real(*a, **k)
        s["max_profit"] = None
        return s
    monkeypatch.setattr(ss, "_assemble", _none)
    assert ss.build_calendars(_ladder_chain(days=(7, 35)), "XYZ", 100.0, 0.28, 5, 60) == []


def _diag_width(sig):
    ks = [l["strike"] for l in sig["legs"]]
    return abs(ks[0] - ks[1])


def test_a_diagonal_sells_thirty_delta_front_and_buys_seventy_delta_back():
    """Practitioner geometry (the poor man's covered call and its put mirror):
    short ~0.30 delta out of the money on the front, long ~0.70 delta in the money
    on the back, net debit under the width. An at-the-money short can never satisfy
    that rule for calls on a flat term structure."""
    out = _by_type(ss.build_calendars(_ladder_chain(days=(7, 35), step=1.0, n=15),
                                      "XYZ", 100.0, 0.28, 5, 60))
    for t, call in (("DIAGONAL_CALL", True), ("DIAGONAL_PUT", False)):
        legs = {l["side"]: l for l in out[t]["legs"]}
        short, long_ = legs["short"], legs["long"]
        assert short["expiration"] == _exp(7) and long_["expiration"] == _exp(35)
        assert abs(abs(short["delta"]) - 0.30) <= 0.10, (t, short["delta"])
        assert abs(abs(long_["delta"]) - 0.70) <= 0.10, (t, long_["delta"])
        if call:
            assert short["strike"] > 100.0 and long_["strike"] < 100.0
        else:
            assert short["strike"] < 100.0 and long_["strike"] > 100.0
        assert out[t]["net_debit"] < _diag_width(out[t]) * 100
        assert out[t]["max_profit"] > 0

@pytest.mark.parametrize("step,n", [(5.0, 6), (1.0, 15), (2.5, 8)])
def test_every_emitted_diagonal_costs_less_than_its_width(step, n):
    for days in ((7, 35), (14, 42)):
        sigs = ss.build_calendars(_ladder_chain(days=days, step=step, n=n),
                                  "XYZ", 100.0, 0.28, 5, 60)
        for s in sigs:
            if s["type"].startswith("DIAGONAL_"):
                assert s["net_debit"] < _diag_width(s) * 100, s["id"]


# ---- Review follow-up: judge an at-the-money hole on the strikes the chain LISTS ----
def _merge_chains(*chains):
    out = {"underlyingPrice": chains[0]["underlyingPrice"],
           "callExpDateMap": {}, "putExpDateMap": {}}
    for c in chains:
        for m in ("callExpDateMap", "putExpDateMap"):
            out[m].update(c[m])
    return out


def _raw_contract(chain, map_key, days, strike):
    exp = [k for k in chain[map_key] if k.endswith(f":{days}")][0]
    return chain[map_key][exp][strike][0]


def test_mixed_strike_spacing_builds_the_calendar_at_the_nearest_common_strike():
    """Weeklies list $1 strikes where the monthly lists $5. At spot 102 the nearest
    strike is 102, which only the front lists - a structural ladder difference, not a
    hole - so the calendar sits at the nearest strike BOTH expiries list."""
    chain = _merge_chains(_ladder_chain(days=(7,), step=1.0, n=15),
                          _ladder_chain(days=(35,), step=5.0, n=6))
    out = _by_type(ss.build_calendars(chain, "XYZ", 102.0, 0.28, 5, 60))
    assert {l["strike"] for l in out["CALENDAR_CALL"]["legs"]} == {100.0}
    assert {l["strike"] for l in out["CALENDAR_PUT"]["legs"]} == {100.0}


def test_a_delta_hole_on_both_sides_at_the_money_builds_no_straddle_fly_or_condor():
    """A strike with no delta on the call AND the put side vanishes from both
    extracted maps, so a union of those maps cannot see it and the builder
    recentred (a straddle at 95, a 85/95/105 butterfly at spot 100)."""
    chain = _ladder_chain()
    for m in ("callExpDateMap", "putExpDateMap"):
        _raw_contract(chain, m, 30, "100.0")["delta"] = None
    straddles = _by_type(ss.build_straddles_strangles(chain, "XYZ", 100.0, 0.28, 5, 90))
    assert "LONG_STRADDLE" not in straddles and "SHORT_STRADDLE" not in straddles
    assert "LONG_STRANGLE" in straddles and "SHORT_STRANGLE" in straddles
    assert ss.build_butterflies_condors(chain, "XYZ", 100.0, 0.28, 5, 90) == []


def test_a_sentinel_iv_at_the_money_on_both_expiries_builds_no_call_calendar():
    """The hole is judged BEFORE the IV filter: with the 100C unusable on both
    expiries, the nearest strike both list is still 100, so no call calendar is
    recentred onto 95 or 105. The put side is untouched."""
    chain = _ladder_chain(days=(7, 35))
    for d in (7, 35):
        _raw_contract(chain, "callExpDateMap", d, "100.0")["volatility"] = -999.0
    out = _by_type(ss.build_calendars(chain, "XYZ", 100.0, 0.28, 5, 60))
    assert "CALENDAR_CALL" not in out
    assert {l["strike"] for l in out["CALENDAR_PUT"]["legs"]} == {100.0}


def test_an_exact_tie_goes_to_the_strike_both_maps_list():
    """Spot 102.5 sits midway between 100 and 105. The put chain does not list 100,
    so the tie goes to 105 - listed on both sides - rather than to the lower strike
    and then refusing to build."""
    chain = _drop(_ladder_chain(), "putExpDateMap", 30, "100.0")
    straddles = _by_type(ss.build_straddles_strangles(chain, "XYZ", 102.5, 0.28, 5, 90))
    assert {l["strike"] for l in straddles["SHORT_STRADDLE"]["legs"]} == {105.0}
    flies = _by_type(ss.build_butterflies_condors(chain, "XYZ", 102.5, 0.28, 5, 90))
    body = next(l for l in flies["BUTTERFLY_CALL"]["legs"] if l["side"] == "short")
    assert body["strike"] == 105.0


def test_mixed_spacing_with_the_back_grid_strike_missing_builds_no_calendar():
    """Front $1, back $5 with the back 100C absent, spot 101. The back call ladder
    judges itself: its strike nearest spot is 105, whose listed neighbours (95 and
    110) put its own local step at 5, and on that spacing the strike nearest 101 is
    100 - not listed, so the back ladder has a hole at the money and the call
    calendar is skipped rather than recentred four points onto 105. The put side,
    whose back 100 is intact, builds."""
    chain = _merge_chains(_ladder_chain(days=(7,), step=1.0, n=15),
                          _drop(_ladder_chain(days=(35,), step=5.0, n=6),
                                "callExpDateMap", 35, "100.0"))
    out = _by_type(ss.build_calendars(chain, "XYZ", 101.0, 0.28, 5, 60))
    assert "CALENDAR_CALL" not in out
    assert {l["strike"] for l in out["CALENDAR_PUT"]["legs"]} == {100.0}


# ---- Review follow-up: the diagonal's short leg sits inside a delta band ----
def test_a_near_the_money_put_is_never_sold_as_a_diagonal_short():
    """At spot 100.1 the only out-of-the-money put strike on a $5 ladder near 0.30
    delta is the 100P at about -0.47 - an at-the-money short under another name."""
    out = ss.build_calendars(_ladder_chain(days=(7, 35)), "XYZ", 100.1, 0.28, 5, 60)
    for s in out:
        if s["type"].startswith("DIAGONAL_"):
            short = next(l for l in s["legs"] if l["side"] == "short")
            assert abs(short["delta"]) <= 0.45, (s["type"], short["strike"], short["delta"])


def test_a_token_delta_short_is_not_a_diagonal():
    """On the $2.50 ladder at IV 28 the nearest out-of-the-money front strikes are
    102.5C (delta ~0.28) and 97.5P (~0.24), and both diagonals build. At IV 14 the
    same strikes are 102.5C ~0.11 and 97.5P ~0.09: a token short, not the ~0.30 one
    a diagonal sells, so neither kind is built. (The $5 ladder's own case is the
    first assertion of test_diagonals_buy_in_the_money_and_never_cost_their_width.)"""
    rich = _by_type(ss.build_calendars(_ladder_chain(days=(7, 35), step=2.5, n=8),
                                       "XYZ", 100.0, 0.28, 5, 60))
    assert "DIAGONAL_CALL" in rich and "DIAGONAL_PUT" in rich
    chain = _ladder_chain(days=(7, 35), step=2.5, n=8, iv=14.0)
    assert abs(_raw_contract(chain, "callExpDateMap", 7, "102.5")["delta"]) < 0.15
    assert abs(_raw_contract(chain, "putExpDateMap", 7, "97.5")["delta"]) < 0.15
    out = _by_type(ss.build_calendars(chain, "XYZ", 100.0, 0.14, 5, 60))
    assert "DIAGONAL_CALL" not in out and "DIAGONAL_PUT" not in out


@pytest.mark.parametrize("hole", ["sentinel", "deleted"])
def test_a_hole_on_the_diagonals_own_long_strike_is_never_picked(hole):
    """On the $1 ladder the call diagonal buys the back 96C. With that contract
    unusable (Schwab's -999 IV) or absent, it picks the next listed strike nearest
    0.70 delta - 97C, against the same 102C short - never 96."""
    chain = _ladder_chain(days=(7, 35), step=1.0, n=15)
    if hole == "sentinel":
        _raw_contract(chain, "callExpDateMap", 35, "96.0")["volatility"] = -999.0
    else:
        _drop(chain, "callExpDateMap", 35, "96.0")
    out = _by_type(ss.build_calendars(chain, "XYZ", 100.0, 0.28, 5, 60))
    diag = out["DIAGONAL_CALL"]
    legs = {l["side"]: l for l in diag["legs"]}
    assert legs["short"]["strike"] == 102.0
    assert legs["long"]["strike"] == 97.0
    assert legs["long"]["iv"] != -999.0


# ---- Review follow-up: a calendar needs BOTH ladders whole at the money ----
def _keep_strikes(chain, days, keep):
    """Leave only ``keep`` (strike keys) listed on both maps for the ``days`` expiry."""
    for m in ("callExpDateMap", "putExpDateMap"):
        exp = [k for k in chain[m] if k.endswith(f":{days}")][0]
        chain[m][exp] = {k: v for k, v in chain[m][exp].items() if k in keep}
    return chain


def _cal_strikes(out, t):
    return {l["strike"] for l in out[t]["legs"]} if t in out else None


@pytest.mark.parametrize("spot", [100.0, 100.5])
def test_a_front_hole_at_the_money_builds_no_calendar(spot):
    """The mirror of the back-leg hole: with the FRONT 100C missing, recentring
    built a call calendar at 95 (spot 100) or 105 (spot 100.5)."""
    chain = _drop(_ladder_chain(days=(7, 35)), "callExpDateMap", 7, "100.0")
    out = _by_type(ss.build_calendars(chain, "XYZ", spot, 0.28, 5, 60))
    assert "CALENDAR_CALL" not in out
    assert "CALENDAR_PUT" in out


@pytest.mark.parametrize("front_step,back_step,spot,want", [
    (5.0, 1.0, 102.0, 100.0),
    (1.0, 2.5, 101.8, 100.0), (1.0, 2.5, 102.0, 100.0), (1.0, 2.5, 103.0, 105.0),
    (2.5, 1.0, 101.8, 100.0), (2.5, 1.0, 102.0, 100.0), (2.5, 1.0, 103.0, 105.0),
])
def test_non_nesting_ladders_build_at_the_nearest_common_strike(front_step, back_step,
                                                                  spot, want):
    """$1 against $2.5 strikes do not nest, yet 100 and 105 are listed on both and
    sit within a step of spot - a legitimate calendar, which the grid rule refused."""
    n = {1.0: 15, 2.5: 8, 5.0: 6}
    chain = _merge_chains(_ladder_chain(days=(7,), step=front_step, n=n[front_step]),
                          _ladder_chain(days=(35,), step=back_step, n=n[back_step]))
    out = _by_type(ss.build_calendars(chain, "XYZ", spot, 0.28, 5, 60))
    assert _cal_strikes(out, "CALENDAR_CALL") == {want}
    assert _cal_strikes(out, "CALENDAR_PUT") == {want}


def test_a_stray_far_strike_does_not_distort_the_back_ladder():
    """One odd strike far from the money (62.5 on a $5 ladder) made the smallest gap
    anywhere 2.5, so a whole ladder read as a hole at spot 102."""
    back = _ladder_chain(days=(35,), step=5.0, n=8)
    for m in ("callExpDateMap", "putExpDateMap"):
        exp = next(iter(back[m]))
        back[m][exp]["62.5"] = back[m][exp]["60.0"]
    chain = _merge_chains(_ladder_chain(days=(7,), step=1.0, n=15), back)
    out = _by_type(ss.build_calendars(chain, "XYZ", 102.0, 0.28, 5, 60))
    assert _cal_strikes(out, "CALENDAR_CALL") == {100.0}


def test_a_single_strike_back_expiry_builds_no_calendar():
    """One listed strike carries no spacing, so whether the money is covered cannot
    be judged."""
    chain = _keep_strikes(_ladder_chain(days=(7, 35)), 35, {"100.0"})
    out = _by_type(ss.build_calendars(chain, "XYZ", 100.0, 0.28, 5, 60))
    assert "CALENDAR_CALL" not in out and "CALENDAR_PUT" not in out


def test_a_back_expiry_listing_only_far_strikes_builds_no_calendar():
    """A back month listing only 80 and 120 under a $1 front at spot 100 built a
    calendar at 80 - twenty points from the money."""
    chain = _merge_chains(_ladder_chain(days=(7,), step=1.0, n=20),
                          _keep_strikes(_ladder_chain(days=(35,), step=1.0, n=20), 35,
                                        {"80.0", "120.0"}))
    out = _by_type(ss.build_calendars(chain, "XYZ", 100.0, 0.28, 5, 60))
    assert "CALENDAR_CALL" not in out and "CALENDAR_PUT" not in out


@pytest.mark.parametrize("chain_spot,step,spot,want", [
    (8.0, 1.0, 8.0, 8.0), (10.0, 1.0, 9.5, 9.0), (4.0, 0.5, 4.0, 4.0),
])
def test_low_priced_chains_keep_their_calendars(chain_spot, step, spot, want):
    """Sub-$10 stocks list $1 or $0.50 strikes - wider than 10% of spot - and a
    10%-of-spot step limit alone refused every calendar on them."""
    chain = _ladder_chain(spot=chain_spot, days=(7, 35), step=step, n=6, iv=40.0)
    out = _by_type(ss.build_calendars(chain, "XYZ", spot, 0.40, 5, 60))
    assert _cal_strikes(out, "CALENDAR_CALL") == {want}
    assert _cal_strikes(out, "CALENDAR_PUT") == {want}


# ---- Covered call, protective put, collar ----
def _stock_structures(**kw):
    kw.setdefault("put_band", (-0.20, -0.10))
    kw.setdefault("call_band", (0.10, 0.20))
    return _by_type(ss.build_stock_structures(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90,
                                              **kw))


def test_share_structures_hold_one_lot_and_band_midpoint_options():
    out = _by_type(ss.build_stock_structures(
        _ladder_chain(), "XYZ", 100.0, 0.28, 5, 90,
        put_band=(-0.20, -0.10), call_band=(0.10, 0.20)))
    for t in ("COVERED_CALL", "PROTECTIVE_PUT", "COLLAR"):
        stock = [l for l in out[t]["legs"] if l["kind"] == "stock"]
        assert len(stock) == 1 and stock[0]["qty"] == 1 and stock[0]["mark"] == 100.0
        assert out[t]["family"] == "DIRECTIONAL"
    cc = {l["kind"]: l for l in out["COVERED_CALL"]["legs"]}
    assert cc["call"]["side"] == "short" and cc["call"]["strike"] > 100.0
    pp = {l["kind"]: l for l in out["PROTECTIVE_PUT"]["legs"]}
    assert pp["put"]["side"] == "long" and pp["put"]["strike"] < 100.0
    col = {l["kind"]: l for l in out["COLLAR"]["legs"]}
    assert col["call"]["strike"] == cc["call"]["strike"]
    assert col["put"]["strike"] == pp["put"]["strike"]
    assert out["PROTECTIVE_PUT"]["unbounded_profit"] is True


def test_a_far_ladder_call_under_the_band_floor_still_builds_the_covered_call():
    """On a step-20 ladder the only out-of-the-money calls sit far out (120C at
    |delta| ~0.014): under a 0.10 ceiling AND below the 0.05 floor. Only the
    ceiling binds - escaping the band downward is a thin credit, not extra
    assignment risk - so the covered call is built. The test below exercises the
    ceiling actually dropping it. The collar and protective put are not built on
    this ladder, but for a different reason: its only out-of-the-money put (80P,
    ~0.002 delta) is under the 0.10 hedge floor, and the 120C is under the collar's
    0.05 call floor."""
    out = _by_type(ss.build_stock_structures(_ladder_chain(step=20.0, n=2), "XYZ",
                                             100.0, 0.28, 5, 90, call_band=(0.05, 0.10)))
    call = next(l for l in out["COVERED_CALL"]["legs"] if l["kind"] == "call")
    assert call["strike"] == 120.0 and abs(call["delta"]) < 0.05
    assert "COLLAR" not in out and "PROTECTIVE_PUT" not in out


def test_the_call_band_ceiling_binds_when_the_only_otm_call_is_richer_than_it():
    """On ``step=5, n=1`` the only out-of-the-money call is 105 at ~0.30 delta,
    richer than a 0.10 ceiling: no covered call and no collar. The long put is
    not governed by a band's ceiling: the only out-of-the-money put, 95P at ~0.23,
    is richer than the 0.20 put ceiling passed here, and the protective put is
    still built."""
    out = _by_type(ss.build_stock_structures(_ladder_chain(step=5.0, n=1), "XYZ",
                                             100.0, 0.28, 5, 90, call_band=(0.05, 0.10),
                                             put_band=(-0.20, -0.10)))
    assert "COVERED_CALL" not in out and "COLLAR" not in out
    assert "PROTECTIVE_PUT" in out


def test_the_share_leg_kind_is_the_pricers_stock_constant():
    import options_calculator as oc
    out = _stock_structures()
    for t in ("COVERED_CALL", "PROTECTIVE_PUT", "COLLAR"):
        shares = [l for l in out[t]["legs"] if not l.get("strike")]
        assert len(shares) == 1 and shares[0]["kind"] == oc.STOCK_KIND


def test_covered_call_risks_the_stock_less_the_premium_and_caps_at_the_strike():
    import commissions as _cm
    s = _stock_structures()["COVERED_CALL"]
    call = next(l for l in s["legs"] if l["kind"] == "call")
    comm = _cm.round_trip_commission(1, None, 1)
    assert abs(s["max_loss"] - ((100.0 - call["mark"]) * 100 + comm)) < 0.05
    assert abs(s["max_profit"] - ((call["strike"] - 100.0 + call["mark"]) * 100 - comm)) < 0.05
    assert s["unbounded_profit"] is False and s["unbounded_loss"] is False


def test_collar_is_bounded_by_its_put_and_call_strikes():
    import commissions as _cm
    s = _stock_structures()["COLLAR"]
    call = next(l for l in s["legs"] if l["kind"] == "call")
    put = next(l for l in s["legs"] if l["kind"] == "put")
    comm = _cm.round_trip_commission(2, None, 1)
    assert abs(s["max_loss"]
               - ((100.0 + put["mark"] - call["mark"] - put["strike"]) * 100 + comm)) < 0.05
    assert abs(s["max_profit"]
               - ((call["strike"] - 100.0 - put["mark"] + call["mark"]) * 100 - comm)) < 0.05


def test_protective_put_has_unbounded_profit_and_loses_down_to_its_strike():
    import commissions as _cm
    s = _stock_structures()["PROTECTIVE_PUT"]
    put = next(l for l in s["legs"] if l["kind"] == "put")
    comm = _cm.round_trip_commission(1, None, 1)
    assert s["max_profit"] is None and s["unbounded_profit"] is True
    assert abs(s["max_loss"] - ((100.0 + put["mark"] - put["strike"]) * 100 + comm)) < 0.05


# ---- Review follow-up: a share structure's capital is the cash it ties up ----
def test_a_collars_capital_is_the_cash_to_open_not_its_max_loss():
    """A collar risks ~$980 but ties up ~$9,977 of cash in the shares. Scoring
    capital efficiency on the max loss rated collars ~10x better than covered calls
    and protective puts, which tie up the same cash."""
    import commissions as _cm
    s = _stock_structures()["COLLAR"]
    comm = _cm.round_trip_commission(2, None, 1)
    assert abs(s["capital"] - (s["net_debit"] + comm)) < 0.05
    assert s["capital"] >= s["max_loss"]


def test_covered_call_and_protective_put_capital_are_unchanged():
    """Both already reported the cash to open; pinned numerically on the default
    $5 ladder (spot 100, IV 28, 30 DTE, bands 0.10-0.20). The protective put buys
    the 95P since it took its own 0.25-delta hedge (it was 10031.30 on the 90P)."""
    out = _stock_structures()
    assert out["COVERED_CALL"]["capital"] == 9948.30
    assert out["PROTECTIVE_PUT"]["capital"] == 10115.30


# ---- Review follow-up: long straddles and strangles want nearby breakevens ----
def test_long_straddles_and_strangles_are_volatility_shorts_stay_neutral():
    out = _by_type(ss.build_straddles_strangles(
        _ladder_chain(), "XYZ", 100.0, 0.28, 5, 90,
        put_band=(-0.20, -0.10), call_band=(0.10, 0.20)))
    assert out["LONG_STRADDLE"]["family"] == "VOLATILITY"
    assert out["LONG_STRANGLE"]["family"] == "VOLATILITY"
    assert out["SHORT_STRADDLE"]["family"] == "NEUTRAL"
    assert out["SHORT_STRANGLE"]["family"] == "NEUTRAL"


def test_a_long_straddle_scores_higher_when_its_breakevens_are_nearer_spot():
    """A long straddle profits from a BIG move, so the move it needs - the distance
    to its nearest breakeven - should be small. Tagged NEUTRAL it was rewarded for
    breakevens far apart, which ranks the dearer straddle first."""
    import strategy_scoring as sc
    cheap, dear = (_by_type(ss.build_straddles_strangles(
        _ladder_chain(iv=iv), "XYZ", 100.0, iv / 100, 5, 90))["LONG_STRADDLE"]
        for iv in (28.0, 50.0))
    assert max(cheap["breakevens"]) - min(cheap["breakevens"]) < (
        max(dear["breakevens"]) - min(dear["breakevens"]))
    em = 15.0
    assert sc.q_breakeven_vs_em(cheap, em) > sc.q_breakeven_vs_em(dear, em)


# ---- Review follow-up: the calendar step limit floors at $2.50 ----
@pytest.mark.parametrize("chain_spot,spot,want", [(20.0, 20.0, 20.0), (15.0, 14.0, 15.0)])
def test_a_twelve_to_twenty_five_dollar_stock_on_two_fifty_strikes_keeps_its_calendars(
        chain_spot, spot, want):
    """$2.50 is the normal strike spacing from $12.50 to $25, where it exceeds 10% of
    spot. A $1 floor refused every calendar on those chains."""
    chain = _ladder_chain(spot=chain_spot, days=(7, 35), step=2.5, n=5, iv=40.0)
    out = _by_type(ss.build_calendars(chain, "XYZ", spot, 0.40, 5, 60))
    assert _cal_strikes(out, "CALENDAR_CALL") == {want}
    assert _cal_strikes(out, "CALENDAR_PUT") == {want}


# ---- Review follow-up: a spacing change next to the money is skipped ----
def _spacing_change_chain():
    """$1 front; a back ladder listing $2.50 strikes up to 100 and $5 above it."""
    back = _ladder_chain(days=(35,), step=2.5, n=12)
    wide = _ladder_chain(days=(35,), step=5.0, n=6)
    for m in ("callExpDateMap", "putExpDateMap"):
        exp = next(iter(back[m]))
        back[m][exp] = {**{k: v for k, v in back[m][exp].items() if float(k) <= 100.0},
                        **{k: v for k, v in wide[m][exp].items() if float(k) > 100.0}}
    return _merge_chains(_ladder_chain(days=(7,), step=1.0, n=15), back)


def test_a_back_spacing_change_next_to_the_money_builds_no_calendar():
    """A deliberate CONSERVATIVE skip. At spot 101.5 the back ladder's strike nearest
    spot is 100, whose neighbours 97.5 and 105 put its local step at 2.5 - and on
    that spacing 102.5 belongs next to the money and is not listed. From two
    neighbours a spacing change and a missing strike look the same, so the builder
    cannot tell this legitimate ladder from a hole; skipping never builds an
    off-centre calendar. The same chain at spot 100.5, where the 2.5 spacing puts
    100 next to the money, builds at 100."""
    chain = _spacing_change_chain()
    out = _by_type(ss.build_calendars(chain, "XYZ", 101.5, 0.28, 5, 60))
    assert "CALENDAR_CALL" not in out and "CALENDAR_PUT" not in out
    control = _by_type(ss.build_calendars(chain, "XYZ", 100.5, 0.28, 5, 60))
    assert _cal_strikes(control, "CALENDAR_CALL") == {100.0}
    assert _cal_strikes(control, "CALENDAR_PUT") == {100.0}


# ---- Review follow-up: a long strangle buys its own thirty-delta wings ----
def test_a_long_strangle_on_the_default_band_clears_the_long_pop_bar():
    """Buying the strikes the short strangle sells put both wings near 0.15 delta
    at the Finder's default band, and its PoP (17.7 here; 22-25 across the IV x DTE
    cells a review tried) never cleared the LONG profile's 30 bar - the row was
    always cut. Its own ~0.30-delta wings measure 34.5."""
    import strategy_scoring as sc
    s = _by_type(ss.build_straddles_strangles(
        _ladder_chain(), "XYZ", 100.0, 0.28, 5, 90,
        put_band=(-0.20, -0.10), call_band=(0.10, 0.20)))["LONG_STRANGLE"]
    assert s["dte"] == 30
    assert s["pop_pct"] >= sc.GATE_BARS["LONG"]["min"]["pop"] == 30


# ---- Review follow-up: share structures - a 7-day front, a real 0.25-delta hedge ----
def test_share_structures_skip_an_expiry_under_seven_days():
    """The page's DTE min defaults to 0. On a 1-DTE front a protective put hedging
    with a put that expires tomorrow showed a tiny max loss and passed the LONG
    gate; the share structures take the calendar's 7-day front floor instead."""
    out = _by_type(ss.build_stock_structures(
        _ladder_chain(days=(1, 30)), "XYZ", 100.0, 0.28, 0, 90,
        put_band=(-0.20, -0.10), call_band=(0.10, 0.20)))
    for t in ("COVERED_CALL", "PROTECTIVE_PUT", "COLLAR"):
        assert out[t]["expiration"] == _exp(30), t
        assert all(l["expiration"] == _exp(30) for l in out[t]["legs"] if l["kind"] != "stock")


def test_the_protective_put_buys_a_quarter_delta_hedge_not_the_short_band():
    """A band says where you SELL premium. Borrowed for a hedge, the 0.10-0.20 put
    band bought the 90P at ~0.08 delta - a lottery ticket, not protection."""
    out = _stock_structures()
    for t in ("PROTECTIVE_PUT", "COLLAR"):
        put = next(l for l in out[t]["legs"] if l["kind"] == "put")
        assert put["side"] == "long" and put["strike"] < 100.0
        assert abs(abs(put["delta"]) - 0.25) <= 0.10, (t, put["strike"], put["delta"])


def test_a_hedge_under_ten_delta_builds_no_protective_put_or_collar():
    """On a step-20 ladder the only out-of-the-money put is 80P at ~0.002 delta:
    holding it is essentially holding bare stock, so neither hedge row is built.
    The covered call does not hold a put and is unaffected."""
    out = _by_type(ss.build_stock_structures(_ladder_chain(step=20.0, n=2), "XYZ",
                                             100.0, 0.28, 5, 90, call_band=(0.05, 0.10)))
    assert "PROTECTIVE_PUT" not in out and "COLLAR" not in out
    assert "COVERED_CALL" in out


def test_a_collar_whose_call_is_under_five_delta_is_not_built():
    """With 105C and 110C unlisted the nearest out-of-the-money call is 115C at
    ~0.04 delta: a collar selling it is essentially a protective put, so it is not
    built - while the protective put and covered call are."""
    chain = _ladder_chain()
    for k in ("105.0", "110.0"):
        _drop(chain, "callExpDateMap", 30, k)
    out = _by_type(ss.build_stock_structures(chain, "XYZ", 100.0, 0.28, 5, 90,
                                             put_band=(-0.20, -0.10), call_band=(0.10, 0.20)))
    call = next(l for l in out["COVERED_CALL"]["legs"] if l["kind"] == "call")
    assert call["strike"] == 115.0 and abs(call["delta"]) < 0.05
    assert "COLLAR" not in out
    assert "PROTECTIVE_PUT" in out


# ---- Final review: straddles, strangles, flies and condors take the 7-day front ----
def test_neutral_structures_skip_an_expiry_under_seven_days():
    """Operator decision 2026-09-13. The page's DTE min defaults to 0, and on a
    daily-listing name the nearest common expiry is a 0-1 DTE structure: a
    straddle, strangle, butterfly, iron butterfly or condor there is a same-day
    bet, not the position the Finder describes. They take the calendars' and
    share structures' 7-day front floor."""
    chain = _ladder_chain(days=(1, 30))
    rows = (ss.build_straddles_strangles(chain, "XYZ", 100.0, 0.28, 0, 90,
                                         put_band=(-0.20, -0.10), call_band=(0.10, 0.20))
            + ss.build_butterflies_condors(chain, "XYZ", 100.0, 0.28, 0, 90))
    out = _by_type(rows)
    for t in ("LONG_STRADDLE", "SHORT_STRADDLE", "LONG_STRANGLE", "SHORT_STRANGLE",
              "BUTTERFLY_CALL", "BUTTERFLY_PUT", "IRON_BUTTERFLY",
              "CONDOR_CALL", "CONDOR_PUT"):
        assert out[t]["expiration"] == _exp(30), t
        assert all(l["expiration"] == _exp(30) for l in out[t]["legs"]), t


def test_directional_and_debit_verticals_keep_the_nearest_front():
    """The floor is for the neutral and multi-expiry groups only: a single long or
    short option and a debit vertical still build on the nearest expiry in the
    window, 1 DTE here. A $1 ladder, so the 0.60 and 0.30 debit legs land on
    different strikes at 1 DTE."""
    chain = _ladder_chain(days=(1, 30), step=1.0, n=10)
    for rows in (ss.build_directional(chain, "XYZ", 100.0, 0.28, 0, 90),
                 ss.build_debit_verticals(chain, "XYZ", 100.0, 0.28, 0, 90)):
        assert rows
        for s in rows:
            assert s["expiration"] == _exp(1), s["type"]


# ---- Final review: a fly or condor priced the wrong way round is not a trade ----
def _marks(chain, map_key, marks):
    """Overwrite the 30-DTE mark (and a tight bid/ask around it) per strike."""
    for strike, mark in marks.items():
        c = _raw_contract(chain, map_key, 30, strike)
        c["mark"], c["bid"], c["ask"] = mark, mark - 0.05, mark + 0.05


def test_the_default_ladder_builds_a_debit_fly_and_condor_and_a_credit_iron_fly():
    """The control for the three tests below: on fairly priced marks every one of
    these is built, the long structures for a debit and the iron fly for a credit
    under its wing."""
    out = _by_type(ss.build_butterflies_condors(_ladder_chain(), "XYZ", 100.0, 0.28, 5, 90))
    for t in ("BUTTERFLY_CALL", "BUTTERFLY_PUT", "CONDOR_CALL", "CONDOR_PUT"):
        assert out[t]["net_debit"] > 0, t
    assert 0 < out["IRON_BUTTERFLY"]["net_credit"] < 500.0


def test_a_long_butterfly_priced_for_a_credit_is_not_emitted():
    """Mid marks on a wide market can break convexity. Measured: 95C 6.5 / 100C
    4.1 x2 / 105C 1.5 is a $20 CREDIT for a long fly, which reported max loss
    25.2, R:R 20.4, no breakevens and PoP 100 - and ranked first."""
    chain = _ladder_chain()
    _marks(chain, "callExpDateMap", {"95.0": 6.5, "100.0": 4.1, "105.0": 1.5})
    out = _by_type(ss.build_butterflies_condors(chain, "XYZ", 100.0, 0.28, 5, 90))
    assert "BUTTERFLY_CALL" not in out
    assert out["BUTTERFLY_PUT"]["net_debit"] > 0          # the put side is untouched


def test_a_long_condor_priced_for_a_credit_is_not_emitted():
    chain = _ladder_chain()
    # 90P/95P long-short and 105P/110P short-long: 1.0 - 3.0 - 7.0 + 8.0 = -1.0
    _marks(chain, "putExpDateMap", {"90.0": 1.0, "95.0": 3.0, "105.0": 7.0, "110.0": 8.0})
    out = _by_type(ss.build_butterflies_condors(chain, "XYZ", 100.0, 0.28, 5, 90))
    assert "CONDOR_PUT" not in out
    assert out["CONDOR_CALL"]["net_debit"] > 0


def test_an_iron_butterfly_whose_credit_reaches_its_width_is_not_emitted():
    """A $5-wide iron fly collecting $5.20 cannot lose - the marks are wrong, not
    the trade good."""
    chain = _ladder_chain()
    _marks(chain, "callExpDateMap", {"100.0": 4.1, "105.0": 1.5})
    _marks(chain, "putExpDateMap", {"100.0": 4.1, "95.0": 1.5})
    out = _by_type(ss.build_butterflies_condors(chain, "XYZ", 100.0, 0.28, 5, 90))
    assert "IRON_BUTTERFLY" not in out


def test_a_long_fly_costing_its_whole_wing_and_an_iron_fly_for_a_debit_are_not_emitted():
    """The other edge of the same range: a $5-wide call fly bought for $5.10 can
    never profit, and an iron fly that PAYS to open is not an iron fly."""
    chain = _ladder_chain()
    _marks(chain, "callExpDateMap", {"95.0": 9.0, "100.0": 2.0, "105.0": 0.1})
    _marks(chain, "putExpDateMap", {"95.0": 5.0, "100.0": 0.5})
    out = _by_type(ss.build_butterflies_condors(chain, "XYZ", 100.0, 0.28, 5, 90))
    assert "BUTTERFLY_CALL" not in out           # 9.0 - 4.0 + 0.1 = 5.10 debit
    assert "IRON_BUTTERFLY" not in out           # 0.5 + 2.0 - 5.0 - 0.1 = -2.60


def test_a_long_fly_priced_just_under_its_wing_that_commission_makes_unprofitable_is_not_emitted():
    """Inside the range is not enough: a $5-wide call fly bought for $4.97 passes
    ``_priced_inside``, but the four-contract round-trip commission takes its max
    profit below zero (measured -2.2), so it can never make money. The put side,
    fairly priced, is the control."""
    chain = _ladder_chain()
    _marks(chain, "callExpDateMap", {"95.0": 8.97, "100.0": 2.05, "105.0": 0.10})
    out = _by_type(ss.build_butterflies_condors(chain, "XYZ", 100.0, 0.28, 5, 90))
    assert "BUTTERFLY_CALL" not in out           # 8.97 - 4.10 + 0.10 = 4.97 debit
    assert out["BUTTERFLY_PUT"]["max_profit"] > 0


# ---- Strategy Finder redesign: payoff_curve ----
def test_payoff_curve_long_call_is_flat_then_rising_per_contract():
    legs = [_leg("call", "long", 100.0, 3.0)]
    pts = ss.payoff_curve(legs, spot=100.0, atm_iv=0.28, dte=30, n=25)
    assert len(pts) == 25
    xs = [p[0] for p in pts]
    assert xs == sorted(xs) and xs[0] < 100.0 < xs[-1]
    low, high = pts[0][1], pts[-1][1]
    assert abs(low - (-300.0)) < 0.01          # below the strike: lose the debit
    assert high > 0


def test_payoff_curve_spans_two_expected_moves_to_the_front_expiry():
    import math
    legs = [_leg("put", "long", 100.0, 3.0)]
    pts = ss.payoff_curve(legs, spot=100.0, atm_iv=0.28, dte=30, n=25)
    em = 100.0 * 0.28 * math.sqrt(30 / 365)
    assert abs(pts[0][0] - (100.0 - 2 * em)) < 0.02
    assert abs(pts[-1][0] - (100.0 + 2 * em)) < 0.02


def test_payoff_curve_values_a_calendar_at_the_front_expiry():
    short, long_ = _cal_legs()
    pts = ss.payoff_curve([short, long_], spot=100.0, atm_iv=0.28, dte=14, n=25)
    peak = max(pts, key=lambda p: p[1])
    assert abs(peak[0] - 100.0) < 3.0 and peak[1] > 0


def test_payoff_curve_covered_call_includes_the_shares():
    call = _leg("call", "short", 105.0, 1.0)
    call["expiration"] = _exp(30)
    pts = ss.payoff_curve([_stock(100.0), call], spot=100.0, atm_iv=0.28, dte=30, n=25)
    assert pts[0][1] < -500                     # shares lose below spot
    assert abs(pts[-1][1] - 600.0) < 0.5        # capped at strike - spot + credit


def test_payoff_curve_refuses_unusable_inputs_quietly():
    bad = [_leg("call", "short", 100.0, 1.0, iv=-999.0), _leg("call", "long", 100.0, 2.0, iv=-999.0)]
    bad[0]["expiration"], bad[1]["expiration"] = _exp(7), _exp(35)
    assert ss.payoff_curve(bad, spot=100.0, atm_iv=0.28, dte=7) is None
    assert ss.payoff_curve([_leg("call", "long", 100.0, 3.0)], spot=None, atm_iv=0.28, dte=30) is None



def test_payoff_curve_draws_no_shape_for_no_legs():
    """An empty position is not a flat line at zero - that would be a made-up
    shape, which the docstring promises the page never gets."""
    assert ss.payoff_curve([], spot=100.0, atm_iv=0.28, dte=30) is None
    assert ss.payoff_curve(None, spot=100.0, atm_iv=0.28, dte=30) is None

def test_payoff_curve_refuses_a_degenerate_grid():
    legs = [_leg("call", "long", 100.0, 3.0)]
    assert ss.payoff_curve(legs, spot=100.0, atm_iv=0.28, dte=30, n=1) is None
    assert ss.payoff_curve(legs, spot=100.0, atm_iv=0.28, dte=30, n=0) is None


def test_payoff_curve_treats_a_bad_dte_as_one_day():
    import math
    legs = [_leg("call", "long", 100.0, 3.0)]
    # IV 0.60 so two one-day moves (+/-6.3) sit outside the strike +/-3% bound,
    # which would otherwise set the window and hide what dte did.
    em = 100.0 * 0.60 * math.sqrt(1 / 365)
    for bad in (float("nan"), "x", -5, None):
        pts = ss.payoff_curve(legs, spot=100.0, atm_iv=0.60, dte=bad, n=25)
        assert pts is not None and len(pts) == 25, bad
        assert abs(pts[0][0] - (100.0 - 2 * em)) < 0.02, bad
        assert abs(pts[-1][0] - (100.0 + 2 * em)) < 0.02, bad


def test_payoff_curve_window_reaches_the_outermost_strikes():
    # 10-wide wings for a 2.00 credit: max loss $800 lives past +/-2 expected moves.
    ic = [_leg("put", "long", 80.0, 0.50), _leg("put", "short", 90.0, 1.50),
          _leg("call", "short", 110.0, 1.50), _leg("call", "long", 120.0, 0.50)]
    pts = ss.payoff_curve(ic, spot=100.0, atm_iv=0.28, dte=30, n=25)
    assert pts[0][0] <= 80.0 * 0.97 + 0.01 and pts[-1][0] >= 120.0 * 1.03 - 0.01
    assert abs(pts[0][1] - (-800.0)) < 1.0
    assert abs(pts[-1][1] - (-800.0)) < 1.0


def test_payoff_curve_window_ignores_share_legs_strike():
    call = _leg("call", "short", 105.0, 1.0)
    call["expiration"] = _exp(30)
    pts = ss.payoff_curve([_stock(100.0), call], spot=100.0, atm_iv=0.28, dte=30, n=25)
    assert pts is not None and len(pts) == 25


def test_payoff_curve_accepts_a_numpy_iv():
    import math
    import numpy as np
    legs = [_leg("put", "long", 100.0, 3.0)]
    pts = ss.payoff_curve(legs, spot=100.0, atm_iv=np.float32(0.28), dte=30, n=25)
    em = 100.0 * 0.28 * math.sqrt(30 / 365)
    assert abs(pts[0][0] - (100.0 - 2 * em)) < 0.02
    assert abs(pts[-1][0] - (100.0 + 2 * em)) < 0.02


def test_payoff_curve_draws_nothing_for_a_malformed_leg():
    no_mark = _leg("call", "long", 100.0, 3.0)
    del no_mark["mark"]
    assert ss.payoff_curve([no_mark], spot=100.0, atm_iv=0.28, dte=30) is None
    bad_strike = [_leg("put", "short", 95.0, 1.0), _leg("put", "long", "x", 0.5)]
    assert ss.payoff_curve(bad_strike, spot=100.0, atm_iv=0.28, dte=30) is None
