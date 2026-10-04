"""The lint gate is enforced: by the test suite, and by a commit hook that runs.

Audit CQ-02 (2026-10-03). ``ruff check .`` was defined in three places and
enforced in none, and an undefined name sat on main for two weeks. Three guards
now, each tested here:

* this file's first test - the tree is ruff-clean, so a finding fails the suite
  that is run before every promote;
* ``tools/git-hooks/pre-commit`` - refuses a commit that stages a finding;
* ``.claude/hooks/ruff_fix.py`` - reports what it could not fix (its own tests
  are in ``.claude/hooks/tests``).
"""
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import lint_gate  # noqa: E402

HAS_RUFF = importlib.util.find_spec("ruff") is not None
needs_ruff = pytest.mark.skipif(not HAS_RUFF, reason="ruff is a dev dependency")
BASH, GIT = shutil.which("bash"), shutil.which("git")

UNDEFINED = "def f():\n    return evaluate_regime()\n"      # F821, not auto-fixable
CLEAN = "def f():\n    return 1\n"


# ── the tree ─────────────────────────────────────────────────────────────────

@needs_ruff
def test_the_tree_is_ruff_clean():
    """The gate itself. A finding under the configured rules (syntax errors and
    undefined names) is a bug, and this is where the suite says so."""
    code, output = lint_gate.check(["."])
    assert code == 0, output


# ── the check ────────────────────────────────────────────────────────────────

@needs_ruff
def test_an_undefined_name_is_a_finding(tmp_path):
    (tmp_path / "bad.py").write_text(UNDEFINED)
    code, output = lint_gate.check(["bad.py"], repo=tmp_path)
    assert code != 0 and "F821" in output


@needs_ruff
def test_a_clean_file_passes(tmp_path):
    (tmp_path / "ok.py").write_text(CLEAN)
    assert lint_gate.check(["ok.py"], repo=tmp_path)[0] == 0


def test_a_missing_ruff_says_the_gate_did_not_run(monkeypatch, capsys):
    monkeypatch.setattr(lint_gate, "ruff_available", lambda: False)
    assert lint_gate.main(["--all"]) == 0
    assert "DID NOT RUN" in capsys.readouterr().err


def test_nothing_staged_is_a_pass_without_running_ruff(monkeypatch):
    monkeypatch.setattr(lint_gate, "ruff_available", lambda: True)
    monkeypatch.setattr(lint_gate, "staged_python_files", lambda: [])
    monkeypatch.setattr(lint_gate, "check",
                        lambda *a, **k: pytest.fail("ruff ran with nothing staged"))
    assert lint_gate.main(["--staged"]) == 0


def test_a_finding_in_a_staged_file_fails_the_gate(monkeypatch, capsys):
    monkeypatch.setattr(lint_gate, "ruff_available", lambda: True)
    monkeypatch.setattr(lint_gate, "staged_python_files", lambda: ["a.py"])
    monkeypatch.setattr(lint_gate, "check", lambda paths: (1, "a.py:2:12: F821 ..."))
    assert lint_gate.main(["--staged"]) == 1
    assert "F821" in capsys.readouterr().err


# ── the hook file ────────────────────────────────────────────────────────────

HOOK = ROOT / "tools" / "git-hooks" / "pre-commit"


def test_the_hook_is_a_posix_script_with_lf_endings():
    raw = HOOK.read_bytes()
    assert raw.startswith(b"#!/bin/sh\n")
    assert b"\r" not in raw, "a CRLF shebang does not run"


def test_gitattributes_pins_the_hooks_line_endings():
    assert "tools/git-hooks/*" in (ROOT / ".gitattributes").read_text(encoding="utf-8")


# ── the hook, run by git ─────────────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path):
    """A git repository whose hooks path points at a copy of the real hook."""
    def git(*args, check=True):
        return subprocess.run([GIT, *args], cwd=tmp_path, capture_output=True,
                              text=True, check=check,
                              env={**os.environ, "LINT_GATE_PYTHON": sys.executable})
    git("init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.test"), ("user.name", "t"),
                 ("core.autocrlf", "false"), ("core.hooksPath", "tools/git-hooks")):
        git("config", k, v)
    (tmp_path / "tools" / "git-hooks").mkdir(parents=True)
    hook = tmp_path / "tools" / "git-hooks" / "pre-commit"
    hook.write_bytes(HOOK.read_bytes())
    hook.chmod(0o755)
    shutil.copy(ROOT / "tools" / "lint_gate.py", tmp_path / "tools" / "lint_gate.py")
    return tmp_path, git


@needs_ruff
@pytest.mark.skipif(not (BASH and GIT), reason="needs git and a POSIX shell")
def test_a_commit_that_stages_an_undefined_name_is_refused(repo):
    path, git = repo
    (path / "bad.py").write_text(UNDEFINED)
    git("add", "bad.py")
    done = git("commit", "-m", "x", check=False)
    assert done.returncode != 0
    assert "F821" in done.stderr
    assert git("log", "--oneline", check=False).stdout.strip() == ""    # nothing committed


@needs_ruff
@pytest.mark.skipif(not (BASH and GIT), reason="needs git and a POSIX shell")
def test_a_clean_commit_goes_through(repo):
    path, git = repo
    (path / "ok.py").write_text(CLEAN)
    git("add", "ok.py")
    assert git("commit", "-m", "x", check=False).returncode == 0


@needs_ruff
@pytest.mark.skipif(not (BASH and GIT), reason="needs git and a POSIX shell")
def test_an_unstaged_finding_does_not_block_an_unrelated_commit(repo):
    path, git = repo
    (path / "bad.py").write_text(UNDEFINED)          # in the tree, not staged
    (path / "ok.py").write_text(CLEAN)
    git("add", "ok.py")
    assert git("commit", "-m", "x", check=False).returncode == 0
