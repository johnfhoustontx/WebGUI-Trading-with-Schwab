"""Tests for the NiceGUI-free sentiment compute module.

These call real engine code, so we test the safe/defensive paths by
monkeypatching the proxy accessor so nothing requires a live proxy.
"""
import sys

from services import _proxy
from services.sentiment_svc import compute


def test_load_live_returns_none_on_client_error(monkeypatch):
    """load_live swallows engine errors and returns None (defensive).

    We make sectors_ref.load_sectors_data raise so the exception escapes into
    load_live's try/except (compute_live itself tolerates a broken client and
    returns a zeroed snapshot, so patching the client alone won't trip it)."""
    import sectors_ref

    def _raise(*a, **k):
        raise RuntimeError("sectors error")

    monkeypatch.setattr(sectors_ref, "load_sectors_data", _raise)
    assert compute.load_live() is None


def test_proxy_up_false_when_health_raises(monkeypatch):
    """proxy_up returns False when the health check raises (defensive)."""
    def _raise(*a, **k):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(_proxy, "health", _raise)
    assert compute.proxy_up() is False


def _snap(date, total, **scores):
    base = {"vix_complex": 5.0, "put_call": 5.0, "breadth": 5.0,
            "rotation": 5.0, "sector_perf": 5.0, "credit_pulse": 5.0}
    base.update(scores)
    return {
        "date": date,
        "composite": {"total_score": f"{total:.2f}", "bias": "Neutral",
                      "size_modifier": "1.00x", "aggregate_confidence": 0.8},
        "component_scores": base,
        "component_confidence": {k: 0.9 for k in base},
    }


def test_derive_composite_extras_shape_and_values():
    """derive_composite_extras returns the full derived payload with real
    scoring-computed weights/band/velocity/divergence/trend."""
    snaps = [_snap(f"2026-05-{d:02d}", 5.0 + (d % 3) * 0.2) for d in range(1, 21)]
    live = _snap("2026-06-01", 8.0, vix_complex=9.0, sector_perf=2.0)
    spy = [100.0 + i * 0.5 for i in range(260)]
    out = compute.derive_composite_extras(live, snaps, spy)

    assert set(out) == {"weights", "size", "bias", "signal",
                        "velocity", "divergence", "trend", "trend_30d_ago"}
    # weights = sentiment v4.3 WEIGHTS (credit_pulse excluded, sums to 1.0).
    assert abs(sum(out["weights"].values()) - 1.0) < 1e-9
    assert "credit_pulse" not in out["weights"]
    assert out["weights"]["sector_perf"] == 0.25
    # band labels populated (signal_band over total 8.0).
    assert out["size"] and out["bias"] and out["signal"]
    # velocity text built from the prior series (full backfill since live).
    assert "3d ROC" in out["velocity"]["text"] and "20d Z" in out["velocity"]["text"]
    # vix 9 vs sector_perf 2 -> >=4 spread -> a divergence label.
    assert "DIVERGENCE" in out["divergence"]
    # trend dict mirrors build_trend_dict (state/label/days/...).
    tr = out["trend"]
    assert tr is not None
    assert tr["state"] in {"bull_trend", "pullback_in_bull", "range",
                           "bear_rally", "bear_trend"}
    assert {"label", "description", "raw_state", "spy_close", "sma_50",
            "sma_200", "confidence", "days"} <= set(tr)


def test_derive_composite_extras_defensive_no_spy():
    """No spy -> trend None; minimal snaps -> velocity dashes, no crash."""
    out = compute.derive_composite_extras(None, [], [])
    assert out["trend"] is None
    assert out["divergence"] == ""
    assert out["velocity"]["flag"] == ""
    assert isinstance(out["weights"], dict)


def test_build_trend_dict_none_on_empty():
    assert compute.build_trend_dict([]) is None
    assert compute.build_trend_dict(None) is None


def test_derive_sector_summary_values():
    sd = [
        {"kind": "sector", "sector": "Tech", "etf": "XLK",
         "sp_weight": 30.0, "name": "Tech"},
        {"kind": "sector", "sector": "Energy", "etf": "XLE",
         "sp_weight": 5.0, "name": "Energy"},
    ]
    quotes = {"XLK": {"change_pct": 1.0}, "XLE": {"change_pct": -0.5}}
    out = compute.derive_sector_summary({"sector_data": sd, "quotes": quotes})
    assert set(out) == {"wpct", "score"}
    assert out["wpct"] is not None
    assert isinstance(out["score"], float)


def test_derive_sector_summary_defensive():
    out = compute.derive_sector_summary(None)
    assert out == {"wpct": None, "score": 0.0}


def test_derive_composite_extras_includes_trend_30d_ago():
    # 260 rising closes -> a valid regime; [:-30] still has >200 bars.
    spy = [100.0 + i * 0.5 for i in range(260)]
    snaps = [{"composite": {"total_score": "6.00"}}]
    out = compute.derive_composite_extras(live=None, snaps=snaps, spy=spy)
    assert "trend" in out and "trend_30d_ago" in out
    t30 = out["trend_30d_ago"]
    assert t30 is not None and t30.get("state") in {
        "bull_trend", "pullback_in_bull", "range", "bear_rally", "bear_trend"}
    assert "sma_200_slope_pct" in t30 and "drawdown_pct" in t30


def test_derive_composite_extras_trend_30d_ago_degrades_on_short_spy():
    spy = [100.0 + i for i in range(40)]  # < 30 + MIN_BARS_PARTIAL -> use full spy
    out = compute.derive_composite_extras(live=None, snaps=[], spy=spy)
    assert "trend_30d_ago" in out   # present (may be None), never raises


def test_compute_imports_clean():
    """compute imports without pulling in nicegui or the webgui UI tier."""
    import services.sentiment_svc.compute as c  # noqa: F401
    # The module must not expose a NiceGUI `ui` handle (the page's render does).
    assert not hasattr(compute, "ui")
    # And it must not import nicegui at module scope.
    assert "nicegui" not in compute.__dict__
