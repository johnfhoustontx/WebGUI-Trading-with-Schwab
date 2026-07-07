"""Smoke test: the offline five-state validation harness imports with NO network.

Only asserts the module loads and wires the pure daily_direction helpers; the
live proxy fetch (fetch_ohlcv/fetch_vix_map/main) is NOT exercised here.
"""


def test_import_is_network_free_and_wires_pure_helpers():
    import validate_market_state as v

    # Entry points exist.
    assert hasattr(v, "main")
    assert hasattr(v, "run_study")

    # It references the pure daily_direction helpers.
    assert hasattr(v, "reconstruct_state_series")
    assert hasattr(v, "forward_returns")
    assert hasattr(v, "per_state_stats")
    assert hasattr(v, "ordinal_ic")

    # Output paths are resolved from repo_paths (not hard-coded).
    from repo_paths import (MARKET_STATE_VALIDATION_REPORT,
                            MARKET_STATE_VALIDATION_JSON)
    assert v.MARKET_STATE_VALIDATION_REPORT == MARKET_STATE_VALIDATION_REPORT
    assert v.MARKET_STATE_VALIDATION_JSON == MARKET_STATE_VALIDATION_JSON
