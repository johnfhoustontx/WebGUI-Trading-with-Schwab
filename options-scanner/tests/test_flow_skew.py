"""
test_flow_skew.py - unit tests for the PURE options-flow skew module.

Covers the 25-delta risk-reversal and index call/put volume pure functions.
"""
import flow_skew


#############################################
# FIXTURE HELPERS
#############################################

def _call(strike, delta, iv, vol=0, oi=0):
    return {"strike": strike, "delta": delta, "volatility": iv,
            "totalVolume": vol, "openInterest": oi}


def _put(strike, delta, iv, vol=0, oi=0):
    return {"strike": strike, "delta": delta, "volatility": iv,
            "totalVolume": vol, "openInterest": oi}


#############################################
# risk_reversal_25d
#############################################

def test_rr_exact_deltas():
    chain = {
        "callExpDateMap": {
            "2026-07-11:3": {
                "5900.0": [_call(5900.0, 0.25, 18.0)],
                "5950.0": [_call(5950.0, 0.10, 17.0)],
            },
        },
        "putExpDateMap": {
            "2026-07-11:3": {
                "5800.0": [_put(5800.0, -0.25, 22.0)],
                "5750.0": [_put(5750.0, -0.10, 24.0)],
            },
        },
    }
    rr = flow_skew.risk_reversal_25d(chain)
    assert rr is not None
    assert rr["call_iv"] == 18.0
    assert rr["put_iv"] == 22.0
    assert rr["rr"] == 4.0


def test_rr_nearest_delta_fallback():
    # No exact 0.25/-0.25; nearest picks the closest.
    chain = {
        "callExpDateMap": {
            "2026-07-11:3": {
                "5900.0": [_call(5900.0, 0.30, 18.0)],   # |0.30-0.25|=0.05
                "5950.0": [_call(5950.0, 0.15, 17.0)],   # |0.15-0.25|=0.10
            },
        },
        "putExpDateMap": {
            "2026-07-11:3": {
                "5800.0": [_put(5800.0, -0.22, 22.0)],   # |-0.22+0.25|=0.03  <- closest
                "5750.0": [_put(5750.0, -0.40, 24.0)],   # |-0.40+0.25|=0.15
            },
        },
    }
    rr = flow_skew.risk_reversal_25d(chain)
    assert rr["call_iv"] == 18.0   # 0.30 nearest to 0.25
    assert rr["put_iv"] == 22.0    # -0.22 nearest to -0.25
    assert rr["rr"] == 4.0


def test_rr_put_skew_positive():
    chain = {
        "callExpDateMap": {"2026-07-11:3": {"5900.0": [_call(5900.0, 0.25, 15.0)]}},
        "putExpDateMap": {"2026-07-11:3": {"5800.0": [_put(5800.0, -0.25, 28.0)]}},
    }
    rr = flow_skew.risk_reversal_25d(chain)
    assert rr["rr"] > 0
    assert rr["rr"] == 13.0


def test_rr_uses_front_expiration_only():
    chain = {
        "callExpDateMap": {
            "2026-07-18:10": {"5900.0": [_call(5900.0, 0.25, 99.0)]},  # far exp, wrong IV
            "2026-07-11:3": {"5900.0": [_call(5900.0, 0.25, 18.0)]},   # FRONT
        },
        "putExpDateMap": {
            "2026-07-18:10": {"5800.0": [_put(5800.0, -0.25, 99.0)]},
            "2026-07-11:3": {"5800.0": [_put(5800.0, -0.25, 22.0)]},
        },
    }
    rr = flow_skew.risk_reversal_25d(chain)
    assert rr["call_iv"] == 18.0
    assert rr["put_iv"] == 22.0


def test_rr_uses_shared_front_expiration():
    # Call map's earliest exp (2026-07-10:2) is ABSENT from the put map; both
    # maps share 2026-07-11:3. RR must read BOTH legs from the shared 07-11 exp,
    # NOT the call map's earlier-but-unmatched front.
    chain = {
        "callExpDateMap": {
            "2026-07-10:2": {"5900.0": [_call(5900.0, 0.25, 99.0)]},  # unmatched front
            "2026-07-11:3": {"5900.0": [_call(5900.0, 0.25, 18.0)]},  # SHARED
        },
        "putExpDateMap": {
            "2026-07-11:3": {"5800.0": [_put(5800.0, -0.25, 22.0)]},  # SHARED (earliest)
        },
    }
    rr = flow_skew.risk_reversal_25d(chain)
    assert rr["call_iv"] == 18.0   # from shared 07-11, not the 99.0 at 07-10
    assert rr["put_iv"] == 22.0
    assert rr["rr"] == 4.0


def test_rr_no_common_expiration_none():
    chain = {
        "callExpDateMap": {"2026-07-10:2": {"5900.0": [_call(5900.0, 0.25, 18.0)]}},
        "putExpDateMap": {"2026-07-11:3": {"5800.0": [_put(5800.0, -0.25, 22.0)]}},
    }
    assert flow_skew.risk_reversal_25d(chain) is None


def test_rr_skips_none_volatility():
    # Nearest-by-delta call has None IV -> must be skipped; next-nearest used.
    chain = {
        "callExpDateMap": {
            "2026-07-11:3": {
                "5900.0": [_call(5900.0, 0.26, None)],  # nearest delta but no IV
                "5950.0": [_call(5950.0, 0.20, 17.0)],  # usable
            },
        },
        "putExpDateMap": {"2026-07-11:3": {"5800.0": [_put(5800.0, -0.25, 22.0)]}},
    }
    rr = flow_skew.risk_reversal_25d(chain)
    assert rr["call_iv"] == 17.0
    assert rr["put_iv"] == 22.0


def test_rr_skips_none_delta():
    chain = {
        "callExpDateMap": {
            "2026-07-11:3": {
                "5900.0": [_call(5900.0, None, 18.0)],   # no delta -> skip
                "5950.0": [_call(5950.0, 0.24, 16.0)],   # usable
            },
        },
        "putExpDateMap": {"2026-07-11:3": {"5800.0": [_put(5800.0, -0.25, 22.0)]}},
    }
    rr = flow_skew.risk_reversal_25d(chain)
    assert rr["call_iv"] == 16.0


def test_rr_empty_chain_none():
    assert flow_skew.risk_reversal_25d({}) is None
    assert flow_skew.risk_reversal_25d(None) is None
    assert flow_skew.risk_reversal_25d({"callExpDateMap": {}, "putExpDateMap": {}}) is None


def test_rr_missing_put_side_none():
    chain = {
        "callExpDateMap": {"2026-07-11:3": {"5900.0": [_call(5900.0, 0.25, 18.0)]}},
        "putExpDateMap": {},
    }
    assert flow_skew.risk_reversal_25d(chain) is None


def test_rr_no_usable_call_none():
    chain = {
        "callExpDateMap": {"2026-07-11:3": {"5900.0": [_call(5900.0, None, None)]}},
        "putExpDateMap": {"2026-07-11:3": {"5800.0": [_put(5800.0, -0.25, 22.0)]}},
    }
    assert flow_skew.risk_reversal_25d(chain) is None


def test_rr_malformed_contracts_dont_raise():
    chain = {
        "callExpDateMap": {"2026-07-11:3": {"5900.0": ["garbage", {}, _call(5900.0, 0.25, 18.0)]}},
        "putExpDateMap": {"2026-07-11:3": {"5800.0": [None, _put(5800.0, -0.25, 22.0)]}},
    }
    rr = flow_skew.risk_reversal_25d(chain)
    assert rr["call_iv"] == 18.0
    assert rr["put_iv"] == 22.0


#############################################
# index_call_put_volume
#############################################

def test_volume_sums_across_strikes_and_expirations():
    chain = {
        "callExpDateMap": {
            "2026-07-11:3": {
                "5900.0": [_call(5900.0, 0.25, 18.0, vol=100)],
                "5950.0": [_call(5950.0, 0.10, 17.0, vol=200)],
            },
            "2026-07-18:10": {
                "5900.0": [_call(5900.0, 0.25, 18.0, vol=50)],
            },
        },
        "putExpDateMap": {
            "2026-07-11:3": {
                "5800.0": [_put(5800.0, -0.25, 22.0, vol=300)],
            },
            "2026-07-18:10": {
                "5750.0": [_put(5750.0, -0.10, 24.0, vol=100)],
            },
        },
    }
    v = flow_skew.index_call_put_volume(chain)
    assert v["call_vol"] == 350
    assert v["put_vol"] == 400
    assert v["ratio"] == 400 / 350


def test_volume_call_zero_ratio_none():
    chain = {
        "callExpDateMap": {"2026-07-11:3": {"5900.0": [_call(5900.0, 0.25, 18.0, vol=0)]}},
        "putExpDateMap": {"2026-07-11:3": {"5800.0": [_put(5800.0, -0.25, 22.0, vol=300)]}},
    }
    v = flow_skew.index_call_put_volume(chain)
    assert v["call_vol"] == 0
    assert v["put_vol"] == 300
    assert v["ratio"] is None


def test_volume_empty_none():
    assert flow_skew.index_call_put_volume({}) is None
    assert flow_skew.index_call_put_volume(None) is None


def test_volume_missing_or_none_totalvolume_treated_zero():
    chain = {
        "callExpDateMap": {
            "2026-07-11:3": {
                "5900.0": [{"strike": 5900.0}],                       # no totalVolume
                "5950.0": [_call(5950.0, 0.10, 17.0, vol=None)],      # None vol
                "6000.0": [_call(6000.0, 0.05, 16.0, vol=40)],
            },
        },
        "putExpDateMap": {
            "2026-07-11:3": {"5800.0": [_put(5800.0, -0.25, 22.0, vol=60)]},
        },
    }
    v = flow_skew.index_call_put_volume(chain)
    assert v["call_vol"] == 40
    assert v["put_vol"] == 60
    assert v["ratio"] == 60 / 40


def test_volume_malformed_contracts_dont_raise():
    chain = {
        "callExpDateMap": {"2026-07-11:3": {"5900.0": ["x", None, _call(5900.0, 0.25, 18.0, vol=10)]}},
        "putExpDateMap": {"2026-07-11:3": {"5800.0": [_put(5800.0, -0.25, 22.0, vol=5)]}},
    }
    v = flow_skew.index_call_put_volume(chain)
    assert v["call_vol"] == 10
    assert v["put_vol"] == 5


#############################################
# index_call_put_premium
#############################################

def _pc(vol, mark=None, bid=None, ask=None):
    d = {"strike": 100.0, "totalVolume": vol}
    if mark is not None:
        d["mark"] = mark
    if bid is not None:
        d["bid"] = bid
    if ask is not None:
        d["ask"] = ask
    return d


def test_index_call_put_premium_sums_mark_x_vol_x100():
    chain = {
        "callExpDateMap": {"2026-07-11:3": {"100.0": [_pc(10, mark=2.0)],
                                            "105.0": [_pc(5, mark=1.0)]}},
        "putExpDateMap": {"2026-07-11:3": {"95.0": [_pc(4, mark=3.0)]}},
    }
    out = flow_skew.index_call_put_premium(chain)
    assert out["call_prem"] == (10 * 2.0 + 5 * 1.0) * 100  # 2500
    assert out["put_prem"] == 4 * 3.0 * 100                # 1200


def test_premium_falls_back_to_bid_ask_mid():
    chain = {"callExpDateMap": {"e": {"100.0": [_pc(10, bid=1.0, ask=3.0)]}},  # mid 2.0
             "putExpDateMap": {}}
    assert flow_skew.index_call_put_premium(chain)["call_prem"] == 10 * 2.0 * 100  # 2000


def test_premium_skips_unusable_and_zero_vol():
    chain = {"callExpDateMap": {"e": {"100.0": [{"strike": 100, "totalVolume": 10},  # no price
                                               _pc(0, mark=5.0)]}},                  # zero vol
             "putExpDateMap": {}}
    assert flow_skew.index_call_put_premium(chain)["call_prem"] == 0.0


def test_premium_defensive_empty_and_none():
    assert flow_skew.index_call_put_premium({"callExpDateMap": {}, "putExpDateMap": {}}) == {
        "call_prem": 0.0, "put_prem": 0.0}
    assert flow_skew.index_call_put_premium(None) is None
    assert flow_skew.index_call_put_premium({}) is None


#############################################
# premium_by_strike
#############################################
# The per-strike companion to index_call_put_premium. Its return shape is not a
# free choice: it is written straight into gex_history_db as a ``view="prem"``
# grid, whose columnar float32 packer accepts EXACTLY three plain numbers per
# cell ({call, put, net}). A test below pins that, because drifting off it would
# not fail loudly — it would silently fall back to the slower JSON path.

def _sc(strike, vol, mark=None, bid=None, ask=None):
    """One contract at ``strike``; mirrors _pc but lets the strike vary."""
    d = {"strike": strike, "totalVolume": vol}
    if mark is not None:
        d["mark"] = mark
    if bid is not None:
        d["bid"] = bid
    if ask is not None:
        d["ask"] = ask
    return d


def test_premium_by_strike_splits_call_and_put_per_strike():
    chain = {
        "callExpDateMap": {"2026-07-11:3": {
            "100.0": [_sc(100.0, 10, mark=2.0)],
            "105.0": [_sc(105.0, 5, mark=1.0)],
        }},
        "putExpDateMap": {"2026-07-11:3": {
            "100.0": [_sc(100.0, 4, mark=3.0)],
        }},
    }
    out = flow_skew.premium_by_strike(chain)
    assert out[100.0] == {"call": 2000.0, "put": 1200.0, "net": 800.0}
    # A strike traded on one side only still reports the other side as 0.0 —
    # absent is not unknown here, it is "nothing traded".
    assert out[105.0] == {"call": 500.0, "put": 0.0, "net": 500.0}


def test_premium_by_strike_accumulates_across_expirations():
    # The collector fetches today -> +7d, so one strike legitimately appears in
    # several expiration maps. The ladder is a per-STRIKE read, so they sum.
    chain = {
        "callExpDateMap": {
            "2026-07-11:0": {"100.0": [_sc(100.0, 10, mark=2.0)]},
            "2026-07-18:7": {"100.0": [_sc(100.0, 3, mark=1.0)]},
        },
        "putExpDateMap": {},
    }
    assert flow_skew.premium_by_strike(chain)[100.0]["call"] == 10 * 2.0 * 100 + 3 * 1.0 * 100


def test_premium_by_strike_totals_match_index_call_put_premium():
    """The two functions must never disagree — the rail's session totals and the
    ladder's columns are read side by side, and a reader would see it."""
    chain = {
        "callExpDateMap": {"e": {"100.0": [_sc(100.0, 10, mark=2.0)],
                                 "105.0": [_sc(105.0, 5, bid=0.5, ask=1.5)]}},
        "putExpDateMap": {"e": {"95.0": [_sc(95.0, 4, mark=3.0)],
                                "100.0": [_sc(100.0, 1, mark=1.0)]}},
    }
    ladder = flow_skew.premium_by_strike(chain)
    totals = flow_skew.index_call_put_premium(chain)
    assert sum(c["call"] for c in ladder.values()) == totals["call_prem"]
    assert sum(c["put"] for c in ladder.values()) == totals["put_prem"]


def test_premium_by_strike_cells_are_packer_shaped():
    chain = {"callExpDateMap": {"e": {"100.0": [_sc(100.0, 10, mark=2.0)]}},
             "putExpDateMap": {}}
    for strike, cell in flow_skew.premium_by_strike(chain).items():
        assert isinstance(strike, float)
        assert set(cell) == {"call", "put", "net"}
        # bools are ints in Python and would sail through a bare isinstance
        # check, so exclude them explicitly — the packer's own gate does too.
        assert all(type(v) is float for v in cell.values())


def test_premium_by_strike_prefers_map_key_over_contract_strike():
    # The map key is the authoritative strike; a contract's own field can be
    # absent or disagree. Keying off the contract would scatter one strike's
    # premium across several ladder rows.
    chain = {"callExpDateMap": {"e": {"100.0": [{"totalVolume": 10, "mark": 2.0}]}},
             "putExpDateMap": {}}
    assert list(flow_skew.premium_by_strike(chain)) == [100.0]


def test_premium_by_strike_skips_unusable_rows():
    chain = {
        "callExpDateMap": {"e": {
            "100.0": [_sc(100.0, 10, mark=2.0), _sc(100.0, 0, mark=5.0),  # zero vol
                      {"strike": 100.0, "totalVolume": 7}],               # no price
            "notastrike": [_sc(1.0, 10, mark=2.0)],                       # bad key
        }},
        "putExpDateMap": {},
    }
    out = flow_skew.premium_by_strike(chain)
    assert out == {100.0: {"call": 2000.0, "put": 0.0, "net": 2000.0}}


def test_premium_by_strike_omits_strikes_that_traded_nothing():
    """A strike with volume but no usable price contributes no row at all —
    an all-zero row is indistinguishable from a real 0.0 and would pad the
    ladder with noise."""
    chain = {"callExpDateMap": {"e": {"100.0": [{"strike": 100.0, "totalVolume": 7}]}},
             "putExpDateMap": {}}
    assert flow_skew.premium_by_strike(chain) == {}


def test_premium_by_strike_defensive():
    assert flow_skew.premium_by_strike(None) is None
    assert flow_skew.premium_by_strike({}) is None
    assert flow_skew.premium_by_strike("nope") is None
    assert flow_skew.premium_by_strike({"callExpDateMap": {}, "putExpDateMap": {}}) == {}
    # A malformed map must degrade, not raise.
    assert flow_skew.premium_by_strike(
        {"callExpDateMap": {"e": "notamap"}, "putExpDateMap": None}) == {}


# ── non-finite and sentinel inputs (2026-08-20, NaN sweep round 3) ──────────

def test_as_float_rejects_non_finite():
    """`_as_float` is the parsing guard ("float or None"); NaN/inf are not usable
    numbers. A NaN delta used to seed `best_dist = nan` in _pick_nearest_delta,
    and every later `dist < nan` comparison is False -- so the NaN contract's IV
    won permanently. Mirrors the guard completed in the sentiment scorers today."""
    from math import inf, nan

    from flow_skew import _as_float
    assert _as_float(nan) is None
    assert _as_float(inf) is None and _as_float(-inf) is None
    assert _as_float("3.5") == 3.5 and _as_float(None) is None


def test_pick_nearest_delta_skips_a_nan_delta_contract():
    from math import nan

    from flow_skew import _pick_nearest_delta
    smap = {"100.0": [{"delta": nan, "volatility": 55.0}],
            "101.0": [{"delta": -0.25, "volatility": 22.0}]}
    assert _pick_nearest_delta(smap, -0.25) == 22.0


def test_pick_nearest_delta_rejects_schwabs_minus_999_iv_sentinel():
    """Schwab publishes volatility = -999 for an uncomputable IV. An IV must be
    positive to be usable; the sentinel contract must not win on delta proximity."""
    from flow_skew import _pick_nearest_delta
    smap = {"100.0": [{"delta": -0.25, "volatility": -999.0}],
            "101.0": [{"delta": -0.30, "volatility": 22.0}]}
    assert _pick_nearest_delta(smap, -0.25) == 22.0


def test_pick_nearest_delta_none_when_no_usable_contract():
    from math import nan

    from flow_skew import _pick_nearest_delta
    assert _pick_nearest_delta({"100.0": [{"delta": nan, "volatility": -999.0}]},
                               -0.25) is None
