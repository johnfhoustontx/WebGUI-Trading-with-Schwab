"""Run from the repo root with the repo venv:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_swing_wiring.py -v
(never `pytest services` over all services — cross-app module-name collisions.)

Hermetic: every proxy call and Redis touch is monkeypatched, so these run with no
proxy / Memurai. Follows test_markov_analyze.py / test_markov_prior.py style."""
import numpy as np
import pandas as pd
from services.trade_svc import compute


# A synthetic ~300-bar daily OHLCV frame (enough to seed every factor window:
# mom_12_1 needs 252+21, low_vol 60, trend_quality 200). A gentle uptrend so the
# factors take finite, non-degenerate values.
def _synthetic_daily(n=320, start=100.0):
    ts = pd.date_range("2024-01-01", periods=n, freq="B")
    close = start + np.linspace(0, 40, n) + np.sin(np.arange(n) / 7.0)
    return pd.DataFrame({
        "datetime": ts,
        "open": close - 0.3,
        "high": close + 0.6,
        "low": close - 0.6,
        "close": close,
        "volume": np.full(n, 1_000_000.0) + np.arange(n) * 10.0,
    })


_ARTIFACT = {
    "version": "2026-06-28", "horizon": 20,
    "regimes": {"all": {
        "weights": {"mom_12_1": 0.5, "low_vol": -0.5},
        "factor_ic": {"mom_12_1": {"mean_ic": 0.04}, "low_vol": {"mean_ic": -0.066}},
        "norm": {"mom_12_1": {"mean": 0.0, "std": 0.1}, "low_vol": {"mean": -0.02, "std": 0.01}},
        "calibration": [
            {"band": 0, "score_lo": -3, "score_hi": -0.5, "mean_fwd": -0.008, "hit_rate": 0.43, "n": 100},
            {"band": 1, "score_lo": -0.5, "score_hi": 0.5, "mean_fwd": 0.0, "hit_rate": 0.49, "n": 100},
            {"band": 2, "score_lo": 0.5, "score_hi": 3, "mean_fwd": 0.0135, "hit_rate": 0.523, "n": 100}],
        "oos_ic": 0.0367}}}


def test_build_universe_factor_snapshot_shape(monkeypatch):
    """The snapshot now keeps SYMBOL IDENTITY ({"by_symbol": {sym: {factor:
    value}}}) so it can rank as well as cross-section. The flat basis the
    scorer consumes is derived from it — asserted here too, because that
    derivation is what keeps the scoring path unchanged."""
    monkeypatch.setattr(compute, "_price_history",
                        lambda *a, **k: _synthetic_daily())
    snap = compute.build_universe_factor_snapshot()
    assert isinstance(snap, dict) and snap
    by_symbol = snap["by_symbol"]
    assert by_symbol and all(isinstance(r, dict) and r for r in by_symbol.values())
    assert all(np.isfinite(v) for r in by_symbol.values() for v in r.values())

    flat = compute.flat_basis(snap)
    for factor, values in flat.items():
        assert isinstance(values, list) and len(values) >= 1
        assert all(np.isfinite(v) for v in values)
    # The non-ref momentum/vol factors should always be present given 320 bars.
    assert "mom_12_1" in flat and "low_vol" in flat


def test_build_universe_factor_snapshot_all_missing(monkeypatch):
    # proxy-down: every history None -> {} (no raise; scorer falls back to norm).
    monkeypatch.setattr(compute, "_price_history", lambda *a, **k: None)
    assert compute.build_universe_factor_snapshot() == {}


def test_get_universe_snapshot_uses_fresh_cache(monkeypatch):
    calls = {"n": 0}

    def fake_build():
        calls["n"] += 1
        return {"mom_12_1": [0.1, 0.2]}

    monkeypatch.setattr(compute, "build_universe_factor_snapshot", fake_build)
    fresh = {"factors": {"mom_12_1": [0.1, 0.2, 0.3]}, "date": compute._today_ct_str()}
    monkeypatch.setattr(compute, "_read_universe_snapshot", lambda: fresh)
    snap = compute.get_universe_snapshot()
    assert calls["n"] == 0  # fresh cache -> no rebuild
    assert snap == {"mom_12_1": [0.1, 0.2, 0.3]}


def test_get_universe_snapshot_rebuilds_when_stale(monkeypatch):
    calls = {"n": 0}

    def fake_build():
        calls["n"] += 1
        return {"mom_12_1": [0.5]}

    monkeypatch.setattr(compute, "build_universe_factor_snapshot", fake_build)
    monkeypatch.setattr(compute, "_read_universe_snapshot",
                        lambda: {"factors": {"x": [1.0]}, "date": "1999-01-01"})
    monkeypatch.setattr(compute, "_write_universe_snapshot", lambda s: None)
    snap = compute.get_universe_snapshot()
    assert calls["n"] == 1  # stale date -> rebuilt
    assert snap == {"mom_12_1": [0.5]}


def test_swing_universe_reads_fit_universe(monkeypatch):
    # The snapshot universe is the model's fit_universe (the calibration cross-section).
    from services.trade_svc import swing_model
    u10 = [f"S{i}" for i in range(10)]
    monkeypatch.setattr(swing_model, "load_artifact", lambda: {"fit_universe": u10})
    assert compute._swing_universe() == u10


def test_swing_universe_falls_back_to_mk_universe(monkeypatch):
    # No artifact / no field / too-thin list -> fall back to the curated _MK_UNIVERSE.
    from services.trade_svc import swing_model
    for bad in (None, {}, {"fit_universe": []}, {"fit_universe": ["A", "B"]}):
        monkeypatch.setattr(swing_model, "load_artifact", lambda b=bad: b)
        assert compute._swing_universe() == list(compute._MK_UNIVERSE)


def test_handler_fields_include_swing_model():
    from services.trade_svc import handlers
    assert "swing_model" in handlers._FIELDS


class _FakeClient:
    """Minimal proxy stand-in: a finite quote + the synthetic daily frame for any
    /pricehistory call + empty fundamentals."""

    def get_quote(self, symbol):
        return {"symbol": symbol, "last": 140.0, "volume": 1_000_000}

    def _request(self, endpoint, params):
        df = _synthetic_daily()
        candles = [{"datetime": int(t.value // 1_000_000), "open": o, "high": h,
                    "low": lo, "close": c, "volume": v}
                   for t, o, h, lo, c, v in zip(
                       df["datetime"], df["open"], df["high"], df["low"],
                       df["close"], df["volume"])]
        return {"candles": candles}

    def get_fundamentals(self, symbol):
        return None


def test_analyze_attaches_swing_model(monkeypatch):
    # End-to-end (hermetic) analyze: fake proxy + monkeypatched artifact/snapshot.
    # Proves the swing block is wired into analyze() and lands in the result.
    from services import _proxy
    monkeypatch.setattr(_proxy, "schwab_client", _FakeClient())
    from services.trade_svc import swing_model as _swing
    monkeypatch.setattr(_swing, "load_artifact", lambda: _ARTIFACT)
    monkeypatch.setattr(compute, "get_universe_snapshot",
                        lambda: {"mom_12_1": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
                                 "low_vol": [-0.05, -0.04, -0.03, -0.02, -0.01, 0.0]})

    result = compute.analyze("AAPL")
    assert result is not None
    assert "swing_model" in result
    sm = result["swing_model"]
    assert sm is not None
    assert sm["verdict"] in ("BUY", "HOLD", "SELL")
    assert sm["source"] == "validated"
    assert sm["model_version"] == "2026-06-28"
    assert any(c["factor"] in ("mom_12_1", "low_vol") for c in sm["contributions"])


def test_analyze_swing_model_none_without_artifact(monkeypatch):
    # No artifact -> swing_model None, analyze() otherwise intact.
    from services import _proxy
    monkeypatch.setattr(_proxy, "schwab_client", _FakeClient())
    from services.trade_svc import swing_model as _swing
    monkeypatch.setattr(_swing, "load_artifact", lambda: None)

    result = compute.analyze("AAPL")
    assert result is not None
    assert result["swing_model"] is None
    assert result["errors"] == []  # analyze still succeeded


def test_analyze_does_not_build_markov_block(monkeypatch):
    # The Trade page's Markov card was removed, so analyze() must not spend a
    # pooled-prior rebuild (+ its universe-wide history fetch) on a block nobody
    # reads. Guards the CALL PATH, not just the absent key: analyze()'s broad
    # try/except would swallow a raising stub, so count the calls instead.
    from services import _proxy
    monkeypatch.setattr(_proxy, "schwab_client", _FakeClient())
    from services.trade_svc import swing_model as _swing
    monkeypatch.setattr(_swing, "load_artifact", lambda: None)
    calls = {"n": 0}

    def _counting_prior():
        calls["n"] += 1
        return np.full((5, 5), 0.2), "test"

    monkeypatch.setattr(compute, "get_prior", _counting_prior)

    result = compute.analyze("AAPL")
    assert result is not None
    assert not result.get("markov")  # key absent (contract default -> None)
    assert calls["n"] == 0           # no prior rebuild on the request path
