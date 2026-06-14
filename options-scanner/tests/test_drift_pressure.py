"""Tests for Drift Pressure classifier helpers and panel formatter."""
import pytest

from gamma_tool import (
    _classify_pair_state,
    _compute_drift_confidence,
    _classify_pair_regime,
)


# ── _classify_pair_state ──

def test_pair_state_agree_up_vanna_neg_vix_down_post_call_flip():
    # Vanna short + VIX crush + spot above charm flip = bullish drift
    state = _classify_pair_state(
        net_vanna=-800e6, net_charm=200e6, vix_delta=-0.83,
        spot=5847.0, charm_flip=5840.0,
    )
    assert state == "AGREE_UP"


def test_pair_state_agree_up_vanna_pos_vix_up_post_call_flip():
    state = _classify_pair_state(
        net_vanna=800e6, net_charm=200e6, vix_delta=0.83,
        spot=5847.0, charm_flip=5840.0,
    )
    assert state == "AGREE_UP"


def test_pair_state_agree_down_vanna_pos_vix_up_below_flip():
    state = _classify_pair_state(
        net_vanna=800e6, net_charm=-200e6, vix_delta=0.83,
        spot=5830.0, charm_flip=5840.0,
    )
    assert state == "AGREE_DOWN"


def test_pair_state_conflict_mixed_signs():
    # Vanna says up, charm says down
    state = _classify_pair_state(
        net_vanna=-800e6, net_charm=-200e6, vix_delta=-0.83,
        spot=5830.0, charm_flip=5840.0,
    )
    assert state == "CONFLICT"


def test_pair_state_flat_when_both_small():
    state = _classify_pair_state(
        net_vanna=1e3, net_charm=1e3, vix_delta=0.01,
        spot=5847.0, charm_flip=5840.0,
    )
    assert state == "FLAT"


def test_pair_state_vix_delta_none_falls_back_to_agree_or_conflict():
    # Without VIX direction, just check sign-pair of vanna and charm
    same_sign = _classify_pair_state(
        net_vanna=800e6, net_charm=200e6, vix_delta=None,
        spot=5847.0, charm_flip=5840.0,
    )
    assert same_sign == "AGREE"

    opposite = _classify_pair_state(
        net_vanna=800e6, net_charm=-200e6, vix_delta=None,
        spot=5847.0, charm_flip=5840.0,
    )
    assert opposite == "CONFLICT"


def test_pair_state_charm_flip_none_uses_charm_sign():
    # When charm flip is None (no zero-cross), fall back to net_charm sign
    state = _classify_pair_state(
        net_vanna=-800e6, net_charm=200e6, vix_delta=-0.83,
        spot=5847.0, charm_flip=None,
    )
    # net_charm > 0 → post-call-flip behavior assumed → AGREE_UP
    assert state == "AGREE_UP"


# ── _compute_drift_confidence ──

def test_confidence_floor_at_0_20_for_conflict():
    c = _compute_drift_confidence(
        state="CONFLICT", net_vanna=-800e6, net_charm=200e6, vix_delta=0.0,
        spot=5847.0, charm_flip=None, expected_move=10.0,
        dte=2, hours_to_close=6.0, top5_oi=50,
        vix_open=15.0,
    )
    assert c == pytest.approx(0.20, abs=0.01)


def test_confidence_high_when_agree_plus_strong_vol_plus_far_from_flip():
    c = _compute_drift_confidence(
        state="AGREE_UP", net_vanna=-800e6, net_charm=200e6, vix_delta=-1.5,
        spot=5847.0, charm_flip=5840.0, expected_move=10.0,
        dte=0, hours_to_close=4.0, top5_oi=20000,
        vix_open=15.0,
    )
    # base 0.20 + AGREE 0.40 + vol_weight ~0.10 + flip ~0.07 + OI 0.10 = ~0.87
    assert c > 0.65


def test_confidence_final_hour_derate():
    base = _compute_drift_confidence(
        state="AGREE_UP", net_vanna=-800e6, net_charm=200e6, vix_delta=-0.5,
        spot=5847.0, charm_flip=5840.0, expected_move=10.0,
        dte=0, hours_to_close=2.0, top5_oi=20000,
        vix_open=15.0,
    )
    derated = _compute_drift_confidence(
        state="AGREE_UP", net_vanna=-800e6, net_charm=200e6, vix_delta=-0.5,
        spot=5847.0, charm_flip=5840.0, expected_move=10.0,
        dte=0, hours_to_close=0.5, top5_oi=20000,
        vix_open=15.0,
    )
    assert derated == pytest.approx(base - 0.10, abs=0.01)


def test_confidence_thin_chain_drops_oi_weight():
    c = _compute_drift_confidence(
        state="AGREE_UP", net_vanna=-800e6, net_charm=200e6, vix_delta=-0.5,
        spot=5847.0, charm_flip=5840.0, expected_move=10.0,
        dte=2, hours_to_close=6.0, top5_oi=50,  # below 200 threshold
        vix_open=15.0,
    )
    thick = _compute_drift_confidence(
        state="AGREE_UP", net_vanna=-800e6, net_charm=200e6, vix_delta=-0.5,
        spot=5847.0, charm_flip=5840.0, expected_move=10.0,
        dte=2, hours_to_close=6.0, top5_oi=500,
        vix_open=15.0,
    )
    assert thick == pytest.approx(c + 0.10, abs=0.01)


def test_confidence_vix_delta_none_zero_vol_weight():
    c = _compute_drift_confidence(
        state="AGREE", net_vanna=-800e6, net_charm=200e6, vix_delta=None,
        spot=5847.0, charm_flip=5840.0, expected_move=10.0,
        dte=2, hours_to_close=6.0, top5_oi=20000,
    )
    # base 0.20 + AGREE 0.40 + vol 0 + flip ~0.07 + OI 0.10 = ~0.77
    # (Treats unknown VIX as no vol contribution.)
    assert c < 0.85
    assert c > 0.55


def test_confidence_clamped_to_0_1():
    # Implausibly large vol move shouldn't drive confidence > 1.0
    c = _compute_drift_confidence(
        state="AGREE_UP", net_vanna=-800e6, net_charm=200e6, vix_delta=-100.0,
        spot=5847.0, charm_flip=5840.0, expected_move=10.0,
        dte=2, hours_to_close=6.0, top5_oi=20000,
        vix_open=15.0,
    )
    assert 0.0 <= c <= 1.0


# ── _classify_pair_regime ──

def test_regime_vanna_dominant():
    # vanna_impact = |−800M * −1.5| = 1.2B
    # charm_impact = |200M * 6/24|   = 50M
    r = _classify_pair_regime(
        net_vanna=-800e6, vix_delta=-1.5,
        net_charm=200e6, hours_to_close=6.0,
    )
    assert r == "VANNA_DOMINANT"


def test_regime_charm_dominant():
    r = _classify_pair_regime(
        net_vanna=-50e6, vix_delta=-0.1,
        net_charm=500e6, hours_to_close=6.0,
    )
    assert r == "CHARM_DOMINANT"


def test_regime_balanced():
    # Roughly equal impacts → BALANCED
    r = _classify_pair_regime(
        net_vanna=-100e6, vix_delta=-1.0,
        net_charm=400e6, hours_to_close=6.0,
    )
    assert r == "BALANCED"


def test_regime_vix_delta_none_returns_charm_dominant():
    # Without VIX move, vanna impact is unknown → charm wins by default.
    r = _classify_pair_regime(
        net_vanna=-800e6, vix_delta=None,
        net_charm=200e6, hours_to_close=6.0,
    )
    assert r == "CHARM_DOMINANT"


def test_regime_handles_zero_charm():
    r = _classify_pair_regime(
        net_vanna=-800e6, vix_delta=-1.5,
        net_charm=0.0, hours_to_close=6.0,
    )
    assert r == "VANNA_DOMINANT"


def test_regime_handles_all_zeros():
    # All-zero state — return BALANCED rather than crash.
    r = _classify_pair_regime(
        net_vanna=0.0, vix_delta=0.0,
        net_charm=0.0, hours_to_close=6.0,
    )
    assert r == "BALANCED"


# ── format_drift_pressure_panel integration ──

def _make_vanna_data(net=-800e6, atm_strike=5847.0):
    return {
        "spot": 5847.0,
        "gex": {
            atm_strike - 5.0: {"call": net * 0.3, "put": net * 0.2,
                               "net": net * 0.5},
            atm_strike:       {"call": net * 0.2, "put": net * 0.3,
                               "net": net * 0.5},
        },
        "strike_count": 2,
    }


def _make_charm_data(net=200e6, atm_strike=5847.0):
    return {
        "spot": 5847.0,
        "gex": {
            atm_strike - 5.0: {"call": net * 0.4, "put": net * 0.1,
                               "net": net * 0.5},
            atm_strike + 5.0: {"call": net * 0.4, "put": net * 0.1,
                               "net": net * 0.5},
        },
        "strike_count": 2,
    }


def test_format_drift_panel_full_shape():
    from gamma_tool import format_drift_pressure_panel
    result = format_drift_pressure_panel(
        vanna_data=_make_vanna_data(),
        charm_data=_make_charm_data(),
        vix_now=14.21, vix_open=15.04,
        spot=5847.0, dte=2, expected_move=10.0,
        hours_to_close=6.0, top5_oi=15000,
        charm_flip=5840.0,
    )
    expected_keys = {
        "net_vanna", "net_charm", "vix_now", "vix_delta",
        "charm_flip", "pair_state", "confidence",
        "confidence_band", "regime", "regime_note",
    }
    assert set(result.keys()) == expected_keys
    assert result["net_vanna"] == pytest.approx(-800e6, rel=1e-6)
    assert result["net_charm"] == pytest.approx(200e6, rel=1e-6)
    assert result["vix_now"] == 14.21
    assert result["vix_delta"] == pytest.approx(-0.83, abs=1e-6)
    assert result["pair_state"] == "AGREE_UP"
    assert result["confidence_band"] in ("LOW", "MED", "HIGH")
    assert result["regime"] in ("VANNA_DOMINANT", "CHARM_DOMINANT", "BALANCED")


def test_format_drift_panel_handles_missing_vix():
    from gamma_tool import format_drift_pressure_panel
    result = format_drift_pressure_panel(
        vanna_data=_make_vanna_data(),
        charm_data=_make_charm_data(),
        vix_now=None, vix_open=None,
        spot=5847.0, dte=2, expected_move=10.0,
        hours_to_close=6.0, top5_oi=15000,
        charm_flip=5840.0,
    )
    assert result["vix_now"] is None
    assert result["vix_delta"] is None
    assert result["pair_state"] in ("AGREE", "CONFLICT", "FLAT")
    assert 0.0 <= result["confidence"] <= 1.0


def test_format_drift_panel_handles_none_vanna_data():
    from gamma_tool import format_drift_pressure_panel
    result = format_drift_pressure_panel(
        vanna_data=None, charm_data=_make_charm_data(),
        vix_now=14.21, vix_open=15.04,
        spot=5847.0, dte=2, expected_move=10.0,
        hours_to_close=6.0, top5_oi=15000,
        charm_flip=5840.0,
    )
    assert result["net_vanna"] == 0.0
    assert result["pair_state"] in ("FLAT", "CONFLICT", "AGREE_DOWN", "AGREE_UP")
    assert result["confidence_band"] == "LOW"


def test_format_drift_panel_band_mapping():
    """Confidence band boundaries: <0.35=LOW, 0.35-0.65=MED, >0.65=HIGH."""
    from gamma_tool import format_drift_pressure_panel
    # Force HIGH: AGREE_UP + strong VIX + far from flip + thick OI
    high = format_drift_pressure_panel(
        vanna_data=_make_vanna_data(net=-800e6),
        charm_data=_make_charm_data(net=200e6),
        vix_now=13.0, vix_open=14.5,
        spot=5860.0, dte=0, expected_move=10.0,
        hours_to_close=4.0, top5_oi=30000,
        charm_flip=5840.0,
    )
    assert high["confidence_band"] == "HIGH"

    # Force LOW: tiny inputs
    low = format_drift_pressure_panel(
        vanna_data=_make_vanna_data(net=1000),
        charm_data=_make_charm_data(net=1000),
        vix_now=14.5, vix_open=14.6,
        spot=5847.0, dte=2, expected_move=10.0,
        hours_to_close=6.0, top5_oi=100,
        charm_flip=5840.0,
    )
    assert low["confidence_band"] == "LOW"
