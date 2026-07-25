# tests/test_heatmap.py
import math

import pytest

from options_calculator import bs_gamma


def test_bs_gamma_atm_matches_reference():
    """ATM call, 30-day, 20% IV, 4.5% rate — reference value."""
    S, K, T, r, sigma = 100.0, 100.0, 30 / 365.0, 0.045, 0.20
    # Hand-computed reference: gamma = N'(d1) / (S * sigma * sqrt(T))
    # d1 = (ln(100/100) + (0.045 + 0.02)*30/365) / (0.2*sqrt(30/365)) ≈ 0.0932
    # N'(d1) ≈ 0.3972
    # sqrt(30/365) ≈ 0.2867
    # gamma ≈ 0.3972 / (100 * 0.2 * 0.2867) ≈ 0.0693
    g = bs_gamma(S, K, T, r, sigma, "call")
    assert g == pytest.approx(0.0693, rel=0.01)


def test_bs_gamma_call_put_symmetric():
    """Gamma is identical for calls and puts at the same strike."""
    args = (100.0, 105.0, 7 / 365.0, 0.045, 0.22)
    assert bs_gamma(*args, "call") == pytest.approx(bs_gamma(*args, "put"))


def test_bs_gamma_expired_returns_zero():
    assert bs_gamma(100.0, 100.0, 0.0, 0.045, 0.20, "call") == 0.0
    assert bs_gamma(100.0, 100.0, -0.1, 0.045, 0.20, "call") == 0.0


def test_bs_gamma_zero_vol_returns_zero():
    assert bs_gamma(100.0, 100.0, 30 / 365.0, 0.045, 0.0, "call") == 0.0
    assert bs_gamma(100.0, 100.0, 30 / 365.0, 0.045, -0.1, "call") == 0.0


def test_bs_gamma_peaks_at_atm():
    """For a given expiry, gamma peaks at ATM vs ITM/OTM."""
    T, r, sigma = 30 / 365.0, 0.045, 0.20
    atm = bs_gamma(100.0, 100.0, T, r, sigma, "call")
    itm = bs_gamma(100.0,  90.0, T, r, sigma, "call")
    otm = bs_gamma(100.0, 110.0, T, r, sigma, "call")
    assert atm > itm
    assert atm > otm


from gamma_tool import GammaEngine


def test_engine_retains_last_chain():
    """After calc_from_chain succeeds, engine holds a reference to the chain."""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    exp_key = f"{today}:0"
    chain = {
        "underlyingPrice": 5000.0,
        "callExpDateMap": {exp_key: {"5000.0": [
            {"gamma": 0.001, "openInterest": 100, "volatility": 20.0, "daysToExpiration": 0}
        ]}},
        "putExpDateMap": {exp_key: {"5000.0": [
            {"gamma": 0.001, "openInterest": 50, "volatility": 20.0, "daysToExpiration": 0}
        ]}},
    }
    eng = GammaEngine()
    assert eng._last_chain is None
    eng.calc_from_chain(chain)
    assert eng._last_chain is chain


def test_engine_last_chain_not_set_on_empty():
    """Empty/None chain should NOT overwrite a valid _last_chain."""
    eng = GammaEngine()
    eng.calc_from_chain(None)
    assert eng._last_chain is None
    eng.calc_from_chain({})
    assert eng._last_chain is None


def test_engine_last_chain_not_set_on_zero_spot():
    """Chain with non-positive spot should NOT be retained."""
    eng = GammaEngine()
    bad_chain = {"underlyingPrice": 0, "callExpDateMap": {}, "putExpDateMap": {}}
    eng.calc_from_chain(bad_chain)
    assert eng._last_chain is None


def test_engine_last_chain_set_by_charm_and_dex():
    """calc_charm_from_chain and calc_dex_from_chain also populate _last_chain."""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    exp_key = f"{today}:0"
    chain = {
        "underlyingPrice": 5000.0,
        "callExpDateMap": {exp_key: {"5000.0": [
            {"delta": 0.5, "gamma": 0.001, "openInterest": 100,
             "volatility": 20.0, "daysToExpiration": 0}
        ]}},
        "putExpDateMap": {exp_key: {"5000.0": [
            {"delta": -0.5, "gamma": 0.001, "openInterest": 50,
             "volatility": 20.0, "daysToExpiration": 0}
        ]}},
    }

    # Charm path.
    eng1 = GammaEngine()
    eng1.calc_charm_from_chain(chain)
    assert eng1._last_chain is chain

    # DEX path.
    eng2 = GammaEngine()
    eng2.calc_dex_from_chain(chain)
    assert eng2._last_chain is chain


from gamma_tool import iter_contracts


def _chain_fixture():
    """Chain with two strikes, each with call+put, across two expiries."""
    return {
        "underlyingPrice": 5000.0,
        "callExpDateMap": {
            "2026-04-18:0": {
                "4990.0": [{"delta": 0.50, "openInterest": 10, "volatility": 20.0, "daysToExpiration": 0}],
                "5010.0": [{"delta": 0.40, "openInterest": 20, "volatility": 19.0, "daysToExpiration": 0}],
            },
            "2026-04-25:7": {
                "5000.0": [{"delta": 0.52, "openInterest": 5, "volatility": 18.0, "daysToExpiration": 7}],
            },
        },
        "putExpDateMap": {
            "2026-04-18:0": {
                "4990.0": [{"delta": -0.50, "openInterest": 30, "volatility": 20.0, "daysToExpiration": 0}],
                "5010.0": [{"delta": -0.60, "openInterest": 15, "volatility": 19.0, "daysToExpiration": 0}],
            },
        },
    }


def test_iter_contracts_yields_all():
    chain = _chain_fixture()
    results = list(iter_contracts(chain))
    # 3 calls + 2 puts = 5 contracts.
    assert len(results) == 5


def test_iter_contracts_shape():
    """Each yielded tuple is (contract_dict, option_type, strike_float)."""
    chain = _chain_fixture()
    for c, opt_type, strike in iter_contracts(chain):
        assert isinstance(c, dict)
        assert opt_type in ("call", "put")
        assert isinstance(strike, float)


def test_iter_contracts_filter_0dte_only():
    """Optional dte kwarg selects only contracts matching."""
    chain = _chain_fixture()
    zero_dte = list(iter_contracts(chain, dte=0))
    # 2 calls + 2 puts at 0-DTE = 4 contracts.
    assert len(zero_dte) == 4
    seven_dte = list(iter_contracts(chain, dte=7))
    assert len(seven_dte) == 1


def test_iter_contracts_handles_missing_maps():
    """Chain with only callExpDateMap still iterates."""
    chain = {
        "underlyingPrice": 5000.0,
        "callExpDateMap": _chain_fixture()["callExpDateMap"],
    }
    results = list(iter_contracts(chain))
    assert len(results) == 3  # 3 calls, 0 puts


def test_iter_contracts_empty_chain():
    """None or empty dict yields nothing (no crash)."""
    assert list(iter_contracts(None)) == []
    assert list(iter_contracts({})) == []


def test_iter_contracts_malformed_strike_key_skipped():
    """Non-numeric strike keys are silently skipped (defensive)."""
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {
            "2026-04-18:0": {
                "not_a_number": [{"delta": 0.5, "openInterest": 10}],
                "100.0": [{"delta": 0.5, "openInterest": 10}],
            },
        },
        "putExpDateMap": {},
    }
    results = list(iter_contracts(chain))
    assert len(results) == 1  # only the valid "100.0" strike
    assert results[0][2] == 100.0


def test_project_gex_forward_matches_hand_calc():
    """Single-strike synthetic chain -> GEX exposure at given T matches hand computation."""
    from options_calculator import bs_gamma
    from gamma_tool import GammaEngine
    eng = GammaEngine()
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {"2026-04-18:0": {"100.0": [
            {"openInterest": 100, "volatility": 20.0, "daysToExpiration": 0}
        ]}},
        "putExpDateMap": {"2026-04-18:0": {"100.0": [
            {"openInterest": 50, "volatility": 20.0, "daysToExpiration": 0}
        ]}},
    }
    eng.calc_from_chain(chain)
    T_future = 6.0 / (365 * 24)

    result = eng.project_exposure_forward("gex", T_future)
    spot = 100.0
    g = bs_gamma(spot, 100.0, T_future, 0.045, 0.20, "call")
    expected = (100 + 50) * g * 100 * spot * spot * 0.01
    assert 100.0 in result
    assert result[100.0] == pytest.approx(expected, rel=1e-6)


def test_project_dex_forward_signs():
    """DEX: at ATM with equal call+put OI, net delta ~= 0."""
    from gamma_tool import GammaEngine
    eng = GammaEngine()
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {"2026-04-18:0": {"100.0": [
            {"openInterest": 100, "volatility": 20.0, "daysToExpiration": 0}
        ]}},
        "putExpDateMap": {"2026-04-18:0": {"100.0": [
            {"openInterest": 100, "volatility": 20.0, "daysToExpiration": 0}
        ]}},
    }
    eng.calc_from_chain(chain)
    T_future = 6.0 / (365 * 24)
    result = eng.project_exposure_forward("dex", T_future)
    assert result[100.0] == pytest.approx(0.0, abs=5e5)


def test_project_gamma_intensifies_as_t_shrinks():
    """Gamma wall tightens: ATM gamma higher at shorter T."""
    from gamma_tool import GammaEngine
    eng = GammaEngine()
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {"2026-04-18:0": {"100.0": [
            {"openInterest": 100, "volatility": 20.0, "daysToExpiration": 0}
        ]}},
        "putExpDateMap": {},
    }
    eng.calc_from_chain(chain)
    near = eng.project_exposure_forward("gex", 0.5 / (365 * 24))
    far = eng.project_exposure_forward("gex", 6.0 / (365 * 24))
    assert near[100.0] > far[100.0]


def test_project_charm_scales_with_one_over_t():
    """Charm magnitude grows as T shrinks."""
    from gamma_tool import GammaEngine
    eng = GammaEngine()
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {"2026-04-18:0": {"100.0": [
            {"openInterest": 100, "volatility": 20.0, "daysToExpiration": 0}
        ]}},
        "putExpDateMap": {},
    }
    eng.calc_from_chain(chain)
    near = eng.project_exposure_forward("charm", 0.5 / (365 * 24))
    far = eng.project_exposure_forward("charm", 6.0 / (365 * 24))
    assert abs(near[100.0]) > abs(far[100.0])


def test_project_skips_missing_iv():
    """Contracts with iv=0 contribute 0 and don't poison the bucket."""
    import math
    from gamma_tool import GammaEngine
    eng = GammaEngine()
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {"2026-04-18:0": {"100.0": [
            {"openInterest": 100, "volatility": 0.0, "daysToExpiration": 0}
        ]}},
        "putExpDateMap": {"2026-04-18:0": {"100.0": [
            {"openInterest": 100, "volatility": 20.0, "daysToExpiration": 0}
        ]}},
    }
    eng.calc_from_chain(chain)
    result = eng.project_exposure_forward("gex", 6.0 / (365 * 24))
    assert 100.0 in result
    assert not math.isnan(result[100.0])


def test_project_returns_empty_when_no_chain():
    """No _last_chain -> empty dict (not None, not exception)."""
    from gamma_tool import GammaEngine
    eng = GammaEngine()
    result = eng.project_exposure_forward("gex", 0.001)
    assert result == {}


from gamma_tool import build_historical_matrix


def test_build_historical_matrix_shape_and_values():
    """Given 3 rows with known grids, matrix shape (n_strikes, 3) with correct cells."""
    rows = [
        # (ts, spot, flip, top_pos, top_neg, net_total, grid)
        (1_000_000, 100.0, None, None, None, 0.0,
         {99.0: {"call": 1.0, "put": -1.0, "net": 0.0},
          100.0: {"call": 2.0, "put": -1.0, "net": 1.0}}),
        (1_000_300, 100.0, None, None, None, 0.0,
         {100.0: {"call": 3.0, "put": -2.0, "net": 1.0},
          101.0: {"call": 0.5, "put": -0.5, "net": 0.0}}),
        (1_000_600, 100.0, None, None, None, 0.0,
         {99.0: {"call": 1.5, "put": -0.5, "net": 1.0},
          100.0: {"call": 4.0, "put": -1.5, "net": 2.5}}),
    ]
    strikes, times, matrix = build_historical_matrix(rows, current_spot=100.0, display="net")
    assert list(strikes) == [99.0, 100.0, 101.0]
    assert list(times) == [1_000_000, 1_000_300, 1_000_600]
    import numpy as np
    # matrix shape: (3 strikes, 3 times)
    assert matrix.shape == (3, 3)
    # Strike 100 across all 3 times: 1.0, 1.0, 2.5
    np.testing.assert_array_almost_equal(matrix[1, :], [1.0, 1.0, 2.5])
    # Strike 99 only in rows 0 and 2: 0.0, NaN, 1.0
    assert matrix[0, 0] == 0.0
    assert np.isnan(matrix[0, 1])
    assert matrix[0, 2] == 1.0
    # Strike 101 only in row 1.
    assert np.isnan(matrix[2, 0])
    assert matrix[2, 1] == 0.0
    assert np.isnan(matrix[2, 2])


def test_build_historical_matrix_strike_filter_5pct():
    """Strikes beyond ±5% of spot are excluded."""
    rows = [
        (1, 100.0, None, None, None, 0.0,
         {94.0: {"call": 0, "put": 0, "net": 1.0},   # -6%  (excluded)
          96.0: {"call": 0, "put": 0, "net": 1.0},   # -4%  (included)
          100.0: {"call": 0, "put": 0, "net": 1.0},  # ATM
          105.0: {"call": 0, "put": 0, "net": 1.0},  # +5%  (included)
          107.0: {"call": 0, "put": 0, "net": 1.0}}),  # +7% (excluded)
    ]
    strikes, _, _ = build_historical_matrix(rows, current_spot=100.0, display="net")
    assert list(strikes) == [96.0, 100.0, 105.0]


def test_build_historical_matrix_empty_rows():
    strikes, times, matrix = build_historical_matrix([], current_spot=100.0, display="net")
    assert list(strikes) == []
    assert list(times) == []
    assert matrix.shape == (0, 0)


def test_build_historical_matrix_honors_display_param():
    """display='call' selects the 'call' field instead of 'net'."""
    rows = [
        (1, 100.0, None, None, None, 0.0,
         {100.0: {"call": 5.0, "put": -3.0, "net": 2.0}}),
    ]
    import numpy as np
    _, _, matrix_net = build_historical_matrix(rows, current_spot=100.0, display="net")
    _, _, matrix_call = build_historical_matrix(rows, current_spot=100.0, display="call")
    _, _, matrix_put = build_historical_matrix(rows, current_spot=100.0, display="put")
    assert matrix_net[0, 0] == 2.0
    assert matrix_call[0, 0] == 5.0
    assert matrix_put[0, 0] == -3.0


from gamma_tool import find_key_gamma_strike


def test_find_key_gamma_within_1pct_of_spot():
    grid = {
        7000.0: {"net": 5e9},    # ATM, biggest magnitude
        7050.0: {"net": 1e9},    # +0.71% (within 1%)
        7100.0: {"net": 9e9},    # +1.4% (OUTSIDE 1%)
        6950.0: {"net": -3e9},   # -0.71% (within 1%)
    }
    # Only strikes within ±1% are candidates; 7000 has biggest |net|.
    assert find_key_gamma_strike(grid, spot=7000.0) == 7000.0


def test_find_key_gamma_picks_max_abs():
    grid = {
        7000.0: {"net": 1e9},
        7020.0: {"net": -5e9},   # biggest magnitude
        7040.0: {"net": 2e9},
    }
    # All within ±1% of 7030; -5e9 at 7020 has largest |net|.
    assert find_key_gamma_strike(grid, spot=7030.0) == 7020.0


def test_find_key_gamma_empty_grid():
    assert find_key_gamma_strike({}, spot=7000.0) is None


def test_find_key_gamma_all_outside_1pct():
    grid = {
        6900.0: {"net": 5e9},   # -1.4%
        7100.0: {"net": 5e9},   # +1.4%
    }
    assert find_key_gamma_strike(grid, spot=7000.0) is None


def test_fetch_last_close_returns_last_candle_close(monkeypatch):
    """Mock fetch_price_history; verify the last candle's close is returned and cached."""
    from unittest.mock import MagicMock, patch

    class _Stub:
        def __init__(self):
            self._client = MagicMock()
            self._last_close_cache = {}
            self._last_close_attempted = set()

    # _fetch_last_close is a GUI-only helper: it lives on the parked Tk window
    # (gamma_window_legacy), not the headless gamma_tool engine.
    from gamma_window_legacy import GammaWindow
    _Stub._fetch_last_close = GammaWindow._fetch_last_close

    fake_hist = {"candles": [
        {"datetime": 1, "close": 100.0},
        {"datetime": 2, "close": 101.5},
        {"datetime": 3, "close": 102.25},
    ]}
    with patch("scanner_engine.fetch_price_history", return_value=fake_hist):
        stub = _Stub()
        assert stub._fetch_last_close("$SPX") == 102.25
        # Cached: second call returns same value without re-fetching.
        assert stub._fetch_last_close("$SPX") == 102.25


def test_fetch_last_close_empty_candles_returns_none(monkeypatch):
    from unittest.mock import MagicMock, patch

    class _Stub:
        def __init__(self):
            self._client = MagicMock()
            self._last_close_cache = {}
            self._last_close_attempted = set()
    # _fetch_last_close is a GUI-only helper: it lives on the parked Tk window
    # (gamma_window_legacy), not the headless gamma_tool engine.
    from gamma_window_legacy import GammaWindow
    _Stub._fetch_last_close = GammaWindow._fetch_last_close

    with patch("scanner_engine.fetch_price_history", return_value={"candles": []}):
        stub = _Stub()
        assert stub._fetch_last_close("$SPX") is None


def test_fetch_last_close_failure_cached_as_none(monkeypatch):
    from unittest.mock import MagicMock, patch

    class _Stub:
        def __init__(self):
            self._client = MagicMock()
            self._last_close_cache = {}
            self._last_close_attempted = set()
    # _fetch_last_close is a GUI-only helper: it lives on the parked Tk window
    # (gamma_window_legacy), not the headless gamma_tool engine.
    from gamma_window_legacy import GammaWindow
    _Stub._fetch_last_close = GammaWindow._fetch_last_close

    with patch("scanner_engine.fetch_price_history", side_effect=RuntimeError("boom")):
        stub = _Stub()
        assert stub._fetch_last_close("$SPX") is None
        # Retry not allowed in same session — returns cached None without re-raising.
        assert stub._fetch_last_close("$SPX") is None


def test_forward_band_intensifies_near_close():
    """Gamma forward band at a near-close slot should show higher ATM values
    than an early-in-session slot — demonstrates time-decay intensification.

    Exercises the full chain: engine retains last chain -> project_exposure_forward
    computes per-strike gamma at two different T values -> near-close value >
    early value (the "gamma wall tightens" property).
    """
    from gamma_tool import GammaEngine
    eng = GammaEngine()
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {"2026-04-18:0": {"100.0": [
            {"openInterest": 1000, "volatility": 20.0, "daysToExpiration": 0}
        ]}},
        "putExpDateMap": {},
    }
    eng.calc_from_chain(chain)
    # Two T values: 5 min before close vs 6 hours before close.
    T_near_close = 5 / 60 / 24 / 365
    T_early = 6 / 24 / 365
    near = eng.project_exposure_forward("gex", T_near_close)
    early = eng.project_exposure_forward("gex", T_early)
    assert 100.0 in near and 100.0 in early
    # Gamma at ATM scales as 1/sqrt(T), so near-close > early.
    assert near[100.0] > early[100.0]
