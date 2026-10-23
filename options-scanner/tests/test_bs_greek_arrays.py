"""bs_greek_arrays: gamma, charm, vanna and call delta over arrays of strikes AND
volatilities at once (the heatmap's forward band prices each contract at its
own volatility). Every value is held to the scalar function of the same name."""
import numpy as np
import pytest

from options_calculator import bs_charm, bs_delta, bs_gamma, bs_greek_arrays, bs_vanna

R = 0.045
STRIKES = [90.0, 95.0, 100.0, 105.0, 110.0]


@pytest.mark.parametrize("T", [1e-6, 1e-4, 0.01, 0.1])
def test_each_value_matches_the_scalar_function(T):
    sigmas = [0.1, 0.25, 0.4, 0.15, 0.6]
    out = bs_greek_arrays(100.0, np.array(STRIKES), T, R, np.array(sigmas))
    for i, (k, s) in enumerate(zip(STRIKES, sigmas)):
        assert out["gamma"][i] == pytest.approx(bs_gamma(100.0, k, T, R, s), rel=1e-9, abs=1e-300)
        assert out["charm"][i] == pytest.approx(bs_charm(100.0, k, T, R, s, "call"), rel=1e-9, abs=1e-300)
        assert out["vanna"][i] == pytest.approx(bs_vanna(100.0, k, T, R, s), rel=1e-9, abs=1e-300)
        assert out["delta_call"][i] == pytest.approx(bs_delta(100.0, k, T, R, s, "call"), rel=1e-9)


def test_a_column_of_contracts_against_a_row_of_times():
    """The forward band's shape: contracts down, marks across."""
    sigmas = np.array([[0.2], [0.3], [0.0]])
    ks = np.array([[95.0], [100.0], [105.0]])
    ts = np.array([[0.01, 0.001, 1e-6]])
    out = bs_greek_arrays(100.0, ks, ts, R, sigmas)
    assert out["gamma"].shape == (3, 3)
    for i in range(3):
        for j in range(3):
            k, s, t = ks[i, 0], sigmas[i, 0], ts[0, j]
            assert out["gamma"][i, j] == pytest.approx(bs_gamma(100.0, k, t, R, s), rel=1e-9, abs=1e-300)
            assert out["charm"][i, j] == pytest.approx(bs_charm(100.0, k, t, R, s, "call"), rel=1e-9, abs=1e-300)


def test_no_volatility_is_zero_and_does_not_poison_its_neighbours():
    out = bs_greek_arrays(100.0, np.array([95.0, 100.0, 105.0]), 0.01, R,
                          np.array([0.2, 0.0, -0.1]))
    for key in ("gamma", "charm", "vanna"):
        assert out[key][1] == 0.0 and out[key][2] == 0.0
        assert np.isfinite(out[key]).all()
    assert out["gamma"][0] == pytest.approx(bs_gamma(100.0, 95.0, 0.01, R, 0.2), rel=1e-9)


def test_no_time_left_is_zero():
    out = bs_greek_arrays(100.0, np.array([95.0, 100.0]), 0.0, R, np.array([0.2, 0.2]))
    assert out["gamma"].tolist() == [0.0, 0.0]
    assert out["charm"].tolist() == [0.0, 0.0]
    assert out["vanna"].tolist() == [0.0, 0.0]
