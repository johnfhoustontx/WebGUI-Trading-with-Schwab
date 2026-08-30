"""Shell scripts must be stored with LF, and must stay that way on checkout.

Replaces test_batch_line_endings.py, whose subject (the .bat launchers) was
deleted in the systemd migration. The concern INVERTED rather than disappearing:
that test existed because cmd.exe mis-parses an LF batch file under codepage
65001; this one exists because a CRLF shell script does not run at all.

The kernel reads a shebang literally, so `#!/usr/bin/env bash\r` requests an
interpreter named "bash\r":

    bad interpreter: /usr/bin/env bash^M: no such file or directory

-- a message that names neither the real problem nor, on some shells, the file.
And `core.autocrlf` is a per-USER setting that is true on the Windows dev box,
so without the .gitattributes rule a fresh clone there silently produces broken
scripts. The rule is what makes this independent of whose machine checked out.
"""
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = sorted(ROOT.glob("tools/*.sh"))


def test_there_are_shell_scripts_to_check():
    """Non-vacuity: every test below passes trivially over an empty list."""
    assert SCRIPTS, "no tools/*.sh found - has the migration moved them?"


@pytest.mark.parametrize("p", SCRIPTS, ids=lambda p: p.name)
def test_no_carriage_returns_in_the_working_tree(p):
    assert b"\r" not in p.read_bytes(), (
        f"{p.name} has CRLF; it will fail on Linux with 'bad interpreter'")


@pytest.mark.parametrize("p", SCRIPTS, ids=lambda p: p.name)
def test_starts_with_a_shebang(p):
    assert p.read_bytes().startswith(b"#!"), p.name


def test_gitattributes_pins_lf_for_shell_scripts():
    """The working-tree check above passes on a machine that happens to be
    configured correctly. This asserts the REPO carries the rule, which is what
    protects the next clone on someone else's settings."""
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in text


def test_git_stores_them_with_lf():
    """What is in the object database, not what is on disk -- the two differ
    precisely when autocrlf is doing something."""
    for p in SCRIPTS:
        rel = p.relative_to(ROOT).as_posix()
        blob = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT,
                              capture_output=True)
        if blob.returncode != 0:
            continue          # not committed yet
        assert b"\r\n" not in blob.stdout, rel


def test_git_stores_them_executable():
    """The exec bit is part of the REPO, and on Windows nothing sets it.

    Git records a mode per file (100644 / 100755). A script authored on Windows
    is committed 100644, because there is no exec bit there for git to observe
    -- and `core.fileMode` is false there, so `git status` never reports the
    difference either. The result checks out on Linux as 664 and dies with

        bash: tools/promote.sh: Permission denied

    Found 2026-08-29: ALL THREE tools/*.sh were 100644, so `tools/promote.sh` --
    spelled exactly that way in CLAUDE.md and the operator runbook -- had never
    been runnable on the VPS. `bash tools/promote.sh` works and hides it, which
    is why it survived the migration unnoticed.

    Fix a new script with:  git update-index --chmod=+x tools/<name>.sh
    """
    out = subprocess.run(["git", "ls-files", "-s", "--", "tools/*.sh"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        pytest.skip("not a git checkout, or no tracked tools/*.sh")
    non_exec = [line.split("\t", 1)[1] for line in out.stdout.strip().splitlines()
                if not line.startswith("100755 ")]
    assert not non_exec, (
        "tracked shell scripts are not executable in git, so they check out "
        f"non-runnable on Linux: {non_exec}")
