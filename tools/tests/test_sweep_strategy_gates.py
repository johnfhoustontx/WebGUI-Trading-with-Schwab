"""``tools/sweep_strategy_gates.py`` -- the measurement behind the Strategy
Finder's new gate profiles (``strategy_scoring._TYPE_PROFILE``).

The script is a re-runnable sweep, not a test; what is pinned here is that it
still REACHES every new structure through the real builders and that the design
doc's two central cuts -- the short straddle and the covered call grade Weak --
still reproduce through the real scorers. If either stops holding, the
``_TYPE_PROFILE`` comments and the design doc's Scoring table are stale.
"""
import importlib.util
from pathlib import Path


def _tool():
    p = Path(__file__).resolve().parents[1] / "sweep_strategy_gates.py"
    spec = importlib.util.spec_from_file_location("sweep_strategy_gates", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sweep_reaches_every_new_structure_and_reproduces_the_two_cuts():
    rows = list(_tool().rows(100.0, 0.28, 30, 2.5))
    types = {r["type"] for r in rows}
    assert {"LONG_STRADDLE", "SHORT_STRADDLE", "BUTTERFLY_CALL", "IRON_BUTTERFLY",
            "CONDOR_PUT", "CALENDAR_CALL", "DIAGONAL_PUT", "COVERED_CALL",
            "PROTECTIVE_PUT", "COLLAR"} <= types
    by = {r["type"]: r for r in rows}
    assert by["SHORT_STRADDLE"]["grade"] == "Weak"
    assert by["COVERED_CALL"]["grade"] == "Weak"
