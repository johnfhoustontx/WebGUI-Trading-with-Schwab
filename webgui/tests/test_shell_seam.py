"""The page-to-shell seam, and the reason it is a module of its own.

``pages/options/gamma.py`` used to do ``import main``. In a SECOND NiceGUI
process that executes main.py's module body, and that body registers every
``@_page`` route -- so a public live process importing it would serve
``/terminate`` (Stop All Services) and ``/settings`` to the internet, silently,
while looking entirely correct.

These tests pin the seam as a leaf module both entrypoints can provide.
"""
import ast
import pathlib

_PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"


def _imports_main(path: pathlib.Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "main" for a in node.names):
                return True
        if isinstance(node, ast.ImportFrom) and node.module == "main":
            return True
    return False


def test_no_page_imports_main():
    """THE GUARD THAT KEEPS /terminate OFF THE PUBLIC ORIGIN.

    A page that imports ``main`` cannot be rendered by any entrypoint other than
    main.py without dragging the whole route table along. Every page reaches the
    shell through ``shell.py`` instead."""
    offenders = [p.relative_to(_PAGES).as_posix()
                 for p in _PAGES.rglob("*.py") if _imports_main(p)]
    assert offenders == [], (
        f"these pages import main and so cannot be served by the live "
        f"process: {offenders}")


def test_the_seam_exposes_what_the_pages_actually_call():
    import shell
    for name in ("subtab_slot", "bind_breadcrumb_leaf",
                 "set_breadcrumb_leaf", "_view_name", "play_alert"):
        assert hasattr(shell, name), f"shell.py is missing {name}"


def test_main_still_exposes_the_seam_for_backwards_compatibility():
    """main.py re-exports the seam so anything still reaching for
    ``main.set_breadcrumb_leaf`` keeps working."""
    import main
    import shell
    assert main.subtab_slot is shell.subtab_slot
    assert main.set_breadcrumb_leaf is shell.set_breadcrumb_leaf
    assert main.play_alert is shell.play_alert


def test_the_layout_mutates_the_seam_state_it_no_longer_owns():
    """``_layout`` fills ``_SUBTAB_SLOT``/``_breadcrumb_leaf`` in place.

    The re-export binds the SAME dict objects, so a page calling
    ``shell.subtab_slot()`` sees what main's ``_layout`` just wrote. Rebinding
    either name in main (``_SUBTAB_SLOT = {...}``) would break that silently --
    no test would fail, the subtab row would just stop mounting."""
    import main
    import shell
    assert main._SUBTAB_SLOT is shell._SUBTAB_SLOT
    assert main._breadcrumb_leaf is shell._breadcrumb_leaf
