"""``shared.positions.merge_positions`` - the one rule both the proxy's
``/positions`` and the Portfolio engine fold accounts by."""
import pytest

from shared.positions import merge_positions


def _p(symbol, qty, avg, mv):
    return {"symbol": symbol, "quantity": qty, "avg_price": avg,
            "market_value": mv, "day_pl": 1.0, "total_pl": 2.0}


def test_same_symbol_folds_and_reweights_the_average_price():
    out = merge_positions([_p("AAPL", 10, 150.0, 1500.0), _p("MSFT", 1, 300.0, 300.0),
                           _p("AAPL", 5, 180.0, 900.0)])
    assert [r["symbol"] for r in out] == ["AAPL", "MSFT"]
    assert out[0]["quantity"] == 15.0
    assert out[0]["avg_price"] == pytest.approx(160.0)
    assert out[0]["market_value"] == 2400.0
    assert out[0]["day_pl"] == 2.0


def test_a_single_row_passes_through_as_a_copy():
    row = _p("AAPL", 10, 150.0, 1500.0)
    out = merge_positions([row])
    assert out == [row] and out[0] is not row


def test_the_proxy_folds_with_the_shared_rule():
    # Read at source level: importing the proxy here would pull its whole stack.
    from repo_paths import SCHWAB_PROXY
    src = (SCHWAB_PROXY / "schwab_proxy.py").read_text(encoding="utf-8")
    assert "_merge_positions = _positions.merge_positions" in src
    assert "def _merge_positions" not in src
