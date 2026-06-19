"""Tests for the NiceGUI-free sentiment compute module.

These call real engine code, so we test the safe/defensive paths by
monkeypatching the proxy accessor so nothing requires a live proxy.
"""
import sys

import pandas as pd

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
    # trend defaults to the neutral directional dict when none is threaded in.
    tr = out["trend"]
    assert tr is not None
    assert tr["state"] in {"bull_trend", "pullback_in_bull", "range",
                           "bear_rally", "bear_trend"}
    assert {"label", "description", "raw_state", "score", "smoothed_score",
            "confidence", "sub_scores"} <= set(tr)


def test_derive_composite_extras_defensive_no_spy():
    """No trend threaded in -> neutral 'range' placeholder; minimal snaps ->
    velocity dashes, no crash."""
    out = compute.derive_composite_extras(None, [], [])
    assert out["trend"]["state"] == "range"
    assert out["trend_30d_ago"]["state"] == "range"
    assert out["divergence"] == ""
    assert out["velocity"]["flag"] == ""
    assert isinstance(out["weights"], dict)


def test_build_trend_dict_none_on_empty():
    assert compute.build_trend_dict([]) is None
    assert compute.build_trend_dict(None) is None


def test_bridge_trend_merges_intraday_over_daily(monkeypatch):
    """``_bridge_trend`` overlays the intraday model's directional state +
    trend_score/sub_scores onto the daily classify dict's structural sma_*."""
    monkeypatch.setattr(compute, "build_trend_dict",
                        lambda spy: {"state": "range", "sma_50": 480.0,
                                     "sma_200": 450.0, "spy_close": 500.0,
                                     "confidence": 0.4})
    intraday = {"state": "bull_trend", "label": "Bull Trend", "description": "x",
                "raw_state": "bull_trend", "confidence": 0.9,
                "smoothed_score": 84.0, "sub_scores": {"price": 88}}
    merged = compute._bridge_trend(intraday, spy=[1.0, 2.0])
    assert merged["state"] == "bull_trend"
    assert merged["trend_score"] == 84.0
    assert merged["sub_scores"] == {"price": 88}
    assert merged["confidence"] == 0.9
    # structural fields preserved from the daily dict
    assert merged["sma_50"] == 480.0
    assert merged["spy_close"] == 500.0


def test_bridge_trend_falls_back_to_daily_when_no_intraday(monkeypatch):
    daily = {"state": "range", "sma_50": 480.0, "confidence": 0.4}
    monkeypatch.setattr(compute, "build_trend_dict", lambda spy: dict(daily))
    assert compute._bridge_trend(None, spy=[1.0]) == daily
    # an intraday dict with no state also falls back
    assert compute._bridge_trend({"confidence": 0.9}, spy=[1.0]) == daily


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
    snaps = [{"composite": {"total_score": "6.00"}}]
    out = compute.derive_composite_extras(live=None, snaps=snaps, spy=[])
    assert "trend" in out and "trend_30d_ago" in out
    t30 = out["trend_30d_ago"]
    assert t30 is not None and t30.get("state") in {
        "bull_trend", "pullback_in_bull", "range", "bear_rally", "bear_trend"}
    # neutral placeholder shape (directional, not the daily build_trend_dict).
    assert "sub_scores" in t30


def test_derive_composite_extras_trend_30d_passes_through():
    t30 = {"state": "bull_trend", "score": 90.0, "marker": "verbatim30"}
    out = compute.derive_composite_extras(live=None, snaps=[], spy=[],
                                          trend_30d=t30)
    assert out["trend_30d_ago"] == t30


# --- intraday trend (Phase 3) -------------------------------------------------

_TREND_STATES = {"bull_trend", "pullback_in_bull", "range",
                 "bear_rally", "bear_trend"}


def _bars(n, start, step, vol=1_000_000):
    """A synthetic OHLCV DataFrame of ``n`` rising (step>0) / falling bars."""
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "open": closes,
        "high": [c + abs(step) for c in closes],
        "low": [c - abs(step) for c in closes],
        "close": closes,
        "volume": [vol] * n,
        "datetime": pd.date_range("2026-06-01", periods=n, freq="5min"),
    })


class _FakeBullSchwab:
    """Canned bullish market: rising bars, advn>decn, low/falling VIX, green
    cyclical sectors. Every method returns data (no failures)."""

    def get_intraday_history(self, symbol, minutes=15, days=1):
        if symbol == "SPY":
            return _bars(120, 500.0, 0.5)
        return _bars(120, 100.0, 0.2)

    def get_daily_history(self, symbol, months=12):
        if symbol == "$VIX":
            # last close lower than prior -> falling VIX
            return _bars(60, 20.0, -0.05)
        return _bars(260, 400.0, 0.6)

    def get_quote(self, symbol):
        if symbol == "$VIX":
            return {"last": 14.0}
        return {"last": 1.0}

    def get_quotes(self, symbols):
        out = {}
        for s in symbols:
            if s in ("$ADVN",):
                out[s] = {"last": 2400.0}
            elif s in ("$DECN",):
                out[s] = {"last": 600.0}
            elif s in ("$NYHGH", "$NYHGH.X", "$NEWH"):
                out[s] = {"last": 300.0}
            elif s in ("$NYLOW", "$NYLOW.X", "$NEWL"):
                out[s] = {"last": 40.0}
            elif s in ("$SPXA50R", "$NYA50R", "$MMFI"):
                out[s] = {"last": 72.0}
            elif s in ("$VIX1D",):
                out[s] = {"last": 13.0}
            elif s in ("$VIX9D",):
                out[s] = {"last": 15.0}
            else:
                # sector / industry ETFs -> green day move
                out[s] = {"last": 100.0, "change_pct": 1.2}
        return out


class _FakeDeadSchwab:
    """Every method raises or returns None (proxy down / no data)."""

    def get_intraday_history(self, *a, **k):
        raise RuntimeError("dead")

    def get_daily_history(self, *a, **k):
        return None

    def get_quote(self, *a, **k):
        raise RuntimeError("dead")

    def get_quotes(self, *a, **k):
        return None


def test_compute_intraday_trend_shape_and_bull():
    out = compute.compute_intraday_trend(_FakeBullSchwab())
    for k in ("score", "smoothed_score", "state", "label", "description",
              "sub_scores", "sub_confidence", "confidence"):
        assert k in out, f"missing key {k}"
    assert 0.0 <= out["score"] <= 100.0
    assert out["score"] > 60.0
    assert out["state"] in {"bull_trend", "pullback_in_bull"}
    assert set(out["sub_scores"]) == {"price", "breadth", "sector", "vix"}
    assert set(out["sub_confidence"]) == {"price", "breadth", "sector", "vix"}


def test_compute_intraday_trend_defensive_no_data():
    out = compute.compute_intraday_trend(_FakeDeadSchwab())
    assert out["score"] == 50.0
    assert out["confidence"] == 0.0
    assert out["state"] == "range"
    assert set(out["sub_scores"]) == {"price", "breadth", "sector", "vix"}


def test_compute_intraday_trend_hysteresis_threads_state():
    """A single bull read does NOT flip a prior committed 'range' (2-day
    hysteresis): commit_state needs HYSTERESIS_DAYS matching raws to flip."""
    out = compute.compute_intraday_trend(
        _FakeBullSchwab(), prior_history=["range"], prior_committed="range")
    # committed state stays 'range' on the first divergent read.
    assert out["state"] == "range"
    # raw_state reflects the fresh (bullish) classification.
    assert out["raw_state"] in {"bull_trend", "pullback_in_bull"}
    # the rolling history was advanced by commit_state.
    assert out["state_history"] and out["state_history"][-1] == out["raw_state"]


# --- 30-day structural trend (Phase 3) ----------------------------------------

def test_compute_30d_trend_from_daily():
    spy = _bars(260, 400.0, 0.6)  # steadily rising daily closes
    months = {"XLK": 4.0, "XLF": 3.0, "XLY": 2.5, "XLP": -0.5, "XLU": -1.0}
    out = compute.compute_30d_trend(spy, months)
    for k in ("score", "state", "label", "description", "confidence",
              "sub_scores"):
        assert k in out
    assert out["score"] > 60.0
    assert out["state"] in {"bull_trend", "pullback_in_bull", "range"}
    assert set(out["sub_scores"]) == {"price", "sector"}


def test_compute_30d_trend_insufficient_data():
    out = compute.compute_30d_trend(None, {})
    assert out["score"] == 50.0
    assert out["state"] == "range"


def test_derive_composite_extras_passes_through_trend():
    trend = {"state": "bull_trend", "score": 88.0, "marker": "verbatim"}
    out = compute.derive_composite_extras(None, [], [], trend=trend)
    assert out["trend"] == trend


def test_compute_imports_clean():
    """compute imports without pulling in nicegui or the webgui UI tier."""
    import services.sentiment_svc.compute as c  # noqa: F401
    # The module must not expose a NiceGUI `ui` handle (the page's render does).
    assert not hasattr(compute, "ui")
    # And it must not import nicegui at module scope.
    assert "nicegui" not in compute.__dict__
