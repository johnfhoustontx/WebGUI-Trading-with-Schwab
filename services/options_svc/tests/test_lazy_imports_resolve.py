"""Every module ``options_svc`` imports LAZILY must actually exist.

``compute.py`` imports its engines inside ~40 functions (the documented
``scoring`` collision forces it), so a deleted engine module is not a startup
error -- it is a ``ModuleNotFoundError`` on one command path, which the page
then renders as "Live data unavailable". That is how the Paper Ledger's
Analyze button stayed broken from 2026-08-20 (``trade_analyzer.py`` deleted as
dead) until 2026-09-19: its own tests planted a fake module in ``sys.modules``,
so they passed against a file that no longer existed.

This walks the source and resolves every imported top-level name with
``importlib.util.find_spec`` -- it imports nothing, so it cannot trip the
collision it exists beside.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

_SVC = Path(__file__).resolve().parents[1]
_ROOT = _SVC.parents[1]
_FILES = sorted(p for p in _SVC.glob("*.py") if not p.name.startswith("test_"))


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_every_imported_module_exists(path, monkeypatch):
    # The service's own sys.path glue: repo root, its own dir, options-scanner.
    for extra in (_ROOT, _SVC, _ROOT / "options-scanner"):
        monkeypatch.syspath_prepend(str(extra))
    missing = []
    for name in sorted(_imported_roots(path)):
        if name in sys.builtin_module_names:
            continue
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    assert not missing, f"{path.name} imports modules that do not exist: {missing}"
