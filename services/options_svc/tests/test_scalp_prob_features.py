"""The chance's factors (scalp_prob_features): each one signed by the call's
direction, a gap kept as None until scaling, and scaling that never centres.

The snapshot fixtures follow ``scalp_confirm.snapshot``'s shape.
"""
import pytest

from shared import scalp_config
from shared import symbols as _symbols
from services.options_svc import scalp_prob_features as pf


def _row(spot=500.0, call_wall=None, put_wall=None, flip=None, net_gex=1.0e6):
    return {"spot": spot, "call_wall": call_wall, "put_wall": put_wall,
            "flip": flip, "net_gex": net_gex, "walls_untrusted": not net_gex}


def _snap(matrix=None, netprem=None, sentiment=None, flow=None):
    return {"matrix": matrix or {}, "netprem": netprem or {}, "sentiment": sentiment,
            "flow": flow, "sources": {}}


def test_untrusted_wall_is_a_gap_not_no_room():
    snap = _snap({"SPY": _row(spot=500.0, call_wall=490.0, net_gex=0)})
    out = pf.source_factors(snap, "SPY", 1, 10.0, 500.0)
    assert out["room"] is None
    assert out["flip_side"] is None


def test_room_ratio_capped_and_signed():
    snap = _snap({"SPY": _row(call_wall=530.0, put_wall=470.0)})
    out = pf.source_factors(snap, "SPY", 1, 10.0, 500.0)
    assert out["room"] == scalp_config.prob_room_cap() == 3.0
    near = _snap({"SPY": _row(call_wall=505.0)})
    assert pf.source_factors(near, "SPY", 1, 10.0, 500.0)["room"] == pytest.approx(0.5)
    beyond = _snap({"SPY": _row(call_wall=495.0)})
    assert pf.source_factors(beyond, "SPY", 1, 10.0, 500.0)["room"] == pytest.approx(-0.5)
    down = _snap({"SPY": _row(put_wall=495.0)})
    assert pf.source_factors(down, "SPY", -1, 10.0, 500.0)["room"] == pytest.approx(0.5)
    way = _snap({"SPY": _row(call_wall=900.0)})
    assert pf.source_factors(way, "SPY", 1, 10.0, 500.0)["room"] == 3.0
    under = _snap({"SPY": _row(call_wall=100.0)})
    assert pf.source_factors(under, "SPY", 1, 10.0, 500.0)["room"] == -3.0


def test_room_needs_win_points_and_a_wall():
    snap = _snap({"SPY": _row(call_wall=530.0)})
    assert pf.source_factors(snap, "SPY", 1, None, 500.0)["room"] is None
    assert pf.source_factors(snap, "SPY", 1, 0.0, 500.0)["room"] is None
    assert pf.source_factors(snap, "SPY", -1, 10.0, 500.0)["room"] is None  # no put wall
    assert pf.source_factors(None, "SPY", 1, 10.0, 500.0)["room"] is None


def test_room_uses_the_entry_spot_not_the_matrix_spot():
    snap = _snap({"SPY": _row(spot=520.0, call_wall=510.0)})
    # From the matrix spot the wall is already behind; from the entry it is 10 away.
    assert pf.source_factors(snap, "SPY", 1, 10.0, 500.0)["room"] == pytest.approx(1.0)


def test_dollar_prefixed_symbol_matches_either_spelling():
    snap = _snap({"$SPX": _row(spot=5000.0, call_wall=5010.0)})
    assert pf.source_factors(snap, "$SPX", 1, 10.0, 5000.0)["room"] == pytest.approx(1.0)
    assert pf.source_factors(snap, "SPX", 1, 10.0, 5000.0)["room"] == pytest.approx(1.0)


def test_flip_side_follows_direction():
    snap = _snap({"SPY": _row(flip=495.0)})
    assert pf.source_factors(snap, "SPY", 1, 10.0, 500.0)["flip_side"] == 1
    assert pf.source_factors(snap, "SPY", -1, 10.0, 500.0)["flip_side"] == -1
    snap = _snap({"SPY": _row(flip=505.0)})
    assert pf.source_factors(snap, "SPY", 1, 10.0, 500.0)["flip_side"] == -1
    assert pf.source_factors(snap, "SPY", -1, 10.0, 500.0)["flip_side"] == 1
    assert pf.source_factors(_snap({"SPY": _row(flip=None)}), "SPY", 1, 10.0,
                             500.0)["flip_side"] is None


def test_flip_side_is_a_gap_without_a_spot():
    snap = _snap({"SPY": _row(spot=None, flip=495.0)})
    assert pf.source_factors(snap, "SPY", 1, 10.0, None)["flip_side"] is None


def _big10_matrix(with_room, without_room, untrusted=0):
    names = list(_symbols.big10())
    matrix = {}
    for i, name in enumerate(names):
        if i < with_room:
            matrix[name] = _row(spot=100.0, call_wall=103.0)       # 3% room
        elif i < with_room + without_room:
            matrix[name] = _row(spot=100.0, call_wall=100.2)       # 0.2% room
        elif i < with_room + without_room + untrusted:
            matrix[name] = _row(spot=100.0, call_wall=103.0, net_gex=0)
    return matrix


def test_breadth_room_is_share_minus_half():
    # win 10 points on a 500 spot = 2%; 8 names have 3%, 2 have 0.2%.
    snap = _snap(_big10_matrix(8, 2))
    out = pf.source_factors(snap, "SPY", 1, 10.0, 500.0)
    assert out["breadth_room"] == pytest.approx(0.8 - 0.5)


def test_breadth_room_gap_under_min_members():
    need = scalp_config.breadth_min_members()
    snap = _snap(_big10_matrix(need - 1, 0))
    assert pf.source_factors(snap, "SPY", 1, 10.0, 500.0)["breadth_room"] is None
    snap = _snap(_big10_matrix(need, 0))
    assert pf.source_factors(snap, "SPY", 1, 10.0, 500.0)["breadth_room"] == pytest.approx(0.5)


def test_breadth_untrusted_names_are_not_counted():
    # 3 with room, 7 untrusted: only 3 read, under the minimum.
    snap = _snap(_big10_matrix(3, 0, untrusted=7))
    assert pf.source_factors(snap, "SPY", 1, 10.0, 500.0)["breadth_room"] is None


def test_breadth_needs_a_target_and_a_spot():
    snap = _snap(_big10_matrix(8, 2))
    assert pf.source_factors(snap, "SPY", 1, None, 500.0)["breadth_room"] is None
    assert pf.source_factors(snap, "SPY", 1, 10.0, None)["breadth_room"] is None


def test_against_roles_flip_sign():
    assert pf.ROLE_SIGN == {"long_with": 1, "credit_with": 1,
                            "long_against": -1, "credit_against": -1}


def test_netprem_sign_follows_direction():
    snap = _snap(netprem={"SPY": {"call": 3.0, "put": 1.0, "ts": 1}})
    assert pf.source_factors(snap, "SPY", 1, 10.0, 500.0)["netprem"] == pytest.approx(0.5)
    assert pf.source_factors(snap, "SPY", -1, 10.0, 500.0)["netprem"] == pytest.approx(-0.5)


def test_netprem_gaps():
    zero = _snap(netprem={"SPY": {"call": 0.0, "put": 0.0, "ts": 1}})
    assert pf.source_factors(zero, "SPY", 1, 10.0, 500.0)["netprem"] is None
    assert pf.source_factors(_snap(netprem={"SPY": None}), "SPY", 1, 10.0,
                             500.0)["netprem"] is None
    assert pf.source_factors(_snap(), "SPY", 1, 10.0, 500.0)["netprem"] is None


def test_flow_signed_capped_and_divided_by_the_cap():
    cap = scalp_config.prob_flow_cap()
    win = lambda bull, bear: _snap(flow={"date": "2026-10-14", "window": {  # noqa: E731
        "state": "ok", "bull": bull, "bear": bear, "net": bull - bear}})
    assert pf.source_factors(win(2, 0), "SPY", 1, 10.0, 500.0)["flow"] == pytest.approx(2 / cap)
    assert pf.source_factors(win(2, 0), "SPY", -1, 10.0, 500.0)["flow"] == pytest.approx(-2 / cap)
    assert pf.source_factors(win(9, 1), "SPY", 1, 10.0, 500.0)["flow"] == 1.0
    assert pf.source_factors(win(0, 9), "SPY", 1, 10.0, 500.0)["flow"] == -1.0
    # A quiet window is a real zero, not a gap.
    assert pf.source_factors(win(0, 0), "SPY", 1, 10.0, 500.0)["flow"] == 0.0


def test_flow_other_day_is_a_gap():
    other = _snap(flow={"date": "2026-10-13", "state": "other_day"})
    assert pf.source_factors(other, "SPY", 1, 10.0, 500.0)["flow"] is None
    assert pf.source_factors(_snap(flow=None), "SPY", 1, 10.0, 500.0)["flow"] is None
    nowindow = _snap(flow={"date": "d", "n": 3, "window": None})
    assert pf.source_factors(nowindow, "SPY", 1, 10.0, 500.0)["flow"] is None


def test_sentiment_follows_the_state_and_direction():
    bull = _snap(sentiment={"state": "bullish", "score": 7, "age_min": 1})
    assert pf.source_factors(bull, "SPY", 1, 10.0, 500.0)["sentiment"] == 1
    assert pf.source_factors(bull, "SPY", -1, 10.0, 500.0)["sentiment"] == -1
    neutral = _snap(sentiment={"state": "neutral", "score": 0, "age_min": 1})
    assert pf.source_factors(neutral, "SPY", 1, 10.0, 500.0)["sentiment"] is None
    assert pf.source_factors(_snap(), "SPY", 1, 10.0, 500.0)["sentiment"] is None


def test_a_missing_snapshot_is_all_gaps():
    out = pf.source_factors(None, "SPY", 1, 10.0, 500.0)
    assert set(out) == {"room", "breadth_room", "flip_side", "netprem", "flow",
                        "sentiment"}
    assert all(v is None for v in out.values())


def test_price_inputs_signed_by_direction():
    assert pf.price_factors({"since_open": 0.2}, -1)["price:since_open"] == pytest.approx(-0.2)
    assert pf.price_factors({"since_open": 0.2}, 1)["price:since_open"] == pytest.approx(0.2)
    assert pf.price_factors({"vol_change": 1.0}, 1)["price:vol_change"] == pytest.approx(-1.0)
    assert pf.price_factors({"vol_change": 1.0}, -1)["price:vol_change"] == pytest.approx(1.0)


def test_price_inputs_by_kind():
    out = pf.price_factors({"breadth_up_share": 0.75, "fut_range_pos": 0.25,
                            "open_close_loc": 1.0, "open_broke_pm": -1.0,
                            "open_range_ratio": 1.4, "etf:XLK": 0.3,
                            "fut_gap": None, "mystery": 0.5}, -1)
    assert out["price:breadth_up_share"] == pytest.approx(-0.5)
    assert out["price:fut_range_pos"] == pytest.approx(0.5)
    assert out["price:open_close_loc"] == pytest.approx(-1.0)
    assert out["price:open_broke_pm"] == pytest.approx(1.0)
    assert out["price:open_range_ratio"] == pytest.approx(1.4)
    assert out["price:etf:XLK"] == pytest.approx(-0.3)
    assert out["price:fut_gap"] is None
    assert out["price:mystery"] == pytest.approx(-0.5)


def test_price_inputs_not_finite_is_a_gap():
    out = pf.price_factors({"since_open": float("nan"), "fut_15m": "x"}, 1)
    assert out["price:since_open"] is None
    assert out["price:fut_15m"] is None


def test_trend_signed():
    out = pf.trend_signed({"slope_5": 0.1, "rsi_5": 75.0, "slope_15": None,
                           "rsi_15": 25.0, "slope_60": -0.2, "rsi_60": None}, -1)
    assert out["slope_5"] == pytest.approx(-0.1)
    assert out["rsi_5"] == pytest.approx(-0.5)
    assert out["slope_15"] is None
    assert out["rsi_15"] == pytest.approx(0.5)
    assert out["slope_60"] == pytest.approx(0.2)
    assert out["rsi_60"] is None


def test_factors_union_for_an_intraday_call():
    snap = _snap({"SPY": _row(call_wall=505.0, flip=495.0)})
    out = pf.factors("intraday", "SPY", "09:06", 1, {"since_open": 0.2},
                     {"slope_5": 0.1, "rsi_5": 60.0}, snap, 10.0, 500.0)
    assert out["price:since_open"] == pytest.approx(0.2)
    assert out["slope_5"] == pytest.approx(0.1)
    assert out["rsi_5"] == pytest.approx(0.2)
    assert out["room"] == pytest.approx(0.5)
    assert out["flip_side"] == 1
    assert out["block:midday"] == 0.0 and out["block:afternoon"] == 0.0


def test_factors_blocks_follow_the_slot():
    for slot, mid, aft in (("08:51", 0.0, 0.0), ("10:51", 1.0, 0.0), ("13:06", 0.0, 1.0)):
        out = pf.factors("intraday", "SPY", slot, 1, {}, {}, None, 10.0, 500.0)
        assert (out["block:midday"], out["block:afternoon"]) == (mid, aft), slot


def test_morning_call_has_no_block_indicators():
    out = pf.factors("morning", "SPY", "08:36", 1, {"fut_gap": 0.1}, {}, None, 10.0, 500.0)
    assert not any(k.startswith("block:") for k in out)
    assert out["price:fut_gap"] == pytest.approx(0.1)


def test_fit_scaling_is_the_interquartile_range():
    rows = [{"a": v, "flow": 1.0, "b": 5.0} for v in (1.0, 2.0, 3.0, 4.0, 5.0)]
    s = pf.fit_scaling(rows)
    assert s["a"] == pytest.approx(2.0)         # q75 4 - q25 2
    assert s["b"] == 1.0                        # range 0
    assert "flow" not in s                      # discrete


def test_fit_scaling_needs_four_values_and_skips_gaps():
    rows = [{"a": 1.0}, {"a": None}, {"a": 2.0}, {"a": 100.0}]
    assert pf.fit_scaling(rows)["a"] == 1.0     # three real values
    rows = [{"a": v} for v in (1.0, 2.0, 3.0, 4.0)] + [{"a": None}] * 5
    assert pf.fit_scaling(rows)["a"] == pytest.approx(1.5)


def test_fit_scaling_a_name_in_some_rows_only():
    rows = [{"a": 1.0}, {"a": 2.0, "z": 3.0}, {"a": 3.0}, {"a": 4.0}]
    assert pf.fit_scaling(rows)["z"] == 1.0


def test_scaled_never_centres():
    rows = [{"a": v} for v in (1.0, 2.0, 3.0, 4.0)]
    s = pf.fit_scaling(rows)
    out = pf.scaled({"a": 0.0}, s)
    assert out["a"] == 0.0
    assert pf.scaled({"a": None}, s)["a"] == 0.0
    assert pf.scaled({"a": 3.0}, s)["a"] == pytest.approx(3.0 / s["a"])
    assert pf.scaled({"a": -3.0}, s)["a"] == pytest.approx(-3.0 / s["a"])


def test_scaled_is_capped():
    cap = scalp_config.prob_cap()
    assert pf.scaled({"a": 1000.0}, {"a": 1.0})["a"] == cap
    assert pf.scaled({"a": -1000.0}, {"a": 1.0})["a"] == -cap


def test_discrete_factors_not_rescaled():
    scaling = {"flow": 0.01, "flip_side": 0.01, "sentiment": 0.01,
               "block:midday": 0.01, "block:afternoon": 0.01}
    out = pf.scaled({"flow": 1.0, "flip_side": -1, "sentiment": 1, "block:midday": 1.0,
                     "block:afternoon": 0.0}, scaling)
    assert out == {"flow": 1.0, "flip_side": -1.0, "sentiment": 1.0,
                   "block:midday": 1.0, "block:afternoon": 0.0}
    assert pf.scaled({"flow": None, "flip_side": None}, {}) == {"flow": 0.0, "flip_side": 0.0}


def test_a_factor_without_a_scale_is_zero():
    assert pf.scaled({"unknown": 5.0}, {"a": 1.0})["unknown"] == 0.0


def test_wide_and_tight():
    assert pf.is_wide("price:since_open") and pf.is_wide("slope_15")
    assert pf.is_wide("rsi_60") and pf.is_wide("block:midday")
    assert not any(pf.is_wide(n) for n in ("room", "breadth_room", "flip_side",
                                           "netprem", "flow", "sentiment"))
