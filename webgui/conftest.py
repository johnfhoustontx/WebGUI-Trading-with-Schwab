"""Pytest path glue for the webgui app.

Mirrors the runtime sys.path setup used by every entrypoint: the repo root
(for ``repo_paths`` and the hyphenated app folders) and the ``webgui`` package
dir (so ``import proxy`` / ``import pages.*`` resolve when tests run from inside
this folder).
"""
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "webgui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
