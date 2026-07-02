"""Regression: term-structure GEX must use the SAME per-1% unit as the
intraday GEX path.

The intraday path (`calc_from_chain`) computes dollar-gamma per 1% spot move:
    val = gamma * OI * 100 * spot^2 * 0.01
(SqueezeMetrics / SpotGamma convention).

The term-structure path (`compute_term_grid`) previously dropped the `* 0.01`
factor, making its cells 100x too large (per-$1^2 instead of per-1%). This
test pins that both paths agree for a shared contract.
"""
import pytest
import gamma_tool
from gamma_tool import GammaEngine


def _shared_chain():
    """A chain whose NEAREST expiration (used by the intraday GEX path) is the
    same expiration the term grid reports first, so a given strike's call/put
    contracts are identical across both computations."""
    exp = "2999-12-31:100"  # far future so it's always the nearest & only exp
    call_map = {
        exp: {
            "7135.0": [{"strike": 7135.0, "openInterest": 5000,
                        "gamma": 0.005, "volatility": 18, "delta": 0.5,
                        "totalVolume": 10}],
        }
    }
    put_map = {
        exp: {
            "7135.0": [{"strike": 7135.0, "openInterest": 4500,
                        "gamma": 0.005, "volatility": 18, "delta": -0.5,
                        "totalVolume": 10}],
        }
    }
    return {
        "underlyingPrice": 7135.9,
        "underlying": {"last": 7135.9},
        "callExpDateMap": call_map,
        "putExpDateMap": put_map,
    }


def test_term_and_intraday_gex_same_scale_for_shared_contract():
    chain = _shared_chain()
    eng = GammaEngine()

    intraday = eng.calc_from_chain(chain)
    grid = eng.compute_term_grid(chain, top_n=5)

    strike = 7135.0
    intraday_cell = intraday["gex"][strike]
    term_cell = grid["cells"]["2999-12-31"][strike]

    # The intraday path stores put as a NEGATIVE contribution; the term path
    # stores call_gex/put_gex as positive magnitudes with net = call - put.
    # Both use net = call + (put-with-sign) = call - |put|.
    assert intraday_cell["call"] > 0
    assert intraday_cell["put"] < 0

    # Same scale: term call magnitude == intraday call magnitude (the two paths
    # multiply the same factors in a slightly different order, so compare with a
    # relative tolerance rather than bit-exact equality).
    assert term_cell["call_gex_usd"] == pytest.approx(intraday_cell["call"], rel=1e-12)
    # Intraday stores put negative; term stores put positive magnitude.
    assert term_cell["put_gex_usd"] == pytest.approx(-intraday_cell["put"], rel=1e-12)
    # Net matches too.
    assert term_cell["net_gex_usd"] == pytest.approx(intraday_cell["net"], rel=1e-12)


def test_term_gex_uses_per_one_percent_factor():
    """Explicit magnitude check: call_gex == gamma*OI*100*S^2*0.01."""
    chain = _shared_chain()
    eng = GammaEngine()
    grid = eng.compute_term_grid(chain, top_n=5)

    S = 7135.9
    expected_call = 0.005 * 5000 * 100 * (S ** 2) * 0.01
    expected_put = 0.005 * 4500 * 100 * (S ** 2) * 0.01

    cell = grid["cells"]["2999-12-31"][7135.0]
    assert cell["call_gex_usd"] == pytest.approx(expected_call, rel=1e-12)
    assert cell["put_gex_usd"] == pytest.approx(expected_put, rel=1e-12)
    assert cell["net_gex_usd"] == pytest.approx(expected_call - expected_put, rel=1e-12)
