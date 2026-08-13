#!/usr/bin/env python
"""PreToolUse hook: development work must land in DEV before it reaches PROD.

Claude Code passes the tool call as JSON on stdin. If the command would mutate
the git state of the **prod** checkout by any route other than
``tools\\promote.bat``, exit 2 with a message -> the call is blocked and the
message is shown to Claude. Fail-open on any unexpected error, so a bug here can
never wedge every shell call.

WHY THIS EXISTS. Prod is pinned to ``main`` and is meant to change only when the
operator promotes. ``tools/promote.bat`` is the sanctioned path precisely because
it refuses on a dirty tree, stops the stack before pulling, reinstalls only when
the lockfile moved, and restarts afterwards. None of that happens when an agent
runs ``git pull`` in the prod checkout directly -- which is exactly what happened
on 2026-08-09, repeatedly, in the same session that built the environment split.
Knowing the rule was not enough; the bypass is one keystroke shorter than the
sanctioned path, so the guard has to be mechanical.

WHAT IS BLOCKED: a mutating git verb whose target is the prod checkout, whether
that checkout is named in the command or is simply the shell's cwd (the Bash
tool's cwd persists between calls, so ``git pull`` on its own is enough).

WHAT IS NOT BLOCKED: anything in dev or a worktree; read-only git anywhere
(status/log/diff/rev-parse/show); and ``promote.bat`` itself, which is the whole
point -- the guard redirects to the safe path, it does not forbid promotion.
"""
import json
import os
import re
import sys

# The prod checkout, matched case-insensitively on the path fragment rather than
# an absolute path: the folder is machine-local, and hard-coding a drive letter
# here would make the guard silently inert on any other machine.
PROD_FRAGMENT = "webgui trading prod"

# git subcommands that can move what prod is running. `fetch` is deliberately
# absent -- it only updates remote-tracking refs and is how you inspect before
# promoting.
MUTATING = (
    "pull", "merge", "rebase", "reset", "checkout", "switch", "restore",
    "cherry-pick", "revert", "clean", "am", "apply",
)

# The sanctioned path. If the command runs it, allow -- promote.bat carries the
# dirty-tree refusal, the stop, the conditional reinstall and the restart.
SANCTIONED = ("promote.bat",)


# A leading `cd <prod>` — the shape the real bypass takes. ANCHORED at the start
# on purpose: an unanchored "mentions prod anywhere" test also fires on a command
# that merely WRITES the path into a file (a heredoc, a test fixture, a doc edit),
# which it did within a minute of this hook going live. The cwd check below is the
# other half, since the Bash tool's cwd persists and `git pull` alone is enough.
_PROD_CD = re.compile(
    r"^\s*cd\s+(?:/d\s+)?[\"']?[^\"'&;|]*" + PROD_FRAGMENT.replace(" ", r"\s") +
    r"[^\"'&;|]*[\"']?\s*(?:&&|;)", re.IGNORECASE)


def _targets_prod(command: str, cwd: str) -> bool:
    """True when a git verb here would run INSIDE the prod checkout."""
    if PROD_FRAGMENT in (cwd or "").lower():
        return True
    return bool(_PROD_CD.search(command))


def _mutating_git(command: str) -> str:
    """The mutating git verb in the command, or ''. Also catches `branch -f`."""
    low = command.lower()
    for m in re.finditer(r"\bgit\s+(?:-c\s+\S+\s+)*([a-z-]+)", low):
        verb = m.group(1)
        if verb in MUTATING:
            return verb
        if verb == "branch" and re.search(r"\bgit\s+branch\s+(-f|--force|-[a-zA-Z]*f)", low):
            return "branch -f"
        if verb == "push" and PROD_FRAGMENT in low:
            return "push"
    return ""


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 -- can't parse -> don't block
        return 0
    command = (data.get("tool_input") or {}).get("command") or ""
    cwd = data.get("cwd") or os.getcwd()
    if not command:
        return 0
    low = command.lower()
    if any(s in low for s in SANCTIONED):
        return 0
    if not _targets_prod(command, cwd):
        return 0
    verb = _mutating_git(command)
    if not verb:
        return 0
    sys.stderr.write(
        f"Blocked: `git {verb}` would change the PROD checkout directly.\n"
        "\n"
        "Development work has to be COMPLETED AND VERIFIED IN DEV before it "
        "moves to prod. Land it in dev, run it there, then promote:\n"
        "\n"
        '    cd "D:\\WebGUI Trading Prod" && tools\\promote.bat\n'
        "\n"
        "promote.bat refuses on a dirty tree, stops the stack before pulling, "
        "reinstalls only if requirements.lock moved, and restarts afterwards - "
        "none of which a bare git command does. If you genuinely need to inspect "
        "prod, read-only git (status/log/diff/rev-parse) is not blocked.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
