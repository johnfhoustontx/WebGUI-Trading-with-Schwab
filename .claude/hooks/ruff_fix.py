#!/usr/bin/env python
"""PostToolUse hook: ruff --fix the edited Python file (best-effort, quiet).

Runs the project venv's ruff on the just-edited *.py file to auto-fix formatting
/ import drift, matching the repo's "ruff clean" standard. Never blocks (exit 0
always); a ruff or path problem is silently ignored so it can't disrupt editing.
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


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path.endswith(".py"):
        return 0
    repo = Path(__file__).resolve().parents[2]
    py = venv_python(repo)
    if py is None:
        return 0
    try:
        subprocess.run([str(py), "-m", "ruff", "check", "--fix", path],
                       cwd=str(repo), capture_output=True, timeout=30)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
