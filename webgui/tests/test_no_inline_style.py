"""Guard: the converted shared Options helpers are .style()-free (Tailwind-only).

Phase 2 of the Tailwind-first migration removes every inline `.style()` from the
shared `pages/options/*` helpers, mapping dynamic colors to fixed palette classes.
This test prevents a regression that reintroduces an inline style.
"""
import pathlib


def test_options_helpers_have_no_inline_style():
    base = pathlib.Path(__file__).resolve().parents[1] / "pages" / "options"
    for fn in ["detail.py", "header.py", "overlay.py", "leg_editor.py", "strategy_menu.py"]:
        src = (base / fn).read_text(encoding="utf-8")
        assert ".style(" not in src, f"{fn} still uses .style()"
