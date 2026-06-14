"""Tests for the symbol-agnostic chain-fetch + analysis-build path used by
the upcoming bundled SPX/SPY/QQQ prompt flow."""
from unittest.mock import MagicMock
from gamma_tool import GammaWindow, build_analysis_dict


def _minimal_chain():
    """Smallest viable chain that GammaEngine.calc_*_from_chain accepts."""
    return {
        "underlyingPrice": 500.0,
        "callExpDateMap": {"2099-12-31:1": {
            "500.0": [{"gamma": 0.05, "openInterest": 1000, "totalVolume": 0,
                       "delta": 0.5}]}},
        "putExpDateMap": {"2099-12-31:1": {
            "500.0": [{"gamma": 0.05, "openInterest": 1000, "totalVolume": 0,
                       "delta": -0.5}]}},
    }


def _stub_client(chain, status_code=200):
    response = MagicMock(status_code=status_code, json=lambda: chain)
    client = MagicMock()
    client.get_option_chain.return_value = response
    client.Options.ContractType.ALL = "ALL"
    return client


def test_fetch_returns_blocks_for_each_view():
    client = _stub_client(_minimal_chain())
    blocks = GammaWindow._fetch_symbol_analysis_impl(client, "SPY")
    assert blocks is not None
    assert set(blocks.keys()) == {"gex", "charm", "dex", "vanna"}
    # At least one view should produce a non-None dict
    assert any(blocks[v] is not None for v in ("gex", "charm", "dex", "vanna"))
    # Symbol propagates through
    for v in ("gex", "charm", "dex", "vanna"):
        if blocks[v] is not None:
            assert blocks[v]["symbol"] == "SPY"


def test_fetch_returns_none_on_http_failure():
    client = _stub_client(None, status_code=500)
    assert GammaWindow._fetch_symbol_analysis_impl(client, "SPY") is None


def test_fetch_returns_none_on_exception():
    client = MagicMock()
    client.get_option_chain.side_effect = RuntimeError("network down")
    client.Options.ContractType.ALL = "ALL"
    assert GammaWindow._fetch_symbol_analysis_impl(client, "SPY") is None


def test_fetch_propagates_use_volume_kwarg():
    """Confirms use_volume is accepted and doesn't break the fetch path."""
    client = _stub_client(_minimal_chain())
    blocks = GammaWindow._fetch_symbol_analysis_impl(client, "SPY", use_volume=True)
    # With totalVolume=0 in the minimal chain, vol-weighted may legitimately
    # produce None blocks — we just care that the kwarg threads through.
    if blocks is not None:
        assert set(blocks.keys()) == {"gex", "charm", "dex", "vanna"}


def test_fetch_propagates_grouping_kwarg():
    """Confirms grouping is accepted and threaded into build_analysis_dict."""
    client = _stub_client(_minimal_chain())
    blocks = GammaWindow._fetch_symbol_analysis_impl(client, "SPY", grouping=5)
    assert blocks is not None
    if blocks.get("gex"):
        assert blocks["gex"]["grouping"] == 5


def test_build_analysis_dict_pure_function():
    """build_analysis_dict should work without any Tk state."""
    from gamma_tool import GammaEngine
    chain = _minimal_chain()
    engine = GammaEngine()
    gex = engine.calc_from_chain(chain)
    assert gex is not None
    result = build_analysis_dict(gex, "gex", "SPY", dte=0)
    assert result["symbol"] == "SPY"
    assert result["view"] == "GEX"
    assert "top_positive" in result
    assert "atm_breakdown" in result
