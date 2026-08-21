"""`shared/analysis_lib` is a LIBRARY of three live modules, not an application.

Until 2026-08-20 the package was the abandoned "Blueprint Analyzer" Tk app: ~9,600
of its 11,406 lines had zero callers outside each other, and `__init__.py` eagerly
imported all of them -- including `schwab_client`, documented in-repo as broken.
That eager init is WHY all four live consumers (sentiment_svc.compute,
trade_svc.compute, scoring.regime_evidence, portfolio-analyzer/src/sectors) carry a
sys.path bootstrap to import `technical` standalone and dodge the package.

These pin the surface so the app cannot grow back into the library.
"""
import pathlib
import subprocess
import sys
import textwrap

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PKG = _ROOT / "shared" / "analysis_lib"

# The modules that survive. Everything else was the Tk app.
LIVE_MODULES = {"__init__", "technical", "sector_analysis", "config"}

DELETED_MODULES = {
    "gui_main", "blueprint_scorer", "market_data", "macro_score", "schwab_client",
    "main", "tos_import", "mtf_analysis", "position_sizer", "alerts", "data_cache",
}


def test_analysis_lib_holds_only_the_live_modules():
    present = {p.stem for p in _PKG.glob("*.py")}
    assert present == LIVE_MODULES, f"unexpected modules: {present - LIVE_MODULES}"


def test_the_abandoned_tk_app_is_gone():
    for name in DELETED_MODULES:
        assert not (_PKG / f"{name}.py").exists(), f"{name}.py is back"
    assert not (_PKG / "agents").exists(), "the agents/ package is back"


def test_importing_the_package_pulls_in_no_gui_stack_and_no_broken_client():
    """The whole point of the trim: the package init must be importable from a
    headless service without dragging in Tk, matplotlib, or the broken client."""
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, r"{_ROOT}")
        from shared.analysis_lib import technical, sector_analysis, config
        bad = sorted(m for m in sys.modules
                     if m.split(".")[0] in ("tkinter", "matplotlib")
                     or m.endswith("schwab_client"))
        print("|".join(bad))
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"pulled in: {out.stdout.strip()}"


def test_the_live_functions_are_importable_straight_off_the_package():
    """With the eager init gone, a consumer can import normally instead of doing
    sys.path gymnastics to reach the module standalone."""
    from shared.analysis_lib import config, sector_analysis, technical

    assert callable(technical.calculate_adx)
    assert callable(technical.calculate_ema)
    assert callable(sector_analysis.get_sector_info)
    assert isinstance(config.SECTORS, dict)
