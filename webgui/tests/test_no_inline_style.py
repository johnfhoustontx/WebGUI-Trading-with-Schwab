"""Guard: the converted Options pages/helpers are inline-style-free (Tailwind-only).

Phase 2 of the Tailwind-first migration removes every inline `.style()` from the
shared `pages/options/*` helpers; Phase 3a extends this to the six signal-table
screens (Scanner/Swing/Captured/Paper/Paper-Portfolio/Rescue), which ALSO drop the
Vue `:style=` bindings in their Quasar table slots — dynamic cell colors map to
stamped Tailwind `:class` bindings instead. These tests prevent a regression that
reintroduces either an inline `.style()` or a slot `:style=`.
"""
import pathlib


def test_options_helpers_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options"
    for fn in ["detail.py", "header.py", "overlay.py", "leg_editor.py", "strategy_menu.py"]:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"


# Phase 3a signal-table screens: no `.style(` AND no `:style=` (slot bindings).
PHASE_3A_FILES = ["scanner.py", "swing.py", "captured.py", "paper.py",
                  "portfolio.py", "rescue.py"]


def test_phase3a_pages_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options"
    for fn in PHASE_3A_FILES:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"
        assert ":style=" not in src, f"{fn} still uses a Vue :style= slot binding"


# Phase 3b builder/analytic screens: Calculator + Simulator on Tailwind tokens.
# The Calculator's P&L heatmap grid is a raw ``ui.html()`` HTML string with inline
# ``style=`` attributes (NOT the `.style(` method / Vue `:style=` binding) — so it
# is out of scope and the guard (which checks `.style(`/`:style=`) still passes.
PHASE_3B_FILES = ["calculator.py", "simulator.py"]


def test_phase3b_pages_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options"
    for fn in PHASE_3B_FILES:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"
        assert ":style=" not in src, f"{fn} still uses a Vue :style= slot binding"
