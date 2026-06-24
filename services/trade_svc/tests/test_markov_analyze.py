"""Run from the repo root with the repo venv:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_markov_analyze.py -v
(never `pytest services` over all services — cross-app module-name collisions.)"""
import numpy as np
import pandas as pd
from services.trade_svc import compute


def test_build_markov_block_happy(monkeypatch):
    monkeypatch.setattr(compute, "get_prior",
                        lambda: (np.full((5, 5), 0.2), "test"))
    bands = pd.Series([2, 2, 3, 3, 4, 3, 3, 4] * 30, dtype=float)
    block = compute.build_markov_block(bands, composite_daily_now=25.0,
                                       composite_full=38.0)
    assert block["current_band"] == 3
    assert len(block["transition_row"]) == 5
    assert {"n", "dist", "p_buy", "p_sell", "e_score"} <= set(block["horizons"][0])
    assert -100 <= block["markov_adjusted_score"] <= 100
    assert abs(block["tilt"]) <= 12.0 + 1e-9
    assert block["prior_version"] == "test"


def test_build_markov_block_degrades_on_thin_series(monkeypatch):
    monkeypatch.setattr(compute, "get_prior",
                        lambda: (np.full((5, 5), 0.2), "test"))
    assert compute.build_markov_block(pd.Series([], dtype=float), 0.0, 0.0) is None
    assert compute.build_markov_block(pd.Series([2.0, 3.0]), 0.0, 0.0) is None  # < 30


def test_handler_fields_include_markov():
    from services.trade_svc import handlers
    assert "markov" in handlers._FIELDS


def test_markov_adjusted_score_is_full_plus_tilt(monkeypatch):
    # markov_adjusted_score must equal clip(composite_full + tilt) exactly
    monkeypatch.setattr(compute, "get_prior", lambda: (np.full((5, 5), 0.2), "test"))
    monkeypatch.setattr(compute._markov, "drift_tilt", lambda *a, **k: 5.0)
    bands = pd.Series([2, 2, 3, 3, 4, 3, 3, 4] * 30, dtype=float)
    block = compute.build_markov_block(bands, composite_daily_now=25.0,
                                       composite_full=38.0)
    assert block["tilt"] == 5.0
    assert block["markov_adjusted_score"] == 43.0  # clip(38 + 5)


def test_build_markov_block_has_dense_trajectory(monkeypatch):
    # The chart needs denser near-term horizons than the metric cards. The block
    # must carry an additive `trajectory` (1/2/3/5/10/20d) while `horizons`
    # (the cards/tilt source) stays 5/10/20 and the tilt math is unchanged.
    monkeypatch.setattr(compute, "get_prior",
                        lambda: (np.full((5, 5), 0.2), "test"))
    bands = pd.Series([2, 2, 3, 3, 4, 3, 3, 4] * 30, dtype=float)
    block = compute.build_markov_block(bands, composite_daily_now=25.0,
                                       composite_full=38.0)
    assert [h["n"] for h in block["horizons"]] == [5, 10, 20]
    assert [t["n"] for t in block["trajectory"]] == [1, 2, 3, 5, 10, 20]
    assert all(len(t["dist"]) == 5 for t in block["trajectory"])
    assert all(abs(sum(t["dist"]) - 1.0) < 1e-6 for t in block["trajectory"])
