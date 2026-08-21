"""``gamma_tool`` must stay importable WITHOUT a GUI toolkit.

``gamma_tool`` is the shared GEX/Charm/DEX/Vanna engine. It is imported by
headless server processes — ``services/options_svc/compute.py`` (~10 lazy import
sites), ``gex_collector.py``, ``scanner_engine.py``, ``tools/gex_term_one_shot.py``.

Historically the module also carried the legacy Tk desktop window
(``class GammaWindow(tk.Toplevel)``) with ``import tkinter`` / ``import
matplotlib`` / ``matplotlib.use("TkAgg")`` at MODULE scope, so every one of those
headless importers paid ~0.7 s and loaded a GUI toolkit (and forced the TkAgg
backend process-wide). The window now lives in ``gamma_window_legacy.py``.

These tests run the import in a SUBPROCESS on purpose: the rest of the suite
imports tkinter/matplotlib for the legacy dashboard tests, so an in-process
``sys.modules`` check would be poisoned by test-ordering.
"""

import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]

_PROBE = """
import sys
import gamma_tool
bad = [m for m in ("tkinter", "matplotlib") if m in sys.modules]
print(",".join(bad))
"""


def _import_probe():
    """Import gamma_tool in a clean interpreter; return the GUI modules it pulled in."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(APP_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"importing gamma_tool failed:\n{proc.stderr}"
    return [m for m in proc.stdout.strip().split(",") if m]


def test_import_does_not_pull_in_gui_toolkit():
    assert _import_probe() == [], (
        "gamma_tool pulled a GUI toolkit into a headless import — a module-level "
        "tkinter/matplotlib import has crept back in."
    )


def test_engine_entry_points_survive_without_gui():
    """The names headless callers actually use must still import and be callable."""
    probe = """
import gamma_tool as gt
for name in ("GammaEngine", "get_gex_walls", "calc_dex_from_chain", "get_dex_walls",
             "get_directional_walls", "build_analysis_dict", "calc_flip_point",
             "build_summary_prompt_bundled"):
    assert hasattr(gt, name), name
print("ok")
"""
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(APP_DIR), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


def test_calc_flip_point_is_module_level():
    """Pure flip math must not live on the Tk window class.

    ``build_analysis_dict`` (headless, called by the options service) used to
    reach into ``GammaWindow._calc_flip_point`` — a forward reference into the
    GUI class from pure engine code.
    """
    import gamma_tool as gt

    assert callable(gt.calc_flip_point)
    # zero-crossing between 99 (+) and 101 (-) around spot 100 -> interpolated 100.0
    gex = {99.0: {"net": 1.0}, 101.0: {"net": -1.0}}
    assert gt.calc_flip_point(gex, 100.0) == 100.0
    # no crossing -> None
    assert gt.calc_flip_point({99.0: {"net": 1.0}, 101.0: {"net": 2.0}}, 100.0) is None
    # too few strikes -> None
    assert gt.calc_flip_point({100.0: {"net": 1.0}}, 100.0) is None
