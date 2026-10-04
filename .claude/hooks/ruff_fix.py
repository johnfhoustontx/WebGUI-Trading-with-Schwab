#!/usr/bin/env python
"""PostToolUse hook: ruff --fix the edited Python file, and REPORT what is left.

Runs the project venv's ruff on the just-edited *.py file to auto-fix import
drift, matching the repo's "ruff clean" standard. It never undoes or blocks an
edit; a ruff or path problem is ignored so it cannot disrupt editing.

What it does NOT ignore is a finding ruff could not fix. Until 2026-10-03 the
run's output was captured and dropped, so the one class the lint gate exists
for - an undefined name, which is not auto-fixable - passed through unseen, and
such a bug sat on main for two weeks (audit CQ-02). Remaining findings now go to
stderr with exit code 2, which is how a PostToolUse hook hands text back to the
session that made the edit.
"""
import json
import subprocess
import sys
from pathlib import Path


def venv_python(repo):
    """The venv interpreter for `repo`, or None when there is no venv.

    ⚠ Checks BOTH layouts. This used to hardcode `.venv/Scripts/python.exe`, and
    the very next line is `if not py.exists(): return 0` -- so on Linux, where
    the interpreter lives at `.venv/bin/python`, this hook NO-OPPED AND RETURNED
    SUCCESS. Ruff auto-fix would simply have stopped running, with nothing
    anywhere saying so: a silent degrade in the tooling whose own repo documents
    that exact bug class as its most expensive.

    Windows first, then POSIX. Order is irrelevant to correctness -- only one
    exists on a given host -- but it keeps the common case one stat call.
    """
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        candidate = repo.joinpath(".venv", *rel)
        if candidate.exists():
            return candidate
    return None


def main_checkout(repo):
    """The MAIN checkout a worktree belongs to, or None.

    ``git rev-parse --git-common-dir`` is ``<main>/.git`` from any worktree.
    """
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=str(repo),
                             capture_output=True, text=True, timeout=10)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        common = Path(out.stdout.strip())
        if not common.is_absolute():
            common = Path(repo) / common
        return common.resolve().parent
    except Exception:
        return None


def find_python(repo, main_checkout=main_checkout):
    """The venv interpreter to lint with: this checkout's, else the main one's.

    ⚠ A git WORKTREE has no ``.venv`` of its own, and every session here works
    in one - so looking only under ``repo`` meant this hook found nothing and
    returned success on every edit. It never ran.
    """
    repo = Path(repo)
    here = venv_python(repo)
    if here is not None:
        return here
    main = main_checkout(repo)
    if main is None:
        return None
    return venv_python(Path(main))


def lint(py, path, repo, run=subprocess.run):
    """Auto-fix ``path``, then return whatever ruff still reports ("" if clean).

    Never raises. A ruff that cannot run - no interpreter, no ruff module, a
    timeout - returns "": that is "the gate did not run", not a finding, and
    the commit hook and the test suite are the gates that fail loudly.
    """
    try:
        done = run([str(py), "-m", "ruff", "check", "--fix", "--quiet", str(path)],
                   cwd=str(repo), capture_output=True, text=True, timeout=30)
    except Exception:
        return ""
    if done.returncode == 0:
        return ""
    if "No module named ruff" in (done.stderr or ""):
        return ""
    return (done.stdout or "").strip()


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path.endswith(".py"):
        return 0
    repo = Path(__file__).resolve().parents[2]
    py = find_python(repo)
    if py is None:
        return 0
    left = lint(py, path, repo)
    if not left:
        return 0
    print(f"ruff could not fix these in {path}:\n{left}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
