"""Rate my trade, service side (design 2026-09-16, section 3)."""
import math

from services.options_svc import compute  # puts options-scanner on sys.path
from services.options_svc import rate_trade as RT

import strategy_scoring as ssc

EXP = "2026-10-16"


def _c(mark, delta, **kw):
    return [dict({"mark": mark, "bid": round(mark - .05, 2), "ask": round(mark + .05, 2),
                  "delta": delta, "gamma": .01, "theta": -.02, "vega": .1,
                  "volatility": 25.0, "openInterest": 900, "totalVolume": 120}, **kw)]


CHAIN = {"callExpDateMap": {f"{EXP}:30": {"105.0": _c(1.2, .30)}},
         "putExpDateMap": {f"{EXP}:30": {"95.0": _c(1.5, -.25), "90.0": _c(.6, -.12)}}}
PCS = [{"option_type": "put", "side": "short", "strike": 95.0, "expiry": EXP, "qty": 3,
        "premium": 1.55},
       {"option_type": "put", "side": "long", "strike": 90.0, "expiry": EXP, "qty": 3,
        "premium": None}]


# ── the structure map ───────────────────────────────────────────────────────

def test_every_mapped_structure_is_a_type_the_scorer_knows():
    for code, (stype, family, label, bias) in RT.CALC_TO_SCORER.items():
        assert stype in ssc._TYPE_PROFILE, code
        assert family and label, code
        assert bias in ("bullish", "bearish", "neutral"), code


def test_structure_meta_keeps_a_custom_structure_honest():
    assert RT.structure_meta("NAKED_PUT", net_delta=.3) == (
        "SHORT_PUT", "DIRECTIONAL", "Short Put", "bullish", True)
    custom = RT.structure_meta("CUSTOM", net_delta=-.4)
    assert custom[0] == "CUSTOM" and custom[3] == "bearish" and custom[4] is False
    assert RT.structure_meta(None, net_delta=.01)[3] == "neutral"
    assert RT.structure_meta("NOT_A_CODE", net_delta=.4)[3] == "bullish"
    assert RT.structure_meta("CUSTOM", net_delta=None)[3] == "neutral"


# ── the legs ────────────────────────────────────────────────────────────────

def test_legs_take_quotes_from_the_chain_and_the_price_from_the_page():
    legs, err = RT.finder_legs(PCS, CHAIN, spot=100.0)
    assert err is None
    short, long_ = legs
    assert (short["kind"], short["side"], short["strike"], short["expiration"]) == \
        ("put", "short", 95.0, EXP)
    assert short["mark"] == 1.55 and long_["mark"] == 0.6    # page price, else chain mark
    assert short["iv"] == 25.0 and short["oi"] == 900 and short["delta"] == -.25
    assert short["bid"] == 1.45 and short["volume"] == 120


def test_a_zero_or_junk_page_price_falls_back_to_the_chain_mark():
    for bad in (0, 0.0, float("nan"), "x", True):
        legs, _ = RT.finder_legs([dict(PCS[0], premium=bad)], CHAIN, 100.0)
        assert legs[0]["mark"] == 1.5, bad


def test_quantities_reduce_to_the_structures_ratio():
    legs, _ = RT.finder_legs(PCS, CHAIN, spot=100.0)
    assert [l["qty"] for l in legs] == [1, 1]
    fly = [dict(PCS[0], qty=2), dict(PCS[1], qty=4)]
    assert [l["qty"] for l in RT.finder_legs(fly, CHAIN, 100.0)[0]] == [1, 2]
    odd = [dict(PCS[0], qty=2), dict(PCS[1], qty=3)]       # no common ratio: kept
    assert [l["qty"] for l in RT.finder_legs(odd, CHAIN, 100.0)[0]] == [2, 3]


def test_a_share_leg_is_one_lot_at_the_pages_price():
    share = {"option_type": "stock", "side": "long", "strike": None, "expiry": None,
             "qty": 1, "premium": 99.5}
    legs, err = RT.finder_legs([share], CHAIN, 100.0)
    assert err is None and legs[0]["kind"] == "stock" and legs[0]["mark"] == 99.5
    legs, _ = RT.finder_legs([dict(share, premium=None)], CHAIN, 100.0)
    assert legs[0]["mark"] == 100.0                          # unpriced shares cost spot


def test_a_contract_the_chain_lacks_refuses_by_name():
    legs, err = RT.finder_legs([dict(PCS[0], strike=80.0)], CHAIN, 100.0)
    assert legs is None and "80 put" in err and "Oct 16" in err
    legs, err = RT.finder_legs([dict(PCS[0], expiry="2026-11-20")], CHAIN, 100.0)
    assert legs is None and "Nov 20" in err


def test_no_legs_refuses():
    assert RT.finder_legs([], CHAIN, 100.0)[0] is None
    assert RT.finder_legs([{"option_type": "put"}], CHAIN, 100.0)[0] is None


# ── the shared vol inputs (Task 3) ──────────────────────────────────────────

def test_vol_inputs_invert_the_daily_move_and_fall_back_to_current_iv():
    dem, atm = compute.scan_vol_inputs(
        {"expected_moves": {"daily": {"move_dollars": 2.0}}}, 100.0)
    assert dem == 2.0 and math.isclose(atm, 2.0 * math.sqrt(365) / 100)
    assert compute.scan_vol_inputs({"current_iv": 28.0}, 100.0) == (None, 0.28)
    assert compute.scan_vol_inputs({}, 100.0) == (None, 0.20)
    assert compute.scan_vol_inputs(None, 100.0) == (None, 0.20)
