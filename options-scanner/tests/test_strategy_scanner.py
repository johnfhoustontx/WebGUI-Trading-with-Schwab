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
    for t, side in (("LONG_STRADDLE", "long"), ("SHORT_STRADDLE", "short")):
        legs = out[t]["legs"]
        assert {(l["kind"], l["side"], l["strike"]) for l in legs} == {
            ("call", side, 100.0), ("put", side, 100.0)}
        assert out[t]["expiration"] == _exp(30) and out[t]["family"] == "NEUTRAL"


def test_short_strangle_aims_at_the_band_midpoint_and_long_mirrors_it():
    out = _by_type(ss.build_straddles_strangles(
        _ladder_chain(), "XYZ", 100.0, 0.28, 5, 90,
        put_band=(-0.20, -0.10), call_band=(0.10, 0.20)))
    short = {l["kind"]: l for l in out["SHORT_STRANGLE"]["legs"]}
    long_ = {l["kind"]: l for l in out["LONG_STRANGLE"]["legs"]}
    assert short["call"]["strike"] > 100.0 and short["put"]["strike"] < 100.0
    assert abs(abs(short["call"]["delta"]) - 0.15) < 0.08
    assert {k: l["strike"] for k, l in short.items()} == {k: l["strike"] for k, l in long_.items()}


def test_the_band_ceiling_drops_a_short_strangle_but_never_a_straddle():
    rich = _ladder_chain(step=20.0, n=2)   # nothing between ATM (0.5) and far OTM
    out = _by_type(ss.build_straddles_strangles(
        rich, "XYZ", 100.0, 0.28, 5, 90, put_band=(-0.20, -0.30), call_band=(0.20, 0.30)))
    assert "SHORT_STRADDLE" in out
    for l in out.get("SHORT_STRANGLE", {"legs": []})["legs"]:
        assert abs(l["delta"]) <= 0.30


def test_the_band_ceiling_binds_when_the_only_otm_strike_is_richer_than_it():
    """The step-20 ladder above never exercises the ceiling: its far strikes sit
    at |delta| ~0.01, inside any band. Here the only OTM strikes are 105 (call
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
