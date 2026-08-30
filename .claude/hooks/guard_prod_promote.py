#!/usr/bin/env python
"""PreToolUse hook: development work must land in DEV before it reaches PROD.

Claude Code passes the tool call as JSON on stdin. If the command would mutate
the git state of the **prod** checkout by any route other than
``tools\\promote.sh``, exit 2 with a message -> the call is blocked and the
message is shown to Claude. Fail-open on any unexpected error, so a bug here can
never wedge every shell call.

WHY THIS EXISTS. Prod is pinned to ``main`` and is meant to change only when the
operator promotes. ``tools/promote.sh`` is the sanctioned path precisely because
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
(status/log/diff/rev-parse/show); and ``promote.sh`` itself, which is the whole
point -- the guard redirects to the safe path, it does not forbid promotion.
"""
import json
import os
import re
import sys

# The prod checkout, matched case-insensitively on a path FRAGMENT rather than an
# absolute path: the folder is machine-local, and hard-coding a drive letter here
# would make the guard silently inert on any other machine.
#
# BOTH spellings are listed, and that is not tidiness. The Windows fragment alone
# made this guard completely INERT on the Linux VPS -- /home/administrator/prod
# contains none of those words, so every mutating git verb in prod would have
# sailed through, on the one guard that exists because knowing the rule was not
# enough. Replacing rather than adding would have been just as bad in reverse:
# Windows prod stays live and authoritative until cutover, and during the
# parallel-run week BOTH prod checkouts exist at once.
#
# Keep these specific. "/prod" would swallow the sibling /home/administrator/dev
# checkout's neighbours and block ordinary development.
PROD_FRAGMENTS = (
    "webgui trading prod",        # Windows
    "/home/administrator/prod",   # Linux VPS
)

# git subcommands that can move what prod is running. `fetch` is deliberately
# absent -- it only updates remote-tracking refs and is how you inspect before
# promoting.
MUTATING = (
    "pull", "merge", "rebase", "reset", "checkout", "switch", "restore",
    "cherry-pick", "revert", "clean", "am", "apply",
)

# The sanctioned path. If the command runs it, allow -- promote.sh carries the
# dirty-tree refusal, the stop, the conditional reinstall and the restart.
#
# ⚠ This MUST track what actually exists. promote.sh was deleted with the rest
# of the supervision layer; had this kept naming it, the guard would have blocked
# the only sanctioned way to move prod -- turning a safety rail into a total
# block, which is the fastest way to get a guard disabled.
SANCTIONED = ("promote.sh",)


# A leading `cd <prod>` — the shape the real bypass takes. ANCHORED at the start
# on purpose: an unanchored "mentions prod anywhere" test also fires on a command
# that merely WRITES the path into a file (a heredoc, a test fixture, a doc edit),
# which it did within a minute of this hook going live. The cwd check below is the
# other half, since the Bash tool's cwd persists and `git pull` alone is enough.
def _fragment_pattern(fragment):
    """A fragment as a regex, with each space matching one whitespace char."""
    return r"\s".join(re.escape(part) for part in fragment.split(" "))


_ANY_PROD = "(?:" + "|".join(_fragment_pattern(f) for f in PROD_FRAGMENTS) + ")"

_PROD_CD = re.compile(
    r"^\s*cd\s+(?:/d\s+)?[\"']?[^\"'&;|]*" + _ANY_PROD +
    r"[^\"'&;|]*[\"']?\s*(?:&&|;)", re.IGNORECASE)


# `git -C <prod>` reaches the checkout with no `cd` at all, and is the natural
# one-liner -- this repo's own notes suggested exactly that form. Anchored at a
# COMMAND POSITION (start of line, or after && || ; ) for the same reason the cd
# match is: `echo "git -C <prod> pull" > fixture` must stay allowed.
_CMD_POS = r"(?:^|&&|\|\||;|\n)\s*"

_PROD_GIT_C = re.compile(
    _CMD_POS + r"git\s+(?:-c\s+\S+\s+)*-C\s+[\"']?[^\"'&;|]*" + _ANY_PROD,
    re.IGNORECASE)

# An ssh-wrapped command is the shape EVERY prod command now takes: both stacks
# moved to the VPS, so the cwd is a dev-side checkout and the prod path lives
# inside the quoted remote command, where neither test above could see it. The
# guard was therefore blind on the only path that reaches prod any more -- the
# third time it has gone quietly inert on a change of address (first the
# Windows-only fragment, then the start-anchor).
#
# Unwrap rather than widen: the remote command is tested with the SAME anchored
# rules as a local one, so `ssh host 'echo "cd <prod> && git pull" >> notes'`
# stays allowed. Widening to "mentions prod anywhere" would fix ssh and
# instantly re-break that, which is the regression the anchor exists for.
_SSH_OPEN = re.compile(r"\s*ssh\b[^'\"]*(['\"])")


def _ssh_payload(command: str):
    """The remote command inside `ssh [opts] host '<...>'`, or None."""
    m = _SSH_OPEN.match(command)
    if not m:
        return None
    quote = m.group(1)
    rest = command[m.end():]
    end = rest.rfind(quote)
    return rest[:end] if end != -1 else rest


def _targets_prod(command: str, cwd: str) -> bool:
    """True when a git verb here would run INSIDE the prod checkout."""
    low_cwd = (cwd or "").lower()
    if any(f in low_cwd for f in PROD_FRAGMENTS):
        return True
    candidates = [command]
    payload = _ssh_payload(command)
    if payload:
        candidates.append(payload)
    return any(_PROD_CD.search(c) or _PROD_GIT_C.search(c) for c in candidates)


def _mutating_git(command: str) -> str:
    """The mutating git verb in the command, or ''. Also catches `branch -f`."""
    low = command.lower()
    # The option argument may be QUOTED and contain spaces -- `git -C "D:\WebGUI
    # Trading Prod" pull`. With a bare \S+ the skip consumed only `"D:\WebGUI`
    # and the verb read as `Trading`, so the whole command scanned as
    # non-mutating. (`command` is lowercased here, so -C and -c are the same
    # token by then; both take exactly one argument, which is why one branch
    # covers `-c key=val` too.)
    for m in re.finditer(
            r"\bgit\s+(?:-c\s+(?:\"[^\"]*\"|'[^']*'|\S+)\s+)*([a-z-]+)", low):
        verb = m.group(1)
        if verb in MUTATING:
            return verb
        if verb == "branch" and re.search(r"\bgit\s+branch\s+(-f|--force|-[a-zA-Z]*f)", low):
            return "branch -f"
        if verb == "push" and any(f in low for f in PROD_FRAGMENTS):
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
        "    cd /home/administrator/prod && tools/promote.sh\n"
        "\n"
        "promote.sh refuses on a dirty tree, stops the stack before pulling, "
        "reinstalls only if requirements.lock moved, and restarts afterwards - "
        "none of which a bare git command does. If you genuinely need to inspect "
        "prod, read-only git (status/log/diff/rev-parse) is not blocked.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
