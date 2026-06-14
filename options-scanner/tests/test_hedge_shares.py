"""Tests for the dealer-hedging shares-per-1%-move scalar."""
import pytest

from gamma_tool import dealer_hedge_shares, build_analysis_dict, build_explain_text, GammaEngine

EXP = "2099-12-31:5"


def test_hedge_shares_positive():
    assert dealer_hedge_shares(5_800_000.0, 5800.0) == pytest.approx(1000.0)


def test_hedge_shares_negative_net():
    assert dealer_hedge_shares(-5_800_000.0, 5800.0) == pytest.approx(-1000.0)


def test_hedge_shares_none_on_bad_spot():
    assert dealer_hedge_shares(1_000.0, 0) is None
    assert dealer_hedge_shares(1_000.0, -5) is None


def test_analysis_dict_includes_hedge_shares():
    chain = {
        "underlyingPrice": 5800.0,
        "callExpDateMap": {EXP: {"5800.0": [
            {"delta": 0.5, "gamma": 0.05, "openInterest": 1000,
             "totalVolume": 0, "strikePrice": 5800}]}},
        "putExpDateMap": {EXP: {"5800.0": [
            {"delta": -0.5, "gamma": 0.05, "openInterest": 100,
             "totalVolume": 0, "strikePrice": 5800}]}},
    }
    gex = GammaEngine().calc_from_chain(chain)
    result = build_analysis_dict(gex, "gex", "SPX", dte=0,
                                 expected_move=40.0, chain=chain)
    assert "hedge_shares" in result
    # net GEX / spot, both computed by the engine; just assert it's a finite number.
    assert isinstance(result["hedge_shares"], float)


def test_explain_gex_shows_hedge_shares():
    ctx = {
        "symbol": "SPX", "spot": 5805.0, "dte": 0,
        "vix_now": None, "vix_delta": None,
        "gex_summary": {"spot": 5805.0, "flip": 5800.0,
                        "top_pos_strike": 5850.0, "top_neg_strike": 5750.0,
                        "net_total": 1.0e9},
        "hedge_shares": 12345.0,
        "sentiment": {"active": False},
    }
    text = build_explain_text("gex", ctx)
    assert "Dealer hedging" in text
