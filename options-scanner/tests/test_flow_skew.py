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
