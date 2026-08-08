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


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path.endswith(".py"):
        return 0
    repo = Path(__file__).resolve().parents[2]
    py = repo / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        return 0
    try:
        subprocess.run([str(py), "-m", "ruff", "check", "--fix", path],
                       cwd=str(repo), capture_output=True, timeout=30)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
