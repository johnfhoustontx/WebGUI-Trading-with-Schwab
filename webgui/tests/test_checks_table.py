"""The shared Checks-column helpers (``checks_table.py``) - PURE parts.

The Market Scanner and the Strategy Finder both draw the checklist's one-chip
verdict and the "Only clear" filter. The helpers live in one widget-free module
so the Finder never imports the scanner PAGE for them, and the two tables
cannot word or filter the same verdict differently.
"""
import ast
import pathlib

from pages.options import checks_table as ct


def _imports(module):
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {node.module or ""} | {a.name for a in node.names}
    return names


def test_module_imports_no_widget_library_and_no_page():
    names = _imports(ct)
    assert names, "parsed no imports at all - the check is vacuous"
    assert not {n for n in names if n.split(".")[0] == "nicegui"}
    assert not {n for n in names if n.split(".")[-1] in ("scanner", "swing",
                                                          "strategy_table")}


def test_the_scanner_re_exports_the_shared_helpers_under_their_old_names():
    from pages.options import scanner
    assert scanner.stamp_checks is ct.stamp_checks
    assert scanner.only_clear is ct.only_clear
    assert scanner.restamp is ct.restamp
    assert scanner.only_clear_empty_label is ct.only_clear_empty_label
    assert scanner.filtered_tab_label is ct.filtered_tab_label
    assert scanner._CHECKS_SLOT is ct.CHECKS_SLOT
    assert scanner._ONLY_CLEAR_TIP is ct.ONLY_CLEAR_TIP


def test_filtered_tab_label_needs_no_scanner_helper():
    assert ct.filtered_tab_label("Swing", 40, 3, have=True, filtering=False) == "Swing (40)"
    assert ct.filtered_tab_label("Swing", 40, 3, have=True, filtering=True) == "Swing (3 of 40)"
    assert ct.filtered_tab_label("Swing", 40, 3, have=False, filtering=True) == "Swing"
