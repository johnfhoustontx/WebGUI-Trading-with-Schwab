#!/usr/bin/env python
"""Point this clone's git at the repository's own hooks (``tools/git-hooks``).

    python tools/install_git_hooks.py            enable
    python tools/install_git_hooks.py --status   say whether it is enabled
    python tools/install_git_hooks.py --remove   disable

Git never runs a hook that merely sits in the repository: hooks are per CLONE.
This sets ``core.hooksPath`` in the clone's config, which every worktree of the
clone shares, so one run covers all of them. The path is relative, so each
worktree runs the hook from its OWN tree - a branch that predates the hook has
no such file and git simply runs nothing there.

The hook itself is ``tools/git-hooks/pre-commit``: ruff over the staged Python
files (see ``tools/lint_gate.py`` for why).
"""
from __future__ import annotations

import argparse
import subprocess
import sys

HOOKS_PATH = "tools/git-hooks"


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=30)


def current() -> str:
    return _git("config", "--get", "core.hooksPath").stdout.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args(argv)

    now = current()
    if args.status:
        print(f"core.hooksPath = {now or '(unset)'}: the repository hooks are "
              f"{'ENABLED' if now == HOOKS_PATH else 'NOT enabled'}")
        return 0 if now == HOOKS_PATH else 1
    if args.remove:
        if now == HOOKS_PATH:
            _git("config", "--unset", "core.hooksPath")
            print("repository hooks disabled (core.hooksPath unset)")
        else:
            print(f"core.hooksPath is {now or '(unset)'} - nothing to remove")
        return 0
    if now and now != HOOKS_PATH:
        print(f"core.hooksPath is already set to {now!r}; not overwriting it. "
              "Unset it first if the repository's hooks should replace it.",
              file=sys.stderr)
        return 1
    done = _git("config", "core.hooksPath", HOOKS_PATH)
    if done.returncode != 0:
        print(done.stderr.strip(), file=sys.stderr)
        return 1
    print(f"repository hooks enabled: core.hooksPath = {HOOKS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
