"""The prod-promotion guard — see .claude/hooks/guard_prod_promote.py.

The rule it enforces is a workflow one: development work is completed and
verified in DEV, and reaches PROD only through ``tools\\promote.sh``. Knowing
that rule was demonstrably not enough — the whole environment split was built in
a session that then bypassed promote on every single commit, because
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
    """The guard redirects to promote.sh; it does not forbid promotion."""
    assert run(f'cd "{PROD}" && tools\\promote.sh', cwd=DEV) == ALLOWED


def test_the_sanctioned_allow_list_actually_overrides_a_mutating_verb():
    """Deliberately paired with the test above, which does NOT discriminate:
    `cd prod && tools\\promote.sh` carries no git verb, so it is allowed whether
    or not the allow-list exists — emptying SANCTIONED left all ten tests green.

    This one is the real check. The allow-list is insurance rather than a path
    taken today (promote.sh's own git commands run inside the script and never
    reach this hook), but a guard whose only sanctioned exit could itself be
    blocked is a guard that strands you, so the override is pinned."""
    assert run(f'cd "{PROD}" && git pull && tools\\promote.sh', cwd=DEV) == ALLOWED
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


# --- Linux prod checkout (the systemd migration) ------------------------------
# The guard identified prod by the case-insensitive fragment "webgui trading
# prod", chosen over an absolute path so a drive-letter change could not defeat
# it. /home/administrator/prod does not contain that fragment, so on the VPS
# EVERY mutating git verb in prod would have sailed straight through -- on the
# one guard that exists because knowing the rule was not enough.
#
# BOTH spellings are matched, deliberately. Replacing the Windows fragment would
# leave prod unguarded for the whole migration window, including the parallel-run
# week when two prod checkouts exist at once.
LINUX_PROD = "/home/administrator/prod"
LINUX_DEV = "/home/administrator/dev"


def test_blocks_a_bare_git_pull_in_the_linux_prod_checkout():
    assert run("git pull", cwd=LINUX_PROD) == BLOCKED


def test_blocks_a_leading_cd_into_the_linux_prod_checkout():
    assert run(f"cd {LINUX_PROD} && git reset --hard origin/main",
               cwd=LINUX_DEV) == BLOCKED


def test_still_blocks_the_windows_prod_checkout():
    """Non-regression. Windows prod stays live and authoritative until cutover;
    swapping its fragment out to add the Linux one would unguard it meanwhile."""
    assert run("git pull", cwd=PROD) == BLOCKED


def test_allows_read_only_git_in_the_linux_prod_checkout():
    """Inspecting prod is how you decide whether to promote."""
    for cmd in ("git status", "git log --oneline -5", "git diff", "git rev-parse HEAD"):
        assert run(cmd, cwd=LINUX_PROD) == ALLOWED, cmd


def test_allows_everything_in_the_linux_DEV_checkout():
    """The sibling directory differs by four characters, so an over-broad
    fragment like "/prod" would swallow it."""
    assert run("git pull", cwd=LINUX_DEV) == ALLOWED


def test_the_sanctioned_path_is_the_shell_script_now():
    """promote.bat went with the rest of the supervision layer. If SANCTIONED
    still named only it, the guard would block the ONLY sanctioned way to move
    prod -- turning a safety rail into a total block."""
    assert run("git pull", cwd=LINUX_PROD) == BLOCKED
    assert run("tools/promote.sh", cwd=LINUX_PROD) == ALLOWED
    assert run("./tools/promote.sh", cwd=LINUX_PROD) == ALLOWED


def test_the_hook_never_names_a_file_that_no_longer_exists():
    r"""Its refusal message told people to run tools\promote.bat. A guard that
    blocks you and then names a missing file reads as a bug and invites a
    workaround -- which is the one outcome it cannot afford."""
    src = HOOK.read_text(encoding="utf-8")
    assert "promote.bat" not in src
    assert "promote.sh" in src


def test_a_push_from_inside_prod_does_not_crash_the_hook():
    """Regression. After PROD_FRAGMENT became a tuple, the push branch still
    referenced the old singular name, so evaluating a `git push` inside prod
    raised NameError INSIDE the hook.

    ⚠ Ruff caught this; no test did. Push was the one verb reachable only from
    prod's own cwd, and every existing test that used prod's cwd used a
    different verb. The assertion is on the EXIT CODE being a decision (0 or 2)
    rather than a crash (1), because either decision is defensible and a crash
    is not: a hook that dies is a hook whose verdict nobody gets."""
    for cwd in (PROD, LINUX_PROD):
        rc = run("git push origin main", cwd=cwd)
        assert rc in (ALLOWED, BLOCKED), f"hook crashed with rc={rc} in {cwd}"


def test_an_ordinary_push_from_dev_is_still_allowed():
    """Non-vacuity: pushing is how work leaves dev."""
    assert run("git push origin Using_Highcharts", cwd=DEV) == ALLOWED
    assert run("git push origin main", cwd=LINUX_DEV) == ALLOWED


###############################################
# The command REACHES prod without standing in it
###############################################
#
# Both shapes below sailed straight through until 2026-08-29. The ssh one is the
# serious regression: since the Linux migration every prod command is issued
# over ssh from a dev-side cwd, so the guard was blind on the ONLY path that
# reaches prod any more -- a `git checkout` in prod ran unchallenged. This is
# the third time this guard has gone quietly inert on a change of address (first
# the Windows-only path fragment, then the start-anchor), so the tests are
# written against the SHAPE of the access, not against one spelling of it.


def test_blocks_a_mutating_verb_sent_over_ssh():
    """The access path the whole stack now uses."""
    assert run(f"ssh vps-ts 'cd {LINUX_PROD} && git checkout main'", cwd=DEV) == BLOCKED


def test_blocks_it_regardless_of_the_quoting_or_ssh_options():
    for cmd in (
        f'ssh vps-ts "cd {LINUX_PROD} && git pull --ff-only"',
        f"ssh -p 2222 vps-ts 'cd {LINUX_PROD} && git reset --hard origin/main'",
        f"ssh administrator@10.0.0.5 'cd {LINUX_PROD} && git merge feature'",
    ):
        assert run(cmd, cwd=DEV) == BLOCKED, f"not blocked: {cmd!r}"


def test_blocks_git_dash_C_pointed_at_prod():
    """`git -C <path>` needs no cd at all, and is the natural one-liner -- the
    repo's own notes suggested exactly this form."""
    assert run(f"git -C {LINUX_PROD} checkout main", cwd=DEV) == BLOCKED
    assert run(f'git -C "{PROD}" pull --ff-only', cwd=DEV) == BLOCKED
    assert run(f"ssh vps-ts 'git -C {LINUX_PROD} reset --hard'", cwd=DEV) == BLOCKED


def test_still_allows_read_only_git_over_ssh():
    """Inspecting prod is how you decide whether to promote."""
    for cmd in (f"ssh vps-ts 'cd {LINUX_PROD} && git status --porcelain'",
                f"ssh vps-ts 'cd {LINUX_PROD} && git log --oneline -5'",
                f"ssh vps-ts 'git -C {LINUX_PROD} rev-parse HEAD'",
                f"ssh vps-ts 'cd {LINUX_PROD} && git fetch origin main'"):
        assert run(cmd, cwd=DEV) == ALLOWED, f"over-blocked: {cmd!r}"


def test_still_allows_the_sanctioned_path_over_ssh():
    """The guard redirects to promote.sh, so it must not block promote.sh."""
    assert run(f"ssh vps-ts 'cd {LINUX_PROD} && tools/promote.sh'", cwd=DEV) == ALLOWED
    assert run(f"ssh vps-ts 'cd {LINUX_PROD} && bash tools/promote.sh'", cwd=DEV) == ALLOWED


def test_still_allows_ordinary_dev_work_over_ssh():
    """Non-vacuity: dev now lives on the VPS too, and is reached the same way."""
    for cmd in (f"ssh vps-ts 'cd {LINUX_DEV} && git checkout main'",
                f"ssh vps-ts 'cd {LINUX_DEV} && git merge feature'",
                f"ssh vps-ts 'git -C {LINUX_DEV} pull --ff-only'"):
        assert run(cmd, cwd=DEV) == ALLOWED, f"over-blocked: {cmd!r}"


def test_naming_the_prod_path_over_ssh_is_still_not_running_a_verb_in_it():
    """The anchor's original reason, carried into the new shapes. Widening the
    match to "mentions prod anywhere" would fix ssh and immediately re-break
    this -- which is the failure that motivated the anchor in the first place."""
    naming = (
        f"ssh vps-ts 'echo \"cd {LINUX_PROD} && git pull\" >> notes.txt'",
        f"ssh vps-ts 'grep -rn \"git checkout\" {LINUX_PROD}/docs'",
        f'echo "git -C {LINUX_PROD} pull" > fixture.txt',
        f"ssh vps-ts 'cat {LINUX_PROD}/CLAUDE.md'",
    )
    for cmd in naming:
        assert run(cmd, cwd=DEV) == ALLOWED, f"over-blocked: {cmd!r}"


def test_the_refusal_message_names_the_checkout_that_actually_exists():
    r"""It told people to `cd "D:\WebGUI Trading Prod"`, a path that is now an
    archive on a decommissioned box. A guard that blocks you and then gives an
    instruction that cannot work reads as a bug and invites a workaround."""
    src = HOOK.read_text(encoding="utf-8")
    start = src.index("Blocked: `git")
    message = src[start:]
    assert "/home/administrator/prod" in message
    assert "D:" not in message, "refusal still points at the Windows checkout"
