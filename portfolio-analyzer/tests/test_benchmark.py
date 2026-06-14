from __future__ import annotations

import pathlib

from src.benchmark import DEFAULT_BENCHMARK_PATH, load_benchmark_weights


def test_load_benchmark_weights_reads_real_config():
    weights = load_benchmark_weights()
    assert weights, "real benchmark config should be non-empty"
    assert abs(weights["Technology"] - 0.30) < 1e-9


def test_default_path_points_at_config():
    assert DEFAULT_BENCHMARK_PATH.name == "benchmark_weights.toml"
    assert DEFAULT_BENCHMARK_PATH.exists()


def test_load_benchmark_weights_missing_path_returns_empty(tmp_path):
    missing = tmp_path / "does_not_exist.toml"
    assert load_benchmark_weights(missing) == {}
