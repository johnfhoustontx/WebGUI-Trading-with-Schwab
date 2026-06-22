"""Run from the repo root with the repo venv:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_markov_prior.py -v
(never `pytest services` over all services — cross-app module-name collisions.)"""
import numpy as np
import pandas as pd
from services.trade_svc import compute


def test_universe_prior_from_series(monkeypatch):
    series = {
        "AAA": pd.Series([0, 0, 2, 3, 4, 4, 3] * 40, dtype=float),
        "BBB": pd.Series([2, 2, 1, 0, 1, 2] * 40, dtype=float),
    }
    monkeypatch.setattr(compute, "_symbol_band_series", lambda sym: series.get(sym))
    prior, n = compute.build_pooled_prior(["AAA", "BBB", "MISSING"])
    assert prior.shape == (5, 5)
    np.testing.assert_allclose(prior.sum(axis=1), 1.0)
    assert n == 2


def test_get_prior_uses_fresh_cache(monkeypatch):
    calls = {"n": 0}

    def fake_build(_universe):
        calls["n"] += 1
        return np.full((5, 5), 0.2), 2

    monkeypatch.setattr(compute, "build_pooled_prior", fake_build)
    fresh = {"matrix": [[0.2] * 5] * 5, "date": compute._today_ct_str(),
             "n_symbols": 2}
    monkeypatch.setattr(compute, "_read_prior_cache", lambda: fresh)
    P, ver = compute.get_prior()
    assert calls["n"] == 0  # fresh cache -> no rebuild
    np.testing.assert_allclose(np.array(P).sum(axis=1), 1.0)


def test_get_prior_rebuilds_when_stale(monkeypatch):
    calls = {"n": 0}

    def fake_build(_universe):
        calls["n"] += 1
        return np.full((5, 5), 0.2), 3

    monkeypatch.setattr(compute, "build_pooled_prior", fake_build)
    monkeypatch.setattr(compute, "_read_prior_cache", lambda: {"matrix": [[0.2]*5]*5,
                        "date": "1999-01-01", "n_symbols": 1})  # stale date
    monkeypatch.setattr(compute, "_write_prior_cache", lambda m, n: None)
    P, ver = compute.get_prior()
    assert calls["n"] == 1  # stale -> rebuilt
