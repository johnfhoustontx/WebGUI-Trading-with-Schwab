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
         "putExpDateMap": {f"{EXP}:30": {"95.0": _c(1.5, -.25, vega=.15), "90.0": _c(.6, -.12)}}}
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


# ── rate(): score and stamp one row (Task 4) ────────────────────────────────

CC = {"symbol": "XYZ", "api": "XYZ", "price": 100.0, "chain": CHAIN}
_IV = {"iv_rank": 42.0, "current_iv": 25.0, "hv_current": 20.0,
       "expected_moves": {"daily": {"move_dollars": 1.3}}}


def _stub(monkeypatch, iv=None, earnings=("clear", None)):
    calls = {}
    monkeypatch.setattr(RT.se, "fetch_price_history",
                        lambda c, s: calls.setdefault("history", s) and {"candles": []})
    monkeypatch.setattr(RT.se, "calc_technicals",
                        lambda h: {"trend": "up", "rsi14": 55, "price": 100, "sma20": 98})

    def _iv(client, symbol, **kw):
        calls["iv"] = (symbol, kw.get("price"), kw.get("chain") is CHAIN)
        return dict(_IV if iv is None else iv)
    monkeypatch.setattr(RT, "run_iv_analysis", _iv)

    def _earn(symbol):
        if isinstance(earnings, Exception):
            raise earnings
        return earnings
    monkeypatch.setattr(RT.compute, "scan_earnings", _earn)
    return calls


def test_rate_returns_a_graded_stamped_row(monkeypatch):
    calls = _stub(monkeypatch)
    out = RT.rate("XYZ", "PCS", PCS, CC, market_state=None)
    assert out["error"] is None
    row = out["row"]
    assert row["type"] == "PCS" and row["family"] == "VERTICAL"
    assert row["grade"] in ("Strong", "Good", "Marginal", "Weak")
    assert isinstance(row["composite_score"], (int, float))
    for k in ("friction_pct", "em_to_expiry", "vol_floor", "earnings_status",
              "iv_rank", "ledger_risk_basis", "daily_em", "fit_score"):
        assert k in row, k
    assert row["iv_rank"] == 42.0 and row["daily_em"] == 1.3
    assert row["earnings_status"] == "clear"
    assert row["structure_known"] is True and row["vol_gate_blocks"] is False
    assert row["id"] == "calc_rate_XYZ"
    # the chain the Calculator loaded is the one analysed - no second fetch
    assert calls["iv"] == ("XYZ", 100.0, True)


def test_a_weak_trade_is_still_returned_graded(monkeypatch):
    _stub(monkeypatch)
    upside_down = [dict(PCS[0], premium=0.01), dict(PCS[1], premium=0.60)]
    out = RT.rate("XYZ", "PCS", upside_down, CC)
    assert out["error"] is None and out["row"]["grade"] == "Weak"


def test_a_custom_structure_is_graded_and_marked_unknown(monkeypatch):
    _stub(monkeypatch)
    row = RT.rate("XYZ", "CUSTOM", PCS, CC)["row"]
    assert row["type"] == "CUSTOM" and row["structure_known"] is False
    assert row["grade"] in ("Strong", "Good", "Marginal", "Weak")


def test_refusals_are_sentences_with_no_row(monkeypatch):
    _stub(monkeypatch)
    none = RT.rate("XYZ", "PCS", PCS, None)
    assert none["row"] is None and "chain" in none["error"].lower()
    wrong = RT.rate("ABC", "PCS", PCS, CC)
    assert wrong["row"] is None and "ABC" in wrong["error"] and "XYZ" in wrong["error"]
    assert RT.rate("XYZ", "PCS", [], CC)["row"] is None
    no_price = RT.rate("XYZ", "PCS", PCS, dict(CC, price=None))
    assert no_price["row"] is None and no_price["error"]
    missing = RT.rate("XYZ", "PCS", [dict(PCS[0], strike=80.0)], CC)
    assert missing["row"] is None and "80 put" in missing["error"]


def test_the_symbol_match_ignores_case_and_the_index_dollar(monkeypatch):
    _stub(monkeypatch)
    spx = dict(CC, symbol="$SPX", api="$SPX")
    assert RT.rate("spx", "PCS", PCS, spx)["error"] is None


def test_a_cheap_premium_short_is_flagged_not_dropped(monkeypatch):
    _stub(monkeypatch, iv=dict(_IV, iv_rank=1.0))
    row = RT.rate("XYZ", "PCS", PCS, CC)["row"]
    assert row is not None and row["vol_gate_blocks"] is True


def test_an_earnings_lookup_that_raises_costs_only_the_earnings_line(monkeypatch):
    _stub(monkeypatch, earnings=RuntimeError("vendor down"))
    row = RT.rate("XYZ", "PCS", PCS, CC)["row"]
    assert row is not None and row["earnings_status"] == "not_listed"


def test_a_failure_inside_scoring_is_a_sentence_not_a_raise(monkeypatch):
    _stub(monkeypatch)
    monkeypatch.setattr(RT.ssc, "score_all", lambda *a, **k: 1 / 0)
    out = RT.rate("XYZ", "PCS", PCS, CC)
    assert out["row"] is None and "ZeroDivisionError" in out["error"]
