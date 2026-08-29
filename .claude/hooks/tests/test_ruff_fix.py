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
