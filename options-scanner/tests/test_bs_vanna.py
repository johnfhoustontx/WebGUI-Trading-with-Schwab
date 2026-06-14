"""Tests for bs_vanna (scalar and vectorized) in options_calculator.py."""
import math
import numpy as np
import pytest

from options_calculator import bs_vanna, bs_vanna_v, bs_greeks


def test_vanna_atm_short_dated_slightly_negative():
    # ATM short-dated call: S=K=500, T=2/252, sigma=0.20, r=0.045
    # With positive r, d2 is slightly positive at ATM, so vanna = -phi(d1)*d2/sigma
    # is slightly negative. Per 1 vol-point convention: ~-0.000222.
    v = bs_vanna(500.0, 500.0, 2 / 252, 0.045, 0.20, "call")
    assert v == pytest.approx(-0.000222, abs=5e-5)


def test_vanna_call_put_symmetric():
    # Vanna is the same for calls and puts (put-call parity in delta-sigma space).
    args = (500.0, 510.0, 30 / 252, 0.045, 0.22)
    assert bs_vanna(*args, "call") == pytest.approx(bs_vanna(*args, "put"), abs=1e-9)


def test_vanna_zero_at_expiration():
    assert bs_vanna(500.0, 500.0, 0.0, 0.045, 0.20, "call") == 0.0
    assert bs_vanna(500.0, 500.0, -0.1, 0.045, 0.20, "put") == 0.0


def test_vanna_zero_when_sigma_nonpositive():
    assert bs_vanna(500.0, 500.0, 0.05, 0.045, 0.0, "call") == 0.0
    assert bs_vanna(500.0, 500.0, 0.05, 0.045, -0.1, "put") == 0.0


def test_vanna_deep_otm_call_near_zero():
    # Deep OTM: small but non-zero
    v = bs_vanna(100.0, 200.0, 30 / 252, 0.045, 0.20, "call")
    assert abs(v) < 0.005


def test_vanna_vectorized_matches_scalar():
    strikes = np.array([490.0, 495.0, 500.0, 505.0, 510.0])
    out = bs_vanna_v(500.0, strikes, 30 / 252, 0.045, 0.20, "call")
    expected = np.array([
        bs_vanna(500.0, float(k), 30 / 252, 0.045, 0.20, "call") for k in strikes
    ])
    np.testing.assert_allclose(out, expected, atol=1e-9)


def test_vanna_vectorized_zero_at_expiration():
    strikes = np.array([490.0, 500.0, 510.0])
    out = bs_vanna_v(500.0, strikes, 0.0, 0.045, 0.20, "call")
    np.testing.assert_array_equal(out, np.zeros(3))


def test_bs_greeks_includes_vanna():
    g = bs_greeks(500.0, 500.0, 30 / 252, 0.045, 0.20, "call")
    assert "vanna" in g
    assert g["vanna"] == pytest.approx(
        bs_vanna_v(500.0, 500.0, 30 / 252, 0.045, 0.20, "call"), abs=1e-9
    )
