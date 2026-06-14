"""Tests for gamma_tool DEX computation and wall extraction."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
