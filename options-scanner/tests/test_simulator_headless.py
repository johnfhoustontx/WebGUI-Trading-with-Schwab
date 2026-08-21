"""The simulator package must be importable HEADLESS.

`options_simulator/__init__.py` eagerly imported `window.OptionsSimulatorWindow`,
which imports tkinter + matplotlib.backends.backend_tkagg at module scope. So the
first `sim_fetch` command in options_svc -- a headless FastAPI service -- pulled
the whole Tk/matplotlib stack in through the package init, defeating the
deliberately-lazy import at compute.py's simulator call site (2026-08-20).
"""
import subprocess
import sys
import textwrap


def _import_probe(statement):
    """Import in a FRESH interpreter and report which GUI modules got loaded."""
    code = textwrap.dedent(f"""
        import sys, pathlib
        sys.path.insert(0, r"{sys.path[0]}")
        sys.path.insert(0, str(pathlib.Path(r"{__file__}").resolve().parents[1]))
        {statement}
        gui = sorted(m for m in sys.modules
                     if m.split(".")[0] in ("tkinter", "matplotlib"))
        print("|".join(gui))
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return [m for m in out.stdout.strip().split("|") if m]


def test_importing_options_simulator_pulls_in_no_gui_stack():
    assert _import_probe("import options_simulator") == []


def test_importing_the_simulator_engines_pulls_in_no_gui_stack():
    assert _import_probe("from options_simulator import engine, data, pnl") == []
