"""The ruff auto-fix hook must find the venv on either platform.

WHY: it hardcoded `.venv/Scripts/python.exe` and the next line was
`if not py.exists(): return 0`. On Linux that path does not exist, so the hook
no-opped AND RETURNED SUCCESS -- ruff auto-fix quietly stopped running with
nothing anywhere reporting it. A silent degrade in the tooling of a repo whose
own CLAUDE.md calls that the most expensive bug class it has.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ruff_fix  # noqa: E402


def _fake_venv(root, *rel):
    p = root.joinpath(".venv", *rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    return p


def test_finds_the_windows_layout(tmp_path):
    want = _fake_venv(tmp_path, "Scripts", "python.exe")
    assert ruff_fix.venv_python(tmp_path) == want


def test_finds_the_posix_layout(tmp_path):
    want = _fake_venv(tmp_path, "bin", "python")
    assert ruff_fix.venv_python(tmp_path) == want


def test_no_venv_is_None_not_a_crash(tmp_path):
    """The hook's contract is to be a no-op when it cannot run. That is correct
    when there is genuinely no venv -- it was only wrong when a venv existed and
    the hook looked in the other platform's directory."""
    assert ruff_fix.venv_python(tmp_path) is None


def test_the_windows_path_alone_is_no_longer_hardcoded():
    """Source-level, because a value check cannot see a path that is never
    consulted. This is what would have caught the original bug."""
    src = pathlib.Path(ruff_fix.__file__).read_text(encoding="utf-8")
    assert "bin" in src and "Scripts" in src, "both layouts must be reachable"


# ── a worktree has no venv of its own; the main checkout's is the one ────────
# (audit CQ-02). Every session here works in a worktree, where ``.venv`` does
# not exist, so the hook found nothing and returned success: it never ran.

def test_a_worktree_falls_back_to_the_main_checkouts_venv(tmp_path):
    main = tmp_path / "main"
    want = _fake_venv(main, "Scripts", "python.exe")
    worktree = main / ".claude" / "worktrees" / "feature"
    worktree.mkdir(parents=True)
    assert ruff_fix.find_python(worktree, main_checkout=lambda _: main) == want


def test_the_worktrees_own_venv_wins_when_it_has_one(tmp_path):
    main, worktree = tmp_path / "main", tmp_path / "wt"
    _fake_venv(main, "bin", "python")
    want = _fake_venv(worktree, "bin", "python")
    assert ruff_fix.find_python(worktree, main_checkout=lambda _: main) == want


def test_no_venv_anywhere_is_None(tmp_path):
    assert ruff_fix.find_python(tmp_path, main_checkout=lambda _: None) is None
    assert ruff_fix.find_python(tmp_path, main_checkout=lambda _: tmp_path / "x") is None


# ── what ruff could not fix is REPORTED, not discarded ───────────────────────
# The hook ran ``ruff check --fix`` with the output captured and dropped. An
# undefined name is not auto-fixable, so the one finding class the lint gate
# exists for (F821 - the bug that sat on main for two weeks) passed through the
# hook unseen.

class _Done:
    def __init__(self, returncode, stdout=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, ""


def test_remaining_findings_are_returned(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return _Done(1, "a.py:3:5: F821 Undefined name `evaluate_regime`\n")

    out = ruff_fix.lint(tmp_path / "py", "a.py", tmp_path, run=run)
    assert "F821" in out
    assert "--fix" in calls[0]


def test_a_clean_file_reports_nothing(tmp_path):
    assert ruff_fix.lint(tmp_path / "py", "a.py", tmp_path,
                         run=lambda cmd, **kw: _Done(0, "All checks passed!\n")) == ""


def test_a_ruff_that_cannot_run_reports_nothing_and_does_not_raise(tmp_path):
    def run(cmd, **kw):
        raise OSError("no such interpreter")

    assert ruff_fix.lint(tmp_path / "py", "a.py", tmp_path, run=run) == ""


def test_a_missing_ruff_module_is_not_reported_as_a_finding(tmp_path):
    # ``python -m ruff`` with no ruff installed exits 1 with this on stderr.
    def run(cmd, **kw):
        d = _Done(1, "")
        d.stderr = "No module named ruff"
        return d

    assert ruff_fix.lint(tmp_path / "py", "a.py", tmp_path, run=run) == ""
