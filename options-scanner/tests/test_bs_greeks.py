import numpy as np
import pytest
from options_calculator import bs_price, bs_theta, bs_vega, bs_rho, bs_greeks


# Reference values computed against scipy's analytical Black-Scholes
# using the formulas implemented in options_calculator.py.
# S=100, K=100, T=30/365, r=0.04, sigma=0.20, ATM
ATM_CALL = dict(S=100.0, K=100.0, T=30/365, r=0.04, sigma=0.20, option_type="call")
ATM_PUT  = dict(S=100.0, K=100.0, T=30/365, r=0.04, sigma=0.20, option_type="put")


def test_bs_price_atm_call_matches_reference():
    p = bs_price(**ATM_CALL)
    assert p == pytest.approx(2.45126, abs=1e-3)


def test_bs_price_atm_put_matches_reference():
    p = bs_price(**ATM_PUT)
    assert p == pytest.approx(2.12303, abs=1e-3)


def test_bs_price_expired_call_intrinsic():
    p = bs_price(S=105.0, K=100.0, T=0.0, r=0.04, sigma=0.20, option_type="call")
    assert p == pytest.approx(5.0)


def test_bs_price_expired_put_intrinsic():
    p = bs_price(S=95.0, K=100.0, T=0.0, r=0.04, sigma=0.20, option_type="put")
    assert p == pytest.approx(5.0)


def test_bs_theta_per_day_convention_negative_for_long_call():
    th = bs_theta(**ATM_CALL)
    assert th < 0
    assert th == pytest.approx(-0.04357, abs=1e-3)


def test_bs_vega_per_one_vol_point():
    v = bs_vega(**ATM_CALL)
    assert v == pytest.approx(0.11395, abs=1e-3)


def test_bs_rho_per_one_pct_rate():
    rh = bs_rho(**ATM_CALL)
    assert rh == pytest.approx(0.04190, abs=1e-3)


def test_bs_greeks_returns_full_dict():
    g = bs_greeks(**ATM_CALL)
    assert set(g.keys()) == {"price", "delta", "gamma", "theta", "vega", "rho", "vanna"}
    assert g["delta"] == pytest.approx(0.534, abs=0.02)


def test_bs_greeks_expired_otm_returns_intrinsic_and_zero_greeks():
    # OTM expired call: intrinsic 0, all greeks 0.
    g = bs_greeks(S=95.0, K=100.0, T=0.0, r=0.04, sigma=0.20, option_type="call")
    assert g["price"] == pytest.approx(0.0)
    assert g["delta"] == 0
    assert g["gamma"] == 0
    assert g["theta"] == 0
    assert g["vega"] == 0
    assert g["rho"] == 0
    assert g["vanna"] == 0


def test_bs_greeks_vectorized_over_strikes():
    strikes = np.array([95.0, 100.0, 105.0])
    g = bs_greeks(S=100.0, K=strikes, T=30/365, r=0.04, sigma=0.20, option_type="call")
    assert g["price"].shape == (3,)
    assert g["delta"].shape == (3,)
    assert g["delta"][0] > g["delta"][1] > g["delta"][2]


def test_existing_bs_delta_still_works():
    from options_calculator import bs_delta
    d = bs_delta(S=100.0, K=100.0, T=30/365, r=0.04, sigma=0.20, option_type="call")
    assert d == pytest.approx(0.534, abs=0.02)


def test_bs_greeks_zero_sigma_returns_intrinsic_and_zero_greeks():
    """Schwab can briefly report volatility=0; must not produce nan/inf."""
    g = bs_greeks(S=105.0, K=100.0, T=30/365, r=0.04, sigma=0.0, option_type="call")
    assert g["price"] == pytest.approx(5.0)
    assert g["delta"] == pytest.approx(1.0)   # ITM call with sigma=0 → behaves like stock
    assert g["gamma"] == 0
    assert g["theta"] == 0
    assert g["vega"] == 0
    assert g["rho"] == 0
    assert g["vanna"] == 0
    # Also OTM:
    g2 = bs_greeks(S=95.0, K=100.0, T=30/365, r=0.04, sigma=0.0, option_type="call")
    assert g2["price"] == 0
    assert g2["delta"] == 0
    assert g2["vanna"] == 0


# --- charm (delta decay) -----------------------------------------------------
# Added 2026-08-20: bs_charm had never been pinned, and the put branch carried a
# spurious `+ r*exp(-r*T)` term (a Black-model / forward-delta contamination).

def _fd_delta_decay(S, K, T, r, sigma, option_type, h=1e-6):
    """Numerical d(delta)/d(time passing) = -d(delta)/d(T)."""
    from options_calculator import bs_delta
    up = bs_delta(S, K, T + h, r, sigma, option_type)
    dn = bs_delta(S, K, T - h, r, sigma, option_type)
    return -(up - dn) / (2 * h)


@pytest.mark.parametrize("K", [90.0, 100.0, 110.0])
def test_put_charm_equals_call_charm_without_dividends(K):
    """With q=0, put delta = call delta - 1. The -1 is a CONSTANT, so its time
    derivative is zero and put charm must equal call charm exactly. The old put
    branch added r*exp(-r*T) (= +0.0449 here), tinting every put strike in the
    Charm heatmap positive in proportion to its open interest."""
    from options_calculator import bs_charm
    args = (100.0, K, 30 / 365, 0.045, 0.20)
    assert bs_charm(*args, "put") == pytest.approx(bs_charm(*args, "call"), abs=1e-12)


@pytest.mark.parametrize("option_type", ["call", "put"])
@pytest.mark.parametrize("K", [90.0, 100.0, 110.0])
def test_charm_matches_finite_difference_delta_decay(option_type, K):
    """Charm is d(delta)/d(time passing); pin it against a numerical derivative
    of bs_delta so neither branch can drift from the definition."""
    from options_calculator import bs_charm
    args = (100.0, K, 30 / 365, 0.045, 0.20)
    assert bs_charm(*args, option_type) == pytest.approx(
        _fd_delta_decay(*args, option_type), abs=1e-4)
