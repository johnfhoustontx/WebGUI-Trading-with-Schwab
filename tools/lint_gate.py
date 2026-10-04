#!/usr/bin/env python
"""The lint gate: ``ruff check`` over the staged Python files, or the whole tree.

    python tools/lint_gate.py --staged     what the git pre-commit hook runs
    python tools/lint_gate.py --all        the whole repository

Exit 0 when clean, 1 when ruff reports a finding, and 0 WITH A LOUD LINE when
ruff is not installed in this interpreter (the production venv has no ruff, and
a gate that cannot run must say so rather than pass quietly or block a box that
never commits).

WHY THIS EXISTS (audit CQ-02, 2026-10-03). The rule set lives in
``pyproject.toml``, and it was "enforced" in three places that each enforced
nothing: ``.pre-commit-config.yaml`` with no hook installed, an editor hook that
dropped ruff's output and did not run in a worktree, and a CI run that had been
red on every push for an unrelated reason. An undefined name sat on main for two
weeks. The rule set is small on purpose (E9, F63, F7, F82: syntax errors and
undefined names; and, since 2026-10-04, F401 and F811: unused imports and
redefinitions), so a finding here is a bug, not a style note. The last two are
reported and never fixed automatically (``unfixable`` in pyproject.toml).
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def staged_python_files(repo=ROOT) -> list:
    """Staged ``*.py`` paths that still exist (added, copied, modified, renamed)."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=str(repo), capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        return []
    return [p for p in out.stdout.splitlines()
            if p.endswith(".py") and (pathlib.Path(repo) / p).exists()]


def ruff_available(run=subprocess.run) -> bool:
    try:
        done = run([sys.executable, "-m", "ruff", "--version"],
                   capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    return done.returncode == 0


def check(paths, repo=ROOT, run=subprocess.run):
    """``(returncode, output)`` of ``ruff check`` on ``paths`` (["."] = the tree).

    ``--force-exclude`` so a file the project config excludes stays excluded
    even when it is named on the command line, as a staged file is.
    """
    done = run([sys.executable, "-m", "ruff", "check", "--force-exclude", *paths],
               cwd=str(repo), capture_output=True, text=True, timeout=300)
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)

    if not ruff_available():
        print("[lint] ruff is not installed in this interpreter - THE LINT GATE "
              "DID NOT RUN.\n[lint]   pip install -r requirements-dev.txt",
              file=sys.stderr)
        return 0
    if args.all:
        paths = ["."]
    else:
        paths = staged_python_files()
        if not paths:
            return 0
    code, output = check(paths)
    if code != 0:
        print(output, file=sys.stderr)
        print("[lint] ruff found the problems above - the commit is refused.\n"
              "[lint]   These rules are syntax errors and undefined names, so each "
              "one is a bug.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
