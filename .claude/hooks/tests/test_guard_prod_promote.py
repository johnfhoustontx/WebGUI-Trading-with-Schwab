"""The prod-promotion guard — see .claude/hooks/guard_prod_promote.py.

The rule it enforces is a workflow one: development work is completed and
verified in DEV, and reaches PROD only through ``tools\\promote.bat``. Knowing
that rule was demonstrably not enough — the whole environment split was built in
a session that then bypassed ``promote.bat`` on every single commit, because
``git pull`` in the prod checkout is one keystroke shorter.

Two properties matter and pull against each other, so both are pinned here:

* it must block the bypass, including the bare ``git pull`` that relies on the
  Bash tool's persistent cwd; and
* it must NOT block a command that merely *names* the prod path — writing a
  fixture, editing a doc, grepping. The first version did, within a minute of
  going live, which is why the ``cd`` match is anchored at the start of the
  command rather than searched anywhere in it.
"""
import json
import pathlib
import subprocess
import sys

HOOK = pathlib.Path(__file__).resolve().parents[1] / "guard_prod_promote.py"
PROD = r"D:\WebGUI Trading Prod"
DEV = r"D:\WebGUI Trading with Schwab"
WORKTREE = DEV + r"\.claude\worktrees\some-feature"

BLOCKED = 2
ALLOWED = 0


def run(command, cwd=DEV):
    """The hook's exit code for a tool call. 2 = blocked."""
    payload = json.dumps({"tool_input": {"command": command}, "cwd": cwd})
    p = subprocess.run([sys.executable, str(HOOK)], input=payload,
                       capture_output=True, text=True)
    return p.returncode


###############################################
# It must block the bypass
###############################################

def test_blocks_the_exact_bypass_that_prompted_it():
    assert run(f'cd "{PROD}" && git pull --ff-only', cwd=DEV) == BLOCKED


def test_blocks_a_bare_git_pull_when_cwd_is_already_prod():
    """The Bash tool's cwd persists between calls, so the `cd` may have happened
    in an earlier call and the mutating command carries no path at all."""
    assert run("git pull --ff-only", cwd=PROD) == BLOCKED


def test_blocks_the_other_ways_to_move_what_prod_is_running():
    for cmd in ("git checkout main", "git reset --hard origin/main",
                "git merge Using_Highcharts", "git branch -f main Using_Highcharts",
                "git rebase main", "git cherry-pick abc123"):
        assert run(cmd, cwd=PROD) == BLOCKED, f"{cmd!r} was not blocked in prod"


###############################################
# It must not block anything else
###############################################

def test_allows_the_sanctioned_path():
    """The guard redirects to promote.bat; it does not forbid promotion."""
    assert run(f'cd "{PROD}" && tools\\promote.bat', cwd=DEV) == ALLOWED


def test_the_sanctioned_allow_list_actually_overrides_a_mutating_verb():
    """Deliberately paired with the test above, which does NOT discriminate:
    `cd prod && tools\\promote.bat` carries no git verb, so it is allowed whether
    or not the allow-list exists — emptying SANCTIONED left all ten tests green.

    This one is the real check. The allow-list is insurance rather than a path
    taken today (promote.bat's own git commands run inside the batch and never
    reach this hook), but a guard whose only sanctioned exit could itself be
    blocked is a guard that strands you, so the override is pinned."""
    assert run(f'cd "{PROD}" && git pull && tools\\promote.bat', cwd=DEV) == ALLOWED
    # ...and the same command without the sanctioned invocation IS blocked,
    # which is what makes the assertion above mean something.
    assert run(f'cd "{PROD}" && git pull', cwd=DEV) == BLOCKED


def test_allows_read_only_git_in_prod():
    """Inspecting prod is how you decide whether to promote."""
    for cmd in ("git status --short", "git log --oneline -5", "git diff HEAD",
                "git rev-parse --short HEAD", "git fetch", "git show HEAD"):
        assert run(cmd, cwd=PROD) == ALLOWED, f"{cmd!r} should be readable in prod"


def test_allows_everything_in_dev_and_in_a_worktree():
    for cwd in (DEV, WORKTREE):
        for cmd in ("git pull --ff-only", "git merge --ff-only claude/feature",
                    "git checkout main", "git branch -f main Using_Highcharts"):
            assert run(cmd, cwd=cwd) == ALLOWED, f"{cmd!r} should be free in {cwd}"


def test_does_not_block_a_command_that_merely_names_the_prod_path():
    """The regression that appeared within a minute of the hook going live: an
    unanchored match fires on writing a fixture or a doc that quotes the path."""
    naming = (
        f'cd "{WORKTREE}" && echo \'cd "{PROD}" && git checkout main\' > fixture.json',
        f'grep -rn "git pull" "{PROD}/docs"',
        f'cd "{WORKTREE}" && python -c "print(r\'{PROD}\')"',
    )
    for cmd in naming:
        assert run(cmd, cwd=WORKTREE) == ALLOWED, f"over-blocked: {cmd!r}"


def test_allows_non_git_commands_in_prod():
    for cmd in ("python -m pytest tests/ -q", "dir", "type CLAUDE.md"):
        assert run(cmd, cwd=PROD) == ALLOWED


###############################################
# It must never wedge the shell
###############################################

def test_fails_open_on_a_malformed_payload():
    p = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True)
    assert p.returncode == ALLOWED


def test_fails_open_when_there_is_no_command():
    assert json.loads("{}") == {}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"cwd": PROD}),
                       capture_output=True, text=True)
    assert p.returncode == ALLOWED
