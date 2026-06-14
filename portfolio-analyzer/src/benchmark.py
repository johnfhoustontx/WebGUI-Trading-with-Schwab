"""Benchmark sector weights for comparison #2 (my weights vs benchmark).

The benchmark is a *static, editable* TOML of approximate S&P 500 GICS sector
weights (``config/benchmark_weights.toml``). It carries no logic — it is just a
``{sector: fraction}`` table the comparison code subtracts from the portfolio's
own sector weights.

Path resolution uses the repo-root ``repo_paths`` constant (no hard-coded
absolute paths), matching the sys.path-insert pattern used by the other ``src``
modules.
"""
from __future__ import annotations

import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # repo root
from repo_paths import PORTFOLIO_ANALYZER

DEFAULT_BENCHMARK_PATH = PORTFOLIO_ANALYZER / "config" / "benchmark_weights.toml"


def load_benchmark_weights(path=DEFAULT_BENCHMARK_PATH) -> dict[str, float]:
    """Load benchmark sector weights from a TOML ``[weights]`` table.

    Args:
        path: TOML file to read (defaults to the bundled config). Read in binary
            mode as ``tomllib`` requires.

    Returns:
        ``{sector: fraction}`` from the ``[weights]`` table. Returns ``{}`` if
        the file is missing or has no ``[weights]`` table — the caller treats an
        empty dict as "skip comparison #2".
    """
    path = pathlib.Path(path)
    if not path.exists():
        return {}
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return dict(data.get("weights", {}))
