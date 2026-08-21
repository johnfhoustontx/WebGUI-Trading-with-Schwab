"""Options Simulator — replay / what-if / IV-shock engines for a multi-leg position.

ENGINES ONLY. The Tk window this package used to export (`window.py`,
`OptionsSimulatorWindow`) was deleted on 2026-08-20 along with the rest of the
dropped desktop UI. Keep this init import-light: it eagerly imported that window,
which imports tkinter + matplotlib.backends.backend_tkagg at module scope, so the
first `sim_fetch` command in the headless `options_svc` pulled 106 GUI modules in
through the package init — defeating the deliberately-lazy import at the call site
in `compute.py`. `options-scanner/tests/test_simulator_headless.py` guards it.
"""
