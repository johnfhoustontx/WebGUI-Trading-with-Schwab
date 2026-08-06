"""Tests for gamma_tool DEX computation and wall extraction."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from gamma_tool import calc_dex_from_chain, get_dex_walls


def _mock_chain(spot=5300.0):
    """Chain with 3 call strikes and 3 put strikes, varying delta x OI."""
    today_key = "2026-04-19:0"
    return {
        "underlyingPrice": spot,
        "callExpDateMap": {
            today_key: {
                "5320.0": [{"delta": 0.30, "openInterest": 1000, "totalVolume": 500}],
                "5300.0": [{"delta": 0.50, "openInterest": 2000, "totalVolume": 800}],
                "5280.0": [{"delta": 0.70, "openInterest": 500,  "totalVolume": 200}],
            }
        },
        "putExpDateMap": {
            today_key: {
                "5320.0": [{"delta": -0.70, "openInterest": 500,  "totalVolume": 100}],
                "5300.0": [{"delta": -0.50, "openInterest": 1500, "totalVolume": 600}],
                "5280.0": [{"delta": -0.30, "openInterest": 800,  "totalVolume": 300}],
            }
        },
    }


class TestCalcDexFromChain:
    def test_returns_expected_structure(self):
        result = calc_dex_from_chain(_mock_chain())
        assert "spot" in result
        assert "dex" in result
        assert "strike_count" in result
        assert result["spot"] == 5300.0

    def test_dex_formula_calls_positive(self):
        """Call DEX = delta * OI * 100 * spot (positive)."""
        result = calc_dex_from_chain(_mock_chain())
        assert result["dex"][5300.0]["call"] == 0.5 * 2000 * 100 * 5300

    def test_dex_formula_puts_negative(self):
        """Put DEX = delta * OI * 100 * spot; delta is negative."""
        result = calc_dex_from_chain(_mock_chain())
        assert result["dex"][5300.0]["put"] == -0.5 * 1500 * 100 * 5300

    def test_net_is_sum_of_call_and_put(self):
        result = calc_dex_from_chain(_mock_chain())
        entry = result["dex"][5300.0]
        assert entry["net"] == entry["call"] + entry["put"]

    def test_returns_none_when_chain_empty(self):
        assert calc_dex_from_chain(None) is None
        assert calc_dex_from_chain({"underlyingPrice": 0}) is None


class TestGetDexWalls:
    def test_returns_top_n_by_absolute_net(self):
        dex_data = {
            "spot": 5300.0,
            "dex": {
                5300.0: {"call": 1e9, "put": -5e8, "net": 5e8},
                5310.0: {"call": 2e9, "put": -1e9, "net": 1e9},
                5290.0: {"call": 1e8, "put": -2e8, "net": -1e8},
            },
        }
        walls = get_dex_walls(dex_data, top_n=2)
        assert walls == [5310.0, 5300.0]

    def test_empty_dex_returns_empty_list(self):
        assert get_dex_walls({"dex": {}}) == []

    def test_none_returns_empty_list(self):
        assert get_dex_walls(None) == []

    def test_missing_dex_key_returns_empty_list(self):
        assert get_dex_walls({"spot": 5300}) == []


# --- Per-strike 0-DTE charm drift + the projected flip built on it ---

def _drift_contracts():
    # Two 0-DTE strikes; charm pushes the call's delta up and the put's toward 0.
    return [
        ({"delta": 0.40, "charm": 8760.0, "openInterest": 10}, "call", 100.0),
        ({"delta": -0.30, "charm": 8760.0, "openInterest": 20}, "put", 90.0),
    ]


def test_drift_by_strike_totals_match_hedge_pressure():
    """The per-strike drift is a REDISTRIBUTION of hedge_pressure, not a new number:
    its sum must equal what project_0dte_pressure reports."""
    import gamma_tool as gt
    spot, hours = 100.0, 8760.0 / 8760.0 * 24.0   # 24h -> dt_years = 1/365
    trips = _drift_contracts()
    by_strike = gt.project_0dte_drift_by_strike(trips, spot, hours)
    _, _, pressure = gt.project_0dte_pressure([(c, t) for c, t, _ in trips], spot, hours)
    assert set(by_strike) == {100.0, 90.0}
    assert by_strike[100.0] + by_strike[90.0] == pytest.approx(pressure, rel=1e-9)


def test_drift_by_strike_is_defensive():
    import gamma_tool as gt
    assert gt.project_0dte_drift_by_strike([], 100.0, 5.0) == {}
    assert gt.project_0dte_drift_by_strike(None, 100.0, 5.0) == {}
    # oi<=0 / delta==0 contribute nothing (mirrors project_0dte_pressure).
    assert gt.project_0dte_drift_by_strike(
        [({"delta": 0.0, "charm": 1.0, "openInterest": 5}, "call", 100.0)], 100.0, 5.0) == {}


def test_projected_flip_uses_per_strike_drift_not_a_flat_average():
    """REGRESSION: the old code added hedge/n_strikes to EVERY strike. On real $SPX
    data that erased 56 of 57 negative strikes and threw the crossing ~1800 points
    past spot. The drift must land on the strikes it actually belongs to."""
    import gamma_tool as gt
    # Curve crosses zero between 100 and 110. Drift is concentrated at 110 only,
    # which pushes the crossing UP toward 110 — a flat average would also lift 90,
    # 80 … and move the crossing somewhere unrelated.
    grid = {80.0: {"net": -100.0}, 90.0: {"net": -60.0},
            100.0: {"net": -20.0}, 110.0: {"net": 40.0}, 120.0: {"net": 90.0}}
    data = {"gex": grid, "hedge_pressure": 30.0,
            "hedge_drift_by_strike": {100.0: 30.0}}
    pf = gt.compute_projected_flip(data, spot=105.0)
    # 100 goes -20 -> +10, so the crossing moves BELOW 100 (between 90 and 100).
    assert 90.0 < pf < 100.0


def test_projected_flip_none_without_per_strike_drift():
    """No 0-DTE book -> no projection. Returning None beats returning the old
    flat-average guess, which was silently wrong."""
    import gamma_tool as gt
    grid = {100.0: {"net": -20.0}, 110.0: {"net": 40.0}}
    assert gt.compute_projected_flip({"gex": grid, "hedge_pressure": 30.0}, 105.0) is None
    assert gt.compute_projected_flip({"gex": grid, "hedge_drift_by_strike": {}}, 105.0) is None
    assert gt.compute_projected_flip({}, 105.0) is None
